"""Лента сверок и акт сверки для лаборатории; сводка по сессии; решения методиста; наблюдения кандидата.

Улика хранится в anticheat_evidence (классификация ответа). Контекст — время копирования, вставка в своё поле,
момент предупреждения (первое копирование в сессии: тост «Копирование условия фиксируется» показан именно тогда) —
собирается ПРИ ЧТЕНИИ из журнала событий сессии: события приходят пачками и могут опоздать к ответу.
Сводка по сессии — чистое ядро (evidence.aggregate_session): одно попадание на класс, знаменатель — все задания с
двойником, до предупреждения не выше «С оговоркой»; вес в доверии 0 до калибровки ε; автобана нет.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.errors import bad_request, not_found
from app.models.candidate import CandidateProfile
from app.models.testing import SessionEvent as SessionEventRow
from app.models.testing import TestResponse
from app.models.testing import TestSession as SessionRow
from app.services.anticheat import store
from app.services.anticheat.cfg import RuntimeConfig, runtime_config
from app.services.anticheat.models import (
    REVIEW_DISMISSED,
    REVIEW_OPEN,
    REVIEW_STATUSES,
    REVIEW_VERIFICATION,
    AnticheatEvidence,
    AnticheatSandboxRun,
)
from app.services.anticheat.schemas import ReconciliationCountsOut
from app.testing.anticheat.act import ReconciliationAct, build_act
from app.testing.anticheat.config import AntiCheatConfig, load_anticheat_config
from app.testing.anticheat.evidence import (
    AFTER,
    BEFORE,
    UNKNOWN,
    AnswerClass,
    SessionTwinSummary,
    TwinObservation,
    aggregate_session,
)
from app.testing.anticheat.twins import Twin
from app.testing.bank.registry import all_families

SANDBOX_PREFIX = "sb-"

_ACTION_STATUS = {"assign_verification": REVIEW_VERIFICATION, "dismiss": REVIEW_DISMISSED, "reopen": REVIEW_OPEN}


# ----------------------------------------------------------------------------- подписи


def family_title(family_id: str) -> str:
    fam = all_families().get(family_id)
    return str(getattr(fam, "title", family_id)) if fam is not None else family_id


def candidate_label(db: Session, user_id: uuid.UUID | None, seq: int, variant: str, rc: RuntimeConfig) -> str:
    """«Сессия кандидата Илья К., задание 7, вариант 7F3A»: имя и инициал фамилии, а без имени — короткий код."""
    name = None
    if user_id is not None:
        full = db.scalar(select(CandidateProfile.full_name).where(CandidateProfile.user_id == user_id))
        parts = (full or "").split()
        if parts:
            name = parts[0] + (f" {parts[-1][0]}." if len(parts) > 1 else "")
    if name:
        return rc.text("label_session", name=name, seq=seq, variant=variant)
    code = (user_id.hex[: rc.raw_text("candidate_code_len")] if user_id else "—").upper()
    return rc.text("label_session_anon", code=code, seq=seq, variant=variant)


def headline_for(ac: AnswerClass, rc: RuntimeConfig) -> str:
    if ac.kind == "twin_trace":
        name = {"exact": "twin_exact", "err_twin": "twin_err"}.get(ac.strength or "", "twin_token")
    else:
        name = ac.kind if ac.kind in ("correct", "misconception") else "other"
    return rc.text(f"headline.{name}")


def probability_value(p: float, rc: RuntimeConfig) -> str:
    """«2%» · «0,5%» · «< 0,1%»: вероятность честного совпадения процентами с русской запятой."""
    pct = p * 100
    if pct < 0.1:
        return str(rc.raw_text("stats.probability.floor_label"))
    return (f"{pct:.0f}%" if pct >= 10 else f"{pct:.1f}%").replace(".", ",")


# ----------------------------------------------------------------------------- журнал событий сессии


@dataclass
class EventCtx:
    copy_at: dict[int, datetime] = field(default_factory=dict)  # seq → первое копирование задания
    copy_render: dict[int, str] = field(default_factory=dict)  # seq → render_id из события copy
    self_paste: set[int] = field(default_factory=set)
    warned_at: datetime | None = None  # первое копирование в сессии: тогда показан тост


def load_event_ctx(db: Session, session_id: uuid.UUID, rc: RuntimeConfig | None = None) -> EventCtx:
    rc = rc or runtime_config()
    types = (*rc.copy_types, rc.self_paste_type)
    ctx = EventCtx()
    rows = db.scalars(
        select(SessionEventRow)
        .where(SessionEventRow.session_id == session_id, SessionEventRow.type.in_(types))
        .order_by(SessionEventRow.ts, SessionEventRow.id)
    )
    for r in rows:
        payload = dict(r.payload or {})
        raw_seq = payload.get("seq")
        seq = int(raw_seq) if isinstance(raw_seq, int | float) and not isinstance(raw_seq, bool) else None
        if r.type == rc.self_paste_type:
            if seq is not None:
                ctx.self_paste.add(seq)
            continue
        ctx.warned_at = ctx.warned_at or r.ts
        if seq is not None:
            ctx.copy_at.setdefault(seq, r.ts)
            rid = payload.get("render_id")
            if isinstance(rid, str) and rid:
                ctx.copy_render.setdefault(seq, rid)
    return ctx


# ----------------------------------------------------------------------------- сводка по сессии


@dataclass
class SessionCtx:
    session_id: uuid.UUID
    summary: SessionTwinSummary
    rows: dict[int, AnticheatEvidence]  # seq → улика
    responses: dict[int, TestResponse]
    events: EventCtx
    twin_render: dict[int, str | None]

    def hit_index(self, seq: int) -> int | None:
        """Номер учтённого попадания в сессии (1 — первое); None — задание не учтено как попадание."""
        order = sorted(h.seq for h in self.summary.hits)
        return order.index(seq) + 1 if seq in order else None


def _ac(ev: AnticheatEvidence) -> AnswerClass:
    return AnswerClass(ev.kind, ev.matched, ev.strength, ev.tag)  # type: ignore[arg-type]


def session_ctx(db: Session, session_id: uuid.UUID, cfg: AntiCheatConfig | None = None) -> SessionCtx:
    cfg = cfg or load_anticheat_config()
    rc = runtime_config()
    evs = list(
        db.scalars(select(AnticheatEvidence).where(AnticheatEvidence.session_id == session_id).order_by(AnticheatEvidence.seq))
    )
    resp = {r.id: r for r in db.scalars(select(TestResponse).where(TestResponse.session_id == session_id))}
    events = load_event_ctx(db, session_id, rc)
    obs: list[TwinObservation] = []
    rows: dict[int, AnticheatEvidence] = {}
    responses: dict[int, TestResponse] = {}
    twin_render: dict[int, str | None] = {}
    for ev in evs:
        r = resp.get(ev.response_id)
        rows[ev.seq] = ev
        if r is not None:
            responses[ev.seq] = r
        twin_render[ev.seq] = ev.render_id
        copied_render = events.copy_render.get(ev.seq)
        obs.append(
            TwinObservation(
                seq=ev.seq,
                family_id=ev.family_id,
                variant_code=ev.variant_code,
                mutation_class=ev.mutation_class,
                mutation_subclass=ev.mutation_subclass,
                answer=ev.answer,
                answer_class=_ac(ev),
                honest_p=ev.honest_p,
                render_id=copied_render,
                twin_render_id=ev.render_id if copied_render is not None else None,
                self_paste=ev.seq in events.self_paste,
                issued_at=r.shown_at if r is not None else None,
                answered_at=r.answered_at if r is not None else None,
                copy_at=events.copy_at.get(ev.seq),
            )
        )
    summary = aggregate_session(obs, warned_at=events.warned_at, a11y_mode=False, cfg=cfg)
    return SessionCtx(session_id, summary, rows, responses, events, twin_render)


def session_twin_out(summary: SessionTwinSummary, cfg: AntiCheatConfig) -> dict[str, Any]:
    """Сводка по сессии для карточки (SessionTwinOut)."""
    d = summary.to_dict("methodist", cfg)
    return {
        "state": d["state"],
        "state_ru": d["state_ru"],
        "n_twin_items": d["n_twin_items"],
        "n_counted_hits": d["n_counted_hits"],
        "p_honest_text": d["p_honest_text"],
        "proposed_level_ru": d["proposed_level_ru"],
        "suggested_level_ru": cfg.text(f"levels_ru.{summary.suggested_level}"),
        "context_only": d["context_only"],
        "trust_weight": d["trust_weight"],
        "decision": d["decision"],
    }


def candidate_observations(db: Session, session_id: uuid.UUID) -> dict[str, Any]:
    """Что видит кандидат ПОСЛЕ закрытия сессии: номер задания и нейтральная фраза, без ключей и вероятностей."""
    return session_ctx(db, session_id).summary.to_dict("candidate")


# ----------------------------------------------------------------------------- лента


def _status_counts(db: Session) -> ReconciliationCountsOut:
    ev = dict(
        db.execute(
            select(AnticheatEvidence.review_status, func.count())
            .where(AnticheatEvidence.kind == "twin_trace")
            .group_by(AnticheatEvidence.review_status)
        ).all()
    )
    sb: dict[str, int] = {}
    for run in db.scalars(select(AnticheatSandboxRun).where(AnticheatSandboxRun.result.is_not(None))):
        n = sum(1 for i in (run.result or {}).get("items", []) if i.get("kind") == "twin_trace")
        if n:
            sb[run.review_status] = sb.get(run.review_status, 0) + n  # строка ленты — задание, а не мини-тест
    return ReconciliationCountsOut(
        open=int(ev.get(REVIEW_OPEN, 0)) + sb.get(REVIEW_OPEN, 0),
        verification_assigned=int(ev.get(REVIEW_VERIFICATION, 0)) + sb.get(REVIEW_VERIFICATION, 0),
        dismissed=int(ev.get(REVIEW_DISMISSED, 0)) + sb.get(REVIEW_DISMISSED, 0),
    )


def list_reconciliations(
    db: Session, *, status: str | None, include_sandbox: bool, limit: int, session_id: uuid.UUID | None = None
) -> tuple[list[dict[str, Any]], ReconciliationCountsOut]:
    cfg, rc = load_anticheat_config(), runtime_config()
    out: list[dict[str, Any]] = []
    q = (
        select(AnticheatEvidence, SessionRow.candidate_id)
        .join(SessionRow, SessionRow.id == AnticheatEvidence.session_id)
        .where(AnticheatEvidence.kind == "twin_trace")
        .order_by(AnticheatEvidence.created_at.desc())
        .limit(limit)
    )
    if status:
        q = q.where(AnticheatEvidence.review_status == status)
    if session_id is not None:
        q = q.where(AnticheatEvidence.session_id == session_id)
    cache: dict[uuid.UUID, SessionCtx] = {}
    for ev, candidate_id in db.execute(q).all():
        ctx = cache.get(ev.session_id) or cache.setdefault(ev.session_id, session_ctx(db, ev.session_id, cfg))
        out.append(_row_dict(db, ev, candidate_id, ctx, cfg, rc))
    if include_sandbox:
        from app.services.anticheat import sandbox  # noqa: PLC0415

        out.extend(sandbox.list_rows(db, status=status, limit=limit))
    out.sort(key=lambda r: r["_sort"], reverse=True)
    for r in out:
        r.pop("_sort", None)
    return out[:limit], _status_counts(db)


def _phase_of(ctx: SessionCtx, seq: int) -> str:
    for e in ctx.summary.items:
        if e.seq == seq:
            return e.phase
    return UNKNOWN


def _row_dict(
    db: Session, ev: AnticheatEvidence, candidate_id: uuid.UUID | None, ctx: SessionCtx, cfg: AntiCheatConfig, rc: RuntimeConfig
) -> dict[str, Any]:
    ac = _ac(ev)
    phase = _phase_of(ctx, ev.seq)
    resp = ctx.responses.get(ev.seq)
    return {
        "id": str(ev.id),
        "source": "session",
        "session_id": ev.session_id,
        "label": candidate_label(db, candidate_id, ev.seq, ev.variant_code, rc),
        "seq": ev.seq,
        "variant_code": ev.variant_code,
        "family_id": ev.family_id,
        "family_title": family_title(ev.family_id),
        "mutation_class": ev.mutation_class,
        "answer": ev.answer,
        "kind": ev.kind,
        "matched": ev.matched,
        "matched_ru": cfg.text(f"matched_ru.{ev.matched}"),
        "strength": ev.strength,
        "headline_ru": headline_for(ac, rc),
        "phase": phase,
        "phase_ru": cfg.text(f"phase_ru.{phase}"),
        "session_state": ctx.summary.state,
        "session_state_ru": cfg.text(f"states_ru.{ctx.summary.state}"),
        "review_status": ev.review_status,
        "review_status_ru": rc.text(f"review_status_ru.{ev.review_status}"),
        "answered_at": resp.answered_at if resp is not None else None,
        "demo_assisted": ev.demo_assisted,
        "_sort": ev.created_at,
    }


# ----------------------------------------------------------------------------- акт


def block_view(act: ReconciliationAct, changed: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Строки блока с подменой (код или таблица) и остальной текст условия над ним.

    Составной текст — формулировка, таблицы, код (twins.compose_text); блок кода — последний, блок таблицы заканчивается
    пустой строкой. Номера строк в блоке — с 1, как в редакторе (Figma «Акт сверки»)."""
    lines = list(act.lines)
    start = max(0, int(changed["line"]) - int(changed["block_line"]))
    if str(changed["block"]) == "code":
        end = len(lines)
    else:
        end = start + 1
        while end < len(lines) and lines[end].shown != "":
            end += 1
    block = [
        {
            "no": i,
            "shown": ln.shown,
            "clipboard": ln.clipboard,
            "changed": ln.changed,
            "shown_span": list(ln.shown_span) if ln.shown_span else None,
            "clipboard_span": list(ln.clipboard_span) if ln.clipboard_span else None,
        }
        for i, ln in enumerate(lines[start:end], start=1)
    ]
    context = "\n".join(ln.shown for ln in lines[:start]).strip()
    return block, context


def _stats(
    act: ReconciliationAct,
    *,
    first_hit: bool | None,
    summary: SessionTwinSummary | None,
    rc: RuntimeConfig,
) -> list[dict[str, str]]:
    twin_hit = act.answer_class.is_exact_twin
    prob = {
        "id": "probability",
        "value": probability_value(act.p_honest, rc),
        "caption": rc.text("stats.probability.caption"),
        "tone": "brand",
    }
    if act.phase == UNKNOWN and not twin_hit:
        phase_key = "unknown_phase"
    elif first_hit is True or act.phase == BEFORE:
        phase_key = "first"
    elif act.phase == AFTER:
        phase_key = "repeat"
    else:
        phase_key = "unknown_phase"
    phase = {
        "id": "phase",
        "value": rc.text(f"stats.{phase_key}.value"),
        "caption": rc.text(f"stats.{phase_key}.caption"),
        "tone": "neutral",
    }
    correct = act.answer_class.scored_correct
    scored_key = "scored_right" if correct else "scored_wrong"
    scored = {
        "id": "scored",
        "value": rc.text(f"stats.{scored_key}.value"),
        "caption": rc.text(f"stats.{scored_key}.caption"),
        "tone": "neutral" if correct else "danger",
    }
    context_only = True if summary is None else summary.context_only
    weight = 0.0 if summary is None else summary.trust_weight
    wkey = "weight_zero" if context_only else "weight_set"
    w = {
        "id": "weight",
        "value": rc.text(f"stats.{wkey}.value", w=f"{weight:g}"),
        "caption": rc.text(f"stats.{wkey}.caption"),
        "tone": "neutral",
    }
    return [prob, phase, scored, w]


def act_payload(
    twin: Twin,
    *,
    ident: str,
    source: str,
    label: str,
    answer: str,
    ac: AnswerClass,
    phase: str,
    p_honest: float,
    render_id: str | None,
    twin_render_id: str | None,
    self_paste: bool,
    copy_at: datetime | None,
    answered_at: datetime | None,
    warned_at: datetime | None,
    summary: SessionTwinSummary | None,
    first_hit: bool | None,
    review_status: str,
    review_note: str | None,
    reviewed_at: datetime | None,
    session_id: uuid.UUID | None,
    demo_assisted: bool,
) -> dict[str, Any]:
    cfg, rc = load_anticheat_config(), runtime_config()
    act = build_act(
        twin,
        answer=answer,
        answer_class=ac,
        phase=phase,  # type: ignore[arg-type]
        p_honest=p_honest,
        render_id=render_id,
        twin_render_id=twin_render_id,
        self_paste=self_paste,
        a11y_mode=False,
        copy_at=copy_at,
        answered_at=answered_at,
        warned_at=warned_at,
        cfg=cfg,
    )
    d = act.to_dict("methodist")
    lines, context = block_view(act, d["changed_literal"])
    means_key = "means_exact" if ac.is_exact_twin else ("means_context" if ac.kind == "twin_trace" else "means_other")
    return {
        "id": ident,
        "source": source,
        "session_id": session_id,
        "label": label,
        "headline_ru": headline_for(ac, rc),
        "lead_ru": rc.text("lead_ru"),
        "title_ru": d["title_ru"],
        "variant_code": d["variant_code"],
        "family_id": d["family_id"],
        "family_title": family_title(d["family_id"]),
        "mutation_class": d["mutation_class"],
        "mutation_subclass": d["mutation_subclass"],
        "changed_literal": d["changed_literal"],
        "block_name": str(d["changed_literal"]["block"]),
        "context_ru": context,
        "lines": lines,
        "keys": d["keys"],
        "candidate_answer": d["candidate_answer"],
        "kind": d["kind"],
        "matched": d["matched"],
        "matched_ru": d["matched_ru"],
        "strength": d["strength"],
        "verdict_ru": d["verdict_ru"],
        "phase": d["phase"],
        "phase_ru": d["phase_ru"],
        "copy_at": copy_at,
        "answered_at": answered_at,
        "warned_at": warned_at,
        "p_honest": p_honest,
        "p_honest_text": d["p_honest_text"],
        "stats": _stats(act, first_hit=first_hit, summary=summary, rc=rc),
        "checklist": d["checklist"],
        "means_title": rc.text("means_title"),
        "means_ru": rc.text(means_key),
        "session": session_twin_out(summary, cfg) if summary is not None else None,
        "review_status": review_status,
        "review_status_ru": rc.text(f"review_status_ru.{review_status}"),
        "review_note": review_note,
        "reviewed_at": reviewed_at,
        "actions": _actions(review_status, rc),
        "demo_assisted": demo_assisted,
        "auto_ban": False,
    }


def _actions(status: str, rc: RuntimeConfig) -> list[dict[str, str]]:
    ids = ["reopen"] if status != REVIEW_OPEN else ["assign_verification", "dismiss"]
    return [{"id": i, "label": rc.text(f"actions_ru.{i}")} for i in ids]


def get_act(db: Session, ident: str) -> dict[str, Any]:
    """Акт сверки по идентификатору: uuid улики сессии или `sb-<тест>-<номер>` песочницы."""
    if ident.startswith(SANDBOX_PREFIX):
        from app.services.anticheat import sandbox  # noqa: PLC0415

        return sandbox.act_for(db, ident)
    ev = _evidence_by_ident(db, ident)
    cfg, rc = load_anticheat_config(), runtime_config()
    trow = store.get_row(db, ev.instance_id)
    twin = store.twin_from_row(trow) if trow is not None else None
    if twin is None:
        raise not_found("twin_not_found", "Двойник этого задания не найден")
    ctx = session_ctx(db, ev.session_id, cfg)
    candidate_id = db.scalar(select(SessionRow.candidate_id).where(SessionRow.id == ev.session_id))
    resp = ctx.responses.get(ev.seq)
    copied_render = ctx.events.copy_render.get(ev.seq)
    hit = ctx.hit_index(ev.seq)
    return act_payload(
        twin,
        ident=str(ev.id),
        source="session",
        label=candidate_label(db, candidate_id, ev.seq, ev.variant_code, rc),
        answer=ev.answer,
        ac=_ac(ev),
        phase=_phase_of(ctx, ev.seq),
        p_honest=ev.honest_p,
        render_id=copied_render,
        twin_render_id=ev.render_id if copied_render is not None else None,
        self_paste=ev.seq in ctx.events.self_paste,
        copy_at=ctx.events.copy_at.get(ev.seq),
        answered_at=resp.answered_at if resp is not None else None,
        warned_at=ctx.events.warned_at,
        summary=ctx.summary,
        first_hit=(hit == 1) if hit is not None else None,
        review_status=ev.review_status,
        review_note=ev.review_note,
        reviewed_at=ev.reviewed_at,
        session_id=ev.session_id,
        demo_assisted=ev.demo_assisted,
    )


def _evidence_by_ident(db: Session, ident: str, *, lock: bool = False) -> AnticheatEvidence:
    try:
        uid = uuid.UUID(ident)
    except ValueError:
        raise not_found("reconciliation_not_found", "Сверка не найдена") from None
    q = select(AnticheatEvidence).where(AnticheatEvidence.id == uid)
    if lock:
        q = q.with_for_update()
    ev = db.scalars(q).first()
    if ev is None or ev.kind != "twin_trace":
        raise not_found("reconciliation_not_found", "Сверка не найдена")
    return ev


# ----------------------------------------------------------------------------- решение методиста


def review(db: Session, ident: str, action: str, user_id: uuid.UUID, note: str | None) -> dict[str, Any]:
    """Назначить подтверждение / снять отметку / вернуть в очередь. Автобана нет: решает только методист.

    «Снять отметку» помечает запись; пересчёт уровня без спорного задания (spec §2.1, S7) — отдельный шаг методиста
    по правилам флагов лаборатории. Работодатель решения и причины не видит: только итоговый уровень доверия."""
    status = _ACTION_STATUS.get(action)
    if status is None or status not in REVIEW_STATUSES:
        raise bad_request("unknown_action", "Неизвестное действие")
    note = (note or "").strip() or None
    if note is not None and len(note) > runtime_config().note_max_chars:
        raise bad_request("note_too_long", "Комментарий слишком длинный")
    now = clock.now()
    if ident.startswith(SANDBOX_PREFIX):
        from app.services.anticheat import sandbox  # noqa: PLC0415

        sandbox.set_review(db, ident, status, user_id, note, now)
    else:
        ev = _evidence_by_ident(db, ident, lock=True)
        ev.review_status = status
        ev.review_note = note
        ev.reviewed_by = user_id
        ev.reviewed_at = now
        db.flush()
    return get_act(db, ident)
