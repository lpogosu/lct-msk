"""Необязательное LLM-дополнение разбора брифа (концепция §4.2 «Разбор брифа», spec-contact §3.1 п. 6).

LLM только ДОБАВЛЯЕТ навыки, которые пропустили правила, и только с цитатой, найденной в тексте брифа
(partial_ratio ≥ порога — проверяет шлюз app/llm; поле без цитаты отбрасывается). Значение сводится к id
справочника навыков; чего нет в справочнике — не добавляется. Вид навыка — «желательный»: обязательность решают
маркеры текста и работодатель. Источник чипа — «YandexGPT» / «GigaChat», цитата подсвечивается, как у правил.

По умолчанию LLM выключена (VZ_LLM_PROVIDER=none): демо-брифы разбираются только правилами, ответ детерминирован.
В Score, ранжировании и объяснениях LLM не участвует никогда (CLAUDE.md).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from app.matching import brief_parser as bp
from app.matching.skills import get_catalog

log = logging.getLogger("vzaimno.matching.llm")

TASK = "brief_skills"
INSTRUCTIONS = (
    "Это описание вакансии (бриф работодателя). Найди технологии, языки, фреймворки, базы данных и инструменты, "
    'которые нужны кандидату. Для каждой верни {"field": "skill", "value": "<название технологии>", '
    '"quote": "<дословная цитата из текста, где она упомянута или подразумевается>"}.'
)
SOURCE_LABELS = {"yandexgpt": "YandexGPT", "gigachat": "GigaChat"}


def get_gateway() -> Any:
    from app.llm.gateway import get_gateway as _gw

    return _gw()


def augment(p: bp.ParsedBrief, gateway: Any | None = None) -> bp.ParsedBrief:
    """Разбор правилами + навыки, найденные LLM с проверенной цитатой (или тот же разбор, если LLM выключена)."""
    try:
        gw = gateway or get_gateway()
        if not gw.enabled:
            return p
        res = gw.extract(TASK, p.text, INSTRUCTIONS, allowed_fields={"skill"})
    except Exception:  # noqa: BLE001 — сбой LLM не должен ронять разбор: работают правила
        log.exception("LLM-дополнение брифа не удалось — остаёмся на правилах")
        return p
    if not res.used or not res.fields:
        return p
    cat = get_catalog()
    have = {c.skill_id for c in p.skills}
    source = SOURCE_LABELS.get(str(res.provider), str(res.provider))
    added = []
    for f in res.fields:
        found = cat.find_skills(f.value, fuzzy=False)
        if not found:
            continue
        sid = found[0].id
        if sid in have:
            continue
        have.add(sid)
        sk = cat.skills[sid]
        added.append(
            bp.SkillChip(
                sid,
                sk.name,
                "nice",
                source,
                bp.Quote(f.start, f.end, f.quote),
                None,
                "llm",
                "llm",
                sk.untestable,
                sk.modules,
            )
        )
    if not added:
        return p
    out = replace(p, skills=p.skills + tuple(added))
    return replace(out, question=bp.clarifying_question(out))


def llm_used(p: bp.ParsedBrief) -> bool:
    return any(c.method == "llm" for c in p.skills)
