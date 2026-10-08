"""Метка «Проверенная компания»: ИНН/ОГРН -> контрольные суммы -> учебный реестр -> название -> домен почты.

Добровольная и информативная: ни одна проверка здесь не блокирует работу компании (приглашения, вакансии, квоты не зависят
от метки). Отзыв методистом/администратором — `revoke`. Текущий уровень читают карточка компании и профиль (`trust_of`).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.configs import load_config
from app.core.errors import bad_request, conflict, not_found
from app.models.company import Company
from app.models.trust import CompanyVerification
from app.trust.checksums import inn_valid, ogrn_valid
from app.trust.registry import EgrulProvider, RegistryRecord, mock_registry

_LEGAL_FORMS = re.compile(r"\b(ооо|оао|зао|пао|ао|ип|нко)\b")
_NOISE = re.compile(r"[^\w\s]", re.UNICODE)
_BASE_CHECKS = (
    "inn_checksum",
    "ogrn_checksum",
    "registry_found",
    "inn_ogrn_match",
    "registry_active",
    "name_match",
    "inn_unique",
)


def cfg() -> dict[str, Any]:
    return load_config("trust")


def _registry() -> EgrulProvider:
    # Единственная реализация в MVP; реальный провайдер ФНС подключается здесь по cfg()["verification"]["registry"]
    return mock_registry()


def _words(name: str | None) -> set[str]:
    text = _NOISE.sub(" ", (name or "").lower().replace("ё", "е"))
    return {w for w in _LEGAL_FORMS.sub(" ", text).split() if w}


def name_ratio(declared: str | None, registry: RegistryRecord) -> float:
    """Доля слов названия компании, найденных в названии реестра (краткое или полное), без орг.-правовой формы."""
    mine = _words(declared)
    if not mine:
        return 0.0
    theirs = _words(registry.name) | _words(registry.short_name)
    return len(mine & theirs) / len(mine)


def level_label(level: str) -> str:
    return cfg()["verification"]["levels"].get(level, {}).get("label", "")


def evaluate(
    db: Session,
    company: Company,
    inn: str,
    ogrn: str,
    *,
    provider: EgrulProvider | None = None,
) -> tuple[str, dict[str, bool], RegistryRecord | None]:
    """(уровень, результаты проверок, запись реестра). Чистая оценка без записи в БД."""
    vc = cfg()["verification"]
    provider = provider or _registry()
    checks: dict[str, bool] = dict.fromkeys(vc["checks"], False)
    checks["inn_checksum"] = inn_valid(inn)
    checks["ogrn_checksum"] = ogrn_valid(ogrn)
    record = provider.lookup(inn) if checks["inn_checksum"] else None
    checks["registry_found"] = record is not None
    if record is not None:
        checks["inn_ogrn_match"] = record.ogrn == ogrn
        checks["registry_active"] = record.status == vc["active_status"]
        checks["name_match"] = name_ratio(company.name, record) >= float(vc["name_match_min_ratio"])
        checks["domain_match"] = (not company.personal_domain) and any(
            company.email_domain == d or company.email_domain.endswith("." + d) for d in record.domains
        )
    taken = db.scalar(
        select(CompanyVerification.company_id).where(
            CompanyVerification.inn == inn,
            CompanyVerification.company_id != company.id,
            CompanyVerification.level != "none",
            CompanyVerification.revoked_at.is_(None),
        )
    )
    checks["inn_unique"] = taken is None
    base = all(checks[k] for k in _BASE_CHECKS)
    level = ("domain" if checks["domain_match"] else "requisites") if base else "none"
    return level, checks, record


def submit(db: Session, company: Company, inn: str, ogrn: str, at: datetime) -> CompanyVerification:
    """Проверяет и сохраняет результат. Неудачная проверка — тоже результат (level none + список непрошедших проверок)."""
    inn, ogrn = (inn or "").strip(), (ogrn or "").strip()
    if not inn.isdigit() or not ogrn.isdigit():
        raise bad_request("requisites_format", "ИНН и ОГРН — только цифры")
    existing = db.get(CompanyVerification, company.id)
    if existing is not None and existing.revoked_at is not None:
        raise conflict(
            "verification_revoked",
            "Метка снята модератором платформы. Чтобы получить её снова, напишите в поддержку",
        )
    level, checks, record = evaluate(db, company, inn, ogrn)
    if existing is None:
        existing = CompanyVerification(company_id=company.id, inn=inn, ogrn=ogrn, submitted_at=at)
        db.add(existing)
    existing.inn, existing.ogrn = inn, ogrn
    existing.level = level
    existing.checks = checks
    existing.registry_name = record.name if record else None
    existing.submitted_at = at
    if level != "none":
        company.inn = inn
    db.flush()
    return existing


def withdraw(db: Session, company: Company) -> None:
    row = db.get(CompanyVerification, company.id)
    if row is not None and row.revoked_at is None:
        db.delete(row)
        db.flush()


def revoke(db: Session, company_id: uuid.UUID, by: uuid.UUID, reason: str, note: str | None, at: datetime) -> CompanyVerification:
    if reason not in cfg()["verification"]["revoke_reasons"]:
        raise bad_request("revoke_reason", "Выберите причину отзыва метки")
    row = db.get(CompanyVerification, company_id)
    if row is None or row.revoked_at is not None:
        raise not_found("verification_not_found", "У компании нет действующей метки")
    row.level = "none"
    row.revoked_at, row.revoked_by, row.revoke_reason = at, by, reason
    row.revoke_note = (note or "").strip()[:300] or None
    db.flush()
    return row


def restore(db: Session, company_id: uuid.UUID) -> None:
    """Снять отзыв: компания сможет подать реквизиты заново (результат старой проверки не возвращается)."""
    row = db.get(CompanyVerification, company_id)
    if row is None or row.revoked_at is None:
        raise not_found("verification_not_found", "Отзыва метки нет")
    db.delete(row)
    db.flush()


def _view(row: CompanyVerification | None) -> dict[str, Any]:
    vc = cfg()["verification"]
    level = row.level if row is not None and row.revoked_at is None else "none"
    return {
        "level": level,
        "label": level_label(level),
        "hint": vc["levels"].get(level, {}).get("hint"),
        "verified": level != "none",
    }


def trust_of(db: Session, company_id: uuid.UUID) -> dict[str, Any]:
    """Публичная часть для кандидата: уровень и подпись. Реквизиты и детали проверок кандидату не показываем."""
    return _view(db.get(CompanyVerification, company_id))


def detail_of(db: Session, company_id: uuid.UUID) -> dict[str, Any]:
    """Для самой компании: плюс реквизиты, список проверок и причина отзыва."""
    row = db.get(CompanyVerification, company_id)
    vc = cfg()["verification"]
    labels = vc["checks"]
    out = _view(row)
    out.update(
        {
            "submitted": row is not None,
            "inn": row.inn if row else None,
            "ogrn": row.ogrn if row else None,
            "registry_name": row.registry_name if row else None,
            "checks": [{"code": k, "label": labels[k], "ok": bool(v)} for k, v in (row.checks if row else {}).items() if k in labels],
            "revoked": bool(row and row.revoked_at),
            "revoke_reason": vc["revoke_reasons"].get(row.revoke_reason) if row and row.revoked_at else None,
        }
    )
    return out


def recheck(db: Session, company: Company, at: datetime) -> None:
    """После смены названия компании метку пересчитываем: переименование не должно сохранять чужое «проверено»."""
    row = db.get(CompanyVerification, company.id)
    if row is None or row.revoked_at is not None or row.level == "none":
        return
    level, checks, record = evaluate(db, company, row.inn, row.ogrn)
    row.level, row.checks, row.registry_name = level, checks, record.name if record else None
    row.submitted_at = at
    db.flush()
