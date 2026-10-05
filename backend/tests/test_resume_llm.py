"""Шлюз LLM при разборе резюме (концепция §3.1.1): по умолчанию выключен; ПДн маскируются ДО вызова;
поле без цитаты (partial_ratio < 90) отбрасывается; LLM только добавляет элементы справочника;
цепочка YandexGPT → GigaChat → без LLM; кэш по sha256; общий таймаут."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.llm.gateway import LLMError, LLMGateway, verify_quote
from app.llm.masking import MASK_CHAR, mask_pii
from app.llm.settings import LLMSettings
from app.services.resume import parse_resume

FIX = Path(__file__).parent / "fixtures" / "resumes"
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


class FakeProvider:
    def __init__(self, name: str, answer: str | None = None, fail: bool = False) -> None:
        self.name = name
        self.answer = answer
        self.fail = fail
        self.calls: list[str] = []

    def complete(self, system: str, user: str, timeout_s: float) -> str:
        self.calls.append(user)
        assert timeout_s <= 20.0
        if self.fail:
            raise LLMError(f"{self.name}: недоступен")
        return self.answer or '{"items": []}'


def _gw(*providers: FakeProvider) -> LLMGateway:
    return LLMGateway(LLMSettings(_env_file=None, provider="auto"), providers=list(providers))


def test_disabled_by_default_never_calls_network():
    gw = LLMGateway(LLMSettings(_env_file=None))
    assert gw.enabled is False
    res = gw.extract("resume", "Python", "…", allowed_fields=("skill",))
    assert res.used is False and res.fields == ()
    _, parse = parse_resume((FIX / "backend_middle.pdf").read_bytes(), now=NOW, gateway=gw)
    assert parse.llm.used is False


def test_mask_keeps_length_and_hides_contacts_and_birth_date():
    text = "Иван\nEmail: ivan@example.com\nТел.: +7 (915) 204-18-73\nДата рождения: 01.01.1990\nt.me/handle_x\nPython"
    m = mask_pii(text, extra_spans=[(0, 4)])
    assert len(m.text) == len(text)
    for secret in ("Иван", "ivan@example.com", "204-18-73", "1990", "handle_x"):
        assert secret not in m.text
    assert m.text.endswith("Python")
    assert MASK_CHAR in m.text


def test_llm_adds_only_dictionary_items_with_verified_quotes_and_pii_is_masked():
    answer = json.dumps(
        {
            "items": [
                # Смысловое добавление: цитата есть в тексте → поле принимается с источником llm и цитатой,
                # кандидат видит подсветку и подтверждает или отклоняет его (экран К7)
                {"field": "skill", "value": "Prometheus", "quote": "мониторинг в Grafana"},
                {"field": "skill", "value": "Grafana", "quote": "мониторинг в Grafana"},
                {"field": "skill", "value": "Rust", "quote": "писал на Rust"},  # цитаты нет в тексте
                {"field": "skill", "value": "Квантовая магия", "quote": "Микросервисы на Python"},  # не из справочника
                {"field": "email", "value": "x@y.ru", "quote": "Почта"},  # поле не разрешено
                {"field": "soft_skill", "value": "Системное мышление", "quote": "системное мышление"},
            ]
        },
        ensure_ascii=False,
    )
    yandex = FakeProvider("yandexgpt", answer)
    _, parse = parse_resume((FIX / "fullstack_senior.pdf").read_bytes(), now=NOW, gateway=_gw(yandex))
    assert parse.llm.used is True and parse.llm.provider == "yandexgpt"
    sent = yandex.calls[0]
    # ПДн в запросе к модели нет: ФИО, email, телефон, дата рождения замаскированы
    for secret in ("Шаблонов", "mikhail.shablonov", "79035550199", "14.03.1990", "shablonov-dev"):
        assert secret not in sent
    assert parse.llm.rejected >= 3
    for item in parse.skills + parse.soft_skills:
        assert item.quote.text  # у каждого — цитата
    # Rust не добавлен (цитаты нет в тексте); добавленное моделью помечено источником llm и несёт цитату
    assert "rust" not in {s.id for s in parse.skills}
    prom = next(s for s in parse.skills if s.id == "prometheus")
    assert prom.source == "llm" and prom.quote.text == "мониторинг в Grafana"
    assert parse.llm.added == 1  # системное мышление правила уже нашли — дубля нет


def test_every_mention_of_the_name_is_masked_before_llm():
    """ФИО маскируется везде, а не только в шапке: подпись, отзывы, падежные формы."""
    from app.services.resume.extract import ExtractedText
    from app.services.resume.parser import parse_text

    text = (
        "Кортежев Денис Павлович\nBackend-разработчик\n\nОпыт работы\n03.2021 — настоящее время\n"
        "Разработчик Python, FastAPI. Денис вёл команду из трёх человек.\n\nРекомендации\n"
        "«С Денисом Павловичем приятно работать» — руководитель о Кортежеве.\nПодпись: ДЕНИС КОРТЕЖЕВ\n"
    )
    yandex = FakeProvider("yandexgpt", '{"items": []}')
    parse = parse_text(ExtractedText(text, 1, (0,)), now=NOW, gateway=_gw(yandex))
    assert parse.full_name is not None and parse.full_name.value == "Кортежев Денис Павлович"
    sent = yandex.calls[0]
    for secret in ("Кортежев", "КОРТЕЖЕВ", "Денис", "ДЕНИС", "Павлович"):
        assert secret not in sent, secret
    assert "FastAPI" in sent and "Разработчик" in sent  # остальной текст не тронут


def test_llm_item_added_with_llm_source_when_rules_missed_it():
    # Роль, которую словарь не находит («Архитектура складской системы»), — с цитатой и источником llm
    gw = _gw(FakeProvider("yandexgpt", json.dumps({"items": [{"field": "role", "value": "Архитектор", "quote": "Архитектура складской системы"}]}, ensure_ascii=False)))
    _, parse = parse_resume((FIX / "fullstack_senior.pdf").read_bytes(), now=NOW, gateway=gw)
    added = [r for r in parse.roles if r.source == "llm"]
    assert added and added[0].id == "architect"
    assert added[0].quote.text.startswith("Архитектура")
    assert parse.llm.added >= 1


def test_chain_falls_back_to_gigachat_then_to_rules_and_caches():
    yandex = FakeProvider("yandexgpt", fail=True)
    giga = FakeProvider("gigachat", '{"items": []}')
    gw = _gw(yandex, giga)
    r1 = gw.extract("resume", "Опыт: Python и FastAPI", "Найди навыки", allowed_fields=("skill",))
    assert r1.provider == "gigachat" and not r1.cached
    r2 = gw.extract("resume", "Опыт: Python и FastAPI", "Найди навыки", allowed_fields=("skill",))
    assert r2.cached and len(giga.calls) == 1  # кэш по sha256 замаскированного текста
    dead = _gw(FakeProvider("yandexgpt", fail=True), FakeProvider("gigachat", fail=True))
    r3 = dead.extract("resume", "Python", "…", allowed_fields=("skill",))
    assert r3.used is False and r3.error  # детерминированный fallback: результат правил не меняется


def test_record_then_replay_cassette_without_network(tmp_path):
    """DECISIONS п. 23: record пишет ответы в кассету, replay воспроизводит их без сети и ключей."""
    from app.llm.gateway import Cassette

    cassette = tmp_path / "llm" / "demo.json"
    answer = json.dumps({"items": [{"field": "skill", "value": "Kafka", "quote": "очередь сообщений"}]}, ensure_ascii=False)
    yandex = FakeProvider("yandexgpt", answer)
    rec = LLMGateway(LLMSettings(_env_file=None, provider="auto", mode="record", cassette_path=str(cassette)), providers=[yandex])
    text = "Сервис платежей: очередь сообщений, Python. Почта hr@example.com"
    r1 = rec.extract("brief_skills", text, "…", allowed_fields=("skill",))
    assert r1.provider == "yandexgpt" and r1.fields[0].quote == "очередь сообщений"
    stored = json.loads(cassette.read_text(encoding="utf-8"))
    assert len(stored["entries"]) == 1 and "hr@example.com" not in cassette.read_text(encoding="utf-8")

    # replay: ни провайдеров, ни ключей; тот же ответ из кассеты
    rep = LLMGateway(LLMSettings(_env_file=None, mode="replay", cassette_path=str(cassette)))
    assert rep.enabled and rep.providers == [] and len(rep.cassette) == 1
    r2 = rep.extract("brief_skills", text, "…", allowed_fields=("skill",))
    assert r2.provider == "yandexgpt" and r2.cached and r2.fields == r1.fields
    # Промах кассеты — «без LLM», в сеть не идём
    miss = rep.extract("brief_skills", "Другой бриф про Go", "…", allowed_fields=("skill",))
    assert miss.used is False and miss.error
    assert len(yandex.calls) == 1
    # Без кассеты replay выключен; режим none выключен даже с провайдерами
    assert LLMGateway(LLMSettings(_env_file=None, mode="replay")).enabled is False
    assert LLMGateway(LLMSettings(_env_file=None, provider="auto", mode="none"), providers=[yandex]).enabled is False
    assert len(Cassette(tmp_path / "missing.json")) == 0


def test_quote_verification_threshold():
    text = "Разрабатывал сервисы на FastAPI и PostgreSQL"
    assert verify_quote("сервисы на FastAPI", text, 90, 3)[0] == 100.0
    assert verify_quote("сервисы на FastAPl", text, 90, 3) is not None  # опечатка — partial_ratio ≥ 90
    assert verify_quote("писал на Rust и C++", text, 90, 3) is None
    assert verify_quote("***", text, 90, 3) is None
