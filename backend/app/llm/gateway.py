"""Шлюз LLM: YandexGPT → GigaChat → без LLM (концепция §3.1.1, §4.2 «LLM только добавляет, с цитатой»).

Правила работы:
- выключен по умолчанию (VZ_LLM_PROVIDER=none) — тогда extract() сразу возвращает пустой результат;
- ПДн маскируются ДО вызова (app/llm/masking.py), маска сохраняет длину текста;
- каждое предложенное поле обязано прийти с цитатой; цитата ищется в тексте через
  rapidfuzz.fuzz.partial_ratio_alignment, поле с partial_ratio < порога (90) отбрасывается;
- общий бюджет времени — 20 с на всю цепочку провайдеров; сбой или таймаут → следующий провайдер,
  в конце — «без LLM» (детерминированный fallback: результат правил не меняется);
- ответ кэшируется по sha256(версия промпта + задача + замаскированный текст): повторный разбор того же
  резюме не ходит в сеть и даёт тот же результат;
- режимы (DECISIONS п. 23): none — выключена; live — провайдеры; record — провайдеры + запись ответа в
  JSON-кассету по тому же ключу sha256; replay — только кассета (без сети и ключей): демо воспроизводимо,
  а промах кассеты — это «без LLM», как при недоступности провайдеров. Текст запроса в кассете не хранится:
  только ключ sha256 и ответ модели на замаскированный текст (ПДн туда не попадают).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import httpx
from rapidfuzz import fuzz

from app.llm.masking import MASK_CHAR, mask_pii
from app.llm.settings import LLMSettings

log = logging.getLogger("vzaimno.llm")

PROMPT_VERSION = "extract-v1"


class LLMError(Exception):
    """Провайдер не ответил или ответил не по формату."""


class LLMProvider(Protocol):
    name: str

    def complete(self, system: str, user: str, timeout_s: float) -> str: ...


# --------------------------------------------------------------------------- провайдеры


class YandexGPTProvider:
    name = "yandexgpt"

    def __init__(self, settings: LLMSettings, client: httpx.Client | None = None) -> None:
        self._s = settings
        self._client = client or httpx.Client()

    def complete(self, system: str, user: str, timeout_s: float) -> str:
        s = self._s
        assert s.yandex_api_key is not None
        body = {
            "modelUri": f"gpt://{s.yandex_folder_id}/{s.yandex_model}",
            "completionOptions": {"stream": False, "temperature": 0, "maxTokens": 2000},
            "messages": [{"role": "system", "text": system}, {"role": "user", "text": user}],
        }
        headers = {"Authorization": f"Api-Key {s.yandex_api_key.get_secret_value()}", "x-folder-id": s.yandex_folder_id or ""}
        try:
            r = self._client.post(s.yandex_url, json=body, headers=headers, timeout=timeout_s)
            r.raise_for_status()
            return str(r.json()["result"]["alternatives"][0]["message"]["text"])
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as e:
            raise LLMError(f"YandexGPT: {e.__class__.__name__}") from e


class GigaChatProvider:
    name = "gigachat"

    def __init__(self, settings: LLMSettings, client: httpx.Client | None = None) -> None:
        self._s = settings
        verify: bool | str = settings.gigachat_verify
        if verify in ("true", "false"):
            verify = verify == "true"
        self._client = client or httpx.Client(verify=verify)
        self._token: str | None = None
        self._token_exp = 0.0

    def _access_token(self, timeout_s: float) -> str:
        if self._token and time.monotonic() < self._token_exp:
            return self._token
        s = self._s
        assert s.gigachat_auth_key is not None
        r = self._client.post(
            s.gigachat_oauth_url,
            data={"scope": s.gigachat_scope},
            headers={"Authorization": f"Basic {s.gigachat_auth_key.get_secret_value()}", "RqUID": str(uuid.uuid4())},
            timeout=timeout_s,
        )
        r.raise_for_status()
        payload = r.json()
        self._token = str(payload["access_token"])
        # expires_at — миллисекунды эпохи; держим токен с запасом в минуту
        self._token_exp = time.monotonic() + 25 * 60
        return self._token

    def complete(self, system: str, user: str, timeout_s: float) -> str:
        s = self._s
        try:
            token = self._access_token(timeout_s)
            r = self._client.post(
                s.gigachat_url,
                json={
                    "model": s.gigachat_model,
                    "temperature": 0.01,  # GigaChat не принимает строго 0
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout_s,
            )
            r.raise_for_status()
            return str(r.json()["choices"][0]["message"]["content"])
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as e:
            raise LLMError(f"GigaChat: {e.__class__.__name__}") from e


class OpenAICompatProvider:
    """Любой OpenAI-совместимый /chat/completions (открытые модели у облачных хостингов, свой vLLM/Ollama).

    Принимает только задачи из `tasks` (зарубежный адрес не получает резюме без явного решения оператора)."""

    name = "openai_compat"

    def __init__(self, settings: LLMSettings, client: httpx.Client | None = None) -> None:
        self._s = settings
        self._client = client or httpx.Client()
        self.tasks: frozenset[str] | None = settings.openai_task_set

    def complete(self, system: str, user: str, timeout_s: float) -> str:
        s = self._s
        assert s.openai_api_key is not None and s.openai_base_url is not None
        body = {
            "model": s.openai_model,
            "temperature": 0,
            "max_tokens": 2000,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {"Authorization": f"Bearer {s.openai_api_key.get_secret_value()}"}
        try:
            r = self._client.post(s.openai_base_url.rstrip("/") + "/chat/completions", json=body, headers=headers, timeout=timeout_s)
            r.raise_for_status()
            return str(r.json()["choices"][0]["message"]["content"])
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as e:
            raise LLMError(f"OpenAI-compat: {e.__class__.__name__}") from e


def providers_from_settings(settings: LLMSettings) -> list[LLMProvider]:
    """Цепочка по настройкам: auto → YandexGPT, GigaChat, затем OpenAI-совместимый (только настроенные)."""
    chain: list[LLMProvider] = []
    if settings.provider in ("auto", "yandexgpt") and settings.yandex_configured:
        chain.append(YandexGPTProvider(settings))
    if settings.provider in ("auto", "gigachat") and settings.gigachat_configured:
        chain.append(GigaChatProvider(settings))
    if settings.provider in ("auto", "openai_compat") and settings.openai_configured:
        chain.append(OpenAICompatProvider(settings))
    return chain


# --------------------------------------------------------------------------- результат


@dataclass(frozen=True)
class LLMField:
    """Поле, предложенное LLM и подтверждённое цитатой: text[start:end] — найденная цитата."""

    field: str
    value: str
    quote: str
    start: int
    end: int
    score: float


@dataclass(frozen=True)
class LLMExtraction:
    provider: str | None  # None — LLM не вызывалась (выключена, нет ключей или все провайдеры упали)
    fields: tuple[LLMField, ...] = ()
    rejected: int = 0  # поля без цитаты или с цитатой, которой нет в тексте
    cached: bool = False
    error: str | None = None
    masked_spans: int = 0

    @property
    def used(self) -> bool:
        return self.provider is not None


@dataclass
class _Cache:
    size: int
    _data: OrderedDict[str, tuple[str, str]] = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, key: str) -> tuple[str, str] | None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            return None

    def put(self, key: str, value: tuple[str, str]) -> None:
        if self.size <= 0:
            return
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.size:
                self._data.popitem(last=False)


class Cassette:
    """Записанные ответы модели: {"version": 1, "entries": {sha256: {"provider", "task", "raw"}}}.

    Запись атомарная (временный файл → replace), чтение — один раз при создании шлюза."""

    VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, str]] = {}
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                entries = data.get("entries") if isinstance(data, dict) else None
                if isinstance(entries, dict):
                    self._entries = {
                        str(k): v for k, v in entries.items() if isinstance(v, dict) and "raw" in v and "provider" in v
                    }
            except (OSError, ValueError) as e:
                log.warning("Кассета LLM %s не читается: %s — работаем без неё", self.path, e)

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: str) -> tuple[str, str] | None:
        with self._lock:
            e = self._entries.get(key)
        return (str(e["provider"]), str(e["raw"])) if e else None

    def put(self, key: str, task: str, provider: str, raw: str) -> None:
        with self._lock:
            self._entries[key] = {"provider": provider, "task": task, "raw": raw}
            payload = {"version": self.VERSION, "entries": dict(sorted(self._entries.items()))}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)


_SYSTEM = (
    "Ты извлекаешь факты из текста резюме на русском языке. Отвечай ТОЛЬКО JSON вида "
    '{"items": [{"field": "...", "value": "...", "quote": "..."}]}. '
    "quote — точная дословная цитата из текста (5–80 символов), на которой основано поле. "
    "Не придумывай: если цитаты нет, поле не возвращай. Символы «*» — скрытые персональные данные, "
    "их не цитируй и не пытайся восстановить."
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_items(raw: str) -> list[dict[str, Any]]:
    """JSON из ответа модели (допускаем обрамление ```json … ```)."""
    m = _JSON_RE.search(raw or "")
    if not m:
        raise LLMError("Ответ без JSON")
    try:
        data = json.loads(m.group(0))
    except ValueError as e:
        raise LLMError("Некорректный JSON") from e
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise LLMError("В JSON нет списка items")
    return [i for i in items if isinstance(i, dict)]


def verify_quote(quote: str, text: str, min_ratio: float, min_len: int) -> tuple[float, int, int] | None:
    """Найти цитату в тексте: (score, start, end) при partial_ratio ≥ порога, иначе None."""
    q = (quote or "").strip()
    if len(q) < min_len or MASK_CHAR * 3 in q:
        return None
    exact = text.find(q)
    if exact >= 0:
        return 100.0, exact, exact + len(q)
    al = fuzz.partial_ratio_alignment(q, text)
    if al is None or al.score < min_ratio:
        return None
    return float(al.score), al.dest_start, al.dest_end


class LLMGateway:
    """Точка входа. Без настроенных провайдеров (умолчание) — всегда «без LLM»."""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        providers: Sequence[LLMProvider] | None = None,
        cassette: Cassette | None = None,
    ) -> None:
        self.settings = settings or LLMSettings()
        self.mode = self.settings.effective_mode
        # replay не ходит в сеть: провайдеры (и ключи) не нужны
        self.providers: list[LLMProvider] = (
            [] if self.mode == "replay" else list(providers) if providers is not None else providers_from_settings(self.settings)
        )
        self._cache = _Cache(self.settings.cache_size)
        if cassette is None and self.mode in ("record", "replay") and self.settings.cassette_path:
            cassette = Cassette(self.settings.cassette_path)
        self.cassette = cassette if self.mode in ("record", "replay") else None

    @property
    def enabled(self) -> bool:
        if self.mode == "none":
            return False
        if self.mode == "replay":
            return self.cassette is not None
        return bool(self.providers)

    def _cache_key(self, task: str, masked: str) -> str:
        return hashlib.sha256(f"{PROMPT_VERSION}\x1f{task}\x1f{masked}".encode()).hexdigest()

    def extract(
        self,
        task: str,
        text: str,
        instructions: str,
        *,
        allowed_fields: Iterable[str],
        extra_pii_spans: Iterable[tuple[int, int]] = (),
    ) -> LLMExtraction:
        """Предложения LLM по полям allowed_fields с проверенными цитатами (смещения — в исходном тексте)."""
        if not self.enabled:
            return LLMExtraction(provider=None)
        s = self.settings
        masked = mask_pii(text[: s.max_input_chars], extra_pii_spans)
        allowed = set(allowed_fields)
        key = self._cache_key(task, masked.text)
        hit = self._cache.get(key)
        if hit is None and self.cassette is not None:
            hit = self.cassette.get(key)
        provider_name: str | None
        if hit is not None:
            provider_name, raw = hit
            cached = True
        elif self.mode == "replay":
            # Промах кассеты = «без LLM»: детерминированный результат правил, в сеть не идём
            return LLMExtraction(provider=None, error="В кассете нет записи для этого текста", masked_spans=masked.count)
        else:
            cached = False
            provider_name, raw, error = self._call_chain(instructions, masked.text, task)
            if provider_name is None:
                return LLMExtraction(provider=None, error=error, masked_spans=masked.count)
            if self.cassette is not None:  # record
                self.cassette.put(key, task, provider_name, raw)
        self._cache.put(key, (provider_name, raw))
        try:
            items = parse_items(raw)
        except LLMError as e:
            return LLMExtraction(provider=provider_name, cached=cached, error=str(e), masked_spans=masked.count)
        fields: list[LLMField] = []
        rejected = 0
        for item in items:
            name, value, quote = str(item.get("field", "")), str(item.get("value", "")).strip(), str(item.get("quote", ""))
            if name not in allowed or not value:
                rejected += 1
                continue
            found = verify_quote(quote, masked.text, s.min_quote_ratio, s.min_quote_len)
            if found is None:
                rejected += 1
                continue
            score, start, end = found
            fields.append(LLMField(name, value, text[start:end], start, end, score))
        return LLMExtraction(
            provider=provider_name, fields=tuple(fields), rejected=rejected, cached=cached, masked_spans=masked.count
        )

    def _call_chain(self, instructions: str, masked_text: str, task: str = "") -> tuple[str | None, str, str | None]:
        deadline = time.monotonic() + self.settings.timeout_s
        user = f"{instructions}\n\nТекст:\n{masked_text}"
        last_error: str | None = None
        for p in self.providers:
            allowed_tasks = getattr(p, "tasks", None)  # провайдер с ограничением задач (зарубежный) пропускает чужие
            if allowed_tasks is not None and task not in allowed_tasks:
                continue
            remaining = deadline - time.monotonic()
            if remaining < 1.0:
                last_error = "Истёк общий таймаут LLM"
                break
            try:
                return p.name, p.complete(_SYSTEM, user, remaining), None
            except LLMError as e:
                last_error = str(e)
                log.warning("LLM-провайдер %s недоступен: %s — пробуем следующий", p.name, e)
            except Exception as e:  # сбой провайдера не должен ронять разбор: дальше работают правила
                last_error = f"{p.name}: {e.__class__.__name__}"
                log.warning("LLM-провайдер %s упал: %s", p.name, e)
        return None, "", last_error


@lru_cache(maxsize=1)
def get_gateway() -> LLMGateway:
    """Процессный шлюз (кэш ответов общий на процесс)."""
    return LLMGateway()
