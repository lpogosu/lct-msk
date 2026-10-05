"""Кто что видит (концепция §4.5): слепая проекция без ПДн, раскрытие по согласию, псевдоним, антифейк."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from app.contact.antifraud import check_text
from app.contact.disclosure import active_disclosure_for, grant_disclosure
from app.contact.pseudonym import pseudonym, unique_pseudonym
from app.contact.settings import get_contact_config
from app.contact.visibility import (
    DisclosureRequired,
    blind_view,
    disclosed_view,
    employer_view,
    pii_keys_in,
)

NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)

PII_VALUES = [
    "Сидорова Анна Павловна",
    "anna.sidorova@example.org",
    "+7 999 123-45-67",
    "@anna_sid",
    "https://files.example/resume.pdf",
    "ООО «Ромашка-Софт»",
    "МГТУ",
    "Химки",
    "ЕКП-2025-77134",
    "Команда «Байт»",
    "https://fsp.example/protocol/77134",
    "1999",
    "female",
]


def profile(**overrides):
    base = {
        "candidate_id": "cand-1",
        "category": {"code": "backend_python:middle", "name": "Backend · Middle"},
        "test_date": "2026-09-14",
        "skills": [
            {"skill": "FastAPI", "source": "test", "module": "http_api", "level": "strong"},
            {
                "skill": "Kafka",
                "source": "declared",
                "email": "anna.sidorova@example.org",
            },  # мусор во вложенности
        ],
        "tasks": [{"title": "Идемпотентность платежей", "score": "4/5"}],
        "test": {
            "score": 542,
            "theta": 0.42,
            "se": 0.21,
            "percentile": 0.93,
            "interval": [500, 584],
            "modules": {"sql": 0.9},
        },
        "fsp": [
            {
                "discipline": "Продуктовое программирование",
                "level": "всероссийские",
                "year": 2025,
                "place": 2,
                "team": "Команда «Байт»",
                "ekp": "ЕКП-2025-77134",
                "role": "backend",
                "protocol_url": "https://fsp.example/protocol/77134",
            },
        ],
        "has_fsp": True,
        "salary_min": 250_000,
        "salary_visibility": "signal",
        "region": "Московская область",
        "city": "Химки",
        "full_name": "Сидорова Анна Павловна",
        "photo_url": "https://files.example/photo.jpg",
        "email": "anna.sidorova@example.org",
        "phone": "+7 999 123-45-67",
        "telegram": "@anna_sid",
        "resume": "https://files.example/resume.pdf",
        "current_employer": "ООО «Ромашка-Софт»",
        "university": "МГТУ",
        "birth_year": 1999,
        "gender": "female",
        "age": 27,
        "secret_note": "не из матрицы — не должно уйти никуда",
    }
    base.update(overrides)
    return base


def disclosure(fields=("full_name", "email"), company="co-1"):
    return grant_disclosure(
        candidate_id="cand-1",
        company_id=company,
        basis="invitation",
        basis_id="inv-1",
        at=NOW - timedelta(hours=1),
        fields=fields,
    )


# --- слепая проекция ----------------------------------------------------------------------------------


def test_blind_view_contains_zero_pii_fields():
    view = blind_view(profile(), pseudonym="К-4821", offer_salary_to=280_000)
    assert pii_keys_in(view) == set()
    dump = json.dumps(view, ensure_ascii=False)
    for value in PII_VALUES:
        assert value not in dump, value
    assert "secret_note" not in view and "candidate_id" not in view


def test_blind_view_shows_evidence_in_generalized_form():
    view = blind_view(profile(), pseudonym="К-4821", offer_salary_to=280_000)
    assert view["view"] == "blind" and view["pseudonym"] == "К-4821"
    assert view["category"]["name"] == "Backend · Middle"
    assert view["test"] == {"band": "топ-10%", "interval": [500, 584]}  # точного балла нет
    assert view["fsp"] == [
        {
            "discipline": "Продуктовое программирование",
            "level": "всероссийские",
            "year": 2025,
            "role": "backend",
            "place_band": "топ-3",
        }
    ]
    assert view["region"] == "Московская область"
    assert view["salary_min"] == {"signal": "в вилке ✓"}  # по умолчанию только значок
    assert view["skills"][0]["skill"] == "FastAPI"


def test_salary_signal_above_and_exact():
    assert blind_view(profile(), pseudonym="К-1", offer_salary_to=240_000)["salary_min"] == {
        "signal": "выше вилки"
    }
    exact = blind_view(profile(salary_visibility="exact"), pseudonym="К-1", offer_salary_to=280_000)
    assert exact["salary_min"] == {"signal": "в вилке ✓", "value": 250_000}


@pytest.mark.parametrize(
    "pct,band", [(0.95, "топ-10%"), (0.8, "топ-25%"), (0.55, "топ-50%"), (0.3, "категория подтверждена")]
)
def test_test_bands(pct, band):
    view = blind_view(profile(test={"score": 500, "percentile": pct}), pseudonym="К-1")
    assert view["test"]["band"] == band


# --- раскрытие ----------------------------------------------------------------------------------------


def test_disclosed_view_only_allowed_fields_and_never_age_gender():
    view = disclosed_view(profile(), pseudonym="К-4821", company_id="co-1", disclosure=disclosure(), now=NOW)
    assert view["view"] == "disclosed"
    assert view["full_name"] == "Сидорова Анна Павловна" and view["email"] == "anna.sidorova@example.org"
    assert "phone" not in view and "telegram" not in view  # кандидат не разрешил
    assert view["test"]["score"] == 542  # точный балл — после раскрытия
    assert view["fsp"][0]["ekp"] == "ЕКП-2025-77134"
    assert view["city"] == "Химки"
    for never in ("age", "gender", "birth_year", "candidate_id", "secret_note", "salary_visibility"):
        assert never not in view


def test_disclosed_view_refused_without_valid_consent():
    with pytest.raises(DisclosureRequired):
        disclosed_view(profile(), pseudonym="К-1", company_id="co-2", disclosure=disclosure(), now=NOW)
    revoked = disclosure().revoke(NOW - timedelta(minutes=5))
    with pytest.raises(DisclosureRequired):
        disclosed_view(profile(), pseudonym="К-1", company_id="co-1", disclosure=revoked, now=NOW)
    with pytest.raises(DisclosureRequired):
        disclosed_view(
            profile(candidate_id="cand-2"),
            pseudonym="К-1",
            company_id="co-1",
            disclosure=disclosure(),
            now=NOW,
        )


def test_employer_view_switches_on_active_disclosure_only():
    assert employer_view(profile(), pseudonym="К-1", company_id="co-1", now=NOW)["view"] == "blind"
    assert (
        employer_view(profile(), pseudonym="К-1", company_id="co-1", now=NOW, disclosure=disclosure())["view"]
        == "disclosed"
    )
    revoked = disclosure().revoke(NOW)
    view = employer_view(profile(), pseudonym="К-1", company_id="co-1", now=NOW, disclosure=revoked)
    assert view["view"] == "blind" and pii_keys_in(view) == set()
    found = active_disclosure_for([disclosure(company="co-2"), disclosure()], "cand-1", "co-1", NOW)
    assert found is not None and found.company_id == "co-1"


def test_config_forbids_pii_in_blind_projection():
    from pydantic import ValidationError

    from app.contact.settings import ContactConfig
    from app.core.configs import load_config

    raw = json.loads(json.dumps(load_config("contact"), default=str))
    raw["visibility"]["fields"]["email"]["blind"] = "show"
    with pytest.raises(ValidationError):
        ContactConfig.model_validate(raw)


# --- псевдоним ----------------------------------------------------------------------------------------


def test_pseudonym_format_and_determinism():
    p = pseudonym("s3cret", "co-1", "cand-1")
    assert re.fullmatch(r"К-\d{4}", p)
    assert p == pseudonym("s3cret", "co-1", "cand-1")
    ids = [f"cand-{i}" for i in range(20)]
    changed = sum(pseudonym("s3cret", "co-1", c) != pseudonym("other", "co-1", c) for c in ids)
    assert changed >= 18  # без секрета псевдоним не вычислить
    with pytest.raises(ValueError):
        pseudonym("", "co-1", "cand-1")


def test_pseudonym_differs_between_companies():
    ids = [f"cand-{i}" for i in range(50)]
    a = [pseudonym("s3cret", "co-1", c) for c in ids]
    b = [pseudonym("s3cret", "co-2", c) for c in ids]
    assert sum(x != y for x, y in zip(a, b, strict=True)) >= 45
    # разделитель: («co-1», «2») и («co-12», «») — разные входы
    assert pseudonym("s", "co-1", "2") != pseudonym("s", "co-12", "")


def test_unique_pseudonym_resolves_collision():
    base = pseudonym("s3cret", "co-1", "cand-1")
    other = unique_pseudonym("s3cret", "co-1", "cand-1", taken={base})
    assert other != base and re.fullmatch(r"К-\d{4}", other)
    assert unique_pseudonym("s3cret", "co-1", "cand-1", taken=set()) == base


# --- антифейк -----------------------------------------------------------------------------------------

HONEST = (
    "Команда платёжного шлюза ищет бэкенд-разработчика: Python, FastAPI, PostgreSQL. "
    "Делаем Telegram-бота для поддержки и интеграции с банками. Гибрид, Москва."
)


def test_honest_text_has_no_caution():
    result = check_text("Backend Middle", HONEST)
    assert not result.caution and result.badge is None


@pytest.mark.parametrize(
    "text,code",
    [
        ("Перед стартом внесите предоплату 3000 ₽ за обучение", "prepayment"),
        ("Стажировка: оплата за обучение 15 000 ₽", "prepayment"),
        ("Пришлите скан паспорта и СНИЛС для оформления", "documents"),
        ("Нужен номер карты для зачисления аванса", "documents"),
        ("Пишите напрямую: hr.boss@mail.ru", "contacts_in_text"),
        ("Звоните: +7 (999) 123-45-67", "contacts_in_text"),
        ("Связь в телеге @hr_manager_ivan", "contacts_in_text"),
        ("Подробности: t.me/hr_vacancy", "contacts_in_text"),
        ("Анкета тут: bit.ly/3abcDEF", "short_link"),
        ("Анкета: https://clck.ru/38xyz", "short_link"),
    ],
)
def test_caution_rules(text, code):
    result = check_text("Вакансия", HONEST + " " + text)
    assert code in result.codes
    assert result.badge == "Требует осторожности"
    assert all(r for r in result.reasons)


def test_caution_case_and_yo_insensitive():
    assert "prepayment" in check_text("ВНЕСИТЕ ВЗНОС").codes
    assert check_text("Вакансия", "пришлите паспорт").codes == ["documents"]


def test_antifraud_reasons_from_config():
    cfg = get_contact_config()
    assert {r.code for r in cfg.antifraud.rules} == {
        "prepayment",
        "documents",
        "contacts_in_text",
        "short_link",
    }
    assert cfg.antifraud.label == "Требует осторожности"
