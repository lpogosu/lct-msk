"""Общее для регулярных задач: конфиг, часы, банк семейств, категории кандидата, одноразовые экземпляры."""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contact.settings import TasksCfg, get_contact_config
from app.contact.texts import relative
from app.core.clock import clock
from app.core.configs import load_config
from app.core.errors import ApiError
from app.models.enums import AssignmentStatus, FamilyStatus, QualificationStatus
from app.models.tasks import EmployerTask, TaskAssignment
from app.models.testing import ItemFamilyParams, ItemInstance, Qualification

log = logging.getLogger("vzaimno.tasks")

GRADES: tuple[str, ...] = ("intern", "junior", "middle", "senior")

# Задача ждёт кандидата (занимает слот «≤ 3 активных»)
ACTIVE = (AssignmentStatus.offered.value, AssignmentStatus.opened.value)
# Кандидат свою часть сделал
DONE = (
    AssignmentStatus.submitted.value,
    AssignmentStatus.auto_scored.value,
    AssignmentStatus.awaiting_review.value,
    AssignmentStatus.reviewed.value,
)
# Итог есть (балл посчитан) — это видит подбор (свежесть A, доказательство модуля в K)
SCORED = (AssignmentStatus.auto_scored.value, AssignmentStatus.reviewed.value)
# «Пропуск» для серии «3 пропуска подряд → пауза» (отказ не штрафуется, только пауза предложений)
SKIPS = (AssignmentStatus.skipped.value, AssignmentStatus.expired.value)

SOURCE_OFFER = "offer"
SOURCE_CHECK_SET = "check_set"


def cfg() -> TasksCfg:
    return get_contact_config().tasks


def now() -> datetime:
    return clock.now()


def rel(target: datetime | None, at: datetime | None = None) -> str | None:
    return relative(target, at or now()) if target is not None else None


def grade_index(grade: str | None) -> int | None:
    return GRADES.index(grade) if grade in GRADES else None


def grade_name(grade: str | None) -> str | None:
    for g in load_config("taxonomy").get("grades", ()):
        if g.get("code") == grade:
            return str(g.get("name"))
    return grade


def spec_names() -> dict[str, str]:
    return {s["code"]: s["name"] for s in load_config("taxonomy").get("specializations", ())}


def module_name(module: str | None) -> str | None:
    if module is None:
        return None
    return str(load_config("taxonomy").get("modules", {}).get(module, {}).get("name", module))


# --- банк семейств ------------------------------------------------------------------------------


def families() -> dict[str, Any]:
    try:
        from app.testing.bank.registry import all_families

        return dict(all_families())
    except Exception as e:  # noqa: BLE001 — банк недоступен: каталог пуст, тип B работает
        log.warning("Банк заданий недоступен: %s", e)
        return {}


def family(family_id: str | None) -> Any | None:
    return families().get(family_id) if family_id else None


def family_minutes(fam: Any) -> int:
    """Время на задачу в минутах (вверх), не меньше 1."""
    return max(1, -(-int(fam.time_limit_s) // 60))


# Семейства, которые нельзя выдавать задачей: карантин детектора утечек (CUSUM) и выведенные из банка
WITHDRAWN_FAMILY_STATUSES = (FamilyStatus.quarantine.value, FamilyStatus.retired.value)


def withdrawn_family_ids(db: Session) -> set[str]:
    """Семейство в карантине или выведено хотя бы на одной шкале — в каталог задач и наборы не идёт."""
    return set(
        db.scalars(select(ItemFamilyParams.family_id).where(ItemFamilyParams.status.in_(WITHDRAWN_FAMILY_STATUSES)))
    )


# --- категории кандидата (аудитория «специализация × грейд ±1») -----------------------------


def candidate_categories(db: Session, user_id: uuid.UUID) -> list[tuple[str, str]]:
    """Подтверждённые категории кандидата; Fullstack — производная (обе квалификации, грейд — меньший)."""
    rows = db.scalars(
        select(Qualification).where(
            Qualification.candidate_id == user_id,
            Qualification.status.in_((QualificationStatus.confirmed.value, QualificationStatus.upgrade_testing.value)),
            Qualification.grade.is_not(None),
        )
    )
    cats = {(q.specialization, str(q.grade)) for q in rows if q.grade in GRADES}
    by_spec = dict(cats)
    if "backend_python" in by_spec and "frontend_js" in by_spec:
        low = min(by_spec["backend_python"], by_spec["frontend_js"], key=GRADES.index)
        cats.add(("fullstack", low))
    return sorted(cats)


def audience_fits(audience: dict[str, Any] | None, cats: Iterable[tuple[str, str]], window: int) -> bool:
    if not audience:
        return False
    spec, grade = audience.get("specialization"), audience.get("grade")
    gi = grade_index(grade)
    for s, g in cats:
        ci = grade_index(g)
        if s == spec and gi is not None and ci is not None and abs(gi - ci) <= window:
            return True
    return False


# --- одноразовый экземпляр задачи типа A -------------------------------------------------------


def task_seed(assignment_id: uuid.UUID) -> int:
    """Свой seed на назначение: случайный (из UUID назначения), но воспроизводимый — хранится в экземпляре.

    Отрицательный и по модулю ≥ 2^40: пул теста растёт с 0 вверх (max(seed)+1), поэтому экземпляры задач
    не сдвигают его счётчик и никогда не совпадут с экземпляром, выданным в тесте (random.Random(-s)
    в CPython равен random.Random(s) — отсюда большой модуль, а не просто знак)."""
    h = int.from_bytes(hashlib.sha256(f"task-instance|{assignment_id}".encode()).digest()[:6], "big")
    return -((1 << 40) + h)


def issue_task_instance(db: Session, a: TaskAssignment, fam: Any, at: datetime) -> ItemInstance:
    """Сгенерировать экземпляр семейства для этого кандидата; ключ — только на сервере (зашифрован)."""
    from app.services.testing.pool import instance_row

    try:
        inst = fam.generate(task_seed(a.id))
    except Exception as e:  # noqa: BLE001 — генератор семейства (например, без node) — понятная ошибка
        log.error("Не удалось сгенерировать экземпляр %s: %s", fam.id, e)
        raise ApiError(503, "task_unavailable", "Задача временно недоступна — попробуйте позже") from None
    values = instance_row(inst)
    row = ItemInstance(**values, issued_to_user_id=a.candidate_id, issued_at=at, created_at=at)
    db.add(row)
    db.flush()
    return row


def solve_deadline(a: TaskAssignment, task: EmployerTask) -> datetime | None:
    """Серверный таймер решения: время задачи с момента открытия (+ небольшой запас на сеть)."""
    if a.opened_at is None:
        return None
    return a.opened_at + timedelta(minutes=int(task.time_limit_min)) + timedelta(seconds=cfg().solve_grace_s)


@lru_cache(maxsize=1)
def _scale_ok() -> bool:
    try:
        from app.testing.irt import load_scale

        load_scale()
        return True
    except Exception:  # noqa: BLE001
        return False


def apply_to_theta(db: Session, candidate_id: uuid.UUID, fam: Any, correct: bool) -> dict[str, Any] | None:
    """Тип A влияет и на уровень (концепция §5.4): апостериор с приором N(θ̂, SE²(t)); грейд не меняется.

    Делает это движок квалификации (Qualification.apply_regular_task); любая ошибка не ломает задачу —
    результат всё равно идёт в свежесть и в доказательство модуля."""
    if not _scale_ok():
        return None
    try:
        from app.services.testing import qualification as qs
        from app.testing.irt import ItemParams, load_scale

        scale = load_scale()
        result: dict[str, Any] | None = None
        for spec in fam.specs:
            row = qs.get_row(db, candidate_id, spec, lock=True)
            if row is None or row.status not in (
                QualificationStatus.confirmed.value,
                QualificationStatus.upgrade_testing.value,
            ):
                continue
            params = db.get(ItemFamilyParams, (fam.id, spec))
            # Уровень сдвигаем только по откалиброванным параметрам (operational): b семейства в pretest —
            # экспертная оценка, а семейство в карантине могло утечь (концепция §5.4: «откалиброванное»)
            if params is None or params.status != FamilyStatus.operational.value:
                continue
            # Общий модуль (http_api, algo) живёт на шкале каждой специализации — обновляем каждую
            with db.begin_nested():
                q = qs.load_domain(row)
                item = ItemParams.from_family(params.b, params.sigma_b, fam.c, scale)
                result = dict(q.apply_regular_task(item, correct, clock, scale=scale))
                qs.save_domain(db, row, q)
        return result
    except Exception as e:  # noqa: BLE001 — модуль квалификации не принял задачу: без влияния на уровень
        log.info("Задача не повлияла на уровень: %s", e)
    return None


def invalidate_features(db: Session, candidate_id: uuid.UUID) -> None:
    """Хук подбора: признаки кандидата пересоберутся (свежесть A и подтверждение модуля в K)."""
    try:
        from app.services.matching import invalidate_features as inv

        inv(db, candidate_id)
    except Exception:  # noqa: BLE001 — кэш подбора и так сверяется по отпечатку источников
        pass
