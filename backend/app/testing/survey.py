"""Опрос и классификатор специализации (concept §2.4, spec-testing §2.2 п.2, §3.7).

    s_k = Σ_a W[a,k]·freq_a + 0.3·Σ_t V[t,k]·recency_t + 0.5·[выбор = k];   p = softmax(s / 0.5)
    Подсказка, если max_k p_k − p_выбор ≥ 0.2. Итоговая специализация = выбор кандидата.

W (16 действий × специализации) и V (технологии × специализации) — в config/testing.yaml (раздел survey).
Технологии дают только серые чипы «заявлено» и в оценку уровня НЕ входят (DECISIONS п.1).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.core.configs import load_config


@dataclass(frozen=True)
class SurveyConfig:
    temperature: float
    tech_weight: float
    choice_bonus: float
    hint_margin: float
    frequency: dict[str, float]
    frequency_labels: dict[str, str]
    recency: dict[str, float]
    recency_labels: dict[str, str]
    actions: dict[str, dict[str, Any]]  # code → {label, weights}
    technologies: dict[str, dict[str, Any]]  # code → {label, weights, modules}
    grade_min_months: dict[str, int]
    senior_warning_months: int
    version: int = 1

    @classmethod
    def from_config(cls, testing: dict[str, Any] | None = None) -> SurveyConfig:
        s = (testing if testing is not None else load_config("testing"))["survey"]
        gh = s.get("grade_hint", {})
        return cls(
            temperature=float(s["temperature"]),
            tech_weight=float(s["tech_weight"]),
            choice_bonus=float(s["choice_bonus"]),
            hint_margin=float(s["hint_margin"]),
            frequency={f["code"]: float(f["value"]) for f in s["frequency_levels"]},
            frequency_labels={f["code"]: f["label"] for f in s["frequency_levels"]},
            recency={r["code"]: float(r["value"]) for r in s["recency_levels"]},
            recency_labels={r["code"]: r["label"] for r in s["recency_levels"]},
            actions={a["code"]: {"label": a["label"], "weights": dict(a.get("weights", {}))} for a in s["actions"]},
            technologies={
                t["code"]: {
                    "label": t["label"],
                    "weights": dict(t.get("weights", {})),
                    "modules": list(t.get("modules", [])),
                }
                for t in s["technologies"]
            },
            grade_min_months={k: int(v) for k, v in gh.get("min_months", {}).items()},
            senior_warning_months=int(gh.get("senior_warning_months", 24)),
            version=int(s.get("version", 1)),
        )


@lru_cache(maxsize=1)
def load_survey_config() -> SurveyConfig:
    return SurveyConfig.from_config()


def _specializations(taxonomy: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    tax = taxonomy if taxonomy is not None else load_config("taxonomy")
    return list(tax["specializations"])


def survey_form(cfg: SurveyConfig | None = None, taxonomy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Структура опроса для экрана К1 (4 шага) — подписи на русском."""
    cfg = cfg or load_survey_config()
    tax = taxonomy if taxonomy is not None else load_config("taxonomy")
    return {
        "version": cfg.version,
        "industries": [{"code": i["code"], "label": i["name"]} for i in tax.get("industries", [])],
        "actions": [{"code": c, "label": a["label"]} for c, a in cfg.actions.items()],
        "frequency_levels": [{"code": c, "label": lab} for c, lab in cfg.frequency_labels.items()],
        "technologies": [{"code": c, "label": t["label"]} for c, t in cfg.technologies.items()],
        "recency_levels": [{"code": c, "label": lab} for c, lab in cfg.recency_labels.items()],
        "specializations": [
            {"code": s["code"], "label": s["name"], "status": s.get("status", "active")} for s in tax["specializations"]
        ],
    }


@dataclass(frozen=True)
class SurveyAnswers:
    chosen_spec: str
    actions: Mapping[str, str] = field(default_factory=dict)  # действие → never | sometimes | regularly
    technologies: Mapping[str, str | float] = field(default_factory=dict)  # технология → код давности или 0..1
    experience_months: int | None = None
    industries: tuple[str, ...] = ()


@dataclass(frozen=True)
class SurveyResult:
    scores: dict[str, float]
    probs: dict[str, float]
    top: str
    chosen: str
    final_spec: str
    hint: bool
    hint_text_ru: str | None
    chosen_status: str
    waitlist_text_ru: str | None
    declared_modules: tuple[str, ...]
    suggested_grade: str | None
    suggested_grade_text_ru: str | None
    derived_text_ru: str | None = None  # Fullstack: производная категория, теста «Fullstack» нет

    def to_dict(self) -> dict[str, Any]:
        return {
            "scores": {k: round(v, 4) for k, v in self.scores.items()},
            "probs": {k: round(v, 4) for k, v in self.probs.items()},
            "top": self.top,
            "chosen": self.chosen,
            "final_spec": self.final_spec,
            "hint": self.hint,
            "hint_text_ru": self.hint_text_ru,
            "chosen_status": self.chosen_status,
            "waitlist_text_ru": self.waitlist_text_ru,
            "declared_modules": list(self.declared_modules),
            "suggested_grade": self.suggested_grade,
            "suggested_grade_text_ru": self.suggested_grade_text_ru,
            "derived_text_ru": self.derived_text_ru,
        }


def _recency_value(v: str | float, cfg: SurveyConfig) -> float:
    if isinstance(v, (int, float)):
        return max(0.0, min(1.0, float(v)))
    if v not in cfg.recency:
        raise ValueError(f"Неизвестная давность использования: {v}")
    return cfg.recency[v]


def softmax(scores: Mapping[str, float], temperature: float) -> dict[str, float]:
    m = max(scores.values())
    ex = {k: math.exp((v - m) / temperature) for k, v in scores.items()}
    z = sum(ex.values())
    return {k: v / z for k, v in ex.items()}


def classify(
    answers: SurveyAnswers, cfg: SurveyConfig | None = None, taxonomy: dict[str, Any] | None = None
) -> SurveyResult:
    """Вероятности специализаций по опросу. Решает кандидат: подсказка ни к чему не обязывает."""
    cfg = cfg or load_survey_config()
    specs = _specializations(taxonomy)
    codes = [s["code"] for s in specs]
    names = {s["code"]: s["name"] for s in specs}
    if answers.chosen_spec not in names:
        raise ValueError(f"Неизвестная специализация: {answers.chosen_spec}")
    scores = {k: 0.0 for k in codes}
    for action, freq in answers.actions.items():
        if action not in cfg.actions:
            raise ValueError(f"Неизвестное действие: {action}")
        if freq not in cfg.frequency:
            raise ValueError(f"Неизвестная частота: {freq}")
        f = cfg.frequency[freq]
        for k, w in cfg.actions[action]["weights"].items():
            if k in scores:
                scores[k] += w * f
    declared: list[str] = []
    for tech, rec in answers.technologies.items():
        if tech not in cfg.technologies:
            raise ValueError(f"Неизвестная технология: {tech}")
        r = _recency_value(rec, cfg)
        for k, v in cfg.technologies[tech]["weights"].items():
            if k in scores:
                scores[k] += cfg.tech_weight * v * r
        for m in cfg.technologies[tech]["modules"]:
            if m not in declared:
                declared.append(m)
    scores[answers.chosen_spec] += cfg.choice_bonus
    probs = softmax(scores, cfg.temperature)
    # детерминированный тай-брейк: порядок справочника
    top = max(codes, key=lambda k: (round(probs[k], 12), -codes.index(k)))
    gap = probs[top] - probs[answers.chosen_spec]
    hint = top != answers.chosen_spec and gap >= cfg.hint_margin - 1e-12
    hint_text = (
        f"По ответам вы ближе к направлению «{names[top]}». Решаете вы — можно оставить свой выбор." if hint else None
    )
    chosen = next(s for s in specs if s["code"] == answers.chosen_spec)
    status = chosen.get("status", "active")
    waitlist = "Тест по этому направлению появится позже. Вы в листе ожидания" if status == "survey_only" else None
    derived_text = None
    if status == "derived":
        # Производная категория (concept §2.1): подтверждены обе квалификации, грейд — минимальный из двух
        parts = " и ".join(f"«{names.get(c, c)}»" for c in chosen.get("derived_from", ()))
        derived_text = (
            f"Отдельного теста нет: категория присваивается, когда подтверждены {parts}. "
            "Грейд — меньший из двух"
        )
    grade, grade_text = suggest_grade(answers.experience_months, cfg)
    return SurveyResult(
        scores=scores,
        probs=probs,
        top=top,
        chosen=answers.chosen_spec,
        final_spec=answers.chosen_spec,
        hint=hint,
        hint_text_ru=hint_text,
        chosen_status=status,
        waitlist_text_ru=waitlist,
        declared_modules=tuple(declared),
        suggested_grade=grade,
        suggested_grade_text_ru=grade_text,
        derived_text_ru=derived_text,
    )


def suggest_grade(experience_months: int | None, cfg: SurveyConfig | None = None) -> tuple[str | None, str | None]:
    """Подсказка по стажу при выборе грейда («вам ближе Junior») — ни к чему не обязывает."""
    cfg = cfg or load_survey_config()
    if experience_months is None or not cfg.grade_min_months:
        return None, None
    tax = load_config("taxonomy")
    names = {g["code"]: g["name"] for g in tax["grades"]}
    best = None
    for grade, months in sorted(cfg.grade_min_months.items(), key=lambda kv: kv[1]):
        if experience_months >= months:
            best = grade
    if best is None:
        return None, None
    return best, f"По стажу вам ближе {names.get(best, best)} — это лишь ориентир, не критерий: грейд определяют навыки, выбираете вы"


def senior_warning(claimed_grade: str, experience_months: int | None, cfg: SurveyConfig | None = None) -> str | None:
    """Мягкое предупреждение при выборе Senior и стаже < 24 мес."""
    cfg = cfg or load_survey_config()
    if claimed_grade != "senior" or experience_months is None or experience_months >= cfg.senior_warning_months:
        return None
    return (
        "Для Senior обычно нужен большой опыт, но грейд определяют навыки, а не годы. Попробовать можно: "
        "если не получится, система предложит подтвердить уровень ниже по тем же ответам"
    )
