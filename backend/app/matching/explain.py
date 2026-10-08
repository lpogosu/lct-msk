"""Объяснения ранга без LLM (концепция §4.4, spec-contact §3.5).

1. Разложение: c_i = 100·G·Cov·w_i·x_i, Σ c_i = Score (± 0,1 после округления).
2. 3 «за» — компоненты с c_i − медиана_версии(c_i) > 1 (топ-3 по превышению);
   1 «против» — первый сработавший флаг по приоритету. Отсутствие ФСП в «против» не попадает никогда.
3. «Почему выше соседа» — Δ, главный плюс и главный минус.
4. Кандидату — те же «за», переписанные на «вы», без сравнения с другими.
Каждый пункт ссылается на доказательство (попытка теста, задача, запись ФСП, настройки, навык брифа).
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.matching.scoring import (
    COMPONENTS,
    BriefSpec,
    ScoreResult,
    ScoringConfig,
    display_parts,
    fmt1,
    round1,
)
from app.matching.skills import SkillCatalog, get_catalog

FORMAT_WORDS = {"office": "офис", "hybrid": "гибрид", "remote": "удалёнка"}
FORMAT_ACC = {"office": "офис", "hybrid": "гибрид", "remote": "удалёнку"}  # «рассматривает удалёнку»


@dataclass(frozen=True)
class Evidence:
    type: str  # test_session | category | task | fsp_result | prefs | brief_skill | declared | module
    id: str
    label: str | None = None


@dataclass(frozen=True)
class Reason:
    kind: str  # pro | con
    code: str
    component: str | None
    text: str
    evidence: tuple[Evidence, ...]
    delta: float | None = None  # c_i − медиана


@dataclass(frozen=True)
class Explanation:
    candidate_id: str
    score: float
    parts: Mapping[str, float]  # вклады с шагом 0,1, Σ = округлённый Score
    contributions: Mapping[str, float]
    pros: tuple[Reason, ...]
    con: Reason | None
    why_you: tuple[str, ...]

    @property
    def reasons(self) -> tuple[Reason, ...]:
        return self.pros + ((self.con,) if self.con else ())


@dataclass(frozen=True)
class NeighborDelta:
    upper_rank: int
    lower_rank: int
    upper_id: str
    lower_id: str
    delta: float
    plus: tuple[str, float] | None
    minus: tuple[str, float] | None
    text: str


# --------------------------------------------------------------------------- тексты


def plural(n: int, one: str, few: str, many: str) -> str:
    n100, n10 = abs(n) % 100, abs(n) % 10
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


def relative_days(days: float | None) -> str:
    """Даты в UI только относительные (DECISIONS п.9): «сегодня», «вчера», «6 дней назад», «3 мес. назад»."""
    if days is None:
        return ""
    d = int(days)
    if d <= 0:
        return "сегодня"
    if d == 1:
        return "вчера"
    if d < 31:
        return f"{d} {plural(d, 'день', 'дня', 'дней')} назад"
    if d < 365:
        # 360–364 дня: d // 30 = 12, но это ещё не год — иначе получалось «0 лет назад».
        return f"{min(11, d // 30)} мес. назад"
    years = d // 365
    return f"{years} {plural(years, 'год', 'года', 'лет')} назад"


def _rub(x: float) -> str:
    return f"{int(round(x)):,}".replace(",", " ") + " ₽"


def _join(names: Sequence[str]) -> str:
    return ", ".join(names)


def _band(pct: float | None, cfg: ScoringConfig) -> str | None:
    if pct is None:
        return None
    for b in cfg.section("T")["bands"]:
        if pct >= float(b["min"]) - 1e-9:
            return str(b["label"])
    return None


def _test_evidence(r: ScoreResult) -> Evidence:
    f = r.features
    if f.test_session_id:
        return Evidence("test_session", f.test_session_id, "попытка теста")
    return Evidence("category", f"{r.spec}:{r.grade}", "категория")


def _skill_evidence(r: ScoreResult, skill_ids: Iterable[str]) -> tuple[Evidence, ...]:
    out = []
    want = set(skill_ids)
    for ch in r.skills:
        if ch.skill_id not in want:
            continue
        if ch.source == "test" and ch.ref:
            out.append(Evidence("module", ch.ref, ch.label))
        elif ch.source == "task" and ch.ref:
            out.append(Evidence("task", ch.ref, ch.label))
        elif ch.source == "fsp":
            out.append(Evidence("fsp_result", r.features.fsp_ref or f"fsp:{ch.ref}", ch.label))
    return tuple(out)


def _pro(
    r: ScoreResult,
    comp: str,
    delta: float,
    cfg: ScoringConfig,
    cat: SkillCatalog,
    brief: BriefSpec | None = None,
) -> tuple[Reason, str]:
    """(«за» для работодателя, та же мысль для кандидата на «вы»)."""
    f = r.features
    category = cfg.category_name(r.spec, r.grade)
    if comp == "T":
        band = _band(r.t.pct, cfg)
        when = relative_days(f.test_days_ago)
        text = f"Тест: {band} категории {category}" if band else f"Тест: уровень {category} подтверждён"
        if when:
            text += f", тест {when}"
        you = f"Ваш тест подтвердил уровень {category}" + (
            " с запасом" if band and r.t.pct and r.t.pct >= 0.75 else ""
        )
        return Reason("pro", "test", comp, text, (_test_evidence(r),), delta), you
    if comp == "K":
        conf = [ch for ch in r.skills if ch.state == "confirmed"]
        names = [f"{ch.name} ({ch.label})" for ch in conf[:3]]
        if r.must_total:
            text = f"Стек: подтверждено {r.must_confirmed} из {r.must_total} обязательных"
        elif r.skills:  # в брифе только желательные навыки
            text = f"Стек: подтверждено {len(conf)} из {len(r.skills)} навыков брифа"
        else:
            text = "Стек: модули теста подтверждены"
        if names:
            text += " — " + _join(names)
        you = (
            "Вы подтвердили навыки из запроса: " + _join([ch.name for ch in conf[:3]])
            if conf
            else "Ваши модули теста подходят под запрос"
        )
        ev = _skill_evidence(r, [ch.skill_id for ch in conf[:3]]) or (_test_evidence(r),)
        return Reason("pro", "stack", comp, text, ev, delta), you
    if comp == "F":
        summary = f.fsp_summary or (
            f"достижения по дисциплине «{_disc(cfg, r.f.discipline)}»" if r.f.discipline else "достижения"
        )
        ref = f.fsp_ref or f"fsp:{r.f.discipline}"
        return (
            Reason(
                "pro",
                "fsp",
                comp,
                f"ФСП: {summary}",
                (Evidence("fsp_result", ref, "запись реестра ФСП"),),
                delta,
            ),
            f"Ваше достижение ФСП: {summary}",
        )
    if comp == "A":
        a = r.a
        title = a.title or "регулярная задача"
        score5 = f", {round((a.q or 0) * 5)}/5" if a.q is not None else ""
        text = f"Свежая задача «{title}», {relative_days(a.days_ago)}{score5}"
        you = f"Вы недавно решили задачу «{title}»{score5}"
        return Reason("pro", "fresh", comp, text, (Evidence("task", a.task_id or "", title),), delta), you
    # U: называем только то, что правда совпало («Ожидания в вашей вилке ✓; гибрид подходит», §4.2).
    # U может быть выше медианы и при ожиданиях чуть выше вилки — тогда про деньги молчим, а не пишем «в вилке».
    u = r.u
    sal_in = u.salary_known and u.salary_above_pct is None
    fmt_word = None
    common = sorted((brief.formats if brief else frozenset()) & r.features.formats)
    if u.format_match and u.city_ok and common:  # формат называем, только если он реально совпал
        fmt_word = FORMAT_WORDS.get(common[0], common[0])
    if sal_in:
        text = "Ожидания в вашей вилке ✓" + (f"; {fmt_word} подходит" if fmt_word else "")
        you = "Ваши ожидания в вилке" + (f"; {fmt_word} подходит" if fmt_word else "")
    elif fmt_word:
        text = f"{fmt_word.capitalize()} подходит"
        you = f"Ваш формат работы подходит: {fmt_word}"
    elif not u.salary_known and not r.features.formats:
        text = "Ограничений по зарплате и формату не указано"
        you = "Вы не указали ограничений по зарплате и формату"
    else:
        text = "Условия ближе к брифу, чем у большинства в подборке"
        you = "Ваши условия близки к запросу"
    return Reason(
        "pro", "terms", comp, text, (Evidence("prefs", r.candidate_id, "настройки кандидата"),), delta
    ), you


def _disc(cfg: ScoringConfig, d: str | None) -> str:
    for x in cfg.fsp.get("disciplines", ()):
        if x.get("short") == d:
            return str(x.get("label", d))
    return d or ""


def _con(
    r: ScoreResult,
    code: str,
    deltas: Mapping[str, float],
    cfg: ScoringConfig,
    cat: SkillCatalog,
    brief: BriefSpec | None = None,
) -> Reason | None:
    f = r.features
    if code == "category_unconfirmed":
        # Заявленная категория без теста показывается со статусом и ниже подтверждённых (DECISIONS п. 29).
        if r.confirmed:
            return None
        return Reason(
            "con", code, "T", "Категория заявлена, тестом не подтверждена",
            (Evidence("category", f"{r.spec}:{r.grade}", "категория по опросу"),),
        )
    if code == "must_unconfirmed":
        miss = [ch for ch in r.skills if ch.kind == "must" and ch.state != "confirmed"]
        if not miss:
            return None
        ev = tuple(Evidence("brief_skill", ch.skill_id, ch.name) for ch in miss) + tuple(
            Evidence("declared", ch.skill_id, "заявлено") for ch in miss if ch.declared
        )
        if len(miss) == 1:
            ch = miss[0]
            tail = " — только заявлен" if ch.declared else ""
            text = f"Обязательный навык {ch.name} не подтверждён{tail}"
        else:
            text = f"Подтверждено {r.must_confirmed} из {r.must_total} обязательных: не хватает {_join([c.name for c in miss])}"
        return Reason("con", code, "K", text, ev)
    if code == "salary_above":
        if r.u.salary_above_pct is None:
            return None
        # Конкретная причина множителя Cond и его величина: порядок подборки не меняется «молча».
        mult = f"; Score ×{fmt1(r.cond_parts['salary'], digits=2)}"
        cap = ""
        if brief is not None and brief.salary_to:
            cap_basis = "на руки" if brief.salary_net else "до вычета НДФЛ"
            cap = f" до {_rub(brief.salary_to)} {cap_basis}"
        if f.salary_visibility == "exact" and f.salary_min is not None:
            # Базис называем всегда («на руки» / «до вычета НДФЛ»): сумма без подписи двусмысленна (DECISIONS п. 29).
            basis = " на руки" if f.salary_net else " до вычета НДФЛ"
            text = (
                f"Ожидания {_rub(f.salary_min)}{basis} — выше верха вилки{cap} "
                f"на {int(round(r.u.salary_above_pct))}%{mult}"
            )
        else:
            text = f"Ожидания выше вашей вилки{cap}{mult}"
        return Reason("con", code, "U", text, (Evidence("prefs", r.candidate_id, "настройки кандидата"),))
    if code == "format_mismatch":
        if r.u.format_match:
            return None
        mult = f"; Score ×{fmt1(r.cond_parts['format'], digits=2)}"
        if not r.u.city_ok:
            # До раскрытия работодатель видит только регион (концепция §4.5) — город кандидата не называем.
            same_region = False
            if brief is not None and brief.city and f.city:
                a, b = cat.cities.get(brief.city), cat.cities.get(f.city)
                same_region = bool(a and b and a.region == b.region)
            where = "Другой город того же региона" if same_region else "Другой регион"
            text = f"{where}, переезд не рассматривает{mult}"
        else:
            fm = ", ".join(sorted(FORMAT_ACC.get(x, x) for x in f.formats)) or "другой формат"
            want = ""
            if brief is not None and brief.formats:
                want = ", в брифе — " + ", ".join(sorted(FORMAT_ACC.get(x, x) for x in brief.formats))
            text = f"Формат: только {fm}{want}{mult}"
        return Reason("con", code, "U", text, (Evidence("prefs", r.candidate_id, "настройки кандидата"),))
    if code == "borderline":
        if "borderline" not in r.flags:
            return None
        return Reason("con", code, "T", "Тест сдан на границе грейда", (_test_evidence(r),))
    if code == "low_trust":
        if "low_trust" not in r.flags:
            return None
        return Reason("con", code, "T", "Пониженное доверие к результату теста", (_test_evidence(r),))
    if code == "stale_test":
        if "stale_test" not in r.flags:
            return None
        return Reason("con", code, "T", "Тест сдан больше 12 месяцев назад", (_test_evidence(r),))
    if code == "weak_component":
        margin = float(cfg.section("explain")["con_margin"])
        # F исключён: отсутствие ФСП — отсутствие бонуса, не недостаток (концепция §6.4).
        weak = [(d, k) for k, d in deltas.items() if k != "F" and d < margin]
        if not weak:
            return None
        d, k = min(weak)
        names = cfg.section("explain")["component_titles"]
        if k == "T" and not r.confirmed:
            # У кандидата «по опросу» теста нет — «ниже медианы» было бы неправдой (DECISIONS п.5).
            text = "Тест не пройден: категория заявлена, тестом не подтверждена"
            ev = (Evidence("category", f"{r.spec}:{r.grade}", "категория по опросу"),)
        elif k == "A" and r.x["A"] <= 0:
            text = "Свежесть: нет задач за последние 180 дней"
            ev = (Evidence("prefs", r.candidate_id, "профиль кандидата"),)
        else:
            text = f"{names[k]}: ниже медианы подборки на {fmt1(-d)}"
            ev = {
                "T": (_test_evidence(r),),
                "K": tuple(Evidence("brief_skill", ch.skill_id, ch.name) for ch in r.skills)
                or (_test_evidence(r),),
                "A": (Evidence("task", r.a.task_id or "", r.a.title or "задача"),),
                "U": (Evidence("prefs", r.candidate_id, "настройки кандидата"),),
            }[k]
        return Reason("con", code, k, text, ev, d)
    if code == "nice_unconfirmed":
        miss = [ch for ch in r.skills if ch.kind == "nice" and ch.state != "confirmed"]
        if not miss:
            return None
        ch = next((c for c in miss if c.declared), miss[0])
        text = (
            f"{ch.name}: только заявлено, не подтверждено" if ch.declared else f"{ch.name}: не подтверждено"
        )
        ev = (Evidence("brief_skill", ch.skill_id, ch.name),) + (
            (Evidence("declared", ch.skill_id, "заявлено"),) if ch.declared else ()
        )
        return Reason("con", code, "K", text, ev)
    return None


# --------------------------------------------------------------------------- API


def version_medians(results: Sequence[ScoreResult]) -> dict[str, float]:
    """Медиана каждого вклада c_i по всей версии подборки."""
    if not results:
        return {k: 0.0 for k in COMPONENTS}
    return {k: float(statistics.median(r.c[k] for r in results)) for k in COMPONENTS}


def explain(
    r: ScoreResult,
    medians: Mapping[str, float],
    *,
    brief: BriefSpec | None = None,
    cfg: ScoringConfig | None = None,
    catalog: SkillCatalog | None = None,
) -> Explanation:
    cfg = cfg or ScoringConfig.load()
    cat = catalog or get_catalog()
    ec = cfg.section("explain")
    deltas = {k: r.c[k] - medians.get(k, 0.0) for k in COMPONENTS}
    margin = float(ec["pro_margin"])
    ranked = sorted(
        (k for k in COMPONENTS if deltas[k] > margin), key=lambda k: (-deltas[k], COMPONENTS.index(k))
    )
    pros: list[Reason] = []
    you: list[str] = []
    for k in ranked[: int(ec["max_pros"])]:
        reason, text = _pro(r, k, deltas[k], cfg, cat, brief)
        pros.append(reason)
        you.append(text)
    if not pros and r.confirmed:
        # «При нехватке добавляется „категория подтверждена тестом“» (spec-contact §3.5).
        category = cfg.category_name(r.spec, r.grade)
        pros.append(
            Reason("pro", "category", None, f"Категория {category} подтверждена тестом", (_test_evidence(r),))
        )
        you.append(f"Ваша категория {category} подтверждена тестом")
    con = None
    for code in ec["con_priority"]:
        con = _con(r, code, deltas, cfg, cat, brief)
        if con is not None:
            break
    return Explanation(
        candidate_id=r.candidate_id,
        score=r.score,
        parts=display_parts(r.c, r.score),
        contributions=dict(r.c),
        pros=tuple(pros),
        con=con,
        why_you=tuple(you),
    )


def explain_all(results: Sequence[ScoreResult], **kw: Any) -> dict[str, Explanation]:
    med = version_medians(results)
    out: dict[str, Explanation] = {}
    for r in results:
        out.setdefault(r.candidate_id, explain(r, med, **kw))
    return out


def neighbor_delta(
    upper: ScoreResult, lower: ScoreResult, upper_rank: int, lower_rank: int, cfg: ScoringConfig | None = None
) -> NeighborDelta:
    """«#2 выше #3 на 2,1: свежесть +4,0, тест −1,9» (концепция §4.4 п.3)."""
    cfg = cfg or ScoringConfig.load()
    names = cfg.section("explain")["component_names"]
    d = {k: upper.c[k] - lower.c[k] for k in COMPONENTS}
    delta = upper.score - lower.score
    pk = max(COMPONENTS, key=lambda k: (d[k], -COMPONENTS.index(k)))
    mk = min(COMPONENTS, key=lambda k: (d[k], COMPONENTS.index(k)))
    plus = (pk, d[pk]) if d[pk] > 0.05 else None
    minus = (mk, d[mk]) if d[mk] < -0.05 else None
    if upper.confirmed and not lower.confirmed and delta < 0.05:
        # Порядок задан не баллом: «категория по опросу» всегда ниже подтверждённых (DECISIONS п.5).
        text = (
            f"#{upper_rank} выше #{lower_rank}: категория подтверждена тестом, у #{lower_rank} — только опрос"
        )
    elif round(delta, 9) <= 0:
        # Ключи сортировки после Score (§4.2, DECISIONS п.1): T, заявленные навыки брифа, hash.
        if round(upper.x["T"], 9) > round(lower.x["T"], 9):
            why = "выше по тесту"
        elif upper.declared_overlap > lower.declared_overlap:
            why = "больше заявленных навыков из брифа"
        else:
            why = "порядок задан детерминированным ключом"
        text = f"#{upper_rank} выше #{lower_rank} при равном балле: {why}"
    else:
        bits = [f"{names[item[0]]} {fmt1(item[1], sign=True)}" for item in (plus, minus) if item is not None]
        shown = fmt1(delta) if round1(delta) > 0 else "менее 0,1"
        text = f"#{upper_rank} выше #{lower_rank} на {shown}" + (": " + ", ".join(bits) if bits else "")
    return NeighborDelta(
        upper_rank, lower_rank, upper.candidate_id, lower.candidate_id, delta, plus, minus, text
    )
