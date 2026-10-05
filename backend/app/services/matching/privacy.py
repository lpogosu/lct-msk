"""Что видит работодатель (концепция §4.5, DECISIONS п. 20).

- Кандидат в API работодателя — псевдоним «К-4821» = HMAC(company‖candidate) и непрозрачная ссылка ``ref``
  (своя у каждой компании). Внутренний id кандидата, id попыток теста, задач и записей ФСП, номера ЕКП и
  ссылки на протоколы до раскрытия наружу не уходят: доказательства — непрозрачные ссылки в рамках компании.
- Строка ФСП до раскрытия — только обобщённая («Призёр всероссийских · продуктовое · 2025 · топ-3»);
  без ФСП — нейтральная серая строка, в «против» отсутствие ФСП не попадает никогда (это гарантирует движок).
- Раскрытая карточка (CandidateDisclosed) — только при действующем ContactDisclosure этой пары
  (app/contact/visibility.py решает по белому списку config/contact.yaml).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.contact.disclosure import ContactDisclosure as DomainDisclosure
from app.contact.pseudonym import unique_pseudonym
from app.contact.settings import get_contact_config
from app.contact.visibility import employer_view, score_band
from app.core.clock import clock, relative_ru
from app.core.configs import load_config
from app.core.settings import get_settings
from app.matching.explain import relative_days
from app.matching.scoring import ScoringConfig, build_category_stats, component_T, component_U
from app.matching.skills import get_catalog
from app.models.candidate import DISCLOSURE_FIELD_COLUMNS, CandidateProfile
from app.models.contact import ContactDisclosure
from app.models.matching import CandidateAlias, MatchingFeatures, Need, ProfileView, Shortlist
from app.models.testing import GradeChange
from app.services.matching.features import features_from_payload, refresh_stale
from app.services.matching.store import category_of, chip_dict
from app.testing.irt import load_scale
from app.testing.session import freshness_of

# Типы доказательств, чьи id внутренние (попытка теста, задача, запись ФСП, настройки кандидата):
# наружу — только HMAC-ссылка в рамках компании. Код модуля, навык брифа и категория — публичные справочники.
PUBLIC_EVIDENCE_TYPES = frozenset({"module", "brief_skill", "declared", "category"})
# Флаги строки, которые работодатель видит (флаги целостности теста — никогда, только «против» словами).
PUBLIC_FLAGS = frozenset({"borderline", "stale_test", "unconfirmed"})
INTERVAL_ROUND = 10  # линейка до раскрытия — с шагом 10 баллов (точный балл — после раскрытия)


def _secret(secret: str | None = None) -> str:
    return secret or get_settings().pseudonym_secret.get_secret_value()


def _mac(secret: str, *parts: str, n: int) -> str:
    digest = hmac.new(secret.encode(), "\x1f".join(parts).encode(), hashlib.sha256).digest()
    return base64.b32encode(digest).decode().lower().rstrip("=")[:n]


def candidate_ref(company_id: uuid.UUID | str, candidate_id: uuid.UUID | str, secret: str | None = None) -> str:
    """Непрозрачная ссылка на кандидата в API компании (детерминированная, своя у каждой компании)."""
    return "c" + _mac(_secret(secret), "cand", str(company_id), str(candidate_id), n=20)


def evidence_ref(company_id: uuid.UUID | str, kind: str, raw_id: str, secret: str | None = None) -> str:
    """Непрозрачная ссылка на доказательство: одно доказательство → одна ссылка внутри компании."""
    if kind in PUBLIC_EVIDENCE_TYPES:
        return raw_id
    return "e" + _mac(_secret(secret), "ev", str(company_id), kind, raw_id, n=16)


# --------------------------------------------------------------------------- псевдонимы


@dataclass(frozen=True)
class Alias:
    candidate_id: uuid.UUID
    ref: str
    pseudonym: str


def get_aliases(
    db: Session, company_id: uuid.UUID, candidate_ids: Iterable[uuid.UUID], *, secret: str | None = None
) -> dict[uuid.UUID, Alias]:
    """Псевдонимы и ссылки кандидатов для компании; новые сохраняются при первом показе."""
    ids = list(dict.fromkeys(candidate_ids))
    if not ids:
        return {}
    sec = _secret(secret)
    rows = db.execute(
        select(CandidateAlias.candidate_id, CandidateAlias.ref, CandidateAlias.pseudonym).where(
            CandidateAlias.company_id == company_id, CandidateAlias.candidate_id.in_(ids)
        )
    )
    out = {cid: Alias(cid, ref, ps) for cid, ref, ps in rows}
    missing = [cid for cid in ids if cid not in out]
    if missing:
        taken = set(
            db.scalars(select(CandidateAlias.pseudonym).where(CandidateAlias.company_id == company_id))
        )
        values = []
        for cid in missing:
            ps = unique_pseudonym(sec, str(company_id), str(cid), taken)
            taken.add(ps)
            values.append(
                {
                    "company_id": company_id,
                    "candidate_id": cid,
                    "ref": candidate_ref(company_id, cid, sec),
                    "pseudonym": ps,
                    "created_at": clock.now(),
                }
            )
        db.execute(pg_insert(CandidateAlias).on_conflict_do_nothing(), values)
        rows = db.execute(
            select(CandidateAlias.candidate_id, CandidateAlias.ref, CandidateAlias.pseudonym).where(
                CandidateAlias.company_id == company_id, CandidateAlias.candidate_id.in_(missing)
            )
        )
        out.update({cid: Alias(cid, ref, ps) for cid, ref, ps in rows})
    return out


def get_alias(db: Session, company_id: uuid.UUID, candidate_id: uuid.UUID, *, secret: str | None = None) -> Alias:
    return get_aliases(db, company_id, [candidate_id], secret=secret)[candidate_id]


def resolve_ref(db: Session, company_id: uuid.UUID, ref: str) -> uuid.UUID | None:
    """Ссылка из API компании → внутренний id кандидата (None — чужая/неизвестная ссылка)."""
    return db.scalar(
        select(CandidateAlias.candidate_id).where(CandidateAlias.company_id == company_id, CandidateAlias.ref == ref)
    )


# --------------------------------------------------------------------------- «открывал карточку»


def record_view(
    db: Session, company_id: uuid.UUID, candidate_id: uuid.UUID, *, viewer_id: uuid.UUID | None, need_id: uuid.UUID | None
) -> None:
    db.add(ProfileView(company_id=company_id, viewer_id=viewer_id, candidate_id=candidate_id, need_id=need_id, at=clock.now()))


def card_opened(db: Session, company_id: uuid.UUID, candidate_id: uuid.UUID) -> bool:
    """DECISIONS п. 7: панель Р6 открывали ИЛИ кандидат в шортлисте любого брифа компании."""
    viewed = db.scalar(
        select(exists().where(ProfileView.company_id == company_id, ProfileView.candidate_id == candidate_id))
    )
    if viewed:
        return True
    return bool(
        db.scalar(
            select(
                exists().where(
                    Shortlist.candidate_id == candidate_id,
                    Shortlist.need_id == Need.id,
                    Need.company_id == company_id,
                )
            )
        )
    )


# --------------------------------------------------------------------------- куски карточки


def test_view(payload: Mapping[str, Any], percentile: float | None) -> dict[str, Any]:
    """Полоса «топ-10/25/50%» и интервал на линейке (80%) — до раскрытия; точный балл — только после."""
    theta, se = payload.get("theta"), payload.get("se")
    out: dict[str, Any] = {"percentile": percentile}
    if theta is not None and se is not None:
        scale = load_scale()
        lo, hi = scale.score_interval(float(theta), float(se))
        out["interval"] = [int(round(lo / INTERVAL_ROUND)) * INTERVAL_ROUND, int(round(hi / INTERVAL_ROUND)) * INTERVAL_ROUND]
        out["score"] = int(round(scale.score(float(theta))))
    return out


def blind_test(payload: Mapping[str, Any], percentile: float | None) -> dict[str, Any]:
    return score_band(test_view(payload, percentile))


def freshness(payload: Mapping[str, Any], now: datetime) -> dict[str, Any] | None:
    last = payload.get("last_activity_at")
    if not last:
        return None
    f = freshness_of(datetime.fromisoformat(last), now)
    return {"relevance": f["relevance"], "band": f["band"], "label": f["band_ru"], "since": f["since_ru"]}


def fsp_public_items(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Обобщённые результаты ФСП: дисциплина, уровень, год, диапазон места, роль — без ЕКП, команды, протокола."""
    from app.fsp import vss

    levels = {str(getattr(k, "value", k)): v for k, v in getattr(vss, "_LEVEL_NOMINATIVE", {}).items()}
    out = []
    for i in payload.get("fsp_public") or ():
        out.append(
            {
                "discipline": i.get("discipline_label"),
                "level": levels.get(str(i.get("level")), i.get("level")),
                "year": i.get("year"),
                "role": i.get("role"),
                "place_band": i.get("place_band"),
                "outcome": i.get("outcome"),
                "text": i.get("text"),
            }
        )
    return out


def industries_names(codes: Iterable[str]) -> list[str]:
    names = {i["code"]: i["name"] for i in load_config("taxonomy").get("industries", ())}
    return [names.get(c, c) for c in codes]


def _salary_signal(payload: Mapping[str, Any], above_pct: float | None, known: bool) -> dict[str, Any] | None:
    """«в вилке ✓ / выше вилки» по компоненту U движка; число — только если кандидат выбрал «показывать число»."""
    if not known:
        return None
    sig = get_contact_config().visibility.salary_signal
    out: dict[str, Any] = {"signal": sig.within if above_pct is None else sig.above}
    if payload.get("salary_visibility") == "exact" and payload.get("salary_min") is not None:
        out["value"] = int(payload["salary_min"])
        out["net"] = bool(payload.get("salary_net", True))
    return out


def item_extras(
    item: Any, payload: Mapping[str, Any], features: Any, brief: Any, cfg: ScoringConfig, now: datetime
) -> dict[str, Any]:
    """Данные карточки строки подборки, которых нет в SelectionItem (кэш признаков + компонент U движка)."""
    trust = float(payload.get("trust") or 1.0)
    pct = (float(item.components.get("T", 0.0)) / trust) if (item.confirmed and trust > 0) else None
    u = component_U(features, brief, cfg)
    return {
        "test": blind_test(payload, pct),
        "freshness": freshness(payload, now),
        "fsp_line": payload.get("fsp_line"),
        "fsp_badges": list(payload.get("fsp_badges") or ()),
        "fsp_source": payload.get("fsp_source_label"),
        "region": payload.get("region_code"),
        "industries": list(payload.get("industries") or ()),
        "formats": list(payload.get("formats") or ()),
        "search_mode": payload.get("search_mode"),
        "salary": _salary_signal(payload, u.salary_above_pct, u.salary_known),
    }


# --------------------------------------------------------------------------- DTO строки для работодателя


def employer_evidence(company_id: uuid.UUID, ev: Mapping[str, Any], secret: str) -> dict[str, Any]:
    return {"type": ev["type"], "ref": evidence_ref(company_id, ev["type"], str(ev["id"]), secret), "label": ev.get("label")}


def reword_con(r: Mapping[str, Any]) -> dict[str, Any]:
    """DECISIONS п. 16: «против» по непроверенному навыку — без ссылки на резюме и самоописание
    («Kafka не подтверждена — проверьте на интервью»); заявленное остаётся только серым чипом.

    Движок (app/matching/explain.py) пишет «— только заявлен» / «только заявлено, не подтверждено» и прикладывает
    доказательство «заявлено» — здесь текст переписывается, а ссылка на заявленное убирается."""
    out = dict(r)
    if r.get("kind") != "con" or r.get("code") not in ("must_unconfirmed", "nice_unconfirmed"):
        return out
    evidence = [e for e in r.get("evidence") or () if e.get("type") != "declared"]
    names = [str(e.get("label") or e.get("id")) for e in evidence if e.get("type") == "brief_skill"]
    out["evidence"] = evidence
    if r["code"] == "must_unconfirmed" and len(names) == 1:
        out["text"] = f"Обязательный навык {names[0]} не подтверждён — проверьте на интервью"
    elif r["code"] == "must_unconfirmed":
        text = str(r.get("text") or "")
        out["text"] = text if text.endswith("на интервью") else f"{text} — проверьте на интервью"
    elif names:
        out["text"] = f"Навык {names[0]} не подтверждён — проверьте на интервью"
    return out


def employer_reason(company_id: uuid.UUID, r: Mapping[str, Any], secret: str) -> dict[str, Any]:
    return {
        "kind": r["kind"],
        "code": r["code"],
        "component": r.get("component"),
        "text": r["text"],
        "evidence": [employer_evidence(company_id, e, secret) for e in r.get("evidence") or ()],
    }


def employer_chip(company_id: uuid.UUID, raw: Any, secret: str) -> dict[str, Any]:
    c = chip_dict(raw)
    src = c.get("source")
    ref = c.get("ref")
    if src == "task" and ref:
        ref = evidence_ref(company_id, "task", str(ref), secret)
    return {
        "skill_id": c["skill_id"],
        "name": c["name"],
        "kind": c["kind"],
        "state": c["state"],
        "source": src,
        "label": c["label"],
        "evidence_ref": ref,
        "untestable": bool(c.get("untestable")),
    }


def employer_item(
    company_id: uuid.UUID,
    stored: Mapping[str, Any],
    alias: Alias,
    *,
    secret: str,
    badge: Mapping[str, Any] | None = None,
    shortlisted: bool = False,
    neighbor: str | None = None,
) -> dict[str, Any]:
    """Строка подборки для работодателя: без внутренних id, ЕКП и ссылок на протоколы (DECISIONS п. 20)."""
    x = stored.get("x") or {}
    reasons = [employer_reason(company_id, r, secret) for r in stored.get("reasons") or ()]
    return {
        "ref": alias.ref,
        "pseudonym": alias.pseudonym,
        "category": category_of(stored),
        "spec": stored["spec"],
        "grade": stored["grade"],
        "confirmed": bool(stored.get("confirmed")),
        "score": stored["score_display"],
        "parts": dict(stored["parts"]),
        "components": {k: round(float(v), 2) for k, v in stored["components"].items()},
        "rank": stored.get("rank"),
        "category_rank": stored.get("category_rank"),
        "pros": [r for r in reasons if r["kind"] == "pro"],
        "con": next((r for r in reasons if r["kind"] == "con"), None),
        "why_you": list(stored.get("why_you") or ()),
        "chips": [employer_chip(company_id, c, secret) for c in stored.get("chips") or ()],
        "hidden_by": list(stored.get("hidden_by") or ()),
        "hidden_reasons": list(stored.get("hidden_reasons") or ()),
        "has_fsp": bool(stored.get("has_fsp")),
        "fsp_line": x.get("fsp_line"),
        "fsp_badges": list(x.get("fsp_badges") or ()),
        "test": x.get("test") or {},
        "freshness": x.get("freshness"),
        "salary": x.get("salary"),
        "flags": sorted(set(stored.get("flags") or ()) & PUBLIC_FLAGS),
        "badge": dict(badge) if badge else None,
        "shortlisted": shortlisted,
        "weak_match": stored["score_display"] < get_contact_config().matching.weak_score_below,
        "neighbor_below": neighbor,
    }


# --------------------------------------------------------------------------- карточка кандидата (Р6)


def _active_disclosure(db: Session, company_id: uuid.UUID, candidate_id: uuid.UUID) -> DomainDisclosure | None:
    row = db.scalars(
        select(ContactDisclosure)
        .where(
            ContactDisclosure.company_id == company_id,
            ContactDisclosure.candidate_id == candidate_id,
            ContactDisclosure.revoked_at.is_(None),
        )
        .order_by(ContactDisclosure.granted_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return DomainDisclosure(
        id=str(row.id),
        candidate_id=str(row.candidate_id),
        company_id=str(row.company_id),
        basis=row.basis,  # type: ignore[arg-type]
        basis_id=str(row.basis_id),
        fields=tuple(str(f) for f in row.fields or ()),
        consent_text_ver=row.consent_text_ver,
        granted_at=row.granted_at,
        revoked_at=row.revoked_at,
    )


def _category_percentile(db: Session, spec: str, grade: str, payload: Mapping[str, Any], cid: str, now: datetime) -> float | None:
    """Перцентиль нижней границы в категории (как T движка, без доверия) — для полосы «топ-N%»."""
    from app.services.matching.features import load_pool

    cfg = ScoringConfig.load()
    pool = [r.features for r in load_pool(db, [(spec, grade)], now=now)]
    me = features_from_payload(cid, spec, grade, payload, now)
    if me.theta is None:
        return None
    stats = build_category_stats(pool + [me], cfg)
    return component_T(me, stats, cfg).pct


GRADE_CHANGE_REASONS = {
    "initial": "тест",
    "retake": "пересдача",
    "upgrade": "тест на повышение",
    "onboarding_extension": "дополнительные задания",
    "ext_up": "дополнительные задания вверх",
    "ext_down": "дополнительные задания вниз",
    "voluntary_downgrade": "понижение по желанию",
}
# Решение методиста — флаг целостности: работодатель флагов не видит (концепция §5.3, «кандидат видит свои»).
# Аннулированная сессия убирается из истории целиком — и смена, которую она дала, и сама отмена.
HIDDEN_GRADE_REASONS = frozenset({"invalidated"})


def employer_grade_history(rows: Sequence[Any], names: Mapping[str, str], now: datetime) -> list[dict[str, Any]]:
    annulled = {h.session_id for h in rows if h.reason in HIDDEN_GRADE_REASONS and h.session_id is not None}
    return [
        {
            "from": names.get(h.from_grade, h.from_grade) if h.from_grade else None,
            "to": names.get(h.to_grade, h.to_grade) if h.to_grade else None,
            "reason": GRADE_CHANGE_REASONS.get(h.reason, "смена грейда"),
            "when": relative_ru(h.at, now),
        }
        for h in rows
        if h.reason not in HIDDEN_GRADE_REASONS and (h.session_id is None or h.session_id not in annulled)
    ]


def _minimal_view(alias: Alias, disclosure: DomainDisclosure | None) -> dict[str, Any]:
    """Карточка без признаков подбора (категории нет или кандидат скрыт): только подпись и состояние раскрытия."""
    return {
        "view": "disclosed" if disclosure is not None else "blind",
        "pseudonym": alias.pseudonym,
        "ref": alias.ref,
        "category": None,
        "available": False,
        "note": "Кандидат сейчас не показывается в подборе",
    }


def candidate_profile_view(
    db: Session,
    *,
    company_id: uuid.UUID,
    candidate_id: uuid.UUID,
    need_id: uuid.UUID | None = None,
    offer_salary_to: int | None = None,
    require_visible: bool = False,
    secret: str | None = None,
    selection_item: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Карточка кандидата для компании с учётом раскрытия (Р6, банк, превью и карточка приглашения).

    Без действующего ContactDisclosure — CandidateBlind (0 полей ПДн, без внутренних id, ЕКП и протоколов);
    с ним — CandidateDisclosed (точный балл, выписка ФСП, разрешённые кандидатом поля).
    require_visible=False (контракт для приглашений) — всегда словарь: если признаков подбора нет или кандидат
    скрыт, возвращается минимальная карточка с псевдонимом. require_visible=True (панель Р6 из подборки или
    банка) — None, если кандидат сейчас не виден этой компании (скрыт, блок-лист) и контакт не раскрыт.
    selection_item — строка текущей версии подборки брифа (разложение Score, «за»/«против»), если есть.
    """
    sec = _secret(secret)
    now = clock.now()
    refresh_stale(db, [candidate_id])  # отзыв согласия, «не ищу», блок-лист — видны сразу, а не после пересборки
    rows = list(
        db.execute(
            select(MatchingFeatures.specialization, MatchingFeatures.grade, MatchingFeatures.visible, MatchingFeatures.payload)
            .where(MatchingFeatures.candidate_id == candidate_id)
            .order_by(MatchingFeatures.specialization)
        )
    )
    disclosure = _active_disclosure(db, company_id, candidate_id)
    blocked = {str(x) for x in ((rows[0][3].get("blocked") if rows else None) or ())}
    hidden_here = not any(r[2] for r in rows) or str(company_id) in blocked
    if disclosure is None and hidden_here:
        if require_visible:
            return None
        return _minimal_view(get_alias(db, company_id, candidate_id, secret=sec), None)
    if not rows:
        return _minimal_view(get_alias(db, company_id, candidate_id, secret=sec), disclosure)
    # Основная строка: категория строки подборки, иначе самый высокий грейд (Fullstack — последним).
    from app.matching.scoring import GRADES

    def key(r: Any) -> tuple:
        return (r[0] == "fullstack", -GRADES.index(r[1]) if r[1] in GRADES else 0, r[0])

    main = sorted(rows, key=key)[0]
    if selection_item is not None:
        main = next((r for r in rows if (r[0], r[1]) == (selection_item["spec"], selection_item["grade"])), main)
    spec, grade, _, payload = main
    cfg = ScoringConfig.load()
    cid = str(candidate_id)
    if offer_salary_to is None and need_id is not None:
        offer_salary_to = _offer_to_in_candidate_basis(db, company_id, need_id, payload, cfg)
    pct = _category_percentile(db, spec, grade, payload, cid, now)
    alias = get_alias(db, company_id, candidate_id, secret=sec)
    prof = db.get(CandidateProfile, candidate_id)
    history = employer_grade_history(
        list(
            db.scalars(
                select(GradeChange)
                .where(GradeChange.candidate_id == candidate_id, GradeChange.specialization == spec)
                .order_by(GradeChange.at, GradeChange.id)
            )
        ),
        cfg.grade_names,
        now,
    )
    modules_cfg = load_config("taxonomy")["modules"]
    cat = get_catalog()
    feats = features_from_payload(cid, spec, grade, payload, now)
    skills = [
        {
            "module": m,
            "name": modules_cfg.get(m, {}).get("name", m),
            "state": ms.tag,
            "symbol": "✓✓" if ms.tag == "strong" else "✓",
        }
        for m, ms in sorted(feats.modules.items())
        if ms.tag in ("strong", "confirmed")
    ]
    tasks = [
        {
            "title": t.title,
            "kind": t.kind,
            "result": f"{round(t.q * 5)}/5",
            "when": relative_days(t.days_ago),
            "ref": evidence_ref(company_id, "task", t.task_id, sec),
        }
        for t in sorted(feats.tasks, key=lambda t: t.days_ago)
    ]
    test = test_view(payload, pct)
    profile: dict[str, Any] = {
        "candidate_id": cid,
        "category": {
            "spec": spec,
            "grade": grade,
            "name": cfg.category_name(spec, grade),
            "borderline": bool(payload.get("borderline")),
            "fullstack": bool(payload.get("is_fullstack")),
            "all": [cfg.category_name(s, g) for s, g, vis, _ in rows if vis or disclosure is not None],
        },
        "grade_history": history,
        "test_date": relative_days(feats.test_days_ago) if feats.test_days_ago is not None else None,
        "skills": skills,
        # Заявленное (резюме, опрос) — только серые чипы «заявлено», в категорию и Score не входит (DECISIONS п. 1).
        "declared_skills": [{"skill_id": s, "name": cat.name(s), "state": "declared"} for s in payload.get("declared") or ()],
        "tasks": tasks,
        "freshness": freshness(payload, now),
        "industries": industries_names(payload.get("industries") or ()),
        "formats": list(payload.get("formats") or ()),
        "search_mode": payload.get("search_mode"),
        "has_fsp": bool(payload.get("vss")),
        "test": test,
        "fsp": fsp_public_items(payload),
        "salary_min": payload.get("salary_min"),
        "salary_visibility": payload.get("salary_visibility"),
        "region": payload.get("region_code"),
    }
    if prof is not None:
        profile["city"] = prof.city
        for key_name, column in DISCLOSURE_FIELD_COLUMNS.items():
            if column is not None:
                profile[key_name] = getattr(prof, column, None)
    view = employer_view(
        profile,
        pseudonym=alias.pseudonym,
        company_id=str(company_id),
        now=now,
        disclosure=disclosure,
        offer_salary_to=offer_salary_to,
    )
    view["ref"] = alias.ref
    view["available"] = not hidden_here
    view["fsp_line"] = payload.get("fsp_line")
    view["fsp_badges"] = list(payload.get("fsp_badges") or ())
    view["fsp_source"] = payload.get("fsp_source_label")
    if disclosure is not None:
        view["fsp_extract"] = _fsp_extract(db, candidate_id, now)
    if selection_item is not None:
        view["selection"] = employer_item(company_id, selection_item, alias, secret=sec)
    return view


def _offer_to_in_candidate_basis(
    db: Session, company_id: uuid.UUID, need_id: uuid.UUID, payload: Mapping[str, Any], cfg: ScoringConfig
) -> int | None:
    """Верх вилки брифа в базисе минимума кандидата (на руки / до НДФЛ) — для значка «в вилке ✓ / выше вилки»."""
    need = db.get(Need, need_id)
    if need is None or need.company_id != company_id or not need.salary_to:
        return None
    cand_net = bool(payload.get("salary_net", True))
    if bool(need.salary_net) == cand_net:
        return int(need.salary_to)
    ndfl = float(cfg.section("U")["ndfl_rate"])
    return int(round(need.salary_to * (1 - ndfl) if cand_net else need.salary_to / (1 - ndfl)))


def _fsp_extract(db: Session, candidate_id: uuid.UUID, now: datetime) -> str | None:
    """«Выписка из протокола» с номером ЕКП — только после раскрытия (концепция §6.2)."""
    from app.fsp.vss import protocol_extract
    from app.services.matching import sources

    accounts = sources.load_accounts(db, [candidate_id])
    prof = sources.load_fsp_profiles(db, [candidate_id], accounts, now)[candidate_id]
    if prof.state.value == "not_linked":
        return None
    try:
        return protocol_extract(prof, now)
    except Exception:  # noqa: BLE001 — выписка не должна ронять карточку
        return None


# ПДн из матрицы видимости с правилом if_allowed / hide → в DTO API они живут только в блоке `disclosed`.
DISCLOSED_KEYS = ("full_name", "email", "phone", "telegram", "resume", "current_employer", "university", "city")


def card_dto(view: Mapping[str, Any]) -> dict[str, Any]:
    """Проекция candidate_profile_view → CandidateCardOut: ПДн вынесены в блок `disclosed` (есть только у
    раскрытой карточки), даты — относительные, полоса теста — и в раскрытой карточке."""
    out = {
        k: v
        for k, v in view.items()
        if k not in DISCLOSED_KEYS and k not in ("disclosure", "fsp_extract", "salary_min", "grade_history", "photo_url")
    }
    out["salary"] = view.get("salary_min")
    out["grade_history"] = [
        {"from_grade": h.get("from"), "to_grade": h.get("to"), "reason": h.get("reason"), "when": h.get("when")}
        for h in view.get("grade_history") or ()
    ]
    test = view.get("test")
    if isinstance(test, Mapping) and "band" not in test:
        out["test"] = {**test, "band": score_band(test).get("band")}
    out["disclosed"] = None
    if view.get("view") == "disclosed":
        d = view.get("disclosure") or {}
        out["disclosed"] = {
            "basis": d.get("basis") or "",
            "fields": list(d.get("fields") or ()),
            **{k: view.get(k) for k in DISCLOSED_KEYS},
            "fsp_extract": view.get("fsp_extract"),
        }
    return out


def strip_internal(obj: Any, banned: Sequence[str] = ("candidate_id", "user_id")) -> Any:
    """Защита в глубину: убрать внутренние ключи из произвольного JSON для работодателя."""
    if isinstance(obj, Mapping):
        return {k: strip_internal(v, banned) for k, v in obj.items() if k not in banned}
    if isinstance(obj, list | tuple):
        return [strip_internal(v, banned) for v in obj]
    return obj


__all__ = [
    "Alias",
    "candidate_profile_view",
    "candidate_ref",
    "card_opened",
    "employer_item",
    "evidence_ref",
    "get_alias",
    "get_aliases",
    "item_extras",
    "record_view",
    "resolve_ref",
]
