"""Согласия 152-ФЗ: справочник документов и журнал (каждое согласие — отдельная запись).

Модули, которым нужно согласие (тест — test_logging, банк — blind_card, ФСП — fsp_enrichment,
контакт — contact_disclosure), вызывают grant_consent()/has_active_consent() отсюда.
Отзыв contact_disclosure гасит связанные ContactDisclosure и вызывает зарегистрированные обработчики
(модуль контакта переводит приглашение в consent_revoked — концепция §4.3).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.configs import load_config
from app.core.errors import conflict
from app.models.contact import ContactDisclosure
from app.models.enums import ConsentPurpose
from app.models.users import Consent


@dataclass(frozen=True)
class ConsentDocument:
    purpose: ConsentPurpose
    version: str  # меняется при каждой правке текста; старые согласия хранят свою версию
    title: str
    summary: str
    required_for_registration: bool = False
    revocable_in_journal: bool = True


def _contact_consent_version() -> str:
    return str(load_config("contact")["disclosure"]["consent_text_version"])


CONSENT_DOCUMENTS: dict[ConsentPurpose, ConsentDocument] = {
    ConsentPurpose.processing: ConsentDocument(
        ConsentPurpose.processing,
        "2026-10-05",
        "Согласие на обработку персональных данных",
        "Мы обрабатываем email, ответы опроса и теста, данные профиля, чтобы присвоить категорию и "
        "показывать вас работодателям только в слепом виде. Данные хранятся на серверах в РФ.",
        required_for_registration=True,
        # Без этого согласия аккаунт не может работать: его отзыв = удаление аккаунта, а не кнопка журнала
        revocable_in_journal=False,
    ),
    ConsentPurpose.blind_card: ConsentDocument(
        ConsentPurpose.blind_card,
        "2026-10-05",
        "Согласие на показ слепой карточки работодателям",
        "Работодатели увидят псевдоним, категорию, подтверждённые навыки и обобщённые достижения — "
        "без имени, контактов и фото. Без этого согласия вас нет в банке кандидатов.",
    ),
    ConsentPurpose.fsp_enrichment: ConsentDocument(
        ConsentPurpose.fsp_enrichment,
        "2026-10-05",
        "Согласие на обогащение профиля данными ФСП",
        "Мы получим ваши результаты из реестра ФСП по ФСП ID. Отдельные результаты можно скрыть.",
    ),
    ConsentPurpose.contact_disclosure: ConsentDocument(
        ConsentPurpose.contact_disclosure,
        # Версия текста — та же, что пишет модуль контакта в ContactDisclosure (config/contact.yaml):
        # иначе журнал и раскрытие ссылались бы на разные редакции, а grant_consent отвечал бы 409
        _contact_consent_version(),
        "Согласие на передачу контактов компании",
        "Компания увидит контакты и поля профиля, которые вы разрешили. Согласие даётся одной компании "
        "и отзывается в журнале согласий.",
    ),
    ConsentPurpose.test_logging: ConsentDocument(
        ConsentPurpose.test_logging,
        "2026-10-05",
        "Согласие на журналирование событий теста",
        "Во время теста мы записываем уход со вкладки, вставку текста и время ответа. Камера и микрофон "
        "не используются.",
    ),
    ConsentPurpose.calibration: ConsentDocument(
        ConsentPurpose.calibration,
        "2026-10-05",
        "Согласие на использование обезличенных ответов для калибровки",
        "Ваши ответы без имени и контактов помогут уточнить сложность заданий.",
    ),
}

# Цели, которые нельзя выдать через общий журнал: они требуют контекста (компания, приглашение/отклик)
CONTEXTUAL_PURPOSES = frozenset({ConsentPurpose.contact_disclosure})

RevokeHook = Callable[[Session, Consent], None]
_revoke_hooks: list[RevokeHook] = []


def on_consent_revoked(hook: RevokeHook) -> RevokeHook:
    """Декоратор/регистратор: вызвать hook(db, consent) после отзыва согласия (в той же транзакции)."""
    _revoke_hooks.append(hook)
    return hook


def current_version(purpose: ConsentPurpose | str) -> str:
    return CONSENT_DOCUMENTS[ConsentPurpose(purpose)].version


def revocable_in_journal(purpose: ConsentPurpose | str) -> bool:
    return CONSENT_DOCUMENTS[ConsentPurpose(purpose)].revocable_in_journal


def grant_consent(
    db: Session,
    user_id: uuid.UUID,
    purpose: ConsentPurpose | str,
    *,
    text_version: str | None = None,
    scope: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> Consent:
    """Записать новое согласие. Версия текста должна совпадать с актуальной: человек соглашается
    с тем текстом, который видел; устаревшая версия → 409 (перезагрузить страницу)."""
    purpose = ConsentPurpose(purpose)
    actual = current_version(purpose)
    if text_version is not None and text_version != actual:
        raise conflict(
            "consent_outdated",
            "Текст согласия обновился. Обновите страницу и ознакомьтесь с новой редакцией",
            {"purpose": purpose.value, "actual_version": actual},
        )
    consent = Consent(
        user_id=user_id,
        purpose=purpose.value,
        text_version=actual,
        granted_at=clock.now(),
        scope=scope or {},
        ip=ip,
        user_agent=(user_agent or "")[:512] or None,
    )
    db.add(consent)
    db.flush()
    return consent


def active_consents(db: Session, user_id: uuid.UUID, purpose: ConsentPurpose | str | None = None) -> list[Consent]:
    q = select(Consent).where(Consent.user_id == user_id, Consent.revoked_at.is_(None))
    if purpose is not None:
        q = q.where(Consent.purpose == ConsentPurpose(purpose).value)
    return list(db.scalars(q.order_by(Consent.granted_at)))


def has_active_consent(db: Session, user_id: uuid.UUID, purpose: ConsentPurpose | str) -> bool:
    return bool(active_consents(db, user_id, purpose))


def find_active_consent(
    db: Session, user_id: uuid.UUID, purpose: ConsentPurpose | str, text_version: str | None = None
) -> Consent | None:
    """Действующее согласие цели (при text_version — именно этой редакции текста), самое свежее."""
    q = select(Consent).where(
        Consent.user_id == user_id,
        Consent.purpose == ConsentPurpose(purpose).value,
        Consent.revoked_at.is_(None),
    )
    if text_version is not None:
        q = q.where(Consent.text_version == text_version)
    return db.scalars(q.order_by(Consent.granted_at.desc()).limit(1)).first()


def revoke_consent(db: Session, consent: Consent) -> Consent:
    """Отозвать согласие (запись остаётся в журнале с revoked_at). Повторный отзыв → 409.

    Для целей без контекста (blind_card, fsp_enrichment, test_logging, …) отзыв гасит ВСЕ действующие
    записи этой цели: человек отзывает цель, а не строку журнала. Иначе после двойной выдачи
    (повторная галочка на К5 и в К13) отзыв одной записи оставил бы цель действующей.
    contact_disclosure привязано к компании — гасится только эта запись.
    """
    if consent.revoked_at is not None:
        raise conflict("consent_already_revoked", "Это согласие уже отозвано")
    now = clock.now()
    consent.revoked_at = now
    if ConsentPurpose(consent.purpose) not in CONTEXTUAL_PURPOSES:
        db.execute(
            update(Consent)
            .where(
                Consent.user_id == consent.user_id,
                Consent.purpose == consent.purpose,
                Consent.revoked_at.is_(None),
                Consent.id != consent.id,
            )
            .values(revoked_at=now)
            .execution_options(synchronize_session="fetch")
        )
    if consent.purpose == ConsentPurpose.contact_disclosure.value:
        # Контакты у компании снова скрыты: гасим раскрытия, опирающиеся на это согласие
        db.execute(
            update(ContactDisclosure)
            .where(ContactDisclosure.consent_id == consent.id, ContactDisclosure.revoked_at.is_(None))
            .values(revoked_at=now)
        )
    db.flush()
    for hook in list(_revoke_hooks):
        hook(db, consent)
    return consent
