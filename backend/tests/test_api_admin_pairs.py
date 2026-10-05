"""POST /api/admin/eval/pairs: загрузка размеченных пар → P@5, P@10, nDCG@10 со средним по брифам и бутстреп-ДИ.
Контрольный пример с известным ответом, наш набор пар (ours, b0–b3), пары из сохранённого импорта, GET /api/admin/validation."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.models.users import User
from app.services.demo import models as _demo_models  # noqa: F401
from app.services.demo.cfg import demo_cfg

PAIRS_FILE = Path(__file__).resolve().parents[1] / "eval" / "pairs_test.jsonl"
GOLD = {
    "spec": "backend_python", "grade": "middle", "must": ["python"], "nice": [], "formats": ["hybrid"],
    "salary_from": 220000, "salary_to": 280000, "salary_net": True, "city": None,
}


@pytest.fixture
def admin(make_account, db_session):
    acc = make_account()
    db_session.get(User, acc.user_id).role = "admin"
    db_session.flush()
    return acc


def candidate(cid: str, theta: float) -> dict:
    """Кандидат с тестом: все различия между A, B и C — только уровень (T монотонен по θ)."""
    return {
        "features": {
            "candidate_id": cid, "spec": "backend_python", "grade": "middle", "theta": theta, "se": 0.3, "modules": {},
            "tasks": [], "vss_by_discipline": {}, "declared_skills": ["python"], "salary_min": 230000, "salary_net": True,
            "formats": ["hybrid"], "city": None, "relocation": False, "theta_age_days": 10.0,
        },
        "self_description": "Python-разработчик",
    }


def pair(brief_id: str, cid: str, theta: float, rel: int) -> dict:
    brief = {"text": "Нужен Middle Python-разработчик. Гибрид. 220–280 тыс. на руки", "gold": GOLD}
    return {"brief_id": brief_id, "brief": brief, "candidate_id": cid, "candidate": candidate(cid, theta), "rel": rel}


def test_known_answer_metrics_for_two_briefs(client, admin):
    # Бриф x: наш порядок A > B > C совпадает с разметкой → rel по порядку [3, 2, 0]
    # Бриф y: разметка перевёрнута, rel по нашему порядку [0, 2, 3]
    rows = [
        pair("x", "A", 1.5, 3), pair("x", "B", 0.5, 2), pair("x", "C", -0.5, 0),
        pair("y", "A", 1.5, 0), pair("y", "B", 0.5, 2), pair("y", "C", -0.5, 3),
    ]
    r = client.post("/api/admin/eval/pairs", json={"pairs": rows, "use_gold": True}, headers=admin.headers)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["n_briefs"] == 2 and res["n_pairs"] == 6 and res["n_candidates"] == 3 and res["n_unconfirmed"] == 0
    per = {b["brief_id"]: b for b in res["per_brief"]}
    # P@5 = (доля rel ≥ 2 среди первых 5, пустые места нерелевантны): x → 2/5, y → 2/5; P@10 = 2/10
    assert per["x"]["p@5"] == pytest.approx(0.4) and per["x"]["p@10"] == pytest.approx(0.2)
    assert per["y"]["p@5"] == pytest.approx(0.4) and per["y"]["p@10"] == pytest.approx(0.2)
    # nDCG: x — идеальный порядок = 1; y — [0, 2, 3] против идеала [3, 2, 0]
    dcg = 0 + (2**2 - 1) / math.log2(3) + (2**3 - 1) / math.log2(4)
    ideal = (2**3 - 1) + (2**2 - 1) / math.log2(3)
    assert per["x"]["ndcg@10"] == pytest.approx(1.0)
    assert per["y"]["ndcg@10"] == pytest.approx(dcg / ideal, abs=1e-3)
    ours = res["ours"]
    assert ours["p@5"]["mean"] == pytest.approx(0.4) and ours["p@10"]["mean"] == pytest.approx(0.2)
    assert ours["ndcg@10"]["mean"] == pytest.approx((1.0 + dcg / ideal) / 2, abs=1e-3)
    for m in ours.values():  # два брифа — ДИ считается и охватывает среднее
        assert m["n_briefs"] == 2 and m["ci_low"] is not None and m["ci_low"] <= m["mean"] <= m["ci_high"]
    # бутстреп детерминирован (seed в config/demo.yaml): повтор даёт то же самое
    again = client.post("/api/admin/eval/pairs", json={"pairs": rows, "use_gold": True}, headers=admin.headers).json()
    assert again["ours"] == res["ours"]
    assert any("пулинг" in n for n in res["notes"])


def test_one_brief_has_no_confidence_interval(client, admin):
    rows = [pair("x", "A", 1.5, 3), pair("x", "B", 0.5, 2), pair("x", "C", -0.5, 0)]
    res = client.post("/api/admin/eval/pairs", json={"pairs": rows, "use_gold": True}, headers=admin.headers).json()
    assert res["ours"]["p@5"]["mean"] == pytest.approx(0.4) and res["ours"]["p@5"]["ci_low"] is None
    assert any("доверительный интервал не считается" in n for n in res["notes"])


def test_file_pairs_with_reported_systems_and_stored_batch(client, admin):
    all_rows = [json.loads(line) for line in PAIRS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in all_rows if r["brief_id"] in ("b01", "b03", "b05")]
    res = client.post("/api/admin/eval/pairs", json={"pairs": rows}, headers=admin.headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["n_briefs"] == 3 and body["n_unconfirmed"] > 0  # кандидаты без теста учтены: ниже подтверждённых
    assert set(body["reported_systems"]) >= {"ours", "b0", "b1", "b2", "b3"}
    for name in ("p@5", "p@10", "ndcg@10"):
        m = body["ours"][name]
        assert 0.0 <= m["mean"] <= 1.0 and m["ci_low"] <= m["mean"] <= m["ci_high"]
        for system in body["reported_systems"].values():
            assert 0.0 <= system[name]["mean"] <= 1.0
    assert body["per_brief"][0]["n_relevant"] > 0

    # Те же пары из сохранённого импорта (без повторной загрузки)
    imp = client.post("/api/admin/import", json={"name": "b01-b05", "pairs": rows}, headers=admin.headers).json()
    stored = client.post("/api/admin/eval/pairs", json={"batch_id": imp["batch_id"]}, headers=admin.headers)
    assert stored.status_code == 200 and stored.json()["ours"] == body["ours"]
    only = client.post("/api/admin/eval/pairs", json={"pairs": rows, "systems": ["b0"]}, headers=admin.headers).json()
    assert set(only["reported_systems"]) == {"b0"}


def test_pairs_errors(client, admin):
    r = client.post("/api/admin/eval/pairs", json={}, headers=admin.headers)
    assert r.status_code == 404 and r.json()["code"] == "batch_not_found"
    rows = [pair("x", "A", 1.0, 3)]
    rows[0]["rel"] = 9
    r = client.post("/api/admin/eval/pairs", json={"pairs": rows}, headers=admin.headers)
    assert r.status_code == 400 and r.json()["code"] == "import_invalid"
    empty = client.post("/api/admin/import", json={"briefs": [{"brief_id": "z", "text": "Python. 100–150 тыс."}]}, headers=admin.headers)
    assert empty.status_code == 201 and empty.json()["pairs"] == 0
    r = client.post("/api/admin/eval/pairs", json={"batch_id": empty.json()["batch_id"]}, headers=admin.headers)
    assert r.status_code == 400 and r.json()["code"] == "pairs_required"


def test_validation_endpoint_reads_latest_json(client, admin, tmp_path, monkeypatch):
    f = tmp_path / "validation.json"
    f.write_text(json.dumps({"meta": {"seed": 1}, "matching": {"n_population": 5}, "tiles": []}), encoding="utf-8")
    monkeypatch.setitem(demo_cfg()["admin"], "validation_json", str(f))
    r = client.get("/api/admin/validation", headers=admin.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sections"] == ["matching", "meta", "tiles"] and body["meta"] == {"seed": 1}
    assert body["result"]["matching"]["n_population"] == 5

    monkeypatch.setitem(demo_cfg()["admin"], "validation_json", str(tmp_path / "нет.json"))
    r = client.get("/api/admin/validation", headers=admin.headers)
    assert r.status_code == 404 and r.json()["code"] == "validation_missing" and "make validate" in r.json()["message"]
