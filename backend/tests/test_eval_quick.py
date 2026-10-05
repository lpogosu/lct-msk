"""Сквозной `python -m eval.validate --quick`: прогон завершается, пишет JSON для «Лаборатории», отчёт и графики."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from sqlalchemy import select

from eval import validate as V


@pytest.fixture(scope="module")
def quick_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("validate_quick")
    assert V.main(["--quick", "--workers", "1", "--out", str(out)]) == 0
    return out


def test_quick_run_writes_json_with_all_sections(quick_run: Path):
    data = json.loads((quick_run / V.QUICK_RESULT_NAME).read_text(encoding="utf-8"))
    assert data["meta"]["mode"] == "quick" and data["meta"]["seed"] == V.MASTER_SEED
    assert data["meta"]["total_s"] < 300
    for key in ("population", "testing", "categorization", "matching", "tiles"):
        assert key in data
    t = data["testing"]
    for key in ("accuracy", "retest", "hysteresis", "expert_error", "baselines", "leak", "cusum", "calibration",
                "discrimination", "instance_overlap", "no_fsp", "tags"):
        assert key in t
    tg = t["tags"]
    assert tg["modules"] > 0 and 0.0 <= tg["check"]["precision"] <= 1.0 and 0.0 <= tg["check"]["recall"] <= 1.0
    assert tg["concept_targets"]["preregistered"] is False
    assert t["instance_overlap"]["shared_between_candidates"] == 0
    assert [c["sigma_b"] for c in t["expert_error"]["conditions"]][:3] == [0.0, 0.3, 0.5]
    m = data["matching"]
    assert m["test"]["n_briefs"] == 20 and m["dev"]["n_briefs"] == 20
    assert "_rankings" not in m and "_rels" not in m
    assert m["fidelity"]["sum_equals_score"] == 1.0
    assert m["fidelity"]["fsp_never_con"] == 1.0
    ids = [x["id"] for x in data["tiles"]]
    assert len(ids) == len(set(ids)) and "m_p10" in ids and "t_decision" in ids
    by = {x["id"]: x for x in data["tiles"]}
    for tid in ("t_decision", "t_retest_agree", "l_coal20", "c_macro_f1", "m_p10", "m_fid_sum", "m_build"):
        assert by[tid]["value"] is not None, tid
        assert by[tid]["pass"] in (True, False)
    assert "θ" not in json.dumps(data, ensure_ascii=False)


def test_quick_run_writes_report_and_charts(quick_run: Path):
    md = (quick_run / "VALIDATION_quick.md").read_text(encoding="utf-8")
    for heading in ("## Итоги", "## 2. Тестирование", "## 3. Категоризация", "## 4. Подбор", "## 6. Честные ограничения"):
        assert heading in md
    assert "θ" not in md
    assert "ИИ-ассистента" in md  # честно: брифы написаны командой с ИИ-ассистентом
    assert "заменитель эмбеддингов" in md
    charts = sorted((quick_run / "charts_quick").glob("*.svg"))
    assert {p.name for p in charts} >= {"leak.svg", "calibration.svg", "p10.svg"}
    for p in charts:
        assert ET.parse(p).getroot().tag.endswith("svg")
        assert f"charts_quick/{p.name}" in md


def test_quick_is_deterministic(quick_run: Path, tmp_path: Path):
    """Тот же сид → те же цифры (кроме времени и git)."""
    a = json.loads((quick_run / V.QUICK_RESULT_NAME).read_text(encoding="utf-8"))
    rc = V.RunConfig.make(True)
    pop1 = V.build_population(rc, 1)
    assert len(pop1.candidates) == a["population"]["n"]
    assert pop1.summary()["mismatched_share"] == pytest.approx(a["population"]["mismatched_share"], abs=1e-4)


def _stable(data: dict) -> dict:
    """Результат без того, что честно зависит от машины: время, число процессов, git."""
    d = json.loads(json.dumps(data, ensure_ascii=False))
    d.pop("meta", None)
    d["matching"].pop("build_ms", None)
    d["tiles"] = [t for t in d["tiles"] if t["id"] != "m_build"]
    return d


def test_quick_result_does_not_depend_on_worker_count(quick_run: Path):
    """Разделы и куски популяции считаются в процессах; у каждого свой сид — цифры те же, что при 1 процессе."""
    a = json.loads((quick_run / V.QUICK_RESULT_NAME).read_text(encoding="utf-8"))
    b = V.public_result(V.run(V.RunConfig.make(True), workers=2, log=False))
    assert _stable(a) == _stable(b)


def test_lab_tiles_are_honest(quick_run: Path):
    data = json.loads((quick_run / V.QUICK_RESULT_NAME).read_text(encoding="utf-8"))
    by = {x["id"]: x for x in data["tiles"]}
    fm = data["testing"]["leak"]["false_middle"]
    i = data["testing"]["leak"]["coalitions"].index(20)
    # Плитка утечки — худшая для нас стратегия списывающего, а не самая выгодная.
    assert by["l_coal20"]["value"] == pytest.approx(max(fm["ours_naive"][i], fm["ours_careful"][i]))
    io = data["testing"]["instance_overlap"]
    assert io["by_construction"] is True
    assert 0.0 <= io["content_identical_pair_max"] <= 1.0 and io["content_families"] > 0
    assert by["t_content"]["pass"] is None  # порога нет — справочно
    for t in data["tiles"]:
        assert t["label"] and "θ" not in t["label"]


def test_from_json_saves_existing_result_without_rerun(quick_run: Path, db_session, monkeypatch):
    import app.db as app_db
    from app.models import ValidationRun

    class _Ctx:
        def __enter__(self):
            return db_session

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(app_db, "get_sessionmaker", lambda: (lambda: _Ctx()))
    monkeypatch.setattr(V, "run", lambda *a, **k: pytest.fail("--from-json не должен пересчитывать"))
    assert V.main(["--save-db", "--from-json", str(quick_run / V.QUICK_RESULT_NAME)]) == 0
    rows = db_session.scalars(select(ValidationRun)).all()
    assert {r.kind for r in rows} >= set(V.VALIDATION_KINDS)
    with pytest.raises(SystemExit):
        V.main(["--from-json", str(quick_run / V.QUICK_RESULT_NAME)])  # без --save-db — ошибка аргументов


def test_save_validation_runs(quick_run: Path, db_session):
    from app.models import ValidationRun

    data = json.loads((quick_run / V.QUICK_RESULT_NAME).read_text(encoding="utf-8"))
    n = V.save_validation_runs(db_session, data)
    assert n == len(V.VALIDATION_KINDS)
    rows = db_session.scalars(select(ValidationRun).order_by(ValidationRun.kind)).all()
    kinds = {r.kind for r in rows}
    assert kinds >= set(V.VALIDATION_KINDS)
    leak = next(r for r in rows if r.kind == "leak")
    assert leak.seeds == [V.MASTER_SEED]
    assert "false_middle" in leak.metrics and leak.metrics["tiles"]
