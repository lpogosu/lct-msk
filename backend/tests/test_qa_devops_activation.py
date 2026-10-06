"""Специализации QA и DevOps включены: справочник, банк под blueprint, CAT-сессии симулянта, популяция и демо-кандидаты.

Конфиги: config/taxonomy.yaml (модули qa_testing, devops_core; status active), skills.yaml (навыки → модули),
testing.yaml (веса опроса), anticheat.yaml (skill_aliases). Банк: families/qa и families/devops (CATALOG.md).
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from app.core.configs import load_config
from app.matching.skills import get_catalog
from app.testing.cat import load_blueprint
from app.testing.irt import load_scale
from app.testing.simulate import run_simulated_session
from eval.common import bank_for, grade_index, real_bank
from eval.population import (
    SPEC_MIX_WITH_QA_DEVOPS,
    SPEC_SKILLS,
    PopulationConfig,
    generate_candidate,
    generate_population,
)

NEW_SPECS = {"qa": "qa_testing", "devops": "devops_core"}


def _spec(code: str) -> dict:
    return next(s for s in load_config("taxonomy")["specializations"] if s["code"] == code)


@pytest.mark.parametrize("spec,module", list(NEW_SPECS.items()))
def test_taxonomy_activates_spec_with_blueprint(spec, module):
    s = _spec(spec)
    assert s["status"] == "active" and s["direction"] == spec
    bp = load_blueprint(spec)
    assert sum(bp.values()) == pytest.approx(1.0)
    assert max(bp, key=bp.get) == module and bp[module] >= 0.5 - 1e-9
    assert set(bp) <= set(load_config("taxonomy")["modules"])
    assert load_config("taxonomy")["modules"][module]["name"]


@pytest.mark.parametrize("spec", list(NEW_SPECS))
def test_bank_covers_every_blueprint_module_and_all_grades(spec):
    fams = bank_for(spec)
    by_module = Counter(f.module for f in fams)
    assert set(by_module) == set(load_blueprint(spec)), by_module
    grades = {f.target_grade for f in fams}
    assert {"intern", "junior", "middle", "senior"} <= grades  # есть семейства на каждый порог грейда
    assert sum(1 for f in fams if f.is_anchor) >= 4


def test_shared_families_open_for_new_specializations():
    by_id = {f.id: f for f in real_bank()}
    for fid in ("sql.group.having", "http.status.scenario", "http.pagination.offset_math", "sh.pipeline.uniq"):
        assert "qa" in by_id[fid].specs, fid
    for fid in ("sh.chmod.octal", "git.workflow.order", "http.ratelimit.token_bucket"):
        assert "devops" in by_id[fid].specs, fid
    # QA-семейства не попадают в тест Backend, DevOps — в тест Frontend
    assert not {f.id for f in bank_for("backend_python")} & {f.id for f in bank_for("devops") if f.id.startswith("ops.")}
    assert not {f.id for f in bank_for("frontend_js")} & {f.id for f in bank_for("qa") if f.id.startswith("qa.")}


@pytest.mark.parametrize("spec", list(NEW_SPECS))
def test_simulated_sessions_recover_grade_monotonically(spec):
    """CAT-симулянт на настоящем банке: грейд растёт с уровнем, ошибка не больше ступени, длина в пределах 8–20."""
    scale = load_scale()
    bank = real_bank()
    finals = []
    for theta, claimed in ((-1.6, "intern"), (-0.5, "junior"), (0.5, "middle"), (1.8, "senior")):
        res = [
            run_simulated_session(theta, spec, claimed, bank, random.Random(f"{spec}:{theta}:{k}"))
            for k in range(3)
        ]
        true_idx = grade_index(scale.grade_of(theta))
        for r in res:
            assert 8 <= r["n_total"] <= 20, (theta, r["n_total"])
            assert r["final_grade"] is None or abs(grade_index(r["final_grade"]) - true_idx) <= 1, (theta, r["final_grade"])
            mods = {it["module"] for it in r["items"]}
            assert mods <= set(load_blueprint(spec))
        finals.append(max(grade_index(r["final_grade"]) for r in res))
    assert finals == sorted(finals) and finals[-1] > finals[0]


# --------------------------------------------------------------------------- популяция


def test_population_skills_exist_in_the_catalog():
    cat = get_catalog()
    for spec in NEW_SPECS:
        ids = [sid for sid, _ in SPEC_SKILLS[spec]]
        assert len(ids) == len(set(ids)) >= 25
        assert all(sid in cat.skills for sid in ids), [sid for sid in ids if sid not in cat.skills]
    # у DevOps тестируемый стек — через devops_core, у QA — через qa_testing
    assert any("devops_core" in cat.modules_of(sid) for sid, _ in SPEC_SKILLS["devops"])
    assert any("qa_testing" in cat.modules_of(sid) for sid, _ in SPEC_SKILLS["qa"])


def test_population_with_qa_devops_has_about_fifteen_percent_each():
    assert sum(w for _, w in SPEC_MIX_WITH_QA_DEVOPS) == pytest.approx(1.0)
    pop = generate_population(PopulationConfig(n=600, seed=31, run_tests=False, spec_mix=SPEC_MIX_WITH_QA_DEVOPS))
    shares = pop.summary()["true_spec"]
    assert 0.10 <= shares["qa"] <= 0.20 and 0.10 <= shares["devops"] <= 0.20
    for c in pop.candidates:
        if c.truth.spec in NEW_SPECS:
            assert NEW_SPECS[c.truth.spec] in c.truth.module_theta
            assert c.claimed_spec in (c.truth.spec, "backend_python")
            assert c.truth.skills <= {sid for sid, _ in SPEC_SKILLS[c.truth.spec]}
            assert c.self_description.startswith(("QA-инженер", "DevOps-инженер")) or c.mismatched
        else:
            assert "qa_testing" not in c.truth.module_theta and "devops_core" not in c.truth.module_theta


def test_default_population_is_not_shifted_by_new_specializations():
    """Прежняя смесь (Backend 50 / Frontend 40 / Fullstack 10) и поток случайных чисел кандидатов не менялись:
    опубликованные числа валидации воспроизводимы."""
    cfg = PopulationConfig(n=60, seed=20261005, run_tests=False)
    pop = generate_population(cfg)
    assert set(pop.summary()["true_spec"]) <= {"backend_python", "frontend_js", "fullstack"}
    for c in pop.candidates:
        assert set(c.truth.module_theta) == {
            "python_core", "sql", "http_api", "algo", "git_linux", "js_core", "react", "html_css",
        }
    again = generate_candidate(7, cfg)
    assert again.to_public_dict() == pop.candidates[7].to_public_dict()


@pytest.mark.parametrize("spec", list(NEW_SPECS))
def test_qa_devops_candidate_gets_a_category_from_the_real_test(spec):
    cfg = PopulationConfig(n=1, seed=5, spec_mix=((spec, 1.0),), mismatch_rate=0.0)
    got = [generate_candidate(i, cfg) for i in range(8)]
    assert all(c.truth.spec == spec == c.claimed_spec and c.test is not None and c.test.spec == spec for c in got)
    confirmed = [c for c in got if c.test.final_grade is not None]
    assert len(confirmed) >= 5
    for c in confirmed:
        assert abs(grade_index(c.test.final_grade) - grade_index(c.truth.grade)) <= 1
        assert set(c.test.subscores) <= set(load_blueprint(spec))
        assert c.features() is not None and c.features().spec == spec


def test_validation_reports_accuracy_by_specialization():
    from eval import testing_eval as T
    from eval import validate as V

    pop = generate_population(PopulationConfig(n=60, seed=3, spec_mix=SPEC_MIX_WITH_QA_DEVOPS))
    by = T.accuracy_by_spec(pop)
    assert {"qa", "devops"} <= set(by)
    for spec, v in by.items():
        assert v["n"] > 0 and 0.0 <= v["decision_accuracy"] <= 1.0 and 0.0 <= v["confirmed_share"] <= 1.0, spec
        assert 8 <= v["total_mean"] <= 20 and v["grade"]["within1"] is not None
    assert list(T._specs_present(pop))[:2] == ["backend_python", "frontend_js"]
    assert V.RunConfig.make(True, with_qa_devops=True).with_qa_devops and not V.RunConfig.make(True).with_qa_devops
