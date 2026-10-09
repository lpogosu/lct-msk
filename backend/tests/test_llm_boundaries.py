"""LLM не вызывается в рантайме теста и в ранжировании (CLAUDE.md, docs/LLM.md §3).

Шлюз подменён: он «включён» и записывает каждое обращение. Допустимые обращения — только разбор брифа при создании/
разборе (`brief_skills`) и разбор резюме (`resume`). Сессия адаптивного теста, сборка подборки, выдача и карточка кандидата
не должны обращаться к шлюзу ни разу.
"""

from __future__ import annotations

import pytest

from app.llm import gateway as gw
from tests import test_api_testing_support as support
from tests.test_api_needs_flow import _create, employer, pool  # noqa: F401  (фикстуры)
from tests.test_api_testing_support import by_ability, do_survey, run_session, start

api = support.api
bank = support.bank
fixed_seed = support.fixed_seed

pytestmark = pytest.mark.usefixtures("frozen_clock")


@pytest.fixture
def _clean_llm_env(monkeypatch):
    """Тесты «по умолчанию» не зависят от VZ_LLM_* оператора (например, включённого GigaChat в .env)."""
    import os

    for k in [k for k in os.environ if k.startswith("VZ_LLM_")]:
        monkeypatch.delenv(k)


@pytest.fixture
def llm_calls(monkeypatch) -> list[str]:
    calls: list[str] = []

    def record(self, task, text, instructions, *, allowed_fields, extra_pii_spans=()):
        calls.append(task)
        return gw.LLMExtraction(provider=None)

    monkeypatch.setattr(gw.LLMGateway, "extract", record)
    monkeypatch.setattr(gw.LLMGateway, "enabled", property(lambda self: True))
    # и на уровне провайдеров/сети: любое прямое обращение — тоже обращение
    for cls in (gw.YandexGPTProvider, gw.GigaChatProvider, gw.OpenAICompatProvider):
        monkeypatch.setattr(cls, "complete", lambda self, *a, **k: calls.append(f"provider:{self.name}") or "{}")
    return calls


def test_test_session_makes_no_llm_calls(api, bank, make_account, llm_calls):  # noqa: F811
    acc = make_account()
    do_survey(api, acc, months=60)
    s = start(api, acc, "middle")
    run_session(api, bank, acc, s["id"], by_ability(3.0))
    api.get(f"/api/tests/sessions/{s['id']}/result", acc)
    assert llm_calls == []


def test_selection_and_ranking_make_no_llm_calls(client, employer, pool, llm_calls):  # noqa: F811
    nid = _create(client, employer).json()["id"]
    assert set(llm_calls) <= {"brief_skills"}  # единственное место LLM на стороне работодателя — разбор брифа
    llm_calls.clear()
    r = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers)
    assert r.status_code in (200, 201), r.text
    sel = client.get(f"/api/needs/{nid}/selection", headers=employer.headers)
    assert sel.status_code == 200 and sel.json()["items"]
    ref = sel.json()["items"][0]["ref"]
    assert client.get(f"/api/needs/{nid}/candidates/{ref}", headers=employer.headers).status_code == 200
    assert llm_calls == []


# --------------------------------------------------------------------------- шлюз: умолчания и OpenAI-совместимый провайдер


def _settings(**kw):
    from app.llm.settings import LLMSettings

    return LLMSettings(_env_file=None, **kw)


def test_gateway_off_by_default_without_keys(_clean_llm_env):
    g = gw.LLMGateway(_settings())
    assert g.mode == "none" and not g.enabled and g.providers == []
    res = g.extract("brief_skills", "Python, FastAPI", "x", allowed_fields={"skill"})
    assert not res.used and res.fields == ()
    # ключи без провайдера none тоже ничего не включают
    assert not gw.LLMGateway(_settings(openai_base_url="https://llm.example/v1", openai_api_key="k")).enabled


def test_openai_compat_provider_and_task_restriction():
    import json

    import httpx

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"url": str(request.url), "auth": request.headers["authorization"], "user": body["messages"][1]["content"]})
        content = json.dumps({"items": [{"field": "skill", "value": "FastAPI", "quote": "FastAPI"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    s = _settings(provider="openai_compat", openai_base_url="https://llm.example/v1/", openai_api_key="secret-key")
    provider = gw.OpenAICompatProvider(s, client=httpx.Client(transport=httpx.MockTransport(handler)))
    g = gw.LLMGateway(s, providers=[provider])
    assert g.enabled
    ok = g.extract("brief_skills", "Нужен опыт с FastAPI", "найди", allowed_fields={"skill"})
    assert ok.used and ok.provider == "openai_compat" and [f.value for f in ok.fields] == ["FastAPI"]
    assert seen[0]["url"] == "https://llm.example/v1/chat/completions" and seen[0]["auth"] == "Bearer secret-key"
    # резюме (ПДн) на этот адрес по умолчанию не уходит: задача не из openai_tasks → «без LLM», сеть не тронута
    before = len(seen)
    res = g.extract("resume", "Иван, python, ivan@example.org", "найди", allowed_fields={"skill"})
    assert not res.used and len(seen) == before
    # явное решение оператора разрешает резюме, но ПДн всё равно замаскированы до вызова
    s2 = _settings(provider="openai_compat", openai_base_url="https://llm.example/v1", openai_api_key="k", openai_tasks="brief_skills,resume")
    g2 = gw.LLMGateway(s2, providers=[gw.OpenAICompatProvider(s2, client=httpx.Client(transport=httpx.MockTransport(handler)))])
    g2.extract("resume", "python, ivan@example.org, +7 912 345-67-89", "найди", allowed_fields={"skill"})
    assert "ivan@example.org" not in seen[-1]["user"] and "912" not in seen[-1]["user"]


def test_provider_failure_degrades_to_no_llm():
    import httpx

    def boom(request):
        return httpx.Response(500)

    s = _settings(provider="openai_compat", openai_base_url="https://llm.example/v1", openai_api_key="k")
    g = gw.LLMGateway(s, providers=[gw.OpenAICompatProvider(s, client=httpx.Client(transport=httpx.MockTransport(boom)))])
    res = g.extract("brief_skills", "Python и FastAPI", "найди", allowed_fields={"skill"})
    assert not res.used and res.fields == () and res.error
