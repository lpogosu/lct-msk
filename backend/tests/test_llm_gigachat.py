"""GigaChat: сертификат НУЦ Минцифры из поставки, обмен OAuth-токена, воспроизведение кассеты брифов без сети и ключей."""

from __future__ import annotations

import hashlib
import ssl

import httpx
import pytest

from app.llm import gateway as gw
from app.llm.settings import LLMSettings

# SHA-256 корневого сертификата «Russian Trusted Root CA» (gu-st.ru, страница gosuslugi.ru/crt); см. docs/LLM.md §3
ROOT_CA_SHA256 = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    import os

    for k in [k for k in os.environ if k.startswith("VZ_LLM_")]:
        monkeypatch.delenv(k)


def test_bundled_root_ca_fingerprint_and_context():
    der = ssl.PEM_cert_to_DER_cert(gw.BUNDLED_CA.read_text())
    assert hashlib.sha256(der).hexdigest() == ROOT_CA_SHA256
    assert isinstance(gw._gigachat_verify("bundled"), ssl.SSLContext)
    assert LLMSettings(_env_file=None).gigachat_verify == "bundled"
    assert gw._gigachat_verify("false") is False and gw._gigachat_verify("true") is True


def test_oauth_exchange_and_chat_request_shape():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("/oauth"):
            return httpx.Response(200, json={"access_token": "tok", "expires_at": 1})
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}],
                                         "usage": {"prompt_tokens": 7, "completion_tokens": 3}})

    s = LLMSettings(_env_file=None, provider="gigachat", gigachat_auth_key="KEY")
    p = gw.GigaChatProvider(s, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert p.complete("sys", "usr", 5) == "{}"
    oauth, chat = seen
    assert oauth.headers["Authorization"] == "Basic KEY" and oauth.headers["RqUID"] and b"GIGACHAT_API_PERS" in oauth.content
    assert chat.headers["Authorization"] == "Bearer tok"
    assert p.usage == {"calls": 1, "prompt_tokens": 7, "completion_tokens": 3}


def test_brief_cassette_replay_is_deterministic_and_never_worse():
    from eval import llm_brief_eval as ev

    if not ev.CASSETTE.is_file():
        pytest.skip("кассета не записана")
    r = ev.run("replay")
    assert r["n"] >= 62 and r["failure_rate"] == 0.0 and r["latency_s"]["calls"] == 0
    assert r["llm"]["skills"]["f1"] >= r["rules"]["skills"]["f1"] - 1e-9  # LLM только добавляет: не хуже правил
    assert ev.run("replay")["llm"] == r["llm"]
