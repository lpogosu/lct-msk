"""Контроль доступа к записям (защита от IDOR, аудит G9: «правило владелец записи в сервисном слое»).

Правило: запись, принадлежащая другому пользователю, неотличима от несуществующей → 404,
а не 403 — иначе по кодам ответа можно перебирать чужие идентификаторы.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any, TypeVar

from sqlalchemy.orm import Session

from app.core.errors import forbidden, not_found
from app.models.enums import UserRole
from app.models.users import User

T = TypeVar("T")

# Роли, которым можно разрешить чужие записи в служебных разделах: ensure_owner(..., allow_roles=STAFF_ROLES)
STAFF_ROLES = frozenset({UserRole.admin.value})


def is_owner(obj: Any, user: User, owner_attr: str = "user_id") -> bool:
    """Владелец записи — пользователь (owner_attr указывает на users.id, например user_id/candidate_id)
    или его компания (owner_attr="company_id" → сравниваем с user.company_id)."""
    value = getattr(obj, owner_attr)
    if owner_attr == "company_id":
        return user.company_id is not None and value == user.company_id
    return bool(value == user.id)


def ensure_owner(
    obj: T | None,
    user: User,
    owner_attr: str = "user_id",
    *,
    allow_roles: Iterable[str] = (),
    message: str = "Запись не найдена",
) -> T:
    """Вернуть obj, если он существует и принадлежит user (или роль user в allow_roles); иначе 404."""
    if obj is None:
        raise not_found(message=message)
    if user.role in set(allow_roles) or is_owner(obj, user, owner_attr):
        return obj
    raise not_found(message=message)


def get_owned_or_404(
    db: Session,
    model: type[T],
    obj_id: uuid.UUID | str,
    user: User,
    owner_attr: str = "user_id",
    *,
    allow_roles: Iterable[str] = (),
    message: str = "Запись не найдена",
) -> T:
    """db.get + ensure_owner в одном вызове. Некорректный id тоже даёт 404."""
    try:
        key = obj_id if isinstance(obj_id, uuid.UUID) else uuid.UUID(str(obj_id))
    except ValueError:
        raise not_found(message=message) from None
    return ensure_owner(db.get(model, key), user, owner_attr, allow_roles=allow_roles, message=message)


def ensure_role(user: User, *roles: str) -> None:
    """403, если роль пользователя не из списка."""
    if user.role not in {str(r) for r in roles}:
        raise forbidden(
            "role_forbidden",
            "Это действие недоступно для вашей роли",
            {"required": sorted(str(r) for r in roles), "role": user.role},
        )
