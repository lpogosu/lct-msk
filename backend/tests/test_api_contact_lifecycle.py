"""Полный цикл приглашения через HTTP: два работодателя и кандидат (концепция §3.1 шаги 11–12, §3.2 шаги 8–10, §4.3).

«Да, если…» принято → контакты открыты → «Связались» → найм → «условия совпали?» через 10 дней;
«Нет» с причиной «зарплата» → пауза 30 дней с исключением при вилке от 1,15× прошлого верха.
"""

from __future__ import annotations

from sqlalchemy import select

from tests.test_api_contact_support import (
    INVITE,
    advance,
    assert_no_pii,
    invite,
    make_candidate,
    make_employer,
    open_card,
    reliability_kinds,
)


def test_full_lifecycle_two_employers(client, make_account, db_session, frozen_clock, mailer):
    e1 = make_employer(client, make_account, db_session, "severny-kod.ru")
    e2 = make_employer(
        client,
        make_account,
        db_session,
        "yuzhny-bit.ru",
        profile={
            "name": "АО «Южный бит»",
            "field": "Логистика",
            "description": "Разрабатываем систему управления складом и доставкой для сетей магазинов.",
            "contact_methods": {"phone": "+7 495 000-00-01"},
        },
    )
    cand = make_candidate(make_account, db_session, salary_min=230000)
    a1 = open_card(db_session, e1, cand)
    a2 = open_card(db_session, e2, cand)
    assert a1.pseudonym != a2.pseudonym or a1.ref != a2.ref  # свой псевдоним у каждой компании

    # --- E1 приглашает: письмо кандидату с вилкой в теме, уведомление в кабинете
    r = invite(client, e1, a1.ref)
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["sent"]) == 1 and body["rejected"] == []
    inv1 = body["sent"][0]
    assert inv1["status"] == "sent" and inv1["status_label"] == "Отправлено"
    assert inv1["pseudonym"] == a1.pseudonym and inv1["candidate_ref"] == a1.ref
    assert body["quota"]["used"] == 1 and body["quota"]["label"].startswith("осталось ")
    assert_no_pii(body, cand)
    mail = mailer.last_to(cand.acct.email)
    assert mail is not None and "220 000–280 000 ₽ на руки" in mail.subject and "Северный код" in mail.subject

    # --- кандидат: список (вилка первой строкой), карточка (GET отмечает «просмотрено»)
    r = client.get("/api/invitations/inbox", headers=cand.headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert r.json()["new_count"] == 1 and items[0]["salary"]["text"] == "220 000–280 000 ₽ на руки"
    assert items[0]["company"]["name"] == "ООО «Северный код»" and "Надёжность" in items[0]["company"]["reliability"]
    r = client.get(f"/api/invitations/inbox/{inv1['id']}", headers=cand.headers)
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["status"] == "viewed"
    assert set(detail["actions"]) >= {"accept", "decline", "counter"}
    assert detail["company"]["reliability"]["display"] == "Надёжность: мало данных"
    assert detail["terms"]["verified"] is True and len(detail["terms"]["sha256"]) == 64
    assert detail["salary_vs_min"] == "Верх вилки выше вашего минимума на 22%"
    assert detail["counter_limits"]["salary_max"] == 360000  # 1,3·280 000, шаг 5000
    salary_reason = next(x for x in detail["decline_reasons"] if x["code"] == "salary")
    assert salary_reason["suggest_counter"] and "Да, если" in salary_reason["hint"]

    # --- «Да, если…»: от 270 000 и удалёнка; комментарий с контактом отклоняется (DECISIONS 20)
    bad = client.post(
        f"/api/invitations/{inv1['id']}/counter",
        json={"salary_from": 270000, "format": "remote", "comment": "пишите в телеграм @anna_dev"},
        headers=cand.headers,
    )
    assert bad.status_code == 400 and "контакты" in bad.json()["message"]
    too_many = client.post(
        f"/api/invitations/{inv1['id']}/counter",
        json={"salary_from": 270000, "format": "remote", "start_date": "2026-11-01"},
        headers=cand.headers,
    )
    assert too_many.status_code == 400 and "Не больше 2 условий" in too_many.json()["message"]
    r = client.post(
        f"/api/invitations/{inv1['id']}/counter",
        json={"salary_from": 270000, "format": "remote", "comment": "Готова выйти через две недели"},
        headers=cand.headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "countered" and r.json()["counter"]["within_range"] is True

    # --- работодатель видит встречное первой колонкой с карточкой разницы; кандидат всё ещё слепой
    r = client.get("/api/invitations", headers=e1.headers)
    assert r.status_code == 200
    kb = r.json()
    assert kb["columns"][0]["status"] == "countered" and kb["columns"][0]["count"] == 1
    summary = kb["columns"][0]["items"][0]["counter"]["summary"]
    assert "от 270 000 ₽ (ваша вилка 220 000–280 000 ₽)" in summary and "в пределах вашей вилки" in summary
    r = client.get(f"/api/invitations/{inv1['id']}", headers=e1.headers)
    assert r.status_code == 200
    assert r.json()["candidate"]["view"] == "blind" and r.json()["contacts_open"] is False
    assert_no_pii(r.json(), cand)
    assert all("actor_id" not in e for e in r.json()["events"])

    # --- «Согласиться»: новые условия v2, автораскрытие, обратный отсчёт «свяжитесь до …»
    r = client.post(f"/api/invitations/{inv1['id']}/counter/accept", headers=e1.headers)
    assert r.status_code == 200, r.text
    emp_view = r.json()
    assert emp_view["status"] == "accepted" and emp_view["contacts_open"] is True
    assert [t["ver"] for t in emp_view["terms"]] == [1, 2]
    v2 = emp_view["terms"][1]
    assert v2["salary_from"] == 270000 and v2["format"] == "remote" and v2["author"] == "candidate" and v2["verified"]
    cv = emp_view["candidate"]
    assert cv["view"] == "disclosed" and cv["email"] == cand.email and cv["full_name"] == cand.full_name
    assert emp_view["timers"]["contact_due_text"] == "через 3 дня"
    assert "candidate_id" not in str(cv)
    # журнал согласий кандидата: отдельная запись contact_disclosure на эту компанию
    consents = client.get("/api/consents", headers=cand.headers).json()
    cd = [c for c in consents if c["purpose"] == "contact_disclosure" and c["active"]]
    assert len(cd) == 1 and cd[0]["scope"]["company_id"] == str(e1.company_id)
    # уведомления обеим сторонам
    notes = client.get("/api/notifications", headers=cand.headers).json()
    assert any("Контакт открыт" in n["message"] for n in notes["items"])
    assert notes["unread"]["total"] >= 2

    # --- «Связались» в срок → good; итог «Найм»; через 10 дней «условия совпали?»
    r = client.post(f"/api/invitations/{inv1['id']}/contacted", headers=e1.headers)
    assert r.status_code == 200 and r.json()["contacted"] is True
    r = client.post(f"/api/invitations/{inv1['id']}/outcome", json={"outcome": "hired"}, headers=e1.headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "closed" and r.json()["status_label"] == "Завершено"
    early = client.post(f"/api/invitations/{inv1['id']}/check", json={"answer": "match"}, headers=cand.headers)
    assert early.status_code == 409 and "станет доступна" in early.json()["message"]
    advance(days=10, hours=1)
    r = client.post(f"/api/invitations/{inv1['id']}/check", json={"answer": "match"}, headers=cand.headers)
    assert r.status_code == 200, r.text
    assert r.json()["outcome_check"] == "match"
    assert reliability_kinds(db_session, e1.company_id) == ["contacted_in_time", "outcome_reported", "terms_matched"]

    # --- E2: «Нет» с причиной «зарплата» → пауза 30 дней, раньше — только с вилкой от 1,15× прошлого верха
    r = invite(client, e2, a2.ref, salary_from=200000, salary_to=250000, contact_method="phone")
    assert r.status_code == 201, r.text
    inv2 = r.json()["sent"][0]
    client.get(f"/api/invitations/inbox/{inv2['id']}", headers=cand.headers)
    no_reason = client.post(f"/api/invitations/{inv2['id']}/decline", json={"reason": "nope"}, headers=cand.headers)
    assert no_reason.status_code == 400 and "Выберите причину отказа" in no_reason.json()["message"]
    r = client.post(
        f"/api/invitations/{inv2['id']}/decline",
        json={"reason": "salary", "comment": "Рассматриваю от 280 тысяч"},
        headers=cand.headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "declined"
    e2_view = client.get(f"/api/invitations/{inv2['id']}", headers=e2.headers).json()
    assert e2_view["decline_reason_label"] == "Зарплата" and e2_view["decline_comment"] == "Рассматриваю от 280 тысяч"
    assert_no_pii(e2_view, cand)
    again = invite(client, e2, a2.ref, salary_from=200000, salary_to=250000, contact_method="phone")
    assert again.status_code == 409 and "Пауза после отказа" in again.json()["message"]
    assert "287 500 ₽" in again.json()["message"]  # ⌈1,15 · 250 000⌉
    lifted = invite(client, e2, a2.ref, salary_from=290000, salary_to=400000, contact_method="phone")
    assert lifted.status_code == 201, lifted.text
    assert any("Пауза снята" in n for n in lifted.json()["notes"])
    kb2 = client.get("/api/invitations", headers=e2.headers).json()
    assert kb2["decline_summary"] == [{"reason": "salary", "label": "Зарплата", "count": 1}]
    assert "из-за зарплаты" in kb2["decline_hint"]


def test_decline_with_counter_hint_and_one_active_per_pair(client, make_account, db_session, frozen_clock):
    e1 = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    a = open_card(db_session, e1, cand)
    r = invite(client, e1, a.ref)
    assert r.status_code == 201
    dup = invite(client, e1, a.ref)
    assert dup.status_code == 409 and "уже есть активное приглашение" in dup.json()["message"]
    # Чужой кандидат (карточку не открывали) — понятный отказ
    other = make_candidate(make_account, db_session, n=2)
    from app.services.matching.privacy import get_alias

    alias = get_alias(db_session, e1.company_id, other.user_id)
    r = invite(client, e1, alias.ref)
    assert r.status_code == 409 and "Сначала откройте карточку" in r.json()["message"]
    # Чужое приглашение не видно: ни кандидату, ни другой компании (IDOR → 404)
    inv_id = client.get("/api/invitations", headers=e1.headers).json()["columns"][1]["items"][0]["id"]
    assert client.get(f"/api/invitations/inbox/{inv_id}", headers=other.headers).status_code == 404
    e2 = make_employer(client, make_account, db_session, "drugaya-firma.ru")
    assert client.get(f"/api/invitations/{inv_id}", headers=e2.headers).status_code == 404
    assert client.post(f"/api/invitations/{inv_id}/withdraw", json={"reason": "other"}, headers=e2.headers).status_code == 404
    # Роли: кандидат не может звать, работодатель — отвечать
    assert client.post("/api/invitations", json={**INVITE, "candidate": a.ref}, headers=cand.headers).status_code == 403
    assert client.post(f"/api/invitations/{inv_id}/accept", json={"consent": True}, headers=e1.headers).status_code == 403


def test_accept_requires_separate_consent_and_channel(client, make_account, db_session, frozen_clock):
    e1 = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    a = open_card(db_session, e1, cand)
    inv = invite(client, e1, a.ref).json()["sent"][0]
    client.get(f"/api/invitations/inbox/{inv['id']}", headers=cand.headers)
    r = client.post(f"/api/invitations/{inv['id']}/accept", json={"consent": False}, headers=cand.headers)
    assert r.status_code == 400 and "согласие" in r.json()["message"]
    r = client.post(
        f"/api/invitations/{inv['id']}/accept", json={"consent": True, "fields": ["full_name"]}, headers=cand.headers
    )
    assert r.status_code == 400 and "хотя бы один способ связи" in r.json()["message"]
    r = client.post(
        f"/api/invitations/{inv['id']}/accept",
        json={"consent": True, "fields": ["full_name", "telegram"]},
        headers=cand.headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "accepted" and r.json()["contacts_open"] is True
    view = client.get(f"/api/invitations/{inv['id']}", headers=e1.headers).json()["candidate"]
    # Раскрыто только разрешённое: Telegram и имя, но не email и не телефон
    assert view["telegram"] == cand.telegram and view["full_name"] == cand.full_name
    assert not view.get("email") and not view.get("phone")  # не разрешено — null (поле в схеме есть, значения нет)
    from app.models.contact import ContactDisclosure

    rows = list(db_session.scalars(select(ContactDisclosure).where(ContactDisclosure.candidate_id == cand.user_id)))
    assert len(rows) == 1 and rows[0].consent_id is not None and rows[0].revoked_at is None


def test_counter_declined_with_reason(client, make_account, db_session, frozen_clock):
    e1 = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    a = open_card(db_session, e1, cand)
    inv = invite(client, e1, a.ref).json()["sent"][0]
    client.get(f"/api/invitations/inbox/{inv['id']}", headers=cand.headers)
    r = client.post(f"/api/invitations/{inv['id']}/counter", json={"salary_from": 340000}, headers=cand.headers)
    assert r.status_code == 200 and r.json()["counter"]["within_range"] is False
    # Только один раунд «Да, если…»
    r2 = client.post(f"/api/invitations/{inv['id']}/counter", json={"format": "remote"}, headers=cand.headers)
    assert r2.status_code == 409
    no_reason = client.post(f"/api/invitations/{inv['id']}/counter/decline", json={"reason": "x"}, headers=e1.headers)
    assert no_reason.status_code == 400
    r = client.post(f"/api/invitations/{inv['id']}/counter/decline", json={"reason": "salary"}, headers=e1.headers)
    assert r.status_code == 200 and r.json()["status"] == "counter_declined"
    assert r.json()["status_label"] == "Не сошлись по условиям"
    notes = client.get("/api/notifications", headers=cand.headers).json()["items"]
    assert any("не приняла встречное условие: не можем предложить такую зарплату" in n["message"] for n in notes)
    again = invite(client, e1, a.ref)
    assert again.status_code == 409 and "несостоявшегося встречного" in again.json()["message"]
    assert "322 000 ₽" in again.json()["message"]  # ⌈1,15 · 280 000⌉
