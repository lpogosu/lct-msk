"""Генератор популяции с латентной истиной: детерминизм, префиксное свойство, доли, слепые данные, V-CAT."""

from __future__ import annotations

from dataclasses import replace

import pytest

from eval import vcat
from eval.common import grade_band, grade_index, stream
from eval.population import (
    Population,
    PopulationConfig,
    generate_candidate,
    generate_chunk,
    generate_population,
    retest_of,
    vss_truth_correlation,
)


@pytest.fixture(scope="module")
def small_pop() -> Population:
    return generate_population(PopulationConfig(n=12, seed=777))


def test_candidate_is_deterministic_by_seed_and_index():
    cfg = PopulationConfig(n=5, seed=4242)
    a, b = generate_candidate(3, cfg), generate_candidate(3, cfg)
    assert a.to_public_dict() == b.to_public_dict()
    assert a.test is not None and a.test.instances == b.test.instances
    assert a.truth == b.truth
    other = generate_candidate(3, replace(cfg, seed=4243))
    assert other.to_public_dict() != a.to_public_dict()


def test_population_prefix_and_partition_independence(small_pop: Population):
    cfg6 = replace(small_pop.cfg, n=6)
    prefix = generate_population(cfg6)
    assert [c.to_public_dict() for c in prefix.candidates] == [c.to_public_dict() for c in small_pop.candidates[:6]]
    # Разбиение на куски (как в процессах) не меняет кандидатов и их пересдачу.
    one = generate_chunk(cfg6, list(range(6)), True)
    two = generate_chunk(cfg6, [0, 1, 2], True) + generate_chunk(cfg6, [3, 4, 5], True)
    assert [c.to_public_dict() for c in one] == [c.to_public_dict() for c in two]
    assert [c.retest.instances if c.retest else None for c in one] == [c.retest.instances if c.retest else None for c in two]


def test_retest_excludes_first_session_families(small_pop: Population):
    for c in small_pop.candidates:
        rt = retest_of(c, small_pop.cfg.seed)
        if c.test is None or rt is None:
            continue
        assert not set(c.test.families) & set(rt.families)
        assert not set(c.test.instances) & set(rt.instances)


def test_population_shares_match_config():
    pop = generate_population(PopulationConfig(n=400, seed=99, run_tests=False))
    s = pop.summary()
    assert 0.05 <= s["mismatched_share"] <= 0.16
    assert 0.27 <= s["fsp_share"] <= 0.43
    assert 0.42 <= s["tasks_share"] <= 0.58
    assert set(s["true_spec"]) == {"backend_python", "frontend_js", "fullstack"}
    for c in pop.candidates:
        assert c.fake_skills.isdisjoint(c.truth.skills)
        assert c.declared_skills == c.truth.skills | c.fake_skills
        assert c.salary_min > 0 and c.salary_min % pop.cfg.salary_round == 0
        lo, hi = grade_band(c.truth.grade)
        assert lo <= c.truth.theta < hi
        if c.truth.spec == "fullstack":
            assert not c.mismatched and c.claimed_spec in ("backend_python", "frontend_js")
        assert c.test is None and c.features() is None
    # Около 30% приписанных навыков к истинным
    ratio = s["fake_skills_mean"] / s["true_skills_mean"]
    assert 0.2 <= ratio <= 0.4


def test_vss_correlates_with_truth():
    pop = generate_population(PopulationConfig(n=500, seed=5, run_tests=False))
    vc = vss_truth_correlation(pop)
    assert vc["n"] > 100
    assert 0.3 <= vc["pearson"] <= 0.75


def test_public_dict_and_features_hide_truth(small_pop: Population):
    for c in small_pop.candidates:
        pub = c.to_public_dict()
        flat = repr(pub)
        assert "truth" not in pub and "fsp_strength" not in flat and "true_" not in flat
        assert "fake_skills" not in pub  # какие навыки приписаны — тоже скрытая истина
        f = c.features()
        if c.test is not None and c.test.final_grade is not None:
            assert f is not None and f.spec == c.test.spec and f.grade == c.test.final_grade
            assert c.features(point_estimate=True).se == 0.0
        else:
            assert f is None


def test_streams_are_independent_and_stable():
    assert stream("a", 1).random() == stream("a", 1).random()
    assert stream("a", 1).random() != stream("b", 1).random()
    assert grade_index("below_intern") == -1 and grade_index(None) == -1 and grade_index("senior") == 3


def test_vcat_profiles_deterministic_and_classified():
    reg1, hard1 = vcat.generate(per_spec=3, seed=11)
    reg2, hard2 = vcat.generate(per_spec=3, seed=11)
    assert reg1 == reg2 and hard1 == hard2
    assert len(reg1) == 3 * len(vcat.specializations()) and len(hard1) == 30
    res = vcat.evaluate(per_spec=6, seed=11)
    assert 0.0 <= res["macro_f1"] <= 1.0
    assert res["no_fsp_inputs_in_survey"] is True
    assert set(res["hard"]["by_kind"]) == {"fullstack", "career_change", "contradictory"}
