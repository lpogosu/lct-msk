"""Регулярные задачи и проверочный набор через HTTP (концепция §3.1 шаг 14, §3.2 шаг 12, §5.4; DECISIONS 3).

Тип A — свой одноразовый экземпляр семейства, ключ только на сервере, автопроверка через family.check;
тип B — «предложи подход», оценка работодателя по рубрике 4 × 0–2. Лимиты кандидата: одно предложение в 7 дней,
≤ 3 активных, 3 пропуска подряд → пауза 30 дней; без задач компаний — тренировочная задача платформы.
"""

from __future__ import annotations

from sqlalchemy import select

from tests.test_api_contact_support import (
    advance,
    dump,
    make_candidate,
    make_employer,
    make_need,
    shortlist,
)

APPROACH_PROMPT = (
    "Сервис оплаты иногда получает один и тот же запрос дважды из-за повторов клиента. "
    "Опишите, как сделать обработку платежа идемпотентной: что храните, где проверяете, какие риски."
)


def confirm(db, cand, spec: str = "backend_python", grade: str = "junior") -> None:
    """Подтверждённая категория (модуль тестирования) — аудитория задач «специализация × грейд ±1»."""
    from app.models.testing import Qualification

    db.add(Qualification(candidate_id=cand.user_id, specialization=spec, status="CONFIRMED", grade=grade, theta=-0.6, se=0.3))
    db.flush()


def _key_of(db, assignment_id) -> object:
    from app.models.tasks import TaskAssignment
    from app.models.testing import ItemInstance
    from app.services.testing.keystore import decrypt_key

    a = db.get(TaskAssignment, assignment_id)
    return decrypt_key(db.get(ItemInstance, a.instance_id).key_enc)


def test_type_a_instance_autocheck_limits_and_pause(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    r = client.get("/api/tasks/catalog?specialization=backend_python&module=python_core", headers=emp.headers)
    assert r.status_code == 200 and r.json()
    fam = next(f for f in r.json() if f["family_id"] == "py.dict.basics")
    assert fam["module_name"] == "Python" and fam["minutes"] <= 15
    bad = client.post(
        "/api/tasks",
        json={"kind": "catalog", "family_id": "py.dict.basics", "specialization": "backend_python", "grade": "junior", "not_real_work_ack": False},
        headers=emp.headers,
    )
    assert bad.status_code == 400 and bad.json()["code"] == "not_real_work_ack_required"
    r = client.post(
        "/api/tasks",
        json={"kind": "catalog", "family_id": "py.dict.basics", "specialization": "backend_python", "grade": "junior", "not_real_work_ack": True, "tags": ["python", "нет-такого"]},
        headers=emp.headers,
    )
    assert r.status_code == 201, r.text
    task = r.json()
    assert task["tags"] == ["python"] and task["module"] == "python_core" and task["status"] == "active"

    cand = make_candidate(make_account, db_session)
    nobody = client.get("/api/tasks/me", headers=cand.headers).json()
    assert nobody["active"] == [] and "после подтверждения категории" in nobody["empty_text"]
    confirm(db_session, cand)
    me = client.get("/api/tasks/me", headers=cand.headers).json()
    assert len(me["active"]) == 1
    offer = me["active"][0]
    assert offer["origin"]["kind"] == "company" and offer["origin"]["label"] == "ООО «Северный код»"
    assert offer["kind_label"] == "Задача из каталога" and "Python" in offer["effect"]
    assert offer["not_real_work_label"].startswith("Это не рабочая задача")

    r = client.post(f"/api/tasks/assignments/{offer['id']}/submit", json={"answer": "x"}, headers=cand.headers)
    assert r.status_code == 409 and "Сначала откройте задачу" in r.json()["message"]
    r = client.post(f"/api/tasks/assignments/{offer['id']}/start", headers=cand.headers)
    assert r.status_code == 200, r.text
    started = r.json()
    assert started["status"] == "opened" and started["item"]["prompt"] and started["item"]["variant_code"]
    key = _key_of(db_session, offer["id"])
    assert "key" not in started["item"] and dump(key) not in dump(started["item"].get("options") or [])
    # Свой экземпляр: повторное открытие возвращает тот же вариант, а не новый
    again = client.post(f"/api/tasks/assignments/{offer['id']}/start", headers=cand.headers).json()
    assert again["item"]["variant_code"] == started["item"]["variant_code"]

    r = client.post(f"/api/tasks/assignments/{offer['id']}/submit", json={"answer": key}, headers=cand.headers)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["status"] == "auto_scored" and res["outcome"]["correct"] is True and res["result"]["score"] == 1.0
    from app.models.tasks import TaskAssignment

    row = db_session.get(TaskAssignment, offer["id"])
    assert (row.module, row.family_id, row.is_correct) == ("python_core", "py.dict.basics", True)
    assert row.finished_at is not None and row.submitted_at is not None  # это читает подбор (свежесть A, K)
    from app.models.testing import ItemInstance

    inst = db_session.get(ItemInstance, row.instance_id)
    assert inst.seed < 0 and inst.issued_to_user_id == cand.user_id  # вне пула теста

    subs = client.get(f"/api/tasks/{task['id']}/submissions", headers=emp.headers).json()
    assert len(subs) == 1 and subs[0]["score_text"] == "Верно" and subs[0]["pseudonym"].startswith("К-")
    assert str(cand.user_id) not in dump(subs)
    tasks = client.get("/api/tasks", headers=emp.headers).json()
    assert tasks[0]["stats"]["submitted"] == 1 and tasks[0]["stats"]["avg_score"] == 1.0

    # Не чаще одного предложения в 7 дней; потом — тренировочная задача платформы (задач компаний больше нет)
    me = client.get("/api/tasks/me", headers=cand.headers).json()
    assert me["active"] == [] and me["next_offer_text"] == "через 7 дней"
    for week in range(3):
        advance(days=7, hours=1)
        me = client.get("/api/tasks/me", headers=cand.headers).json()
        assert len(me["active"]) == 1, (week, me)
        offer = me["active"][0]
        assert offer["origin"]["kind"] == "platform" and offer["origin"]["label"] == "Тренировочная задача от платформы"
        r = client.post(f"/api/tasks/assignments/{offer['id']}/skip", headers=cand.headers)
        assert r.status_code == 200 and r.json()["status"] == "skipped"
    # 3 пропуска подряд → пауза 30 дней, без штрафа
    assert r.json()["outcome"]["paused_until_text"] == "через 30 дней"
    advance(days=7, hours=1)
    me = client.get("/api/tasks/me", headers=cand.headers).json()
    assert me["active"] == [] and me["paused_until_text"] and "на паузе" in me["empty_text"]


def test_type_a_timeout_and_wrong_answer(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    client.post(
        "/api/tasks",
        json={"kind": "catalog", "family_id": "py.slicing.string", "specialization": "backend_python", "grade": "junior", "not_real_work_ack": True},
        headers=emp.headers,
    )
    cand = make_candidate(make_account, db_session)
    confirm(db_session, cand)
    offer = client.get("/api/tasks/me", headers=cand.headers).json()["active"][0]
    client.post(f"/api/tasks/assignments/{offer['id']}/start", headers=cand.headers)
    key = _key_of(db_session, offer["id"])
    advance(hours=1)  # серверный таймер: 2 минуты на задачу
    r = client.post(f"/api/tasks/assignments/{offer['id']}/submit", json={"answer": key}, headers=cand.headers)
    assert r.status_code == 200
    assert r.json()["outcome"]["timed_out"] is True and r.json()["outcome"]["correct"] is False
    assert r.json()["result"]["score"] == 0.0


def test_type_b_review_and_screening(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    r = client.post(
        "/api/tasks",
        json={"kind": "approach", "title": "Идемпотентность", "prompt": APPROACH_PROMPT + " Пишите на hr@severny-kod.ru", "specialization": "backend_python", "grade": "middle", "not_real_work_ack": True},
        headers=emp.headers,
    )
    assert r.status_code == 400 and r.json()["code"] == "task_text_caution"
    r = client.post(
        "/api/tasks",
        json={"kind": "approach", "title": "Идемпотентность платежей", "prompt": APPROACH_PROMPT, "specialization": "backend_python", "grade": "middle", "not_real_work_ack": True},
        headers=emp.headers,
    )
    assert r.status_code == 201, r.text
    task = r.json()
    assert len(task["rubric"]["criteria"]) == 4 and task["rubric"]["max_points"] == 2 and task["minutes"] == 15

    cand = make_candidate(make_account, db_session)
    confirm(db_session, cand, grade="junior")  # middle ±1 → junior подходит
    offer = client.get("/api/tasks/me", headers=cand.headers).json()["active"][0]
    assert offer["kind"] == "approach" and offer["kind_label"] == "Предложи подход"
    started = client.post(f"/api/tasks/assignments/{offer['id']}/start", headers=cand.headers).json()
    assert started["prompt"] == APPROACH_PROMPT and set(started["rubric"]["criteria"]) == {"problem", "approach", "tradeoffs", "clarity"}
    r = client.post(
        f"/api/tasks/assignments/{offer['id']}/submit",
        json={"answer": "Ключ идемпотентности в заголовке, уникальный индекс. Мой телеграм @anna_dev"},
        headers=cand.headers,
    )
    assert r.status_code == 400 and r.json()["code"] == "answer_contacts"
    answer = "Клиент передаёт ключ идемпотентности; сервер хранит ключ и результат в таблице с уникальным индексом."
    r = client.post(f"/api/tasks/assignments/{offer['id']}/submit", json={"answer": answer}, headers=cand.headers)
    assert r.status_code == 200 and r.json()["status"] == "awaiting_review"
    notes = client.get("/api/notifications?kind=task", headers=emp.headers).json()["items"]
    assert any("оцените по рубрике" in n["message"] for n in notes) and str(cand.user_id) not in dump(notes)

    subs = client.get(f"/api/tasks/{task['id']}/submissions", headers=emp.headers).json()
    assert subs[0]["answer"] == answer and subs[0]["can_review"] is True
    aid = subs[0]["assignment_id"]
    r = client.post(f"/api/tasks/assignments/{aid}/review", json={"scores": {"problem": 2}}, headers=emp.headers)
    assert r.status_code == 400 and r.json()["code"] == "rubric_incomplete"
    r = client.post(
        f"/api/tasks/assignments/{aid}/review",
        json={"scores": {"problem": 2, "approach": 3, "tradeoffs": 1, "clarity": 1}},
        headers=emp.headers,
    )
    assert r.status_code == 400 and r.json()["code"] == "rubric_value"
    r = client.post(
        f"/api/tasks/assignments/{aid}/review",
        json={"scores": {"problem": 2, "approach": 1, "tradeoffs": 2, "clarity": 1}, "comment": "Хорошо про индекс"},
        headers=emp.headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "reviewed" and r.json()["score"] == 0.75 and r.json()["score_text"] == "6 из 8"
    hist = client.get("/api/tasks/me", headers=cand.headers).json()["history"][0]
    assert hist["result"]["score_text"] == "6 из 8" and hist["result"]["review_comment"] == "Хорошо про индекс"
    again = client.post(f"/api/tasks/assignments/{aid}/review", json={"scores": {"problem": 2, "approach": 1, "tradeoffs": 2, "clarity": 1}}, headers=emp.headers)
    assert again.status_code == 409
    # Чужая компания не видит решений и не оценивает их
    other = make_employer(client, make_account, db_session, "drugaya-firma.ru")
    assert client.get(f"/api/tasks/{task['id']}/submissions", headers=other.headers).status_code == 404
    assert client.post(f"/api/tasks/assignments/{aid}/review", json={"scores": {}}, headers=other.headers).status_code == 404


def test_employer_active_limit(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    body = {"kind": "approach", "title": "Подход", "prompt": APPROACH_PROMPT, "specialization": "backend_python", "grade": "middle", "not_real_work_ack": True}
    ids = [client.post("/api/tasks", json=body, headers=emp.headers).json()["id"] for _ in range(5)]
    r = client.post("/api/tasks", json=body, headers=emp.headers)
    assert r.status_code == 409 and r.json()["code"] == "tasks_active_limit"
    assert client.post(f"/api/tasks/{ids[0]}/pause", headers=emp.headers).json()["status"] == "paused"
    assert client.post("/api/tasks", json=body, headers=emp.headers).status_code == 201
    r = client.post(f"/api/tasks/{ids[0]}/activate", headers=emp.headers)
    assert r.status_code == 409


def test_check_set_for_shortlist(client, make_account, db_session, frozen_clock):
    from app.models.tasks import TaskAssignment
    from app.services.matching.privacy import get_alias

    emp = make_employer(client, make_account, db_session)
    need = make_need(db_session, emp, must=("python", "postgresql"))
    c1 = make_candidate(make_account, db_session)
    c2 = make_candidate(make_account, db_session, n=2)
    shortlist(db_session, need, c1)
    ref1 = get_alias(db_session, emp.company_id, c1.user_id).ref
    ref2 = get_alias(db_session, emp.company_id, c2.user_id).ref

    r = client.post("/api/tasks/check-sets", json={"need_id": str(need.id), "candidates": [ref1, ref2]}, headers=emp.headers)
    assert r.status_code == 201, r.text
    cs = r.json()
    assert 1 <= len(cs["slots"]) <= 5 and cs["total_minutes"] <= 15
    assert {s["module"] for s in cs["slots"]} <= {"python_core", "sql"}
    assert cs["sent"][0]["assignments"] == len(cs["slots"]) and cs["sent"][0]["ref"] == ref1
    assert cs["rejected"][0]["ref"] == ref2 and "шортлиста" in cs["rejected"][0]["message"]
    again = client.post("/api/tasks/check-sets", json={"need_id": str(need.id), "candidates": [ref1]}, headers=emp.headers).json()
    assert again["sent"] == [] and "уже отправлен" in again["rejected"][0]["message"]
    # Набор не в общем списке задач и не занимает лимит «≤ 5 активных»
    assert client.get("/api/tasks", headers=emp.headers).json() == []

    me = client.get("/api/tasks/me", headers=c1.headers).json()  # без подтверждённой категории — набор всё равно пришёл
    assert len(me["active"]) == len(cs["slots"]) and all(a["origin"]["kind"] == "check_set" for a in me["active"])
    assert me["active"][0]["origin"]["label"].startswith("Проверочный набор по вакансии")
    first = me["active"][0]
    client.post(f"/api/tasks/assignments/{first['id']}/start", headers=c1.headers)
    r = client.post(f"/api/tasks/assignments/{first['id']}/submit", json={"answer": _key_of(db_session, first["id"])}, headers=c1.headers)
    assert r.status_code == 200 and r.json()["outcome"]["correct"] is True

    res = client.get(f"/api/tasks/check-sets/{need.id}", headers=emp.headers).json()
    row = res["candidates"][0]
    assert row["candidate_ref"] == ref1 and row["done"] == 1 and row["correct"] == 1 and row["total"] == len(cs["slots"])
    assert str(c1.user_id) not in dump(res)
    stored = list(db_session.scalars(select(TaskAssignment).where(TaskAssignment.candidate_id == c1.user_id)))
    assert {a.source for a in stored} == {"check_set"} and {a.need_id for a in stored} == {need.id}
    done = [a for a in stored if a.status == "auto_scored"]
    assert len(done) == 1 and done[0].module in {"python_core", "sql"} and done[0].finished_at is not None

    other = make_employer(client, make_account, db_session, "drugaya-firma.ru")
    assert client.get(f"/api/tasks/check-sets/{need.id}", headers=other.headers).status_code == 404
