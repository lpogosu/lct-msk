"""Псевдоним кандидата для конкретной компании: «К-4821» = HMAC(secret, company‖candidate) (концепция §4.5).

Свой для каждой компании — две компании не могут сопоставить свои слепые карточки. Секрет — из окружения,
не из YAML. Псевдоним — только подпись для UI; при первом показе слой БД сохраняет его (Invitation.pseudonym)
и разрешает редкие совпадения внутри компании через unique_pseudonym().
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Collection

from .settings import ContactConfig, get_contact_config

# Разделитель вместо «‖»: без него пары ("ab","c") и ("a","bc") дали бы один и тот же псевдоним.
_SEP = "\x1f"


def pseudonym(
    secret: str | bytes,
    company_id: str,
    candidate_id: str,
    *,
    attempt: int = 0,
    cfg: ContactConfig | None = None,
) -> str:
    cfg = cfg or get_contact_config()
    if not secret:
        raise ValueError("Пустой секрет: псевдонимы стали бы предсказуемыми")
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    msg = _SEP.join((str(company_id), str(candidate_id)))
    if attempt:
        msg += f"{_SEP}{attempt}"
    digest = hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()
    digits = cfg.pseudonym.digits
    low = 10 ** (digits - 1)
    number = low + int.from_bytes(digest[:8], "big") % (9 * low)
    return f"{cfg.pseudonym.prefix}{number}"


def unique_pseudonym(
    secret: str | bytes,
    company_id: str,
    candidate_id: str,
    taken: Collection[str],
    cfg: ContactConfig | None = None,
) -> str:
    """Первый свободный вариант среди уже выданных этой компании (детерминированная последовательность)."""
    cfg = cfg or get_contact_config()
    for attempt in range(cfg.pseudonym.max_collision_attempts):
        candidate = pseudonym(secret, company_id, candidate_id, attempt=attempt, cfg=cfg)
        if candidate not in taken:
            return candidate
    raise RuntimeError("Не удалось подобрать свободный псевдоним")
