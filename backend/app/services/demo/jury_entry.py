"""Вход жюри (DEMO_MODE): одноразовые кандидаты для экспресс- и полного теста, вход в роли работодателя и методиста
без пароля. Решение 29 (Q&A 07.10): жюри не обязано регистрироваться, но обязано иметь возможность пройти тест САМО.

Одноразовый кандидат — активный аккаунт без пароля (войти в него можно только токеном из этого модуля), опрос заполнен
настоящим сервисом (никаких обходных путей к квалификации), сессия теста помечена demo_assisted. Роли «работодатель» и
«методист» — это существующие демо-аккаунты seed; пароли здесь не читаются и не печатаются.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import conflict, not_found
from app.core.settings import RateRule, Settings
from app.models.users import User
from app.services.demo import accounts, profiles
from app.services.demo.cfg import section
from app.services.testing import labels
from app.services.testing import sessions as test_sessions


def cfg() -> dict[str, Any]:
    return section("jury_entry")


def start_rate() -> RateRule:
    """Лимит одноразовых кандидатов с одного адреса: жюри сидит за одним NAT, поэтому щедрее, чем у регистрации."""
    r = cfg()["start_rate"]
    return RateRule(int(r["capacity"]), int(r["period_s"]))


def create_guest(db: Session, settings: Settings, *, spec: str, grade: str) -> User:
    """Одноразовый кандидат с пройденным опросом по выбранной специализации."""
    c = cfg()
    email = f"guest-{uuid.uuid4().hex[:10]}@{c['guest_email_domain']}"
    user = accounts.ensure_user(db, settings, email=email, role="candidate", password=None)
    # kind=staged (ограничение CHECK таблицы demo_personas), key=jury_guest: сброс демо удаляет гостей вместе со ступенчатыми
    accounts.mark(db, user, "staged", "jury_guest", spec=spec, grade=grade, guest=True)
    months = int(c["months_by_grade"].get(grade, 24))
    profiles.fill_survey(db, user, spec, list(c["industries"]), months, set())
    return user


def start_for_guest(db: Session, user: User, *, mode: str, spec: str, grade: str, ip: str | None, ua: str | None) -> uuid.UUID:
    """Создать сессию теста (экспресс или полную) для одноразового кандидата; согласие на журналирование — «галочка»
    брифинга, поставленная за гостя (в журнал согласий уходит как обычно). Возвращает id сессии."""
    ld = test_sessions.create_session(
        db, user, spec=spec, grade=grade, upgrade=False, consent_test_logging=True, ip=ip, user_agent=ua,
        express=mode == "express",
    )
    ld.row.demo_assisted = True  # гость жюри: ни калибровка, ни метрики, ни экспозиция банка
    db.flush()
    return ld.row.id


def check_spec(spec: str) -> None:
    if not labels.spec_testable(spec):
        raise conflict("specialization_not_testable", "Тест по этому направлению появится позже", {"specialization": spec})


def persona_user(db: Session, persona: str) -> User:
    """Демо-аккаунт для входа без пароля: HR компании сценария или жюри-методист. Админа нет умышленно."""
    c = cfg()
    if persona == "employer":
        email = next(x["hr_email"] for x in section("companies") if x["key"] == c["employer_company"])
    elif persona in ("lab", "fsp"):  # панель ФСП открывает тот же аккаунт жюри (роль методист), другая стартовая страница
        email = section("staff")[c["lab_account"]]["email"]
    else:  # pragma: no cover — схема запроса уже ограничила значения
        raise not_found("persona_not_found", "Такой роли в демо нет")
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        raise not_found("persona_not_seeded", "Демо-мир не собран: выполните `make seed` (или `make reset-demo`)")
    return user

