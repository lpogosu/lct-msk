"""VSS — «подтверждённая сила» по дисциплине ФСП, вклад F в Score, доказательство навыка для K,
и тексты для карточки (обобщённая строка до раскрытия, выписка из протокола после).

Формулы — research/brief.md §5.4 и концепция §6.3, поправки DECISIONS.md п.2 и п.29 (Q&A организаторов):
    ФСП никогда не влияет на грейд кандидата — только на Score (F) и доказательство навыков (K).
   
    points(r) = L · C · Q · Ro · T · V            (L = 1 для всех уровней: мероприятия не ранжируем, п. 29;
                                                   C — класс результата: призёр 1,0 / участник 0,5)
    A_d       = Σ топ-5 p_(i) · 0,6^(i−1)         (отбор и финал одного соревнования → max;
                                                   участий без призового места учитывается не больше 2)
    VSS_d     = 100 · (1 − exp(−(A_d + 0,5·Rk) / 0,6));  разряд без результатов → VSS_d ≤ 50
    F         = max_d m(d, s) · VSS_d / 100        (нет истории → F = 0: отсутствие бонуса, не штраф)
    K: v_k   ⊇ 0,8 · m(d, k) · VSS_d / 100
Все числа — из config/fsp.yaml. Время передаётся явно (`now` из Clock): модуль не читает системные часы.
Судья и тренер — только бейджи, в VSS не входят. Скрытые и неподтверждённые результаты не учитываются.
Разряд учитывается, только если у него есть вес в vss.rank_weights (нормы ЕВСК по спортивному программированию:
МС, КМС, I–III, юношеские; МСМК в них нет). Роль в команде (team_function) — со слов участника из заявки:
в протоколах ФСП её нет, поэтому она не входит ни в VSS, ни в обобщённую строку, а показывается отдельно,
серым, с пометкой «по заявке участника».
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime

from pydantic import BaseModel

from app.core.clock import relative_ru
from app.fsp.config import FSPConfig, get_fsp_config
from app.fsp.models import (
    JudgeCategory,
    Level,
    Rank,
    RankCode,
    ResultRole,
    RoleKind,
    Source,
    Stage,
    StatusType,
)
from app.fsp.schema import FSPProfile, FSPState, NormalizedResult

NOT_LINKED_LINE = "Достижения ФСП не привязаны — оценка по тесту и задачам"
NO_RESULTS_LINE = "Участник ФСП, результатов пока нет"
AWAITING_PROTOCOL_LINE = "Ожидает протокол"

_LEVEL_GENITIVE = {
    Level.international: "международных",
    Level.all_russian: "всероссийских",
    Level.interregional: "межрегиональных",
    Level.regional: "региональных",
    Level.municipal: "муниципальных",
}
_LEVEL_NOMINATIVE = {
    Level.international: "международные",
    Level.all_russian: "всероссийские",
    Level.interregional: "межрегиональные",
    Level.regional: "региональные",
    Level.municipal: "муниципальные",
}
_STATUS_LABEL = {
    StatusType.championship: "чемпионат",
    StatusType.cup: "кубок",
    StatusType.first: "первенство",
    StatusType.other_sport: "официальное соревнование",
    StatusType.physical_culture_event: "физкультурное мероприятие",
    StatusType.student: "студенческое соревнование",
}
_STAGE_LABEL = {Stage.qualifier: "отборочный этап", Stage.final: "финал", Stage.single: None}
_ROLE_LABEL = {
    ResultRole.personal: "личный зачёт",
    ResultRole.captain: "капитан",
    ResultRole.participant: "участник команды",
    ResultRole.reserve: "запасной",
}
_FUNCTION_LABEL = {
    "backend": "backend",
    "frontend": "frontend",
    "fullstack": "fullstack",
    "mobile": "mobile",
    "ml": "ML",
    "devops": "DevOps",
    "design": "дизайнер",
    "pm": "менеджер продукта",
    "analyst": "аналитик",
}
# МСМК нет: в нормах ЕВСК по спортивному программированию такого звания нет, и оно не показывается.
_RANK_LABEL = {
    RankCode.MS: "МС",
    RankCode.KMS: "КМС",
    RankCode.I: "I разряд",
    RankCode.II: "II разряд",
    RankCode.III: "III разряд",
    RankCode.I_y: "I юношеский разряд",
    RankCode.II_y: "II юношеский разряд",
    RankCode.III_y: "III юношеский разряд",
}
DECLARED_ROLE_NOTE = "по заявке участника"
_JUDGE_CATEGORY_LABEL = {
    JudgeCategory.VK: "ВК",
    JudgeCategory.K1: "1К",
    JudgeCategory.K2: "2К",
    JudgeCategory.K3: "3К",
    JudgeCategory.YuSS: "ЮСС",
}
_SOURCE_LABEL = {
    Source.fsp_api: "Реестр ФСП",
    Source.fsp_mock: "Демо-реестр ФСП",
    Source.diploma: "Загруженный диплом",
}


# --------------------------------------------------------------------------- модели разложения


class ResultPoints(BaseModel):
    """Разложение points(r) = L · Q · Ro · T · V — у каждого числа есть объяснение."""

    result_id: str
    ekp: str
    group_key: str
    level_weight: float  # L (все уровни равноправны, config/fsp.yaml)
    result_class: str = "participant"  # podium | participant (DECISIONS п. 29)
    class_weight: float = 1.0  # C
    place_quality: float  # Q
    role_weight: float  # Ro
    freshness: float  # T
    source_weight: float  # V
    points: float
    counted: bool = False  # попал в топ-N после схлопывания «отбор + финал»


class VSSBreakdown(BaseModel):
    discipline: str
    vss: float
    a: float  # A_d
    rank_weight: float  # Rk с учётом срока действия
    rank_code: RankCode | None = None
    rank_only: bool = False  # VSS ограничен сверху: разряд есть, результатов в реестре нет
    results: list[ResultPoints] = []


class FitResult(BaseModel):
    """F или доказательство навыка: значение 0..1 и дисциплина, давшая максимум."""

    value: float = 0.0
    discipline: str | None = None
    module: str | None = None
    vss: float = 0.0
    relevance: float = 0.0


class FSPPublicItem(BaseModel):
    """Обобщённая запись для работодателя до раскрытия (концепция §4.5, spec-contact §4 fsp_public)."""

    discipline: str
    discipline_label: str
    level: Level
    year: int
    outcome: str
    place_band: str | None
    role: str | None
    text: str
    # Функция в команде из заявки («роль backend — по заявке участника»): в протоколе ФСП её нет, в text и VSS
    # не входит; интерфейс показывает её отдельно серым.
    declared_role: str | None = None


# --------------------------------------------------------------------------- множители points(r)


def _cfg(cfg: FSPConfig | None) -> FSPConfig:
    return cfg or get_fsp_config()


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def months_between(earlier: date, later: date | datetime, cfg: FSPConfig | None = None) -> float:
    """Δ в месяцах (средний григорианский месяц); будущее → 0."""
    days = (_as_date(later) - earlier).days
    return max(0, days) / _cfg(cfg).vss.days_per_month


def level_weight(level: Level, status: StatusType, stage: Stage, cfg: FSPConfig | None = None) -> float:
    """L по иерархии ЕВСК: первое подходящее правило из vss.level_rules."""
    for rule in _cfg(cfg).vss.level_rules:
        if rule.matches(level, status, stage):
            return rule.weight
    raise ValueError(f"Нет правила веса уровня для {level}/{status}/{stage}")


def place_quality(
    place: int | None,
    place_to: int | None,
    field_size: int,
    score: float | None = None,
    max_score: float | None = None,
    cfg: FSPConfig | None = None,
) -> float:
    """Q — место с поправкой на размер поля (бриф §5.4) и полом Q_min (DECISIONS п.2)."""
    pc = _cfg(cfg).vss.place
    if place is None:
        # Подтверждённое участие без места: тот же пол, что и для последнего места.
        return pc.q_min
    worst = place_to if place_to is not None and place_to >= place else place
    n = max(field_size, worst)
    # Диапазон мест (5–8): для q берём середину, бонус — по худшему месту (консервативно).
    mid = (place + worst) / 2
    q = 1.0 - (mid - 1.0) / max(n - 1, 1)
    q = min(1.0, max(0.0, q))
    if score is not None and max_score:
        q = (1.0 - pc.raw_score_weight) * q + pc.raw_score_weight * min(1.0, max(0.0, score / max_score))
    f_n = min(1.0, math.log(1 + n) / math.log(pc.field_size_ref))
    bonus = pc.podium_bonus.get(worst, 0.0)
    return float(max(pc.q_min, min(1.0, q**pc.exponent * f_n + bonus)))


def role_weight(role: ResultRole, cfg: FSPConfig | None = None) -> float:
    return float(_cfg(cfg).vss.role_weights[role])


def freshness(end_date: date, now: date | datetime, cfg: FSPConfig | None = None) -> float:
    """T = 2^(−Δмес / полураспад)."""
    c = _cfg(cfg)
    return float(2.0 ** (-months_between(end_date, now, c) / c.vss.freshness_half_life_months))


def source_weight(source: Source, cfg: FSPConfig | None = None) -> float:
    return _cfg(cfg).vss.source_weights[source]


def result_class(r: NormalizedResult, cfg: FSPConfig | None = None) -> str:
    """Класс результата: podium — призёр (худшее место ≤ порога), иначе participant (участник, участник отбора,
    финалист без призового места). Мероприятия между собой не ранжируем (DECISIONS п. 29)."""
    wp = r.worst_place
    return "podium" if wp is not None and wp <= _cfg(cfg).vss.result_class.podium_max_place else "participant"


def result_points(r: NormalizedResult, now: date | datetime, cfg: FSPConfig | None = None) -> ResultPoints:
    c = _cfg(cfg)
    lw = level_weight(r.level, r.status_type, r.stage, c)
    cls = result_class(r, c)
    cw = c.vss.result_class.class_weights[cls]
    q = place_quality(r.place, r.place_to, r.field_size, r.score, r.max_score, c)
    ro = role_weight(r.role, c)
    t = freshness(r.end_date, now, c)
    v = source_weight(r.source, c)
    return ResultPoints(
        result_id=r.result_id,
        ekp=r.ekp,
        group_key=r.group_key,
        level_weight=lw,
        result_class=cls,
        class_weight=cw,
        place_quality=q,
        role_weight=ro,
        freshness=t,
        source_weight=v,
        points=lw * cw * q * ro * t * v,
    )


def rank_recognized(rank: Rank, cfg: FSPConfig | None = None) -> bool:
    """Разряд из норм ЕВСК по спортивному программированию: у него есть вес (у MSMK нет, config/fsp.yaml)."""
    return rank.code in _cfg(cfg).vss.rank_weights


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 февраля → 28 февраля
        return d.replace(year=d.year + years, day=28)


def rank_valid_until(rank: Rank, cfg: FSPConfig | None = None) -> date | None:
    """Окончание срока действия: из реестра, а если реестр его не прислал — по Положению о ЕВСК (п. 76):
    КМС — 3 года, I–III и юношеские — 2 года от присвоения (vss.rank_validity_years); МС — бессрочно (None).
    Дата считается так же, как в демо-реестре: та же дата через N лет."""
    if rank.valid_until is not None:
        return rank.valid_until
    years = _cfg(cfg).vss.rank_validity_years.get(rank.code)
    return _add_years(rank.assigned_at, years) if years else None


def rank_is_active(rank: Rank, now: date | datetime, cfg: FSPConfig | None = None) -> bool:
    today = _as_date(now)
    until = rank_valid_until(rank, cfg)
    return rank.assigned_at <= today and (until is None or today <= until)


def rank_weight(rank: Rank, now: date | datetime, cfg: FSPConfig | None = None) -> float:
    """Rk: × 1, пока разряд действует; после окончания срока — × свежесть от даты окончания.
    Разряд вне норм ЕВСК по спортивному программированию (MSMK) веса не имеет: 0."""
    c = _cfg(cfg)
    today = _as_date(now)
    if rank.assigned_at > today or not rank_recognized(rank, c):
        return 0.0
    base = c.vss.rank_weights[rank.code]
    until = rank_valid_until(rank, c)
    if until is None or today <= until:
        return base
    return base * freshness(until, today, c)


# --------------------------------------------------------------------------- агрегация


def aggregate(points: Iterable[float], cfg: FSPConfig | None = None) -> float:
    """A_d = Σ топ-N p_(i) · decay^(i−1) — убывающая отдача, повторами не накрутить."""
    agg = _cfg(cfg).vss.aggregation
    top = sorted(points, reverse=True)[: agg.top_n]
    return sum(p * agg.decay**i for i, p in enumerate(top))


def vss_value(a: float, rk: float, *, rank_only: bool, cfg: FSPConfig | None = None) -> float:
    c = _cfg(cfg).vss
    value = 100.0 * (1.0 - math.exp(-(a + c.rank_factor * rk) / c.scale))
    if rank_only:
        value = min(value, c.rank_only_cap)
    return value


def discipline_vss(
    discipline: str,
    results: Iterable[NormalizedResult],
    ranks: Iterable[Rank],
    now: date | datetime,
    cfg: FSPConfig | None = None,
) -> VSSBreakdown:
    """VSS одной дисциплины. `results` должны быть уже отфильтрованы (подтверждены, не скрыты)."""
    c = _cfg(cfg)
    scored = [result_points(r, now, c) for r in results]
    # Отбор и финал одного соревнования — один результат: берём лучший (бриф §5.4).
    best_by_group: dict[str, ResultPoints] = {}
    for rp in sorted(scored, key=lambda x: (-x.points, x.result_id)):
        best_by_group.setdefault(rp.group_key, rp)
    ranked = sorted(best_by_group.values(), key=lambda x: (-x.points, x.result_id))
    # Потолок участий без призового места: онлайн-явками профиль не накрутить (DECISIONS п. 29).
    cap = c.vss.result_class.participation_max_counted
    seen_participation = 0
    pool: list[ResultPoints] = []
    for rp in ranked:
        if rp.result_class == "participant":
            seen_participation += 1
            if seen_participation > cap:
                continue
        pool.append(rp)
    for rp in pool[: c.vss.aggregation.top_n]:
        rp.counted = True
    a = aggregate((rp.points for rp in pool), c)

    rk, rk_code = 0.0, None
    for rank in ranks:
        w = rank_weight(rank, now, c)
        if w > rk:
            rk, rk_code = w, rank.code
    rank_only = not ranked
    return VSSBreakdown(
        discipline=discipline,
        vss=vss_value(a, rk, rank_only=rank_only, cfg=c),
        a=a,
        rank_weight=rk,
        rank_code=rk_code,
        rank_only=rank_only and rk > 0,
        results=sorted(scored, key=lambda x: (-x.points, x.result_id)),
    )


def compute_vss(profile: FSPProfile, now: date | datetime, cfg: FSPConfig | None = None) -> dict[str, VSSBreakdown]:
    """VSS по всем дисциплинам профиля. Нет ФСП ID или нет данных → пустой словарь."""
    c = _cfg(cfg)
    if profile.state == FSPState.not_linked:
        return {}
    by_disc: dict[str, list[NormalizedResult]] = defaultdict(list)
    for r in profile.counted_results:
        by_disc[r.discipline].append(r)  # type: ignore[index]  # counted_results гарантирует discipline

    ranks_by_disc: dict[str, list[Rank]] = defaultdict(list)
    unattributed: list[Rank] = []
    for rank in profile.ranks:
        if rank.discipline_code is None:
            unattributed.append(rank)
            continue
        disc = c.discipline_by_code(rank.discipline_code)
        if disc is not None:
            ranks_by_disc[disc.short].append(rank)
        # Разряд по дисциплине вне справочника (не спортивное программирование) в VSS не идёт.
    # Разряд без указания дисциплины относится ко всем дисциплинам, где есть результаты.
    for rank in unattributed:
        for d in by_disc:
            ranks_by_disc[d].append(rank)

    out: dict[str, VSSBreakdown] = {}
    for d in sorted(set(by_disc) | set(ranks_by_disc)):
        bd = discipline_vss(d, by_disc.get(d, []), ranks_by_disc.get(d, []), now, c)
        if bd.vss > 0:
            out[d] = bd
    return out


def vss_by_discipline(profile: FSPProfile, now: date | datetime, cfg: FSPConfig | None = None) -> dict[str, float]:
    """{дисциплина: VSS} — то, что кладётся в CandidateFeatures.vss_by_discipline."""
    return {d: bd.vss for d, bd in compute_vss(profile, now, cfg).items()}


# --------------------------------------------------------------------------- F и K


def fsp_fit(vss: Mapping[str, float], spec: str, cfg: FSPConfig | None = None) -> FitResult:
    """F = max_d m(d, s) · VSS_d / 100. Пустой vss (нет истории) → F = 0."""
    c = _cfg(cfg)
    best = FitResult()
    for d in sorted(vss):
        m = c.spec_relevance.get(d, {}).get(spec, 0.0)
        value = m * vss[d] / 100.0
        if value > best.value:
            best = FitResult(value=value, discipline=d, vss=vss[d], relevance=m)
    return best


def skill_evidence(vss: Mapping[str, float], modules: str | Iterable[str], cfg: FSPConfig | None = None) -> FitResult:
    """Член K: 0,8 · m(d, k) · VSS_d / 100, максимум по дисциплинам и по модулям навыка."""
    c = _cfg(cfg)
    mods = [modules] if isinstance(modules, str) else sorted(set(modules))
    best = FitResult()
    for d in sorted(vss):
        row = c.module_relevance.get(d, {})
        for k in mods:
            m = row.get(k, 0.0)
            value = c.skill_evidence_factor * m * vss[d] / 100.0
            if value > best.value:
                best = FitResult(value=value, discipline=d, module=k, vss=vss[d], relevance=m)
    return best


# --------------------------------------------------------------------------- тексты


def _fmt_num(x: float) -> str:
    if float(x).is_integer():
        return str(int(x))
    return f"{x:.1f}".replace(".", ",")


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    n100, n10 = n % 100, n % 10
    if n10 == 1 and n100 != 11:
        return forms[0]
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return forms[1]
    return forms[2]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def fetched_ago(fetched_at: datetime, now: date | datetime) -> str:
    """Когда получены данные реестра. С моментом времени — общий relative_ru платформы («2 часа назад»,
    DECISIONS п.9), с датой — по дням. Будущее (рассинхрон часов) показываем как «только что»."""
    if isinstance(now, datetime):
        then, cur = _aware(fetched_at), _aware(now)
        return relative_ru(min(then, cur), cur)
    return relative_age(fetched_at, now)


def relative_age(then: date | datetime, now: date | datetime) -> str:
    """«сегодня», «вчера», «5 дней назад», «3 мес. назад» — даты в UI только относительные (RK9)."""
    days = (_as_date(now) - _as_date(then)).days
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    if days < 60:
        return f"{days} {_plural(days, ('день', 'дня', 'дней'))} назад"
    months = round(days / 30.4375)
    if months < 12:
        return f"{months} мес. назад"
    years = max(1, days // 365)
    return f"{years} {_plural(years, ('год', 'года', 'лет'))} назад"


def discipline_label(short: str | None, cfg: FSPConfig | None = None) -> str:
    d = _cfg(cfg).discipline_by_short(short) if short else None
    return d.label if d else "другая дисциплина"


def outcome_label(r: NormalizedResult) -> str:
    """Исход до раскрытия. 1-е место тоже «Призёр», не «Победитель»: до раскрытия показываем только
    диапазон места (концепция §4.5), а «победитель + год + уровень» по публичным протоколам почти
    однозначно выдаёт команду и человека. Точное место — в выписке после раскрытия."""
    wp = r.worst_place
    podium = wp is not None and wp <= 3
    if r.stage == Stage.qualifier:
        return "Призёр отбора" if podium else "Участник отбора"
    if podium:
        return "Призёр"
    if r.stage == Stage.final:
        return "Финалист"
    return "Участник"


def place_band(r: NormalizedResult, cfg: FSPConfig | None = None) -> str | None:
    """Полоса места вместо точного места: «топ-3», «топ-10%»… (концепция §4.5)."""
    wp = r.worst_place
    if wp is None:
        return None
    for band in _cfg(cfg).place_bands:
        if band.max_place is not None and wp <= band.max_place:
            return band.label
        if band.max_share is not None and wp / max(r.field_size, 1) <= band.max_share:
            return band.label
    return None


def role_text(r: NormalizedResult) -> str | None:
    """Роль из протокола: личный зачёт, капитан, участник команды. Функцию в команде (backend, дизайн…) сюда
    не подмешиваем: в протоколах ФСП её нет, вклад в командный хакатон по ним честно не выделить."""
    if r.role == ResultRole.personal:
        return "личный зачёт"
    if r.role == ResultRole.captain:
        return "капитан команды"
    return "в команде"


def function_label(team_function: str | None) -> str | None:
    if not team_function:
        return None
    return _FUNCTION_LABEL.get(team_function, team_function)


def declared_role_text(r: NormalizedResult) -> str | None:
    """«роль backend — по заявке участника»: заявленная функция в команде, отдельно от подтверждённого."""
    label = function_label(r.team_function)
    return f"роль {label} — {DECLARED_ROLE_NOTE}" if label else None


def result_public_item(r: NormalizedResult, cfg: FSPConfig | None = None) -> FSPPublicItem:
    c = _cfg(cfg)
    outcome = outcome_label(r)
    band = place_band(r, c)
    role = role_text(r)
    label = discipline_label(r.discipline, c)
    parts = [f"{outcome} {_LEVEL_GENITIVE[r.level]}", label, str(r.end_date.year), band, role]
    return FSPPublicItem(
        discipline=r.discipline or "",
        discipline_label=label,
        level=r.level,
        year=r.end_date.year,
        outcome=outcome,
        place_band=band,
        role=role,
        text=" · ".join(p for p in parts if p),
        declared_role=declared_role_text(r),
    )


def _ordered_results(
    profile: FSPProfile, now: date | datetime, spec: str | None, cfg: FSPConfig
) -> list[NormalizedResult]:
    """Видимые подтверждённые результаты по убыванию points × m(d, spec)."""

    def key(r: NormalizedResult) -> tuple[float, int, str]:
        p = result_points(r, now, cfg).points
        if spec is not None:
            p *= cfg.spec_relevance.get(r.discipline or "", {}).get(spec, 0.0)
        return (-p, -r.end_date.toordinal(), r.result_id)

    return sorted(profile.counted_results, key=key)


def public_items(
    profile: FSPProfile, now: date | datetime, spec: str | None = None, cfg: FSPConfig | None = None
) -> list[FSPPublicItem]:
    c = _cfg(cfg)
    return [result_public_item(r, c) for r in _ordered_results(profile, now, spec, c)]


def public_summary(
    profile: FSPProfile, now: date | datetime, spec: str | None = None, cfg: FSPConfig | None = None
) -> str | None:
    """Обобщённая строка лучшего результата:
    «Призёр всероссийских · продуктовое программирование (хакатон) · 2025 · топ-3 · в команде»."""
    items = public_items(profile, now, spec, cfg)
    return items[0].text if items else None


def public_declared_role(
    profile: FSPProfile, now: date | datetime, spec: str | None = None, cfg: FSPConfig | None = None
) -> str | None:
    """Заявленная роль в команде для того же результата, что в обобщённой строке (показывается серым рядом)."""
    items = public_items(profile, now, spec, cfg)
    return items[0].declared_role if items else None


def rank_badge(rank: Rank, cfg: FSPConfig | None = None) -> str:
    label = _RANK_LABEL[rank.code]
    until = rank_valid_until(rank, cfg)
    return label if until is None else f"{label} до {until.year}"


def badges(profile: FSPProfile, now: date | datetime, cfg: FSPConfig | None = None) -> list[str]:
    """Бейджи: действующий разряд («КМС до 2027»), «судья-эксперт (1К)», «тренер». В Score не входят.
    Разряды вне норм ЕВСК по спортивному программированию (МСМК) не показываются."""
    c = _cfg(cfg)
    out: list[str] = []
    active = [r for r in profile.ranks if rank_recognized(r, c) and rank_is_active(r, now, c)]
    for rank in sorted(active, key=lambda r: (-c.vss.rank_weights[r.code], r.id)):
        out.append(rank_badge(rank, c))
    for role in sorted(profile.roles, key=lambda r: (r.kind != RoleKind.judge, r.id)):
        if role.kind == RoleKind.judge:
            text = role.position or "судья"
            if role.category is not None:
                text = f"{text} ({_JUDGE_CATEGORY_LABEL[role.category]})"
        else:
            text = role.position or "тренер"
        out.append(text)
    return list(dict.fromkeys(out))


def employer_line(
    profile: FSPProfile, now: date | datetime, spec: str | None = None, cfg: FSPConfig | None = None
) -> str:
    """Строка ФСП в слепой карточке (концепция §6.2, §6.4). Отсутствие ФСП — нейтрально, не «против»."""
    c = _cfg(cfg)
    if profile.state == FSPState.not_linked:
        return NOT_LINKED_LINE
    summary = public_summary(profile, now, spec, c)
    if summary:
        return summary
    breakdowns = compute_vss(profile, now, c)
    if breakdowns:
        # Только разряд, результатов в реестре нет: VSS ограничен, показываем пометку.
        d, bd = max(breakdowns.items(), key=lambda kv: (kv[1].vss, kv[0]))
        # Разряд, давший Rk: действующий, иначе истёкший (вклад уже уменьшен свежестью, бейдж «КМС до 2024»).
        candidates = [r for r in profile.ranks if r.code == bd.rank_code and rank_recognized(r, c)]
        rank = max(
            candidates,
            key=lambda r: (rank_is_active(r, now, c), rank_valid_until(r, c) or date.max, r.id),
            default=None,
        )
        if rank is not None:
            return f"{rank_badge(rank, c)} · {discipline_label(d, c)} · оценка по разряду"
    return NO_RESULTS_LINE


def candidate_status_line(profile: FSPProfile) -> str:
    """Что видит сам кандидат в К14 (концепция §6.4)."""
    if profile.state == FSPState.not_linked:
        return "ФСП ID не привязан"
    if profile.state == FSPState.linked_no_results:
        return NO_RESULTS_LINE
    if profile.state == FSPState.awaiting_protocol:
        return AWAITING_PROTOCOL_LINE
    line = "Достижения подтверждены реестром ФСП"
    if profile.pending_count:
        line += f" · ожидает протокол: {profile.pending_count}"
    return line


class FSPFeatures(BaseModel):
    """Признаки ФСП для CandidateFeatures (spec-contact §4): то, что нужно подбору без повторного расчёта."""

    state: FSPState
    has_fsp: bool  # фильтр банка «есть ФСП»: есть подтверждённая сила (видимый результат или разряд)
    vss_by_discipline: dict[str, float]
    fsp_public: list[FSPPublicItem]
    badges: list[str]
    employer_line: str
    # Заявленная роль в команде к результату из employer_line: серым, «по заявке участника» (в Score не входит)
    employer_declared_role: str | None = None
    pending_count: int
    # Пометка источника рядом со строкой ФСП: «Демо-реестр ФСП» (концепция §6.5 — в UI везде), None без ФСП ID.
    source_label: str | None = None


def build_features(profile: FSPProfile, now: date | datetime, cfg: FSPConfig | None = None) -> FSPFeatures:
    c = _cfg(cfg)
    vss = vss_by_discipline(profile, now, c)
    source_label = None
    if profile.state != FSPState.not_linked and profile.source is not None:
        source_label = _SOURCE_LABEL.get(profile.source)
    return FSPFeatures(
        state=profile.state,
        has_fsp=bool(vss),
        vss_by_discipline=vss,
        fsp_public=public_items(profile, now, None, c),
        badges=badges(profile, now, c),
        employer_line=employer_line(profile, now, None, c),
        employer_declared_role=public_declared_role(profile, now, None, c),
        pending_count=profile.pending_count,
        source_label=source_label,
    )


def _protocol_block(r: NormalizedResult, cfg: FSPConfig) -> list[str]:
    disc = cfg.discipline_by_short(r.discipline) if r.discipline else None
    level_parts = [_LEVEL_NOMINATIVE[r.level], _STATUS_LABEL[r.status_type], _STAGE_LABEL[r.stage]]
    if r.start_date == r.end_date:
        dates = r.end_date.strftime("%d.%m.%Y")
    else:
        dates = f"{r.start_date.strftime('%d.%m.%Y')}–{r.end_date.strftime('%d.%m.%Y')}"
    if r.place is None:
        place = "участие без места"
    elif r.place_to is not None and r.place_to > r.place:
        place = f"{r.place}–{r.place_to} из {r.field_size}"
    else:
        place = f"{r.place} из {r.field_size}"
    rows = [
        ("ЕКП", r.ekp),
        ("Соревнование", r.title),
        ("Вид спорта", f"{cfg.sport.name} ({cfg.sport.code})"),
        ("Дисциплина", f"{disc.name} ({r.discipline_code})" if disc else r.discipline_code),
        ("Уровень", " · ".join(p for p in level_parts if p)),
        ("Даты", dates),
        ("Место", place),
    ]
    if r.team_name:
        team = f"«{r.team_name}»"
        if r.team_size:
            team += f", {r.team_size} чел."
        rows.append(("Команда", team))
    rows.append(("Роль", _ROLE_LABEL[r.role]))
    if r.team_function:
        # Функция в команде — из заявки, не из протокола: подписываем источник (см. docstring модуля)
        rows.append(("Функция в команде", f"{function_label(r.team_function)} ({DECLARED_ROLE_NOTE})"))
    if r.score is not None:
        score = _fmt_num(r.score)
        if r.max_score:
            score += f" из {_fmt_num(r.max_score)}"
        rows.append(("Балл", score))
    if r.protocol_url:
        rows.append(("Протокол", r.protocol_url))
    width = max(len(k) for k, _ in rows) + 2
    return [f"{(k + ':').ljust(width)}{v}" for k, v in rows]


def protocol_extract(profile: FSPProfile, now: date | datetime, cfg: FSPConfig | None = None) -> str:
    """«Выписка из протокола» после раскрытия контактов: моноширинный текст по каждому видимому результату.

    Даты соревнований — абсолютные: это содержимое протокола, по нему результат сверяют.
    Момент получения данных — относительный (RK9).
    """
    c = _cfg(cfg)
    results = sorted(profile.counted_results, key=lambda r: (-r.end_date.toordinal(), r.result_id))
    if not results:
        return ""
    source = _SOURCE_LABEL.get(profile.source, "Реестр ФСП") if profile.source else "Реестр ФСП"
    header = ["ВЫПИСКА ИЗ ПРОТОКОЛА", f"Источник: {source}"]
    if profile.fetched_at is not None:
        header[-1] += f", данные получены {fetched_ago(profile.fetched_at, now)}"
    blocks = ["\n".join(header)]
    blocks += ["\n".join(_protocol_block(r, c)) for r in results]
    return "\n\n".join(blocks)
