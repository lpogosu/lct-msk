"""Помощники тестов профиля: подтверждённая квалификация без прохождения теста (через контракт модуля
тестирования save_domain), чтобы проверять PDF-профиль, превью и сверку изолированно от CAT."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.testing import TestSession
from app.services.testing.qualification import get_or_create_row, save_domain
from app.testing.session import GradeChange, Qualification, QualStatus


def confirm_qualification(
    db: Session,
    user_id: uuid.UUID,
    *,
    now: datetime,
    spec: str = "backend_python",
    grade: str = "middle",
    theta: float = 0.42,
    se: float = 0.22,
    p_pass: float = 0.94,
    days_ago: int = 21,
) -> uuid.UUID:
    """Квалификация CONFIRMED(grade) с модулями ✓/✓✓; возвращает id сессии теста, на которой держится грейд."""
    at = now - timedelta(days=days_ago)
    session = TestSession(
        candidate_id=user_id,
        specialization=spec,
        kind="initial",
        claimed_grade=grade,
        status="FINAL",
        decision="PASSED",
        theta=theta,
        se=se,
        p_pass=p_pass,
        n_items=13,
        trust=1.0,
        seed=20261005,
        started_at=at - timedelta(minutes=25),
        finished_at=at,
    )
    db.add(session)
    db.flush()
    q = Qualification(account_id=str(user_id), spec=spec)
    q.status = QualStatus.CONFIRMED
    q.grade = grade
    q.theta = theta
    q.se = se
    q.p_pass = p_pass
    q.trust = 1.0
    q.confirmed_at = at
    q.first_confirmed_at = at
    q.last_activity_at = at
    q.source_session_id = str(session.id)
    q.grade_session_ids = [str(session.id)]
    q.module_tags = {"python_core": "strong", "sql": "confirmed", "http_api": "confirmed"}
    q.subscores = {
        "python_core": {"name": "Python", "n": 5, "theta": 0.9, "se": 0.4, "tag": "strong", "symbol": "✓✓"},
        "sql": {"name": "SQL и базы данных", "n": 3, "theta": 0.2, "se": 0.5, "tag": "confirmed", "symbol": "✓"},
        "http_api": {"name": "HTTP и API", "n": 3, "theta": 0.1, "se": 0.5, "tag": "confirmed", "symbol": "✓"},
        "algo": {"name": "Алгоритмы и структуры данных", "n": 1, "theta": 0.0, "se": 0.8, "tag": "insufficient", "symbol": "~"},
    }
    q.history = [GradeChange(None, grade, at, "initial", str(session.id), False)]
    row = get_or_create_row(db, user_id, spec, lock=False)
    save_domain(db, row, q, notify=False)
    db.commit()
    return session.id
