"""Почтовые домены: нормализация email и признак «личный email» (DECISIONS п. 8)."""

from __future__ import annotations

from app.core.configs import load_config


def normalize_email(email: str) -> str:
    """Email храним в нижнем регистре без пробелов по краям (уникальность без учёта регистра)."""
    return email.strip().lower()


def email_domain(email: str) -> str:
    return normalize_email(email).rsplit("@", 1)[-1]


def personal_email_domains() -> frozenset[str]:
    """Личные почтовые домены — из config/contact.yaml (quotas.personal_email_domains).

    Список один на всю платформу: по нему модуль контакта решает, чья квота (аккаунта или домена),
    а платформа — заводить ли отдельную компанию на аккаунт с пометкой «личный email». Два списка
    разошлись бы (компания «личная», а квота считалась бы на весь домен).
    """
    domains = load_config("contact")["quotas"]["personal_email_domains"]
    return frozenset(str(d).strip().lower() for d in domains)


def is_personal_domain(domain: str) -> bool:
    """gmail.com, mail.ru, yandex.ru, … → квота на аккаунт и пометка «личный email» для кандидата."""
    return domain.strip().lower() in personal_email_domains()
