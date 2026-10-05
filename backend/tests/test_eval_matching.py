"""Валидация подбора: набор брифов, оракул, быстрый ранжировщик = версия подборки, веса, экспозиция, объяснения, пары."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.core.configs import load_config
from app.matching.scoring import GRADES, ScoringConfig
from app.matching.skills import get_catalog
from eval import matching_eval as ME
from eval.population import Candidate, Population, PopulationConfig, generate_candidate, generate_population


@pytest.fixture(scope="module")
def pop() -> Population:
    return generate_population(PopulationConfig(n=150, seed=31))


@pytest.fixture(scope="module")
def world(pop: Population) -> ME.World:
    return ME.World.build(pop)


# --------------------------------------------------------------------------- брифы


def test_brief_set_is_complete_and_labels_are_valid():
    briefs = ME.load_briefs()
    cat = get_catalog()
    specs = {s["code"] for s in load_config("taxonomy")["specializations"]}
    assert len(briefs) == 40
    assert len({b.id for b in briefs}) == 40
    assert sum(b.split == "test" for b in briefs) == 20 and sum(b.split == "dev" for b in briefs) == 20
    assert {b.style for b in briefs} == {"formal", "chat", "short", "long", "mixed_en"}
    for b in briefs:
        g = b.gold
        assert b.author == "team"
        assert g["spec"] in specs and g["grade"] in GRADES
        assert 1 <= len(g["must"]) <= 5
        for s in g["must"] + g["nice"]:
            assert s in cat.skills, (b.id, s)
        assert not set(g["must"]) & set(g["nice"])
        assert 0 < g["salary_from"] < g["salary_to"] <= 2 * g["salary_from"]
        assert set(g["formats"]) <= {"office", "hybrid", "remote"} and g["formats"]
        assert g["city"] is None or g["city"] in cat.cities
        assert len(b.text) > 20


def test_run_brief_always_yields_brief_spec(world: ME.World):
    for b in ME.load_briefs():
        r = ME.run_brief(b, world)
        assert r.spec.salary_from and r.spec.salary_to
        assert r.categories
        if b.form:
            assert r.spec.salary_to == b.form["salary_to"]


# --------------------------------------------------------------------------- оракул


def _cand(**truth: object) -> Candidate:
    c = generate_candidate(0, PopulationConfig(n=1, seed=1, run_tests=False))
    mods = {m: 0.5 for m in c.truth.module_theta}
    c.truth = replace(c.truth, **{"module_theta": mods, **truth})
    c.salary_min = 200_000
    return c


def test_oracle_rules():
    cfg, cat = ScoringConfig.load(), get_catalog()
    c = _cand(spec="backend_python", grade="middle", theta=0.5, skills=frozenset({"python", "django", "docker"}))
    gold = {"spec": "backend_python", "grade": "middle", "must": ["python", "django"], "nice": [], "salary_to": 250_000,
            "salary_net": True}

    def rel(**g: object) -> int:
        return ME.oracle_rel(c, {**gold, **g}, cfg, cat)

    assert rel() == 3
    assert rel(must=["python", "kafka"]) == 2  # покрыто 1 из 2: −1
    assert rel(must=["python", "kafka", "redis", "go"]) == 1  # покрыто меньше половины: −2
    assert rel(must=["python", "docker"]) == 3  # docker не тестируется — достаточно истинного стека
    assert rel(grade="junior") == 2 and rel(grade="senior") == 2  # соседний грейд: −1
    assert rel(grade="intern") == 0  # две ступени
    assert rel(spec="frontend_js") == 0  # смежность 0,3
    assert rel(spec="fullstack") == 3  # смежность 0,8
    c.salary_min = 300_000
    assert rel() == 2  # минимум > 1,1 · верх вилки
    assert rel(salary_to=300_000, salary_net=False) == 2  # до НДФЛ: 300 000 · 0,87 · 1,1 < 300 000
    c.salary_min = 200_000
    c.truth = replace(c.truth, module_theta={**c.truth.module_theta, "python_core": -1.0})
    assert rel(must=["python"]) == 1  # тестируемый навык: модуль ниже порога − 0,5
    assert ME.irrelevance_reason(c, {**gold, "must": ["python"]}, cfg, cat) == "must_lt_half"


# --------------------------------------------------------------------------- ранжирование


def test_rank_fast_equals_build_version_order(world: ME.World):
    for b in ME.load_briefs()[:6]:
        r = ME.run_brief(b, world)
        v, ms = ME.build_ours(r.spec, r.categories, world)
        assert ms >= 0
        assert ME.rank_fast(r.spec, r.categories, world.pool, ME._ctx(world)) == [i.candidate_id for i in v.visible()]


def test_fake_skills_never_move_rank(world: ME.World):
    b = ME.load_briefs()[0]
    r = ME.run_brief(b, world)
    ctx = ME._ctx(world)
    before = ME.rank_fast(r.spec, r.categories, world.pool, ctx)
    weak = set(before[-10:])
    stuffed = [ME._stuff(f, r.spec.skills[:5]) if f.candidate_id in weak else f for f in world.pool]
    after = ME.rank_fast(r.spec, r.categories, stuffed, ctx)
    # Заявленные навыки — только ключ сортировки при равных Score и T: слабые не поднимаются выше сильных.
    assert set(after[:-10]) == set(before[:-10])


def test_shifted_and_ablation_weights_respect_constraints():
    cfg = ScoringConfig.load()
    base = cfg.weights()
    for comp in base:
        for d in (0.05, -0.05):
            w = ME.shifted_weights(base, comp, d, cfg)
            if w is None:
                continue
            assert sum(w.values()) == pytest.approx(1.0)
            assert w["T"] >= 0.30 - 1e-9 and w["F"] <= 0.15 + 1e-9
            assert w[comp] == pytest.approx(base[comp] + d)
    assert ME.shifted_weights(base, "F", 0.05, cfg) is None  # wF уже на потолке 0,15
    no_f = ME.ablation_weights(base, "F")
    assert no_f["F"] == 0 and no_f["T"] == pytest.approx(base["T"] + base["F"])


def test_exposure_ratio_toy():
    rows = [
        {"decile": 0, "has_fsp": True, "relevant": True, "top10": True},
        {"decile": 0, "has_fsp": True, "relevant": True, "top10": True},
        {"decile": 0, "has_fsp": False, "relevant": True, "top10": True},
        {"decile": 0, "has_fsp": False, "relevant": True, "top10": False},
        {"decile": 1, "has_fsp": False, "relevant": False, "top10": True},
    ]
    r = ME.exposure_ratio(rows, 10)
    assert r["expected"] == pytest.approx(1.5) and r["observed"] == 1
    assert r["conditional"] == pytest.approx(2 / 3)
    assert r["unconditional"] == pytest.approx((1 / 3) / (2 / 4))


def test_exposure_full_scope_matches_version_when_not_truncated(world: ME.World):
    """Экспозиция по всем кандидатам категорий совпадает со строками версии, пока версия не обрезана до 500."""
    for b in ME.load_briefs()[:4]:
        r = ME.run_brief(b, world)
        v, _ = ME.build_ours(r.spec, r.categories, world)
        rels = world.rels(b.gold)
        full = ME.exposure_rows_full(r.spec, r.categories, world, rels, (10, 50))
        assert len(v.visible()) < 500
        assert full == ME.exposure_rows(v, rels, (10, 50))


def test_explanations_are_faithful(world: ME.World):
    b = ME.load_briefs()[0]
    r = ME.run_brief(b, world)
    v, _ = ME.build_ours(r.spec, r.categories, world)
    fid = ME.explanation_fidelity(v, r.spec, world, top_n=20)
    assert fid["items"] > 0
    assert fid["sum_equals_score"] == 1.0
    assert fid["evidence_exists"] == 1.0
    assert fid["fsp_never_con"] == 1.0
    assert fid["pros_above_median"] == 1.0


# --------------------------------------------------------------------------- прогон и набор пар


def test_evaluate_and_export_pairs(tmp_path, pop: Population):
    briefs = [b for b in ME.load_briefs() if b.id in ("b01", "b02", "b03", "b04")]
    res = ME.evaluate(pop, seed=1, n_boot=50, briefs=briefs)
    assert res["test"]["n_briefs"] == 2 and res["dev"]["n_briefs"] == 2
    for s in (*ME.SYSTEMS, *ME.ABLATIONS):
        m = res["test"]["systems"][s]["p10"]
        assert 0.0 <= m["mean"] <= 1.0
    assert res["robustness"]["stuffing"]["ours_mean_shift_up"] == 0
    assert set(res["top10_errors"]) >= {"ours", "b2", "labels"}
    path = tmp_path / "pairs.jsonl"
    info = ME.export_pairs(res, pop, path, seed=1)
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert info["lines"] == len(lines) > 0
    schema = ME.pairs_schema()
    json.dumps(schema)
    feat_req = schema["properties"]["candidate"]["properties"]["features"]["required"]
    for row in lines:
        assert set(schema["required"]) <= set(row)
        assert row["brief_id"] in ("b01", "b03")
        assert 0 <= row["rel"] <= 3
        assert set(row["brief"]["gold"]) >= set(schema["properties"]["brief"]["properties"]["gold"]["required"])
        f = row["candidate"]["features"]
        assert f is None or set(feat_req) <= set(f)
        assert "truth" not in json.dumps(row["candidate"])
    assert "_rankings" not in ME.strip_private(res)
