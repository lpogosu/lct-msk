"""VSS, F и тексты ФСП: контрольные примеры брифа §5.4, Q_min (DECISIONS п.2), отсутствие истории,
скрытые и неподтверждённые результаты, обобщение до раскрытия и выписка после."""

from __future__ import annotations

import itertools
import math
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.configs import load_config
from app.fsp.config import get_fsp_config
from app.fsp.models import (
    AgeGroup,
    CompetitionFormat,
    JudgeCategory,
    Level,
    Rank,
    RankCode,
    ResultRole,
    RoleBadge,
    RoleKind,
    Source,
    Stage,
    StatusType,
)
from app.fsp.schema import FSPProfile, FSPState, NormalizedResult
from app.fsp.vss import (
    NO_RESULTS_LINE,
    NOT_LINKED_LINE,
    aggregate,
    badges,
    build_features,
    candidate_status_line,
    compute_vss,
    employer_line,
    fetched_ago,
    freshness,
    fsp_fit,
    place_band,
    place_quality,
    protocol_extract,
    public_items,
    public_summary,
    rank_weight,
    relative_age,
    skill_evidence,
    vss_by_discipline,
)

NOW = date(2026, 10, 5)
NOW_DT = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
PRODUCT = "1010021811Я"
ALGO = "1010011811Я"
_ids = itertools.count(1)


def res(
    *,
    discipline: str = "product",
    level: Level = Level.all_russian,
    status: StatusType = StatusType.championship,
    stage: Stage = Stage.final,
    place: int | None = 1,
    place_to: int | None = None,
    n: int = 100,
    role: ResultRole = ResultRole.participant,
    end: date = NOW,
    score: float | None = None,
    max_score: float | None = None,
    group: str | None = None,
    confirmed: bool = True,
    hidden: bool = False,
    team_function: str | None = None,
    team_name: str | None = None,
    source: Source = Source.fsp_api,
) -> NormalizedResult:
    i = next(_ids)
    ekp = f"20259900000{i:05d}"
    code = {"product": PRODUCT, "algo": ALGO}.get(discipline, "1010051811Я")
    return NormalizedResult(
        result_id=f"r{i}",
        ekp=ekp,
        group_key=group or ekp,
        title=f"Соревнование {i}",
        discipline=discipline,
        discipline_code=code,
        level=level,
        status_type=status,
        stage=stage,
        age_group=AgeGroup.adults,
        format=CompetitionFormat.team if role != ResultRole.personal else CompetitionFormat.personal,
        start_date=end - timedelta(days=2),
        end_date=end,
        field_size=n,
        place=place,
        place_to=place_to,
        score=score,
        max_score=max_score,
        role=role,
        team_name=team_name,
        team_size=4 if team_name else None,
        team_function=team_function,
        protocol_url=f"http://fsp-mock/protocols/{ekp}" if confirmed else None,
        source=source,
        confirmed=confirmed,
        hidden=hidden,
    )


def rank(code: RankCode, *, disc: str | None = PRODUCT, assigned: date = date(2025, 1, 10),
         valid_until: date | None = None) -> Rank:
    return Rank(
        id=f"k{next(_ids)}", athlete_sub="s-1", code=code, discipline_code=disc, assigned_at=assigned,
        valid_until=valid_until, order_no="ДЕМО-2025-001", assigned_by="Минспорт",
    )


def profile(*results: NormalizedResult, ranks: tuple[Rank, ...] = (), roles: tuple[RoleBadge, ...] = (),
            state: FSPState = FSPState.linked) -> FSPProfile:
    return FSPProfile(
        sub="s-1", state=state, results=list(results), ranks=list(ranks), roles=list(roles),
        source=Source.fsp_mock, fetched_at=NOW_DT,
    )


def vss_of(p: FSPProfile, d: str = "product") -> float:
    return compute_vss(p, NOW)[d].vss


# ---------------------------------------------------------------- контрольные примеры брифа §5.4
# Пересчитаны по ответам организаторов (DECISIONS п. 29): L = 1 для всех уровней (мероприятия не ранжируем), класс
# результата C: призёр 1,0 / участник 0,5. Чемпионат России, 1-е место: L 0,9 → 1,0 (86,8 → 88,5).
# Финал КР 30/100 — участник: L 0,8 → 1,0, но C = 0,5 (71,6 → 63,4). Региональный 10/40 — участник: L 0,3 → 1,0,
# C = 0,5 (18,4 → 28,7): региональный старт больше не «весит меньше» только из-за ранга мероприятия.


@pytest.mark.parametrize(
    ("role", "expected"),
    [(ResultRole.participant, 88.5), (ResultRole.personal, 91.1)],
)
def test_control_champion_of_russia_with_ms(role: ResultRole, expected: float) -> None:
    p = profile(res(place=1, n=100, role=role), ranks=(rank(RankCode.MS),))
    assert vss_of(p) == pytest.approx(expected, abs=0.2)


@pytest.mark.parametrize(
    ("role", "expected"),
    [(ResultRole.participant, 63.4), (ResultRole.personal, 66.0)],
)
def test_control_cup_final_30th_of_100_with_kms(role: ResultRole, expected: float) -> None:
    r = res(status=StatusType.cup, stage=Stage.final, place=30, n=100, role=role)
    p = profile(r, ranks=(rank(RankCode.KMS, valid_until=date(2028, 1, 10)),))
    assert vss_of(p) == pytest.approx(expected, abs=0.2)


@pytest.mark.parametrize(
    ("role", "expected"),
    [(ResultRole.participant, 28.7), (ResultRole.personal, 32.8)],
)
def test_control_regional_10th_of_40_ten_months_ago(role: ResultRole, expected: float) -> None:
    ten_months_ago = NOW - timedelta(days=round(10 * 30.4375))
    r = res(level=Level.regional, stage=Stage.single, place=10, n=40, role=role, end=ten_months_ago)
    assert vss_of(profile(r)) == pytest.approx(expected, abs=0.2)


def test_control_formula_matches_brief_by_hand() -> None:
    # Финал КР, 30/100: q = 1 − 29/99, Q = q^1,5 (F(100) = 1, бонуса нет); L = 1; C = 0,5 (участник);
    # Ro = 0,85; КМС 0,7.
    q = (1 - 29 / 99) ** 1.5
    expected = 100 * (1 - math.exp(-(1.0 * 0.5 * q * 0.85 + 0.5 * 0.7) / 0.6))
    r = res(status=StatusType.cup, place=30, n=100)
    p = profile(r, ranks=(rank(RankCode.KMS, valid_until=date(2028, 1, 10)),))
    assert vss_of(p) == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------- Q_min: участие без призового места


def test_q_min_is_configured_at_0_1() -> None:
    assert get_fsp_config().vss.place.q_min == pytest.approx(0.1)


def test_last_place_gets_q_min_instead_of_zero() -> None:
    # Без пола q = 0 за последнее место → points = 0 (G10). С полом Q = 0,1.
    assert place_quality(40, None, 40) == pytest.approx(0.1)
    assert place_quality(None, None, 40) == pytest.approx(0.1)  # подтверждённое участие без места


def test_participant_without_prize_ranks_above_non_participant() -> None:
    r = res(level=Level.regional, stage=Stage.single, place=40, n=40, role=ResultRole.personal)
    f_participant = fsp_fit(vss_by_discipline(profile(r), NOW), "backend_python").value
    f_none = fsp_fit(vss_by_discipline(FSPProfile.not_linked(), NOW), "backend_python").value
    assert f_participant > 0
    assert f_none == 0
    # и это заметно меньше призёра того же уровня
    prize = res(level=Level.regional, stage=Stage.single, place=1, n=40, role=ResultRole.personal)
    assert fsp_fit(vss_by_discipline(profile(prize), NOW), "backend_python").value > f_participant


def test_podium_bonus_and_range_place() -> None:
    assert place_quality(1, None, 100) == pytest.approx(1.0)
    # 5–8 из 38: q по середине диапазона, бонуса нет (худшее место 8)
    q = 1 - (6.5 - 1) / 37
    f_n = math.log(39) / math.log(51)
    assert place_quality(5, 8, 38) == pytest.approx(max(0.1, q**1.5 * f_n))


def test_raw_score_blends_into_q() -> None:
    q = 1 - 9 / 39
    blended = 0.5 * q + 0.5 * 60 / 100
    f_n = math.log(41) / math.log(51)
    assert place_quality(10, None, 40, score=60, max_score=100) == pytest.approx(blended**1.5 * f_n)


# ---------------------------------------------------------------- отсутствие истории ФСП


def test_no_history_gives_zero_f_and_neutral_line() -> None:
    p = FSPProfile.not_linked()
    assert compute_vss(p, NOW) == {}
    for spec in ("backend_python", "frontend_js", "fullstack"):
        assert fsp_fit({}, spec).value == 0.0
    assert skill_evidence({}, "sql").value == 0.0
    assert employer_line(p, NOW) == NOT_LINKED_LINE
    assert candidate_status_line(p) == "ФСП ID не привязан"
    assert badges(p, NOW) == []
    assert protocol_extract(p, NOW) == ""


def test_linked_without_results_gives_zero_f() -> None:
    p = profile(state=FSPState.linked_no_results)
    assert vss_by_discipline(p, NOW) == {}
    assert fsp_fit(vss_by_discipline(p, NOW), "backend_python").value == 0.0
    assert employer_line(p, NOW) == NO_RESULTS_LINE


def test_judge_and_coach_are_badges_only() -> None:
    roles = (
        RoleBadge(id="j", athlete_sub="s-1", kind=RoleKind.judge, position="судья-эксперт",
                  category=JudgeCategory.K1, discipline_code=PRODUCT),
        RoleBadge(id="c", athlete_sub="s-1", kind=RoleKind.coach, position="тренер", discipline_code=ALGO),
    )
    p = profile(roles=roles, state=FSPState.linked_no_results)
    assert vss_by_discipline(p, NOW) == {}
    assert badges(p, NOW) == ["судья-эксперт (1К)", "тренер"]
    assert employer_line(p, NOW) == NO_RESULTS_LINE


# ---------------------------------------------------------------- скрытые и неподтверждённые результаты


def test_hidden_result_is_excluded_everywhere() -> None:
    best = res(place=1, n=100, team_name="Ламповый стек", hidden=True)
    other = res(level=Level.regional, stage=Stage.single, place=5, n=30, end=date(2025, 3, 1))
    with_hidden = profile(best, other)
    only_other = profile(other)
    assert vss_of(with_hidden) == pytest.approx(vss_of(only_other))
    assert len(public_items(with_hidden, NOW)) == 1
    assert "всероссийских" not in (public_summary(with_hidden, NOW) or "")
    extract = protocol_extract(with_hidden, NOW)
    assert best.ekp not in extract and "Ламповый стек" not in extract
    assert other.ekp in extract


def test_awaiting_protocol_result_is_not_counted() -> None:
    pending = res(place=1, n=100, confirmed=False)
    p = FSPProfile(sub="s-1", state=FSPState.awaiting_protocol, results=[pending], fetched_at=NOW_DT)
    assert vss_by_discipline(p, NOW) == {}
    assert candidate_status_line(p) == "Ожидает протокол"
    assert employer_line(p, NOW) == NO_RESULTS_LINE  # работодатель ожидающий результат не видит


# ---------------------------------------------------------------- агрегация и разряды


def test_qualifier_and_final_of_same_competition_count_once() -> None:
    final = res(status=StatusType.cup, place=8, n=80, group="G1")
    qual = res(status=StatusType.cup, stage=Stage.qualifier, place=300, n=900, group="G1")
    assert vss_of(profile(final, qual)) == pytest.approx(vss_of(profile(final)))
    bd = compute_vss(profile(final, qual), NOW)["product"]
    assert sum(1 for rp in bd.results if rp.counted) == 1


def test_top5_with_diminishing_returns() -> None:
    results = [res(place=1, n=100, role=ResultRole.personal) for _ in range(7)]
    bd = compute_vss(profile(*results), NOW)["product"]
    p = 1.0  # L·C·Q·Ro·T·V = 1·1·1·1·1·1 (все уровни равноправны, призёр)
    assert bd.a == pytest.approx(p * sum(0.6**i for i in range(5)))
    assert sum(1 for rp in bd.results if rp.counted) == 5
    assert aggregate([1.0, 0.5]) == pytest.approx(1.0 + 0.5 * 0.6)


def test_rank_only_is_capped_at_50_and_flagged() -> None:
    p = profile(ranks=(rank(RankCode.MS, disc=ALGO),))
    bd = compute_vss(p, NOW)["algo"]
    uncapped = 100 * (1 - math.exp(-(0.5 * 0.9) / 0.6))
    assert uncapped > 50
    assert bd.vss == pytest.approx(50.0)
    assert bd.rank_only
    assert employer_line(p, NOW) == "МС · алгоритмическое · оценка по разряду"
    kms = compute_vss(profile(ranks=(rank(RankCode.KMS, disc=ALGO, valid_until=date(2028, 1, 1)),)), NOW)
    assert kms["algo"].vss == pytest.approx(100 * (1 - math.exp(-0.35 / 0.6)))


def test_expired_rank_decays_with_freshness() -> None:
    expired = rank(RankCode.KMS, assigned=date(2021, 10, 5), valid_until=NOW - timedelta(days=round(24 * 30.4375)))
    assert rank_weight(expired, NOW) == pytest.approx(0.7 * 0.5, abs=1e-3)
    active = rank(RankCode.KMS, valid_until=date(2027, 1, 1))
    assert rank_weight(active, NOW) == pytest.approx(0.7)
    assert badges(profile(ranks=(expired, active)), NOW) == ["КМС до 2027"]


def test_rank_without_discipline_applies_where_results_exist() -> None:
    r = res(place=10, n=100)
    with_rank = profile(r, ranks=(rank(RankCode.KMS, disc=None, valid_until=date(2028, 1, 1)),))
    assert vss_of(with_rank) > vss_of(profile(r))
    assert set(compute_vss(with_rank, NOW)) == {"product"}


def test_rank_in_unknown_discipline_does_not_count() -> None:
    # Код ВРВС не из справочника (другой вид спорта) — не «разряд без дисциплины», в VSS не идёт.
    r = res(place=10, n=100)
    foreign = profile(r, ranks=(rank(RankCode.MS, disc="9999999999Я"),))
    assert vss_of(foreign) == pytest.approx(vss_of(profile(r)))
    assert compute_vss(profile(ranks=(rank(RankCode.MS, disc="9999999999Я"),)), NOW) == {}


def test_rank_only_line_with_expired_rank() -> None:
    # Истёкший разряд без результатов: вклад уменьшен свежестью, строка всё равно объясняет, откуда F > 0.
    expired = rank(RankCode.KMS, disc=ALGO, assigned=date(2021, 3, 1), valid_until=date(2024, 3, 1))
    p = profile(ranks=(expired,))
    f = build_features(p, NOW)
    assert f.has_fsp and 0 < f.vss_by_discipline["algo"] < 50
    assert f.employer_line == "КМС до 2024 · алгоритмическое · оценка по разряду"
    assert f.badges == []  # истёкший разряд бейджем не показываем


def test_freshness_half_life_24_months() -> None:
    assert freshness(NOW, NOW) == pytest.approx(1.0)
    assert freshness(NOW - timedelta(days=round(24 * 30.4375)), NOW) == pytest.approx(0.5, abs=1e-3)
    assert freshness(NOW + timedelta(days=30), NOW) == pytest.approx(1.0)  # будущее не «молодит»


def test_diploma_source_weighs_half() -> None:
    api = vss_of(profile(res(place=1, source=Source.fsp_api)))
    diploma = vss_of(profile(res(place=1, source=Source.diploma)))
    assert diploma < api


# ---------------------------------------------------------------- F и доказательство навыка


def test_fsp_fit_takes_max_over_disciplines() -> None:
    vss = {"product": 86.8, "algo": 50.0}
    f = fsp_fit(vss, "backend_python")
    assert f.value == pytest.approx(0.8 * 0.868)
    assert f.discipline == "product"
    f_front = fsp_fit(vss, "frontend_js")
    assert f_front.value == pytest.approx(max(0.8 * 0.868, 0.4 * 0.5))
    assert fsp_fit(vss, "unknown_spec").value == 0.0


def test_skill_evidence_term() -> None:
    vss = {"product": 86.8, "algo": 50.0}
    ev = skill_evidence(vss, "algo")
    assert ev.value == pytest.approx(0.8 * 1.0 * 0.5)
    assert (ev.discipline, ev.module) == ("algo", "algo")
    ev_many = skill_evidence(vss, ["sql", "http_api"])
    assert ev_many.value == pytest.approx(0.8 * 0.8 * 0.868)
    assert ev_many.module == "http_api"


def test_fsp_contribution_capped_by_vss_scale() -> None:
    # Даже идеальный профиль даёт F ≤ 1 → вклад ≤ 15 баллов при весе 0,15 (концепция §4.2).
    results = [res(place=1, n=200, role=ResultRole.personal, level=Level.international) for _ in range(5)]
    p = profile(*results, ranks=(rank(RankCode.MS),))
    f = fsp_fit(vss_by_discipline(p, NOW), "fullstack")
    assert 0.0 < f.value <= 1.0


# ---------------------------------------------------------------- тексты для карточки


def test_public_summary_matches_concept_example() -> None:
    # Роль в команде (backend) — из заявки, не из протокола: в обобщённую строку не входит (показывается отдельно).
    r = res(place=2, n=100, end=date(2025, 10, 13), team_function="backend")
    assert public_summary(profile(r), NOW) == (
        "Призёр всероссийских · продуктовое программирование (хакатон) · 2025 · топ-3 · в команде"
    )


def test_public_summary_variants() -> None:
    winner = res(place=1, role=ResultRole.personal, discipline="algo", end=date(2026, 5, 17))
    assert public_summary(profile(winner), NOW) == "Призёр всероссийских · алгоритмическое · 2026 · топ-3 · личный зачёт"
    qual = res(stage=Stage.qualifier, status=StatusType.cup, place=111, n=915, role=ResultRole.personal,
               discipline="algo", end=date(2026, 3, 14))
    assert public_summary(profile(qual), NOW) == "Участник отбора всероссийских · алгоритмическое · 2026 · топ-25% · личный зачёт"
    finalist = res(place=40, n=100, role=ResultRole.captain)
    assert public_summary(profile(finalist), NOW).startswith(
        "Финалист всероссийских · продуктовое программирование (хакатон)"
    )


def test_pre_disclosure_text_never_reveals_exact_place() -> None:
    # Концепция §4.5: до раскрытия — только диапазон места; «ЕКП + место» по протоколам выдаёт человека.
    first = res(place=1, n=103, team_name="Ламповый стек", team_function="design", end=date(2025, 10, 13))
    second = res(place=2, n=103, team_function="backend", end=date(2025, 10, 13))
    p1, p2 = profile(first), profile(second)
    assert public_summary(p1, NOW) == public_summary(p2, NOW)
    for item in public_items(p1, NOW) + public_items(p2, NOW):
        assert "Победитель" not in item.text
        assert "из 103" not in item.text and first.ekp not in item.text and "Ламповый" not in item.text
    assert "1 из 103" in protocol_extract(p1, NOW)  # точное место — только в выписке после раскрытия


def test_public_summary_prefers_relevant_discipline_for_spec() -> None:
    algo = res(discipline="algo", place=1, role=ResultRole.personal)
    infosec = res(discipline="infosec", place=1, role=ResultRole.personal)
    p = profile(algo, infosec)
    assert "алгоритмическое" in public_summary(p, NOW, spec="backend_python")
    assert "ИБ" in public_summary(p, NOW, spec="infosec")


def test_place_bands_generalize_exact_place() -> None:
    assert place_band(res(place=3, n=100)) == "топ-3"
    assert place_band(res(place=7, n=100)) == "топ-10"
    assert place_band(res(place=20, n=300)) == "топ-10%"
    assert place_band(res(place=60, n=300)) == "топ-25%"
    assert place_band(res(place=250, n=300)) is None
    assert place_band(res(place=None)) is None


def test_badges_rank_and_roles() -> None:
    roles = (RoleBadge(id="j", athlete_sub="s-1", kind=RoleKind.judge, position="судья-эксперт",
                       category=JudgeCategory.K1),)
    p = profile(res(), ranks=(rank(RankCode.KMS, valid_until=date(2027, 3, 1)), rank(RankCode.MS)), roles=roles)
    assert badges(p, NOW) == ["МС", "КМС до 2027", "судья-эксперт (1К)"]


def test_protocol_extract_after_disclosure() -> None:
    r = res(place=2, n=120, team_name="Ламповый стек", team_function="backend", score=87.5, max_score=100,
            end=date(2025, 10, 13))
    text = protocol_extract(profile(r), NOW)
    assert text.startswith("ВЫПИСКА ИЗ ПРОТОКОЛА\nИсточник: Демо-реестр ФСП, данные получены сегодня")
    assert "ЕКП:" in text and r.ekp in text
    assert "2 из 120" in text
    assert "«Ламповый стек», 4 чел." in text
    assert "Роль:" in text and "участник команды" in text
    assert "backend (по заявке участника)" in text  # функция в команде — со слов участника, не из протокола
    assert "спортивное программирование (1010001311Я)" in text  # код вида спорта ВРВС
    assert "87,5 из 100" in text
    assert "программирование продуктовое (1010021811Я)" in text
    assert "всероссийские · чемпионат · финал" in text
    assert "11.10.2025–13.10.2025" in text
    assert r.protocol_url in text


def test_relative_age_is_relative() -> None:
    assert relative_age(NOW, NOW) == "сегодня"
    assert relative_age(NOW - timedelta(days=1), NOW) == "вчера"
    assert relative_age(NOW - timedelta(days=3), NOW) == "3 дня назад"
    assert relative_age(NOW - timedelta(days=11), NOW) == "11 дней назад"
    assert relative_age(NOW - timedelta(days=21), NOW) == "21 день назад"
    assert relative_age(NOW - timedelta(days=200), NOW) == "7 мес. назад"
    assert relative_age(NOW - timedelta(days=350), NOW) == "11 мес. назад"
    assert relative_age(NOW - timedelta(days=360), NOW) == "1 год назад"  # 11,8 мес. → не «12 мес.» и не «0 лет»
    assert relative_age(NOW - timedelta(days=800), NOW) == "2 года назад"


def test_fetched_ago_uses_platform_relative_dates() -> None:
    # Момент получения данных — через общий relative_ru (DECISIONS п.9); будущее не показываем «через …».
    assert fetched_ago(NOW_DT - timedelta(hours=2), NOW_DT) == "2 часа назад"
    assert fetched_ago(NOW_DT + timedelta(minutes=5), NOW_DT) == "только что"
    assert fetched_ago(datetime(2026, 10, 5, 9, 0), NOW_DT) == "3 часа назад"  # наивное время = UTC
    assert fetched_ago(NOW_DT - timedelta(days=3), NOW) == "3 дня назад"
    p = profile(res(place=2, n=120))
    p.fetched_at = NOW_DT - timedelta(minutes=30)
    assert "данные получены 30 минут назад" in protocol_extract(p, NOW_DT)


def test_candidate_status_with_pending() -> None:
    p = profile(res(), res(confirmed=False))
    assert candidate_status_line(p) == "Достижения подтверждены реестром ФСП · ожидает протокол: 1"


def test_build_features_for_candidate_features() -> None:
    p = profile(res(place=2, n=100, team_function="backend", end=date(2025, 10, 13)), res(confirmed=False),
                ranks=(rank(RankCode.KMS, valid_until=date(2027, 3, 1)),))
    f = build_features(p, NOW)
    assert f.has_fsp and set(f.vss_by_discipline) == {"product"}
    assert f.fsp_public[0].text == f.employer_line
    assert "backend" not in f.employer_line
    assert f.employer_declared_role == "роль backend — по заявке участника"
    assert f.badges == ["КМС до 2027"] and f.pending_count == 1
    assert f.source_label == "Демо-реестр ФСП"
    empty = build_features(FSPProfile.not_linked(), NOW)
    assert not empty.has_fsp and empty.vss_by_discipline == {} and empty.employer_line == NOT_LINKED_LINE
    assert empty.source_label is None


# ---------------------------------------------------------------- конфиг


def test_config_covers_taxonomy() -> None:
    cfg = get_fsp_config()
    tax = load_config("taxonomy")
    specs = {s["code"] for s in tax["specializations"]}
    modules = set(tax["modules"])
    for d, row in cfg.spec_relevance.items():
        assert set(row) == specs, d
    for d, row in cfg.module_relevance.items():
        assert set(row) == modules, d
    assert {d.short for d in cfg.disciplines} == {"algo", "product", "bas", "robotics", "infosec"}
    # опорные значения матрицы из концепции §6.3
    m = cfg.spec_relevance
    assert (m["algo"]["backend_python"], m["algo"]["data_ml"], m["algo"]["frontend_js"]) == (0.8, 0.8, 0.4)
    assert (m["product"]["fullstack"], m["product"]["backend_python"], m["product"]["frontend_js"]) == (0.9, 0.8, 0.8)
    assert m["infosec"]["devops"] == 0.7
    assert m["robotics"]["data_ml"] == m["bas"]["data_ml"] == 0.6


# ---------------------------------------------------------------- ответы организаторов: равноправие и антинакрутка (п. 29)


@pytest.mark.parametrize("level", list(Level))
def test_all_levels_and_event_statuses_are_equal(level: Level) -> None:
    """Мероприятия не ранжируем: тот же результат на любом уровне и в любом статусе даёт тот же VSS."""
    base = vss_of(profile(res(level=Level.all_russian, status=StatusType.championship, place=2, n=60)))
    other = vss_of(profile(res(level=level, status=StatusType.cup, stage=Stage.single, place=2, n=60)))
    assert other == pytest.approx(base)
    assert {r.weight for r in get_fsp_config().vss.level_rules} == {1.0}


def test_participations_without_prize_are_capped() -> None:
    """Онлайн-участиями профиль не накрутить: учитывается не больше participation_max_counted участий."""
    cap = get_fsp_config().vss.result_class.participation_max_counted
    many = [res(place=20, n=60, stage=Stage.qualifier, role=ResultRole.personal) for _ in range(12)]
    bd = compute_vss(profile(*many), NOW)["product"]
    assert sum(1 for rp in bd.results if rp.counted) == cap
    capped = vss_of(profile(*many))
    exact = vss_of(profile(*many[:cap]))
    assert capped == pytest.approx(exact)


def test_podium_outweighs_any_number_of_participations() -> None:
    cap = get_fsp_config().vss.result_class.participation_max_counted
    participations = [res(place=15, n=60, role=ResultRole.personal) for _ in range(20)]
    podium = res(place=2, n=60, role=ResultRole.personal)
    assert vss_of(profile(podium)) > vss_of(profile(*participations))
    # призёр добавляется поверх потолка участий, а не вытесняется ими
    assert vss_of(profile(podium, *participations)) > vss_of(profile(*participations[:cap]))


def test_result_class_and_class_weight_are_exposed() -> None:
    bd = compute_vss(
        profile(res(place=1, n=60, role=ResultRole.personal), res(place=30, n=60, role=ResultRole.personal)), NOW
    )["product"]
    classes = {rp.result_class: rp.class_weight for rp in bd.results}
    assert classes == {"podium": 1.0, "participant": 0.5}


def test_recency_decays_by_half_life() -> None:
    """Свежесть разрешена организаторами: результат двухлетней давности весит вдвое меньше (полураспад 24 мес.)."""
    half = get_fsp_config().vss.freshness_half_life_months
    old = NOW - timedelta(days=round(half * 30.4375))
    fresh_pts = compute_vss(profile(res(place=1, n=60, role=ResultRole.personal)), NOW)["product"].results[0].points
    old_pts = compute_vss(profile(res(place=1, n=60, role=ResultRole.personal, end=old)), NOW)["product"].results[0].points
    assert old_pts == pytest.approx(fresh_pts / 2, rel=1e-3)


def test_spec_relevance_still_applies_but_not_event_rank() -> None:
    """Релевантность дисциплины специализации разрешена (ИБ-соревнования для ИБ-специалиста)."""
    vss = {"infosec": 80.0}
    assert fsp_fit(vss, "infosec").value > fsp_fit(vss, "frontend_js").value


# ---------------------------------------------------------------- точность модели ФСП (открытые документы ФСП)


def test_msmk_has_no_weight_and_is_not_shown() -> None:
    # Нормы ЕВСК по спортивному программированию (Приложение № 24 к приказу Минспорта № 450 от 22.04.2024):
    # МС, КМС, I–III, юношеские. МСМК в них нет: код в контракте остаётся, но веса и бейджа нет.
    cfg = get_fsp_config()
    assert RankCode.MSMK not in cfg.vss.rank_weights
    assert set(cfg.vss.rank_weights) == {
        RankCode.MS, RankCode.KMS, RankCode.I, RankCode.II, RankCode.III, RankCode.I_y, RankCode.II_y, RankCode.III_y,
    }
    msmk = rank(RankCode.MSMK)
    assert rank_weight(msmk, NOW) == 0.0
    only_msmk = profile(ranks=(msmk,))
    assert compute_vss(only_msmk, NOW) == {}
    assert badges(only_msmk, NOW) == []
    assert employer_line(only_msmk, NOW) == NO_RESULTS_LINE
    r = res(place=10, n=100)
    assert vss_of(profile(r, ranks=(msmk,))) == pytest.approx(vss_of(profile(r)))
    assert all("МСМК" not in b for b in badges(profile(r, ranks=(msmk, rank(RankCode.MS))), NOW))


def test_rank_validity_terms_from_config() -> None:
    # Положение о ЕВСК (приказ № 173 от 03.03.2025), п. 76: КМС — 3 года, I–III и юношеские — 2 года, МС — бессрочно.
    from app.fsp.vss import rank_is_active, rank_valid_until

    years = get_fsp_config().vss.rank_validity_years
    assert years == {RankCode.KMS: 3, RankCode.I: 2, RankCode.II: 2, RankCode.III: 2,
                     RankCode.I_y: 2, RankCode.II_y: 2, RankCode.III_y: 2}
    assigned = date(2024, 3, 14)
    kms, first, youth, ms = (rank(c, assigned=assigned) for c in (RankCode.KMS, RankCode.I, RankCode.II_y, RankCode.MS))
    # Реестр не прислал valid_until — срок досчитывается по конфигу, а не считается «пожизненным»
    assert rank_valid_until(kms) == date(2027, 3, 14)
    assert rank_valid_until(first) == date(2026, 3, 14)
    assert rank_valid_until(youth) == date(2026, 3, 14)
    assert rank_valid_until(ms) is None
    assert rank_is_active(kms, NOW) and not rank_is_active(first, NOW) and rank_is_active(ms, NOW)
    assert rank_weight(kms, NOW) == pytest.approx(0.7)
    assert rank_weight(first, NOW) < 0.5  # истёк 14.03.2026 → вес со свежестью от даты окончания
    assert badges(profile(ranks=(kms, first, ms)), NOW) == ["МС", "КМС до 2027"]
    # Явный срок из реестра главнее конфига
    explicit = rank(RankCode.KMS, assigned=assigned, valid_until=date(2026, 1, 1))
    assert rank_valid_until(explicit) == date(2026, 1, 1) and not rank_is_active(explicit, NOW)


def test_freshness_half_life_is_not_rank_validity() -> None:
    # Полураспад свежести — отдельное допущение; сроки разрядов разные (2 и 3 года) и заданы отдельно.
    cfg = get_fsp_config()
    assert cfg.vss.freshness_half_life_months == 24
    assert set(cfg.vss.rank_validity_years.values()) == {2, 3}


def test_sport_code_in_config() -> None:
    cfg = get_fsp_config()
    assert cfg.sport.code == "1010001311Я"
    assert cfg.sport.name == "спортивное программирование"
    # коды дисциплин не менялись
    assert {d.short: d.code for d in cfg.disciplines}["product"] == "1010021811Я"


def test_product_discipline_label_as_fsp_names_it() -> None:
    cfg = get_fsp_config()
    product = cfg.discipline_by_short("product")
    assert product is not None and product.code == "1010021811Я"
    assert product.label == "продуктовое программирование (хакатон)"
    assert product.name == "программирование продуктовое"  # наименование ВРВС в выписке не меняется


def test_team_function_does_not_affect_score_and_is_marked_declared() -> None:
    # В протоколах ФСП роли в команде нет: вклад в командный хакатон честно не выделить. Поэтому функция из
    # заявки не влияет на VSS (а значит, на F и K) и не попадает в обобщённую строку — только серой пометкой.
    base = dict(place=3, n=60, role=ResultRole.participant, end=date(2026, 4, 5), team_name="Тихий кэш")
    plain = res(**base)
    backend = res(**base, team_function="backend")
    design = res(**base, team_function="design")
    assert vss_of(profile(plain)) == pytest.approx(vss_of(profile(backend))) == pytest.approx(vss_of(profile(design)))
    for spec in ("backend_python", "frontend_js", "uiux_design"):
        assert fsp_fit(vss_by_discipline(profile(backend), NOW), spec).value == pytest.approx(
            fsp_fit(vss_by_discipline(profile(design), NOW), spec).value
        )
    item = public_items(profile(backend), NOW)[0]
    assert "backend" not in item.text and item.role == "в команде"
    assert item.declared_role == "роль backend — по заявке участника"
    assert public_items(profile(plain), NOW)[0].declared_role is None
