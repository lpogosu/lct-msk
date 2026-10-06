"""Минимизация сохраняемого ответа реестра ФСП (152-ФЗ, принцип минимизации; хардненинг п. 1).

Платформа хранит сырой ответ реестра, чтобы без сети собирать FSPProfile (FspLink.athlete / achievements). Но из ответа
реально используются немногие поля, остальное — лишние персональные данные:

- спортсмен: нужны `sub` (ключ привязки) и ГОД рождения (политика по несовершеннолетним, концепция §4.3). ФИО, дата
  рождения целиком, регион и организация платформе не нужны и не сохраняются;
- результат: команда нужна как id, название и размер состава (team_size); `athlete_sub` участников команды — чужие ФСП ID
  других людей — не сохраняются (остаются обезличенные записи состава: роль без идентификатора);
- разряд: номер приказа и «кем присвоен» нигде не показываются — не сохраняются.

Соревнования, места и баллы — публичные данные протоколов, не ПДн спортсмена, и остаются: из них считается F.
Функции идемпотентны: повторный прогон по уже минимальным данным ничего не меняет (ночная задача retention дочищает
строки, сохранённые до этой правки).
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.fsp.models import Achievements, Athlete

_ATHLETE_FULL_KEYS = ("last_name", "first_name", "birth_date", "region_code", "region_name")


def minimal_athlete(athlete: Athlete | dict[str, Any] | None) -> dict[str, Any] | None:
    """{"sub", "birth_year"} из полного ответа реестра (или уже минимальной записи)."""
    if athlete is None:
        return None
    if isinstance(athlete, Athlete):
        return {"sub": athlete.sub, "birth_year": athlete.birth_date.year}
    out: dict[str, Any] = {"sub": athlete.get("sub")}
    year = athlete.get("birth_year")
    if year is None and athlete.get("birth_date"):
        year = int(str(athlete["birth_date"])[:4])
    if year is not None:
        out["birth_year"] = int(year)
    return out


def athlete_of(raw: dict[str, Any] | None) -> Athlete | None:
    """Athlete из сохранённой записи, если она полная (старые строки); для минимальной — None: профилю ФСП ФИО не нужны."""
    if not raw or not all(k in raw for k in _ATHLETE_FULL_KEYS):
        return None
    try:
        return Athlete.model_validate(raw)
    except ValidationError:
        return None


def birth_year_of(raw: dict[str, Any] | None) -> int | None:
    """Год рождения из сохранённой записи спортсмена (минимальной или полной)."""
    if not raw:
        return None
    if raw.get("birth_year") is not None:
        return int(raw["birth_year"])
    if raw.get("birth_date"):
        try:
            return int(str(raw["birth_date"])[:4])
        except ValueError:
            return None
    return None


def minimal_achievements(ach: Achievements | dict[str, Any] | None) -> dict[str, Any] | None:
    """Ответ реестра без лишних ПДн (см. docstring модуля); результат по-прежнему проходит Achievements.model_validate."""
    if ach is None:
        return None
    data = ach.model_dump(mode="json") if isinstance(ach, Achievements) else _clone(ach)
    for r in data.get("results") or ():
        team = r.get("team")
        if team:
            # состав команды без ФСП ID других спортсменов: размер (team_size) и роли сохраняются
            team["members"] = [{"athlete_sub": "", "role": m.get("role", "participant")} for m in team.get("members") or ()]
    for rk in data.get("ranks") or ():
        rk["order_no"] = ""
        rk["assigned_by"] = ""
    return data


def _clone(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clone(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clone(v) for v in value]
    return value


def is_minimal(athlete: dict[str, Any] | None, achievements: dict[str, Any] | None) -> bool:
    """Сохранённый ответ уже минимален (для ночной дочистки: не переписывать то, что не меняется)."""
    if athlete and any(k in athlete for k in _ATHLETE_FULL_KEYS):
        return False
    for r in (achievements or {}).get("results") or ():
        for m in ((r.get("team") or {}).get("members")) or ():
            if m.get("athlete_sub"):
                return False
    return all(not rk.get("order_no") and not rk.get("assigned_by") for rk in (achievements or {}).get("ranks") or ())


def minimize_link(link: Any) -> bool:
    """Привести FspLink к минимальному виду на месте. True — что-то изменилось."""
    ath, ach = link.athlete, link.achievements
    if is_minimal(ath, ach):
        return False
    if ath:
        link.athlete = minimal_athlete(ath)
    if ach:
        link.achievements = minimal_achievements(ach)
    return True
