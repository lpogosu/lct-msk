"""Интеграция с ATS: API-ключи и области доступа, pull-API (курсор, ПДн только при раскрытии), обратная синхронизация
статусов через нашу машину состояний, идемпотентность, Хантфлоу-адаптер (эмулятор и реальный режим с подменой сети),
вебхук без изменений."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import clock
from app.models.integration import AtsLog, CompanyApiKey
from app.services.ats import huntflow
from app.services.contact import webhooks
from tests.test_api_contact_support import (
    INVITE,
    assert_no_pii,
    invite,
    make_candidate,
    make_employer,
    make_need,
    open_card,
    reliability_kinds,
)

ALL_SCOPES = ["vacancies:read", "invitations:read", "applications:read", "contacts:read", "status:write"]


def _key(client, emp, scopes=None, name="Хантфлоу, прод"):
    r = client.post("/api/companies/me/ats/keys", json={"name": name, "scopes": scopes or ALL_SCOPES}, headers=emp.headers)
    assert r.status_code == 201, r.text
    return r.json()


def _h(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _sent(client, db, make_account, emp, n=1, **over):
    """n приглашённых кандидатов → [(cand, invitation_id)]."""
    out = []
    for i in range(n):
        cand = make_candidate(make_account, db, n=i + 1)
        alias = open_card(db, emp, cand)
        r = invite(client, emp, alias.ref, **over)
        assert r.status_code == 201, r.text
        out.append((cand, r.json()["sent"][0]["id"]))
    return out


def _accept(client, cand, inv_id):
    client.get(f"/api/invitations/inbox/{inv_id}", headers=cand.headers)
    r = client.post(f"/api/invitations/{inv_id}/accept", json={"consent": True}, headers=cand.headers)
    assert r.status_code == 200, r.text


# ----------------------------------------------------------------------------------------------- ключи


def test_api_keys_hashed_scopes_rotate_revoke(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    issued = _key(client, emp, ["vacancies:read"])
    plain = issued["key"]
    assert plain.startswith("vzk_") and issued["key_id"] in plain
    row = db_session.scalar(select(CompanyApiKey).where(CompanyApiKey.key_id == issued["key_id"]))
    assert plain.split("_", 2)[2] not in (row.key_hash or "") and len(row.key_hash) == 64  # в БД только хеш
    listed = client.get("/api/companies/me/ats/keys", headers=emp.headers).json()
    assert listed[0]["key_id"] == issued["key_id"] and "key" not in listed[0]

    assert client.get("/api/ats/v1/me", headers=_h(plain)).status_code == 200
    # область: список приглашений без invitations:read — 403
    r = client.get("/api/ats/v1/invitations", headers=_h(plain))
    assert r.status_code == 403 and r.json()["code"] == "ats_scope_missing"
    # без ключа / с чужим / с мусором — одинаковый 401
    for bad in ({}, _h("vzk_deadbeef_x"), _h("Basic abc"), {"Authorization": "Bearer " + plain[:-3] + "xyz"}):
        assert client.get("/api/ats/v1/me", headers=bad).status_code == 401
    # JWT работодателя не заменяет ключ
    assert client.get("/api/ats/v1/me", headers=emp.headers).status_code == 401

    # проверки при создании
    assert client.post("/api/companies/me/ats/keys", json={"name": "x", "scopes": []}, headers=emp.headers).status_code == 400
    assert client.post("/api/companies/me/ats/keys", json={"name": "x", "scopes": ["root"]}, headers=emp.headers).status_code == 400
    cand = make_candidate(make_account, db_session)
    assert client.get("/api/companies/me/ats/keys", headers=cand.headers).status_code == 403

    # перевыпуск: старый ключ умирает сразу, новый наследует области
    new = client.post(f"/api/companies/me/ats/keys/{issued['id']}/rotate", headers=emp.headers).json()
    assert new["scopes"] == ["vacancies:read"] and new["key"] != plain
    assert client.get("/api/ats/v1/me", headers=_h(plain)).status_code == 401
    assert client.get("/api/ats/v1/me", headers=_h(new["key"])).status_code == 200
    assert len(client.get("/api/companies/me/ats/keys", headers=emp.headers).json()) == 1
    # чужая компания не отзовёт ключ
    other = make_employer(client, make_account, db_session, "drugaya-firma.ru")
    assert client.delete(f"/api/companies/me/ats/keys/{new['id']}", headers=other.headers).status_code == 404
    assert client.delete(f"/api/companies/me/ats/keys/{new['id']}", headers=emp.headers).status_code == 200
    assert client.get("/api/ats/v1/me", headers=_h(new["key"])).status_code == 401


def test_api_keys_limit(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    for i in range(5):
        _key(client, emp, name=f"k{i}")
    r = client.post("/api/companies/me/ats/keys", json={"name": "лишний", "scopes": ["vacancies:read"]}, headers=emp.headers)
    assert r.status_code == 409 and r.json()["code"] == "ats_keys_limit"


# --------------------------------------------------------------------------------------- pull и ПДн


def test_pull_pagination_cursor_and_updated_since(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    rows = _sent(client, db_session, make_account, emp, n=5)
    key = _key(client, emp)["key"]
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        r = client.get("/api/ats/v1/invitations", params=params, headers=_h(key))
        assert r.status_code == 200, r.text
        body = r.json()
        seen += [i["id"] for i in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == 3 and sorted(seen) == sorted(i for _, i in rows) and len(set(seen)) == 5
    # повтор с тем же курсором — те же записи (чтение идемпотентно)
    first = client.get("/api/ats/v1/invitations", params={"limit": 2}, headers=_h(key)).json()
    again = client.get("/api/ats/v1/invitations", params={"limit": 2, "cursor": first["next_cursor"]}, headers=_h(key)).json()
    again2 = client.get("/api/ats/v1/invitations", params={"limit": 2, "cursor": first["next_cursor"]}, headers=_h(key)).json()
    assert [i["id"] for i in again["items"]] == [i["id"] for i in again2["items"]]
    assert client.get("/api/ats/v1/invitations", params={"cursor": "мусор"}, headers=_h(key)).json()["code"] == "ats_cursor_invalid"

    # updated_since: после ответа кандидата меняется только одна запись
    clock.advance(timedelta(hours=1).total_seconds())
    since = clock.now().isoformat()
    cand, inv_id = rows[2]
    _accept(client, cand, inv_id)
    changed = client.get("/api/ats/v1/invitations", params={"updated_since": since}, headers=_h(key)).json()
    assert [i["id"] for i in changed["items"]] == [inv_id] and changed["items"][0]["status"] == "accepted"
    only = client.get("/api/ats/v1/invitations", params={"status": "sent"}, headers=_h(key)).json()
    assert len(only["items"]) == 4
    # чужая компания не видит наших приглашений
    other = make_employer(client, make_account, db_session, "drugaya-firma.ru")
    okey = _key(client, other)["key"]
    assert client.get("/api/ats/v1/invitations", headers=_h(okey)).json()["items"] == []
    assert client.get(f"/api/ats/v1/invitations/{inv_id}", headers=_h(okey)).status_code == 404


def test_pull_contacts_only_for_disclosed_pairs_with_scope(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    (cand, inv_id), (cand2, inv2) = _sent(client, db_session, make_account, emp, n=2)
    full = _key(client, emp)["key"]
    no_contacts = _key(client, emp, ["invitations:read", "vacancies:read"], name="без контактов")["key"]

    r = client.get("/api/ats/v1/invitations", headers=_h(full)).json()
    assert_no_pii(r, cand)  # до раскрытия — ни контактов, ни внутренних id, даже с contacts:read
    assert_no_pii(r, cand2)
    assert all(i["candidate"]["contacts_open"] is False and i["candidate"].get("contacts") is None for i in r["items"])

    _accept(client, cand, inv_id)
    r = client.get(f"/api/ats/v1/invitations/{inv_id}", headers=_h(full)).json()
    assert r["candidate"]["contacts_open"] is True
    assert r["candidate"]["contacts"]["email"] == cand.email and r["candidate"]["contacts"]["full_name"] == cand.full_name
    assert str(cand.user_id) not in json.dumps(r, ensure_ascii=False)
    # без области contacts:read — контактов нет, только факт раскрытия
    r = client.get(f"/api/ats/v1/invitations/{inv_id}", headers=_h(no_contacts)).json()
    assert r["candidate"]["contacts_open"] is True and r["candidate"].get("contacts") is None
    assert_no_pii(r, cand)
    # второй кандидат, не ответивший, по-прежнему слепой
    r = client.get(f"/api/ats/v1/invitations/{inv2}", headers=_h(full)).json()
    assert r["candidate"].get("contacts") is None
    # отзыв согласия: контакты пропадают, а запись «обновилась» для updated_since
    clock.advance(60)
    since = clock.now().isoformat()
    assert client.post(f"/api/invitations/{inv_id}/revoke-consent", headers=cand.headers).status_code == 200
    r = client.get("/api/ats/v1/invitations", params={"updated_since": since}, headers=_h(full)).json()
    assert [i["id"] for i in r["items"]] == [inv_id]
    assert r["items"][0]["status"] == "consent_revoked" and r["items"][0]["candidate"].get("contacts") is None
    assert_no_pii(r, cand)


def test_pull_vacancies_and_applications(client, make_account, db_session, frozen_clock, monkeypatch):
    import app.services.contact.vacancies as vac_svc
    from app.matching.scoring import CandidateFeatures, ModuleScore

    monkeypatch.setattr(
        vac_svc, "candidate_features",
        lambda db, uid: [CandidateFeatures(candidate_id=str(uid), spec="backend_python", grade="middle", theta=0.6, se=0.3,
                                           modules={"python_core": ModuleScore(0.8, 0.3, "confirmed")}, salary_min=230000,
                                           formats=frozenset({"hybrid"}))],
    )
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    need = make_need(db_session, emp, must=("python",))
    vac = client.post("/api/vacancies", json={"need_id": str(need.id)}, headers=emp.headers).json()["vacancy"]
    key = _key(client, emp)["key"]
    v = client.get("/api/ats/v1/vacancies", headers=_h(key)).json()
    assert [i["id"] for i in v["items"]] == [vac["id"]]
    assert v["items"][0]["salary"] == {"from": 220000, "to": 280000, "net": True, "currency": "RUB"}
    app = client.post("/api/applications", json={"vacancy_id": vac["id"], "consent": True}, headers=cand.headers).json()
    a = client.get("/api/ats/v1/applications", headers=_h(key)).json()
    assert [i["id"] for i in a["items"]] == [app["id"]]
    assert a["items"][0]["candidate"]["contacts_open"] is True
    assert a["items"][0]["candidate"]["contacts"]["email"] == cand.email  # отклик: контакты переданы при отклике
    assert str(cand.user_id) not in json.dumps(a)
    # статус из ATS: отклик → «интерес» (с автопросмотром), повтор — без изменений
    r = client.post(f"/api/ats/v1/applications/{app['id']}/status", json={"status": "interested"}, headers=_h(key))
    assert r.status_code == 200 and r.json()["applied"] is True and r.json()["item"]["status"] == "interested"
    r = client.post(f"/api/ats/v1/applications/{app['id']}/status", json={"status": "interested"}, headers=_h(key))
    assert r.json()["applied"] is False
    r = client.post(f"/api/ats/v1/applications/{app['id']}/status", json={"status": "rejected"}, headers=_h(key))
    assert r.status_code == 400 and r.json()["code"] == "ats_reason_required"
    r = client.post(f"/api/ats/v1/applications/{app['id']}/status", json={"status": "rejected", "reason": "chose_other"}, headers=_h(key))
    assert r.status_code == 409 and r.json()["message"]  # интерес уже выражен — отказать по нашей машине нельзя


# ------------------------------------------------------------------------------ статусы из ATS


def test_status_push_maps_to_state_machine(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    (cand, inv_id), (cand2, inv2) = _sent(client, db_session, make_account, emp, n=2)
    key = _key(client, emp)["key"]
    url = f"/api/ats/v1/invitations/{inv_id}/status"

    # до «да» кандидата статус принять нельзя: машина состояний говорит по-русски
    r = client.post(url, json={"status": "interview"}, headers=_h(key))
    assert r.status_code == 409 and r.json()["code"] == "contact_rejected" and r.json()["message"]
    assert client.post(url, json={"status": "волшебство"}, headers=_h(key)).json()["code"] == "ats_status_unknown"

    _accept(client, cand, inv_id)
    # найм без контакта — нельзя
    r = client.post(url, json={"status": "hired"}, headers=_h(key))
    assert r.status_code == 409 and "Связались" in r.json()["message"]
    r = client.post(url, json={"status": "offer"}, headers=_h(key))
    assert r.status_code == 409 and r.json()["details"]["reason"] == "offer_requires_contact"
    # интервью: отмечает и контакт (надёжность — как при ручной отметке)
    r = client.post(url, json={"status": "interview", "external_id": "hf-77"}, headers=_h(key))
    assert r.status_code == 200 and r.json()["applied"] is True and r.json()["item"]["stage"] == "interview"
    assert client.post(url, json={"status": "interview"}, headers=_h(key)).json()["applied"] is False
    assert client.post(url, json={"status": "contacted"}, headers=_h(key)).json()["applied"] is False
    assert reliability_kinds(db_session, emp.company_id) == ["contacted_in_time"]
    r = client.post(url, json={"status": "offer"}, headers=_h(key))
    assert r.json()["applied"] is True and r.json()["item"]["stage"] == "offer"
    # итог «нанят»
    r = client.post(url, json={"status": "hired"}, headers=_h(key))
    assert r.status_code == 200 and r.json()["item"]["status"] == "closed" and r.json()["item"]["outcome"] == "hired"
    assert "outcome_reported" in reliability_kinds(db_session, emp.company_id)
    # после закрытия — только 409 / повтор итога без изменений
    assert client.post(url, json={"status": "hired"}, headers=_h(key)).json()["applied"] is False
    r = client.post(url, json={"status": "rejected", "reason": "chose_other"}, headers=_h(key))
    assert r.status_code == 409
    # то же видит человек в кабинете
    assert client.get(f"/api/invitations/{inv_id}", headers=emp.headers).json()["status"] == "closed"

    # «отказ» требует причину из справочника; навыки — для skills_missing
    _accept(client, cand2, inv2)
    url2 = f"/api/ats/v1/invitations/{inv2}/status"
    assert client.post(url2, json={"status": "rejected"}, headers=_h(key)).json()["code"] == "ats_reason_required"
    assert client.post(url2, json={"status": "rejected", "reason": "skills_missing"}, headers=_h(key)).status_code == 400
    r = client.post(url2, json={"status": "rejected", "reason": "position_closed", "comment": "Позицию закрыли"}, headers=_h(key))
    assert r.status_code == 200 and r.json()["item"]["outcome"] == "not_matched"
    assert r.json()["item"]["outcome_reason"] == "position_closed"
    # журнал ATS: принятые и отклонённые обращения
    log = client.get("/api/companies/me/ats/log", headers=emp.headers).json()["items"]
    kinds = {(i["kind"], i["status"]) for i in log}
    assert ("status.interview", "applied") in kinds and ("status.hired", "rejected") in kinds and ("status.interview", "replayed") in kinds
    # без области status:write — 403; чужая компания — 404
    ro = _key(client, emp, ["invitations:read"], name="ro")["key"]
    assert client.post(url, json={"status": "hired"}, headers=_h(ro)).status_code == 403
    other = make_employer(client, make_account, db_session, "drugaya-firma.ru")
    okey = _key(client, other)["key"]
    assert client.post(url2, json={"status": "contacted"}, headers=_h(okey)).status_code == 404


def test_status_push_idempotency_key(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    [(cand, inv_id)] = _sent(client, db_session, make_account, emp)
    _accept(client, cand, inv_id)
    key = _key(client, emp)["key"]
    url = f"/api/ats/v1/invitations/{inv_id}/status"
    hdr = {**_h(key), "Idempotency-Key": "evt-1"}
    first = client.post(url, json={"status": "interview"}, headers=hdr)
    assert first.status_code == 200 and first.json()["applied"] is True and first.json()["replayed"] is False
    again = client.post(url, json={"status": "interview"}, headers=hdr)
    assert again.status_code == 200 and again.json()["replayed"] is True and again.json()["applied"] is True
    # тот же ключ для другого статуса — конфликт, а не молчаливая подмена
    clash = client.post(url, json={"status": "offer"}, headers=hdr)
    assert clash.status_code == 409 and clash.json()["code"] == "ats_idempotency_conflict"
    events = client.get(f"/api/invitations/{inv_id}", headers=emp.headers).json()["events"]
    assert sum(1 for e in events if e["action"] == "mark_interview") == 1


# ------------------------------------------------------------------------- настройки и вебхук


def test_ats_settings_token_encrypted_and_validated(client, make_account, db_session, frozen_clock):
    from app.models.integration import AtsSettings

    emp = make_employer(client, make_account, db_session)
    r = client.get("/api/companies/me/ats", headers=emp.headers).json()
    assert r["ats_type"] == "none" and r["mode"] == "none" and r["token_set"] is False
    assert r["ats_types"] == {"none": "Нет", "webhook": "Вебхук", "huntflow": "Хантфлоу"}
    assert client.put("/api/companies/me/ats", json={"ats_type": "sap"}, headers=emp.headers).status_code == 400
    # адрес во внутренней сети запрещён (SSRF)
    r = client.put("/api/companies/me/ats", json={"ats_type": "huntflow", "base_url": "http://localhost:9/v2"}, headers=emp.headers)
    assert r.status_code == 400 and r.json()["code"] == "ats_base_url_invalid"
    r = client.put("/api/companies/me/ats", json={"ats_type": "huntflow", "account_id": "abc"}, headers=emp.headers)
    assert r.status_code == 400
    # без токена — эмулятор
    r = client.put("/api/companies/me/ats", json={"ats_type": "huntflow", "account_id": "42"}, headers=emp.headers).json()
    assert r["mode"] == "emulator" and r["account_id"] == "42"
    r = client.put("/api/companies/me/ats", json={"ats_type": "huntflow", "token": "sekret-token-1234"}, headers=emp.headers).json()
    assert r["mode"] == "real" and r["token_set"] is True and r["token_hint"].endswith("1234") and "sekret" not in json.dumps(r)
    row = db_session.get(AtsSettings, emp.company_id)
    assert "sekret-token" not in (row.token_enc or "") and row.token_enc
    # не передан token — не меняется; пустая строка — стирается
    r = client.put("/api/companies/me/ats", json={"ats_type": "huntflow"}, headers=emp.headers).json()
    assert r["mode"] == "real"
    r = client.put("/api/companies/me/ats", json={"ats_type": "huntflow", "token": ""}, headers=emp.headers).json()
    assert r["mode"] == "emulator" and r["token_set"] is False
    cand = make_candidate(make_account, db_session)
    assert client.get("/api/companies/me/ats", headers=cand.headers).status_code == 403


def test_webhook_unchanged_alongside_huntflow(client, make_account, db_session, frozen_clock):
    sent: list[tuple[str, dict]] = []

    def transport(url, body, headers, timeout):
        sent.append((headers["X-Vzaimno-Event"], json.loads(body)))
        return 200, "ok"

    prev = webhooks.set_transport(transport)
    try:
        emp = make_employer(client, make_account, db_session)
        client.put("/api/companies/me", json={"webhook_url": "https://ats.example-hr.ru/hook"}, headers=emp.headers)
        client.put("/api/companies/me/ats", json={"ats_type": "huntflow"}, headers=emp.headers)
        [(cand, inv_id)] = _sent(client, db_session, make_account, emp)
        _accept(client, cand, inv_id)
    finally:
        webhooks.set_transport(prev)
    types = [t for t, _ in sent]
    assert "invitation.sent" in types and "invitation.accepted" in types
    accepted = next(b for t, b in sent if t == "invitation.accepted")
    assert accepted["data"]["contacts"]["email"] == cand.email  # контакты — только после «да»
    sent_evt = next(b for t, b in sent if t == "invitation.sent")
    assert "contacts" not in sent_evt["data"]


# ------------------------------------------------------------------------------------ Хантфлоу


def test_huntflow_payload_mapping_pure():
    assert huntflow.split_name("Иванов Пётр Сергеевич") == {"last_name": "Иванов", "first_name": "Пётр", "middle_name": "Сергеевич"}
    assert huntflow.split_name("Анна Тестова") == {"first_name": "Анна", "last_name": "Тестова"}
    assert huntflow.split_name(None) == {"first_name": "Кандидат"}
    assert huntflow.money_text(220000, 280000, True) == "220000-280000 руб. на руки"
    assert huntflow.money_text(220000, 280000, False).endswith("до вычета НДФЛ")
    body = huntflow.applicant_payload({"full_name": "Анна Тестова", "email": "a@b.ru", "telegram": "@anna"}, position="Backend", money=None, note="n")
    assert body["email"] == "a@b.ru" and body["social"] == [{"social_type": "TELEGRAM", "value": "anna"}]
    assert "phone" not in body and body["externals"][0]["data"]["body"] == "n"
    assert huntflow.attach_payload(vacancy_id=1, status_id=2, comment="c", rejection_reason=3) == {
        "vacancy": 1, "status": 2, "comment": "c", "rejection_reason": 3,
    }
    assert huntflow.vacancy_payload(title="T", company="C", description="D", money="1-2")["position"] == "T"
    assert huntflow.fake_id(__import__("uuid").UUID(int=1), "a", "b") == huntflow.fake_id(__import__("uuid").UUID(int=1), "a", "b")


def _log(db, company_id):
    db.flush()
    return list(db.scalars(select(AtsLog).where(AtsLog.company_id == company_id, AtsLog.direction == "out").order_by(AtsLog.id)))


def test_huntflow_emulator_records_exact_requests(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    client.put("/api/companies/me/ats", json={"ats_type": "huntflow"}, headers=emp.headers)
    check = client.post("/api/companies/me/ats/check", headers=emp.headers).json()
    assert check["ok"] is True and check["mode"] == "emulator" and "эмулятор" in check["message"].lower()
    [(cand, inv_id)] = _sent(client, db_session, make_account, emp)
    # до раскрытия кандидата в ATS нет: журнал содержит только проверку подключения
    assert [r.kind for r in _log(db_session, emp.company_id)] == ["check"]
    _accept(client, cand, inv_id)
    kinds = [r.kind for r in _log(db_session, emp.company_id)]
    assert kinds == ["check", "vacancy.create", "applicant.create", "applicant.stage.new"]
    rows = {r.kind: r for r in _log(db_session, emp.company_id)}
    ap = rows["applicant.create"]
    assert ap.mode == "emulator" and ap.status == "recorded" and ap.method == "POST"
    assert ap.url == "https://api.huntflow.ru/v2/accounts/1/applicants"
    assert ap.request["headers"]["Authorization"].startswith("Bearer <токен не задан")
    assert ap.request["body"]["email"] == cand.email and ap.request["body"]["position"] == INVITE["title"]
    assert ap.request["body"]["money"] == "220000-280000 руб. на руки"
    applicant_id, vacancy_id = ap.response["id"], rows["vacancy.create"].response["id"]
    st = rows["applicant.stage.new"]
    assert st.url == f"https://api.huntflow.ru/v2/accounts/1/applicants/{applicant_id}/vacancy"
    assert st.request["body"] == {"vacancy": vacancy_id, "status": 101}

    # связались → интервью → найм
    for path, _ in (("contacted", 0), ("interview", 0)):
        assert client.post(f"/api/invitations/{inv_id}/{path}", headers=emp.headers).status_code == 200
    assert client.post(f"/api/invitations/{inv_id}/outcome", json={"outcome": "hired"}, headers=emp.headers).status_code == 200
    moves = [r for r in _log(db_session, emp.company_id) if r.kind.startswith("applicant.stage.")]
    assert [r.kind for r in moves] == [
        "applicant.stage.new", "applicant.stage.contacted", "applicant.stage.interview", "applicant.stage.hired",
    ]
    hired = moves[-1].request["body"]
    assert hired["status"] == 105 and hired["employment_date"] == "2026-10-05"
    # повторное событие не создаёт дубль (идемпотентность по журналу)
    n = len(_log(db_session, emp.company_id))
    client.get(f"/api/invitations/{inv_id}", headers=emp.headers)
    assert len(_log(db_session, emp.company_id)) == n
    # журнал в профиле
    log = client.get("/api/companies/me/ats/log", headers=emp.headers).json()
    assert log["mode"] == "emulator" and any(i["kind"] == "applicant.create" and i["request"]["body"]["email"] == cand.email for i in log["items"])


def test_huntflow_revoke_redacts_personal_data_and_rejects(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    client.put("/api/companies/me/ats", json={"ats_type": "huntflow"}, headers=emp.headers)
    [(cand, inv_id)] = _sent(client, db_session, make_account, emp)
    _accept(client, cand, inv_id)
    assert client.post(f"/api/invitations/{inv_id}/revoke-consent", headers=cand.headers).status_code == 200
    rows = {r.kind: r for r in _log(db_session, emp.company_id)}
    assert rows["applicant.stage.rejected"].request["body"]["comment"].startswith("Кандидат отозвал согласие")
    body = rows["applicant.create"].request["body"]
    assert body["email"] == "[удалено: согласие отозвано]" and cand.email not in json.dumps(body, ensure_ascii=False)
    assert cand.full_name.split()[0] not in json.dumps(body, ensure_ascii=False)


def test_huntflow_not_matched_sends_rejection_reason(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    client.put("/api/companies/me/ats", json={"ats_type": "huntflow"}, headers=emp.headers)
    [(cand, inv_id)] = _sent(client, db_session, make_account, emp)
    _accept(client, cand, inv_id)
    r = client.post(f"/api/invitations/{inv_id}/outcome", json={"outcome": "not_matched", "reason": "position_closed"}, headers=emp.headers)
    assert r.status_code == 200
    rej = next(x for x in _log(db_session, emp.company_id) if x.kind == "applicant.stage.rejected")
    assert rej.request["body"]["status"] == 106 and rej.request["body"]["rejection_reason"] == 202
    assert "position_closed" in rej.request["body"]["comment"]


def test_huntflow_real_mode_uses_transport_and_survives_failure(client, make_account, db_session, frozen_clock):
    calls: list[tuple[str, str, dict, object]] = []

    def fake(method, url, headers, body, timeout):
        calls.append((method, url, dict(headers), body))
        if url.endswith("/vacancies/statuses"):
            return 200, {"items": [{"id": i + 10, "name": n} for i, n in enumerate(
                ["Новый кандидат", "Первичный контакт", "Интервью", "Оффер", "Нанят", "Отказ"])]}
        if url.endswith("/applicants"):
            return 200, {"id": 555}
        if url.endswith("/vacancies"):
            return 200, {"id": 777}
        return 200, {"id": 1}

    prev = huntflow.set_transport(fake)
    try:
        emp = make_employer(client, make_account, db_session)
        client.put("/api/companies/me/ats", json={"ats_type": "huntflow", "token": "tok-secret-9", "account_id": "7",
                                                  "base_url": "https://hf.example-hr.ru/v2"}, headers=emp.headers)
        assert client.post("/api/companies/me/ats/check", headers=emp.headers).json() == {
            "ok": True, "mode": "real", "message": "Подключение к https://hf.example-hr.ru/v2 работает",
        }
        [(cand, inv_id)] = _sent(client, db_session, make_account, emp)
        _accept(client, cand, inv_id)
        client.post(f"/api/invitations/{inv_id}/interview", headers=emp.headers)
    finally:
        huntflow.set_transport(prev)
    urls = [(m, u) for m, u, _, _ in calls]
    assert ("POST", "https://hf.example-hr.ru/v2/accounts/7/applicants") in urls
    assert ("POST", "https://hf.example-hr.ru/v2/accounts/7/applicants/555/vacancy") in urls
    assert all(h["Authorization"] == "Bearer tok-secret-9" for _, _, h, _ in calls)
    moves = [b for m, u, _, b in calls if u.endswith("/applicants/555/vacancy")]
    assert [x["status"] for x in moves] == [10, 12]  # «Новый кандидат», «Интервью» — id этапов из справочника ATS
    # токен не попадает в журнал
    assert "tok-secret-9" not in json.dumps([r.request for r in _log(db_session, emp.company_id)])
    assert {r.status for r in _log(db_session, emp.company_id)} == {"sent"}

    # недоступный ATS не ломает действие пользователя: причина — в журнале
    def down(method, url, headers, body, timeout):
        raise ConnectionError("ATS недоступна")

    prev = huntflow.set_transport(down)
    try:
        [(cand2, inv2)] = _sent(client, db_session, make_account, emp)
        _accept(client, cand2, inv2)
    finally:
        huntflow.set_transport(prev)
    assert client.get(f"/api/invitations/{inv2}", headers=emp.headers).json()["status"] == "accepted"
    failed = [r for r in _log(db_session, emp.company_id) if r.status == "failed"]
    assert failed and "ConnectionError" in json.dumps(failed[0].response)


def test_check_requires_huntflow_type(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    r = client.post("/api/companies/me/ats/check", headers=emp.headers)
    assert r.status_code == 409 and r.json()["code"] == "ats_not_huntflow"


def test_no_ats_no_log(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    [(cand, inv_id)] = _sent(client, db_session, make_account, emp)
    _accept(client, cand, inv_id)
    assert _log(db_session, emp.company_id) == []
    _ = pytest  # noqa: F841
