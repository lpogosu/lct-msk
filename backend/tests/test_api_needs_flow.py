"""API подбора: демо-бриф концепции §11 от разбора до ранжированного списка, версии, откат, шортлист.

Контрольные примеры: бриф «Северного кода» (вымышленная компания) → чипы с цитатами → бриф с вилкой → категории
и охват → «Как мы проверим» → v1 → фильтр «Есть ФСП» → v2 (неразрушающе, «Скрыто фильтрами», жёлтая метка
в шортлисте) → откат на v1 → «почему выше соседа» → карточка Р6 (открытие записано, DECISIONS п. 7).
"""

from __future__ import annotations

import pytest

from app.models.users import User
from app.services.matching import card_opened, resolve_ref
from tests.test_api_needs_support import (
    DEMO_BRIEF,
    FSP_MEDIUM,
    FSP_STRONG,
    assert_no_pii,
    ekp_numbers,
    seed_demo_pool,
)

pytestmark = pytest.mark.usefixtures("frozen_clock")


@pytest.fixture
def employer(make_account):
    return make_account("hr@severny-kod.ru", role="employer")


@pytest.fixture
def pool(db_session):
    return seed_demo_pool(db_session)


def _create(client, employer, **extra):
    body = {"text": DEMO_BRIEF, **extra}
    return client.post("/api/needs", json=body, headers=employer.headers)


def test_parse_demo_brief_chips_with_quotes(client, employer):
    r = client.post("/api/needs/parse", json={"text": DEMO_BRIEF}, headers=employer.headers)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["llm_used"] is False and p["parser_version"]
    kinds = {c["skill_id"]: c["kind"] for c in p["skills"]}
    assert kinds == {"python": "must", "fastapi": "must", "postgresql": "must", "kafka": "nice", "docker": "nice"}
    for c in p["skills"]:
        q = c["quote"]
        assert DEMO_BRIEF[q["start"] : q["end"]] == q["text"], c
        assert c["source"] == "правило"
    kafka = next(c for c in p["skills"] if c["skill_id"] == "kafka")
    assert kafka["marker"] and "плюсом" in kafka["marker"]["text"] and kafka["untestable"] is True
    assert p["spec"]["top"] == "backend_python" and p["spec"]["top_name"] == "Backend (Python)"
    assert p["grade"]["top"] == "middle"
    assert p["salary"]["salary_from"] == 220_000 and p["salary"]["salary_to"] == 280_000 and p["salary"]["net"] is True
    assert p["salary_check"]["status"] == "ok"
    assert [f["value"] for f in p["formats"]] == ["hybrid"]
    assert p["city"]["value"] == "moscow" and p["city"]["name"] == "Москва"
    assert p["question"] is None
    cats = [(c["spec"], c["grade"], c["kind"]) for c in p["categories"]]
    assert cats[0] == ("backend_python", "middle", "primary") and ("backend_python", "senior", "suggested") in cats
    # DECISIONS п. 21: цифры — из движка, никаких ручных: P(Middle) < P(spec) и сумма вероятностей ≈ 1
    assert abs(sum(x["p"] for x in p["grade"]["probs"]) - 1.0) < 0.01


def test_parse_manual_edits_and_question(client, employer):
    r = client.post(
        "/api/needs/parse",
        json={"text": DEMO_BRIEF, "must": ["python", "fastapi", "postgresql", "kafka"], "grade": "senior"},
        headers=employer.headers,
    )
    assert r.status_code == 200, r.text
    p = r.json()
    kafka = next(c for c in p["skills"] if c["skill_id"] == "kafka")
    assert kafka["kind"] == "must" and kafka["source"] == "вручную"
    assert {c["skill_id"] for c in p["skills"] if c["kind"] == "nice"} == {"docker"}
    assert p["grade"]["top"] == "senior" and p["grade"]["source"] == "вручную"
    too_many = client.post(
        "/api/needs/parse",
        json={"text": DEMO_BRIEF, "must": ["python", "fastapi", "postgresql", "kafka", "docker", "django"]},
        headers=employer.headers,
    )
    assert too_many.status_code == 422 and too_many.json()["code"] == "brief_invalid"
    vague = client.post("/api/needs/parse", json={"text": "Нужен разработчик в команду"}, headers=employer.headers)
    assert vague.status_code == 200
    assert vague.json()["question"] is not None


def test_demo_brief_end_to_end(client, employer, pool, db_session):
    r = _create(client, employer, title="Платёжный шлюз")
    assert r.status_code == 201, r.text
    need = r.json()
    nid = need["id"]
    assert need["status"] == "parsed" and need["current_version"] is None
    assert need["brief"]["must"] == ["python", "fastapi", "postgresql"]
    assert need["brief"]["salary_from"] == 220_000 and need["brief"]["salary_net"] is True
    assert need["warnings"] == []
    selected = [(c["spec"], c["grade"]) for c in need["categories"] if c["selected"]]
    assert ("backend_python", "middle") in selected

    # Р4: категории и охват. 13 видимых Backend·Middle (12 + Fullstack-кандидат): скрытые сюда не попали.
    r = client.get(f"/api/needs/{nid}/categories", headers=employer.headers)
    assert r.status_code == 200, r.text
    cards = {(c["spec"], c["grade"]): c for c in r.json()["categories"]}
    mid = cards[("backend_python", "middle")]
    assert mid["n"] == 13 and mid["reach"]["shown"] is True
    assert mid["share_fsp"] == round(2 / 13, 2)
    assert all(c >= 3 for _, c in mid["reach"]["histogram"]["bins"])  # приросты охвата ступенями ≥ 3
    assert cards[("backend_python", "senior")]["n"] == 3 and cards[("backend_python", "senior")]["reach"]["shown"] is False

    # «Как мы проверим»: Kafka — серым; Docker (nice) проверяет devops_core, но его нет в тесте Backend (Python) — «на интервью»
    r = client.get(f"/api/needs/{nid}/check", headers=employer.headers)
    assert r.status_code == 200, r.text
    chk = r.json()
    assert {u["skill_id"] for u in chk["untestable"]} == {"kafka"}
    assert {c["skill_id"] for c in chk["covered"]} == {"python", "fastapi", "postgresql", "docker"}
    assert {c["skill_id"]: c["via"] for c in chk["covered"]} == {
        "python": "standard", "fastapi": "standard", "postgresql": "standard", "docker": "interview",
    }
    assert "проверьте на интервью" in next(c["text"] for c in chk["covered"] if c["skill_id"] == "docker")
    assert abs(sum(m["weight"] for m in chk["module_weights"]) - 1.0) < 1e-6
    assert 3 <= len(chk["check_set"]["slots"]) <= 5 and chk["check_set"]["total_minutes"] <= 15

    # v1
    r = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers)
    assert r.status_code == 201, r.text
    v1 = r.json()
    assert v1["n"] == 1 and v1["current"] is True and v1["parent_n"] is None
    assert v1["tab"] == "overall" and len(v1["items"]) == 10 and v1["pages"] == 2
    # По умолчанию выбрана только основная категория (Senior — предложена): 12 Middle + Middle-строка Fullstack
    assert v1["visible_n"] == v1["total_n"] == 13 and v1["hidden_n"] == 0
    assert [c["grade"] for c in v1["categories"]] == ["middle"]
    page2 = client.get(f"/api/needs/{nid}/selection", params={"page": 2}, headers=employer.headers).json()
    items = v1["items"] + page2["items"]
    assert [i["rank"] for i in items] == list(range(1, 14))
    scores = [i["score"] for i in items]
    assert scores == sorted(scores, reverse=True)
    for it in items:
        assert it["pseudonym"].startswith("К-") and len(it["pseudonym"]) == 6
        assert abs(sum(it["parts"].values()) - it["score"]) <= 0.1 + 1e-9  # Σ вкладов = Score
        assert len(it["pros"]) <= 3
        assert it["con"] is None or it["con"]["code"] != "fsp"  # отсутствие ФСП в «против» — никогда
        assert it["test"]["band"] and it["test"].get("score") is None  # точный балл — только после раскрытия
        assert it["fsp_line"]
    top = v1["items"][0]
    assert top["rank"] == 1 and top["neighbor_below"].startswith("#1 выше #2")
    no_fsp = next(i for i in items if not i["has_fsp"])
    assert no_fsp["fsp_line"] == "Достижения ФСП не привязаны — оценка по тесту и задачам"
    with_fsp = [i for i in items if i["has_fsp"]]
    assert len(with_fsp) == 2 and any("Призёр всероссийских" in i["fsp_line"] for i in with_fsp)
    assert_no_pii(v1, pool.values(), ekp_numbers(FSP_STRONG) | ekp_numbers(FSP_MEDIUM))
    assert_no_pii(page2, pool.values(), ekp_numbers(FSP_STRONG) | ekp_numbers(FSP_MEDIUM))

    # Шортлист: закрепляем #1 и первого без ФСП
    pinned_fsp, pinned_plain = with_fsp[0]["ref"], no_fsp["ref"]
    for ref in (pinned_fsp, pinned_plain):
        r = client.put(f"/api/needs/{nid}/shortlist/{ref}", headers=employer.headers)
        assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 2 and all(e["mark"] == "ok" for e in r.json()["items"])

    # v2: «Есть ФСП» — неразрушающе
    r = client.post(f"/api/needs/{nid}/versions", json={"filters": [{"code": "has_fsp"}]}, headers=employer.headers)
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["n"] == 2 and v2["parent_n"] == 1
    assert v2["total_n"] == v1["total_n"]  # фильтр никого не удаляет
    assert v2["visible_n"] == 2 and v2["hidden"]["count"] == v2["hidden_n"] == 11
    assert v2["hidden_summary"] == [{"code": "has_fsp", "label": "Есть ФСП", "count": 11}]
    assert all("has_fsp" in h["hidden_by"] and h["rank"] is None for h in v2["hidden"]["items"])
    marks = {e["ref"]: e for e in v2["shortlist"]}
    assert marks[pinned_fsp]["mark"] == "ok"
    assert marks[pinned_plain]["mark"] == "yellow" and "ФСП" in marks[pinned_plain]["reason"]
    badges = {i["ref"]: i["badge"] for i in v2["items"]}
    assert all(b is not None and b["kind"] in ("up", "same") for b in badges.values())
    hidden_badges = {i["ref"]: i["badge"] for i in v2["hidden"]["items"]}
    assert all(b and b["kind"] == "hidden" and b["label"] == "скрыт" for b in hidden_badges.values())

    # v1 цела и доступна (без пересчёта); список версий
    r = client.get(f"/api/needs/{nid}/versions/1", headers=employer.headers)
    assert r.status_code == 200
    again = r.json()["items"]
    assert [(i["ref"], i["score"], i["rank"]) for i in again] == [(i["ref"], i["score"], i["rank"]) for i in v1["items"]]
    r = client.get(f"/api/needs/{nid}/versions", headers=employer.headers)
    lst = r.json()
    assert [v["n"] for v in lst["items"]] == [1, 2] and lst["current"] == 2 and lst["rollback_targets"] == [1, 2]

    # Откат на v1 — перенос указателя без пересчёта
    r = client.post(f"/api/needs/{nid}/rollback", json={"n": 1}, headers=employer.headers)
    assert r.status_code == 200, r.text
    back = r.json()
    assert back["n"] == 1 and back["current"] is True
    assert [i["ref"] for i in back["items"]] == [i["ref"] for i in v1["items"]]
    assert all(e["mark"] == "ok" for e in back["shortlist"])  # шортлист сохранился, жёлтые метки ушли
    r = client.get(f"/api/needs/{nid}/selection", headers=employer.headers)
    assert r.json()["n"] == 1

    # «Почему выше соседа»
    r = client.get(f"/api/needs/{nid}/versions/1/neighbors/2", headers=employer.headers)
    assert r.status_code == 200, r.text
    nb = r.json()
    assert nb["upper_rank"] == 2 and nb["lower_rank"] == 3 and nb["text"].startswith("#2 выше #3")
    assert client.get(f"/api/needs/{nid}/versions/1/neighbors/400", headers=employer.headers).status_code == 404

    # Вкладка категории
    r = client.get(
        f"/api/needs/{nid}/selection",
        params={"tab": "category", "category": "backend_python:middle"},
        headers=employer.headers,
    )
    assert r.status_code == 200 and r.json()["category"]["name"] == "Backend (Python) · Middle"
    assert r.json()["items"][0]["category_rank"] == 1

    # Карточка Р6: открытие записывается (DECISIONS п. 7), до «да» — только слепая
    third = v1["items"][2]
    company_id = db_session.get(User, employer.user_id).company_id
    cid = resolve_ref(db_session, company_id, third["ref"])
    assert card_opened(db_session, company_id, cid) is False
    r = client.get(f"/api/needs/{nid}/candidates/{third['ref']}", headers=employer.headers)
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["view"] == "blind" and card["disclosed"] is None and card["available"] is True
    assert card["pseudonym"] == third["pseudonym"] and card["selection"]["ref"] == third["ref"]
    assert card["category"]["name"] == "Backend (Python) · Middle"
    assert card["test"]["band"] and card["test"].get("score") is None
    assert_no_pii(card, pool.values(), ekp_numbers(FSP_STRONG))
    assert card_opened(db_session, company_id, cid) is True
    # шортлист тоже считается «открывал карточку»
    pinned_cid = resolve_ref(db_session, company_id, pinned_plain)
    assert card_opened(db_session, company_id, pinned_cid) is True


def test_patch_brief_creates_version_and_rollback_restores_brief(client, employer, pool):
    nid = _create(client, employer).json()["id"]
    v1 = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers).json()
    # Kafka → must: новая версия v2, у тех, кто Kafka не подтвердил, «против» без ссылки на резюме (DECISIONS п. 16)
    r = client.patch(
        f"/api/needs/{nid}",
        json={"must": ["python", "fastapi", "postgresql", "kafka"]},
        headers=employer.headers,
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["version"]["n"] == 2 and out["need"]["current_version"] == 2
    assert "kafka" in out["need"]["brief"]["must"]
    v2 = client.get(f"/api/needs/{nid}/selection", headers=employer.headers).json()
    cons = [i["con"] for i in v2["items"] if i["con"]]
    assert cons and all("заявлен" not in c["text"] for c in cons)
    assert all(e["type"] != "declared" for c in cons for e in c["evidence"])
    kafka_cons = [c for c in cons if c["code"] == "must_unconfirmed"]
    assert any("проверьте на интервью" in c["text"] for c in kafka_cons)
    # Kafka тестом не проверяется — у Анны (она её «заявила») чип серый «заявлено», а не подтверждение
    anna_chips = [
        ch for i in v2["items"] for ch in i["chips"] if ch["skill_id"] == "kafka" and ch["state"] == "declared"
    ]
    assert anna_chips and all(ch["label"] == "заявлено" for ch in anna_chips)
    # Правка названия не создаёт новую версию
    r = client.patch(f"/api/needs/{nid}", json={"title": "Шлюз"}, headers=employer.headers)
    assert r.status_code == 200 and r.json()["version"] is None
    # Откат на v1 возвращает и бриф
    r = client.post(f"/api/needs/{nid}/rollback", json={"n": 1}, headers=employer.headers)
    assert r.status_code == 200
    need = client.get(f"/api/needs/{nid}", headers=employer.headers).json()
    assert need["brief"]["must"] == ["python", "fastapi", "postgresql"] and need["current_version"] == 1
    assert [i["ref"] for i in r.json()["items"]] == [i["ref"] for i in v1["items"]]


def test_compare_to_and_preset_and_category_choice(client, employer, pool):
    nid = _create(client, employer).json()["id"]
    client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers)
    r = client.post(
        f"/api/needs/{nid}/versions",
        json={"preset": "stack", "categories": [{"spec": "backend_python", "grade": "middle"}, {"spec": "backend_python", "grade": "senior"}]},
        headers=employer.headers,
    )
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["preset"] == "stack" and v2["weights"]["K"] == 0.4
    assert {c["grade"] for c in v2["categories"]} == {"middle", "senior"}
    assert any(i["badge"] and i["badge"]["kind"] == "new" for i in v2["items"])  # Senior пришли «новыми»
    v3 = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers).json()  # «пересчитать»
    assert v3["n"] == 3 and v3["parent_n"] == 2 and v3["preset"] == "stack"  # параметры унаследованы
    assert all(i["badge"]["kind"] == "same" for i in v3["items"])  # данные не менялись — места те же
    r = client.get(f"/api/needs/{nid}/versions/3", params={"compare_to": 1}, headers=employer.headers)
    assert r.json()["compared_to"] == 1
    assert any(i["badge"] and i["badge"]["kind"] == "new" for i in r.json()["items"])
    r = client.get(f"/api/needs/{nid}/versions/3", params={"compare_to": 9}, headers=employer.headers)
    assert r.status_code == 404
    r = client.post(f"/api/needs/{nid}/versions", json={"preset": "turbo"}, headers=employer.headers)
    assert r.status_code == 422
    r = client.post(f"/api/needs/{nid}/versions", json={"categories": []}, headers=employer.headers)
    assert r.status_code == 422 and r.json()["code"] == "categories_empty"
    r = client.post(
        f"/api/needs/{nid}/versions", json={"filters": [{"code": "min_score"}]}, headers=employer.headers
    )
    assert r.status_code == 422 and r.json()["code"] == "filter_invalid"


def test_exclude_filter_by_ref_and_list_needs(client, employer, pool):
    nid = _create(client, employer).json()["id"]
    v1 = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers).json()
    ref = v1["items"][0]["ref"]
    r = client.post(
        f"/api/needs/{nid}/versions",
        json={"filters": [{"code": "exclude", "value": [ref]}]},
        headers=employer.headers,
    )
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert ref not in [i["ref"] for i in v2["items"]]
    assert v2["filters"] == [{"code": "exclude", "value": [ref], "label": "Скрыть вручную"}]
    assert [h["ref"] for h in v2["hidden"]["items"]] == [ref]
    r = client.post(
        f"/api/needs/{nid}/versions",
        json={"filters": [{"code": "exclude", "value": ["cdeadbeefdeadbeef0000"]}]},
        headers=employer.headers,
    )
    assert r.status_code == 422
    lst = client.get("/api/needs", headers=employer.headers).json()["items"]
    assert [n["id"] for n in lst] == [nid] and lst[0]["current_version"] == 2
    assert client.get("/api/needs", params={"status": "closed"}, headers=employer.headers).json()["items"] == []


def test_close_need(client, employer, pool):
    nid = _create(client, employer).json()["id"]
    r = client.patch(f"/api/needs/{nid}", json={"status": "closed", "close_reason": "filled"}, headers=employer.headers)
    assert r.status_code == 200, r.text
    assert r.json()["need"]["status"] == "closed" and r.json()["need"]["close_reason"] == "filled"
    r = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers)
    assert r.status_code == 409 and r.json()["code"] == "need_closed"
    r = client.patch(f"/api/needs/{nid}", json={"title": "x"}, headers=employer.headers)
    assert r.status_code == 409
