"""Скоринг подборки: контрольные примеры A/B/C (концепция §4.2), компоненты, ограничения весов,
детерминизм сортировки и неуязвимость к накрутке заявленными навыками (DECISIONS п.1)."""

import random

import pytest
from scipy.stats import norm

from app.matching.scoring import (
    BriefSpec,
    CandidateFeatures,
    ModuleScore,
    ScoringConfig,
    ScoringContext,
    TaskResult,
    component_A,
    component_G,
    component_U,
    display_parts,
    rank_results,
    round1,
    score_candidate,
    score_from_components,
    score_pool,
    theta_lcb,
    validate_weights,
)
from app.matching.skills import get_catalog

CFG = ScoringConfig.load()
BALANCE = CFG.weights("balance")

BRIEF = BriefSpec(
    need_id="need-1",
    spec="backend_python",
    grade="middle",
    must=("python", "postgresql"),
    nice=("kafka",),
    salary_from=220_000,
    salary_to=280_000,
    salary_net=True,
    formats=frozenset({"hybrid"}),
    city="moscow",
)

# Контрольные примеры §4.2: (T, K, F, A, U) → Score и разложение.
CONTROL = {
    "A": ((0.91, 0.80, 0.72, 0.6, 1.0), 83.2, (36.4, 20.0, 10.8, 6.0, 10.0)),
    "B": ((0.95, 0.85, 0.0, 0.9, 1.0), 78.3, (38.0, 21.3, 0.0, 9.0, 10.0)),
    "C": ((0.60, 0.60, 0.95, 0.2, 0.8), 63.3, (24.0, 15.0, 14.3, 2.0, 8.0)),
}


@pytest.mark.parametrize("name", ["A", "B", "C"])
def test_control_examples_from_components(name):
    x, expected, parts = CONTROL[name]
    score, contrib = score_from_components(dict(zip("TKFAU", x, strict=True)), BALANCE)
    assert abs(score - expected) <= 0.1
    assert round1(score) == expected
    shown = display_parts(contrib, score)
    assert tuple(shown[k] for k in "TKFAU") == parts
    assert round(sum(shown.values()), 1) == expected
    assert abs(sum(contrib.values()) - score) < 1e-9


def test_control_order_strong_test_beats_strong_fsp():
    # Сильный ФСП (C) не перевешивает большой разрыв в тесте; B без ФСП выше C.
    scores = {
        k: score_from_components(dict(zip("TKFAU", v[0], strict=True)), BALANCE)[0]
        for k, v in CONTROL.items()
    }
    assert scores["A"] > scores["B"] > scores["C"]


def _stats(lcb: float, below: int, n: int = 100) -> dict:
    vals = [lcb - 0.5 - i * 0.001 for i in range(below)] + [lcb + 0.5 + i * 0.001 for i in range(n - below)]
    return {("backend_python", "middle"): tuple(sorted(vals))}


def _module_for_p(p: float) -> ModuleScore:
    """Модуль с P(θ_m ≥ cut(middle)=0) ровно p."""
    return ModuleScore(theta=float(norm.ppf(p)), se=1.0, tag="confirmed")


def candidate_a() -> CandidateFeatures:
    return CandidateFeatures(
        candidate_id="A",
        spec="backend_python",
        grade="middle",
        theta=1.0,
        se=0.3,
        test_session_id="sess-A",
        test_days_ago=20,
        modules={"python_core": ModuleScore(2.0, 0.0, "strong"), "sql": _module_for_p(0.92)},
        tasks=(TaskResult("task-A", "B", 0.6, 0, "Идемпотентность платежей"),),
        vss_by_discipline={"product": 90.0},
        fsp_summary="Призёр всероссийских · продуктовое · 2025 · топ-3",
        fsp_ref="fsp-res-1",
        declared_skills=frozenset({"kafka"}),
        salary_min=200_000,
        formats=frozenset({"hybrid"}),
        city="moscow",
    )


def candidate_b() -> CandidateFeatures:
    return CandidateFeatures(
        candidate_id="B",
        spec="backend_python",
        grade="middle",
        theta=1.0,
        se=0.3,
        test_session_id="sess-B",
        test_days_ago=40,
        modules={"python_core": ModuleScore(2.0, 0.0, "strong"), "sql": ModuleScore(2.0, 0.0, "strong")},
        tasks=(
            TaskResult("task-B1", "A", 1 / 9, 0, "Kafka: consumer groups", skills=("kafka",)),
            TaskResult("task-B2", "B", 0.9, 0, "Кэш для каталога"),
        ),
        salary_min=250_000,
        formats=frozenset({"hybrid", "remote"}),
        city="moscow",
    )


def test_control_a_end_to_end_from_features():
    ctx = ScoringContext.build(category_stats=_stats(0.7, 91))
    r = score_candidate(candidate_a(), BRIEF, ctx)
    assert r.x["T"] == pytest.approx(0.91)
    assert r.x["K"] == pytest.approx(0.80)
    assert r.x["F"] == pytest.approx(0.72)
    assert r.x["A"] == pytest.approx(0.6)
    assert r.x["U"] == pytest.approx(1.0)
    assert r.G == 1.0 and r.Cov == 1.0
    assert r.score_display == 83.2
    assert tuple(r.parts_display[k] for k in "TKFAU") == (36.4, 20.0, 10.8, 6.0, 10.0)


def test_control_b_end_to_end_from_features():
    ctx = ScoringContext.build(category_stats=_stats(0.7, 95))
    r = score_candidate(candidate_b(), BRIEF, ctx)
    assert r.x["T"] == pytest.approx(0.95)
    assert r.x["K"] == pytest.approx(0.85)
    assert r.x["F"] == 0.0
    assert r.x["A"] == pytest.approx(0.9)
    assert r.x["U"] == pytest.approx(1.0)
    assert r.score_display == 78.3


def test_t_uses_lower_bound_and_grows_uncertainty_without_activity():
    lcb_fresh, se_fresh = theta_lcb(0.8, 0.3, 0)
    lcb_old, se_old = theta_lcb(0.8, 0.3, 365)
    assert lcb_fresh == pytest.approx(0.5)
    # SE 0,30 → ≈0,57 за 12 мес без активности (spec-testing §3.11).
    assert se_old == pytest.approx((0.09 + 0.02 * 365 / 30.4375) ** 0.5)
    assert 0.55 < se_old < 0.59 and lcb_old < lcb_fresh


def test_t_normal_fallback_when_category_small():
    c = replace_features(candidate_a(), theta=0.9, se=0.3)
    ctx_small = ScoringContext.build(category_stats={("backend_python", "middle"): (0.1, 0.2)})
    r = score_candidate(c, BRIEF, ctx_small)
    fb = CFG.section("T")["fallback"]["middle"]
    assert r.t.method == "normal"
    assert r.x["T"] == pytest.approx(norm.cdf((0.6 - fb["mu"]) / fb["sigma"]))


def test_trust_scales_t():
    ctx = ScoringContext.build(category_stats=_stats(0.7, 91))
    r = score_candidate(replace_features(candidate_a(), trust=0.75), BRIEF, ctx)
    assert r.x["T"] == pytest.approx(0.75 * 0.91)
    assert "low_trust" in r.flags


def test_no_fsp_is_zero_not_penalty():
    ctx = ScoringContext.build(category_stats=_stats(0.7, 91))
    with_fsp = score_candidate(candidate_a(), BRIEF, ctx)
    without = score_candidate(
        replace_features(candidate_a(), vss_by_discipline={}, fsp_summary=None), BRIEF, ctx
    )
    assert without.x["F"] == 0.0
    # Отсутствие ФСП снимает ровно вклад F, остальные компоненты не меняются.
    assert with_fsp.score - without.score == pytest.approx(with_fsp.c["F"])
    for k in "TKAU":
        assert with_fsp.x[k] == pytest.approx(without.x[k])


def test_self_declared_never_counts_in_k():
    ctx = ScoringContext.build(category_stats=_stats(0.7, 91))
    c = replace_features(
        candidate_a(),
        modules={},
        vss_by_discipline={},
        declared_skills=frozenset({"python", "postgresql", "kafka"}),
    )
    r = score_candidate(c, BRIEF, ctx)
    assert r.x["K"] == 0.0
    assert all(ch.state == "declared" and ch.label == "заявлено" for ch in r.skills)


def test_cov_penalty_when_less_than_half_must_confirmed():
    brief = BriefSpec(
        "n", "backend_python", "middle", must=("python", "postgresql", "docker"), salary_to=280_000
    )
    c = replace_features(
        candidate_a(), modules={"python_core": ModuleScore(2.0, 0.0, "strong")}, vss_by_discipline={}
    )
    ctx = ScoringContext.build(category_stats=_stats(0.7, 91))
    r = score_candidate(c, brief, ctx)
    assert r.must_confirmed == 1 and r.must_total == 3
    assert r.Cov == 0.8
    two = replace_features(
        c, modules={"python_core": ModuleScore(2.0, 0.0, "strong"), "sql": ModuleScore(2.0, 0.0, "strong")}
    )
    assert score_candidate(two, brief, ctx).Cov == 1.0


def test_low_data_module_is_not_evidence():
    c = replace_features(
        candidate_a(), modules={"python_core": ModuleScore(2.0, 0.1, "low_data")}, vss_by_discipline={}
    )
    r = score_candidate(c, BRIEF, ScoringContext.build(category_stats=_stats(0.7, 91)))
    assert next(ch for ch in r.skills if ch.skill_id == "python").v == 0.0


def test_k_sources_task_and_fsp():
    cat = get_catalog()
    c = CandidateFeatures(
        "X",
        "backend_python",
        "middle",
        theta=0.5,
        se=0.3,
        tasks=(TaskResult("t1", "A", 0.8, 90, "SQL-задача", module="sql"),),
        vss_by_discipline={"algo": 100.0},
    )
    r = score_candidate(c, BRIEF, ScoringContext.build(category_stats=_stats(0.2, 50)))
    pg = next(ch for ch in r.skills if ch.skill_id == "postgresql")
    # 0,9·0,8·2^(−90/90) = 0,36 против ФСП 0,8·m(algo, sql)·1 — берём максимум.
    fsp_sql = 0.8 * CFG.fsp["module_relevance"]["algo"]["sql"]
    assert pg.v == pytest.approx(max(0.36, fsp_sql))
    py = next(ch for ch in r.skills if ch.skill_id == "python")
    assert py.source == "fsp" and py.v == pytest.approx(
        0.8 * CFG.fsp["module_relevance"]["algo"]["python_core"]
    )
    assert cat.modules_of("kafka") == ()


def test_a_freshness_half_life_window_and_tag_bonus():
    cat = get_catalog()
    t = TaskResult("t", "A", 0.8, 45, "x", skills=("python",))
    c = CandidateFeatures("X", "backend_python", "middle", theta=0.0, tasks=(t,))
    a = component_A(c, BRIEF, CFG, cat)
    assert a.value == pytest.approx(min(1.0, 0.8 * 1.25) * 0.5)
    assert a.tagged
    old = CandidateFeatures(
        "X", "backend_python", "middle", theta=0.0, tasks=(TaskResult("t", "B", 1.0, 181, "x"),)
    )
    assert component_A(old, BRIEF, CFG, cat).value == 0.0


def test_u_salary_fit_and_net_gross():
    c = CandidateFeatures(
        "X", "backend_python", "middle", theta=0.0, salary_min=308_000, formats=frozenset({"hybrid"})
    )
    u = component_U(c, BRIEF, CFG)
    assert u.salary_above_pct == pytest.approx(10.0)
    assert u.sal_fit == pytest.approx(0.6)
    assert u.value == pytest.approx(0.6 * 0.6 + 0.4)
    # 300 000 до НДФЛ = 261 000 на руки — в вилке 220–280 на руки.
    gross = CandidateFeatures(
        "X", "backend_python", "middle", theta=0.0, salary_min=300_000, salary_net=False
    )
    assert component_U(gross, BRIEF, CFG).sal_fit == 1.0


def test_u_format_and_city():
    remote_only = CandidateFeatures(
        "X", "backend_python", "middle", theta=0.0, formats=frozenset({"remote"}), city="moscow"
    )
    assert component_U(remote_only, BRIEF, CFG).fmt_fit == pytest.approx(
        CFG.section("U")["format_fit"]["hybrid"]["remote"]
    )
    other_city = CandidateFeatures(
        "X", "backend_python", "middle", theta=0.0, formats=frozenset({"hybrid"}), city="kazan"
    )
    u = component_U(other_city, BRIEF, CFG)
    assert u.fmt_fit == 0.0 and not u.city_ok
    moving = CandidateFeatures(
        "X",
        "backend_python",
        "middle",
        theta=0.0,
        formats=frozenset({"hybrid"}),
        city="kazan",
        relocation=True,
    )
    assert component_U(moving, BRIEF, CFG).fmt_fit == 1.0


@pytest.mark.parametrize(
    "spec,grade,kw,expected",
    [
        ("backend_python", "middle", {}, 1.0),
        ("backend_python", "senior", {}, 0.85),
        ("backend_python", "junior", {}, 0.80),
        ("backend_python", "middle", {"borderline": True}, 0.9),
        ("frontend_js", "middle", {}, 0.3),
        ("frontend_js", "middle", {"is_fullstack": True}, 1.0),
        ("fullstack", "middle", {}, 0.8),
        ("backend_python", "intern", {}, 0.64),
    ],
)
def test_g_adjacency(spec, grade, kw, expected):
    c = CandidateFeatures("X", spec, grade, theta=0.0, **kw)
    assert component_G(c, BRIEF, CFG) == pytest.approx(expected)


def test_weight_constraints():
    validate_weights(BALANCE, CFG.section("weight_constraints"))
    validate_weights(CFG.weights("stack"), CFG.section("weight_constraints"))
    with pytest.raises(ValueError):
        validate_weights(
            {"T": 0.25, "K": 0.45, "F": 0.1, "A": 0.1, "U": 0.1}, CFG.section("weight_constraints")
        )
    with pytest.raises(ValueError):
        validate_weights(
            {"T": 0.40, "K": 0.20, "F": 0.20, "A": 0.1, "U": 0.1}, CFG.section("weight_constraints")
        )
    with pytest.raises(ValueError):
        validate_weights(
            {"T": 0.40, "K": 0.25, "F": 0.15, "A": 0.1, "U": 0.2}, CFG.section("weight_constraints")
        )


def test_stack_preset_changes_weights_only():
    ctx = ScoringContext.build(preset="stack", category_stats=_stats(0.7, 91))
    r = score_candidate(candidate_a(), BRIEF, ctx)
    assert dict(r.weights) == {"T": 0.30, "K": 0.40, "F": 0.10, "A": 0.10, "U": 0.10}
    assert r.score == pytest.approx(100 * (0.3 * 0.91 + 0.4 * 0.8 + 0.1 * 0.72 + 0.1 * 0.6 + 0.1 * 1.0))


def test_config_version_is_stable():
    assert CFG.version == ScoringConfig.load().version
    assert CFG.version.startswith("1-")


# --------------------------------------------------------------------------- популяция


def replace_features(c: CandidateFeatures, **kw) -> CandidateFeatures:
    from dataclasses import replace

    return replace(c, **kw)


def make_pool(n: int, seed: int = 7) -> list[CandidateFeatures]:
    rng = random.Random(seed)
    pool = []
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
        vss = {"algo": rng.uniform(10, 95)} if rng.random() < 0.35 else {}
        pool.append(
            CandidateFeatures(
                candidate_id=f"c{i:03d}",
                spec="backend_python",
                grade=rng.choice(["middle", "middle", "senior"]),
                theta=theta,
                se=rng.uniform(0.25, 0.45),
                theta_age_days=rng.uniform(0, 300),
                test_days_ago=rng.uniform(1, 300),
                test_session_id=f"s{i}",
                modules=mods,
                tasks=tasks,
                vss_by_discipline=vss,
                salary_min=rng.randrange(150_000, 330_000, 1000),
                formats=frozenset(rng.sample(["office", "hybrid", "remote"], rng.randint(1, 3))),
                city=rng.choice(["moscow", "spb", "kazan"]),
            )
        )
    return pool


def test_fake_declared_skills_shift_rank_by_zero():
    """Накрутка: 50 слабым добавили 5 ложных навыков → Score тот же, сдвиг ранга 0 (DECISIONS п.1)."""
    pool = make_pool(120)
    before = score_pool(pool, BRIEF)
    weakest = {r.candidate_id for r in before[-50:]}
    fake = frozenset({"python", "postgresql", "kafka", "docker", "fastapi"})
    inflated = [replace_features(c, declared_skills=fake) if c.candidate_id in weakest else c for c in pool]
    after = score_pool(inflated, BRIEF)
    assert [r.candidate_id for r in before] == [r.candidate_id for r in after]
    s_before = {r.candidate_id: r.score for r in before}
    assert all(r.score == s_before[r.candidate_id] for r in after)


def test_sort_is_deterministic_and_input_order_independent():
    pool = make_pool(60, seed=3)
    a = [r.candidate_id for r in score_pool(pool, BRIEF)]
    shuffled = pool[:]
    random.Random(1).shuffle(shuffled)
    b = [r.candidate_id for r in score_pool(shuffled, BRIEF)]
    assert a == b


def test_tie_break_t_then_hash():
    base = CandidateFeatures("x", "backend_python", "middle", theta=0.5, se=0.3)
    twins = [replace_features(base, candidate_id=f"twin{i}") for i in range(5)]
    ctx = ScoringContext.build(twins)
    ranked = rank_results([score_candidate(c, BRIEF, ctx) for c in twins])
    assert len({round(r.score, 9) for r in ranked}) == 1
    assert [r.candidate_id for r in ranked] == [
        r.candidate_id for r in sorted(ranked, key=lambda r: r.tie_hash)
    ]
    # При равном Score выше тот, у кого выше T.
    better_t = replace_features(base, candidate_id="tt", theta=0.9)
    ctx2 = ScoringContext.build(category_stats={})
    r1, r2 = score_candidate(better_t, BRIEF, ctx2), score_candidate(base, BRIEF, ctx2)
    assert rank_results([r2, r1])[0].candidate_id == "tt"


def test_unconfirmed_survey_category_goes_below_confirmed():
    pool = make_pool(20)
    survey = CandidateFeatures(
        "survey-1", "backend_python", "middle", theta=None, vss_by_discipline={"algo": 99.0}
    )
    ranked = score_pool(pool + [survey], BRIEF)
    assert ranked[-1].candidate_id == "survey-1"
    assert ranked[-1].x["T"] == 0.0 and "unconfirmed" in ranked[-1].flags


def test_fsp_contribution_capped_by_weight():
    # Вклад ФСП ≤ 15 баллов при любом VSS (wF ≤ 0,15).
    c = replace_features(candidate_a(), vss_by_discipline={"product": 100.0, "algo": 100.0})
    r = score_candidate(c, BRIEF, ScoringContext.build(category_stats=_stats(0.7, 91)))
    assert r.c["F"] <= 15.0 + 1e-9


def test_k_fallback_to_blueprint_when_brief_has_no_skills():
    brief = BriefSpec("n", "backend_python", "middle", salary_to=280_000)
    c = CandidateFeatures(
        "X",
        "backend_python",
        "middle",
        theta=0.5,
        modules={
            m: ModuleScore(3.0, 0.0, "strong")
            for m in ("python_core", "sql", "http_api", "algo", "git_linux")
        },
    )
    r = score_candidate(c, brief, ScoringContext.build(category_stats={}))
    assert r.x["K"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- регрессии ревью


def test_salary_fit_is_banded_so_exact_minimum_is_not_recoverable():
    """Приватность минимума (§4.5): вклад U одинаков для минимумов внутри одной 5%-полосы превышения."""
    us = {
        m: component_U(
            CandidateFeatures("X", "backend_python", "middle", theta=0.0, salary_min=m), BRIEF, CFG
        )
        for m in (281_000, 285_000, 290_000, 294_000)
    }
    assert len({round(u.sal_fit, 9) for u in us.values()}) == 1
    assert us[281_000].sal_fit == pytest.approx(0.8)
    # Следующая полоса — следующий шаг SalFit; в вилке — 1; выше на 25% и больше — 0.
    nxt = component_U(
        CandidateFeatures("X", "backend_python", "middle", theta=0.0, salary_min=295_000), BRIEF, CFG
    )
    assert nxt.sal_fit == pytest.approx(0.6)
    far = component_U(
        CandidateFeatures("X", "backend_python", "middle", theta=0.0, salary_min=360_000), BRIEF, CFG
    )
    assert far.sal_fit == 0.0
    # Точный процент превышения остаётся внутри (для «exact»-видимости), но в балл не попадает.
    assert us[285_000].salary_above_pct == pytest.approx(100 * (285 / 280 - 1))


def test_config_version_covers_fsp_matrices_and_taxonomy():
    import copy

    fsp2 = copy.deepcopy(dict(CFG.fsp))
    fsp2["spec_relevance"]["algo"]["backend_python"] = 0.7
    other = ScoringConfig(CFG.raw, fsp2, CFG.taxonomy)
    assert other.version != CFG.version
    tax2 = copy.deepcopy(dict(CFG.taxonomy))
    tax2["grades"][2]["cut"] = 0.1
    assert ScoringConfig(CFG.raw, CFG.fsp, tax2).version != CFG.version
    assert ScoringConfig(CFG.raw, CFG.fsp, CFG.taxonomy).version == CFG.version


def test_bank_sort_without_brief():
    """Банк (Р9): 0,55·T + 0,25·F + 0,20·A, подтверждённые выше «по опросу», порядок детерминирован."""
    from app.matching.scoring import bank_score, rank_bank

    pool = make_pool(40, seed=9) + [
        CandidateFeatures("survey", "backend_python", "middle", theta=None, vss_by_discipline={"algo": 99.0})
    ]
    ctx = ScoringContext.build(pool)
    row = bank_score(pool[0], ctx)
    w = CFG.section("bank")["weights"]
    assert row.score == pytest.approx(100 * sum(w[k] * row.x[k] for k in ("T", "F", "A")))
    ranked = rank_bank(pool, ctx=ctx)
    assert ranked[-1].candidate_id == "survey"
    scores = [r.score for r in ranked[:-1]]
    assert scores == sorted(scores, reverse=True)
    shuffled = pool[::-1]
    assert [r.candidate_id for r in rank_bank(shuffled, ctx=ctx)] == [r.candidate_id for r in ranked]


def test_cond_is_neutral_when_conditions_met_and_penalizes_mismatch():
    """Cond = 1 при выполненных условиях (контрольные примеры не меняются); несоответствие снижает Score, не обнуляя."""
    ctx = ScoringContext.build(category_stats=_stats(0.7, 91))
    ok = score_candidate(candidate_a(), BRIEF, ctx)
    assert ok.cond == 1.0
    bad = score_candidate(replace_features(candidate_a(), formats=frozenset({"remote"}), salary_min=400_000), BRIEF, ctx)
    assert 0.0 < bad.cond < 0.5
    assert bad.score < ok.score
    assert abs(sum(bad.c.values()) - bad.score) < 1e-9
