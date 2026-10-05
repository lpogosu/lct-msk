"""Проверки ревью интеграции контакта: SSRF вебхука, устойчивость задач, приватность проверочного набора.

Каждый тест — регрессия найденной на ревью проблемы (см. отчёт ревью раздела «contact»).
"""

from __future__ import annotations

import pytest

from tests.test_api_contact_support import (
    advance,
    dump,
    invite,
    make_candidate,
    make_employer,
    make_need,
    open_card,
    shortlist,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/api/admin",
        "http://localhost:5432/",
        "http://LOCALHOST./x",
        "http://db:5432/",
        "http://mailpit:8025/api/v1/messages",
        "http://10.0.0.5/hook",
        "http://192.168.1.10/hook",
        "http://172.17.0.1/hook",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/",
        "http://0.0.0.0:8000/",
        "http://2130706433/",
        "http://backend.internal/hook",
        "http://printer.local/hook",
    ],
)
def test_webhook_url_rejects_internal_hosts(client, make_account, db_session, url):
    """SSRF: адрес ATS не может указывать во внутреннюю сеть платформы (ответ приёмника виден в журнале)."""
    emp = make_employer(client, make_account, db_session)
    r = client.put("/api/companies/me", json={"webhook_url": url}, headers=emp.headers)
    assert r.status_code == 400, (url, r.text)
    assert r.json()["code"] == "webhook_url_invalid"


def test_webhook_transport_refuses_private_resolution(monkeypatch):
    """Защита от DNS-подмены: публичное имя, которое резолвится во внутренний адрес, не получает запрос."""
    import socket

    from app.services.contact import webhooks

    def fake_getaddrinfo(host, port, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    called = []
    monkeypatch.setattr(webhooks.httpx, "post", lambda *a, **kw: called.append(a))
    with pytest.raises(webhooks.WebhookBlocked):
        webhooks.http_transport("https://ats.example.com/h", b"{}", {}, 1.0)
    assert called == []


def test_type_a_malformed_answer_is_wrong_not_500(client, make_account, db_session, frozen_clock):
    from tests.test_api_tasks_flow import confirm

    emp = make_employer(client, make_account, db_session)
    client.post(
        "/api/tasks",
        json={"kind": "catalog", "family_id": "py.dict.basics", "specialization": "backend_python", "grade": "junior", "not_real_work_ack": True},
        headers=emp.headers,
    )
    cand = make_candidate(make_account, db_session)
    confirm(db_session, cand)
    offer = client.get("/api/tasks/me", headers=cand.headers).json()["active"][0]
    client.post(f"/api/tasks/assignments/{offer['id']}/start", headers=cand.headers)
    weird = {"a": [1, {"b": None}], "c": object.__name__}
    r = client.post(f"/api/tasks/assignments/{offer['id']}/submit", json={"answer": weird}, headers=cand.headers)
    assert r.status_code == 200, r.text
    assert r.json()["outcome"]["correct"] is False


def test_type_a_answer_size_limited(client, make_account, db_session, frozen_clock):
    from tests.test_api_tasks_flow import confirm

    emp = make_employer(client, make_account, db_session)
    client.post(
        "/api/tasks",
        json={"kind": "catalog", "family_id": "py.dict.basics", "specialization": "backend_python", "grade": "junior", "not_real_work_ack": True},
        headers=emp.headers,
    )
    cand = make_candidate(make_account, db_session)
    confirm(db_session, cand)
    offer = client.get("/api/tasks/me", headers=cand.headers).json()["active"][0]
    client.post(f"/api/tasks/assignments/{offer['id']}/start", headers=cand.headers)
    r = client.post(
        f"/api/tasks/assignments/{offer['id']}/submit", json={"answer": ["x" * 100] * 200}, headers=cand.headers
    )
    assert r.status_code == 400 and r.json()["code"] == "answer_too_long"


def test_quarantined_family_not_offered(client, make_account, db_session, frozen_clock):
    """Семейство в карантине детектора утечек (или выведенное) не попадает в каталог задач и не создаётся."""
    from app.models.testing import ItemFamilyParams

    db_session.add(
        ItemFamilyParams(
            family_id="py.dict.basics", specialization="backend_python", module="python_core", b=0.0, sigma_b=0.3,
            status="quarantine",
        )
    )
    db_session.flush()
    emp = make_employer(client, make_account, db_session)
    ids = {f["family_id"] for f in client.get("/api/tasks/catalog", headers=emp.headers).json()}
    assert "py.dict.basics" not in ids and ids
    r = client.post(
        "/api/tasks",
        json={"kind": "catalog", "family_id": "py.dict.basics", "specialization": "backend_python", "grade": "junior", "not_real_work_ack": True},
        headers=emp.headers,
    )
    assert r.status_code == 400 and r.json()["code"] == "family_not_found"


def test_check_set_respects_block_list(client, make_account, db_session, frozen_clock):
    """Кандидат, внёсший компанию в блок-лист, не получает от неё проверочный набор (и причина не раскрывается)."""
    from app.services.matching.privacy import get_alias

    emp = make_employer(client, make_account, db_session)
    need = make_need(db_session, emp, must=("python",))
    cand = make_candidate(make_account, db_session, blocked_company_ids=[emp.company_id])
    shortlist(db_session, need, cand)
    ref = get_alias(db_session, emp.company_id, cand.user_id).ref
    r = client.post("/api/tasks/check-sets", json={"need_id": str(need.id), "candidates": [ref]}, headers=emp.headers)
    assert r.status_code == 201, r.text
    assert r.json()["sent"] == [] and "не принимает" in r.json()["rejected"][0]["message"]
    assert "блок" not in dump(r.json()).lower()
    me = client.get("/api/tasks/me", headers=cand.headers).json()
    assert me["active"] == []
    notes = client.get("/api/notifications", headers=cand.headers).json()["items"]
    assert notes == []


def test_invite_requires_bank_visibility_consent(client, make_account, db_session, frozen_clock):
    """Отозвал согласие на показ слепой карточки — работодатель, открывавший карточку раньше, пригласить не может."""
    from app.core.consents import active_consents, revoke_consent
    from app.models.enums import ConsentPurpose

    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    a = open_card(db_session, emp, cand)
    for c in active_consents(db_session, cand.user_id, ConsentPurpose.blind_card):
        revoke_consent(db_session, c)
    db_session.flush()
    r = invite(client, emp, a.ref)
    assert r.status_code == 409, r.text
    assert "не принимает приглашения" in r.json()["message"]


def test_answer_from_list_marks_viewed_first(client, make_account, db_session, frozen_clock):
    """Ответ кандидата без открытия карточки (из списка) — это и есть просмотр: два события, без 409."""
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    inv = invite(client, emp, open_card(db_session, emp, cand).ref).json()["sent"][0]
    r = client.post(f"/api/invitations/{inv['id']}/decline", json={"reason": "stack"}, headers=cand.headers)
    assert r.status_code == 200, r.text
    assert [e["to_status"] for e in r.json()["events"]] == ["sent", "viewed", "declined"]
    other = make_candidate(make_account, db_session, n=3)
    inv2 = invite(client, emp, open_card(db_session, emp, other).ref).json()["sent"][0]
    r = client.post(f"/api/invitations/{inv2['id']}/accept", json={"consent": True}, headers=other.headers)
    assert r.status_code == 200 and r.json()["status"] == "accepted" and r.json()["contacts_open"] is True


def test_cross_company_application_and_invitation_idor(client, make_account, db_session, frozen_clock):
    e1 = make_employer(client, make_account, db_session)
    e2 = make_employer(client, make_account, db_session, "drugaya-firma.ru")
    cand = make_candidate(make_account, db_session)
    a = open_card(db_session, e1, cand)
    inv = invite(client, e1, a.ref).json()["sent"][0]
    for path in (f"/api/invitations/{inv['id']}", f"/api/invitations/{inv['id']}/withdraw"):
        r = client.get(path, headers=e2.headers) if path.endswith(inv["id"]) else client.post(
            path, json={"reason": "other"}, headers=e2.headers
        )
        assert r.status_code == 404
    # Кандидат не может вызвать действия работодателя (403), работодатель — кандидата
    assert client.post(f"/api/invitations/{inv['id']}/contacted", headers=cand.headers).status_code == 403
    assert client.post(f"/api/invitations/{inv['id']}/accept", json={"consent": True}, headers=e1.headers).status_code == 403
    other = make_candidate(make_account, db_session, n=2)
    assert client.get(f"/api/invitations/inbox/{inv['id']}", headers=other.headers).status_code == 404
    advance(hours=1)
