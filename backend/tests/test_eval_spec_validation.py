"""Test-retest и разделяющая способность (eval/spec_validation.py), набор жюри (eval/jury_check.py): быстро, малые N."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import jury_check as JC
from eval import metrics as M
from eval import spec_validation as SV

SEED = 77


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    profiles = [p for s in ("backend_python", "qa") for p in SV.make_profiles(s, 12, SEED)]
    return SV.simulate_profiles(profiles, SEED)


def test_profiles_are_deterministic_and_balanced():
    a = SV.make_profiles("devops", 8, SEED)
    assert a == SV.make_profiles("devops", 8, SEED)
    assert [p["true_grade"] for p in a] == list(SV.GRADES) * 2
    assert a != SV.make_profiles("devops", 8, SEED + 1)


def test_rows_are_reproducible():
    p = SV.make_profiles("backend_python", 2, SEED)
    assert SV.simulate_profiles(p, SEED) == SV.simulate_profiles(p, SEED)


def test_retest_sessions_use_different_exposure(rows):
    # A↔B — независимые клоны (семейства могут совпасть); A↔C — по правилам пересдачи семейства A исключены
    assert all(r["c_shared_families"] == 0 for r in rows)
    assert any(r["a"]["theta"] != r["b"]["theta"] for r in rows)


def test_summary_metrics_in_range(rows):
    s = SV.summarize(rows, ["backend_python", "qa"])
    assert set(s) == {"backend_python", "qa", "all"}
    for b in s.values():
        for key in ("retest_parallel", "retest_real_rules"):
            assert 0 <= b[key]["grade_exact"] <= 1
            assert b[key]["grade_within1"] >= b[key]["grade_exact"]
            assert set(b[key]["flip_across_cut"]) == {"intern|junior", "junior|middle", "middle|senior"}
        sep = b["separation"]
        assert sep["n"] + sep["n_excluded_under_claim"] == b["n"]
        assert sum(sum(r.values()) for r in sep["confusion"].values()) == sep["n"]
        for c in sep["per_cut"].values():
            assert 0 <= c["decision_accuracy"] <= 1
        assert all(v["sem_from_information"] > 0 for v in sep["information"].values())


def test_separation_beats_chance_on_cuts(rows):
    cuts = SV.summarize(rows, ["backend_python", "qa"])["all"]["separation"]["per_cut"]
    assert all(c["auc_theta"] > 0.8 for c in cuts.values())


def test_retest_block_perfect_agreement():
    fake = [{"a": {"idx": i % 4, "passed": True, "theta": float(i)}, "b": {"idx": i % 4, "passed": True, "theta": float(i)},
             "theta_true": 0.0} for i in range(8)]
    out = SV.retest_block(fake, "b")
    assert out["grade_exact"] == 1 and out["flip_any_cut"] == 0 and out["theta_r"] == pytest.approx(1)


def test_leak_does_not_help_naive_cheater():
    cfg = SV.SpecRunConfig(seed=SEED, n_leak=8, coalition=3, n_collision=6)
    res = SV.leak_gain("devops", cfg)
    assert res["theta_gain_mean"] < 0.1
    assert res["false_pass_with_leak"] <= res["false_pass_honest"]


def test_jury_check_cli_on_example(tmp_path: Path, capsys):
    out = tmp_path / "r.json"
    example = Path(__file__).resolve().parents[1] / "eval" / "jury_profiles_example.jsonl"
    assert JC.main([str(example), "--seed", "3", "--out", str(out)]) == 0
    res = json.loads(out.read_text(encoding="utf-8"))
    assert res["n"] == 16 and len(res["profiles"]) == 16
    assert set(res["summary"]) == {*SV.SPECS, "all"}
    assert "Стажёр|Junior" not in capsys.readouterr().out  # вывод использует имена порогов intern|junior
    # тот же сид — тот же JSON
    out2 = tmp_path / "r2.json"
    JC.main([str(example), "--seed", "3", "--out", str(out2)])
    assert out.read_text(encoding="utf-8") == out2.read_text(encoding="utf-8")


def test_jury_csv_and_validation(tmp_path: Path):
    csv_path = tmp_path / "p.csv"
    csv_path.write_text("id,spec,true_grade,theta,claimed_grade\na1,qa,junior,-0.4,junior\na2,devops,senior,,\n", encoding="utf-8")
    profs = JC.load_profiles(csv_path)
    assert profs[0]["theta"] == pytest.approx(-0.4) and profs[1]["theta"] is None and profs[1]["claimed_grade"] == "senior"
    filled = JC.fill_theta(profs, 1)
    assert filled[1]["theta"] > 0.5
    for bad in ({"id": "x", "spec": "cobol", "true_grade": "junior"}, {"id": "x", "spec": "qa", "true_grade": "god"},
                {"id": "", "spec": "qa", "true_grade": "junior"}, {"id": "x", "spec": "qa", "true_grade": "junior", "theta": "abc"}):
        with pytest.raises(JC.ProfileError):
            JC.parse_profile(bad, 2)
    with pytest.raises(JC.ProfileError):
        JC.fill_theta([profs[0], profs[0]], 1)


def test_jury_cli_reports_format_error(tmp_path: Path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id": "a", "spec": "qa"}\n', encoding="utf-8")
    assert JC.main([str(bad), "--out", str(tmp_path / "o.json")]) == 2
    assert "true_grade" in capsys.readouterr().err


def test_round_tree_handles_summary(rows):
    assert M.round_tree(SV.summarize(rows, ["qa"]), 3)
