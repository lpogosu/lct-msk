"""«Акт сверки» для методиста и лаборатории: показано / попало в буфер, рядом, с подсвеченным литералом.

Что в акте (spec-antiassist §2.1 «Что видит методист», killer-features §2.1 п. 1):
- две колонки: показанная версия X и копия X′ из буфера обмена, изменённый литерал подсвечен диапазонами;
- оба ключа (экрана и копии) и ответ кандидата, какому ключу он соответствует;
- время копирования и ответа, отметка «до/после предупреждения»;
- вероятность такого совпадения у честного кандидата («ожидаемо N на 10 000 сессий»);
- чек-лист альтернативных объяснений (self-paste, устойчивое заблуждение, путаница глифов, устаревшая
  копия, режим доступности);
- вердикт простым русским языком, БЕЗ обвинений: только наблюдаемые факты, слова «ИИ» и «списал» нет.
Кандидату (после закрытия сессии) отдаётся только номер задания и фраза «ответ не соответствует показанному условию».
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.testing.anticheat.config import AntiCheatConfig, load_anticheat_config
from app.testing.anticheat.evidence import (
    UNKNOWN,
    AnswerClass,
    Phase,
    classify_answer_ex,
    format_count,
    format_probability,
)
from app.testing.anticheat.twins import Twin
from app.testing.bank.base import Family, Instance


@dataclass(frozen=True)
class Span:
    """Диапазон [start, end) в составном тексте; line с 1, col с 0."""

    start: int
    end: int
    line: int
    col: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "line": self.line, "col": self.col, "text": self.text}


@dataclass(frozen=True)
class ActLine:
    no: int
    shown: str
    clipboard: str
    changed: bool
    shown_span: tuple[int, int] | None = None  # колонки в строке (с 0), [от, до)
    clipboard_span: tuple[int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "no": self.no,
            "shown": self.shown,
            "clipboard": self.clipboard,
            "changed": self.changed,
            "shown_span": list(self.shown_span) if self.shown_span else None,
            "clipboard_span": list(self.clipboard_span) if self.clipboard_span else None,
        }


@dataclass(frozen=True)
class ChecklistItem:
    id: str
    title_ru: str
    status: str  # ok | attention | excluded | unknown
    status_ru: str
    note_ru: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title_ru": self.title_ru,
            "status": self.status,
            "status_ru": self.status_ru,
            "note_ru": self.note_ru,
        }


@dataclass(frozen=True)
class ReconciliationAct:
    variant_code: str
    family_id: str
    mutation_class: str
    mutation_subclass: str
    render_id: str | None
    title_ru: str
    shown_text: str
    clipboard_text: str
    shown_span: Span
    clipboard_span: Span
    lines: tuple[ActLine, ...]
    changed_literal: dict[str, Any]
    key_shown: str
    key_clipboard: str
    candidate_answer: str
    answer_class: AnswerClass
    matched_ru: str
    phase: Phase
    phase_ru: str
    p_honest: float
    p_honest_text: str
    expected_per_10k: float
    copy_at: datetime | None
    answered_at: datetime | None
    warned_at: datetime | None
    checklist: tuple[ChecklistItem, ...]
    verdict_ru: str
    candidate_message_ru: str

    def to_dict(self, audience: str = "methodist") -> dict[str, Any]:
        if audience not in ("methodist", "candidate"):
            raise ValueError(f"акт сверки отдаётся только методисту и кандидату, не «{audience}»")
        if audience == "candidate":
            return self.for_candidate()
        return {
            "title_ru": self.title_ru,
            "variant_code": self.variant_code,
            "family_id": self.family_id,
            "mutation_class": self.mutation_class,
            "mutation_subclass": self.mutation_subclass,
            "render_id": self.render_id,
            "shown_text": self.shown_text,
            "clipboard_text": self.clipboard_text,
            "shown_span": self.shown_span.to_dict(),
            "clipboard_span": self.clipboard_span.to_dict(),
            "lines": [ln.to_dict() for ln in self.lines],
            "changed_literal": dict(self.changed_literal),
            "keys": {"shown": self.key_shown, "clipboard": self.key_clipboard},
            "candidate_answer": self.candidate_answer,
            "kind": self.answer_class.kind,
            "matched": self.answer_class.matched,
            "matched_ru": self.matched_ru,
            "strength": self.answer_class.strength,
            "phase": self.phase,
            "phase_ru": self.phase_ru,
            "p_honest": self.p_honest,
            "p_honest_text": self.p_honest_text,
            "expected_per_10k": self.expected_per_10k,
            "copy_at": self.copy_at.isoformat() if self.copy_at else None,
            "answered_at": self.answered_at.isoformat() if self.answered_at else None,
            "warned_at": self.warned_at.isoformat() if self.warned_at else None,
            "checklist": [c.to_dict() for c in self.checklist],
            "verdict_ru": self.verdict_ru,
        }

    def for_candidate(self) -> dict[str, Any]:
        """Только номер задания и нейтральная фраза: ключи, копия и вероятности кандидату не отдаются.

        Фраза «ответ не соответствует показанному условию» — только при совпадении с ключом копии. Верный ответ,
        типичная ошибка и слабые следы (значение не с экрана, ошибка на копии) кандидату не показываются: пустой словарь.
        """
        if not self.answer_class.is_exact_twin:
            return {}
        cfg = load_anticheat_config()
        return {
            "variant_code": self.variant_code,
            "message_ru": self.candidate_message_ru,
            "actions": list(cfg.raw_text("candidate_actions")),
        }


def _lines(shown: str, clip: str, off: int, old: str, new: str) -> tuple[ActLine, ...]:
    s_lines, c_lines = shown.split("\n"), clip.split("\n")
    out: list[ActLine] = []
    pos = 0
    for i, (a, b) in enumerate(zip(s_lines, c_lines, strict=True), start=1):
        changed = a != b
        s_span = c_span = None
        if changed and pos <= off <= pos + len(a):
            col = off - pos
            s_span, c_span = (col, col + len(old)), (col, col + len(new))
        out.append(ActLine(i, a, b, changed, s_span, c_span))
        pos += len(a) + 1
    return tuple(out)


def _span(text: str, start: int, end: int) -> Span:
    seg = text[:start]
    return Span(start, end, seg.count("\n") + 1, start - (seg.rfind("\n") + 1), text[start:end])


def _verdict(cfg: AntiCheatConfig, twin: Twin, ac: AnswerClass, answer: str) -> str:
    c = twin.changed_literal
    kw: dict[str, Any] = {
        "answer": answer,
        "key": twin.key,
        "twin_key": twin.twin_key,
        "from": c.from_,
        "to": c.to,
        "line": c.block_line,
        "tag": ac.tag or "",
    }
    if ac.kind == "correct":
        return cfg.text("verdict.correct", **kw)
    if ac.kind == "misconception":
        return cfg.text("verdict.misconception", **kw)
    if ac.kind == "twin_trace":
        name = {"exact": "twin_exact", "err_twin": "twin_err"}.get(ac.strength or "", "twin_token")
        return cfg.text(f"verdict.{name}", **kw)
    return cfg.text("verdict.other", **kw)


def build_act(
    twin: Twin,
    *,
    answer: Any,
    answer_class: AnswerClass | None = None,
    distractors: Mapping[str, str] | None = None,
    normalize: Callable[[Any], Any] | None = None,
    phase: Phase = UNKNOWN,
    p_honest: float | None = None,
    render_id: str | None = None,
    twin_render_id: str | None = None,
    self_paste: bool = False,
    a11y_mode: bool = False,
    copy_at: datetime | None = None,
    answered_at: datetime | None = None,
    warned_at: datetime | None = None,
    cfg: AntiCheatConfig | None = None,
) -> ReconciliationAct:
    """Собрать акт сверки по двойнику, ответу и контексту сессии."""
    cfg = cfg or load_anticheat_config()
    ac = answer_class or classify_answer_ex(
        answer,
        key=twin.key,
        twin_key=twin.twin_key,
        distractors=distractors,
        normalize=normalize,
        twin_to=twin.changed_literal.to,
        known_text=twin.shown_text,
        err_twin_keys=twin.err_trace_keys,
    )
    c = twin.changed_literal
    p = twin.honest_collision_estimate if p_honest is None else p_honest
    per10k = p * cfg.evidence.per_10k
    ans = str(answer)

    # --- чек-лист альтернативных объяснений (spec §2.1, M7)
    def item(cid: str, status: str, note: str = "") -> ChecklistItem:
        return ChecklistItem(
            cid, cfg.text(f"checklist.{cid}"), status, cfg.text(f"checklist_status_ru.{status}"), note
        )

    if render_id is None or twin_render_id is None:
        stale = item("stale_clip", "unknown", cfg.text("checklist_notes.stale_unknown"))
    elif render_id != twin_render_id:
        stale = item("stale_clip", "excluded", cfg.text("checklist_notes.stale_mismatch"))
    else:
        stale = item("stale_clip", "ok", cfg.text("checklist_notes.stale_same"))
    glyphs_ok = twin.gates.get("g6_no_confusable_glyphs", True)
    checklist = (
        item("self_paste", "excluded" if self_paste else "ok",
             cfg.text("checklist_notes.self_paste_yes" if self_paste else "checklist_notes.self_paste_no")),
        item("misconception", "attention" if ac.kind == "misconception" else "ok",
             cfg.text("checklist_notes.misconception_yes", tag=ac.tag or "") if ac.kind == "misconception"
             else cfg.text("checklist_notes.misconception_no")),
        item("glyphs", "ok" if glyphs_ok else "attention",
             cfg.text("checklist_notes.glyphs_ok" if glyphs_ok else "checklist_notes.glyphs_attention",
                      **{"from": c.from_, "to": c.to})),
        stale,
        item("a11y_mode", "attention" if a11y_mode else "ok",
             cfg.text("checklist_notes.a11y_yes") if a11y_mode else cfg.text("checklist_notes.a11y_no")),
    )
    return ReconciliationAct(
        variant_code=twin.variant_code,
        family_id=twin.family_id,
        mutation_class=twin.mutation_class,
        mutation_subclass=twin.mutation_subclass,
        render_id=render_id,
        title_ru=cfg.text("act_title"),
        shown_text=twin.shown_text,
        clipboard_text=twin.clipboard_text,
        shown_span=_span(twin.shown_text, *twin.shown_span),
        clipboard_span=_span(twin.clipboard_text, *twin.clipboard_span),
        lines=_lines(twin.shown_text, twin.clipboard_text, c.offset, c.from_, c.to),
        changed_literal=c.to_dict(),
        key_shown=twin.key,
        key_clipboard=twin.twin_key,
        candidate_answer=ans,
        answer_class=ac,
        matched_ru=cfg.text(f"matched_ru.{ac.matched}"),
        phase=phase,
        phase_ru=cfg.text(f"phase_ru.{phase}"),
        p_honest=p,
        p_honest_text=cfg.text("probability_line", p=format_probability(p), n=format_count(per10k)),
        expected_per_10k=per10k,
        copy_at=copy_at,
        answered_at=answered_at,
        warned_at=warned_at,
        checklist=checklist,
        verdict_ru=_verdict(cfg, twin, ac, ans),
        candidate_message_ru=cfg.text("candidate_message", variant=twin.variant_code),
    )


def build_act_for(
    fam: Family, inst: Instance, twin: Twin, answer: Any, **kw: Any
) -> ReconciliationAct:
    """То же с нормализацией и дистракторами экземпляра."""
    return build_act(twin, answer=answer, distractors=inst.distractors, normalize=fam.normalize, **kw)
