"""Админ-API жюри: импорт кандидатов и брифов (формат eval/pairs_test.jsonl) → ранжирование с компонентами Score.

Кандидаты без теста попадают в группу «категория по опросу, не подтверждена» и ранжируются ниже подтверждённых
(DECISIONS п. 5, G3). Импорт и ранжирование — тот же движок app.matching.*, что у подборки работодателя."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from app.models.users import User
from app.services.demo import models as _demo_models  # noqa: F401

PAIRS_FILE = Path(__file__).resolve().parents[1] / "eval" / "pairs_test.jsonl"


def load_rows(*brief_ids: str, limit: int | None = None) -> list[dict]:
    rows = [json.loads(line) for line in PAIRS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if not brief_ids or r["brief_id"] in brief_ids]
    return rows[:limit] if limit else rows


@pytest.fixture
def admin(make_account, db_session):
    acc = make_account(role="candidate")
    db_session.get(User, acc.user_id).role = "admin"  # роль админа саморегистрацией не выдаётся
    db_session.flush()
    return acc


def do_import(client, admin, rows, name="Тест жюри"):
    r = client.post("/api/admin/import", json={"name": name, "pairs": rows}, headers=admin.headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_import_requires_admin_role(client, make_account):
    acc = make_account()
    assert client.post("/api/admin/import", json={"pairs": []}, headers=acc.headers).status_code == 403
    assert client.post("/api/admin/import", json={"pairs": []}).status_code == 401
    assert client.post("/api/admin/eval/rank", json={"text": "x"}, headers=acc.headers).status_code == 403
    assert client.post("/api/admin/eval/pairs", json={}, headers=acc.headers).status_code == 403


def test_import_counts_and_cold_start_group(client, admin):
    rows = load_rows("b01", "b03")
    out = do_import(client, admin, rows)
    assert out["briefs"] == 2 and out["pairs"] == len({(r["brief_id"], r["candidate_id"]) for r in rows})
    n_cand = len({r["candidate_id"] for r in rows})
    n_cold = len({r["candidate_id"] for r in rows if r["candidate"]["features"] is None})
    assert out["candidates"] == n_cand and out["unconfirmed"] == n_cold > 0
    assert out["confirmed"] == n_cand - n_cold
    assert "категория по опросу, не подтверждена" in out["note"].lower()

    lst = client.get("/api/admin/import", headers=admin.headers).json()
    assert lst[0]["id"] == out["batch_id"] and lst[0]["unconfirmed"] == n_cold
    assert client.delete(f"/api/admin/import/{out['batch_id']}", headers=admin.headers).json()["deleted"] is True
    assert client.get("/api/admin/import", headers=admin.headers).json() == []


def test_import_validation_errors_name_the_field(client, admin):
    rows = load_rows("b01", limit=3)
    bad = json.loads(json.dumps(rows))
    del bad[1]["brief"]["gold"]["spec"]
    r = client.post("/api/admin/import", json={"pairs": bad}, headers=admin.headers)
    assert r.status_code == 400 and r.json()["code"] == "import_invalid" and "pairs[1].brief.gold" in r.json()["message"]
    bad = json.loads(json.dumps(rows))
    bad[0]["rel"] = 5
    r = client.post("/api/admin/import", json={"pairs": bad}, headers=admin.headers)
    assert r.status_code == 400 and "pairs[0].rel" in r.json()["message"]
    bad = json.loads(json.dumps(rows))
    bad[2]["candidate"]["features"]["grade"] = "wizard"
    assert client.post("/api/admin/import", json={"pairs": bad}, headers=admin.headers).status_code == 400
    assert client.post("/api/admin/import", json={}, headers=admin.headers).json()["code"] == "import_empty"


def test_import_then_rank_round_trip_with_components_and_reasons(client, admin):
    rows = load_rows("b01", "b03")
    out = do_import(client, admin, rows)
    r = client.post(
        "/api/admin/eval/rank",
        json={"brief_id": "b01", "batch_id": out["batch_id"], "top": 15, "all_categories": True},
        headers=admin.headers,
    )
    assert r.status_code == 200, r.text
    res = r.json()
    items = res["items"]
    assert 0 < len(items) <= 15 and res["pool_size"] == out["candidates"]
    assert [i["rank"] for i in items] == list(range(1, len(items) + 1))
    assert res["brief"]["spec"] == "backend_python" and res["brief"]["grade"] == "middle"
    for it in items:
        assert {"T", "K"} <= set(it["components"]) and it["category"]
        assert abs(sum(it["contributions"].values()) - it["score"]) <= 0.2  # Σ вкладов = Score
        assert it["group"] in ("confirmed", "unconfirmed") and it["source"] == "import"
        assert all(x["kind"] in ("pro", "con") and x["text"] for x in it["reasons"])
    assert any(x["kind"] == "pro" for it in items for x in it["reasons"])
    scores = [i["score"] for i in items if i["group"] == "confirmed"]
    assert scores == sorted(scores, reverse=True)

    # Все: подтверждённые выше неподтверждённых, у неподтверждённых — флаг
    full = client.post(
        "/api/admin/eval/rank",
        json={"brief_id": "b01", "batch_id": out["batch_id"], "top": 200, "all_categories": True},
        headers=admin.headers,
    ).json()
    groups = [i["group"] for i in full["items"]]
    assert "unconfirmed" in groups and "confirmed" in groups
    first_cold = groups.index("unconfirmed")
    assert "confirmed" not in groups[first_cold:], "подтверждённые не должны идти после неподтверждённых"
    cold = next(i for i in full["items"] if i["group"] == "unconfirmed")
    assert "unconfirmed" in cold["flags"] and "не подтверждена" in cold["group_label"].lower()
    assert full["unconfirmed"] >= 1 and full["confirmed"] >= 1


def test_rank_csv_and_json_agree(client, admin):
    out = do_import(client, admin, load_rows("b05"))
    body = {"brief_id": "b05", "batch_id": out["batch_id"], "top": 10}
    js = client.post("/api/admin/eval/rank", json=body, headers=admin.headers).json()
    r = client.post("/api/admin/eval/rank", params={"format": "csv"}, json=body, headers=admin.headers)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.startswith("﻿")
    table = list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
    assert [row["candidate_id"] for row in table] == [i["candidate_id"] for i in js["items"]]
    assert [float(row["score"]) for row in table] == [i["score"] for i in js["items"]]
    assert {"rank", "group", "category", "pros", "cons", "x_T", "x_K"} <= set(table[0])


def test_cold_start_candidate_without_test_ranks_below_confirmed(client, admin):
    rows = load_rows("b01", limit=40)
    out = do_import(client, admin, rows)
    survey_only = {
        "candidate_id": "jury-cold-1",
        "features": None,
        "self_description": "Backend-разработчик, Python и Django, PostgreSQL, Docker",
        "claimed_spec": "backend_python",
        "claimed_grade": "middle",
        "survey": {"declared_skills": ["python", "django", "postgresql", "docker"], "salary_min": 200000, "formats": ["hybrid"]},
    }
    r = client.post("/api/admin/import", json={"name": "Только кандидаты", "candidates": [survey_only]}, headers=admin.headers)
    assert r.status_code == 201 and r.json()["unconfirmed"] == 1 and r.json()["pairs"] == 0
    batch = r.json()["batch_id"]
    res = client.post(
        "/api/admin/eval/rank",
        json={"batch_id": batch, "text": "Ищем Middle Python-разработчика: Django, PostgreSQL. Гибрид. 200–260 тыс. на руки",
              "top": 5},
        headers=admin.headers,
    ).json()
    assert [i["candidate_id"] for i in res["items"]] == ["jury-cold-1"] and res["items"][0]["group"] == "unconfirmed"
    assert out["batch_id"] != batch


def test_rank_errors_and_platform_source(client, admin):
    r = client.post("/api/admin/eval/rank", json={"brief_id": "b01"}, headers=admin.headers)
    assert r.status_code == 404 and r.json()["code"] == "batch_not_found"
    out = do_import(client, admin, load_rows("b01", limit=20))
    r = client.post("/api/admin/eval/rank", json={"brief_id": "b99", "batch_id": out["batch_id"]}, headers=admin.headers)
    assert r.status_code == 404 and r.json()["code"] == "brief_not_found"
    r = client.post("/api/admin/eval/rank", json={"batch_id": out["batch_id"]}, headers=admin.headers)
    assert r.status_code == 400 and r.json()["code"] == "brief_required"
    r = client.post(
        "/api/admin/eval/rank", json={"batch_id": out["batch_id"], "text": "Нужен Python-разработчик, Django"}, headers=admin.headers
    )
    assert r.status_code == 422 and r.json()["code"] == "brief_invalid"  # вилка обязательна
    ok = client.post(
        "/api/admin/eval/rank",
        json={"text": "Нужен Middle Python-разработчик, Django. Гибрид. 220–280 тыс. на руки", "source": "platform"},
        headers=admin.headers,
    )
    assert ok.status_code == 200 and ok.json()["items"] == [] and ok.json()["pool_size"] == 0  # банк платформы пуст
