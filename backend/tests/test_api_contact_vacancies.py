"""Вакансии и отклики через HTTP [доп.] (концепция §3.1 шаг 13, §3.2 шаг 11; spec-contact §2.3).

Список вакансий для кандидата сортируется тем же Score (признаки — контракт подбора build_candidate_features,
в тесте подменён); отклик с отдельным согласием сразу открывает контакты; 7 дней без ответа — no_response.
"""

from __future__ import annotations

import pytest

from tests.test_api_contact_support import (
    advance,
    assert_no_pii,
    make_candidate,
    make_employer,
    make_need,
    reliability_kinds,
)


@pytest.fixture
def features(monkeypatch):
    """Признаки кандидата для Score: Backend·Middle с подтверждённым тестом (как отдаёт сервис подбора)."""
    import app.services.contact.vacancies as vac_svc
    from app.matching.scoring import CandidateFeatures, ModuleScore

    def fake(db, user_id):
        return [
            CandidateFeatures(
                candidate_id=str(user_id),
                spec="backend_python",
                grade="middle",
                theta=0.6,
                se=0.3,
                modules={"python_core": ModuleScore(0.8, 0.3, "confirmed"), "sql": ModuleScore(0.5, 0.4, "confirmed")},
                salary_min=230000,
                formats=frozenset({"hybrid", "remote"}),
            )
        ]

    monkeypatch.setattr(vac_svc, "candidate_features", fake)
    return fake


def _publish(client, emp, need, **over):
    r = client.post("/api/vacancies", json={"need_id": str(need.id), **over}, headers=emp.headers)
    assert r.status_code == 201, r.text
    return r.json()["vacancy"]


def test_vacancy_list_sorted_by_score_and_apply(client, make_account, db_session, frozen_clock, features):
    e1 = make_employer(client, make_account, db_session)
    e2 = make_employer(
        client,
        make_account,
        db_session,
        "front-studio.ru",
        profile={
            "name": "ООО «Фронт-студия»",
            "field": "Веб-разработка",
            "description": "Делаем интерфейсы интернет-магазинов и личных кабинетов для банков.",
            "contact_methods": {"email": "jobs@front-studio.ru"},
        },
    )
    backend = _publish(client, e1, make_need(db_session, e1, must=("python", "postgresql")))
    frontend = _publish(
        client,
        e2,
        make_need(db_session, e2, spec="frontend_js", grade="middle", must=("react",), title="Frontend Middle"),
    )
    assert backend["salary"]["text"] == "220 000–280 000 ₽ на руки"
    dup = client.post("/api/vacancies", json={"need_id": backend["need_id"]}, headers=e1.headers)
    assert dup.status_code == 409 and dup.json()["code"] == "vacancy_exists"

    cand = make_candidate(make_account, db_session)
    r = client.get("/api/vacancies?only_matching=false", headers=cand.headers)
    assert r.status_code == 200, r.text
    items = r.json()
    assert [i["id"] for i in items] == [backend["id"], frontend["id"]]  # тот же Score, что у работодателя
    assert items[0]["score"] > items[1]["score"] and items[0]["company"]["name"] == "ООО «Северный код»"
    assert items[0]["category"] and sum(items[0]["parts"].values()) == pytest.approx(items[0]["score"], abs=0.2)

    detail = client.get(f"/api/vacancies/{backend['id']}", headers=cand.headers).json()
    assert detail["company"]["reliability"]["display"] == "Надёжность: мало данных"
    assert detail["consent"]["text_version"]

    # Отклик без отдельной галочки согласия не уходит
    r = client.post("/api/applications", json={"vacancy_id": backend["id"], "consent": False}, headers=cand.headers)
    assert r.status_code == 400 and "согласие" in r.json()["message"]
    r = client.post("/api/applications", json={"vacancy_id": backend["id"], "consent": True}, headers=cand.headers)
    assert r.status_code == 201, r.text
    app = r.json()
    assert app["status"] == "submitted" and app["contacts_shared"] is True and app["score"] == items[0]["score"]
    again = client.post("/api/applications", json={"vacancy_id": backend["id"], "consent": True}, headers=cand.headers)
    assert again.status_code == 409 and again.json()["code"] == "application_exists"

    # Работодатель: отклики по Score, контакты видны сразу (кандидат откликнулся сам), id кандидата — нет
    lst = client.get("/api/applications", headers=e1.headers).json()
    assert len(lst) == 1 and lst[0]["contacts_visible"] is True and lst[0]["timers"]["sla_due_text"] == "через 7 дней"
    assert str(cand.user_id) not in str(lst)
    det = client.get(f"/api/applications/{app['id']}", headers=e1.headers).json()
    assert det["status"] == "viewed" and det["candidate"]["email"] == cand.email
    assert str(cand.user_id) not in str(det)
    notes = client.get("/api/notifications?kind=application", headers=e1.headers).json()["items"]
    assert any("Новый отклик" in n["message"] for n in notes)
    assert_no_pii(notes, cand)

    # Отказ с причиной → кандидату причина и ссылка на задачу по модулю теста; ответ в срок — good
    r = client.post(
        f"/api/applications/{app['id']}/reject",
        json={"reason": "skills_missing", "skill_ids": ["postgresql"], "comment": "Нужен опыт с индексами"},
        headers=e1.headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    mine = client.get(f"/api/applications/mine/{app['id']}", headers=cand.headers).json()
    assert mine["reject_reason_label"] == "Не хватило навыков из вакансии"
    assert mine["feedback"][0]["links"][0]["kind"] == "task"
    assert reliability_kinds(db_session, e1.company_id) == ["application_answered"]
    # После отказа кандидат может отозвать согласие — контакты у компании снова скрыты
    r = client.post(f"/api/applications/{app['id']}/revoke-consent", headers=cand.headers)
    assert r.status_code == 200 and r.json()["contacts_shared"] is False
    det = client.get(f"/api/applications/{app['id']}", headers=e1.headers).json()
    assert det["contacts_visible"] is False
    assert_no_pii(det["candidate"], cand)
    assert not any(v in str(det) for v in cand.pii())


def test_application_no_response_and_withdraw(client, make_account, db_session, frozen_clock, features):
    emp = make_employer(client, make_account, db_session)
    vac = _publish(client, emp, make_need(db_session, emp))
    c1 = make_candidate(make_account, db_session)
    c2 = make_candidate(make_account, db_session, n=2)
    a1 = client.post("/api/applications", json={"vacancy_id": vac["id"], "consent": True}, headers=c1.headers).json()
    a2 = client.post("/api/applications", json={"vacancy_id": vac["id"], "consent": True}, headers=c2.headers).json()
    # Кандидат передумал — отклик отозван, контакты скрыты
    r = client.post(f"/api/applications/{a2['id']}/withdraw", headers=c2.headers)
    assert r.status_code == 200 and r.json()["status"] == "withdrawn" and r.json()["contacts_shared"] is False
    det2 = client.get(f"/api/applications/{a2['id']}", headers=emp.headers)
    assert det2.status_code == 200 and det2.json()["contacts_visible"] is False
    assert_no_pii(det2.json()["candidate"], c2)
    assert not any(v in str(det2.json()) for v in c2.pii())
    # Ответить, не открыв отклик, нельзя
    r = client.post(f"/api/applications/{a1['id']}/interest", headers=emp.headers)
    assert r.status_code == 409
    advance(days=7, hours=1)
    mine = client.get("/api/applications/mine", headers=c1.headers).json()
    assert mine[0]["status"] == "no_response" and mine[0]["status_label"] == "Нет ответа"
    assert reliability_kinds(db_session, emp.company_id) == ["application_no_response"]
    notes = client.get("/api/notifications", headers=c1.headers).json()["items"]
    assert any("не ответила на отклик" in n["message"] for n in notes)
    # Снятая вакансия: новые отклики не принимаются
    client.post(f"/api/vacancies/{vac['id']}/close", headers=emp.headers)
    c3 = make_candidate(make_account, db_session, n=3)
    r = client.post("/api/applications", json={"vacancy_id": vac["id"], "consent": True}, headers=c3.headers)
    assert r.status_code == 404


def test_vacancy_validation_and_ownership(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    other = make_employer(client, make_account, db_session, "drugaya-firma.ru")
    need = make_need(db_session, emp)
    r = client.post("/api/vacancies", json={"need_id": str(need.id), "salary_to": 600000}, headers=emp.headers)
    assert r.status_code == 400 and r.json()["code"] == "salary_invalid"
    r = client.post("/api/vacancies", json={"need_id": str(need.id)}, headers=other.headers)
    assert r.status_code == 404
    vac = _publish(client, emp, need, salary_from=230000, salary_to=380000)
    r = client.patch(f"/api/vacancies/{vac['id']}", json={"title": "Python-разработчик"}, headers=emp.headers)
    assert r.status_code == 200 and r.json()["vacancy"]["title"] == "Python-разработчик"
    assert any("Широкая вилка" in w for w in r.json()["warnings"])
    assert client.patch(f"/api/vacancies/{vac['id']}", json={"title": "x"}, headers=other.headers).status_code == 404
    mine = client.get("/api/vacancies/mine", headers=emp.headers).json()
    assert len(mine) == 1 and mine[0]["applications_total"] == 0


def test_direct_vacancy_without_brief_creates_need_and_shows_team(client, make_account, db_session, frozen_clock, features):
    """X-04 и E-02: вакансия без брифа создаёт служебный бриф; «о команде» видна кандидату, подбор по брифу работает."""
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    # без обязательных полей — понятная ошибка
    r = client.post("/api/vacancies", json={"title": "Backend"}, headers=emp.headers)
    assert r.status_code == 400 and r.json()["code"] == "vacancy_fields_required"
    team = {"size": 6, "role": "второй backend", "stack_context": "Python, FastAPI, PostgreSQL", "about": "Платёжный сервис"}
    r = client.post(
        "/api/vacancies",
        json={
            "title": "Backend-разработчик в платежи",
            "specialization": "backend_python",
            "grade": "middle",
            "salary_from": 220000,
            "salary_to": 280000,
            "salary_type": "net",
            "format": "hybrid",
            "city": "moscow",
            "must": ["python", "postgresql"],
            "nice": ["kafka"],
            "description": "Развиваем платёжный сервис.",
            "team": {**team, "junk": "x"},
        },
        headers=emp.headers,
    )
    assert r.status_code == 201, r.text
    vac = r.json()["vacancy"]
    assert vac["need_id"] and vac["title"] == "Backend-разработчик в платежи"
    need = client.get(f"/api/needs/{vac['need_id']}", headers=emp.headers).json()
    assert need["brief"]["spec"] == "backend_python" and need["brief"]["grade"] == "middle"
    assert need["brief"]["must"] == ["python", "postgresql"] and need["team"] == team
    # кандидат видит «о команде» в вакансии
    d = client.get(f"/api/vacancies/{vac['id']}", headers=cand.headers).json()
    assert d["team"] == team
    # подбор по этому брифу строится (бриф полноценный)
    v = client.post(f"/api/needs/{vac['need_id']}/versions", json={}, headers=emp.headers)
    assert v.status_code in (200, 201), v.text
    # правка «о команде» брифа не создаёт новую версию подборки и не меняет Score-поля
    p = client.patch(f"/api/needs/{vac['need_id']}", json={"team": {"size": 8}}, headers=emp.headers)
    assert p.status_code == 200 and p.json()["need"]["team"] == {"size": 8} and p.json()["version"] is None
