"""Бриф → категории и охват → «как мы проверим» → версии подборки → шортлист (концепция §3.2 шаги 3–7).

Все правила — в движках app/matching/*: разбор брифа и проверка вилки (brief_parser), рекомендация
категорий и охват с защитой приватности (coverage), Score и объяснения (scoring, explain), версии,
неразрушающие фильтры, diff, откат и шортлист (selection). Здесь — хранение, права и сборка входов.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from sqlalchemy import Text, bindparam, cast, delete, func, insert, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, make_transient_to_detached

from app.contact.settings import get_contact_config
from app.core.clock import clock
from app.core.configs import load_config
from app.core.errors import ApiError, conflict, not_found
from app.matching import brief_parser as bp
from app.matching import market
from app.matching.coverage import (
    CategoryRecommendation,
    category_cards,
    family_catalog_from_registry,
    family_info_from_registry,
    how_we_check,
    plan_check_set,
    recommend_categories,
)
from app.matching.scoring import BriefSpec, ScoringConfig, build_category_stats
from app.matching.selection import (
    FILTER_LABELS,
    Filter,
    Pin,
    SelectionError,
    SelectionHistory,
    build_version,
    diff_badges,
    page,
    shortlist_view,
)
from app.matching.selection import Shortlist as EngineShortlist
from app.matching.skills import get_catalog
from app.models.candidate import SurveyResponse
from app.models.contact import Cooldown, Invitation
from app.models.enums import PAIR_ACTIVE_INVITATION_STATUSES, NeedStatus
from app.models.matching import ImpressionLog, Need, NeedVersion, Shortlist
from app.models.users import User
from app.services.matching import features as feat
from app.services.matching import llm_brief, privacy, store
from app.services.matching.gcpause import no_cycle_gc
from app.services.matching.store import (
    filters_to_store,
    item_to_store,
    neighbor_to_store,
    ranks_only,
    version_from_row,
)

log = logging.getLogger("vzaimno.matching.needs")

# --------------------------------------------------------------------------- ошибки


def _unprocessable(code: str, message: str, details: Any = None) -> ApiError:
    return ApiError(422, code, message, details)


def _need_not_found() -> ApiError:
    return not_found(message="Бриф не найден")


# --------------------------------------------------------------------------- разбор и правки брифа


@dataclass
class BriefInput:
    """Бриф из формы: текст, вилка и правки работодателя поверх разбора (источник «вручную»)."""

    text: str
    salary_from: int | None = None
    salary_to: int | None = None
    salary_net: bool | None = None
    spec: str | None = None
    grade: str | None = None
    must: list[str] | None = None
    nice: list[str] | None = None
    formats: list[str] | None = None
    city: str | None = None
    clear_city: bool = False


@dataclass
class Overrides:
    """Ручные правки, переживающие правку текста: специализация, грейд, вид навыка (must/nice/удалён), формат, город."""

    spec: str | None = None
    grade: str | None = None
    skills: dict[str, str] = field(default_factory=dict)  # skill_id → must | nice | removed
    formats: list[str] | None = None
    city: str | None = None
    city_set: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec,
            "grade": self.grade,
            "skills": dict(self.skills),
            "formats": self.formats,
            "city": self.city,
            "city_set": self.city_set,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> Overrides:
        d = d or {}
        return cls(
            spec=d.get("spec"),
            grade=d.get("grade"),
            skills=dict(d.get("skills") or {}),
            formats=d.get("formats"),
            city=d.get("city"),
            city_set=bool(d.get("city_set")),
        )


def _parse(inp: BriefInput) -> bp.ParsedBrief:
    """Разбор правилами (детерминированное ядро) + необязательное LLM-дополнение навыков с проверенной цитатой."""
    rules = bp.parse_brief(inp.text, salary_from=inp.salary_from, salary_to=inp.salary_to, salary_net=inp.salary_net)
    return llm_brief.augment(rules)


def merge_overrides(base: Overrides, parsed: bp.ParsedBrief, inp: BriefInput) -> Overrides:
    """Правки из формы → операции над разбором. Списки must/nice — желаемое состояние: что не в них — удалено."""
    ov = Overrides(**base.to_dict())
    if inp.spec is not None:
        ov.spec = inp.spec
    if inp.grade is not None:
        ov.grade = inp.grade
    if inp.must is not None or inp.nice is not None:
        current = apply_overrides(parsed, ov)
        must = list(dict.fromkeys(inp.must if inp.must is not None else current.must))
        nice = [s for s in dict.fromkeys(inp.nice if inp.nice is not None else current.nice) if s not in must]
        ops: dict[str, str] = {}
        for chip in parsed.skills:
            if chip.skill_id not in must and chip.skill_id not in nice:
                ops[chip.skill_id] = "removed"
        for sid in nice:
            ops[sid] = "nice"
        for sid in must:
            ops[sid] = "must"
        ov.skills = ops
    if inp.formats is not None:
        ov.formats = list(dict.fromkeys(inp.formats))
    if inp.clear_city:
        ov.city, ov.city_set = None, True
    elif inp.city is not None:
        ov.city, ov.city_set = inp.city, True
    return ov


def apply_overrides(p: bp.ParsedBrief, ov: Overrides) -> bp.ParsedBrief:
    """Разбор + ручные правки через функции движка (set_spec / set_skill / …): must ≤ 5 проверяет движок."""
    try:
        if ov.spec:
            p = bp.set_spec(p, ov.spec)
        if ov.grade:
            p = bp.set_grade(p, ov.grade)
        if ov.skills:
            for sid, kind in ov.skills.items():
                if kind == "removed" and p.chip(sid) is not None:
                    p = bp.remove_skill(p, sid)
            # Сначала понижаем в nice, потом повышаем в must: промежуточное состояние не превышает лимит must.
            for want in ("nice", "must"):
                for sid, kind in ov.skills.items():
                    chip = p.chip(sid)
                    if kind == want and (chip is None or chip.kind != want):
                        p = bp.set_skill(p, sid, want)
        if ov.formats is not None:
            p = bp.set_formats(p, ov.formats)
        if ov.city_set:
            p = bp.set_city(p, ov.city)
    except bp.BriefEditError as e:
        raise _unprocessable("brief_invalid", str(e)) from e
    return p


def salary_problems(p: bp.ParsedBrief) -> list[dict[str, str]]:
    """DECISIONS п. 6: > 2,0× и некорректная вилка — блок (422); 1,6–2,0× — мягкое предупреждение."""
    chk = p.salary_check
    if chk.status in ("missing", "incomplete", "invalid", "blocked"):
        raise _unprocessable(f"salary_{chk.status}", chk.message or "Проверьте вилку", {"field": "salary"})
    if chk.status == "warn":
        return [{"code": "salary_wide", "message": chk.message or "Широкая вилка"}]
    return []


def parse_dict(p: bp.ParsedBrief) -> dict[str, Any]:
    """Сериализация разбора для хранения в Need.parsed (то, что вернул движок, + выбранные spec/grade)."""
    return p.to_dict() | {"top_spec": p.top_spec, "top_grade": p.top_grade, "llm_used": llm_brief.llm_used(p)}


def _probs(src: Mapping[str, Any] | None, top: str | None, names: Mapping[str, str]) -> dict[str, Any]:
    src = src or {"value": {}, "source": bp.SOURCE_RULE, "quotes": []}
    values = dict(src.get("value") or {})
    if top is None and values:
        top = max(values, key=lambda k: values[k])
    probs = sorted(values.items(), key=lambda kv: -float(kv[1]))
    return {
        "top": top or "",
        "top_name": names.get(top or "", top or ""),
        "probs": [{"code": k, "name": names.get(k, k), "p": round(float(v), 4)} for k, v in probs],
        "source": src.get("source") or bp.SOURCE_RULE,
        "quotes": list(src.get("quotes") or ()),
    }


def parse_out(d: Mapping[str, Any], categories: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Разбор движка (ParsedBrief.to_dict) → DTO живой карточки Р3 с названиями для показа."""
    cfg = ScoringConfig.load()
    cat = get_catalog()
    sal = d.get("salary")
    salary = None
    if sal:
        v = sal.get("value") or {}
        salary = {
            "salary_from": v.get("from"),
            "salary_to": v.get("to"),
            "net": v.get("net"),
            "yearly": bool(v.get("yearly")),
            "source": sal.get("source") or bp.SOURCE_RULE,
            "quotes": list(sal.get("quotes") or ()),
        }

    def sourced(s: Mapping[str, Any] | None, names: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        if not s:
            return None
        value = s.get("value")
        name = None
        if names is not None and isinstance(value, str):
            n = names.get(value)
            name = getattr(n, "name", n)
        return {"value": value, "name": name, "source": s.get("source"), "quotes": list(s.get("quotes") or ())}

    q = d.get("question")
    return {
        "parser_version": d.get("parser_version") or "",
        "llm_used": bool(d.get("llm_used")),
        "salary": salary,
        "salary_check": d.get("salary_check") or {"status": "missing"},
        "skills": list(d.get("skills") or ()),
        "spec": _probs(d.get("spec_probs"), d.get("top_spec"), cfg.spec_names),
        "grade": _probs(d.get("grade_probs"), d.get("top_grade"), cfg.grade_names),
        "formats": [sourced(f, cat.formats) for f in d.get("formats") or ()],
        "city": sourced(d.get("city"), cat.cities),
        "relocation": sourced(d.get("relocation")),
        "experience_years": sourced(d.get("experience_years")),
        "question": None
        if not q
        else {
            "field": q["field"],
            "text": q["text"],
            "options": [{"code": o[0], "name": o[1]} for o in q.get("options") or ()],
        },
        "categories": [dict(c) for c in categories],
    }


def market_salary(spec: str, grade: str | None, salary_to: int | None, salary_net: bool) -> dict[str, Any]:
    """Рыночная вилка (открытые данные) + отношение переданной вилки к p25. Подсказка, не влияет на Score."""
    b = market.benchmark(spec, grade)
    if b is None:
        return {"available": False}
    out: dict[str, Any] = {"available": True, **b}
    if salary_to:
        ratio = float(load_config("trust")["flags"]["salary_far_below_market"]["ratio"])
        r = market.salary_vs_market(spec, grade, salary_to, salary_net, ratio)
        if r:
            out["ratio_to_p25"], out["far_below"] = r["ratio_to_p25"], r["far_below"]
    return out


def parse_preview(inp: BriefInput) -> dict[str, Any]:
    """Живая карточка брифа (Р3): чипы с источниками и цитатами, вероятности, вопрос, проверка вилки
    и рекомендованные категории (без N — счётчики кандидатов показывает Р4 уже созданного брифа)."""
    parsed = _parse(inp)
    p = apply_overrides(parsed, merge_overrides(Overrides(), parsed, inp))
    recs = recommend_categories(p.spec_probs.value, p.grade_probs.value)
    return parse_out(parse_dict(p), [_rec_out(r) for r in recs])


def _rec_out(r: Any, selected: bool | None = None) -> dict[str, Any]:
    out = {
        "spec": r.spec,
        "grade": r.grade,
        "p": round(float(r.p), 4),
        "kind": r.kind,
        "name": r.name,
        "status": r.status,
        "n": r.n,
        "note": r.note,
    }
    if selected is not None:
        out["selected"] = selected
    return out


# --------------------------------------------------------------------------- категории и счётчики


def _counts(db: Session) -> dict[tuple[str, str], int]:
    """N видимых по категориям + лист ожидания survey_only-специализаций (прошли опрос, ждут теста)."""
    counts = feat.category_counts(db)
    cfg = ScoringConfig.load()
    survey_only = [s["code"] for s in cfg.taxonomy["specializations"] if s.get("status") == "survey_only"]
    if survey_only:
        rows = db.execute(
            select(SurveyResponse.chosen_spec, func.count(func.distinct(SurveyResponse.candidate_id)))
            .where(SurveyResponse.chosen_spec.in_(survey_only), SurveyResponse.completed_at.is_not(None))
            .group_by(SurveyResponse.chosen_spec)
        )
        waiting = {s: int(n) for s, n in rows}
        for s in survey_only:
            for g in cfg.cuts:
                counts[(s, g)] = waiting.get(s, 0)
    return counts


def _default_selected(recs: Sequence[Any]) -> set[tuple[str, str]]:
    """Р4: «выбирает основную и при желании смежные» — по умолчанию выбрана только основная категория; если она
    пока не тестируется (лист ожидания), — первая тестируемая из предложенных или смежная."""
    testable = [r for r in recs if r.status != "survey_only"]
    main = next((r for r in testable if r.kind == "primary"), testable[0] if testable else None)
    return {(main.spec, main.grade)} if main is not None else set()


def _recommend(db: Session, p: bp.ParsedBrief, previous: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """1–3 категории с P ≥ 0,10 (+ смежная при малом N). По умолчанию выбрана основная;
    прежний выбор работодателя сохраняется."""
    recs = recommend_categories(p.spec_probs.value, p.grade_probs.value, counts=_counts(db))
    prev = {(c["spec"], c["grade"]): bool(c.get("selected")) for c in previous}
    defaults = _default_selected(recs)
    out = []
    for r in recs:
        out.append(_rec_out(r, prev.get((r.spec, r.grade), (r.spec, r.grade) in defaults)))
    for c in previous:  # вручную добавленные категории не теряются при пересчёте
        if c.get("kind") == "manual" and (c["spec"], c["grade"]) not in {(o["spec"], o["grade"]) for o in out}:
            out.append(dict(c))
    return out


def selected_categories(need: Need) -> list[tuple[str, str]]:
    return [(c["spec"], c["grade"]) for c in need.categories or () if c.get("selected")]


# --------------------------------------------------------------------------- создание и правка


def _store_brief(db: Session, need: Need, inp: BriefInput, ov: Overrides) -> list[dict[str, str]]:
    parsed = _parse(inp)
    p = apply_overrides(parsed, ov)
    warnings = salary_problems(p)
    brief = bp.to_brief_spec(p, str(need.id))
    must_chips = [_chip_dict(c) for c in p.skills if c.kind == "must"]
    nice_chips = [_chip_dict(c) for c in p.skills if c.kind == "nice"]
    need.raw_text = inp.text
    need.salary_from = int(brief.salary_from or 0)
    need.salary_to = int(brief.salary_to or 0)
    need.salary_net = bool(brief.salary_net)
    need.specialization = brief.spec
    need.grade = brief.grade
    need.spec_probs = {k: round(v, 6) for k, v in p.spec_probs.value.items()}
    need.grade_probs = {k: round(v, 6) for k, v in p.grade_probs.value.items()}
    need.must_skills = must_chips
    need.nice_skills = nice_chips
    need.format = sorted(brief.formats)[0] if brief.formats else None
    need.city = brief.city
    need.parser_version = p.parser_version
    need.llm_used = llm_brief.llm_used(p)
    need.clarifying_question = parse_dict(p)["question"]
    need.categories = _recommend(db, p, need.categories or ())
    need.parsed = {
        "parse": parse_dict(p),
        "overrides": ov.to_dict(),
        "brief": brief.to_dict(),
        "warnings": warnings,
    }
    return warnings


def _chip_dict(c: bp.SkillChip) -> dict[str, Any]:
    return {
        "skill": c.skill_id,
        "name": c.name,
        "span": [c.quote.start, c.quote.end] if c.quote else None,
        "quote": c.quote.text if c.quote else None,
        "source": c.source,
        "reason": c.reason,
        "untestable": c.untestable,
    }


def _company(user: User) -> uuid.UUID:
    if user.company_id is None:
        raise conflict("company_required", "У аккаунта нет компании: подтвердите email работодателя")
    return user.company_id


def create_need(db: Session, user: User, inp: BriefInput, *, title: str | None, is_internship: bool) -> Need:
    company_id = _company(user)
    feat.ensure_fresh(db)  # N в рекомендованных категориях — по свежему кэшу признаков
    need = Need(id=uuid.uuid4(), company_id=company_id, author_id=user.id, raw_text=inp.text, salary_from=0, salary_to=0)
    need.categories = []
    ov = merge_overrides(Overrides(), _parse(inp), inp)
    _store_brief(db, need, inp, ov)
    need.title = (title or "").strip() or _default_title(need)
    need.is_internship = is_internship
    need.status = NeedStatus.parsed.value
    db.add(need)
    db.flush()
    return need


def _default_title(need: Need) -> str:
    cfg = ScoringConfig.load()
    return cfg.category_name(need.specialization or "", need.grade or "")


def list_needs(db: Session, user: User, status: str | None = None) -> list[dict[str, Any]]:
    """Брифы компании пользователя, новые сверху."""
    company_id = _company(user)
    q = select(Need).where(Need.company_id == company_id)
    if status is not None:
        q = q.where(Need.status == status)
    needs = list(db.scalars(q.order_by(Need.created_at.desc(), Need.id)))
    # Два запроса на весь список вместо двух на каждый бриф (N+1: 90 брифов — 183 запроса, ≈ 0,5 с; docs/LOAD_TEST.md).
    # Версию берём только номером: снимок подборки тяжёлый, а в списке нужен лишь n
    version_n = dict(db.execute(
        select(NeedVersion.id, NeedVersion.n).where(NeedVersion.id.in_([n.current_version_id for n in needs if n.current_version_id]))
    ).all()) if needs else {}
    pins = dict(db.execute(
        select(Shortlist.need_id, func.count()).where(Shortlist.need_id.in_([n.id for n in needs])).group_by(Shortlist.need_id)
    ).all()) if needs else {}
    return [
        need_summary(db, n, version_n=version_n.get(n.current_version_id), pins=int(pins.get(n.id, 0)), batched=True)
        for n in needs
    ]


def get_need(db: Session, user: User, need_id: uuid.UUID) -> Need:
    """Бриф своей компании; чужой неотличим от несуществующего (404, защита от IDOR)."""
    need = db.get(Need, need_id)
    if need is None or user.company_id is None or need.company_id != user.company_id:
        raise _need_not_found()
    return need


@dataclass
class UpdateResult:
    version: NeedVersion | None = None
    withdrawn: int = 0
    finish: Any = None  # Ctx модуля контакта: commit + доставка вебхуков после закрытия брифа


def update_need(
    db: Session,
    user: User,
    need: Need,
    inp: BriefInput,
    *,
    title: str | None,
    is_internship: bool | None,
    status: str | None,
    close_reason: str | None,
    secret: str,
    mailer: Any = None,
) -> UpdateResult:
    """Правка брифа. Если подборка уже собиралась и бриф поменялся (текст, вилка, навыки, формат, город,
    стажировка) — новая неизменяемая версия подборки (концепция §3.2 шаг 6, spec-contact §3.6).
    Закрытие брифа отзывает ждущие ответа приглашения «позиция закрыта» (контракт модуля контакта)."""
    if need.status == NeedStatus.closed.value:
        raise conflict("need_closed", "Бриф закрыт — создайте новый")
    feat.ensure_fresh(db)
    before = ((need.parsed or {}).get("brief"), bool(need.is_internship))
    ov = merge_overrides(Overrides.from_dict((need.parsed or {}).get("overrides")), _parse(inp), inp)
    _store_brief(db, need, inp, ov)
    if title is not None and title.strip():
        need.title = title.strip()
    if is_internship is not None:
        need.is_internship = is_internship
    out = UpdateResult()
    if status is not None:
        if status not in (NeedStatus.active.value, NeedStatus.paused.value, NeedStatus.closed.value):
            raise _unprocessable("status_invalid", "Статус брифа: активен, на паузе или закрыт")
        if status == NeedStatus.closed.value:
            need.close_reason = close_reason or "cancelled"
        need.status = status
    db.flush()
    if need.status == NeedStatus.closed.value:
        out.withdrawn, out.finish = _withdraw_pending(db, need, user, secret=secret, mailer=mailer)
        return out
    changed = before != ((need.parsed or {}).get("brief"), bool(need.is_internship))
    if need.current_version_id is not None and changed:
        out.version = create_version(db, user, need, secret=secret)
    return out


def _withdraw_pending(db: Session, need: Need, user: User, *, secret: str, mailer: Any) -> tuple[int, Any]:
    """spec-contact §2.3: при closed приглашения sent/viewed/countered → withdrawn «позиция закрыта»."""
    try:
        from app.services.contact import Ctx, withdraw_pending_for_need
    except Exception:  # noqa: BLE001 — модуль контакта собирается параллельно
        log.warning("Модуль контакта недоступен: приглашения закрытого брифа %s не отозваны", need.id)
        return 0, None
    ctx = Ctx(db=db, mailer=mailer, secret=secret)
    return int(withdraw_pending_for_need(ctx, need.id, user.id)), ctx


def brief_input_from_need(need: Need) -> BriefInput:
    return BriefInput(
        text=need.raw_text, salary_from=need.salary_from, salary_to=need.salary_to, salary_net=need.salary_net
    )


def patch_input(
    need: Need,
    *,
    text: str | None,
    salary_from: int | None,
    salary_to: int | None,
    salary_net: bool | None,
    **edits: Any,
) -> BriefInput:
    """Вход правки брифа: незаданное берётся из текущего брифа. Новый текст с полной вилкой внутри и без полей
    вилки в запросе — вилка из текста (иначе старые поля формы молча перекрыли бы новую сумму в тексте)."""
    base = brief_input_from_need(need)
    if text is not None and salary_from is None and salary_to is None and salary_net is None:
        found = bp.parse_salary(text)
        if found is not None and found[0].from_ is not None and found[0].to is not None:
            return BriefInput(text=text, **edits)
    return BriefInput(
        text=text if text is not None else base.text,
        salary_from=salary_from if salary_from is not None else base.salary_from,
        salary_to=salary_to if salary_to is not None else base.salary_to,
        salary_net=salary_net if salary_net is not None else base.salary_net,
        **edits,
    )


def need_brief(need: Need) -> BriefSpec:
    return BriefSpec.from_dict((need.parsed or {})["brief"])


# --------------------------------------------------------------------------- категории, охват, «как мы проверим»


def categories_view(db: Session, need: Need) -> dict[str, Any]:
    """Р4: карточки категорий с N, ползунком охвата (N ≥ 10, приросты ≥ 3), P25–P75, долей ФСП и активных."""
    feat.ensure_fresh(db)
    p = _parsed_from_need(need)
    counts = _counts(db)
    recs = list(recommend_categories(p.spec_probs.value, p.grade_probs.value, counts=counts))
    brief = need_brief(need)
    chosen = {(c["spec"], c["grade"]): bool(c.get("selected")) for c in need.categories or ()}
    cfg = ScoringConfig.load()
    status = {s["code"]: s.get("status", "active") for s in cfg.taxonomy["specializations"]}
    taken = {(r.spec, r.grade) for r in recs}
    for c in need.categories or ():  # вручную добавленные категории — тоже карточками
        if c.get("kind") == "manual" and (c["spec"], c["grade"]) not in taken:
            taken.add((c["spec"], c["grade"]))
            recs.append(
                CategoryRecommendation(
                    c["spec"], c["grade"], 0.0, "manual", cfg.category_name(c["spec"], c["grade"]),
                    status.get(c["spec"], "active"), counts.get((c["spec"], c["grade"])), None,
                )
            )
    # N_avail — после жёстких фильтров компании (spec-contact §3.2–3.3): блок-лист и паузы пары не считаются.
    rows = _company_hard_filter(
        db, need.company_id, feat.load_pool(db, sorted(taken)),
        is_internship=bool(need.is_internship), salary_from=need.salary_from,
    )
    cards = category_cards(recs, [r.features for r in rows], brief)
    defaults = _default_selected(recs)
    out = []
    for card in cards:
        r = card.rec
        rv = card.reach
        out.append(
            {
                **_rec_out(r, chosen.get((r.spec, r.grade), (r.spec, r.grade) in defaults)),
                "n": card.n if r.status != "survey_only" else r.n,
                "share_fsp": None if card.share_fsp is None else round(card.share_fsp, 2),
                "share_active": None if card.share_active is None else round(card.share_active, 2),
                "reach": {
                    "shown": rv.shown,
                    "n": rv.n,
                    "reach_at_to": rv.reach_at_to,
                    "histogram": None
                    if rv.histogram is None
                    else {"step": rv.histogram.step, "bins": [list(b) for b in rv.histogram.bins]},
                    "points": [{"upper": pt.upper, "reach": pt.reach} for pt in rv.points],
                    "hint": rv.hint,
                    "p25": rv.p25,
                    "p50": rv.p50,
                    "p75": rv.p75,
                    "note": rv.note,
                },
            }
        )
    return {
        "need_id": str(need.id),
        "salary_from": need.salary_from,
        "salary_to": need.salary_to,
        "salary_net": need.salary_net,
        "categories": out,
    }


def _parsed_from_need(need: Need) -> bp.ParsedBrief:
    ov = Overrides.from_dict((need.parsed or {}).get("overrides"))
    return apply_overrides(_parse(brief_input_from_need(need)), ov)


def _seed(need_id: uuid.UUID | str) -> int:
    return int(hashlib.sha256(f"check-set|{need_id}".encode()).hexdigest()[:12], 16)


def check_view(need: Need) -> dict[str, Any]:
    """«Как мы проверим» (§5.5): покрытие навыков брифа модулями теста, серый список и проверочный набор."""
    brief = need_brief(need)
    # План набора — до разбора навыков: «через проверочный набор» говорим только о навыках, попавших в его слоты
    plan = plan_check_set(
        brief.must,
        family_catalog=family_catalog_from_registry(),
        family_info=family_info_from_registry(),
        seed=_seed(need.id),
        spec=brief.spec,
        brief_grade=brief.grade,
    )
    cov = how_we_check(brief.must, brief.nice, spec=brief.spec, check_set=plan)
    cat = get_catalog()
    return {
        "need_id": str(need.id),
        "note": "Стандартный тест категории под вакансию не меняется — грейды остаются сравнимыми. "
        "Навыки брифа задают веса модулей в компоненте «стек».",
        "covered": [
            {
                "skill_id": i.skill_id,
                "name": i.name,
                "kind": i.kind,
                "modules": [{"code": c, "name": n} for c, n in i.modules],
                "text": i.text,
                "via": i.via,
            }
            for i in cov.covered
        ],
        "untestable": [
            {"skill_id": i.skill_id, "name": i.name, "kind": i.kind, "text": i.text, "reason": i.reason}
            for i in cov.untestable
        ],
        "module_weights": [
            {"code": m, "name": cat.module_names.get(m, m), "weight": round(w, 3)} for m, w in cov.module_weights.items()
        ],
        "check_set": {
            "slots": [
                {
                    "module": s.module,
                    "module_name": s.module_name,
                    "skills": [cat.name(x) for x in s.skills],
                    "family_id": s.family_id,
                    "minutes": round(s.minutes, 1),
                }
                for s in plan.slots
            ],
            "untestable": [cat.name(x) for x in plan.untestable],
            "total_minutes": round(plan.total_minutes, 1),
            "note": plan.note,
        },
    }


# --------------------------------------------------------------------------- пул кандидатов брифа


def _company_hard_filter(
    db: Session, company_id: uuid.UUID, rows: Sequence[feat.PoolRow], *, is_internship: bool, salary_from: int | None
) -> list[feat.PoolRow]:
    """Жёсткие фильтры компании (spec-contact §3.3): блок-лист кандидата, действующая пауза пары, возраст 16–17
    только для стажировок. Это не фильтры работодателя — такие кандидаты не попадают даже в «Скрыто»."""
    now = clock.now()
    paused: set[uuid.UUID] = set()
    for cd in db.scalars(
        select(Cooldown).where(Cooldown.company_id == company_id, Cooldown.is_active.is_(True), Cooldown.until > now)
    ):
        if cd.salary_unlock_from is not None and salary_from is not None and salary_from >= cd.salary_unlock_from:
            continue  # пауза после отказа «зарплата» снимается новым from ≥ 1,15 × прошлого to
        paused.add(cd.candidate_id)
    cid = str(company_id)
    out = []
    for r in rows:
        if r.candidate_id in paused or cid in (r.payload.get("blocked") or ()):
            continue
        if r.payload.get("internship_only") and not is_internship:
            continue
        out.append(r)
    return out


# --------------------------------------------------------------------------- версии подборки


def _filters_from_input(db: Session, company_id: uuid.UUID, raw: Sequence[Mapping[str, Any]]) -> tuple[Filter, ...]:
    """Фильтры из API → фильтры движка. «Скрыть вручную» приходит ссылками кандидатов — переводим во внутренние id."""
    out = []
    for f in raw:
        code, value = f.get("code"), f.get("value")
        if code == "exclude":
            refs = value if isinstance(value, list | tuple) else [value]
            ids = []
            for ref in refs:
                cid = privacy.resolve_ref(db, company_id, str(ref))
                if cid is None:
                    raise _unprocessable("filter_invalid", "Кандидат для «Скрыть вручную» не найден")
                ids.append(str(cid))
            value = ids
        try:
            out.append(Filter(str(code), value))
        except SelectionError as e:
            raise _unprocessable("filter_invalid", str(e)) from e
    return tuple(out)


def _filters_display(db: Session, company_id: uuid.UUID, filters: Sequence[Filter], secret: str) -> list[dict[str, Any]]:
    out = []
    for f in filters:
        d = f.to_dict()
        if f.code == "exclude":
            ids = [uuid.UUID(x) for x in (f.value if isinstance(f.value, tuple) else (f.value,))]
            aliases = privacy.get_aliases(db, company_id, ids, secret=secret)
            d["value"] = [aliases[i].ref for i in ids if i in aliases]
        out.append(d)
    return out


@dataclass
class Assembled:
    version: Any  # SelectionVersion
    by_key: dict[tuple[str, str, str], feat.PoolRow]
    timings_ms: dict[str, float]


def assemble_version(
    db: Session,
    need: Need,
    *,
    n: int,
    parent_n: int | None,
    categories: Sequence[tuple[str, str]],
    filters: tuple[Filter, ...],
    preset: str | None,
    keep: Sequence[str],
) -> Assembled:
    """Собрать версию движком: признаки из кэша → жёсткие фильтры компании → Score, объяснения, hidden_by."""
    t0 = time.perf_counter()
    rows_all = feat.load_pool(db, categories, include_unconfirmed=True)
    t1 = time.perf_counter()
    rows = _company_hard_filter(
        db, need.company_id, rows_all, is_internship=bool(need.is_internship), salary_from=need.salary_from
    )
    cfg = ScoringConfig.load()
    # Перцентиль T — по всей категории, а не по пулу этой компании: ранг не зависит от чужого блок-листа.
    stats = build_category_stats((r.features for r in rows_all), cfg)
    try:
        version = build_version(
            brief=need_brief(need),
            pool=[r.features for r in rows],
            n=n,
            parent_n=parent_n,
            categories=categories,
            filters=filters,
            preset=preset,
            created_at=clock.now(),
            category_stats=stats,
            cfg=cfg,
            keep=keep,
        )
    except (SelectionError, ValueError) as e:
        raise _unprocessable("selection_invalid", str(e)) from e
    t2 = time.perf_counter()
    by_key = {(str(r.candidate_id), r.features.spec, r.features.grade): r for r in rows}
    return Assembled(version, by_key, {"load_ms": (t1 - t0) * 1000, "score_ms": (t2 - t1) * 1000})


def _shortlist_ids(db: Session, need_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        db.scalars(select(Shortlist.candidate_id).where(Shortlist.need_id == need_id).order_by(Shortlist.pinned_at))
    )


def create_version(
    db: Session,
    user: User,
    need: Need,
    *,
    secret: str,
    filters: Sequence[Mapping[str, Any]] | None = None,
    preset: str | None = None,
    categories: Sequence[tuple[str, str]] | None = None,
) -> NeedVersion:
    """Новая неизменяемая версия v(n+1) от текущей: незаданные параметры наследуются (фильтры, пресет, категории)."""
    with no_cycle_gc():
        return _create_version(
            db, user, need, secret=secret, filters=filters, preset=preset, categories=categories
        )


def _create_version(
    db: Session,
    user: User,
    need: Need,
    *,
    secret: str,
    filters: Sequence[Mapping[str, Any]] | None,
    preset: str | None,
    categories: Sequence[tuple[str, str]] | None,
) -> NeedVersion:
    if need.status == NeedStatus.closed.value:
        raise conflict("need_closed", "Бриф закрыт — подборку не пересобрать")
    # Две параллельные сборки одного брифа не должны получить один номер версии (UNIQUE need_id, n).
    db.execute(select(Need.id).where(Need.id == need.id).with_for_update())
    t_start = time.perf_counter()
    feat.ensure_fresh(db)
    t_fresh = time.perf_counter()
    parent = _version_head(db, need.current_version_id) if need.current_version_id else None
    last_n = db.scalar(select(func.max(NeedVersion.n)).where(NeedVersion.need_id == need.id)) or 0
    if filters is None:
        engine_filters = (
            tuple(Filter(f["code"], f.get("value")) for f in (parent.filters or {}).get("engine") or ()) if parent else ()
        )
    else:
        engine_filters = _filters_from_input(db, need.company_id, filters)
    if preset is not None and preset not in ScoringConfig.load().raw["presets"]:
        raise _unprocessable("preset_invalid", "Неизвестный пресет весов: «Баланс» (balance) или «Стек важнее» (stack)")
    preset = preset or (parent.preset if parent else None)
    if categories is not None:
        cats = list(dict.fromkeys(tuple(c) for c in categories))
        if not cats:
            raise _unprocessable("categories_empty", "Выберите хотя бы одну категорию")
        _select_categories(need, cats)
    else:
        cats = selected_categories(need) or (
            [tuple(c) for c in (parent.stats or {}).get("categories") or ()] if parent else []
        )
    if not cats:
        raise _unprocessable("categories_empty", "Выберите хотя бы одну категорию")
    pinned = _shortlist_ids(db, need.id)
    asm = assemble_version(
        db,
        need,
        n=last_n + 1,
        parent_n=parent.n if parent else None,
        categories=cats,
        filters=engine_filters,
        preset=preset,
        keep=[str(c) for c in pinned],
    )
    v = asm.version
    t_built = time.perf_counter()
    cfg = ScoringConfig.load()
    now = clock.now()
    brief = v.brief
    items = []
    for it in v.items:
        row = asm.by_key.get((it.candidate_id, it.spec, it.grade))
        extras = privacy.item_extras(it, row.payload, row.features, brief, cfg, now) if row else {}
        d = item_to_store(it, extras)
        d["reasons"] = [privacy.reword_con(r) for r in d["reasons"]]
        items.append(d)
    badges = {}
    if parent is not None:
        for cid, b in diff_badges(_parent_ranks(db, parent), v).items():
            badges[cid] = {"kind": b.kind, "delta": b.delta, "label": b.label}
    t_items = time.perf_counter()
    nv = NeedVersion(
        id=uuid.uuid4(),
        need_id=need.id,
        n=v.n,
        parent_id=parent.id if parent else None,
        created_by=user.id,
        need_snapshot={
            "brief": brief.to_dict(),
            "text": need.raw_text,
            "title": need.title,
            "overrides": (need.parsed or {}).get("overrides"),
            "categories": need.categories,
            "is_internship": need.is_internship,
            # Живая карточка брифа (чипы с цитатами, вероятности, вопрос, предупреждения) — откат возвращает и её.
            "parse": (need.parsed or {}).get("parse"),
            "warnings": (need.parsed or {}).get("warnings"),
            "columns": {k: getattr(need, k) for k in _PARSE_COLUMNS},
        },
        filters={
            "engine": filters_to_store(v.filters),
            "display": _filters_display(db, need.company_id, v.filters, secret),
        },
        preset=v.preset,
        weights=dict(v.weights),
        scoring_config_version=v.scoring_config_version,
        items=items,
        stats={
            "parent_n": v.parent_n,
            "categories": [list(c) for c in v.categories],
            "neighbors": [neighbor_to_store(nd) for nd in v.neighbors],
            "total_n": v.total_n,
            "hidden_n": v.hidden_n,
            "hidden_counts": dict(v.hidden_counts),
            "badges": badges,
            # Служебные цифры («время сборки», проверка p95): до записи версии в БД.
            "timings_ms": {k: round(x, 1) for k, x in asm.timings_ms.items()}
            | {
                "fresh_ms": round((t_fresh - t_start) * 1000, 1),
                "items_ms": round((t_items - t_built) * 1000, 1),
                "total_ms": round((t_items - t_start) * 1000, 1),
            },
        },
        created_at=now,
    )
    _insert_version(db, nv)
    store.prime(nv.id, v)  # тот же объект движка — просмотр версии не разбирает JSON заново
    need.current_version_id = nv.id
    if need.status in (NeedStatus.draft.value, NeedStatus.parsed.value):
        need.status = NeedStatus.active.value
    _log_impressions(db, need.id, nv, v)
    db.flush()
    return nv


_JSON_COLUMNS = ("items", "stats", "need_snapshot", "filters", "weights")
# Колонки брифа, которые пишет разбор (_store_brief) и которые откат возвращает из снимка версии.
_PARSE_COLUMNS = (
    "spec_probs",
    "grade_probs",
    "must_skills",
    "nice_skills",
    "clarifying_question",
    "parser_version",
    "llm_used",
)


def _insert_version(db: Session, nv: NeedVersion) -> None:
    """Запись версии одним INSERT с JSON, сериализованным без \\u-экранирования кириллицы (≈ вдвое меньше байт,
    чем у сериализатора по умолчанию: тексты «за»/«против» и чипов — большая часть ≤ 500 строк версии).
    Затем объект присоединяется к сессии как уже сохранённый — без второй записи."""
    cols = {c.key: getattr(nv, c.key) for c in NeedVersion.__table__.columns}
    values: dict[str, Any] = {}
    for key, value in cols.items():
        if key in _JSON_COLUMNS:
            values[key] = cast(
                bindparam(f"j_{key}", json.dumps(value, ensure_ascii=False, separators=(",", ":")), type_=Text),
                JSONB,
            )
        else:
            values[key] = value
    db.execute(insert(NeedVersion).values(**values))
    make_transient_to_detached(nv)
    db.add(nv)


def _version_head(db: Session, version_id: uuid.UUID) -> SimpleNamespace | None:
    """Родительская версия без её ≤ 500 строк (они нужны только для бейджей diff — см. _parent_ranks)."""
    row = db.execute(
        select(NeedVersion.id, NeedVersion.n, NeedVersion.filters, NeedVersion.preset, NeedVersion.stats).where(
            NeedVersion.id == version_id
        )
    ).first()
    if row is None:
        return None
    return SimpleNamespace(id=row.id, n=row.n, filters=row.filters, preset=row.preset, stats=row.stats)


def _parent_ranks(db: Session, parent: SimpleNamespace) -> Any:
    """Места в «Общем топе» родительской версии: из памяти процесса, иначе — только (кандидат, место) из JSON."""
    cached = store.cached(parent.id)
    if cached is not None:
        return cached
    rows = db.execute(
        text(
            "SELECT e->>'candidate_id' AS cid, (e->>'rank')::int AS rank "
            "FROM need_versions v, jsonb_array_elements(v.items) e WHERE v.id = :id"
        ),
        {"id": parent.id},
    )
    return SimpleNamespace(items=tuple(SimpleNamespace(candidate_id=cid, rank=rank) for cid, rank in rows))


def _select_categories(need: Need, cats: Sequence[tuple[str, str]]) -> None:
    cfg = ScoringConfig.load()
    known = {s["code"] for s in cfg.taxonomy["specializations"]}
    for s, g in cats:
        if s not in known or g not in cfg.cuts:
            raise _unprocessable("categories_invalid", f"Неизвестная категория: {s}:{g}")
    want = set(cats)
    out = []
    seen = set()
    for c in need.categories or ():
        key = (c["spec"], c["grade"])
        seen.add(key)
        out.append({**c, "selected": key in want})
    for s, g in cats:
        if (s, g) not in seen:
            out.append(
                {
                    "spec": s,
                    "grade": g,
                    "p": 0.0,
                    "kind": "manual",
                    "name": cfg.category_name(s, g),
                    "status": "active",
                    "n": None,
                    "note": None,
                    "selected": True,
                }
            )
    need.categories = out


def _log_impressions(db: Session, need_id: uuid.UUID, nv: NeedVersion, v: Any) -> None:
    """Показы для «как меня видят» (К15): видимые строки общего топа до max_per_category."""
    limit = int(ScoringConfig.load().section("selection")["max_per_category"])
    rows = [
        {"need_id": need_id, "version_id": nv.id, "candidate_id": uuid.UUID(i.candidate_id), "rank": i.rank, "shown_at": nv.created_at}
        for i in v.overall_top(limit)
    ]
    if rows:  # одним INSERT … VALUES (…), (…) — не по запросу на строку
        db.execute(insert(ImpressionLog).values(rows))


def list_versions(db: Session, need: Need) -> list[NeedVersion]:
    return list(db.scalars(select(NeedVersion).where(NeedVersion.need_id == need.id).order_by(NeedVersion.n)))


def get_version(db: Session, need: Need, n: int) -> NeedVersion:
    nv = db.scalar(select(NeedVersion).where(NeedVersion.need_id == need.id, NeedVersion.n == n))
    if nv is None:
        raise not_found("version_not_found", f"Версии v{n} нет")
    return nv


def current_version(db: Session, need: Need) -> NeedVersion | None:
    return db.get(NeedVersion, need.current_version_id) if need.current_version_id else None


def rollback(db: Session, need: Need, n: int) -> NeedVersion:
    """Откат — перенос указателя без пересчёта на любую из последних 10 версий; бриф возвращается к снимку."""
    if need.status == NeedStatus.closed.value:
        raise conflict("need_closed", "Бриф закрыт — откат недоступен")
    target = get_version(db, need, n)
    versions = list_versions(db, need)
    cur = current_version(db, need)
    history = SelectionHistory(
        str(need.id),
        versions=tuple(SimpleNamespace(n=v.n) for v in versions),  # type: ignore[arg-type]
        current_n=cur.n if cur else None,
    )
    try:
        history.rollback(n)
    except SelectionError as e:
        raise conflict("rollback_too_old", str(e)) from e
    snap = target.need_snapshot or {}
    need.current_version_id = target.id
    if snap.get("brief"):
        need.raw_text = snap.get("text", need.raw_text)
        brief = BriefSpec.from_dict(snap["brief"])
        need.salary_from, need.salary_to, need.salary_net = brief.salary_from, brief.salary_to, brief.salary_net
        need.specialization, need.grade = brief.spec, brief.grade
        need.city = brief.city
        need.format = sorted(brief.formats)[0] if brief.formats else None
        if snap.get("is_internship") is not None:
            need.is_internship = bool(snap["is_internship"])
        parsed = dict(need.parsed or {})
        parsed["brief"] = snap["brief"]
        parsed["overrides"] = snap.get("overrides")
        if snap.get("parse") is not None:
            # Чипы, вероятности и вопрос живой карточки — тоже из снимка (иначе бриф и его разбор расходятся).
            parsed["parse"] = snap["parse"]
            parsed["warnings"] = list(snap.get("warnings") or ())
            for k, value in (snap.get("columns") or {}).items():
                if k in _PARSE_COLUMNS:
                    setattr(need, k, value)
        need.parsed = parsed
        if snap.get("categories") is not None:
            need.categories = snap["categories"]
    db.flush()
    return target


# --------------------------------------------------------------------------- представление версии


UNAVAILABLE_REASON = "Кандидат сейчас не показывается в подборе"


def _invitation_status(db: Session, company_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, tuple[str, str]]:
    """Активное приглашение компании кандидату: (код статуса, подпись «как у обеих сторон», концепция §4.3)."""
    if not ids:
        return {}
    labels = get_contact_config().status_labels.invitation
    rows = db.execute(
        select(Invitation.candidate_id, Invitation.status)
        .where(
            Invitation.company_id == company_id,
            Invitation.candidate_id.in_(ids),
            Invitation.status.in_(PAIR_ACTIVE_INVITATION_STATUSES),
        )
        .order_by(Invitation.created_at)
    )
    return {cid: (st, labels.get(st, st)) for cid, st in rows}


def version_meta(nv: NeedVersion, need: Need) -> dict[str, Any]:
    stats = nv.stats or {}
    cfg = ScoringConfig.load()
    v_cats = [tuple(c) for c in stats.get("categories") or ()]
    visible = sum(1 for d in nv.items or () if d.get("rank") is not None)
    return {
        "n": nv.n,
        "parent_n": stats.get("parent_n"),
        "current": need.current_version_id == nv.id,
        "created_at": nv.created_at,
        "preset": nv.preset,
        "weights": dict(nv.weights or {}),
        "scoring_config_version": nv.scoring_config_version,
        "filters": list((nv.filters or {}).get("display") or ()),
        "categories": [{"spec": s, "grade": g, "name": cfg.category_name(s, g)} for s, g in v_cats],
        "total_n": int(stats.get("total_n") or 0),
        "visible_n": visible,
        "hidden_n": int(stats.get("hidden_n") or 0),
        "hidden_summary": [
            {"code": k, "label": FILTER_LABELS.get(k, k), "count": c} for k, c in (stats.get("hidden_counts") or {}).items()
        ],
    }


def version_view(
    db: Session,
    need: Need,
    nv: NeedVersion,
    *,
    secret: str,
    tab: str = "overall",
    category: tuple[str, str] | None = None,
    page_no: int = 1,
    hidden_page: int = 1,
    compare_to: int | None = None,
) -> dict[str, Any]:
    """Р5: страница строк (вкладка «Общий топ» или категория), «Скрыто фильтрами (N)», шортлист с жёлтой меткой,
    бейджи diff (по умолчанию — к родительской версии, compare_to — к любой версии брифа) и «почему выше соседа»."""
    v = version_from_row(nv)
    if compare_to is not None and compare_to != nv.n:
        other = get_version(db, need, compare_to)
        badges_raw = {
            cid: {"kind": b.kind, "delta": b.delta, "label": b.label}
            for cid, b in diff_badges(ranks_only(other), v).items()
        }
        compared_n: int | None = other.n
    else:
        badges_raw = (nv.stats or {}).get("badges") or {}
        compared_n = (nv.stats or {}).get("parent_n")
    stored = {(d["candidate_id"], d["spec"], d["grade"]): d for d in nv.items or ()}
    cfg = ScoringConfig.load()
    size = int(cfg.section("selection")["page_size"])
    pins = list(
        db.execute(select(Shortlist.candidate_id, Shortlist.pinned_at, Shortlist.note).where(Shortlist.need_id == need.id).order_by(Shortlist.pinned_at))
    )
    by_cat = None
    if tab == "category":
        by = v.by_category()
        if category is None:
            category = v.categories[0] if v.categories else None
        if category is None or tuple(category) not in by:
            raise _unprocessable("category_invalid", "Такой категории нет в этой версии подборки")
        tab_rows = by[tuple(category)]
        by_cat = {"spec": category[0], "grade": category[1], "name": cfg.category_name(*category)}
    else:
        tab_rows = v.overall_top()

    # Версия неизменяема, но кандидат, который после сборки отозвал согласие на показ, ушёл в «не ищу» или
    # заблокировал компанию, из неё сразу пропадает (spec-contact §3.3; «отозвал — его нет ни в подборке, ни
    # в банке»). Места остальных не пересчитываются; в шортлисте он остаётся строкой без данных с жёлтой меткой.
    # Строки, помеченные хуками, пересобираются сразу; изменения без хуков — по отпечатку для того, что на экране.
    feat.refresh_marked(db)
    check = {uuid.UUID(i.candidate_id) for i in v.items} | {c for c, _, _ in pins}

    def cut(visible: set[uuid.UUID]) -> tuple[set[str], list[Any], list[Any], list[Any], list[Any]]:
        gone_ = {str(c) for c in check - visible}
        rows_ = [i for i in tab_rows if i.candidate_id not in gone_]
        hidden_ = [i for i in v.hidden() if i.candidate_id not in gone_]
        try:
            return gone_, rows_, page(rows_, page_no, size), hidden_, page(hidden_, hidden_page, size)
        except SelectionError as e:
            raise _unprocessable("page_invalid", str(e)) from e

    gone, rows, page_rows, hidden_all, hidden_rows = cut(feat.visible_for_company(db, need.company_id))
    on_screen = {uuid.UUID(i.candidate_id) for i in (*page_rows, *hidden_rows)} | {c for c, _, _ in pins}
    if feat.refresh_stale(db, on_screen):
        gone, rows, page_rows, hidden_all, hidden_rows = cut(feat.visible_for_company(db, need.company_id))
    total = len(rows)
    pages = max(1, (total + size - 1) // size)
    sl = EngineShortlist(str(need.id), tuple(Pin(str(c), at.isoformat() if at else None, note or "") for c, at, note in pins))
    entries = shortlist_view(sl, v)
    ids = {uuid.UUID(i.candidate_id) for i in page_rows} | {uuid.UUID(i.candidate_id) for i in hidden_rows}
    ids |= {uuid.UUID(e.candidate_id) for e in entries}
    aliases = privacy.get_aliases(db, need.company_id, ids, secret=secret)
    badges = badges_raw
    neighbors = {nd["upper_rank"]: nd["text"] for nd in map(store.neighbor_dict, (nv.stats or {}).get("neighbors") or ())}
    pinned_ids = {str(c) for c, _, _ in pins}
    invited = _invitation_status(db, need.company_id, list(ids))
    gone_ranks = {i.rank for i in v.overall_top() if i.candidate_id in gone} if gone else set()

    def dto(i: Any) -> dict[str, Any]:
        d = stored[(i.candidate_id, i.spec, i.grade)]
        cid = uuid.UUID(i.candidate_id)
        show_neighbor = tab != "category" and i.rank is not None and i.rank + 1 not in gone_ranks
        out = privacy.employer_item(
            need.company_id,
            d,
            aliases[cid],
            secret=secret,
            badge=badges.get(i.candidate_id),
            shortlisted=i.candidate_id in pinned_ids,
            neighbor=neighbors.get(i.rank) if show_neighbor else None,
        )
        st = invited.get(cid)
        out["invitation_status"], out["invitation_label"] = st if st else (None, None)
        return out

    shortlist_out = []
    for e in entries:
        cid = uuid.UUID(e.candidate_id)
        away = e.candidate_id in gone
        shortlist_out.append(
            {
                "ref": aliases[cid].ref,
                "pseudonym": aliases[cid].pseudonym,
                "mark": "yellow" if away else e.mark,
                "reason": UNAVAILABLE_REASON if away else e.reason,
                "pinned_at": e.pin.pinned_at,
                "note": e.pin.note or None,
                "item": dto(e.item) if e.item is not None and not away else None,
            }
        )
    return {
        **version_meta(nv, need),
        "tab": tab,
        "category": by_cat,
        "page": page_no,
        "page_size": size,
        "pages": pages,
        "items_total": total,
        "items": [dto(i) for i in page_rows],
        "hidden": {
            "count": len(hidden_all),
            "page": hidden_page,
            "items": [dto(i) for i in hidden_rows],
        },
        "shortlist": shortlist_out,
        "compared_to": compared_n,
        "build_ms": ((nv.stats or {}).get("timings_ms") or {}).get("total_ms"),
    }


def neighbor_view(nv: NeedVersion, rank: int) -> dict[str, Any]:
    """«Почему #k выше #k+1»: Δ, главный плюс и главный минус (концепция §4.4 п. 3)."""
    for d in map(store.neighbor_dict, (nv.stats or {}).get("neighbors") or ()):
        if d["upper_rank"] == rank:
            names = ScoringConfig.load().section("explain")["component_names"]
            return {
                "upper_rank": d["upper_rank"],
                "lower_rank": d["lower_rank"],
                "delta": round(float(d["delta"]), 1),
                "plus": {"component": d["plus"][0], "name": names[d["plus"][0]], "value": round(d["plus"][1], 1)}
                if d.get("plus")
                else None,
                "minus": {"component": d["minus"][0], "name": names[d["minus"][0]], "value": round(d["minus"][1], 1)}
                if d.get("minus")
                else None,
                "text": d["text"],
            }
    raise not_found("neighbor_not_found", f"Нет пары #{rank} и #{rank + 1} в этой версии")


# --------------------------------------------------------------------------- шортлист и карточка


def _candidate_available(db: Session, need: Need, cid: uuid.UUID) -> bool:
    """Кандидата можно закрепить/открыть: он виден работодателям и не скрыт жёсткими фильтрами компании."""
    from app.models.matching import MatchingFeatures

    feat.refresh_stale(db, [cid])
    rows = db.execute(
        select(MatchingFeatures.candidate_id, MatchingFeatures.specialization, MatchingFeatures.grade, MatchingFeatures.payload).where(
            MatchingFeatures.candidate_id == cid, MatchingFeatures.visible.is_(True)
        )
    ).all()
    if not rows:
        return False
    now = clock.now()
    pool = [feat.PoolRow(c, feat.features_from_payload(str(c), s, g, p, now), p) for c, s, g, p in rows]
    return bool(
        _company_hard_filter(db, need.company_id, pool, is_internship=bool(need.is_internship), salary_from=need.salary_from)
    )


def resolve_candidate(db: Session, need: Need, ref: str) -> uuid.UUID:
    cid = privacy.resolve_ref(db, need.company_id, ref)
    if cid is None:
        raise not_found("candidate_not_found", "Кандидат не найден")
    return cid


def pin(db: Session, user: User, need: Need, ref: str, note: str | None = None) -> None:
    """📌 в шортлист брифа (≤ 30): шортлист живёт отдельно от версий подборки."""
    if need.status == NeedStatus.closed.value:
        raise conflict("need_closed", "Бриф закрыт — закреплять в его шортлист нельзя")
    cid = resolve_candidate(db, need, ref)
    if db.get(Shortlist, (need.id, cid)) is not None:
        return
    if not _candidate_available(db, need, cid):
        raise not_found("candidate_not_found", "Кандидат сейчас недоступен")
    ids = _shortlist_ids(db, need.id)
    sl = EngineShortlist(str(need.id), tuple(Pin(str(c)) for c in ids))
    try:
        sl.pin(str(cid))
    except SelectionError as e:
        raise conflict("shortlist_full", str(e)) from e
    # Двойной клик / две вкладки: повторное закрепление — не ошибка
    db.execute(
        pg_insert(Shortlist)
        .values(need_id=need.id, candidate_id=cid, pinned_by=user.id, pinned_at=clock.now(), note=note)
        .on_conflict_do_nothing()
    )
    db.flush()


def unpin(db: Session, need: Need, ref: str) -> None:
    cid = resolve_candidate(db, need, ref)
    res = db.execute(delete(Shortlist).where(Shortlist.need_id == need.id, Shortlist.candidate_id == cid))
    if res.rowcount == 0:
        raise not_found("pin_not_found", "Этого кандидата нет в шортлисте")
    db.flush()


def shortlist_out(db: Session, need: Need, *, secret: str) -> list[dict[str, Any]]:
    nv = current_version(db, need)
    if nv is None:
        ids = _shortlist_ids(db, need.id)
        aliases = privacy.get_aliases(db, need.company_id, ids, secret=secret)
        return [
            {"ref": aliases[c].ref, "pseudonym": aliases[c].pseudonym, "mark": "yellow",
             "reason": "Подборка ещё не собрана", "pinned_at": None, "note": None, "item": None}
            for c in ids
        ]
    return version_view(db, need, nv, secret=secret)["shortlist"]


def best_stored_item(nv: NeedVersion | None, candidate_id: uuid.UUID) -> dict[str, Any] | None:
    """Строка кандидата в версии: видимая с лучшим местом, иначе с наибольшим Score (как SelectionVersion.item)."""
    if nv is None:
        return None
    rows = [d for d in nv.items or () if d["candidate_id"] == str(candidate_id)]
    if not rows:
        return None
    vis = sorted((d for d in rows if d.get("rank") is not None), key=lambda d: d["rank"])
    return vis[0] if vis else rows[0]


def candidate_card(db: Session, user: User, need: Need, ref: str, *, secret: str) -> dict[str, Any]:
    """Панель Р6: слепая (или раскрытая) карточка + полное разложение Score. Открытие записывается (DECISIONS п. 7)."""
    cid = resolve_candidate(db, need, ref)
    nv = current_version(db, need)
    item = best_stored_item(nv, cid)
    pinned = db.get(Shortlist, (need.id, cid)) is not None
    if item is None and not pinned and not _candidate_available(db, need, cid):
        raise not_found("candidate_not_found", "Кандидат сейчас недоступен")
    view = privacy.candidate_profile_view(
        db,
        company_id=need.company_id,
        candidate_id=cid,
        need_id=need.id,  # значок «в вилке ✓ / выше» — по верху вилки брифа в базисе минимума кандидата
        require_visible=not pinned and item is None,
        secret=secret,
        selection_item=item,
    )
    if view is None:
        raise not_found("candidate_not_found", "Кандидат сейчас недоступен")
    privacy.record_view(db, need.company_id, cid, viewer_id=user.id, need_id=need.id)
    db.flush()
    return view


def score_snapshot(db: Session, need_id: uuid.UUID, candidate_id: uuid.UUID) -> dict[str, Any] | None:
    """Контракт для приглашений: строка кандидата текущей версии (без доказательств и внутренних ссылок)."""
    need = db.get(Need, need_id)
    if need is None:
        return None
    nv = current_version(db, need)
    d = best_stored_item(nv, candidate_id)
    if d is None or nv is None:
        return None
    pros = [r["text"] for r in d.get("reasons") or () if r["kind"] == "pro"]
    con = next((r["text"] for r in d.get("reasons") or () if r["kind"] == "con"), None)
    return {
        "version_n": nv.n,
        "scoring_config_version": nv.scoring_config_version,
        "rank": d.get("rank"),
        "category_rank": d.get("category_rank"),
        "category": store.category_of(d),
        "spec": d["spec"],
        "grade": d["grade"],
        "score": d["score_display"],
        "parts": dict(d["parts"]),
        "pros": pros,
        "con": con,
        "why_you": list(d.get("why_you") or ()),
        "weak_match": d["score_display"] < get_contact_config().matching.weak_score_below,
    }


def need_summary(
    db: Session, need: Need, *, version_n: int | None = None, pins: int | None = None, batched: bool = False
) -> dict[str, Any]:
    """Сводка брифа. `batched=True` — номер версии и число закреплённых уже посчитаны списком (list_needs)."""
    if not batched:
        nv = current_version(db, need)
        version_n = nv.n if nv else None
        pins = db.scalar(select(func.count()).select_from(Shortlist).where(Shortlist.need_id == need.id)) or 0
    return {
        "id": need.id,
        "title": need.title,
        "status": need.status,
        "close_reason": need.close_reason,
        "specialization": need.specialization,
        "grade": need.grade,
        "category": ScoringConfig.load().category_name(need.specialization or "", need.grade or ""),
        "salary_from": need.salary_from,
        "salary_to": need.salary_to,
        "salary_net": need.salary_net,
        "is_internship": need.is_internship,
        "current_version": version_n,
        "shortlist_count": int(pins or 0),
        "created_at": need.created_at,
        "updated_at": need.updated_at,
    }


def need_detail(db: Session, need: Need) -> dict[str, Any]:
    parsed = need.parsed or {}
    brief = parsed.get("brief") or {}
    return {
        **need_summary(db, need),
        "text": need.raw_text,
        "brief": {
            "spec": brief.get("spec"),
            "grade": brief.get("grade"),
            "must": list(brief.get("must") or ()),
            "nice": list(brief.get("nice") or ()),
            "formats": list(brief.get("formats") or ()),
            "city": brief.get("city"),
            "salary_from": brief.get("salary_from"),
            "salary_to": brief.get("salary_to"),
            "salary_net": brief.get("salary_net"),
        },
        "parse": parse_out(parsed.get("parse") or {}, need.categories or ()),
        "warnings": list(parsed.get("warnings") or ()),
        "categories": list(need.categories or ()),
    }
