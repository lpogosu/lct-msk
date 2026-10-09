"""Матрица доступа к инструментам жюри (/api/admin: импорт, ранжирование, метрики, страница проверки).

Аноним — 401, кандидат и работодатель — 403. Методист (вход жюри «Лаборатория» — это методист) видит, ранжирует и удаляет
только свои загрузки: чужая неотличима от несуществующей (404, защита от перебора id). Администратор видит все загрузки.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.users import User
from app.services.demo import models as _demo_models  # noqa: F401
from app.services.demo.cfg import demo_cfg

PAIR_BRIEF = {"text": "Нужен Middle Python-разработчик: Python, Django. Гибрид. 220–280 тыс. на руки"}


def staff(make_account, db_session, role: str):
    acc = make_account()
    db_session.get(User, acc.user_id).role = role  # служебные роли саморегистрацией не выдаются
    db_session.flush()
    return acc


@pytest.fixture
def admin(make_account, db_session):
    return staff(make_account, db_session, "admin")


@pytest.fixture
def methodist(make_account, db_session):
    return staff(make_account, db_session, "methodist")


def small_import(client, acc, name: str) -> str:
    body = {
        "name": name,
        "vacancies": [{"id": "v1", **PAIR_BRIEF}],
        "candidates": [{"id": "c1", "spec": "backend_python", "grade": "middle", "skills": "python;django"},
                       {"id": "c2", "spec": "qa", "grade": "junior"}],
        "labels": [{"vacancy_id": "v1", "candidate_id": "c1", "rel": 3}],
    }
    r = client.post("/api/admin/import/simple", json=body, headers=acc.headers)
    assert r.status_code == 201, r.text
    return r.json()["batch_id"]


ROUTES = [
    ("GET", "/api/admin/import", None),
    ("POST", "/api/admin/import", {"pairs": []}),
    ("POST", "/api/admin/import/simple", {"vacancies": []}),
    ("DELETE", f"/api/admin/import/{uuid.uuid4()}", None),
    ("POST", "/api/admin/eval/rank", {"text": "x"}),
    ("POST", "/api/admin/eval/pairs", {}),
    ("GET", "/api/admin/validation", None),
    ("GET", "/api/admin/check/templates/vacancies.csv", None),
    ("GET", "/api/admin/check/repo", None),
    ("GET", "/api/admin/check/repo/briefs/b01", None),
    ("GET", "/api/admin/check/repo/tops.csv", None),
    ("GET", f"/api/admin/check/batches/{uuid.uuid4()}", None),
    ("GET", f"/api/admin/check/batches/{uuid.uuid4()}/briefs/v1", None),
    ("GET", f"/api/admin/check/batches/{uuid.uuid4()}/tops.csv", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_anonymous_401_candidate_and_employer_403(client, make_account, method, path, body):
    assert client.request(method, path, json=body).status_code == 401
    for role in ("candidate", "employer"):
        acc = make_account(role=role)
        r = client.request(method, path, json=body, headers=acc.headers)
        assert r.status_code == 403, f"{role} {method} {path}: {r.status_code}"


def test_methodist_sees_only_own_imports_admin_sees_all(client, make_account, db_session, admin, methodist):
    other = staff(make_account, db_session, "methodist")
    mine = small_import(client, methodist, "мой набор")
    theirs = small_import(client, other, "чужой набор")
    by_admin = small_import(client, admin, "набор администратора")

    assert [b["id"] for b in client.get("/api/admin/import", headers=methodist.headers).json()] == [mine]
    assert [b["id"] for b in client.get("/api/admin/import", headers=other.headers).json()] == [theirs]
    assert {b["id"] for b in client.get("/api/admin/import", headers=admin.headers).json()} == {mine, theirs, by_admin}
    assert client.get("/api/admin/import", headers=methodist.headers).json()[0]["format"] == "simple"

    # Чужая загрузка для методиста не существует: сводка, бриф, CSV, ранжирование, метрики, удаление — 404
    h = methodist.headers
    for path in (f"/api/admin/check/batches/{theirs}", f"/api/admin/check/batches/{theirs}/briefs/v1",
                 f"/api/admin/check/batches/{theirs}/tops.csv"):
        r = client.get(path, headers=h)
        assert r.status_code == 404 and r.json()["code"] == "batch_not_found", path
    r = client.post("/api/admin/eval/rank", json={"batch_id": theirs, "brief_id": "v1"}, headers=h)
    assert r.status_code == 404 and r.json()["code"] == "batch_not_found"
    assert client.post("/api/admin/eval/pairs", json={"batch_id": theirs}, headers=h).status_code == 404
    assert client.delete(f"/api/admin/import/{theirs}", headers=h).status_code == 404
    assert client.delete(f"/api/admin/import/{by_admin}", headers=h).status_code == 404

    # «Последний импорт» по умолчанию — последний СВОЙ, даже если у других есть новее
    r = client.post("/api/admin/eval/rank", json={"brief_id": "v1", "all_categories": True}, headers=h)
    assert r.status_code == 200, r.text
    assert {i["candidate_id"] for i in r.json()["items"]} == {"c1", "c2"}
    assert client.post("/api/admin/eval/pairs", json={}, headers=h).json()["n_briefs"] == 1

    # Своё — можно: сводка, бриф, удаление
    assert client.get(f"/api/admin/check/batches/{mine}", headers=h).status_code == 200
    assert client.get(f"/api/admin/check/batches/{mine}/briefs/v1", headers=h).status_code == 200
    assert client.delete(f"/api/admin/import/{mine}", headers=h).json()["deleted"] is True
    assert client.get("/api/admin/import", headers=h).json() == []
    r = client.post("/api/admin/eval/rank", json={"brief_id": "v1"}, headers=h)
    assert r.status_code == 404 and r.json()["code"] == "batch_not_found"  # своих загрузок больше нет

    # Администратор работает с любой загрузкой и может удалить чужую
    assert client.get(f"/api/admin/check/batches/{theirs}", headers=admin.headers).status_code == 200
    assert client.delete(f"/api/admin/import/{theirs}", headers=admin.headers).json()["deleted"] is True
    assert [b["id"] for b in client.get("/api/admin/import", headers=other.headers).json()] == []


def test_methodist_can_use_repo_check_and_templates(client, methodist):
    r = client.get("/api/admin/check/templates/vacancies.csv", headers=methodist.headers)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0] == "id,text,salary_from,salary_to,salary_net"
    r = client.get("/api/admin/check/templates/example.json", headers=methodist.headers)
    assert r.status_code == 200 and set(r.json()) == {"vacancies", "candidates", "labels"}
    assert client.get("/api/admin/check/templates/..%2Fcfg.py", headers=methodist.headers).status_code in (404, 422)
    assert client.get("/api/admin/check/repo", headers=methodist.headers).status_code == 200


def test_upload_size_limit_from_config(client, methodist, monkeypatch):
    monkeypatch.setitem(demo_cfg()["admin"]["import"], "max_bytes", 200)
    body = {"name": "большой", "vacancies": [{"id": "v1", "text": "Python " * 100}]}
    r = client.post("/api/admin/import/simple", json=body, headers=methodist.headers)
    assert r.status_code == 413 and r.json()["code"] == "import_too_large"
    r = client.post("/api/admin/import", json={"pairs": [], "briefs": [{"brief_id": "x", "text": "Python " * 100}]},
                    headers=methodist.headers)
    assert r.status_code == 413


def test_import_count_limits_from_config(client, methodist, monkeypatch):
    monkeypatch.setitem(demo_cfg()["admin"]["import"], "max_briefs", 1)
    briefs = [{"brief_id": f"b{i}", "text": "Python-разработчик, 200–250 тыс."} for i in range(2)]
    r = client.post("/api/admin/import", json={"briefs": briefs}, headers=methodist.headers)
    assert r.status_code == 400 and r.json()["code"] == "import_too_large" and "брифов" in r.json()["message"]
    monkeypatch.setitem(demo_cfg()["admin"]["simple"], "max_candidates", 1)
    cands = [{"id": f"c{i}", "spec": "qa", "grade": "junior"} for i in range(2)]
    r = client.post("/api/admin/import/simple", json={"candidates": cands}, headers=methodist.headers)
    assert r.status_code == 400 and r.json()["code"] == "import_too_large" and "Кандидаты" in r.json()["message"]
