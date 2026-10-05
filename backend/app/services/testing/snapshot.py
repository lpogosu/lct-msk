"""Сериализация доменных объектов app/testing в jsonb и обратно.

TestSession хранится целиком (включая выданное, но не отвеченное задание и last_activity_at), чтобы
движок после перезапуска продолжал ровно с того же места (build-reviews: «API layer must persist the
full TestSession»). Флаги сессии — НЕ в снимке: их единственный источник — таблица flags (статус меняет
методист), при загрузке они собираются из строк.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any

from app.testing.cat import CatState, Decision, Offer, Selection
from app.testing.detectors import Flag
from app.testing.irt import ItemParams
from app.testing.session import GradeChange, Qualification, QualStatus, SessionStatus, TestSession

_DT = "$dt"


def enc(v: Any) -> Any:
    """Рекурсивно: datetime → {"$dt": iso}, Enum → значение, tuple/set → list."""
    if isinstance(v, datetime):
        return {_DT: v.isoformat()}
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, Mapping):
        return {str(k): enc(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [enc(x) for x in (sorted(v) if isinstance(v, (set, frozenset)) else v)]
    return v


def dec(v: Any) -> Any:
    if isinstance(v, Mapping):
        if set(v) == {_DT}:
            return datetime.fromisoformat(v[_DT])
        return {k: dec(x) for k, x in v.items()}
    if isinstance(v, list):
        return [dec(x) for x in v]
    return v


def _dt(v: Any) -> datetime | None:
    v = dec(v)
    return v if isinstance(v, datetime) else None


# ---------------------------------------------------------------------------
# Сессия
# ---------------------------------------------------------------------------


def selection_to_dict(s: Selection) -> dict[str, Any]:
    return {
        "seq": s.seq,
        "family_id": s.family_id,
        "module": s.module,
        "family_grade": s.family_grade,
        "is_pretest": s.is_pretest,
        "item": {"b": s.item.b, "c": s.item.c, "a": s.item.a},
        "time_limit_s": s.time_limit_s,
        "reason": enc(s.reason),
        "unavailable_modules": sorted(s.unavailable_modules),
        "journal": enc(list(s.journal)),
    }


def selection_from_dict(d: Mapping[str, Any]) -> Selection:
    return Selection(
        seq=int(d["seq"]),
        family_id=d["family_id"],
        module=d["module"],
        family_grade=d["family_grade"],
        is_pretest=bool(d["is_pretest"]),
        item=ItemParams(**d["item"]),
        time_limit_s=int(d["time_limit_s"]),
        reason=dec(d.get("reason") or {}),
        unavailable_modules=frozenset(d.get("unavailable_modules") or ()),
        journal=tuple(dec(d.get("journal") or [])),
    )


def decision_from_dict(d: Mapping[str, Any] | None) -> Decision | None:
    if not d:
        return None
    fields = {k: d[k] for k in ("code", "target_grade", "p_pass", "theta", "se", "n_items", "n_total", "reason")}
    return Decision(**fields)


def offer_from_dict(d: Mapping[str, Any]) -> Offer:
    return Offer(**{k: d[k] for k in ("direction", "target_grade", "p_already", "instant", "max_items", "text_ru")})


def session_to_state(s: TestSession) -> dict[str, Any]:
    return {
        "v": 1,
        "id": s.id,
        "account_id": s.account_id,
        "spec": s.spec,
        "kind": s.kind,
        "claimed_grade": s.claimed_grade,
        "cat": enc(s.cat.to_dict()),
        "created_at": enc(s.created_at),
        "status": str(s.status),
        "parent_id": s.parent_id,
        "base_grade": s.base_grade,
        "base_borderline": s.base_borderline,
        "started_at": enc(s.started_at),
        "paused_at": enc(s.paused_at),
        "decided_at": enc(s.decided_at),
        "finished_at": enc(s.finished_at),
        "pending": selection_to_dict(s.pending) if s.pending else None,
        "pending_shown_at": enc(s.pending_shown_at),
        "decision": s.decision.to_dict() if s.decision else None,
        "offers": [o.to_dict() for o in s.offers],
        "offers_expire_at": enc(s.offers_expire_at),
        "accepted_offer": s.accepted_offer.to_dict() if s.accepted_offer else None,
        "extended_by": s.extended_by,
        "trust": s.trust,
        "params_version": s.params_version,
        "last_activity_at": enc(s.last_activity_at),
    }


def session_from_state(d: Mapping[str, Any], flags: list[Flag] | None = None) -> TestSession:
    return TestSession(
        id=d["id"],
        account_id=d["account_id"],
        spec=d["spec"],
        kind=d["kind"],
        claimed_grade=d["claimed_grade"],
        cat=CatState.from_dict(dec(d["cat"])),
        created_at=_dt(d["created_at"]),  # type: ignore[arg-type]
        status=SessionStatus(d["status"]),
        parent_id=d.get("parent_id"),
        base_grade=d.get("base_grade"),
        base_borderline=bool(d.get("base_borderline")),
        started_at=_dt(d.get("started_at")),
        paused_at=_dt(d.get("paused_at")),
        decided_at=_dt(d.get("decided_at")),
        finished_at=_dt(d.get("finished_at")),
        pending=selection_from_dict(d["pending"]) if d.get("pending") else None,
        pending_shown_at=_dt(d.get("pending_shown_at")),
        decision=decision_from_dict(d.get("decision")),
        offers=tuple(offer_from_dict(o) for o in d.get("offers") or ()),
        offers_expire_at=_dt(d.get("offers_expire_at")),
        accepted_offer=offer_from_dict(d["accepted_offer"]) if d.get("accepted_offer") else None,
        extended_by=d.get("extended_by"),
        flags=list(flags or []),
        trust=d.get("trust"),
        params_version=d.get("params_version") or "",
        last_activity_at=_dt(d.get("last_activity_at")),
    )


# ---------------------------------------------------------------------------
# Квалификация
# ---------------------------------------------------------------------------


def _change_to_dict(c: GradeChange) -> dict[str, Any]:
    return {
        "from_grade": c.from_grade,
        "to_grade": c.to_grade,
        "at": enc(c.at),
        "reason": c.reason,
        "session_id": c.session_id,
        "counts_as_change": c.counts_as_change,
    }


def _change_from_dict(d: Mapping[str, Any]) -> GradeChange:
    return GradeChange(
        from_grade=d.get("from_grade"),
        to_grade=d.get("to_grade"),
        at=_dt(d["at"]),  # type: ignore[arg-type]
        reason=d["reason"],
        session_id=d.get("session_id"),
        counts_as_change=bool(d.get("counts_as_change")),
    )


_QUAL_PLAIN = (
    "grade",
    "borderline",
    "theta",
    "se",
    "p_pass",
    "trust",
    "hidden",
    "source_session_id",
    "active_session_id",
    "chain_trust",
)
_QUAL_DT = (
    "review_deadline",
    "confirmed_at",
    "first_confirmed_at",
    "onboarding_until",
    "last_activity_at",
    "next_change_allowed_at",
    "next_attempt_allowed_at",
)


def qualification_to_state(q: Qualification) -> dict[str, Any]:
    out: dict[str, Any] = {"v": 1, "account_id": q.account_id, "spec": q.spec, "status": str(q.status)}
    for k in _QUAL_PLAIN:
        out[k] = getattr(q, k)
    for k in _QUAL_DT:
        out[k] = enc(getattr(q, k))
    out["attempts"] = enc(list(q.attempts))
    out["history"] = [_change_to_dict(c) for c in q.history]
    out["module_tags"] = dict(q.module_tags)
    out["module_theta"] = dict(q.module_theta)
    out["subscores"] = enc(q.subscores)
    out["prev_snapshot"] = enc(q.prev_snapshot) if q.prev_snapshot is not None else None
    out["chain_ids"] = list(q.chain_ids)
    out["grade_session_ids"] = list(q.grade_session_ids)
    out["reviews"] = list(q.reviews)
    return out


def qualification_from_state(d: Mapping[str, Any], account_id: str, spec: str) -> Qualification:
    if not d:
        return Qualification(account_id=account_id, spec=spec)
    q = Qualification(account_id=d.get("account_id") or account_id, spec=d.get("spec") or spec)
    q.status = QualStatus(d.get("status") or QualStatus.NONE)
    for k in _QUAL_PLAIN:
        if k in d:
            setattr(q, k, d[k])
    for k in _QUAL_DT:
        setattr(q, k, _dt(d.get(k)))
    q.attempts = [x for x in (dec(d.get("attempts") or [])) if isinstance(x, datetime)]
    q.history = [_change_from_dict(c) for c in d.get("history") or ()]
    q.module_tags = dict(d.get("module_tags") or {})
    q.module_theta = dict(d.get("module_theta") or {})
    q.subscores = dec(d.get("subscores") or {})
    snap = d.get("prev_snapshot")
    if snap is not None:
        snap = dec(snap)
        if snap.get("status") is not None:
            snap["status"] = QualStatus(snap["status"])
    q.prev_snapshot = snap
    q.chain_ids = list(d.get("chain_ids") or [])
    q.grade_session_ids = list(d.get("grade_session_ids") or [])
    q.reviews = list(d.get("reviews") or [])
    return q
