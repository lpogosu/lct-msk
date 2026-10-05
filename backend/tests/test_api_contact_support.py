"""Общие помощники API-тестов контакта, вакансий, задач и профиля компании (тестов в этом модуле нет).

Мир теста строится через настоящее API (регистрация, профиль компании, приглашения); то, что принадлежит
другим разделам (просмотр карточки Р6, шортлист, бриф, квалификация), записывается прямо в БД.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import clock

COMPANY_PROFILE: dict[str, Any] = {
    "name": "ООО «Северный код»",
    "field": "Финтех: платёжный шлюз",
    "description": "Делаем платёжный шлюз для маркетплейсов: команда 40 человек, Москва и удалённо.",
    "contact_methods": {"email": "hr@severny-kod.ru", "telegram": "@severny_hr"},
}

INVITE: dict[str, Any] = {
    "title": "Backend-разработчик (Python) в платёжный шлюз",
    "description": "Ищем бэкенд-разработчика в команду платёжного шлюза: Python, FastAPI, PostgreSQL, Kafka. Гибрид.",
    "salary_from": 220000,
    "salary_to": 280000,
    "salary_type": "net",
    "format": "hybrid",
}


@dataclass
class Emp:
    acct: Any
    company_id: uuid.UUID

    @property
    def headers(self) -> dict[str, str]:
        return self.acct.headers


@dataclass
class Cand:
    acct: Any
    user_id: uuid.UUID
    full_name: str
    email: str
    phone: str
    telegram: str

    @property
    def headers(self) -> dict[str, str]:
        return self.acct.headers

    def pii(self) -> list[str]:
        """Что не должно появиться в ответах работодателю до раскрытия (ни ключом, ни значением)."""
        return [self.full_name, self.email, self.phone, self.telegram, self.acct.email, str(self.user_id)]


def make_employer(client, make_account, db: Session, domain: str = "severny-kod.ru", profile: dict | None = None) -> Emp:
    from app.models.users import User

    acct = make_account(f"hr-{uuid.uuid4().hex[:4]}@{domain}", role="employer")
    company_id = db.get(User, acct.user_id).company_id
    assert company_id is not None
    if profile is not False:
        body = dict(profile or COMPANY_PROFILE)
        r = client.put("/api/companies/me", json=body, headers=acct.headers)
        assert r.status_code == 200, r.text
        assert r.json()["profile_complete"] is True, r.json()
    return Emp(acct, company_id)


def make_candidate(make_account, db: Session, *, n: int = 1, blind_card: bool = True, **profile: Any) -> Cand:
    """Кандидат с контактами в профиле. blind_card — согласие на показ слепой карточки (без него кандидата
    нет в банке, и приглашать его нельзя: концепция §3.1 шаг 6, §3.3)."""
    from app.core.consents import grant_consent
    from app.models.candidate import CandidateProfile
    from app.models.enums import ConsentPurpose

    acct = make_account(role="candidate")
    if blind_card:
        grant_consent(db, acct.user_id, ConsentPurpose.blind_card)
    tag = uuid.uuid4().hex[:6]
    full_name = f"Анна Тестова-{tag}"
    email = f"anna.{tag}@example.com"
    phone = f"+7 900 {n:03d}-{tag[:2]}-{tag[2:4]}".replace("a", "1").replace("b", "2").replace("c", "3").replace(
        "d", "4"
    ).replace("e", "5").replace("f", "6")
    telegram = f"@anna_{tag}"
    prof = db.get(CandidateProfile, acct.user_id)
    if prof is None:
        prof = CandidateProfile(user_id=acct.user_id)
        db.add(prof)
    prof.full_name = full_name
    prof.contact_email = email
    prof.phone = phone
    prof.telegram = telegram
    prof.search_mode = "active"
    for k, v in profile.items():
        setattr(prof, k, v)
    db.flush()
    return Cand(acct, acct.user_id, full_name, email, phone, telegram)


def open_card(db: Session, emp: Emp, cand: Cand) -> Any:
    """Работодатель открыл слепую карточку (Р6) — DECISIONS 7; возвращает псевдоним и ссылку в рамках компании."""
    from app.models.matching import ProfileView
    from app.services.matching.privacy import get_alias

    db.add(ProfileView(company_id=emp.company_id, candidate_id=cand.user_id, at=clock.now()))
    alias = get_alias(db, emp.company_id, cand.user_id)
    db.flush()
    return alias


def make_need(db: Session, emp: Emp, *, spec: str = "backend_python", grade: str = "middle", must=("python",), **kw: Any):
    from app.models.matching import Need

    need = Need(
        company_id=emp.company_id,
        author_id=emp.acct.user_id,
        title=kw.pop("title", "Backend Middle в платёжный шлюз"),
        raw_text=kw.pop(
            "raw_text",
            "Бэкенд в команду платёжного шлюза. Уверенный Python, FastAPI и PostgreSQL, опыт от 2 лет. "
            "Kafka и Docker будут плюсом. Гибрид, Москва. 220–280 тыс. на руки",
        ),
        status="active",
        specialization=spec,
        grade=grade,
        salary_from=kw.pop("salary_from", 220000),
        salary_to=kw.pop("salary_to", 280000),
        salary_net=True,
        format="hybrid",
        must_skills=[{"skill": s} for s in must],
        **kw,
    )
    db.add(need)
    db.flush()
    need.parsed = {
        "brief": {
            "need_id": str(need.id),
            "spec": spec,
            "grade": grade,
            "must": list(must),
            "nice": [],
            "salary_from": need.salary_from,
            "salary_to": need.salary_to,
            "salary_net": True,
            "formats": ["hybrid"],
            "city": None,
        }
    }
    db.flush()
    return need


def shortlist(db: Session, need, cand: Cand) -> None:
    from app.models.matching import Shortlist

    db.add(Shortlist(need_id=need.id, candidate_id=cand.user_id, pinned_at=clock.now()))
    db.flush()


def invite(client, emp: Emp, ref: str, **over: Any):
    return client.post("/api/invitations", json={**INVITE, "candidate": ref, **over}, headers=emp.headers)


def advance(days: float = 0, hours: float = 0) -> None:
    clock.advance(timedelta(days=days, hours=hours).total_seconds())


def dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def assert_no_pii(obj: Any, cand: Cand) -> None:
    """Контракт §4.5 и DECISIONS 20: в ответе работодателю нет ни одного ПДн кандидата и его внутреннего id."""
    text = dump(obj)
    for value in cand.pii():
        assert value not in text, f"ПДн/внутренний id кандидата в ответе работодателю: {value}"
    from app.contact.visibility import pii_keys_in

    assert not pii_keys_in(obj), pii_keys_in(obj)


def reliability_kinds(db: Session, company_id: uuid.UUID) -> list[str]:
    from sqlalchemy import select

    from app.models.company import ReliabilityEventRow

    db.flush()
    return sorted(db.scalars(select(ReliabilityEventRow.kind).where(ReliabilityEventRow.company_id == company_id)))
