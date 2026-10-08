"""Демо-история подбора для панели ФСП «Кадровый резерв» (config/fsp_dashboard.yaml → demo_history).

Зачем: сценарий демо-мира сознательно короткий (15 приглашений), а панель с k-анонимностью ≥ 5 на таких числах состояла бы
из «<5». История — отдельная, воспроизводимая (seed) выборка поверх мира: вымышленные компании без сотрудников, закрытые
брифы-архивы и приглашения в терминальных статусах за последние недели. Эффекта «ФСП сильнее» в неё НЕ заложено: исходы
и вилки одинаковы для спортсменов ФСП и остальных (разница на панели — шум, это честная демонстрация).

Вызывается из scripts/seed_demo.py и POST /api/demo/reset, а не из world.seed_world: сценарий и его тесты не меняются.
Идемпотентна: повторный вызов находит компании-архивы по метке и ничего не делает. Сброс демо-мира стирает их вместе
с остальными демо-компаниями (world.purge_contact_data), после сброса история собирается заново.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.configs import load_config
from app.models.company import Company
from app.models.contact import Invitation, OfferTerms
from app.models.enums import NeedStatus, QualificationStatus
from app.models.fsp import FspLink
from app.models.matching import Need
from app.models.testing import Qualification
from app.services.demo.accounts import stable_id
from app.services.demo.models import DemoPersona


def cfg() -> dict[str, Any]:
    return load_config("fsp_dashboard")["demo_history"]


def _pseudonym(company: uuid.UUID, candidate: uuid.UUID) -> str:
    return "К-" + str(int(hashlib.sha256(f"{company}{candidate}".encode()).hexdigest(), 16) % 9000 + 1000)


def seed_history(db: Session) -> dict[str, Any]:
    """Собрать историю. Возвращает счётчики или {"skipped": причина}."""
    c = cfg()
    if not c["enabled"]:
        return {"skipped": "demo_history.enabled = false"}
    if db.scalar(select(Company.id).where(Company.field == c["field"]).limit(1)) is not None:
        return {"skipped": "история уже собрана"}
    rows = db.execute(
        select(Qualification.candidate_id, Qualification.specialization, Qualification.grade)
        .join(DemoPersona, DemoPersona.user_id == Qualification.candidate_id)
        .where(DemoPersona.kind == "synthetic", Qualification.status == QualificationStatus.confirmed.value, Qualification.grade.is_not(None))
        .order_by(Qualification.candidate_id, Qualification.specialization)
    ).all()
    fsp_ids = set(db.scalars(select(FspLink.user_id)))
    quals: dict[uuid.UUID, list[tuple[str, str]]] = {}
    for cid, spec, grade in rows:
        quals.setdefault(cid, []).append((spec, grade))
    fsp_pool = sorted((x for x in quals if x in fsp_ids), key=str)
    other_pool = sorted((x for x in quals if x not in fsp_ids), key=str)
    if len(fsp_pool) < int(c["min_fsp_candidates"]) or not other_pool:
        return {"skipped": f"мало кандидатов ФСП с категорией ({len(fsp_pool)} < {c['min_fsp_candidates']})"}

    rng = random.Random(int(c["seed"]))
    now = clock.now()
    step = timedelta(hours=float(c["step_hours"]))
    ndfl = float(load_config("fsp_dashboard")["salary"]["ndfl_rate"])
    companies = [
        Company(
            id=stable_id("fsp-history-company", name),
            email_domain=f"{i}.{c['email_domain_suffix']}",
            personal_domain=False,
            name=f"ООО «{name}»",
            field=c["field"],
            is_demo=True,
            profile_completed_at=now,
        )
        for i, name in enumerate(c["companies"], 1)
    ]
    db.add_all(companies)
    db.flush()

    needs: dict[tuple[uuid.UUID, str, str], Need] = {}

    def need_for(company: Company, spec: str, grade: str, mid: int, created: datetime) -> Need:
        key = (company.id, spec, grade)
        if key not in needs:
            needs[key] = Need(
                company_id=company.id, title="Архив подбора", raw_text="Демо-история подбора (архивный бриф).",
                status=NeedStatus.closed.value, close_reason="filled", specialization=spec, grade=grade,
                salary_from=int(mid * 0.9), salary_to=int(mid * 1.1), salary_net=True, created_at=created, updated_at=created,
            )
            db.add(needs[key])
            db.flush()
        return needs[key]

    statuses = list(c["status_mix"])
    weights = [float(c["status_mix"][s]) for s in statuses]
    used: set[tuple[uuid.UUID, uuid.UUID]] = set()
    made = {"invitations": 0, "fsp": 0, "hired": 0}
    horizon = float(c["weeks"]) * 7.0
    for _ in range(int(c["invitations"])):
        is_fsp = rng.random() < float(c["fsp_share"])
        cand = rng.choice(fsp_pool if is_fsp else other_pool)
        spec, grade = rng.choice(quals[cand])
        company = rng.choice(companies)
        if (company.id, cand) in used:
            continue
        used.add((company.id, cand))
        status = rng.choices(statuses, weights)[0]
        if status == "sent":
            created = now - timedelta(days=rng.uniform(0.05, float(c["sent_window_days"])))
        else:  # цепочка событий (≤ 4 шагов) должна уложиться до «сейчас»
            created = now - timedelta(days=rng.uniform(1.0, horizon)) - 4 * step
        mid = float(c["offer_by_grade"][grade]) * (1 + float(c["offer_spread"]) * rng.uniform(-1, 1))
        spread = float(c["offer_spread"])
        gross = rng.random() < float(c["ndfl_gross_share"])
        k = 1.0 / (1.0 - ndfl) if gross else 1.0
        lo, hi = round(mid * (1 - spread) * k, -3), round(mid * (1 + spread) * k, -3)
        need = need_for(company, spec, grade, int(mid), created)
        accepted: datetime | None = created + step if status in ("hired", "interview_open", "not_matched") else None
        inv = Invitation(
            company_id=company.id, need_id=need.id, candidate_id=cand, pseudonym=_pseudonym(company.id, cand),
            created_at=created, updated_at=accepted or created, respond_by=created + timedelta(days=int(c["respond_days"])),
            accepted_at=accepted,
        )
        if status == "hired":
            inv.status, inv.outcome, inv.stage = "closed", "hired", "interview"
            inv.contacted_at, inv.closed_at = created + 2 * step, created + 4 * step
            inv.updated_at = inv.closed_at
            made["hired"] += 1
        elif status == "not_matched":
            inv.status, inv.outcome, inv.stage = "closed", "not_matched", "interview"
            inv.contacted_at, inv.closed_at = created + 2 * step, created + 4 * step
            inv.updated_at = inv.closed_at
        elif status == "interview_open":
            inv.status, inv.stage, inv.contacted_at = "accepted", "interview", created + 2 * step
        elif status == "declined":
            inv.status, inv.responded_at, inv.decline_reason = "declined", created + step, "salary"
            inv.updated_at = inv.responded_at
        elif status == "expired":
            inv.status, inv.closed_at = "expired", created + timedelta(days=int(c["respond_days"]))
            inv.updated_at = min(inv.closed_at, now)
        elif status == "withdrawn":
            inv.status, inv.closed_at, inv.withdraw_reason = "withdrawn", created + step, "Демо-история: бриф закрыт"
            inv.updated_at = inv.closed_at
        else:
            inv.status = "sent"
        db.add(inv)
        db.flush()
        db.add(
            OfferTerms(
                invitation_id=inv.id, ver=1, author="employer", title="Позиция (архив подбора)", description="Демо-история подбора.",
                salary_from=int(lo), salary_to=int(hi), salary_net=not gross, created_at=created,
                sha256=hashlib.sha256(f"{inv.id}:{lo}:{hi}".encode()).hexdigest(),
            )
        )
        made["invitations"] += 1
        made["fsp"] += int(is_fsp)
    # привязки ФСП ID — не все в один день: растягиваем по первым неделям окна, чтобы на графике был рост
    for uid in fsp_pool:
        link = db.scalar(select(FspLink).where(FspLink.user_id == uid))
        if link is not None:
            link.linked_at = now - timedelta(days=horizon - rng.uniform(0, float(c["linked_spread_weeks"]) * 7.0))
    db.commit()
    return {"companies": len(companies), "needs": len(needs), **made}
