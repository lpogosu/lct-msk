"""Лаборатория (Л1–Л4, роль «методист»): трасса сессии, банк, «3 клона рядом», очередь флагов,
валидация, калибровка фазы 1 (spec-testing §2.4, §3.4 п.7, §3.9, §3.10, §5; concept §9.4).

Трасса — внутренний вид: уровень и погрешность на внутренней шкале подписаны «уровень»/«погрешность»
(DECISIONS п. 10 — никакого «θ» в текстах). Ключи клонов показываются только методисту и только для
клонов из отдельного диапазона seed, которые никогда не выдаются кандидатам (limits.PREVIEW_SEED_BASE).
Калибровка — app.testing.calibration (MAP по b при известных LOO-оценках), здесь только сбор ответов.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.errors import bad_request, conflict, not_found
from app.models.enums import EXCLUSIVE_SESSION_KINDS, ConsentPurpose, FamilyStatus, FlagStatus
from app.models.testing import (
    CalibrationRun,
    DriftEvent,
    ItemFamilyParams,
    ItemInstance,
    TestResponse,
    ValidationRun,
)
from app.models.testing import Flag as FlagRow
from app.models.testing import TestSession as SessionRow
from app.models.users import Consent, User
from app.services.testing import labels as L
from app.services.testing.catalog import family_metas
from app.services.testing.integrity import chain_session_ids, session_flag_rows
from app.services.testing.limits import PREVIEW_SEED_BASE
from app.services.testing.pool import EMPTY_STATS, families_for, pool_stats
from app.services.testing.qualification import get_row, load_domain, save_domain
from app.services.testing.schemas import (
    BankFamilyOut,
    BankOut,
    CalibrateOut,
    CloneOut,
    ClonesOut,
    CodeLabel,
    FamilyScaleParamsOut,
    FlagResolveOut,
    LabFlagOut,
    LabSessionOut,
    OptionOut,
    PoolOut,
    TraceOut,
    TraceStepOut,
    ValidationOut,
    ValidationRunOut,
)
from app.services.testing.sessions import bank_params_version, save, tick, wrap
from app.testing.bank.registry import all_families
from app.testing.calibration import calibration_run
from app.testing.cat import module_names
from app.testing.detectors import CusumState, drift_probs, load_detector_config, trust_from_flags
from app.testing.irt import ItemParams, Posterior, eap, load_scale
from app.testing.session import SessionStatus

TRACE_LABELS = {
    "level": "уровень",
    "error": "погрешность",
    "score": "балл",
    "p_pass": "вероятность «выше порога»",
    "difficulty": "сложность задания",
}
MAX_CLONES = 5
# Ответы для калибровки: решённые сессии (брошенные и аннулированные — нет), без режима жюри
CALIBRATION_STATUSES = (
    SessionStatus.FINAL.value,
    SessionStatus.DECIDED.value,
    SessionStatus.EXTENSION_OFFERED.value,
)
# Сессия ещё может получить флаги и закрыться — по её флагам методист решает после закрытия
SESSION_NOT_CLOSED = tuple(
    s.value
    for s in (
        SessionStatus.CREATED,
        SessionStatus.IN_PROGRESS,
        SessionStatus.PAUSED,
        SessionStatus.DECIDED,
        SessionStatus.EXTENSION_OFFERED,
    )
)


def _uuid(value: str | uuid.UUID, what: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        raise not_found(f"{what}_not_found", "Запись не найдена") from None


# ---------------------------------------------------------------------------
# Флаги
# ---------------------------------------------------------------------------


def lab_flag_out(f: FlagRow, session: SessionRow | None) -> LabFlagOut:
    d = f.details or {}
    return LabFlagOut(
        id=f.id,
        session_id=f.session_id,
        candidate_id=session.candidate_id if session is not None else None,
        session_status=session.status if session is not None else None,
        kind=f.kind,
        severity=f.severity,
        value=f.value,
        message=str(d.get("message_ru") or ""),
        family_id=d.get("family_id"),
        source_session_id=f.source_session_id,
        evidence=dict(d["signal"]) if isinstance(d.get("signal"), dict) else None,
        status=f.status,
        dispute_text=f.dispute_text,
        created_at=f.created_at,
    )


def flags_out(db: Session, *, status: str | None, kind: str | None, limit: int) -> list[LabFlagOut]:
    q = select(FlagRow, SessionRow).join(SessionRow, SessionRow.id == FlagRow.session_id)
    if status:
        q = q.where(FlagRow.status == status)
    if kind:
        q = q.where(FlagRow.kind == kind)
    # Сначала жёсткие и оспоренные — их методист разбирает первыми
    q = q.order_by(
        (FlagRow.severity == "hard").desc(), FlagRow.disputed_at.is_(None), FlagRow.created_at.desc()
    ).limit(limit)
    return [lab_flag_out(f, s) for f, s in db.execute(q)]


def _refresh_qualification_trust(db: Session, row: SessionRow) -> None:
    """Флаг снят у закрытой сессии → доверие грейда пересчитывается по сессиям, на которых он держится."""
    qrow = get_row(db, row.candidate_id, row.specialization, lock=True) if row.candidate_id else None
    if qrow is None:
        return
    q = load_domain(qrow)
    if str(row.id) not in q.grade_session_ids or q.hidden:
        return
    ids = [_uuid(s, "session") for s in q.grade_session_ids]
    trusts = [t for t in db.scalars(select(SessionRow.trust).where(SessionRow.id.in_(ids))) if t is not None]
    if not trusts:
        return
    q.chain_trust = min(trusts)
    q.trust = q.chain_trust
    save_domain(db, qrow, q)


def resolve_flag(db: Session, methodist: User, flag_id: str, decision: str, comment: str | None) -> FlagResolveOut:
    """Подтвердить/снять флаг. Когда у сессии на проверке не осталось открытых флагов — решение по
    сессии: если подтверждённые флаги всё ещё требуют проверки (чужой ключ, ≥ 3 мягких) — аннулировать,
    иначе оставить с доверием 0.75 (spec-testing §2.4: UNDER_REVIEW → FINAL(0.75) | INVALIDATED)."""
    fid = _uuid(flag_id, "flag")
    f = db.scalars(select(FlagRow).where(FlagRow.id == fid).with_for_update()).first()
    if f is None:
        raise not_found("flag_not_found", "Флаг не найден")
    if f.status != FlagStatus.open.value:
        raise conflict("flag_already_resolved", "Флаг уже разобран")
    row = db.scalars(select(SessionRow).where(SessionRow.id == f.session_id).with_for_update()).one()
    # Таймеры: брошенная кандидатом сессия (тишина > 30 мин, добор не принят за 24 ч) закрывается здесь же
    tick(db, wrap(db, row))
    if row.status in SESSION_NOT_CLOSED:
        # Решение по флагу — после закрытия сессии. Иначе подтверждённый посреди теста флаг закрыл бы её
        # в UNDER_REVIEW без открытых флагов, и проверку было бы нечем завершить (кандидат скрыт навсегда)
        raise conflict(
            "session_not_closed",
            "Сессия ещё не завершена: решение по флагу — после её окончания",
            {"session_status": row.status},
        )
    now = clock.now()
    f.status = FlagStatus.confirmed.value if decision == "confirm" else FlagStatus.dismissed.value
    f.resolved_by = methodist.id
    f.resolved_at = now
    details = dict(f.details or {})
    if comment:
        details["comment"] = comment.strip()[:1000]
    f.details = details
    db.flush()

    ld = wrap(db, row)
    sess = ld.sess
    review_resolved = False
    message = "Флаг подтверждён" if decision == "confirm" else "Флаг снят"
    open_left = [x for x in sess.flags if x.status == FlagStatus.open.value]
    if sess.status == SessionStatus.UNDER_REVIEW and not open_left:
        verdict = trust_from_flags(sess.flags)
        keep = not verdict.under_review
        sess.review(keep, clock)
        save(db, ld)
        qrow = get_row(db, row.candidate_id, row.specialization, lock=True) if row.candidate_id else None
        if qrow is not None:
            q = load_domain(qrow)
            q.resolve_review(keep, clock, session_id=str(row.id))
            save_domain(db, qrow, q)
        review_resolved = True
        message = (
            "Проверка завершена: результат оставлен, доверие снижено"
            if keep
            else "Проверка завершена: результат аннулирован"
        )
    elif sess.status == SessionStatus.FINAL and decision == "dismiss":
        verdict = trust_from_flags(sess.flags)
        if not verdict.under_review and verdict.trust is not None and (sess.trust or 0) < verdict.trust:
            sess.trust = verdict.trust
            save(db, ld)
            _refresh_qualification_trust(db, row)
            message = "Флаг снят, доверие к результату восстановлено"
    db.flush()
    return FlagResolveOut(
        flag=lab_flag_out(f, row), session_status=str(sess.status), review_resolved=review_resolved, message=message
    )


# ---------------------------------------------------------------------------
# Сессии и трасса
# ---------------------------------------------------------------------------


def sessions_out(db: Session, *, status: str | None, spec: str | None, limit: int) -> list[LabSessionOut]:
    open_flags = (
        select(FlagRow.session_id, func.count().label("n"))
        .where(FlagRow.status == FlagStatus.open.value)
        .group_by(FlagRow.session_id)
        .subquery()
    )
    q = select(SessionRow, open_flags.c.n).outerjoin(open_flags, open_flags.c.session_id == SessionRow.id)
    if status:
        q = q.where(SessionRow.status == status)
    if spec:
        q = q.where(SessionRow.specialization == spec)
    q = q.order_by(SessionRow.created_at.desc(), SessionRow.id).limit(limit)
    return [
        LabSessionOut(
            id=r.id,
            candidate_id=r.candidate_id,
            specialization=r.specialization,
            kind=r.kind,
            status=r.status,
            claimed_grade=r.claimed_grade,
            decision=r.decision,
            n_items=r.n_items,
            score=L.score_of(r.theta),
            trust=r.trust,
            flags_open=int(n or 0),
            demo_assisted=r.demo_assisted,
            parent_session_id=r.parent_session_id,
            started_at=r.started_at,
            finished_at=r.finished_at,
        )
        for r, n in db.execute(q)
    ]


def trace_out(db: Session, session_id: str) -> TraceOut:
    sid = _uuid(session_id, "session")
    row = db.get(SessionRow, sid)
    if row is None:
        raise not_found("session_not_found", "Сессия не найдена")
    if not row.state:
        raise conflict("trace_unavailable", "У сессии нет сохранённого состояния движка — трассу не построить")
    ld = wrap(db, row)
    sess = ld.sess
    scale = load_scale()
    chain = chain_session_ids(db, row)
    resp_rows = {
        r.seq: r for r in db.scalars(select(TestResponse).where(TestResponse.session_id.in_(chain)))
    }
    inst_ids = [r.instance_id for r in resp_rows.values()]
    variants = (
        dict(db.execute(select(ItemInstance.id, ItemInstance.variant_code).where(ItemInstance.id.in_(inst_ids))).all())
        if inst_ids
        else {}
    )
    steps = []
    for r in sess.cat.responses:
        tr = resp_rows.get(r.seq)
        steps.append(
            TraceStepOut(
                seq=r.seq,
                phase=r.phase,
                family_id=r.family_id,
                module=r.module,
                family_grade=r.family_grade,
                is_pretest=r.is_pretest,
                correct=r.correct,
                timed_out=r.timed_out,
                rt_ms=r.rt_ms,
                variant_code=f"#{variants[tr.instance_id]}" if tr is not None and tr.instance_id in variants else None,
                difficulty=round(r.b, 3),
                target_grade=r.target_grade,
                level=r.theta_after,
                error=r.se_after,
                score=round(scale.score(r.theta_after)) if r.theta_after is not None else None,
                p_pass=r.p_after,
                reason=dict(r.selection_reason or {}),
            )
        )
    flags = [lab_flag_out(f, row) for f in session_flag_rows(db, row.id)]
    return TraceOut(
        session_id=row.id,
        candidate_id=row.candidate_id,
        specialization=row.specialization,
        kind=row.kind,
        status=row.status,
        claimed_grade=row.claimed_grade,
        target_grade=sess.cat.target_grade,
        target_level=scale.cut(sess.cat.target_grade),
        decision=row.decision,
        labels=TRACE_LABELS,
        seed=row.seed,
        steps=steps,
        journal=[dict(x) for x in sess.cat.log],
        flags=flags,
    )


# ---------------------------------------------------------------------------
# Банк
# ---------------------------------------------------------------------------


def _collision_rates(db: Session, family_ids: list[str]) -> dict[str, float]:
    if not family_ids:
        return {}
    rows = db.execute(
        select(ItemInstance.family_id, func.count(), func.count(func.distinct(ItemInstance.key_hash)))
        .where(ItemInstance.family_id.in_(family_ids))
        .group_by(ItemInstance.family_id)
    )
    return {fid: (1.0 - d / n) if n else 1.0 for fid, n, d in rows}


def bank_out(db: Session, spec: str | None) -> BankOut:
    if spec is not None and L.spec_info(spec) is None:
        raise bad_request("unknown_specialization", "Неизвестная специализация")
    fams = families_for(spec)
    ids = [f.id for f in fams]
    params: dict[str, list[ItemFamilyParams]] = defaultdict(list)
    if ids:
        for p in db.scalars(
            select(ItemFamilyParams)
            .where(ItemFamilyParams.family_id.in_(ids))
            .order_by(ItemFamilyParams.family_id, ItemFamilyParams.specialization)
        ):
            if spec is None or p.specialization == spec:
                params[p.family_id].append(p)
    stats = pool_stats(db, ids)
    collisions = _collision_rates(db, ids)
    names = module_names()
    by_status: dict[str, int] = defaultdict(int)
    out = []
    for f in fams:
        ps = params.get(f.id, [])
        status = ps[0].status if ps else "not_seeded"
        by_status[status] += 1
        st = stats.get(f.id, EMPTY_STATS)
        total, free = st.total, st.free
        out.append(
            BankFamilyOut(
                family_id=f.id,
                title=str(getattr(f, "title", f.id)),
                module=CodeLabel(code=f.module, label=names.get(f.module, f.module)),
                format=str(f.format),
                specs=list(f.specs),
                target_grade=f.target_grade,
                position=float(f.position),
                guessing=round(float(f.c), 4),
                time_limit_s=int(f.time_limit_s),
                is_anchor=bool(f.is_anchor),
                enemies=list(f.enemies),
                params=[
                    FamilyScaleParamsOut(
                        specialization=p.specialization,
                        status=p.status,
                        difficulty=round(p.b, 4),
                        difficulty_sd=round(p.sigma_b, 4),
                        source=p.source,
                        n_responses=p.n_responses,
                        r_pb=p.r_pb,
                        cusum=round(p.cusum_s, 4),
                        exposure_30d=round(p.exposure_30d, 4),
                        params_version=p.params_version,
                        calibrated_at=p.calibrated_at,
                    )
                    for p in ps
                ],
                pool=PoolOut(
                    total=total,
                    free=free,
                    issued=total - free,
                    legacy_free=st.legacy_free,
                    key_collision_rate=round(collisions.get(f.id, 1.0 if not total else 0.0), 4),
                ),
            )
        )
    return BankOut(specialization=spec, total=len(out), by_status=dict(by_status), families=out)


def clones_out(db: Session, family_id: str, n: int) -> ClonesOut:
    """«3 клона рядом»: код и ключ разные, сложность одна (spec-testing §2.4 ревью, §2.6 п.3)."""
    fam = all_families().get(family_id)
    if fam is None:
        raise not_found("family_not_found", "Семейство не найдено")
    n = max(1, min(n, MAX_CLONES))
    p = db.scalars(
        select(ItemFamilyParams).where(ItemFamilyParams.family_id == family_id).order_by(ItemFamilyParams.specialization)
    ).first()
    clones = []
    for i in range(n):
        inst = fam.generate(PREVIEW_SEED_BASE + i)
        pub = inst.rendered.to_public()
        clones.append(
            CloneOut(
                seed=inst.seed,
                variant_code=f"#{inst.variant_code}",
                prompt=pub["prompt"],
                code=pub["code"],
                language=pub["language"],
                options=[OptionOut(id=o["id"], text=o["text"]) for o in pub["options"]],
                lines=list(pub["lines"]),
                table=pub["table"],
                answer_mode=pub["answer_mode"],
                key=inst.key,
                distractor_tags=sorted(set(inst.distractors.values())),
                explanation=inst.explanation or None,
            )
        )
    return ClonesOut(
        family_id=fam.id,
        title=str(getattr(fam, "title", fam.id)),
        difficulty=round(p.b, 4) if p is not None else None,
        clones=clones,
    )


# ---------------------------------------------------------------------------
# Валидация
# ---------------------------------------------------------------------------


def validation_out(db: Session, kind: str | None) -> ValidationOut:
    kinds = sorted(set(db.scalars(select(ValidationRun.kind).distinct())))
    q = select(ValidationRun).order_by(ValidationRun.created_at.desc(), ValidationRun.id).limit(1)
    if kind:
        q = q.where(ValidationRun.kind == kind)
    run = db.scalars(q).first()
    if run is None:
        return ValidationOut(
            available=False,
            kinds=kinds,
            message="Прогонов валидации ещё нет: запустите `make validate` (eval) — результат появится здесь",
        )
    return ValidationOut(
        available=True,
        kinds=kinds,
        run=ValidationRunOut(
            id=run.id,
            kind=run.kind,
            config=dict(run.config or {}),
            seeds=list(run.seeds or []),
            metrics=dict(run.metrics or {}),
            git_sha=run.git_sha,
            created_at=run.created_at,
        ),
    )


# ---------------------------------------------------------------------------
# Калибровка (фаза 1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Obs:
    """Ответ для калибровки и CUSUM: LOO-апостериор отвечавшего (ответ на зачётное задание исключён)."""

    family_id: str
    answered_at: datetime
    post: Posterior
    correct: bool

    @property
    def theta(self) -> float:
        return self.post.mean


def _observations(db: Session, spec: str) -> list[_Obs]:
    """Ответы решённых сессий шкалы spec в порядке времени.

    Только кандидаты с действующим согласием «calibration» (spec-testing §2.2), без режима жюри, только
    решённые сессии. LOO: ответ на зачётное задание исключается из оценки уровня; pretest в оценку и так
    не входит (§3.9, «по pretest-слотам»)."""
    consented = select(Consent.user_id).where(
        Consent.purpose == ConsentPurpose.calibration.value, Consent.revoked_at.is_(None)
    )
    rows = db.execute(
        select(TestResponse, SessionRow)
        .join(SessionRow, SessionRow.id == TestResponse.session_id)
        .where(
            SessionRow.specialization == spec,
            SessionRow.kind.in_(EXCLUSIVE_SESSION_KINDS),
            SessionRow.demo_assisted.is_(False),
            SessionRow.status.in_(CALIBRATION_STATUSES),
            SessionRow.candidate_id.in_(consented),
            TestResponse.answered_at.is_not(None),
            TestResponse.is_correct.is_not(None),
        )
        .order_by(TestResponse.answered_at, TestResponse.id)
    ).all()
    scale = load_scale()
    posts: dict[uuid.UUID, tuple[Posterior, dict[int, Any]]] = {}
    out: list[_Obs] = []
    for resp, srow in rows:
        if not srow.state:
            continue  # сессия без снимка движка (не из API теста) — оценки уровня нет, в калибровку не идёт
        if srow.id not in posts:
            sess = wrap(db, srow).sess
            post = eap(((r.item, r.correct) for r in sess.cat.scored), scale)
            posts[srow.id] = (post, {r.seq: r for r in sess.cat.responses})
        post, by_seq = posts[srow.id]
        rec = by_seq.get(resp.seq)
        if rec is None:
            continue
        loo = post if rec.is_pretest else post.copy().remove(rec.item, rec.correct)
        out.append(_Obs(resp.family_id, resp.answered_at, loo, bool(resp.is_correct)))
    return out


def _drift_scan(
    db: Session, spec: str, obs: list[_Obs], params: dict[str, ItemFamilyParams], now: datetime
) -> list[dict[str, Any]]:
    """CUSUM дрейфа семейства по новым ответам (spec-testing §3.10): p0 — ожидаемая верность при текущей b,
    p1 — при b − 0.5 («семейство стало легче»: слит метод). S > h → QUARANTINE + DriftEvent.
    Формулы — app.testing.detectors (drift_probs, CusumState); статистика копится в ItemFamilyParams.cusum_s."""
    cfg = load_detector_config()
    scale = load_scale()
    fams = all_families()
    by_family: dict[str, list[_Obs]] = defaultdict(list)
    for o in obs:
        by_family[o.family_id].append(o)
    out: list[dict[str, Any]] = []
    for fid in sorted(by_family):
        p = params.get(fid)
        fam = fams.get(fid)
        if p is None or fam is None or p.status != FamilyStatus.operational.value:
            continue
        item = ItemParams.from_family(p.b, p.sigma_b, float(fam.c), scale)
        st = CusumState(s=float(p.cusum_s or 0.0))
        for o in by_family[fid]:
            p0, p1 = drift_probs(o.post, item, cfg.cusum_shift)
            st.step(o.correct, p0, p1, cfg.cusum_h)
        p.cusum_s = round(st.s, 4)
        entry: dict[str, Any] = {"family_id": fid, "n": len(by_family[fid]), "cusum": p.cusum_s, "action": None}
        if st.s > cfg.cusum_h:
            p.status = FamilyStatus.quarantine.value
            db.add(
                DriftEvent(family_id=fid, specialization=spec, at=now, statistic="cusum", value=st.s, action="quarantine")
            )
            entry["action"] = "quarantine"
        out.append(entry)
    db.flush()
    return out


def calibrate(db: Session, spec: str | None) -> CalibrateOut:
    """Кнопка «Лаборатории» / ночная задача: CUSUM дрейфа по ответам после прошлого прогона, затем MAP-уточнение
    сложности по ответам после прошлой калибровки семейства. Семейство, ушедшее в карантин в этом прогоне,
    не уточняется: перекалибровка в карантине — по новым ответам (§2.4)."""
    if spec is not None:
        if L.spec_info(spec) is None:
            raise bad_request("unknown_specialization", "Неизвестная специализация")
        if not L.spec_testable(spec):
            raise bad_request("specialization_not_testable", "По этой специализации теста нет — калибровать нечего")
        specs = [spec]
    else:
        specs = [s["code"] for s in L.taxonomy()["specializations"] if L.spec_testable(s["code"])]
    now = clock.now()
    version = bank_params_version(db) + 1
    last_run = db.scalar(select(func.max(CalibrationRun.at)))
    reports: dict[str, Any] = {}
    total_n = 0
    updated = 0
    for sp in specs:
        metas = family_metas(db, sp, now)  # статусы до сканирования дрейфа
        params = {
            p.family_id: p
            for p in db.scalars(
                select(ItemFamilyParams).where(ItemFamilyParams.specialization == sp).with_for_update()
            )
        }
        obs = _observations(db, sp)
        drift = _drift_scan(db, sp, [o for o in obs if last_run is None or o.answered_at > last_run], params, now)
        quarantined_now = {d["family_id"] for d in drift if d["action"] == "quarantine"}
        responses: dict[str, list[tuple[float, bool]]] = defaultdict(list)
        for o in obs:
            cut = params[o.family_id].calibrated_at if o.family_id in params else None
            if cut is None or o.answered_at > cut:
                responses[o.family_id].append((o.theta, o.correct))
        rep = calibration_run([m for m in metas if m.id not in quarantined_now], responses, sp)
        for entry in rep["families"]:
            p = params.get(entry["family_id"])
            if p is None or not entry["updated"]:
                continue
            # Апостериор становится новым приором (§3.9); якоря calibrate_family не обновляет
            p.b = float(entry["b"])
            p.sigma_b = float(entry["se_b"])
            p.source = "map"
            p.n_responses = int(p.n_responses or 0) + int(entry["n"])
            p.calibrated_at = now
            p.params_version = int(p.params_version or 1) + 1
            if entry.get("r_pb") is not None:
                p.r_pb = float(entry["r_pb"])
            if entry.get("next_status"):
                p.status = str(entry["next_status"])
        rep["drift"] = drift
        reports[sp] = rep
        total_n += int(rep["n_responses"])
        updated += int(rep["families_updated"])
    run = CalibrationRun(
        phase=1, n_responses=total_n, families_updated=updated, report=reports, params_version=version, at=now
    )
    db.add(run)
    db.flush()
    return CalibrateOut(
        run_id=run.id, specializations=specs, n_responses=total_n, families_updated=updated, report=reports
    )
