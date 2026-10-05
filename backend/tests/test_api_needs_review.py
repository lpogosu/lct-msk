"""API подбора — регрессии ревью интеграции:

- кандидат, который ПОСЛЕ сборки версии отозвал согласие на показ, ушёл в «не ищу» или заблокировал компанию,
  сразу пропадает из уже собранных (неизменяемых) версий и из карточки, а в шортлисте остаётся строкой без данных
  с жёлтой меткой (spec-contact §3.3; 152-ФЗ — «отозвал — его нет ни в подборке, ни в банке»);
- откат возвращает не только бриф, но и его живую карточку (чипы, вероятности) и флаг стажировки;
- фильтр «есть ФСП» в банке никого не удаляет — не прошедшие уходят в «Скрыто фильтрами» (концепция §6.4);
- статус активного приглашения в строке подборки и банка — код и русская подпись.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.contact.settings import get_contact_config
from app.core.clock import clock
from app.core.consents import revoke_consent
from app.models.candidate import CandidateProfile
from app.models.contact import Invitation
from app.models.users import Consent, User
from app.services.matching import get_alias, resolve_ref
from app.services.matching.needs import UNAVAILABLE_REASON
from tests.test_api_needs_support import DEMO_BRIEF, assert_no_pii, seed_candidate, seed_demo_pool

pytestmark = pytest.mark.usefixtures("frozen_clock")


@pytest.fixture
def employer(make_account):
    return make_account("hr@severny-kod.ru", role="employer")


def _company(db, emp):
    return db.get(User, emp.user_id).company_id


def _ids(db, company, items) -> list:
    return [resolve_ref(db, company, i["ref"]) for i in items]


def test_withdrawn_candidates_vanish_from_built_versions(client, db_session, employer):
    company = _company(db_session, employer)
    a = seed_candidate(db_session, theta=0.9)
    b = seed_candidate(db_session, theta=0.7)
    c = seed_candidate(db_session, theta=0.5)
    d = seed_candidate(db_session, theta=0.3)
    nid = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=employer.headers).json()["id"]
    v1 = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers).json()
    assert _ids(db_session, company, v1["items"]) == [a, b, c, d]
    refs = {cid: i["ref"] for cid, i in zip(_ids(db_session, company, v1["items"]), v1["items"], strict=True)}
    for cid in (a, b):
        assert client.put(f"/api/needs/{nid}/shortlist/{refs[cid]}", headers=employer.headers).status_code == 200

    # a отозвал согласие на показ (хук платформы), b ушёл в «не ищу», c заблокировал компанию — оба без хуков
    consent = db_session.scalars(select(Consent).where(Consent.user_id == a, Consent.purpose == "blind_card")).one()
    revoke_consent(db_session, consent)
    db_session.get(CandidateProfile, b).search_mode = "hidden"
    db_session.get(CandidateProfile, c).blocked_company_ids = [company]
    db_session.flush()

    for path in (f"/api/needs/{nid}/selection", f"/api/needs/{nid}/versions/1"):
        r = client.get(path, headers=employer.headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["n"] == 1  # версия та же — без пересчёта
        assert _ids(db_session, company, body["items"]) == [d] and body["items_total"] == 1
        assert body["items"][0]["rank"] == 4  # места остальных не пересчитываются
        sl = {e["ref"]: e for e in body["shortlist"]}
        for cid in (a, b):
            assert sl[refs[cid]]["mark"] == "yellow" and sl[refs[cid]]["reason"] == UNAVAILABLE_REASON
            assert sl[refs[cid]]["item"] is None
        text = str(body)
        assert all(refs[x] not in text for x in (c,))  # заблокировавший компанию не виден даже ссылкой
    tab = client.get(
        f"/api/needs/{nid}/selection",
        params={"tab": "category", "category": "backend_python:middle"},
        headers=employer.headers,
    ).json()
    assert _ids(db_session, company, tab["items"]) == [d]
    # Карточка — без данных подбора (минимальная), банк — без них
    card = client.get(f"/api/needs/{nid}/candidates/{refs[a]}", headers=employer.headers)
    assert card.status_code == 200, card.text
    assert card.json()["available"] is False and card.json()["category"] is None and card.json()["selection"] is None
    assert client.get(f"/api/bank/candidates/{refs[b]}", headers=employer.headers).status_code == 404
    assert _ids(db_session, company, client.get("/api/bank", headers=employer.headers).json()["items"]) == [d]

    # b вернулся в «открыт» — снова виден в той же версии (версия не менялась)
    db_session.get(CandidateProfile, b).search_mode = "open"
    db_session.flush()
    body = client.get(f"/api/needs/{nid}/selection", headers=employer.headers).json()
    assert _ids(db_session, company, body["items"]) == [b, d]
    assert {e["ref"]: e["mark"] for e in body["shortlist"]} == {refs[a]: "yellow", refs[b]: "ok"}


def test_rollback_restores_live_brief_card_and_internship_flag(client, db_session, employer):
    for i in range(3):
        seed_candidate(db_session, theta=0.3 + 0.2 * i)
    nid = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=employer.headers).json()["id"]
    client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers)
    r = client.patch(
        f"/api/needs/{nid}",
        json={"must": ["python", "fastapi", "postgresql", "kafka"], "is_internship": True},
        headers=employer.headers,
    )
    assert r.status_code == 200 and r.json()["version"]["n"] == 2
    chips = {c["skill_id"]: c["kind"] for c in r.json()["need"]["parse"]["skills"]}
    assert chips["kafka"] == "must" and r.json()["need"]["is_internship"] is True
    r = client.post(f"/api/needs/{nid}/rollback", json={"n": 1}, headers=employer.headers)
    assert r.status_code == 200, r.text
    need = client.get(f"/api/needs/{nid}", headers=employer.headers).json()
    assert need["brief"]["must"] == ["python", "fastapi", "postgresql"]
    chips = {c["skill_id"]: (c["kind"], c["source"]) for c in need["parse"]["skills"]}
    assert chips["kafka"] == ("nice", "правило")  # живая карточка совпадает с брифом версии
    assert need["is_internship"] is False
    # Повторная правка после отката работает от восстановленного брифа
    r = client.patch(f"/api/needs/{nid}", json={"title": "Шлюз"}, headers=employer.headers)
    assert r.status_code == 200 and r.json()["version"] is None


def test_bank_has_fsp_filter_hides_instead_of_dropping(client, db_session, employer):
    pool = seed_demo_pool(db_session)
    company = _company(db_session, employer)
    plain = client.get("/api/bank", headers=employer.headers).json()
    assert plain["hidden"]["count"] == 0 and plain["hidden"]["items"] == [] and plain["hidden"]["label"] is None
    body = client.get("/api/bank", params={"has_fsp": True}, headers=employer.headers).json()
    assert sorted(_ids(db_session, company, body["items"])) == sorted([pool["anna"], pool["vera"]])
    hidden = body["hidden"]
    assert hidden["count"] == 14 and hidden["label"] == "Есть ФСП" and len(hidden["items"]) == 10
    assert all(not i["has_fsp"] and i["hidden_by"] == ["has_fsp"] for i in hidden["items"])
    scores = [i["score"] for i in hidden["items"]]
    assert scores == sorted(scores, reverse=True)
    page2 = client.get("/api/bank", params={"has_fsp": True, "hidden_page": 2}, headers=employer.headers).json()
    assert len(page2["hidden"]["items"]) == 4 and page2["hidden"]["page"] == 2
    seen = _ids(db_session, company, hidden["items"] + page2["hidden"]["items"])
    assert len(set(seen)) == 14 and not {pool["anna"], pool["vera"]} & set(seen)
    for key in ("hidden_mode", "no_consent", "minor", "under_review"):
        assert pool[key] not in seen
    assert_no_pii(body, pool.values())


def test_closing_brief_withdraws_pending_invitations_end_to_end(client, db_session, make_account):
    """spec-contact §2.3: бриф закрыт → ждущие ответа приглашения отзываются «позиция закрыта» (контракт контакта);
    приглашение строится из подборки: ref из версии, карточку открывали (DECISIONS п. 7), score_snapshot из версии."""
    from tests.test_api_contact_support import INVITE, make_employer

    emp = make_employer(client, make_account, db_session)
    cid = seed_candidate(db_session, theta=0.8)
    nid = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=emp.headers).json()["id"]
    v1 = client.post(f"/api/needs/{nid}/versions", json={}, headers=emp.headers).json()
    ref = v1["items"][0]["ref"]
    assert client.get(f"/api/needs/{nid}/candidates/{ref}", headers=emp.headers).status_code == 200
    r = client.post("/api/invitations", json={**INVITE, "candidate": ref, "need_id": nid}, headers=emp.headers)
    assert r.status_code in (200, 201), r.text
    inv = db_session.scalars(select(Invitation).where(Invitation.candidate_id == cid)).one()
    assert str(inv.need_id) == nid and inv.status == "sent"
    sel = client.get(f"/api/needs/{nid}/selection", headers=emp.headers).json()["items"][0]
    assert sel["invitation_status"] == "sent" and sel["invitation_label"]
    r = client.patch(f"/api/needs/{nid}", json={"status": "closed", "close_reason": "filled"}, headers=emp.headers)
    assert r.status_code == 200, r.text
    assert r.json()["withdrawn_invitations"] == 1 and r.json()["need"]["status"] == "closed"
    db_session.refresh(inv)
    assert inv.status == "withdrawn"
    # Закрытый бриф — только для чтения: ни отката, ни закрепления; просмотр остаётся
    r = client.post(f"/api/needs/{nid}/rollback", json={"n": 1}, headers=emp.headers)
    assert r.status_code == 409 and r.json()["code"] == "need_closed"
    r = client.put(f"/api/needs/{nid}/shortlist/{ref}", headers=emp.headers)
    assert r.status_code == 409 and r.json()["code"] == "need_closed"
    assert client.get(f"/api/needs/{nid}/selection", headers=emp.headers).status_code == 200


def test_grade_history_hides_methodist_decisions(client, db_session, employer):
    """Работодатель флагов целостности не видит: аннулированная методистом сессия убрана из истории грейда
    целиком; у остальных оснований — русские подписи (никаких служебных кодов)."""
    from app.models.testing import GradeChange, TestSession

    cid = seed_candidate(db_session, theta=0.6)
    now = clock.now()
    s = TestSession(
        candidate_id=cid, specialization="backend_python", kind="upgrade", claimed_grade="senior", status="FINAL",
        seed=2, theta=1.4, se=0.3, started_at=now - timedelta(days=5), finished_at=now - timedelta(days=5),
    )
    db_session.add(s)
    db_session.flush()
    for days, frm, to, sid, reason in (
        (19, "junior", "middle", None, "onboarding_extension"),
        (5, "middle", "senior", s.id, "upgrade"),
        (2, "senior", "middle", s.id, "invalidated"),
    ):
        db_session.add(
            GradeChange(
                candidate_id=cid, specialization="backend_python", from_grade=frm, to_grade=to, session_id=sid,
                at=now - timedelta(days=days), reason=reason,
            )
        )
    db_session.flush()
    nid = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=employer.headers).json()["id"]
    ref = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers).json()["items"][0]["ref"]
    card = client.get(f"/api/needs/{nid}/candidates/{ref}", headers=employer.headers).json()
    reasons = [h["reason"] for h in card["grade_history"]]
    assert "дополнительные задания" in reasons
    assert not any("аннулир" in r or "методист" in r or "_" in r for r in reasons), reasons
    assert all(h["to_grade"] != "Senior" for h in card["grade_history"])


RANKING_MODULES = (
    "app/matching/scoring.py",
    "app/matching/explain.py",
    "app/matching/selection.py",
    "app/matching/coverage.py",
    "app/services/matching/features.py",
    "app/services/matching/sources.py",
    "app/services/matching/store.py",
    "app/services/matching/privacy.py",
    "app/services/matching/bank.py",
)


def test_ranking_path_never_imports_llm():
    """DECISIONS п. 23 / CLAUDE.md: в Score, ранжировании и объяснениях LLM запрещена архитектурно — модули этого
    контура не импортируют шлюз app/llm (LLM допустима только в разборе брифа: app/services/matching/llm_brief.py)."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for rel in RANKING_MODULES:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
                names |= {f"{node.module}.{a.name}" for a in node.names}
        bad = sorted(n for n in names if n == "app.llm" or n.startswith("app.llm.") or n.endswith("llm_brief"))
        assert not bad, (rel, bad)


def test_invitation_status_has_code_and_russian_label(client, db_session, employer):
    company = _company(db_session, employer)
    cid = seed_candidate(db_session, theta=0.8)
    nid = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=employer.headers).json()["id"]
    client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers)
    db_session.add(
        Invitation(
            company_id=company,
            candidate_id=cid,
            pseudonym=get_alias(db_session, company, cid).pseudonym,
            status="sent",
            respond_by=clock.now() + timedelta(days=5),
        )
    )
    db_session.flush()
    label = get_contact_config().status_labels.invitation["sent"]
    item = client.get(f"/api/needs/{nid}/selection", headers=employer.headers).json()["items"][0]
    assert item["invitation_status"] == "sent" and item["invitation_label"] == label
    bank = client.get("/api/bank", headers=employer.headers).json()["items"][0]
    assert bank["invitation_status"] == "sent" and bank["invitation_label"] == label
