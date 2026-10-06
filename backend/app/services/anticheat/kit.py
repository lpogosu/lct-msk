"""«Подтверждение за 5 минут»: комплект вопросов по ЗАКОНЧЕННОЙ сессии кандидата (killer-features §2.1 п. 2).

Сервис собирает вход ядра (verification.build_kit) из БД: ответы сессии, семена экземпляров (секретные, только на
сервере), сложность семейств, медианы времени и «след двойника» (kind = twin_trace) из улик. Ожидаемые ответы вычисляет
ядро исполнением; в представлении для работодателя нет причин выбора заданий, семейств и сидов — улик там нет никогда
(правило видимости M5). Комплект детерминирован по сессии и кэшируется в памяти процесса: сборка запускает код.

Доступ работодателя — только после раскрытия контакта (активная ContactDisclosure пары «кандидат — компания»);
методисту комплект доступен по сессии, с причинами выбора.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import conflict, not_found
from app.models.contact import Invitation
from app.models.matching import Need
from app.models.testing import ItemFamilyParams, ItemInstance, TestResponse
from app.models.testing import Qualification as QualificationRow
from app.models.testing import TestSession as SessionRow
from app.services.anticheat.models import AnticheatEvidence
from app.services.contact.profiles import active_disclosure_row
from app.services.testing.catalog import median_rt_ms
from app.testing.anticheat.verification import SessionItem, VerificationKit, build_kit

_LOCK = threading.Lock()
_KITS: dict[tuple[uuid.UUID, tuple[str, ...]], VerificationKit] = {}
_KITS_CAP = 256

# Сессии, по которым уже есть решение: из них берётся то, что кандидат реально решил
_DONE_STATUSES = ("DECIDED", "EXTENSION_OFFERED", "FINAL", "UNDER_REVIEW")


def session_items(db: Session, session: SessionRow) -> list[SessionItem]:
    """Зачётные ответы сессии в виде входа ядра. Пробные задания и пропуски (без ответа) не берём."""
    rows = db.execute(
        select(TestResponse, ItemInstance.seed)
        .join(ItemInstance, ItemInstance.id == TestResponse.instance_id)
        .where(
            TestResponse.session_id == session.id,
            TestResponse.answered_at.is_not(None),
            TestResponse.is_pretest.is_(False),
        )
        .order_by(TestResponse.seq)
    ).all()
    kinds = {
        seq: kind
        for seq, kind in db.execute(
            select(AnticheatEvidence.seq, AnticheatEvidence.kind).where(AnticheatEvidence.session_id == session.id)
        )
    }
    fam_ids = {r.family_id for r, _ in rows}
    medians = median_rt_ms(db, fam_ids)
    diff = {
        fid: float(b)
        for fid, b in db.execute(
            select(ItemFamilyParams.family_id, ItemFamilyParams.b).where(
                ItemFamilyParams.family_id.in_(fam_ids), ItemFamilyParams.specialization == session.specialization
            )
        )
    }
    out: list[SessionItem] = []
    for resp, seed in rows:
        if resp.answer is None:
            continue
        out.append(
            SessionItem(
                family_id=resp.family_id,
                seed=int(seed),
                answer=resp.answer,
                correct=bool(resp.is_correct),
                rt_ms=resp.rt_ms,
                median_rt_ms=medians.get(resp.family_id),
                difficulty=diff.get(resp.family_id),
                kind=kinds.get(resp.seq),
            )
        )
    return out


def kit_for_session(db: Session, session: SessionRow, must_skills: list[str] | None = None) -> VerificationKit:
    skills = tuple(must_skills or ())
    key = (session.id, skills)
    with _LOCK:
        cached = _KITS.get(key)
    if cached is not None:
        return cached
    kit = build_kit(session_items(db, session), candidate_ref=str(session.id), must_skills=skills)
    with _LOCK:
        if len(_KITS) >= _KITS_CAP:
            _KITS.pop(next(iter(_KITS)))
        _KITS[key] = kit
    return kit


def pick_session(db: Session, candidate_id: uuid.UUID, specialization: str | None) -> SessionRow | None:
    """Сессия, на которой держится квалификация кандидата (по специализации потребности), иначе последняя решённая."""
    q = select(QualificationRow).where(QualificationRow.candidate_id == candidate_id)
    if specialization:
        q = q.where(QualificationRow.specialization == specialization)
    for qual in db.scalars(q.order_by(QualificationRow.updated_at.desc())):
        if qual.source_session_id is not None:
            s = db.get(SessionRow, qual.source_session_id)
            if s is not None and s.state:
                return s
    return db.scalars(
        select(SessionRow)
        .where(SessionRow.candidate_id == candidate_id, SessionRow.status.in_(_DONE_STATUSES))
        .order_by(SessionRow.created_at.desc())
        .limit(1)
    ).first()


def must_skills_of(db: Session, inv: Invitation) -> list[str]:
    """Must-навыки потребности, из которой отправлено приглашение: по ним добавляются вопросы, которых тест не покрывает."""
    if inv.need_id is None:
        return []
    need = db.get(Need, inv.need_id)
    if need is None:
        return []
    return [str(s["skill"]) for s in (need.must_skills or []) if isinstance(s, dict) and s.get("skill")]


def employer_kit(db: Session, inv: Invitation) -> tuple[VerificationKit, SessionRow]:
    """Комплект для работодателя по приглашению — только после раскрытия контакта этой пары."""
    if active_disclosure_row(db, inv.candidate_id, inv.company_id) is None:
        raise conflict(
            "kit_not_available",
            "Сверка за 5 минут станет доступна после взаимного «да»: пока контакты кандидата не раскрыты",
        )
    need_spec = db.scalar(select(Need.specialization).where(Need.id == inv.need_id)) if inv.need_id else None
    session = pick_session(db, inv.candidate_id, need_spec)
    if session is None:
        raise not_found("kit_no_session", "У кандидата нет завершённого теста, по которому можно построить сверку")
    return kit_for_session(db, session, must_skills_of(db, inv)), session


def lab_kit(db: Session, session_id: uuid.UUID) -> tuple[VerificationKit, SessionRow]:
    session = db.get(SessionRow, session_id)
    if session is None or not session.state:
        raise not_found("session_not_found", "Сессия не найдена")
    if session.status not in _DONE_STATUSES:
        raise conflict("session_not_finished", "Сверка строится по законченной сессии: тест ещё идёт")
    return kit_for_session(db, session), session


def kit_dict(kit: VerificationKit, audience: str) -> dict[str, Any]:
    return kit.to_dict(audience)
