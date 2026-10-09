"""Страница «Проверка подбора на ваших данных» (/lab/check) — сквозной путь через API под ролью методиста (вход жюри):
импорт простого формата → сводка с метриками → топ по брифу с релевантностью → CSV топов; набор пар из репозитория;
слепая выдача по банку платформы.

Главное свойство: топ по брифу построен в том же порядке, по которому посчитан P@k, — P@10 пересчитывается руками
по галочкам ✓/✗ и совпадает со сводкой.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from app.models.users import User
from app.services.demo import jury_admin as svc
from app.services.demo import jury_simple as js
from app.services.demo import models as _demo_models  # noqa: F401

PAIRS_FILE = Path(__file__).resolve().parents[1] / "eval" / "pairs_test.jsonl"


@pytest.fixture
def jury(make_account, db_session):
    acc = make_account()
    db_session.get(User, acc.user_id).role = "methodist"  # вход жюри «Лаборатория» — роль методиста
    db_session.flush()
    return acc


def template_body(name: str = "Шаблон жюри") -> dict:
    d = js.TEMPLATES_DIR
    return {"name": name, **{t: (d / f"{t}.csv").read_text(encoding="utf-8") for t in ("vacancies", "candidates", "labels")}}


def manual_p_at(items: list[dict], k: int) -> float:
    return sum(1 for i in items[:k] if i["relevant"]) / k


def test_simple_import_to_metrics_and_tops(client, jury):
    r = client.post("/api/admin/import/simple", json=template_body(), headers=jury.headers)
    assert r.status_code == 201, r.text
    imp = r.json()
    assert imp["briefs"] == 3 and imp["candidates"] == 8 and imp["unconfirmed"] == 8 and imp["confirmed"] == 0
    assert imp["pairs"] == 24 and imp["stats"]["pairs_added"] == 15
    assert "не подтверждена" in imp["note"].lower()
    bid = imp["batch_id"]

    s = client.get(f"/api/admin/check/batches/{bid}", headers=jury.headers)
    assert s.status_code == 200, s.text
    summ = s.json()
    assert summ["source"] == "batch" and summ["title"] == "Шаблон жюри" and summ["n_labeled_briefs"] == 3
    assert summ["n_unconfirmed"] == 8 and summ["relevant_from"] == 2
    ours = summ["metrics"]["ours"]
    assert set(ours) == {"p@5", "p@10", "ndcg@10"} and ours["p@10"]["n_briefs"] == 3
    assert [b["brief_id"] for b in summ["briefs"]] == ["v1", "v2", "v3"]
    assert summ["briefs"][0]["category"] == "Backend (Python) · Middle"
    assert any("меньше 10" in n for n in summ["notes"])  # 8 кандидатов: P@10 ограничен сверху
    # Те же числа, что у POST /api/admin/eval/pairs по этой загрузке
    pairs = client.post("/api/admin/eval/pairs", json={"batch_id": bid}, headers=jury.headers).json()
    assert pairs["ours"] == ours

    per = {b["brief_id"]: b for b in summ["briefs"]}
    for brief_id in ("v1", "v2", "v3"):
        d = client.get(f"/api/admin/check/batches/{bid}/briefs/{brief_id}", params={"top": 10}, headers=jury.headers)
        assert d.status_code == 200, d.text
        det = d.json()
        assert det["mode"] == "labeled" and det["n_labeled"] == 8 and det["pool_size"] == 8
        items = det["items"]
        assert [i["rank"] for i in items] == list(range(1, len(items) + 1))
        assert all(i["rel"] is not None and i["relevant"] == (i["rel"] >= 2) for i in items)
        assert all(i["group"] == "unconfirmed" and "unconfirmed" in i["flags"] for i in items)
        assert all(i["profile"] and not i["profile"]["confirmed"] for i in items)
        # P@k по галочкам = P@k сводки
        assert manual_p_at(items, 10) == pytest.approx(per[brief_id]["metrics"]["p@10"])
        assert manual_p_at(items, 5) == pytest.approx(per[brief_id]["metrics"]["p@5"])
        assert det["metrics"] == per[brief_id]["metrics"]
    v1 = client.get(f"/api/admin/check/batches/{bid}/briefs/v1", headers=jury.headers).json()
    assert v1["brief"]["parsed"]["salary_from"] == 220000 and v1["brief"]["form"]["salary_to"] == 280000
    assert {s["id"] for s in v1["brief"]["parsed"]["must"]} >= {"python", "django"}
    top1 = v1["items"][0]
    assert top1["candidate_id"] == "c01" and top1["rel"] == 3 and top1["has_fsp"]
    assert top1["profile"]["declared_skills"][:2] == ["Python", "Django"]
    assert any(c["state"] == "declared" for c in top1["chips"])  # заявленные навыки — серые чипы, не Score

    csv_r = client.get(f"/api/admin/check/batches/{bid}/tops.csv", params={"top": 3}, headers=jury.headers)
    assert csv_r.status_code == 200 and csv_r.headers["content-type"].startswith("text/csv") and csv_r.text.startswith("﻿")
    table = list(csv.DictReader(io.StringIO(csv_r.text.lstrip("﻿"))))
    assert len(table) == 9 and {r["brief_id"] for r in table} == {"v1", "v2", "v3"}
    first_v1 = [r for r in table if r["brief_id"] == "v1"]
    assert [r["candidate_id"] for r in first_v1] == [i["candidate_id"] for i in v1["items"][:3]]
    assert first_v1[0]["rel"] == "3" and first_v1[0]["relevant"] == "1"


def test_brief_without_labels_ranks_whole_upload_in_recommended_categories(client, jury):
    body = template_body("Без разметки")
    body["labels"] = None
    imp = client.post("/api/admin/import/simple", json=body, headers=jury.headers).json()
    assert imp["pairs"] == 0
    summ = client.get(f"/api/admin/check/batches/{imp['batch_id']}", headers=jury.headers).json()
    assert summ["metrics"] is None and summ["n_labeled_briefs"] == 0
    assert any("без разметки" in n.lower() for n in summ["notes"])
    det = client.get(f"/api/admin/check/batches/{imp['batch_id']}/briefs/v2", headers=jury.headers).json()
    assert det["mode"] == "pool" and det["metrics"] is None and det["pool_size"] == 8
    assert det["items"] and all(i["rel"] is None and i["relevant"] is None for i in det["items"])
    assert {(c["spec"], c["grade"]) for c in det["categories"]} >= {("frontend_js", "junior")}
    assert det["items"][0]["candidate_id"] == "c04"  # Frontend Junior под бриф Frontend Junior
    r = client.get(f"/api/admin/check/batches/{imp['batch_id']}/briefs/v9", headers=jury.headers)
    assert r.status_code == 404 and r.json()["code"] == "brief_not_found"


def test_simple_import_errors_are_russian_with_line_and_field(client, jury):
    body = template_body()
    body["labels"] = "vacancy_id,candidate_id,rel\nv1,c01,3\nv1,c02,7\n"
    r = client.post("/api/admin/import/simple", json=body, headers=jury.headers)
    assert r.status_code == 400
    e = r.json()
    assert e["code"] == "import_invalid" and e["message"].startswith("Разметка, строка 3, поле «rel»")
    assert e["details"] == {"table": "labels", "line": 3, "field": "rel"}
    assert client.get("/api/admin/import", headers=jury.headers).json() == []  # без частичной записи


def test_jsonl_pairs_import_is_checked_the_same_way(client, jury):
    rows = [json.loads(line) for line in PAIRS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if r["brief_id"] in ("b01", "b03")]
    imp = client.post("/api/admin/import", json={"name": "JSONL", "pairs": rows}, headers=jury.headers).json()
    summ = client.get(f"/api/admin/check/batches/{imp['batch_id']}", headers=jury.headers).json()
    assert [b["brief_id"] for b in summ["briefs"]] == ["b01", "b03"] and summ["n_unconfirmed"] > 0
    assert summ["metrics"]["ours"] == svc.pairs_metrics(rows)["ours"]
    assert client.get("/api/admin/import", headers=jury.headers).json()[0]["format"] == "pairs"


def test_repo_pairs_summary_and_manual_check(client, jury):
    r = client.get("/api/admin/check/repo", headers=jury.headers)
    assert r.status_code == 200, r.text
    summ = r.json()
    rows = [json.loads(line) for line in PAIRS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert summ["source"] == "repo" and summ["n_pairs"] == len(rows) and summ["n_briefs"] == len({r["brief_id"] for r in rows})
    assert set(summ["metrics"]["reported_systems"]) >= {"ours", "b0", "b1", "b2", "b3"}
    assert set(summ["system_labels"]) >= {"ours", "b0", "b1", "b2", "b3"}
    for m in summ["metrics"]["ours"].values():
        assert m["ci_low"] <= m["mean"] <= m["ci_high"]
    assert all(b["gold_category"] and b["metrics"] for b in summ["briefs"])

    per = {b["brief_id"]: b for b in summ["briefs"]}
    for brief_id in ("b01", "b21"):
        det = client.get(f"/api/admin/check/repo/briefs/{brief_id}", headers=jury.headers).json()
        assert det["source"] == "repo" and det["mode"] == "labeled" and len(det["items"]) == 10
        assert det["brief"]["gold"]["category"] and det["brief"]["parsed"]["source"] == "parser"
        assert manual_p_at(det["items"], 10) == pytest.approx(per[brief_id]["metrics"]["p@10"])
        assert det["metrics"]["ndcg@10"] == per[brief_id]["metrics"]["ndcg@10"]
        for i in det["items"]:
            assert i["reasons"] and i["category"] and 0 <= i["rel"] <= 3
        assert all(b["rank"] > 10 and b["rel"] >= 2 for b in det["relevant_below"])
    gold = client.get("/api/admin/check/repo/briefs/b01", params={"use_gold": True, "top": 5}, headers=jury.headers).json()
    assert gold["brief"]["parsed"]["source"] == "gold" and len(gold["items"]) == 5
    assert client.get("/api/admin/check/repo/briefs/b02", headers=jury.headers).status_code == 404  # b02 — не test-бриф
    r = client.get("/api/admin/check/repo/tops.csv", params={"top": 2}, headers=jury.headers)
    assert r.status_code == 200 and len(r.text.lstrip("﻿").strip().splitlines()) == 1 + 2 * summ["n_briefs"]


def test_platform_bank_is_blind(client, jury):
    ok = client.post(
        "/api/admin/eval/rank",
        json={"text": "Нужен Middle Python-разработчик, Django. Гибрид. 220–280 тыс. на руки", "source": "platform", "top": 5},
        headers=jury.headers,
    )
    assert ok.status_code == 200, ok.text
    res = ok.json()
    assert res["brief"]["category"] == "Backend (Python) · Middle" and "Django" in res["brief"]["must_names"] + res["brief"]["nice_names"]
    # Псевдонимы вместо id пользователя: стабильные, уникальные в ответе, без исходного id
    fake = {"items": [{"candidate_id": "8a0c6f7e-0000-4000-8000-000000000001", "source": "platform"},
                      {"candidate_id": "8a0c6f7e-0000-4000-8000-000000000002", "source": "platform"},
                      {"candidate_id": "jury-1", "source": "import"}]}
    out = svc.blind_platform_ids(json.loads(json.dumps(fake)))
    a, b, c = (i["candidate_id"] for i in out["items"])
    assert a.startswith("К-") and b.startswith("К-") and a != b and c == "jury-1"
    assert svc.blind_platform_ids(json.loads(json.dumps(fake)))["items"][0]["candidate_id"] == a
