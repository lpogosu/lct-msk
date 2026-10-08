"""Выдача, отзыв и проверка паспортов навыков (docs/CREDENTIALS.md)."""

from __future__ import annotations

import base64
import io
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import qrcode
from sqlalchemy import Sequence, func, select
from sqlalchemy.orm import Session

from app.core.consents import has_active_consent
from app.core.errors import bad_request, conflict, not_found
from app.core.settings import Settings
from app.credentials import builders, di, keys
from app.credentials.config import CredentialsConfig, get_credentials_config
from app.credentials.models import IssuedCredential
from app.credentials.status_list import is_revoked, status_list_credential
from app.models.candidate import CandidateProfile
from app.models.enums import ConsentPurpose
from app.models.users import User

KIND_CATEGORY = "category"
KIND_FSP = "fsp"


# --- что можно выпустить ---------------------------------------------------------------------


@dataclass(frozen=True)
class Offer:
    kind: str
    ref: str  # category: код специализации; fsp: id результата реестра (видит только владелец)
    title: str
    detail: str


def _fsp_results(db: Session, user_id: uuid.UUID, now: datetime) -> list[tuple[str, dict[str, Any]]]:
    """Подтверждённые, не скрытые результаты ФСП кандидата — только при согласии на обогащение и привязке."""
    from app.fsp import vss
    from app.models.fsp import FspLink
    from app.services.fsp_link import stored_profile

    if not has_active_consent(db, user_id, ConsentPurpose.fsp_enrichment):
        return []
    link = db.scalar(select(FspLink).where(FspLink.user_id == user_id))
    prof = stored_profile(link, now) if link is not None else None
    if prof is None:
        return []
    ordered = sorted(
        prof.counted_results, key=lambda r: (-vss.result_points(r, now).points, -r.end_date.toordinal(), r.result_id)
    )
    return [(r.result_id, vss.result_public_item(r).model_dump(mode="json")) for r in ordered]


def _confirmed_snapshots(db: Session, user_id: uuid.UUID, now: datetime) -> list[Any]:
    from app.services.profile.service import qualification_snapshots

    return [s for s in qualification_snapshots(db, user_id, now) if s.confirmed and s.grade and s.visible and s.category]


def available_offers(db: Session, user: User, now: datetime) -> list[Offer]:
    out = [
        Offer(KIND_CATEGORY, s.specialization, str((s.category or {}).get("name")), "Категория, подтверждённая тестом")
        for s in _confirmed_snapshots(db, user.id, now)
    ]
    for rid, item in _fsp_results(db, user.id, now):
        out.append(Offer(KIND_FSP, rid, str(item.get("text") or item.get("discipline_label")), "Достижение из реестра ФСП"))
    return out


# --- выдача ----------------------------------------------------------------------------------


def _new_code(cfg: CredentialsConfig) -> str:
    return cfg.code_prefix + secrets.token_hex((32 - len(cfg.code_prefix)) // 2)


def issue(
    db: Session,
    user: User,
    settings: Settings,
    *,
    kind: str,
    ref: str,
    include_name: bool,
    now: datetime,
) -> IssuedCredential:
    cfg = get_credentials_config()
    active = db.scalar(
        select(func.count()).select_from(IssuedCredential).where(IssuedCredential.user_id == user.id, IssuedCredential.revoked_at.is_(None))
    )
    if (active or 0) >= cfg.max_active:
        raise conflict("credentials_limit", "Слишком много действующих паспортов: отзовите ненужные")
    name = None
    if include_name:
        prof = db.get(CandidateProfile, user.id)
        name = (prof.full_name if prof else None) or None
        if not name:
            raise bad_request("name_missing", "Чтобы указать имя в паспорте, заполните ФИО в профиле")

    key = keys.signing_key(settings)
    status_index = db.scalar(select(Sequence("credential_status_idx").next_value()))
    assert status_index is not None
    if status_index >= cfg.status_size_bits:
        raise conflict("status_list_full", "Реестр отзыва заполнен")
    cred_id = f"urn:uuid:{uuid.uuid4()}"
    code = _new_code(cfg)
    subject = builders.subject_id(settings, cfg, user.id)
    method_url = f"{builders.api_base(settings)}{cfg.raw['method_path']}"

    if kind == KIND_CATEGORY:
        from app.testing.irt import load_scale

        snap = next((s for s in _confirmed_snapshots(db, user.id, now) if s.specialization == ref), None)
        if snap is None:
            raise not_found("category_not_confirmed", "Категория не подтверждена тестом — паспорт выпустить нельзя")
        scale = load_scale()
        sem = scale.score_per_theta * snap.se if snap.se is not None else None
        doc, valid_until = builders.category_credential(
            settings,
            cfg,
            cred_id=cred_id,
            status_index=status_index,
            now=now,
            subject=subject,
            name=name,
            category_code=str(snap.category["code"]),
            category_name=str(snap.category["name"]),
            score=snap.score,
            sem=sem,
            tested_at=snap.confirmed_at,
            method_url=method_url,
        )
        title = str(snap.category["name"])
    elif kind == KIND_FSP:
        item = next((it for rid, it in _fsp_results(db, user.id, now) if rid == ref), None)
        if item is None:
            raise not_found("fsp_result_not_found", "Достижение ФСП не найдено или скрыто")
        doc, valid_until = builders.fsp_credential(
            settings, cfg, cred_id=cred_id, status_index=status_index, now=now, subject=subject, name=name, item=item, method_url=method_url
        )
        title = str(doc["name"]).removeprefix("Достижение ФСП: ")
    else:
        raise bad_request("bad_kind", "Неизвестный вид паспорта")

    signed = di.sign_document(doc, key, keys.verification_method_id(settings), now)
    row = IssuedCredential(
        user_id=user.id,
        code=code,
        kind=kind,
        title=title[:200],
        credential_id=cred_id,
        status_index=status_index,
        document=signed,
        include_name=bool(name),
        valid_until=valid_until,
    )
    db.add(row)
    db.flush()
    return row


def list_for_user(db: Session, user_id: uuid.UUID) -> list[IssuedCredential]:
    return list(
        db.scalars(select(IssuedCredential).where(IssuedCredential.user_id == user_id).order_by(IssuedCredential.created_at.desc()))
    )


def get_owned(db: Session, user_id: uuid.UUID, cred_id: uuid.UUID) -> IssuedCredential:
    row = db.get(IssuedCredential, cred_id)
    if row is None or row.user_id != user_id:
        raise not_found("credential_not_found", "Паспорт не найден")
    return row


def revoke(db: Session, row: IssuedCredential, now: datetime) -> IssuedCredential:
    if row.revoked_at is None:
        row.revoked_at = now
        db.flush()
    return row


def qr_data_uri(url: str) -> str:
    img = qrcode.make(url, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def revoked_indexes(db: Session) -> list[int]:
    return list(db.scalars(select(IssuedCredential.status_index).where(IssuedCredential.revoked_at.is_not(None))))


def build_status_list(db: Session, settings: Settings, now: datetime) -> dict[str, Any]:
    return status_list_credential(settings, get_credentials_config(), keys.signing_key(settings), revoked_indexes(db), now)


# --- проверка --------------------------------------------------------------------------------


@dataclass
class Check:
    id: str
    ok: bool
    label: str
    reason: str | None = None


@dataclass
class VerifyResult:
    valid: bool
    checks: list[Check] = field(default_factory=list)
    summary: dict[str, Any] | None = None
    demo_issuer: bool = True

    @property
    def reasons(self) -> list[str]:
        return [c.reason for c in self.checks if not c.ok and c.reason]


_PROOF_REASONS = {
    "no_proof": "В файле нет цифровой подписи",
    "bad_suite": "Неподдерживаемый вид подписи (ожидается eddsa-jcs-2022)",
    "bad_proof": "Подпись оформлена неверно",
    "bad_signature": "Подпись не совпадает: содержимое файла изменено после выдачи",
}


def _parse_time(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


def _summary(doc: dict[str, Any], *, with_name: bool) -> dict[str, Any]:
    subj = doc.get("credentialSubject") if isinstance(doc.get("credentialSubject"), dict) else {}
    ach = subj.get("achievement") if isinstance(subj.get("achievement"), dict) else {}
    issuer = doc.get("issuer") if isinstance(doc.get("issuer"), dict) else {}
    results = []
    for r in subj.get("result") or []:
        if isinstance(r, dict):
            rid = str(r.get("resultDescription", ""))
            results.append({"kind": "sem" if rid.endswith("#sem") else "score", "value": str(r.get("value", ""))})
    out: dict[str, Any] = {
        "title": str(doc.get("name", ""))[:200],
        "achievement": str(ach.get("name", ""))[:200],
        "issuer_name": str(issuer.get("name", ""))[:200],
        "issuer_id": str(issuer.get("id", ""))[:200],
        "issued_on": str(doc.get("validFrom", ""))[:10],
        "valid_until": str(doc.get("validUntil", ""))[:10] or None,
        "tested_on": str(subj.get("activityEndDate", ""))[:10] or None,
        "results": results,
        "narrative": str((ach.get("criteria") or {}).get("narrative", ""))[:500] if isinstance(ach.get("criteria"), dict) else None,
        "kind": "fsp" if "fsp" in str(ach.get("id", "")) else "category",
    }
    if with_name and isinstance(subj.get("name"), str):
        out["holder_name"] = subj["name"][:200]
    return out


def verify_document(db: Session, settings: Settings, doc: Any, now: datetime, *, with_name: bool) -> VerifyResult:
    """Проверки: структура → издатель → подпись → срок → реестр выдачи и отзыв. valid = все зелёные."""
    checks: list[Check] = []

    def add(cid: str, ok: bool, label: str, reason: str | None = None) -> None:
        checks.append(Check(cid, ok, label, None if ok else reason))

    types = doc.get("type") if isinstance(doc, dict) else None
    structured = (
        isinstance(doc, dict)
        and isinstance(types, list)
        and "VerifiableCredential" in types
        and isinstance(doc.get("credentialSubject"), dict)
        and isinstance(doc.get("issuer"), dict)
    )
    add("format", structured, "Формат: Verifiable Credential / Open Badges", "Это не файл паспорта навыков (ожидается Verifiable Credential)")
    if not structured:
        return VerifyResult(False, checks)

    my_did = keys.issuer_did(settings)
    issuer_id = doc["issuer"].get("id")
    add("issuer", issuer_id == my_did, f"Издатель: {my_did}", "Издатель неизвестен платформе: файл выдан не этой платформой")
    proof = doc.get("proof")
    vm_ok = isinstance(proof, dict) and proof.get("verificationMethod") == keys.verification_method_id(settings)
    if issuer_id == my_did and not vm_ok:
        add("issuer", False, "Ключ подписи издателя", "Подпись сделана неизвестным ключом")
    if issuer_id == my_did and vm_ok:
        pc = di.verify_proof(doc, keys.public_key(settings))
        add("signature", pc.ok, "Подпись Ed25519 (eddsa-jcs-2022) совпадает", _PROOF_REASONS.get(pc.reason or "", "Подпись не прошла проверку"))
        sig_ok = pc.ok
    else:
        add("signature", False, "Подпись Ed25519 (eddsa-jcs-2022) совпадает", "Подпись нельзя проверить: издатель или ключ неизвестны")
        sig_ok = False

    vf, vu = _parse_time(doc.get("validFrom")), _parse_time(doc.get("validUntil"))
    if vf is not None and vf > now:
        add("validity", False, "Срок действия", "Срок действия ещё не наступил")
    elif vu is not None and vu < now:
        add("validity", False, "Срок действия", "Срок действия истёк")
    elif vf is None:
        add("validity", False, "Срок действия", "В файле нет даты выдачи")
    else:
        add("validity", True, "Срок действия не истёк")

    cred_id = doc.get("id")
    row = db.scalar(select(IssuedCredential).where(IssuedCredential.credential_id == cred_id)) if isinstance(cred_id, str) else None
    if row is None:
        add("registry", False, "Запись о выдаче в реестре платформы", "В реестре выдачи записи нет: паспорт удалён владельцем или не выдавался")
    else:
        add("registry", True, "Запись о выдаче найдена в реестре")
        status = doc.get("credentialStatus") if isinstance(doc.get("credentialStatus"), dict) else {}
        listed = False
        try:
            lst = build_status_list(db, settings, now)["credentialSubject"]["encodedList"]
            listed = is_revoked(lst, int(status.get("statusListIndex", row.status_index)))
        except (ValueError, TypeError):
            listed = row.revoked_at is not None
        add("revocation", row.revoked_at is None and not listed, "Не отозван", "Отозван владельцем: паспорт больше не действует")

    valid = all(c.ok for c in checks) and sig_ok
    # Содержимое с неверной подписью не показываем: это данные, которым нельзя верить
    return VerifyResult(valid, checks, _summary(doc, with_name=with_name) if sig_ok else None)


def verify_code(db: Session, settings: Settings, code: str, now: datetime) -> VerifyResult | None:
    row = db.scalar(select(IssuedCredential).where(IssuedCredential.code == code))
    if row is None:
        return None
    return verify_document(db, settings, row.document, now, with_name=False)


# --- карточка работодателя -------------------------------------------------------------------


def passport_summary(db: Session, candidate_id: uuid.UUID, now: datetime) -> dict[str, Any] | None:
    """Для раскрытой карточки: «Паспорт проверен ✓» — у кандидата есть действующий (не отозван, срок не истёк) паспорт
    из реестра выдачи. Подписи здесь не пересчитываем: записи пишет только сама платформа при выдаче.
    Содержимое (баллы, имя) работодателю не отдаём — оно в файле у кандидата; здесь факт, число и названия."""
    rows = [r for r in list_for_user(db, candidate_id) if r.revoked_at is None and r.valid_until > now]
    if not rows:
        return None
    return {
        "verified": True,
        "count": len(rows),
        "label": "Паспорт проверен ✓",
        "titles": [r.title for r in rows][:5],
        "issuer": get_credentials_config().issuer_name,
    }
