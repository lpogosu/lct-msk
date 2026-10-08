"""Сборка несигнированных OpenBadgeCredential (Open Badges 3.0 поверх W3C VC Data Model 2.0).

Минимизация ПДн: субъект — псевдонимный id (HMAC от секрета платформы, без ФИО, email, ФСП ID, id пользователя);
имя добавляется только по явному выбору кандидата. В ФСП-паспорт попадают лишь поля из config/credentials.yaml
(fsp.allowed_fields): без ЕКП, команды, точного места, ссылки на протокол.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta
from typing import Any

from app.core.settings import Settings
from app.credentials.config import CredentialsConfig
from app.credentials.di import iso_z
from app.credentials.keys import issuer_did

VC_CONTEXT = [
    "https://www.w3.org/ns/credentials/v2",
    "https://purl.imsglobal.org/spec/ob/v3p0/context-3.0.3.json",
]


def subject_id(settings: Settings, cfg: CredentialsConfig, user_id: uuid.UUID) -> str:
    key = settings.pseudonym_secret.get_secret_value().encode()
    digest = hmac.new(key, f"credential-subject:{user_id}".encode(), hashlib.sha256).hexdigest()
    sub = cfg.raw["subject"]
    return f"{sub['id_prefix']}{digest[: int(sub['id_hex_len'])]}"


def api_base(settings: Settings) -> str:
    return settings.public_base_url.rstrip("/")


def status_list_url(settings: Settings, cfg: CredentialsConfig) -> str:
    return f"{api_base(settings)}/api/credentials/status/{cfg.status_list_id}"


def verify_url(settings: Settings, code: str) -> str:
    return f"{api_base(settings)}/v/{code}"


def _issuer(settings: Settings, cfg: CredentialsConfig) -> dict[str, Any]:
    return {
        "id": issuer_did(settings),
        "type": ["Profile"],
        "name": cfg.issuer_name,
        "description": cfg.issuer_description,
        "url": api_base(settings),
    }


def _envelope(
    settings: Settings,
    cfg: CredentialsConfig,
    *,
    cred_id: str,
    name: str,
    description: str,
    status_index: int,
    now: datetime,
    subject: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> tuple[dict[str, Any], datetime]:
    valid_until = now + timedelta(days=cfg.validity_days)
    doc = {
        "@context": list(VC_CONTEXT),
        "id": cred_id,
        "type": ["VerifiableCredential", "OpenBadgeCredential"],
        "name": name,
        "description": description,
        "issuer": _issuer(settings, cfg),
        "validFrom": iso_z(now),
        "validUntil": iso_z(valid_until),
        "credentialSubject": subject,
        "credentialStatus": {
            "id": f"{status_list_url(settings, cfg)}#{status_index}",
            "type": "BitstringStatusListEntry",
            "statusPurpose": "revocation",
            "statusListIndex": str(status_index),
            "statusListCredential": status_list_url(settings, cfg),
        },
        "evidence": evidence,
    }
    return doc, valid_until


def category_credential(
    settings: Settings,
    cfg: CredentialsConfig,
    *,
    cred_id: str,
    status_index: int,
    now: datetime,
    subject: str,
    name: str | None,
    category_code: str,
    category_name: str,
    score: int | None,
    sem: float | None,
    tested_at: datetime | None,
    method_url: str,
) -> tuple[dict[str, Any], datetime]:
    scale = cfg.raw["category"]["score_scale"]
    ach_id = f"urn:vzaimno:achievement:category:{category_code.replace(':', '-')}"
    descs = [
        {
            "id": f"{ach_id}#score",
            "type": ["ResultDescription"],
            "name": scale["name"],
            "resultType": "ScaleScore",
            "valueMin": str(scale["min"]),
            "valueMax": str(scale["max"]),
        },
        {
            "id": f"{ach_id}#sem",
            "type": ["ResultDescription"],
            "name": cfg.raw["category"]["sem_name"],
            "resultType": "RawScore",
        },
    ]
    results: list[dict[str, Any]] = []
    if score is not None:
        results.append({"type": ["Result"], "resultDescription": f"{ach_id}#score", "value": str(score)})
    if sem is not None:
        results.append({"type": ["Result"], "resultDescription": f"{ach_id}#sem", "value": f"{sem:.0f}"})
    achievement = {
        "id": ach_id,
        "type": ["Achievement"],
        "achievementType": cfg.raw["category"]["achievement_type"],
        "name": category_name,
        "description": "Категория «специализация × грейд», подтверждённая адаптивным тестом платформы «Взаимно».",
        "criteria": {"narrative": "Адаптивный тест с одноразовыми заданиями; грейд присваивается при достаточной уверенности оценки."},
        "resultDescription": descs,
    }
    subj: dict[str, Any] = {"id": subject, "type": ["AchievementSubject"], "achievement": achievement}
    if name:
        subj["name"] = name
    if results:
        subj["result"] = results
    if tested_at is not None:
        subj["activityEndDate"] = iso_z(tested_at)
    evidence = [
        {
            "id": method_url,
            "type": ["Evidence"],
            "name": "Методика тестирования",
            "description": "Как устроены тест, шкала и погрешность измерения.",
        }
    ]
    return _envelope(
        settings,
        cfg,
        cred_id=cred_id,
        name=f"Категория: {category_name}",
        description=cfg.disclaimer,
        status_index=status_index,
        now=now,
        subject=subj,
        evidence=evidence,
    )


def fsp_credential(
    settings: Settings,
    cfg: CredentialsConfig,
    *,
    cred_id: str,
    status_index: int,
    now: datetime,
    subject: str,
    name: str | None,
    item: dict[str, Any],
    method_url: str,
) -> tuple[dict[str, Any], datetime]:
    allowed = {k: item.get(k) for k in cfg.fsp_allowed_fields if item.get(k) not in (None, "")}
    ach_key = hashlib.sha256("|".join(str(allowed.get(k)) for k in cfg.fsp_allowed_fields).encode()).hexdigest()[:16]
    title = " · ".join(str(allowed[k]) for k in ("outcome", "discipline_label", "year") if k in allowed) or "Достижение ФСП"
    narrative_bits = [f"{k}: {v}" for k, v in allowed.items()]
    achievement = {
        "id": f"urn:vzaimno:achievement:fsp:{ach_key}",
        "type": ["Achievement"],
        "achievementType": cfg.raw["fsp"]["achievement_type"],
        "name": title,
        "description": "Достижение из реестра ФСП (демонстрационные данные), обобщённая запись без персональных данных.",
        "criteria": {"narrative": "; ".join(narrative_bits)},
    }
    subj: dict[str, Any] = {"id": subject, "type": ["AchievementSubject"], "achievement": achievement}
    if name:
        subj["name"] = name
    evidence = [
        {
            "id": method_url,
            "type": ["Evidence"],
            "name": "Источник и правила",
            "description": "Данные получены из реестра ФСП (демо-мок); платформа заверяет факт выписки, а не результат соревнования.",
        }
    ]
    return _envelope(
        settings,
        cfg,
        cred_id=cred_id,
        name=f"Достижение ФСП: {title}",
        description=cfg.disclaimer,
        status_index=status_index,
        now=now,
        subject=subj,
        evidence=evidence,
    )
