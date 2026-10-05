"""Обратная связь кандидату после «не сложилось» / отказа по отклику (концепция §3.2 шаг 10, spec-contact §2.2 п. 6):
причина + «каких навыков не хватило» со ссылкой на короткую задачу по модулю теста или на собеседование."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .common import cfg


def _catalog() -> Any | None:
    try:
        from app.matching.skills import get_catalog

        return get_catalog()
    except Exception:  # noqa: BLE001 — справочник навыков недоступен: ссылки без модулей
        return None


def skill_links(skill_ids: Sequence[str]) -> list[dict[str, Any]]:
    """Навык → ссылка: задача по модулю теста (если навык им проверяется) или «расскажите на собеседовании»."""
    c = cfg()
    cat = _catalog()
    out: list[dict[str, Any]] = []
    for sid in skill_ids:
        name, modules = str(sid), ()
        if cat is not None:
            try:
                name = cat.name(sid)
                modules = tuple(cat.modules_of(sid))
            except Exception:  # noqa: BLE001 — навык не из справочника: показываем как есть
                pass
        if modules:
            out.append(
                {
                    "kind": "task",
                    "skill": sid,
                    "module": modules[0],
                    "text": c.feedback.task_link_text.format(skill=name, minutes=c.tasks.max_minutes),
                }
            )
        else:
            out.append({"kind": "interview", "skill": sid, "module": None, "text": c.feedback.retake_link_text.format(skill=name)})
    return out


def reason_label(reason_code: str, entity_type: str) -> str:
    r = cfg().reasons
    table = r.application_reject if entity_type == "application" else {**r.outcome, **r.not_matched}
    return table.get(reason_code, reason_code)


def feedback_message(reason_code: str, skill_ids: Sequence[str], entity_type: str) -> tuple[str, list[dict[str, Any]]]:
    label = reason_label(reason_code, entity_type)
    head = "Компания подвела итог по вашему приглашению" if entity_type == "invitation" else "Компания ответила на ваш отклик"
    links = skill_links(list(skill_ids or ()))
    text = f"{head}: {label.lower()}"
    if links:
        text += ". " + "; ".join(link["text"] for link in links)
    return text, links
