"""Объяснения: Σ вкладов = Score, 3 «за» / 1 «против» по приоритету, ФСП никогда не «против»,
ссылки на доказательства, «почему выше соседа», «почему вы» для кандидата (концепция §4.4)."""

import random
from dataclasses import replace

import pytest

from app.matching.explain import explain, explain_all, neighbor_delta, relative_days, version_medians
from app.matching.scoring import (
    BriefSpec,
    CandidateFeatures,
    ModuleScore,
    ScoringContext,
    TaskResult,
    round1,
    score_candidate,
    score_pool,
)

BRIEF = BriefSpec(
    need_id="need-x",
    spec="backend_python",
    grade="middle",
    must=("python", "fastapi", "postgresql"),
    nice=("kafka", "docker"),
    salary_from=220_000,
    salary_to=280_000,
    formats=frozenset({"hybrid"}),
    city="moscow",
)


def make_pool(n: int, seed: int = 11) -> list[CandidateFeatures]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        theta = rng.gauss(0.5, 0.6)
        mods = {
            m: ModuleScore(
                theta + rng.gauss(0, 0.4),
                rng.uniform(0.25, 0.6),
                rng.choice(["confirmed", "strong", "low_data"]),
            )
            for m in ("python_core", "sql", "http_api", "algo", "git_linux")
        }
        tasks = tuple(
            TaskResult(
                f"t{i}-{j}",
                rng.choice("AB"),
                rng.random(),
                rng.uniform(0, 200),
                f"Задача {j}",
                module=rng.choice(list(mods)),
            )
            for j in range(rng.randint(0, 2))
        )
        out.append(
            CandidateFeatures(
                candidate_id=f"c{i:03d}",
                spec="backend_python",
                grade="middle",
                theta=theta,
                se=rng.uniform(0.25, 0.45),
                theta_age_days=rng.uniform(0, 300),
                test_days_ago=rng.uniform(1, 420),
                test_session_id=f"s{i}",
                borderline=rng.random() < 0.1,
                trust=1.0 if rng.random() < 0.9 else 0.75,
                modules=mods,
                tasks=tasks,
                vss_by_discipline={"algo": rng.uniform(10, 95)} if rng.random() < 0.35 else {},
                fsp_ref=f"fsp-{i}",
                declared_skills=frozenset(
                    rng.sample(["kafka", "docker", "fastapi", "redis"], rng.randint(0, 2))
                ),
                salary_min=rng.randrange(150_000, 330_000, 1000),
                salary_visibility=rng.choice(["signal", "exact"]),
                formats=frozenset(rng.sample(["office", "hybrid", "remote"], rng.randint(1, 3))),
                city=rng.choice(["moscow", "spb"]),
                relocation=rng.random() < 0.3,
            )
        )
    return out


POOL = make_pool(80)
RESULTS = score_pool(POOL, BRIEF)
MEDIANS = version_medians(RESULTS)
EXPL = explain_all(RESULTS, brief=BRIEF)


def test_sum_of_parts_equals_score():
    for r in RESULTS:
        e = EXPL[r.candidate_id]
        assert abs(sum(e.contributions.values()) - r.score) < 1e-9
        assert abs(sum(e.parts.values()) - round1(r.score)) <= 0.1 + 1e-9
        assert round(sum(e.parts.values()), 1) == round1(r.score)


def test_pros_are_top3_above_median_and_one_con_at_most():
    for r in RESULTS:
        e = EXPL[r.candidate_id]
        assert len(e.pros) <= 3
        comp_pros = [p for p in e.pros if p.component]
        for p in comp_pros:
            assert r.c[p.component] - MEDIANS[p.component] > 1.0
        deltas = sorted((r.c[k] - MEDIANS[k] for k in "TKFAU" if r.c[k] - MEDIANS[k] > 1.0), reverse=True)
        assert [round(p.delta, 9) for p in comp_pros] == [round(d, 9) for d in deltas[:3]]
        assert e.con is None or e.con.kind == "con"


def test_missing_fsp_is_never_a_con():
    for r in RESULTS:
        e = EXPL[r.candidate_id]
        if e.con is not None:
            assert e.con.component != "F"
            assert "ФСП" not in e.con.text


def test_every_reason_references_existing_evidence():
    by_id = {c.candidate_id: c for c in POOL}
    for r in RESULTS:
        c = by_id[r.candidate_id]
        task_ids = {t.task_id for t in c.tasks}
        for reason in EXPL[r.candidate_id].reasons:
            assert reason.evidence, reason
            for ev in reason.evidence:
                if ev.type == "test_session":
                    assert ev.id == c.test_session_id
                elif ev.type == "task":
                    assert ev.id in task_ids
                elif ev.type == "fsp_result":
                    assert c.has_fsp and ev.id == c.fsp_ref
                elif ev.type in ("brief_skill", "declared"):
                    assert ev.id in BRIEF.skills
                elif ev.type == "module":
                    assert ev.id in c.modules
                elif ev.type == "prefs":
                    assert ev.id == c.candidate_id
                else:
                    assert ev.type == "category"


def _base() -> CandidateFeatures:
    return CandidateFeatures(
        candidate_id="K-1",
        spec="backend_python",
        grade="middle",
        theta=1.2,
        se=0.3,
        test_days_ago=6,
        test_session_id="sess-1",
        modules={m: ModuleScore(2.5, 0.2, "strong") for m in ("python_core", "sql", "http_api")},
        tasks=(TaskResult("task-1", "A", 0.8, 6, "Идемпотентность платежей", skills=("fastapi",)),),
        vss_by_discipline={"product": 90.0},
        fsp_summary="Призёр всероссийских · продуктовое · 2025 · топ-3",
        fsp_ref="fsp-1",
        declared_skills=frozenset({"kafka"}),
        salary_min=240_000,
        formats=frozenset({"hybrid"}),
        city="moscow",
    )


def _one(c: CandidateFeatures, pool=POOL):
    ctx = ScoringContext.build(pool)
    r = score_candidate(c, BRIEF, ctx)
    return r, explain(r, MEDIANS, brief=BRIEF)


def test_demo_card_pros_and_kafka_only_declared():
    r, e = _one(_base())
    assert {p.code for p in e.pros} >= {"test", "fsp"}
    assert any(p.text.startswith("ФСП: Призёр всероссийских") for p in e.pros)
    # «против: Kafka только заявлена» — желательный навык без подтверждения (демо §11, 2:15).
    assert e.con is not None and e.con.code == "nice_unconfirmed"
    assert e.con.text == "Kafka: только заявлено, не подтверждено"


@pytest.mark.parametrize(
    "change,code",
    [
        ({"modules": {"python_core": ModuleScore(2.5, 0.2, "strong")}}, "must_unconfirmed"),
        ({"salary_min": 320_000}, "salary_above"),
        ({"formats": frozenset({"office"}), "city": "spb"}, "format_mismatch"),
        ({"borderline": True}, "borderline"),
        ({"trust": 0.75}, "low_trust"),
        ({"test_days_ago": 400}, "stale_test"),
    ],
)
def test_con_priority(change, code):
    r, e = _one(replace(_base(), **change))
    assert e.con is not None and e.con.code == code


def test_con_priority_order_must_beats_salary():
    c = replace(
        _base(),
        modules={"http_api": ModuleScore(2.5, 0.2, "strong")},
        tasks=(),
        vss_by_discipline={},
        salary_min=330_000,
        borderline=True,
    )
    r, e = _one(c)
    assert e.con.code == "must_unconfirmed"
    assert "Python" in e.con.text and "PostgreSQL" in e.con.text
    assert "Подтверждено 1 из 3 обязательных" in e.con.text


def test_salary_con_respects_visibility():
    r, e = _one(replace(_base(), salary_min=308_000, salary_visibility="signal"))
    assert e.con.text == "Ожидания выше вашей вилки"
    r, e = _one(replace(_base(), salary_min=308_000, salary_visibility="exact"))
    assert e.con.text == "Ожидания 308 000 ₽ на руки — выше верха вилки на 10%"


def test_without_fsp_no_con_about_fsp_even_if_far_below_median():
    rich = [replace(c, vss_by_discipline={"product": 95.0}, fsp_ref=f"f{i}") for i, c in enumerate(POOL)]
    res = score_pool(rich, BRIEF)
    med = version_medians(res)
    assert med["F"] > 5
    c = replace(_base(), vss_by_discipline={}, fsp_summary=None, declared_skills=frozenset())
    r = score_candidate(c, BRIEF, ScoringContext.build(rich))
    e = explain(r, med, brief=BRIEF)
    assert r.c["F"] - med["F"] < -3
    assert e.con is None or (e.con.component != "F" and "ФСП" not in e.con.text)


def test_removing_top_pro_evidence_lowers_score_by_its_contribution():
    """Честность объяснений: удаление доказательства довода №1 снижает Score ≥ его вклада (§7.2)."""
    c = replace(_base(), vss_by_discipline={}, fsp_summary=None)
    r, e = _one(c)
    fresh = next(p for p in e.pros if p.component == "A")
    r2, _ = _one(replace(c, tasks=()))
    assert r.score - r2.score >= r.c["A"] - 1e-9
    assert fresh.evidence[0].id == "task-1"


def test_neighbor_delta_text():
    a, b = RESULTS[1], RESULTS[2]
    d = neighbor_delta(a, b, 2, 3)
    assert d.text.startswith(f"#2 выше #3 на {round1(a.score - b.score):.1f}".replace(".", ","))
    if d.plus:
        assert d.plus[1] > 0
    if d.minus:
        assert d.minus[1] < 0 and "−" in d.text


def test_neighbor_delta_example_format():
    up = _one(_base())[0]
    low = _one(replace(_base(), candidate_id="K-2", tasks=(), theta=1.6))[0]
    d = neighbor_delta(up, low, 2, 3)
    assert d.plus[0] == "A" and d.minus[0] == "T"
    assert "свежесть +" in d.text and "тест −" in d.text


def test_why_you_is_candidate_facing():
    r, e = _one(_base())
    assert len(e.why_you) == len(e.pros)
    for line in e.why_you:
        assert line.startswith(("Ваш", "Вы"))
        assert "#" not in line and "медиан" not in line and "θ" not in line


def test_fallback_pro_when_nothing_stands_out():
    c = _base()
    r = score_candidate(c, BRIEF, ScoringContext.build([c]))
    e = explain(r, version_medians([r]), brief=BRIEF)
    assert len(e.pros) == 1 and e.pros[0].code == "category"
    assert e.pros[0].text == "Категория Backend (Python) · Middle подтверждена тестом"


@pytest.mark.parametrize(
    "days,text",
    [
        (0, "сегодня"),
        (1, "вчера"),
        (6, "6 дней назад"),
        (22, "22 дня назад"),
        (21, "21 день назад"),
        (95, "3 мес. назад"),
        (800, "2 года назад"),
    ],
)
def test_relative_days(days, text):
    assert relative_days(days) == text


def test_terms_pro_names_matching_format():
    r = score_candidate(_base(), BRIEF, ScoringContext.build(POOL))
    medians = {"T": 100.0, "K": 100.0, "F": 100.0, "A": 100.0, "U": 0.0}
    e = explain(r, medians, brief=BRIEF)
    assert [p.code for p in e.pros] == ["terms"]
    assert e.pros[0].text == "Ожидания в вашей вилке ✓; гибрид подходит"
    assert e.why_you == ("Ваши ожидания в вилке; гибрид подходит",)


# --------------------------------------------------------------------------- регрессии ревью


def _only_u_pro(c: CandidateFeatures):
    r = score_candidate(c, BRIEF, ScoringContext.build(POOL))
    medians = {"T": 100.0, "K": 100.0, "F": 100.0, "A": 100.0, "U": 0.0}
    return r, explain(r, medians, brief=BRIEF)


def test_terms_pro_never_claims_salary_in_range_when_above():
    # Ожидания на 5% выше вилки и только удалёнка при гибриде: U всё равно выше медианы, но «в вилке» — неправда.
    r, e = _only_u_pro(replace(_base(), salary_min=294_000, formats=frozenset({"remote"})))
    assert r.u.salary_above_pct is not None and not r.u.format_match
    assert [p.code for p in e.pros] == ["terms"]
    assert "в вилке" not in e.pros[0].text and "в вилке" not in e.why_you[0]
    assert "подходит" not in e.pros[0].text
    assert e.con is not None and e.con.code == "salary_above"


def test_terms_pro_format_only_and_no_prefs():
    r, e = _only_u_pro(replace(_base(), salary_min=None))
    assert e.pros[0].text == "Гибрид подходит"
    assert e.why_you[0] == "Ваш формат работы подходит: гибрид"
    r, e = _only_u_pro(replace(_base(), salary_min=None, formats=frozenset(), city=None))
    assert e.pros[0].text == "Ограничений по зарплате и формату не указано"
    assert e.why_you[0].startswith("Вы ")


@pytest.mark.parametrize("days", [360, 361, 364])
def test_relative_days_just_under_a_year(days):
    assert relative_days(days) == "11 мес. назад"


def test_neighbor_delta_confirmed_above_survey_only_with_lower_score():
    up = _one(replace(_base(), vss_by_discipline={}, tasks=(), salary_min=330_000))[0]
    low = _one(replace(_base(), candidate_id="S-1", theta=None, test_session_id=None))[0]
    # Подтверждённый может иметь балл ниже «по опросу» — порядок задаёт DECISIONS п.5, а не балл.
    low = replace(low, score=up.score + 5.0)
    d = neighbor_delta(up, low, 4, 5)
    assert "только опрос" in d.text and "при равном балле" not in d.text


def test_neighbor_delta_tiny_gap_is_not_called_equal():
    a = _one(_base())[0]
    b = replace(a, candidate_id="K-9", score=a.score - 0.03)
    d = neighbor_delta(a, b, 1, 2)
    assert d.text.startswith("#1 выше #2 на менее 0,1")


def test_unconfirmed_weak_test_con_does_not_mention_median():
    # «Категория по опросу»: все модули теста «~», must не подтверждены задачами — первым «против» станет
    # слабый T, и он не должен звучать как «тест ниже медианы» (теста нет).
    c = replace(
        _base(),
        theta=None,
        test_session_id=None,
        declared_skills=frozenset(),
        tasks=(),
        vss_by_discipline={},
    )
    brief = replace(BRIEF, must=(), nice=())
    r = score_candidate(c, brief, ScoringContext.build(POOL))
    e = explain(r, MEDIANS, brief=brief)
    # DECISIONS п. 29: заявленная категория без теста показывается со статусом — первым «против» идёт он
    assert e.con is not None and e.con.code == "category_unconfirmed" and e.con.component == "T"
    assert e.con.text == "Категория заявлена, тестом не подтверждена"
    assert "медиан" not in e.con.text
    assert e.con.evidence[0].type == "category"


def test_format_con_accusative_and_region_wording():
    r, e = _one(replace(_base(), formats=frozenset({"remote"})))
    assert e.con.code == "format_mismatch"
    assert e.con.text == "Формат не совпадает: рассматривает удалёнку"
    brief_kzn = replace(BRIEF, city="kazan")
    c = replace(_base(), city="innopolis", formats=frozenset({"hybrid"}))
    r = score_candidate(c, brief_kzn, ScoringContext.build(POOL))
    e = explain(r, MEDIANS, brief=brief_kzn)
    assert e.con.text == "Другой город того же региона, переезд не рассматривает"
    c2 = replace(c, city="spb")
    r2 = score_candidate(c2, brief_kzn, ScoringContext.build(POOL))
    assert explain(r2, MEDIANS, brief=brief_kzn).con.text == "Другой регион, переезд не рассматривает"


def test_exact_salary_con_names_basis_when_it_differs_from_brief():
    # 360 000 до НДФЛ = 313 200 на руки — выше верха 280 000 на руки на 12%.
    r, e = _one(replace(_base(), salary_min=360_000, salary_net=False, salary_visibility="exact"))
    assert e.con.text == "Ожидания 360 000 ₽ до вычета НДФЛ — выше верха вилки на 12%"


def test_stack_pro_wording_when_brief_has_only_nice_skills():
    brief = replace(BRIEF, must=(), nice=("python", "kafka"))
    r = score_candidate(_base(), brief, ScoringContext.build(POOL))
    e = explain(r, {"T": 100.0, "K": 0.0, "F": 100.0, "A": 100.0, "U": 100.0}, brief=brief)
    assert e.pros[0].text.startswith("Стек: подтверждено 1 из 2 навыков брифа")
