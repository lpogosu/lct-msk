"""Хранение неизменяемой версии подборки (NeedVersion) — полный снимок объекта движка и обратная сборка.

Версия хранит снимок брифа, категории, фильтры, пресет, версию конфига и полный ранжированный список
(≤ 500 строк с components, reasons, hidden_by) — концепция §4.4 п. 5. Из хранилища собирается настоящий
app.matching.selection.SelectionVersion, поэтому вкладки, страницы, «Скрыто фильтрами», шортлист с
жёлтой меткой и бейджи diff считает движок, а не этот слой. Внутренние id остаются на сервере:
наружу строки уходят только через app/services/matching/privacy.employer_item.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from types import MappingProxyType, SimpleNamespace
from typing import Any

from app.matching.explain import Evidence, NeighborDelta, Reason
from app.matching.scoring import BriefSpec, ScoringConfig, SkillEvidence
from app.matching.selection import Filter, SelectionItem, SelectionVersion
from app.matching.skills import get_catalog
from app.models.matching import NeedVersion

# Компактная запись: версия хранит ≤ 500 строк, и её размер — главная цена записи (JSONB в Postgres).
# Соседи — списками позиций, числа — с точностью, которой хватает для показа и порядка (порядок задан полем
# rank, а не пересортировкой), название категории и навыка восстанавливаются по справочнику.
_CHIP_FIELDS = ("skill_id", "kind", "v", "source", "ref", "label", "state", "declared", "untestable")
_DIGITS = 6


def _r(x: float, nd: int = _DIGITS) -> float:
    return round(float(x), nd)


def chip_dict(c: Any) -> dict[str, Any]:
    """Сохранённый чип (список позиций или словарь старого формата) → словарь с названием навыка."""
    d = dict(zip(_CHIP_FIELDS, c, strict=False)) if isinstance(c, list | tuple) else dict(c)
    d.setdefault("name", get_catalog().name(d["skill_id"]))
    d["declared"], d["untestable"] = bool(d.get("declared")), bool(d.get("untestable"))
    return d


def category_of(d: Mapping[str, Any]) -> str:
    return str(d.get("category") or ScoringConfig.load().category_name(d["spec"], d["grade"]))


def item_to_store(i: SelectionItem, extras: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "candidate_id": i.candidate_id,
        "spec": i.spec,
        "grade": i.grade,
        "score": _r(i.score),
        "score_display": i.score_display,
        "parts": dict(i.parts),
        "components": {k: _r(v) for k, v in i.components.items()},
        "contributions": {k: _r(v) for k, v in i.contributions.items()},
        "G": _r(i.G),
        "Cov": _r(i.Cov),
        "Cond": _r(i.Cond, 6),
        "cond_parts": {k: _r(v, 6) for k, v in i.cond_parts.items()},
        "rank": i.rank,
        "category_rank": i.category_rank,
        "reasons": [
            {
                "kind": r.kind,
                "code": r.code,
                "component": r.component,
                "text": r.text,
                "evidence": [{"type": e.type, "id": e.id, "label": e.label} for e in r.evidence],
                "delta": None if r.delta is None else _r(r.delta, 3),
            }
            for r in i.reasons
        ],
        "why_you": list(i.why_you),
        # Чипы — словарями: их читает и модуль профиля кандидата («как меня видят», К15: kind, state, skill_id).
        "chips": [
            {
                "skill_id": c.skill_id,
                "kind": c.kind,
                "v": _r(c.v, 4),
                "source": c.source,
                "ref": c.ref,
                "label": c.label,
                "state": c.state,
                "declared": c.declared,
                "untestable": c.untestable,
            }
            for c in i.chips
        ],
        "hidden_by": list(i.hidden_by),
        "hidden_reasons": list(i.hidden_reasons),
        "has_fsp": i.has_fsp,
        "confirmed": i.confirmed,
        "flags": sorted(i.flags),
        "x": dict(extras or {}),
    }


def item_from_store(d: Mapping[str, Any]) -> SelectionItem:
    chips = [chip_dict(c) for c in d.get("chips") or ()]
    return SelectionItem(
        candidate_id=d["candidate_id"],
        spec=d["spec"],
        grade=d["grade"],
        category=category_of(d),
        score=float(d["score"]),
        score_display=float(d["score_display"]),
        parts=MappingProxyType(dict(d["parts"])),
        components=MappingProxyType(dict(d["components"])),
        contributions=MappingProxyType(dict(d["contributions"])),
        G=float(d["G"]),
        Cov=float(d["Cov"]),
        # Версии до Cond: условия в снимок не писались — множитель 1,0 (Score и вклады в снимке уже итоговые).
        Cond=float(d.get("Cond", 1.0)),
        cond_parts=MappingProxyType({k: float(v) for k, v in (d.get("cond_parts") or {"salary": 1.0, "format": 1.0}).items()}),
        rank=d.get("rank"),
        category_rank=d.get("category_rank"),
        reasons=tuple(
            Reason(
                r["kind"],
                r["code"],
                r.get("component"),
                r["text"],
                tuple(Evidence(e["type"], e["id"], e.get("label")) for e in r.get("evidence") or ()),
                r.get("delta"),
            )
            for r in d.get("reasons") or ()
        ),
        why_you=tuple(d.get("why_you") or ()),
        chips=tuple(
            SkillEvidence(
                c["skill_id"],
                c["name"],
                c["kind"],
                float(c["v"]),
                c.get("source"),
                c.get("ref"),
                c["label"],
                c["state"],
                c["declared"],
                c["untestable"],
            )
            for c in chips
        ),
        hidden_by=tuple(d.get("hidden_by") or ()),
        hidden_reasons=tuple(d.get("hidden_reasons") or ()),
        has_fsp=bool(d.get("has_fsp")),
        confirmed=bool(d.get("confirmed")),
        flags=frozenset(d.get("flags") or ()),
    )


def neighbor_to_store(n: NeighborDelta) -> list[Any]:
    """[место верхнего, место нижнего, Δ, главный плюс, главный минус, текст]: id кандидатов не нужны —
    пара определяется местами в «Общем топе» этой версии."""
    return [
        n.upper_rank,
        n.lower_rank,
        _r(n.delta),
        [n.plus[0], _r(n.plus[1])] if n.plus else None,
        [n.minus[0], _r(n.minus[1])] if n.minus else None,
        n.text,
    ]


def neighbor_dict(d: Any) -> dict[str, Any]:
    if isinstance(d, Mapping):
        return dict(d)
    upper, lower, delta, plus, minus, text = d
    return {"upper_rank": upper, "lower_rank": lower, "delta": delta, "plus": plus, "minus": minus, "text": text}


def neighbor_from_store(d: Any) -> NeighborDelta:
    nd = neighbor_dict(d)
    return NeighborDelta(
        nd["upper_rank"],
        nd["lower_rank"],
        str(nd.get("upper_id") or ""),
        str(nd.get("lower_id") or ""),
        float(nd["delta"]),
        tuple(nd["plus"]) if nd.get("plus") else None,
        tuple(nd["minus"]) if nd.get("minus") else None,
        nd["text"],
    )


def filters_to_store(filters: tuple[Filter, ...]) -> list[dict[str, Any]]:
    return [{"code": f.code, "value": list(f.value) if isinstance(f.value, tuple) else f.value} for f in filters]


def filters_from_store(raw: Any) -> tuple[Filter, ...]:
    return tuple(Filter(f["code"], f.get("value")) for f in raw or ())


# Версии неизменяемы, поэтому разобранный объект движка можно держать в памяти процесса: просмотр страниц,
# вкладок и шортлиста не разбирает заново ≤ 500 строк JSON. Ключ — id версии (UUID, не переиспользуется).
_CACHE_SIZE = 64
_cache: OrderedDict[uuid.UUID, SelectionVersion] = OrderedDict()
_lock = threading.Lock()


def prime(version_id: uuid.UUID, version: SelectionVersion) -> None:
    with _lock:
        _cache[version_id] = version
        _cache.move_to_end(version_id)
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)


def cached(version_id: uuid.UUID) -> SelectionVersion | None:
    with _lock:
        return _cache.get(version_id)


def version_from_row(nv: NeedVersion) -> SelectionVersion:
    """NeedVersion → SelectionVersion движка (неизменяемый объект, все представления — методами движка)."""
    with _lock:
        hit = _cache.get(nv.id)
        if hit is not None:
            _cache.move_to_end(nv.id)
            return hit
    version = _parse_row(nv)
    prime(nv.id, version)
    return version


def _parse_row(nv: NeedVersion) -> SelectionVersion:
    snap = nv.need_snapshot or {}
    stats = nv.stats or {}
    return SelectionVersion(
        need_id=str(nv.need_id),
        n=int(nv.n),
        parent_n=stats.get("parent_n"),
        brief=BriefSpec.from_dict(snap["brief"]),
        categories=tuple(tuple(c) for c in stats.get("categories") or ()),
        filters=filters_from_store((nv.filters or {}).get("engine")),
        preset=nv.preset,
        weights=MappingProxyType(dict(nv.weights or {})),
        scoring_config_version=nv.scoring_config_version,
        created_at=nv.created_at.isoformat() if nv.created_at else None,
        items=tuple(item_from_store(d) for d in nv.items or ()),
        neighbors=tuple(neighbor_from_store(d) for d in stats.get("neighbors") or ()),
        total_n=int(stats.get("total_n") or 0),
        hidden_n=int(stats.get("hidden_n") or 0),
        hidden_counts=MappingProxyType(dict(stats.get("hidden_counts") or {})),
    )


def ranks_only(nv: NeedVersion) -> SimpleNamespace:
    """Лёгкая «версия» для diff_badges движка: ему нужны только candidate_id и место в общем топе."""
    return SimpleNamespace(
        items=tuple(SimpleNamespace(candidate_id=d["candidate_id"], rank=d.get("rank")) for d in nv.items or ())
    )
