"""Банк кандидатов без брифа (Р9, концепция §3.2 шаг 7, spec-contact §2.1).

Фильтры: специализация, грейд, категория, стек (переключатель «только подтверждённые»), «есть ФСП», отрасль,
режим поиска. Сортировка — балл банка движка app.matching.scoring.bank_score: 0,55·T + 0,25·F + 0,20·A
(веса в config/scoring.yaml: bank.weights). Жёсткие правила те же, что у подборки: только видимые работодателям
(категория подтверждена тестом, согласие на слепую карточку, режим не «не ищу», возраст), без блок-листа
кандидата и действующей паузы пары. Кандидат показывается один раз — лучшей своей строкой (Fullstack-кандидат
есть в нескольких категориях). Наружу — только псевдоним и непрозрачная ссылка в рамках компании (DECISIONS п. 20).
«📌 в шортлист брифа X» — PUT /api/needs/{need_id}/shortlist/{ref} той же ссылкой.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.errors import ApiError, not_found
from app.matching.coverage import candidate_mins, quantile
from app.matching.scoring import (
    GRADES,
    ScoringConfig,
    ScoringContext,
    build_category_stats,
    display_parts,
    rank_bank,
    round1,
    skill_value,
)
from app.matching.selection import FILTER_LABELS
from app.matching.skills import get_catalog
from app.models.candidate import CandidateProfile, SurveyResponse
from app.models.enums import NeedStatus, QualificationStatus, SearchMode
from app.models.matching import Need, Shortlist
from app.models.testing import Qualification
from app.models.users import User
from app.services.matching import features as feat
from app.services.matching import needs as needs_svc
from app.services.matching import privacy


def _bad(code: str, message: str) -> ApiError:
    return ApiError(422, code, message, None)


@dataclass
class BankQuery:
    spec: str | None = None
    grade: str | None = None
    category: str | None = None  # «backend_python:middle»
    skills: Sequence[str] = ()
    confirmed_only: bool = True  # стек: «только подтверждённые» (тест, задача, ФСП) или ещё и заявленные
    has_fsp: bool | None = None
    industry: str | None = None
    search_mode: str | None = None
    page: int = 1
    hidden_page: int = 1  # страница группы «Скрыто фильтрами» (фильтр «есть ФСП»)
    need_id: uuid.UUID | None = None  # отметить, кто уже в шортлисте этого брифа
    interest: bool | None = None  # True — только с сигналом интереса; видны лишь открытые карточки (DECISIONS 24)
    interest_first: bool = False  # сначала с сигналом (порядок внутри групп сохраняется)


def _validate(q: BankQuery) -> tuple[list[tuple[str, str]] | None, list[str] | None]:
    cfg = ScoringConfig.load()
    known = {s["code"] for s in cfg.taxonomy["specializations"]}
    cats: list[tuple[str, str]] | None = None
    specs: list[str] | None = None
    if q.category:
        spec, _, grade = q.category.partition(":")
        if spec not in known or grade not in GRADES:
            raise _bad("category_invalid", "Категория указывается как «специализация:грейд», например backend_python:middle")
        cats = [(spec, grade)]
    if q.spec is not None:
        if q.spec not in known:
            raise _bad("spec_invalid", "Неизвестная специализация")
        specs = [q.spec]
    if q.grade is not None and q.grade not in GRADES:
        raise _bad("grade_invalid", "Грейд: intern, junior, middle или senior")
    cat = get_catalog()
    bad = [s for s in q.skills if s not in cat.skills]
    if bad:
        raise _bad("skill_invalid", "Нет в справочнике навыков: " + ", ".join(bad))
    if q.industry is not None and q.industry not in {i["code"] for i in cfg.taxonomy.get("industries", ())}:
        raise _bad("industry_invalid", "Неизвестная сфера бизнеса")
    if q.search_mode is not None and q.search_mode not in (SearchMode.active.value, SearchMode.open.value):
        raise _bad("search_mode_invalid", "Режим поиска: active (активно ищу) или open (открыт к предложениям)")
    if q.page < 1 or q.hidden_page < 1:
        raise _bad("page_invalid", "Номер страницы начинается с 1")
    return cats, specs


def _skill_chips(features: Any, skills: Sequence[str], cfg: ScoringConfig) -> tuple[list[dict[str, Any]], bool, bool]:
    """Чипы запрошенных навыков (оценка навыка — движком, как v_k в K) и прошёл ли кандидат фильтр стека."""
    cat = get_catalog()
    thr = float(cfg.section("Cov")["confirm_threshold"])
    chips = []
    all_confirmed = all_known = True
    for sid in skills:
        v, src, ref, label = skill_value(sid, features, features.grade, cfg, cat)
        declared = sid in features.declared_skills
        if v >= thr:
            state = "confirmed"
        elif declared:
            state, label = "declared", "заявлено"
        elif v > 0:
            state = "weak"
        else:
            state, label = "no_data", "нет данных"
        all_confirmed &= state == "confirmed"
        all_known &= state in ("confirmed", "declared")
        chips.append(
            {
                "skill_id": sid,
                "name": cat.name(sid),
                "kind": "filter",
                "state": state,
                "source": src if state == "confirmed" else None,
                "label": label,
                "evidence_ref": ref if src in ("test", "fsp") else None,
                "untestable": bool(cat.skills[sid].untestable),
            }
        )
    return chips, all_confirmed, all_known


def bank_search(db: Session, user: User, q: BankQuery, *, secret: str) -> dict[str, Any]:
    company_id = needs_svc._company(user)
    cats, specs = _validate(q)
    feat.ensure_fresh(db)
    cfg = ScoringConfig.load()
    rows_all = feat.load_pool(db, cats, specs=specs, include_unconfirmed=True)
    if q.grade is not None:
        rows_all = [r for r in rows_all if r.features.grade == q.grade]
    # Перцентиль T — по всей категории (как в подборке), а не по списку после фильтров: пул выше — это все
    # видимые строки выбранных категорий целиком.
    stats = build_category_stats(r.features for r in rows_all)
    # Без брифа нет флага «стажировка»: 16–17 лет в банке не показываем (концепция §4.3, осторожно в пользу защиты).
    rows = needs_svc._company_hard_filter(db, company_id, rows_all, is_internship=False, salary_from=None)
    chips_of: dict[tuple[str, str], list[dict[str, Any]]] = {}
    kept, hidden = [], []
    for r in rows:
        f = r.features
        if q.industry is not None and q.industry not in f.industries:
            continue
        if q.search_mode is not None and r.payload.get("search_mode") != q.search_mode:
            continue
        if q.skills:
            chips, all_confirmed, all_known = _skill_chips(f, q.skills, cfg)
            if not (all_confirmed if q.confirmed_only else all_known):
                continue
            chips_of[(f.candidate_id, f.spec)] = chips
        # «Есть ФСП» никого не удаляет: не прошедшие уходят в «Скрыто фильтрами» (концепция §6.4 — гарантия
        # для кандидатов без истории ФСП; фильтр включает только сам работодатель).
        if q.has_fsp is not None and f.has_fsp != q.has_fsp:
            hidden.append(r)
        else:
            kept.append(r)
    by_key = {(r.features.candidate_id, r.features.spec): r for r in kept + hidden}
    ctx = ScoringContext.build(cfg=cfg, category_stats=stats)
    best = _best_rows(rank_bank([r.features for r in kept], ctx=ctx, query_id=f"bank|{company_id}"))
    best_hidden = _best_rows(rank_bank([r.features for r in hidden], ctx=ctx, query_id=f"bank|{company_id}"))
    from app.services.contact.interest import visible_for_company  # локально: контакт импортирует подбор

    # Сигнал интереса (DECISIONS 24) — отдельный от Score признак: влияет только на фильтр/порядок по просьбе
    # работодателя, и только по открытым им карточкам.
    interest = visible_for_company(db, company_id)
    if q.interest is not None:
        best = [b for b in best if (uuid.UUID(b.candidate_id) in interest) == q.interest]
    if q.interest_first:
        best = sorted(best, key=lambda b: uuid.UUID(b.candidate_id) not in interest)
    size = int(cfg.section("selection")["page_size"])
    total = len(best)
    page_rows = best[(q.page - 1) * size : q.page * size]
    hidden_rows = best_hidden[(q.hidden_page - 1) * size : q.hidden_page * size]
    ids = [uuid.UUID(b.candidate_id) for b in page_rows + hidden_rows]
    aliases = privacy.get_aliases(db, company_id, ids, secret=secret)
    pinned = _pinned_in(db, company_id, ids)
    invited = needs_svc._invitation_status(db, company_id, ids)
    now = clock.now()
    w = cfg.section("bank")["weights"]
    cat = get_catalog()

    def dto(b: Any, hidden_by: Sequence[str] = ()) -> dict[str, Any]:
        cid = uuid.UUID(b.candidate_id)
        r = by_key[(b.candidate_id, b.spec)]
        f = r.features
        trust = float(r.payload.get("trust") or 1.0)
        pct = (b.x["T"] / trust) if (b.confirmed and trust > 0) else None
        contrib = {k: 100.0 * float(w[k]) * float(b.x[k]) for k in b.x}
        return (
            {
                "ref": aliases[cid].ref,
                "pseudonym": aliases[cid].pseudonym,
                "category": cfg.category_name(b.spec, b.grade),
                "spec": b.spec,
                "grade": b.grade,
                "confirmed": b.confirmed,
                "score": round1(b.score),
                "parts": display_parts(contrib, b.score),
                "components": {k: round(float(v), 2) for k, v in b.x.items()},
                "test": privacy.blind_test(r.payload, pct),
                "freshness": privacy.freshness(r.payload, now),
                "has_fsp": f.has_fsp,
                "fsp_line": r.payload.get("fsp_line"),
                "fsp_badges": list(r.payload.get("fsp_badges") or ()),
                "skills": chips_of.get((b.candidate_id, b.spec), []),
                "modules": [
                    {"module": m, "name": cat.module_names.get(m, m), "symbol": "✓✓" if ms.tag == "strong" else "✓"}
                    for m, ms in sorted(f.modules.items())
                    if ms.tag in ("strong", "confirmed")
                ],
                "declared": [cat.name(s) for s in sorted(f.declared_skills)],
                "industries": privacy.industries_names(sorted(f.industries)),
                "formats": sorted(f.formats),
                "search_mode": r.payload.get("search_mode"),
                "region": r.payload.get("region_code"),
                "shortlisted_in": pinned.get(cid, []),
                "shortlisted": q.need_id in pinned.get(cid, []) if q.need_id else False,
                "invitation_status": invited[cid][0] if cid in invited else None,
                "invitation_label": invited[cid][1] if cid in invited else None,
                "interest": interest.get(cid),
                "hidden_by": list(hidden_by),
            }
        )

    hidden_label = FILTER_LABELS["has_fsp"] if q.has_fsp else "Без ФСП"
    return {
        "total": total,
        "page": q.page,
        "page_size": size,
        "pages": max(1, (total + size - 1) // size),
        "items": [dto(b) for b in page_rows],
        "hidden": {
            "count": len(best_hidden),
            "page": q.hidden_page,
            "label": hidden_label if q.has_fsp is not None else None,
            "items": [dto(b, ("has_fsp",)) for b in hidden_rows],
        },
        "pin_targets": pin_targets(db, company_id),
        "weights": {k: float(v) for k, v in w.items()},
    }


def bank_summary(db: Session, user: User) -> dict[str, Any]:
    """Сводка банка «специализация × грейд» ОДНИМ запросом для карты талантов (вместо ~14 вызовов GET /api/bank).

    Те же правила, что у списка банка: видимые работодателям кандидаты, жёсткие фильтры компании (блок-лист, пауза
    пары). Число в ячейке равно размеру списка банка по этой категории (`GET /api/bank?category=…`): Fullstack-кандидат
    есть и в своей категории, и в категориях обеих квалификаций; `total` — уникальные кандидаты. Ожидания по зарплате —
    P25/P50/P75 «на руки» с защитой приватности: пока кандидатов с указанным минимумом меньше
    coverage.quantiles_min_n, квантилей нет; значения округляются до ступени гистограммы (coverage.histogram_step),
    как в панели охвата брифа. Ранжировать для сводки не нужно: считаются только размеры категорий.
    «Ждут теста» — прошли опрос по направлению, но подтверждённой категории нет (только счётчик, без людей)."""
    company_id = needs_svc._company(user)
    feat.ensure_fresh(db)
    cfg = ScoringConfig.load()
    cc = cfg.section("coverage")
    min_n, step = int(cc["quantiles_min_n"]), int(cc["histogram_step"])
    rows = needs_svc._company_hard_filter(db, company_id, feat.load_pool(db, None), is_internship=False, salary_from=None)
    cells: dict[tuple[str, str], list[Any]] = {}
    fsp_by_candidate: dict[str, bool] = {}
    for r in rows:
        cells.setdefault((r.features.spec, r.features.grade), []).append(r.features)
        fsp_by_candidate[r.features.candidate_id] = bool(r.features.has_fsp)
    waiting = _survey_only_counts(db)
    total, total_fsp = len(fsp_by_candidate), sum(fsp_by_candidate.values())

    specs_out = []
    total_waiting = 0
    for s in cfg.taxonomy["specializations"]:
        code = s["code"]
        status = s.get("status", "active")
        cell_out = []
        for g in GRADES:
            members = cells.get((code, g), [])
            n = len(members)
            with_fsp = sum(1 for f in members if f.has_fsp)
            mins = sorted(candidate_mins(members, True, cfg))
            quant = None
            if len(mins) >= min_n:
                p25, p50, p75 = (int(round(quantile(mins, q) / step)) * step for q in (0.25, 0.5, 0.75))
                quant = {"p25": p25, "p50": p50, "p75": p75}
            cell_out.append(
                {"grade": g, "n": n, "with_fsp": with_fsp, "fsp_share": round(with_fsp / n, 3) if n else None, "salary_min": quant}
            )
        n_spec = sum(c["n"] for c in cell_out)
        fsp_spec = sum(c["with_fsp"] for c in cell_out)
        total_waiting += waiting.get(code, 0)
        specs_out.append(
            {
                "spec": code,
                "name": s["name"],
                "status": status,
                "testable": bool(status == "active" and s.get("blueprint")),
                "n": n_spec,
                "with_fsp": fsp_spec,
                "survey_only": waiting.get(code, 0),
                "cells": cell_out,
            }
        )
    return {
        "specs": specs_out,
        "total": total,
        "with_fsp": total_fsp,
        "survey_only": total_waiting,
        "privacy_min_n": min_n,
        "salary_basis": "net",
        "as_of": clock.now(),
    }


def _survey_only_counts(db: Session) -> dict[str, int]:
    """Сколько кандидатов прошли опрос по специализации (последний завершённый опрос), но подтверждённой категории
    по ней нет. «Не ищу» (скрыт из банка) не считаем: человек просил не показывать его в подборках."""
    latest = (
        select(SurveyResponse.candidate_id.label("cid"), SurveyResponse.chosen_spec.label("spec"))
        .where(SurveyResponse.completed_at.is_not(None), SurveyResponse.chosen_spec.is_not(None))
        .distinct(SurveyResponse.candidate_id)
        .order_by(SurveyResponse.candidate_id, SurveyResponse.completed_at.desc())
        .subquery()
    )
    confirmed = (
        select(Qualification.candidate_id.label("cid"), Qualification.specialization.label("spec"))
        .where(Qualification.status == QualificationStatus.confirmed.value)
        .subquery()
    )
    q = (
        select(latest.c.spec, func.count())
        .select_from(latest)
        .join(CandidateProfile, CandidateProfile.user_id == latest.c.cid)
        .outerjoin(confirmed, and_(confirmed.c.cid == latest.c.cid, confirmed.c.spec == latest.c.spec))
        .where(confirmed.c.cid.is_(None), CandidateProfile.search_mode != SearchMode.hidden.value)
        .group_by(latest.c.spec)
    )
    return {str(spec): int(n) for spec, n in db.execute(q)}


def skills_dictionary() -> list[dict[str, Any]]:
    """Справочник навыков для фильтров банка и брифа: id, название, модули теста, флаг «проверяется тестом»."""
    cat = get_catalog()
    out = []
    for s in cat.skills.values():
        out.append(
            {
                "id": s.id,
                "name": s.name,
                "category": s.category,
                "modules": list(s.modules),
                "specs": sorted(s.spec_weights),
                "testable": bool(s.modules) and not s.untestable,
                "untestable_reason": s.untestable_reason,
            }
        )
    out.sort(key=lambda d: (d["category"], d["name"].lower()))
    return out


def _best_rows(ranked: Sequence[Any]) -> list[Any]:
    """Один раз на кандидата — лучшей строкой (Fullstack-кандидат есть в нескольких категориях)."""
    seen: set[str] = set()
    out = []
    for b in ranked:
        if b.candidate_id in seen:
            continue
        seen.add(b.candidate_id)
        out.append(b)
    return out


def _pinned_in(db: Session, company_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
    if not ids:
        return {}
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    rows = db.execute(
        select(Shortlist.candidate_id, Shortlist.need_id)
        .join(Need, Need.id == Shortlist.need_id)
        .where(Need.company_id == company_id, Shortlist.candidate_id.in_(list(ids)))
        .order_by(Shortlist.pinned_at)
    )
    for cid, nid in rows:
        out.setdefault(cid, []).append(nid)
    return out


def pin_targets(db: Session, company_id: uuid.UUID) -> list[dict[str, Any]]:
    """Брифы компании, в шортлист которых можно закрепить кандидата из банка (кнопка «📌 в бриф X»)."""
    cfg = ScoringConfig.load()
    rows = db.scalars(
        select(Need)
        .where(Need.company_id == company_id, Need.status != NeedStatus.closed.value)
        .order_by(Need.created_at.desc())
    )
    return [
        {
            "need_id": n.id,
            "title": n.title or cfg.category_name(n.specialization or "", n.grade or ""),
            "category": cfg.category_name(n.specialization or "", n.grade or ""),
        }
        for n in rows
    ]


def bank_card(db: Session, user: User, ref: str, *, secret: str) -> dict[str, Any]:
    """Слепая (или раскрытая) карточка из банка. Открытие записывается: «открывал карточку» (DECISIONS п. 7)."""
    company_id = needs_svc._company(user)
    cid = privacy.resolve_ref(db, company_id, ref)
    if cid is None:
        raise not_found("candidate_not_found", "Кандидат не найден")
    view = privacy.candidate_profile_view(
        db, company_id=company_id, candidate_id=cid, require_visible=True, secret=secret
    )
    if view is None:
        raise not_found("candidate_not_found", "Кандидат сейчас недоступен")
    privacy.record_view(db, company_id, cid, viewer_id=user.id, need_id=None)
    db.flush()
    needs_svc._attach_interest(db, view, company_id, cid)
    return view
