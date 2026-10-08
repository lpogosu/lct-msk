"""API подбора: правила вилки (DECISIONS п. 6), накрутка заявленными навыками (DECISIONS п. 1), жёсткие фильтры
компании (блок-лист, пауза пары, 16–17 лет только для стажировок), отзыв согласия, шортлист."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import clock
from app.core.consents import revoke_consent
from app.models.candidate import CandidateProfile
from app.models.contact import Cooldown
from app.models.users import Consent, User
from app.services.matching import invalidate_features
from tests.test_api_needs_support import DEMO_BRIEF, seed_candidate, seed_demo_pool

pytestmark = pytest.mark.usefixtures("frozen_clock")

BRIEF_NO_SALARY = (
    "Бэкенд в команду платёжного шлюза. Уверенный Python, FastAPI, PostgreSQL, опыт от 2 лет. Гибрид, Москва."
)


@pytest.fixture
def employer(make_account):
    return make_account("hr@severny-kod.ru", role="employer")


def _all_refs(client, emp, nid) -> list[dict]:
    first = client.get(f"/api/needs/{nid}/selection", headers=emp.headers).json()
    items = list(first["items"])
    for p in range(2, first["pages"] + 1):
        items += client.get(f"/api/needs/{nid}/selection", params={"page": p}, headers=emp.headers).json()["items"]
    return items


@pytest.mark.parametrize(
    ("salary", "status", "code"),
    [
        ({"salary_from": 220_000, "salary_to": 280_000}, 201, None),
        ({"salary_from": 200_000, "salary_to": 360_000}, 201, "salary_wide"),  # 1,8× — мягкое предупреждение
        ({"salary_from": 200_000, "salary_to": 400_000}, 201, "salary_wide"),  # ровно 2,0× — ещё предупреждение
        ({"salary_from": 100_000, "salary_to": 250_000}, 422, "salary_blocked"),  # 2,5× — блок
        ({"salary_from": 20_000, "salary_to": 30_000}, 422, "salary_invalid"),  # from < 30 000
        ({"salary_from": 250_000, "salary_to": 200_000}, 422, "salary_invalid"),  # to < from
        ({"salary_from": 220_500, "salary_to": 280_000}, 422, "salary_invalid"),  # шаг 1000
        ({"salary_from": 220_000}, 422, "salary_incomplete"),
        ({}, 422, "salary_missing"),
    ],
)
def test_salary_rules(client, employer, salary, status, code):
    r = client.post("/api/needs", json={"text": BRIEF_NO_SALARY, **salary}, headers=employer.headers)
    assert r.status_code == status, r.text
    if status == 201:
        warnings = [w["code"] for w in r.json()["warnings"]]
        assert warnings == ([code] if code else [])
        if code:
            assert "Рекомендуем" in r.json()["warnings"][0]["message"]
    else:
        assert r.json()["code"] == code and r.json()["message"]


def test_salary_from_text_and_patch(client, employer):
    r = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=employer.headers)
    assert r.status_code == 201
    nid = r.json()["id"]
    # Новый текст с другой вилкой и без полей формы — вилка из текста
    text2 = DEMO_BRIEF.replace("220–280 тыс.", "250–320 тыс.")
    r = client.patch(f"/api/needs/{nid}", json={"text": text2}, headers=employer.headers)
    assert r.status_code == 200, r.text
    assert (r.json()["need"]["salary_from"], r.json()["need"]["salary_to"]) == (250_000, 320_000)
    # Поля формы имеют приоритет
    r = client.patch(f"/api/needs/{nid}", json={"salary_to": 300_000}, headers=employer.headers)
    assert r.json()["need"]["salary_to"] == 300_000
    r = client.patch(f"/api/needs/{nid}", json={"salary_to": 900_000}, headers=employer.headers)
    assert r.status_code == 422 and r.json()["code"] == "salary_blocked"
    assert client.get(f"/api/needs/{nid}", headers=employer.headers).json()["salary_to"] == 300_000


def test_fake_declared_skills_change_rank_by_zero(client, db_session, employer):
    """DECISIONS п. 1: заявленные навыки — только серые чипы; накрутка ими сдвигает ранг на 0."""
    ids = [seed_candidate(db_session, theta=0.2 + 0.08 * i, salary_min=200_000 + 5_000 * i) for i in range(10)]
    nid = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=employer.headers).json()["id"]
    client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers)
    before = [(i["ref"], i["score"], i["parts"]) for i in _all_refs(client, employer, nid)]
    # «Хвастуны»: самые слабые заявляют все навыки брифа и ещё десяток
    fake = ["python", "fastapi", "postgresql", "kafka", "docker", "django", "redis", "kubernetes", "go", "java"]
    for cid in ids[:5]:
        prof = db_session.get(CandidateProfile, cid)
        prof.declared_skills = [{"skill": s, "source": "resume"} for s in fake]
        invalidate_features(db_session, cid)
    db_session.flush()
    r = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers)
    assert r.status_code == 201
    after = [(i["ref"], i["score"], i["parts"]) for i in _all_refs(client, employer, nid)]
    assert after == before  # те же места, те же баллы и вклады
    assert all(i["badge"]["kind"] == "same" for i in r.json()["items"])
    chips = [c for i in _all_refs(client, employer, nid) for c in i["chips"] if c["state"] == "declared"]
    assert chips and all(c["label"] == "заявлено" and c["source"] is None for c in chips)


def test_hard_filters_block_list_cooldown_minor(client, db_session, employer, make_account):
    company = db_session.get(User, employer.user_id).company_id
    visible = seed_candidate(db_session, theta=0.9)
    blocker = seed_candidate(db_session, theta=0.8, blocked=[company])
    paused = seed_candidate(db_session, theta=0.7)
    teen = seed_candidate(db_session, theta=0.6, birth_year=clock.now().year - 17)  # 16–17 лет, согласие представителя ниже
    db_session.add(
        Cooldown(company_id=company, candidate_id=paused, until=clock.now() + timedelta(days=20), reason="declined")
    )
    db_session.flush()
    nid = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=employer.headers).json()["id"]
    v = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers).json()
    from app.services.matching.privacy import resolve_ref

    shown = {resolve_ref(db_session, company, i["ref"]) for i in v["items"]}
    assert shown == {visible}
    assert v["hidden_n"] == 0  # жёсткие фильтры — не «Скрыто фильтрами», их просто нет
    # Стажировка: 16–17 лет без согласия законного представителя всё равно не видны
    nid2 = client.post(
        "/api/needs", json={"text": DEMO_BRIEF, "is_internship": True}, headers=employer.headers
    ).json()["id"]
    v2 = client.post(f"/api/needs/{nid2}/versions", json={}, headers=employer.headers).json()
    assert {resolve_ref(db_session, company, i["ref"]) for i in v2["items"]} == {visible}
    # С согласием — видны стажировке, карточка несёт плашку «Кандидату 15–17 лет: …», возраст не раскрывается
    db_session.get(CandidateProfile, teen).guardian_consent = True
    db_session.flush()
    v2 = client.post(f"/api/needs/{nid2}/versions", json={}, headers=employer.headers).json()
    by_id = {resolve_ref(db_session, company, i["ref"]): i for i in v2["items"]}
    assert set(by_id) == {visible, teen}
    card = client.get(f"/api/needs/{nid2}/candidates/{by_id[teen]['ref']}", headers=employer.headers).json()
    assert card["minor_notice"] == "Кандидату 15–17 лет: только стажировка или лёгкий труд, неполный день"
    assert "birth_year" not in str(card) and "guardian" not in str(card)
    adult = client.get(f"/api/needs/{nid2}/candidates/{by_id[visible]['ref']}", headers=employer.headers).json()
    assert adult.get("minor_notice") is None
    db_session.get(CandidateProfile, teen).guardian_consent = False
    db_session.flush()
    # Другая компания видит и блокировавшего не её кандидата, и того, с кем у неё нет паузы
    other = make_account("lead@yuzhny-bereg.ru", role="employer")
    other_company = db_session.get(User, other.user_id).company_id
    nid3 = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=other.headers).json()["id"]
    v3 = client.post(f"/api/needs/{nid3}/versions", json={}, headers=other.headers).json()
    assert {resolve_ref(db_session, other_company, i["ref"]) for i in v3["items"]} == {visible, blocker, paused}
    # Пауза после отказа «зарплата» снимается новым from ≥ 1,15 × прошлого to
    cd = db_session.get(Cooldown, (company, paused))
    cd.salary_unlock_from = 250_000
    db_session.flush()
    nid4 = client.post(
        "/api/needs",
        json={"text": DEMO_BRIEF.replace("220–280 тыс.", "250–320 тыс.")},
        headers=employer.headers,
    ).json()["id"]
    v4 = client.post(f"/api/needs/{nid4}/versions", json={}, headers=employer.headers).json()
    assert paused in {resolve_ref(db_session, company, i["ref"]) for i in v4["items"]}


def test_consent_revocation_and_search_mode_drop_candidate(client, db_session, employer):
    a = seed_candidate(db_session, theta=0.9)
    b = seed_candidate(db_session, theta=0.5)
    nid = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=employer.headers).json()["id"]
    v1 = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers).json()
    assert v1["visible_n"] == 2
    consent = db_session.scalars(select(Consent).where(Consent.user_id == a, Consent.purpose == "blind_card")).one()
    revoke_consent(db_session, consent)  # хук app.core.consents → кэш признаков помечен устаревшим
    db_session.flush()
    v2 = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers).json()
    assert v2["visible_n"] == 1
    # Режим «не ищу» без всяких хуков: подхватывает отпечаток источников
    db_session.get(CandidateProfile, b).search_mode = "hidden"
    db_session.flush()
    v3 = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers).json()
    assert v3["visible_n"] == 0 and v3["items"] == []
    assert client.get("/api/bank", headers=employer.headers).json()["total"] == 0


def test_shortlist_rules(client, db_session, employer):
    ids = [seed_candidate(db_session, theta=0.1 * i) for i in range(4)]
    nid = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=employer.headers).json()["id"]
    # шортлист до первой подборки — по ссылкам из банка
    bank = client.get("/api/bank", headers=employer.headers).json()
    ref = bank["items"][0]["ref"]
    r = client.put(f"/api/needs/{nid}/shortlist/{ref}", json={"note": "сильный SQL"}, headers=employer.headers)
    assert r.status_code == 200, r.text
    entry = r.json()["items"][0]
    assert entry["mark"] == "yellow" and entry["reason"] == "Подборка ещё не собрана" and r.json()["limit"] == 30
    assert client.put(f"/api/needs/{nid}/shortlist/{ref}", headers=employer.headers).status_code == 200  # идемпотентно
    v1 = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers).json()
    sl = v1["shortlist"]
    assert len(sl) == 1 and sl[0]["mark"] == "ok" and sl[0]["note"] == "сильный SQL" and sl[0]["item"]["ref"] == ref
    assert next(i for i in v1["items"] if i["ref"] == ref)["shortlisted"] is True
    # банк отмечает «уже в шортлисте брифа»
    bank = client.get("/api/bank", params={"need_id": nid}, headers=employer.headers).json()
    mark = next(i for i in bank["items"] if i["ref"] == ref)
    assert mark["shortlisted"] is True and mark["shortlisted_in"] == [nid]
    # кандидат ушёл в «не ищу» — закреплённый остаётся строкой без данных с жёлтой меткой
    from app.services.matching.privacy import resolve_ref

    company = db_session.get(User, employer.user_id).company_id
    cid = resolve_ref(db_session, company, ref)
    db_session.get(CandidateProfile, cid).search_mode = "hidden"
    db_session.flush()
    v2 = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers).json()
    assert v2["shortlist"][0]["mark"] == "yellow" and v2["shortlist"][0]["item"] is None
    assert v2["shortlist"][0]["reason"] == "Кандидат сейчас не показывается в подборе"
    assert len(ids) == 4
    r = client.delete(f"/api/needs/{nid}/shortlist/{ref}", headers=employer.headers)
    assert r.status_code == 200 and r.json()["items"] == []
    r = client.delete(f"/api/needs/{nid}/shortlist/{ref}", headers=employer.headers)
    assert r.status_code == 404 and r.json()["code"] == "pin_not_found"
    # скрытого кандидата закрепить нельзя
    assert client.put(f"/api/needs/{nid}/shortlist/{ref}", headers=employer.headers).status_code == 404


def test_salary_signal_respects_net_gross(client, db_session, employer):
    """Минимум 300 тыс. до НДФЛ ≈ 261 тыс. на руки — в вилке 220–280 тыс. на руки (значок, а не число)."""
    gross = seed_candidate(db_session, theta=0.6, salary_min=300_000, salary_net=False)
    high = seed_candidate(db_session, theta=0.5, salary_min=330_000, salary_net=True, salary_visibility="exact")
    nid = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=employer.headers).json()["id"]
    v = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers).json()
    from app.services.matching.privacy import resolve_ref

    company = db_session.get(User, employer.user_id).company_id
    by_id = {resolve_ref(db_session, company, i["ref"]): i for i in v["items"]}
    assert by_id[gross]["salary"]["signal"] == "в вилке ✓" and by_id[gross]["salary"]["value"] is None
    assert by_id[high]["salary"] == {"signal": "выше вилки", "value": 330_000, "net": True}
    assert by_id[high]["con"]["code"] == "salary_above" and "330 000" in by_id[high]["con"]["text"]
    card = client.get(f"/api/needs/{nid}/candidates/{by_id[gross]['ref']}", headers=employer.headers).json()
    assert card["salary"] == {"signal": "в вилке ✓", "value": None, "net": None}
    card = client.get(f"/api/needs/{nid}/candidates/{by_id[high]['ref']}", headers=employer.headers).json()
    assert card["salary"]["signal"] == "выше вилки" and card["salary"]["value"] == 330_000


def test_seed_demo_pool_numbers_reproducible(client, db_session, employer):
    """DECISIONS п. 14/21: цифры демо — из движков и детерминированы (тот же бриф → те же P и тот же порядок)."""
    seed_demo_pool(db_session)
    one = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=employer.headers).json()
    two = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=employer.headers).json()
    assert one["categories"][0]["p"] == two["categories"][0]["p"]
    a = client.post(f"/api/needs/{one['id']}/versions", json={}, headers=employer.headers).json()
    b = client.post(f"/api/needs/{two['id']}/versions", json={}, headers=employer.headers).json()
    assert [i["score"] for i in a["items"]] == [i["score"] for i in b["items"]]
    assert [i["pseudonym"] for i in a["items"][:3]] == [i["pseudonym"] for i in b["items"][:3]]
