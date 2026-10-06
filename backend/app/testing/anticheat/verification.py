"""«Подтверждение за 5 минут»: живая сверка по трассе (killer-features §2.1 п. 2, spec-antiassist §2.8).

Скриншот проходит двойника бесплатно (пилот v2: 42 из 42), поэтому вторая половина защиты — живое
объяснение собственного варианта. Комплект строится по ЗАКОНЧЕННОЙ сессии кандидата:
- 3 вопроса по его же заданиям: предпочтительно трудные, решённые верно и быстро, и любые задания со «следом
  двойника»; в каждом изменён ровно один параметр (литерал данных), ожидаемый ответ вычислен ИСПОЛНЕНИЕМ
  (run_python / run_js / run_sql); если литерал подменить нельзя, берётся новый клон того же семейства
  («family_reseed», ключ — по контракту банка тоже исполнение);
- к каждому вопросу — рубрика 0–2 и короткая подсказка «что слушать» (шаблоны из config/anticheat.yaml);
- до 2 шаблонных вопросов по must-навыкам, которых тест не покрывает, с пометкой «не откалибровано».
Улик и флагов в комплекте нет никогда (правило видимости M5): в представлении для работодателя нет даже
причины выбора задания. LLM здесь не вызывается; шаблоны могут быть перефразированы шлюзом отдельно.
Итог «соответствует / выше / ниже» (VerificationResult) даёт бейдж и экспертную метку для метрики
«согласие теста с интервью».
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.testing.anticheat.config import AntiCheatConfig, load_anticheat_config
from app.testing.anticheat.dispatch import family_twin_result, variant_twin
from app.testing.anticheat.twins import Twin
from app.testing.bank.base import GRADES, Family, Instance, Rendered
from app.testing.bank.registry import all_families

KIT_VERSION = "verify-v1"


@dataclass(frozen=True)
class SessionItem:
    """Задание закончившейся сессии (без доступа к БД)."""

    family_id: str
    seed: int
    answer: Any
    correct: bool
    rt_ms: int | None = None
    median_rt_ms: float | None = None  # медиана времени по семейству
    difficulty: float | None = None  # b, если известна
    kind: str | None = None  # "twin_trace" — ответ классифицирован как след двойника

    @property
    def is_twin_trace(self) -> bool:
        return self.kind == "twin_trace"


@dataclass(frozen=True)
class RubricLevel:
    score: int
    text: str


@dataclass(frozen=True)
class VerificationQuestion:
    id: str
    kind: str  # trace | skill
    prompt_ru: str
    rubric: tuple[RubricLevel, ...]
    listen_for_ru: str
    calibrated: bool
    label_ru: str | None = None  # «не откалибровано» у шаблонных вопросов по навыкам
    # --- вопросы по трассе
    variant_code: str | None = None
    method: str | None = None  # literal_mutation | family_reseed | template
    verified_by: str = "none"  # execution | generator | none
    candidate_answer: str | None = None
    changed: dict[str, Any] | None = None
    variant_text: str | None = None
    expected_answer: str | None = None
    # --- служебное (не для работодателя)
    family_id: str | None = None
    seed: int | None = None
    selection_reason: str | None = None
    variant_rendered: Rendered | None = None  # машинно-читаемый вариант: для независимой перепроверки ответа

    def to_dict(self, audience: str = "methodist") -> dict[str, Any]:
        if audience not in ("employer", "methodist"):
            raise ValueError(f"комплект отдаётся работодателю или методисту, не «{audience}»")
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "prompt_ru": self.prompt_ru,
            "rubric": [{"score": r.score, "text": r.text} for r in self.rubric],
            "listen_for_ru": self.listen_for_ru,
            "calibrated": self.calibrated,
            "label_ru": self.label_ru,
            "variant_code": self.variant_code,
            "method": self.method,
            "verified_by": self.verified_by,
            "candidate_answer": self.candidate_answer,
            "changed": self.changed,
            "variant_text": self.variant_text,
            "expected_answer": self.expected_answer,
        }
        if audience != "employer":
            out.update(family_id=self.family_id, seed=self.seed, selection_reason=self.selection_reason)
        return out


@dataclass(frozen=True)
class VerificationKit:
    kit_id: str
    candidate_ref: str
    questions: tuple[VerificationQuestion, ...]
    shortfall: int  # сколько вопросов по трассе не удалось построить
    version: str = KIT_VERSION
    title_ru: str = ""
    subtitle_ru: str = ""

    @property
    def trace_questions(self) -> tuple[VerificationQuestion, ...]:
        return tuple(q for q in self.questions if q.kind == "trace")

    @property
    def skill_questions(self) -> tuple[VerificationQuestion, ...]:
        return tuple(q for q in self.questions if q.kind == "skill")

    def to_dict(self, audience: str = "employer") -> dict[str, Any]:
        """audience="employer": без причин выбора заданий, семейств и сидов — никаких улик (M5)."""
        return {
            "kit_id": self.kit_id,
            "version": self.version,
            "title_ru": self.title_ru,
            "subtitle_ru": self.subtitle_ru,
            "questions": [q.to_dict(audience) for q in self.questions],
            "shortfall": self.shortfall,
        }


@dataclass(frozen=True)
class VerificationResult:
    kit_id: str
    verdict: str  # matches | above | below
    verdict_ru: str  # соответствует | выше | ниже
    badge_ru: str
    ratio: float
    trace_scores: dict[str, int]
    skill_scores: dict[str, int]
    exceeded_expectations: bool

    def to_expert_label(self) -> dict[str, Any]:
        """Экспертная метка «тест ↔ интервью» для метрики согласия (Л4)."""
        return {
            "source": "verification_5min",
            "kit_id": self.kit_id,
            "label": self.verdict,
            "label_ru": self.verdict_ru,
            "ratio": round(self.ratio, 4),
            "n_trace": len(self.trace_scores),
            "skill_scores_calibrated": False,
        }


# ----------------------------------------------------------------------------- выбор заданий


def _grade_rank(grade: str) -> int:
    return GRADES.index(grade) if grade in GRADES else 0


def _rank(item: SessionItem, fam: Family, cfg: AntiCheatConfig) -> tuple[int, str] | None:
    """(ярус, причина): 0 след двойника · 1 трудное верно и быстро · 2 трудное верно · 3 верное; None — не годится."""
    v = cfg.verification
    if item.is_twin_trace:
        return 0, "twin_trace"
    if not item.correct:
        return None
    hard = (item.difficulty >= v.hard_b_min) if item.difficulty is not None else fam.target_grade in v.hard_grades
    fast = (
        item.rt_ms is not None
        and item.median_rt_ms is not None
        and item.rt_ms <= v.fast_ratio * item.median_rt_ms
    )
    if hard and fast:
        return 1, "hard_fast"
    return (2, "hard") if hard else (3, "correct")


def _hardness(item: SessionItem, fam: Family) -> float:
    return item.difficulty if item.difficulty is not None else float(_grade_rank(fam.target_grade))


def _answer_text(answer: Any) -> str:
    if isinstance(answer, str):
        return " ".join(answer.split())
    return str(answer)


# ----------------------------------------------------------------------------- построение комплекта


def _rubric(cfg: AntiCheatConfig, path: str) -> tuple[RubricLevel, ...]:
    return tuple(RubricLevel(int(r["score"]), " ".join(str(r["text"]).split())) for r in cfg.raw_text(path))


def _variant_question(
    qid: str,
    item: SessionItem,
    fam: Family,
    inst: Instance,
    candidate_ref: str,
    reason: str,
    cfg: AntiCheatConfig,
) -> VerificationQuestion | None:
    """Вопрос по трассе: один изменённый литерал (исполнение) либо новый клон семейства (генератор)."""
    t = cfg.texts["verification"]
    quote = not item.is_twin_trace  # ответ следа двойника не цитируем: комплект не раскрывает улик
    intro = cfg.text(
        "verification.trace_intro_answer" if quote else "verification.trace_intro_plain",
        variant=inst.variant_code,
        answer=_answer_text(item.answer),
    )
    # литерал, на котором стоит двойник, не используем: ответ из чата на него уже известен
    base_twin = family_twin_result(fam, inst, cfg=cfg).twin  # Family.twin() первым, затем общий мутатор
    avoid = [base_twin.changed_literal.ident] if base_twin else []
    found = variant_twin(fam, inst, avoid=avoid, salt=f"verify#{candidate_ref}", cfg=cfg)
    if found is not None:
        v: Twin = found
        c = v.changed_literal
        shown_lines, new_lines = v.shown_text.split("\n"), v.clipboard_text.split("\n")
        ask_key = "verification.trace_ask_sql" if c.role == "cell" else "verification.trace_ask_code"
        ask = cfg.text(
            ask_key,
            line=c.block_line,
            table=c.block.removeprefix("table:"),
            column=c.column,
            **{"from": c.from_, "to": c.to},
        )
        listen_t = t["listen_for"].get(fam.format, t["listen_for"]["generic"])
        return VerificationQuestion(
            id=qid,
            kind="trace",
            prompt_ru=f"{intro} {ask}",
            rubric=_rubric(cfg, "verification.rubric"),
            listen_for_ru=" ".join(str(listen_t).split()).format(**{"from": c.from_, "to": c.to}),
            calibrated=True,
            variant_code=inst.variant_code,
            method="literal_mutation",
            verified_by="execution",
            candidate_answer=_answer_text(item.answer) if quote else None,
            changed={
                **c.to_dict(),
                "line_before": shown_lines[c.line - 1],
                "line_after": new_lines[c.line - 1],
            },
            variant_text=v.clipboard_text,
            expected_answer=v.twin_key,
            family_id=fam.id,
            seed=inst.seed,
            selection_reason=reason,
            variant_rendered=v.twin_rendered,
        )
    # запасной путь: новый клон того же семейства (ключ — по контракту банка получен исполнением)
    new_seed = int(hashlib.sha256(f"{candidate_ref}|{fam.id}|{inst.seed}".encode()).hexdigest()[:8], 16) % 10**6
    try:
        inst2 = fam.generate(new_seed)
    except Exception:  # noqa: BLE001 — семейство не смогло сгенерировать клон: пропускаем задание
        return None
    if inst2.rendered.answer_mode != "text":
        return None
    return VerificationQuestion(
        id=qid,
        kind="trace",
        prompt_ru=f"{intro} {cfg.text('verification.trace_ask_reseed')}",
        rubric=_rubric(cfg, "verification.rubric"),
        listen_for_ru=" ".join(str(t["listen_for"]["generic"]).split()),
        calibrated=True,
        variant_code=inst2.variant_code,
        method="family_reseed",
        verified_by="generator",
        candidate_answer=_answer_text(item.answer) if quote else None,
        changed=None,
        variant_text="\n\n".join(x for x in (inst2.rendered.prompt, inst2.rendered.code or "") if x),
        expected_answer=str(inst2.key),
        family_id=fam.id,
        seed=new_seed,
        selection_reason=reason,
        variant_rendered=inst2.rendered,
    )


def covered_modules(items: Iterable[SessionItem], registry: Mapping[str, Family] | None = None) -> set[str]:
    """Коды модулей и специализаций, которые тест реально затронул (для проверки покрытия must-навыков)."""
    reg = registry if registry is not None else all_families()
    out: set[str] = set()
    for it in items:
        fam = reg.get(it.family_id)
        if fam is not None:
            out.add(fam.module.lower())
            out.update(s.lower() for s in fam.specs)
    return out


def skill_is_covered(skill: str, covered: Iterable[str], cfg: AntiCheatConfig) -> bool:
    s = skill.strip().lower()
    cov = {c.strip().lower() for c in covered}
    if s in cov:
        return True
    prefixes = cfg.verification.skill_aliases.get(s, (s,))
    return any(c.startswith(p) for c in cov for p in prefixes)


def _skill_question(qid: str, skill: str, cfg: AntiCheatConfig) -> VerificationQuestion:
    t = cfg.texts["verification"]
    template = t["skill_templates"].get(skill.strip().lower())
    prompt = " ".join(str(template).split()) if template else cfg.text("verification.skill_question_generic", skill=skill)
    return VerificationQuestion(
        id=qid,
        kind="skill",
        prompt_ru=prompt,
        rubric=_rubric(cfg, "verification.skill_rubric"),
        listen_for_ru=cfg.text("verification.skill_listen_for"),
        calibrated=False,
        label_ru=cfg.text("verification.uncalibrated_label"),
        method="template",
        verified_by="none",
    )


def build_kit(
    items: Sequence[SessionItem],
    *,
    candidate_ref: str | int,
    must_skills: Sequence[str] = (),
    covered_skills: Iterable[str] | None = None,
    registry: Mapping[str, Family] | None = None,
    cfg: AntiCheatConfig | None = None,
) -> VerificationKit:
    """Комплект «Подтверждение за 5 минут» по закончившейся сессии. Детерминирован по (items, candidate_ref)."""
    cfg = cfg or load_anticheat_config()
    reg = registry if registry is not None else all_families()
    ref = str(candidate_ref)

    def salt(fam_id: str, seed: int) -> int:
        return int(hashlib.sha256(f"verify#{ref}|{fam_id}|{seed}".encode()).hexdigest()[:8], 16)

    ranked: list[tuple[tuple[int, float, float, int], SessionItem, Family, str]] = []
    for it in items:
        fam = reg.get(it.family_id)
        rk = _rank(it, fam, cfg) if fam is not None else None
        if fam is None or rk is None:
            continue
        tier, reason = rk
        # ярус → труднее → быстрее → детерминированная «соль» от (кандидат, семейство, сид): порядок входа не важен
        ranked.append(((tier, -_hardness(it, fam), float(it.rt_ms or 0), salt(fam.id, it.seed)), it, fam, reason))
    ranked.sort(key=lambda r: r[0])

    questions: list[VerificationQuestion] = []
    used: set[str] = set()
    for _key, it, fam, reason in ranked:
        if len(questions) >= cfg.verification.n_trace_questions:
            break
        if fam.id in used:
            continue
        try:
            inst = fam.generate(it.seed)
        except Exception:  # noqa: BLE001
            continue
        q = _variant_question(f"q{len(questions) + 1}", it, fam, inst, ref, reason, cfg)
        if q is None:
            continue
        used.add(fam.id)
        questions.append(q)
    shortfall = cfg.verification.n_trace_questions - len(questions)

    covered = set(covered_skills) if covered_skills is not None else covered_modules(items, reg)
    skills = [s for s in must_skills if not skill_is_covered(s, covered, cfg)]
    for k, skill in enumerate(skills[: cfg.verification.max_skill_questions], start=1):
        questions.append(_skill_question(f"s{k}", skill, cfg))

    digest = hashlib.sha256(
        "|".join([ref, *(f"{q.id}:{q.family_id}:{q.seed}:{q.expected_answer}:{q.prompt_ru}" for q in questions)]).encode()
    ).hexdigest()[:12]
    return VerificationKit(
        kit_id=digest,
        candidate_ref=ref,
        questions=tuple(questions),
        shortfall=shortfall,
        title_ru=cfg.text("verification.title"),
        subtitle_ru=cfg.text("verification.subtitle"),
    )


# ----------------------------------------------------------------------------- итог


def evaluate_verification(
    kit: VerificationKit,
    scores: Mapping[str, int],
    *,
    exceeded_expectations: bool = False,
    cfg: AntiCheatConfig | None = None,
) -> VerificationResult:
    """Баллы рубрики 0–2 по вопросам → «соответствует / выше / ниже».

    В итог входят только откалиброванные вопросы по трассе; шаблонные вопросы по навыкам фиксируются, но вес 0.
    «Выше» — только при полном балле и явной отметке эксперта (exceeded_expectations): тест уже заявил, что
    кандидат справляется с этими заданиями, поэтому полный балл — «соответствует».
    """
    cfg = cfg or load_anticheat_config()
    v = cfg.verification
    by_id = {q.id: q for q in kit.questions}
    unknown = set(scores) - set(by_id)
    if unknown:
        raise ValueError(f"баллы для неизвестных вопросов: {sorted(unknown)}")
    for qid, sc in scores.items():
        if not 0 <= int(sc) <= v.rubric_max:
            raise ValueError(f"балл {sc} вопроса {qid} вне 0…{v.rubric_max}")
    trace = {qid: int(sc) for qid, sc in scores.items() if by_id[qid].kind == "trace"}
    skill = {qid: int(sc) for qid, sc in scores.items() if by_id[qid].kind == "skill"}
    if not trace:
        raise ValueError("нужен хотя бы один оценённый вопрос по трассе")
    ratio = sum(trace.values()) / (v.rubric_max * len(trace))
    if ratio < v.matches_min_ratio:
        verdict = "below"
    elif ratio >= v.above_min_ratio and exceeded_expectations:
        verdict = "above"
    else:
        verdict = "matches"
    return VerificationResult(
        kit_id=kit.kit_id,
        verdict=verdict,
        verdict_ru=cfg.text(f"verification.result_ru.{verdict}"),
        badge_ru=cfg.text(f"verification.result_badge_ru.{verdict}"),
        ratio=ratio,
        trace_scores=trace,
        skill_scores=skill,
        exceeded_expectations=exceeded_expectations,
    )
