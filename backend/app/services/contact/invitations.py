"""Сценарии приглашений (концепция §3.1 шаги 11–12, §3.2 шаги 8–10, §4.3; spec-contact §2.1–2.3).

Отправка: проверка профиля компании → кандидаты по ref/псевдониму (только открытые карточки и шортлист,
DECISIONS 7) → ленивые сроки → снимки квоты/лимита/пауз → движок send_invitation/send_batch → сохранение.
Ответы сторон — apply_action движка; отказ движка с понятной причиной → 400/409, сроки, применённые до отказа,
сохраняются (ContactError.prior).
"""

from __future__ import annotations

import base64
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contact.common import ContactError, Outcome
from app.contact.invitation import (
    Invitation as DInv,
)
from app.contact.invitation import (
    InvitationDraft,
    apply_action,
    available_actions,
    decline_hint,
    outcome_check_at,
    send_batch,
    send_invitation,
    status_label,
    withdraw_for_closed_need,
)
from app.contact.invitation import can_dispute as domain_can_dispute
from app.contact.quotas import CandidateState
from app.contact.quotas import Cooldown as DCooldown
from app.contact.statuses import PENDING_STATUSES
from app.contact.statuses import InvitationStatus as S
from app.contact.terms import CounterOffer, counter_salary_cap, counter_summary
from app.contact.texts import salary_range
from app.core.errors import bad_request, conflict, not_found
from app.models.candidate import CandidateProfile
from app.models.company import Company
from app.models.contact import Cooldown, Feedback, Invitation, OfferTerms, Vacancy
from app.models.enums import NeedStatus, UserRole, UserStatus
from app.models.matching import Need
from app.models.trust import TrustComplaint
from app.models.users import User
from app.services.notify import send_mail_safe
from app.trust import complaints as trust_complaints
from app.trust.verification import trust_of_many

from . import identity
from .common import cfg, contact_error, issues, now, rel
from .company import company_of, require_complete, sender_state
from .profiles import candidate_for_employer, company_card, contact_methods_list
from .reliability import events_by_company
from .store import Ctx, jsonable, persist, prefetched, refresh_rows, terms_to_row, to_domain
from .vacancies import team_of

# --- отправка ---------------------------------------------------------------------------------------


@dataclass
class SendSpec:
    refs: list[str]
    need_id: uuid.UUID | None = None
    vacancy_id: uuid.UUID | None = None
    title: str | None = None
    description: str | None = None
    salary_from: int | None = None
    salary_to: int | None = None
    salary_type: str | None = None
    format: str | None = None
    start_date: date | None = None
    contact_method: str | None = None
    contact_value: str | None = None
    response_days: int | None = None
    why_you: dict[str, list[str]] = field(default_factory=dict)  # ref/псевдоним → свой текст «почему вы»
    promises: list[str] = field(default_factory=list)  # «обещание процесса» (config/contact.yaml: invitation.promises)


@dataclass
class SendResult:
    sent: list[Invitation] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _owned_need(db: Session, company: Company, need_id: uuid.UUID | None) -> Need | None:
    if need_id is None:
        return None
    need = db.get(Need, need_id)
    if need is None or need.company_id != company.id:
        raise not_found("need_not_found", "Бриф не найден")
    if need.status == NeedStatus.closed.value:
        raise conflict("need_closed", "Бриф закрыт — приглашения по нему не отправляются")
    return need


def _owned_vacancy(db: Session, company: Company, vacancy_id: uuid.UUID | None) -> Vacancy | None:
    if vacancy_id is None:
        return None
    vac = db.get(Vacancy, vacancy_id)
    if vac is None or vac.company_id != company.id:
        raise not_found("vacancy_not_found", "Вакансия не найдена")
    return vac


def _defaults(spec: SendSpec, company: Company, need: Need | None, vac: Vacancy | None) -> dict[str, Any]:
    """Форма приглашения заполняется из брифа (Р7); из банка без брифа — минимальная форма."""
    src_title = (vac.title if vac else None) or (need.title if need else None)
    src_desc = (vac.description if vac else None) or (need.raw_text if need else None)
    salary_from = spec.salary_from if spec.salary_from is not None else (need.salary_from if need else None)
    salary_to = spec.salary_to if spec.salary_to is not None else (need.salary_to if need else None)
    if salary_from is None or salary_to is None:
        raise bad_request("salary_required", "Укажите вилку «от–до»: без неё приглашение не отправляется")
    salary_type = spec.salary_type or (("net" if need.salary_net else "gross") if need else "net")
    fmt = spec.format if spec.format is not None else (need.format if need else None)
    if fmt not in cfg().invitation.formats:
        fmt = spec.format  # неизвестный формат из брифа не подставляем молча, явный — проверит движок
    method = spec.contact_method
    value = spec.contact_value
    if not method:
        methods = contact_methods_list(company)
        if methods:
            method, value = methods[0]["method"], value or methods[0]["value"]
    elif not value:
        value = (company.contact_methods or {}).get(method)
    return {
        "title": (spec.title or src_title or "").strip(),
        "description": (spec.description or src_desc or "").strip(),
        "salary_from": int(salary_from),
        "salary_to": int(salary_to),
        "salary_type": salary_type,
        "format": fmt,
        "contact_method": method or "",
        "contact_value": str(value or ""),
        "is_internship": bool(need.is_internship) if need else False,
    }


def _week_received(db: Session, candidate_id: uuid.UUID, at: datetime) -> tuple[datetime, ...]:
    since = at - timedelta(days=8)  # ISO-неделя считается в движке; берём с запасом
    return tuple(
        db.scalars(select(Invitation.created_at).where(Invitation.candidate_id == candidate_id, Invitation.created_at > since))
    )


def _cooldowns(db: Session, company_id: uuid.UUID, candidate_id: uuid.UUID) -> tuple[DCooldown, ...]:
    rows = db.scalars(
        select(Cooldown).where(
            Cooldown.company_id == company_id, Cooldown.candidate_id == candidate_id, Cooldown.is_active.is_(True)
        )
    )
    return tuple(
        DCooldown(
            company_id=str(r.company_id),
            candidate_id=str(r.candidate_id),
            until=r.until,
            reason=r.reason,
            salary_unlock_from=r.salary_unlock_from,
            decline_reason=r.decline_reason,
        )
        for r in rows
    )


def candidate_state(
    ctx: Ctx, company: Company, candidate_id: uuid.UUID, need_id: uuid.UUID | None, at: datetime
) -> tuple[CandidateState, dict[str, Any] | None, str | None]:
    db = ctx.db
    profile = db.get(CandidateProfile, candidate_id)
    ident = identity.alias(db, ctx.secret, company.id, candidate_id)
    pair_rows = list(
        db.scalars(select(Invitation).where(Invitation.company_id == company.id, Invitation.candidate_id == candidate_id))
    )
    refresh_rows(ctx, pair_rows)
    snap = identity.score_snapshot(db, need_id, candidate_id)
    score = snap.get("score") if snap else None
    search_mode = profile.search_mode if profile else "open"
    if not identity.in_bank(db, candidate_id):
        search_mode = "hidden"  # согласие на показ отозвано — для движка это «не ищу» (без раскрытия причины)
    state = CandidateState(
        candidate_id=str(candidate_id),
        pseudonym=ident.pseudonym,
        search_mode=search_mode,
        weekly_cap=profile.weekly_invite_limit if profile else cfg().candidate_limits.default_weekly,
        received_at=_week_received(db, candidate_id, at),
        blocked_company_ids=frozenset(str(c) for c in ((profile.blocked_company_ids or []) if profile else [])),
        birth_year=profile.birth_year if profile else None,
        guardian_consent=bool(profile.guardian_consent) if profile else False,
        card_opened=identity.card_opened(db, company.id, candidate_id),
        shortlisted=identity.shortlisted(db, company.id, candidate_id),
        score=float(score) if isinstance(score, int | float) else None,
        pair_statuses=tuple(r.status for r in pair_rows),
        cooldowns=_cooldowns(db, company.id, candidate_id),
    )
    return state, snap, ident.ref


def _resolve_candidates(ctx: Ctx, company: Company, refs: Sequence[str], need_id: uuid.UUID | None) -> tuple[list[tuple[str, uuid.UUID]], list[dict[str, Any]]]:
    db = ctx.db
    pool_ids = identity.pool(db, company.id)
    found: list[tuple[str, uuid.UUID]] = []
    rejected: list[dict[str, Any]] = []
    for ref in refs:
        cid = identity.resolve(db, ctx.secret, company.id, ref, pool_ids)
        user = db.get(User, cid) if cid else None
        if user is None or user.role != UserRole.candidate.value or user.status != UserStatus.active.value:
            rejected.append(
                {
                    "ref": ref,
                    "pseudonym": identity.normalize_label(ref),
                    "errors": [
                        {
                            "code": "card_not_opened",
                            "message": f"Сначала откройте карточку {identity.normalize_label(ref)}: пригласить можно "
                            "только того, чью карточку вы смотрели или добавили в шортлист",
                        }
                    ],
                    "warnings": [],
                }
            )
            continue
        found.append((ref, cid))
    return found, rejected


@dataclass
class _Prepared:
    company: Company
    need: Need | None
    vacancy: Vacancy | None
    form: dict[str, Any]
    at: datetime
    rejected: list[dict[str, Any]]
    sender: Any
    items: list[tuple[InvitationDraft, CandidateState]]
    meta: dict[str, tuple[str, dict[str, Any] | None]]


def _prepare(ctx: Ctx, user: User, spec: SendSpec, *, batch: bool, strict: bool) -> _Prepared:
    """Общее для отправки и превью: форма из брифа, кандидаты, ленивые сроки, снимки квоты/лимита/пауз."""
    db = ctx.db
    c = cfg()
    company = company_of(db, user)
    if strict:
        require_complete(company)
    if batch and len(spec.refs) > c.quotas.batch_max:
        raise bad_request(
            "batch_too_large", f"В пачке — не больше {c.quotas.batch_max} приглашений: выбрано {len(spec.refs)}"
        )
    need = _owned_need(db, company, spec.need_id)
    vac = _owned_vacancy(db, company, spec.vacancy_id)
    if need is None and vac is not None and vac.need_id is not None:
        need = db.get(Need, vac.need_id)
    form = _defaults(spec, company, need, vac)
    at = now()
    resolved, rejected = _resolve_candidates(ctx, company, spec.refs, need.id if need else None)
    if strict and not batch and rejected:
        raise conflict("contact_rejected", rejected[0]["errors"][0]["message"], {"rejected": rejected})

    # Ленивые сроки по всем ждущим ответа приглашениям компании: истёкшее без просмотра возвращает слот
    pending = list(
        db.scalars(select(Invitation).where(Invitation.company_id == company.id, Invitation.status.in_(tuple(PENDING_STATUSES))))
    )
    refresh_rows(ctx, pending)
    sender = sender_state(db, company, user, at)

    items: list[tuple[InvitationDraft, CandidateState]] = []
    meta: dict[str, tuple[str, dict[str, Any] | None]] = {}
    for ref, cid in resolved:
        cand, snap, _ref = candidate_state(ctx, company, cid, need.id if need else None, at)
        why = spec.why_you.get(ref) or spec.why_you.get(cand.pseudonym) or (snap or {}).get("why_you") or []
        draft = InvitationDraft(
            invitation_id=str(uuid.uuid4()),
            company_id=str(company.id),
            candidate_id=str(cid),
            title=form["title"],
            description=form["description"],
            salary_from=form["salary_from"],
            salary_to=form["salary_to"],
            contact_method=form["contact_method"],
            contact_value=form["contact_value"],
            salary_type=form["salary_type"],
            response_days=spec.response_days,
            format=form["format"],
            start_date=spec.start_date,
            need_id=str(need.id) if need else None,
            vacancy_id=str(vac.id) if vac else None,
            is_internship=form["is_internship"],
            why_you=tuple(str(w)[:300] for w in list(why)[: c.invitation.why_you_max_items]),
            actor_id=str(user.id),
            score_snapshot=snap,
            rank_at_send=(snap or {}).get("rank"),
        )
        items.append((draft, cand))
        meta[str(cid)] = (ref, snap)
    return _Prepared(company, need, vac, form, at, rejected, sender, items, meta)


def preview(ctx: Ctx, user: User, spec: SendSpec) -> dict[str, Any]:
    """Р7: «так увидит кандидат» и проверки квоты, лимита, паузы и вилки словами — ничего не отправляя.

    Правила те же, что при отправке (движок check_batch + проверка содержимого): что разрешено здесь,
    то разрешено и в «Отправить» (если между превью и отправкой ничего не изменилось)."""
    from app.contact.antifraud import check_text
    from app.contact.quotas import check_batch
    from app.contact.rules import validate_invitation_content

    c = cfg()
    p = _prepare(ctx, user, spec, batch=True, strict=False)
    form = p.form
    response_days = spec.response_days if spec.response_days is not None else c.response.default_days
    content = validate_invitation_content(
        title=form["title"],
        description=form["description"],
        contact_method=form["contact_method"],
        contact_value=form["contact_value"],
        response_days=response_days,
        work_format=form["format"],
        cfg=c,
    )
    from .company import missing_fields

    missing = missing_fields(p.company)
    batch = check_batch(
        p.sender,
        [cand for _, cand in p.items],
        need_id=str(p.need.id) if p.need else None,
        salary_from=form["salary_from"],
        salary_to=form["salary_to"],
        salary_type=form["salary_type"],
        is_internship=form["is_internship"],
        now=p.at,
        cfg=c,
    )
    checks: list[dict[str, Any]] = []
    for draft, cand in p.items:
        chk = batch.per_candidate.get(cand.candidate_id)
        if chk is None:
            continue
        merged = issues(chk)
        merged["errors"] = [*issues(content)["errors"], *merged["errors"]]
        if missing:
            merged["errors"].insert(
                0,
                {
                    "code": "company_profile_incomplete",
                    "message": "Заполните профиль компании, чтобы отправлять приглашения: " + ", ".join(missing),
                },
            )
        checks.append(
            {
                "ref": p.meta[cand.candidate_id][0],
                "pseudonym": cand.pseudonym,
                "ok": not merged["errors"],
                **merged,
                "why_you": list(draft.why_you[: c.invitation.why_you_max_items]),
            }
        )
    for r in p.rejected:
        checks.append({**r, "ok": False, "notes": [], "why_you": []})
    fraud = check_text(form["title"], form["description"], cfg=c)
    type_label = c.salary.types.get(form["salary_type"], "")
    from .company import quota_dict

    return {
        "title": form["title"],
        "description": form["description"],
        "salary": {
            "salary_from": form["salary_from"],
            "salary_to": form["salary_to"],
            "salary_type": form["salary_type"],
            "salary_type_label": type_label,
            "text": f"{salary_range(form['salary_from'], form['salary_to'])} {type_label}".strip(),
        },
        "format_label": c.invitation.formats.get(form["format"]) if form["format"] else None,
        "contact_method_label": c.invitation.contact_methods.get(form["contact_method"]),
        "respond_in_days": response_days,
        "company": company_card(ctx.db, p.company, p.at),
        "caution": c.antifraud.label if fraud.caution else None,
        "caution_reasons": list(fraud.reasons),
        "checks": checks,
        "quota": quota_dict(ctx.db, p.company, user, p.at),
    }


PROMISE_PREFIX = "promise:"


def promises_of(flags: list[Any] | None) -> list[dict[str, str]]:
    """Обязательства «обещания процесса» из флагов приглашения: код и текст из конфига (неизвестный код не показываем)."""
    labels = cfg().invitation.promises
    codes = [str(f)[len(PROMISE_PREFIX):] for f in flags or [] if str(f).startswith(PROMISE_PREFIX)]
    return [{"code": c, "label": labels[c]} for c in codes if c in labels]


def send(ctx: Ctx, user: User, spec: SendSpec, *, batch: bool) -> SendResult:
    unknown = sorted(set(spec.promises) - set(cfg().invitation.promises))
    if unknown:
        raise bad_request("promise_unknown", f"Неизвестное обещание процесса: {', '.join(unknown)}")
    spec.promises = list(dict.fromkeys(spec.promises))
    c = cfg()
    p = _prepare(ctx, user, spec, batch=batch, strict=True)
    company, sender, items, meta, at = p.company, p.sender, p.items, p.meta, p.at

    result = SendResult(rejected=p.rejected)
    if not batch:
        draft, cand = items[0]
        try:
            inv, out = send_invitation(draft, sender, cand, at, cfg=c)
        except ContactError as err:
            raise contact_error(err) from None
        sent = [(inv, out)]
    else:
        br = send_batch(items, sender, at, cfg=c)
        by_id = {inv.candidate_id: inv for inv in br.sent}
        sent = []
        for _draft, cand in items:
            inv = by_id.get(cand.candidate_id)
            if inv is None:
                chk = br.rejected.get(cand.candidate_id)
                if chk is None:
                    continue  # дубль в пачке
                result.rejected.append(
                    {"ref": meta[cand.candidate_id][0], "pseudonym": cand.pseudonym, **issues(chk)}
                )
                continue
            sent.append((inv, _outcome_of(br.outcome, inv.id)))
        result.warnings.extend(dict.fromkeys(br.outcome.warnings))
        result.notes.extend(n for n in dict.fromkeys(br.outcome.notes) if n not in result.notes)

    batch_id = uuid.uuid4() if batch else None
    for inv, out in sent:
        row = _save_new(ctx, company, user, inv, out, batch_id)
        if spec.promises:  # хранится во флагах приглашения: условия (OfferTerms) и их sha256 не меняются
            row.flags = [*row.flags, *(f"{PROMISE_PREFIX}{p}" for p in spec.promises)]
        result.sent.append(row)
        result.warnings.extend(w for w in out.warnings if w not in result.warnings)
        result.notes.extend(n for n in out.notes if n not in result.notes)
        _mail_candidate(ctx, row, inv, company)
    return result


def _outcome_of(total: Outcome, invitation_id: str) -> Outcome:
    out = Outcome()
    out.events = [e for e in total.events if getattr(e, "invitation_id", None) == invitation_id]
    out.side_effects = [s for s in total.side_effects if s.data.get("invitation_id") == invitation_id]
    return out


def _save_new(ctx: Ctx, company: Company, user: User, inv: DInv, out: Outcome, batch_id: uuid.UUID | None) -> Invitation:
    db = ctx.db
    row = Invitation(
        id=uuid.UUID(inv.id),
        company_id=company.id,
        sender_id=user.id,
        need_id=uuid.UUID(inv.need_id) if inv.need_id else None,
        vacancy_id=uuid.UUID(inv.vacancy_id) if inv.vacancy_id else None,
        candidate_id=uuid.UUID(inv.candidate_id),
        batch_id=batch_id,
        pseudonym=inv.pseudonym,
        status=str(inv.status),
        current_terms_ver=1,
        respond_by=inv.respond_by,
        score_snapshot=jsonable(dict(inv.score_snapshot or {})),
        rank_at_send=inv.rank_at_send,
        why_you=list(inv.why_you),
        flags=list(inv.flags),
        caution=list(inv.caution),
        created_at=inv.sent_at,
        updated_at=inv.sent_at,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        # Гонка двух запросов: «одно активное приглашение на пару» держит частичный уникальный индекс
        raise conflict("pair_active", f"У {inv.pseudonym} уже есть активное приглашение от вас") from None
    db.add(terms_to_row(inv.terms[0]))
    db.flush()
    persist(ctx, row, inv, out)
    return row


def _mail_candidate(ctx: Ctx, row: Invitation, inv: DInv, company: Company) -> None:
    """Письмо кандидату: «Приглашение: …, 220 000–280 000 ₽ на руки — Компания» (spec-contact §2.2 п. 3)."""
    user = ctx.db.get(User, row.candidate_id)
    if user is None:
        return
    t = inv.terms[0]
    type_label = cfg().salary.types.get(t.salary_type, "")
    subject = f"Приглашение: {t.title}, {salary_range(t.salary_from, t.salary_to)} {type_label} — {company.name}"
    text = (
        f"{company.name} приглашает вас: {t.title}.\n"
        f"Вилка: {salary_range(t.salary_from, t.salary_to)} {type_label}.\n"
        f"Ответить нужно {rel(inv.respond_by)}: «Да», «Да, если…» или «Нет».\n"
        "Контакты откроются компании только после вашего согласия."
    )
    send_mail_safe(ctx.mailer, user.email, subject, text)


# --- действия сторон ------------------------------------------------------------------------------


def act(ctx: Ctx, row: Invitation, action: str, actor: str, actor_id: uuid.UUID, *, comment: str | None = None, **payload: Any) -> Outcome:
    """Действие стороны по таблице переходов движка. Отказ → ApiError; ленивые сроки до отказа сохраняются."""
    ctx.db.refresh(row, with_for_update=True)  # одно действие за раз: второе увидит уже новый статус
    inv = to_domain(ctx.db, row)
    try:
        out = apply_action(inv, action, actor, now(), actor_id=str(actor_id), cfg=cfg(), comment=comment, **payload)
    except ContactError as err:
        prior = err.prior
        if prior is not None and (prior.events or prior.side_effects):
            persist(ctx, row, inv, prior)
            ctx.finish()
        raise contact_error(err) from None
    persist(ctx, row, inv, out, comment=comment)
    return out


def candidate_fields(db: Session, candidate_id: uuid.UUID, fields: Sequence[str] | None) -> list[str] | None:
    """Какие поля раскрыть: явный выбор кандидата или его настройки видимости К13 (None — по умолчанию config)."""
    if fields is not None:
        return list(fields)
    profile = db.get(CandidateProfile, candidate_id)
    if profile is None or not profile.field_visibility:
        return None
    return [k for k, on in profile.field_visibility.items() if on]


def make_counter(
    *,
    salary_from: int | None,
    fmt: str | None,
    start_date: date | None,
    comment: str | None,
    auto_disclose: bool | None,
    fields: Sequence[str] | None,
) -> CounterOffer:
    return CounterOffer(
        salary_from=salary_from,
        format=fmt,
        start_date=start_date,
        comment=comment or "",
        auto_disclose=auto_disclose,
        disclosure_fields=tuple(fields) if fields is not None else None,
    )


def withdraw_pending_for_need(ctx: Ctx, need_id: uuid.UUID, actor_id: uuid.UUID | None) -> int:
    """Контракт для раздела брифов: бриф закрыт → ждущие ответа приглашения отзываются «позиция закрыта»."""
    rows = list(ctx.db.scalars(select(Invitation).where(Invitation.need_id == need_id, Invitation.status.in_(tuple(PENDING_STATUSES)))))
    n = 0
    for row in rows:
        inv = to_domain(ctx.db, row)
        out = withdraw_for_closed_need([inv], now(), actor_id=str(actor_id) if actor_id else None, cfg=cfg())
        persist(ctx, row, inv, out)
        n += 1 if inv.status == S.WITHDRAWN else 0
    return n


def withdraw_pending_for_company(ctx: Ctx, company_id: uuid.UUID, actor_id: uuid.UUID | None) -> int:
    """Компания прекращает работу (удалён последний аккаунт работодателя): ждущие ответа приглашения отзываются
    так же, как при закрытии брифа («позиция закрыта») — кандидат получает уведомление, надёжность компании не страдает."""
    rows = list(
        ctx.db.scalars(
            select(Invitation).where(Invitation.company_id == company_id, Invitation.status.in_(tuple(PENDING_STATUSES)))
        )
    )
    n = 0
    for row in rows:
        inv = to_domain(ctx.db, row)
        out = withdraw_for_closed_need([inv], now(), actor_id=str(actor_id) if actor_id else None, cfg=cfg())
        persist(ctx, row, inv, out)
        n += 1 if inv.status == S.WITHDRAWN else 0
    return n


# --- чтение -----------------------------------------------------------------------------------------


def _filters(q: Any, **cond: Any) -> Any:
    for col, val in cond.items():
        if val is not None:
            q = q.where(getattr(Invitation, col) == val)
    return q


def employer_rows(db: Session, company_id: uuid.UUID, need_id: uuid.UUID | None, status: str | None) -> list[Invitation]:
    q = _filters(select(Invitation).where(Invitation.company_id == company_id), need_id=need_id, status=status)
    return list(db.scalars(q.order_by(Invitation.created_at.desc(), Invitation.id.desc())))


def candidate_rows(db: Session, candidate_id: uuid.UUID, status: str | None) -> list[Invitation]:
    q = _filters(select(Invitation).where(Invitation.candidate_id == candidate_id), status=status)
    return list(db.scalars(q.order_by(Invitation.created_at.desc(), Invitation.id.desc())))


def candidate_new_count(db: Session, candidate_id: uuid.UUID) -> int:
    """Непросмотренные входящие (is_new: статус «отправлено» и не открыто) — агрегатом, не по странице."""
    return int(
        db.scalar(
            select(func.count()).where(
                Invitation.candidate_id == candidate_id, Invitation.status == str(S.SENT), Invitation.viewed_at.is_(None)
            )
        )
        or 0
    )


def encode_cursor(row: Invitation) -> str:
    raw = f"{row.created_at.isoformat()}|{row.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, _, rid = raw.partition("|")
        return datetime.fromisoformat(at), uuid.UUID(rid)
    except ValueError:
        raise bad_request("bad_cursor", "Неверный курсор страницы") from None


def _page(q: Any, db: Session, limit: int | None, cursor: str | None) -> tuple[list[Invitation], str | None]:
    """Страница по ключу (created_at, id) по убыванию: стабильна при новых приглашениях (в отличие от offset)."""
    if cursor:
        at, rid = decode_cursor(cursor)
        q = q.where(tuple_(Invitation.created_at, Invitation.id) < tuple_(at, rid))
    q = q.order_by(Invitation.created_at.desc(), Invitation.id.desc())
    if limit is None:
        return list(db.scalars(q)), None
    rows = list(db.scalars(q.limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, (encode_cursor(rows[-1]) if more and rows else None)


def employer_page(
    db: Session, company_id: uuid.UUID, need_id: uuid.UUID | None, status: str | None, limit: int | None, cursor: str | None
) -> tuple[list[Invitation], str | None]:
    q = _filters(select(Invitation).where(Invitation.company_id == company_id), need_id=need_id, status=status)
    return _page(q, db, limit, cursor)


def candidate_page(
    db: Session, candidate_id: uuid.UUID, status: str | None, limit: int | None, cursor: str | None
) -> tuple[list[Invitation], str | None]:
    q = _filters(select(Invitation).where(Invitation.candidate_id == candidate_id), status=status)
    return _page(q, db, limit, cursor)


def get_for_employer(db: Session, user: User, invitation_id: uuid.UUID) -> Invitation:
    row = db.get(Invitation, invitation_id)
    if row is None or user.company_id is None or row.company_id != user.company_id:
        raise not_found("invitation_not_found", "Приглашение не найдено")
    return row


def get_for_candidate(db: Session, user: User, invitation_id: uuid.UUID) -> Invitation:
    row = db.get(Invitation, invitation_id)
    if row is None or row.candidate_id != user.id:
        raise not_found("invitation_not_found", "Приглашение не найдено")
    return row


def _salary(t: OfferTerms | Any) -> dict[str, Any]:
    stype = t.salary_type if hasattr(t, "salary_type") else ("net" if t.salary_net else "gross")
    label = cfg().salary.types.get(stype, "")
    return {
        "salary_from": t.salary_from,
        "salary_to": t.salary_to,
        "salary_type": stype,
        "salary_type_label": label,
        "text": f"{salary_range(t.salary_from, t.salary_to)} {label}".strip(),
    }


def _terms_dict(t: Any) -> dict[str, Any]:
    fmt_labels = cfg().invitation.formats
    return {
        "ver": t.ver,
        "author": t.author,
        "title": t.title,
        "description": t.description,
        **_salary(t),
        "format": t.format,
        "format_label": fmt_labels.get(t.format) if t.format else None,
        "start_date": t.start_date,
        "contact_method": t.contact_method,
        "contact_method_label": cfg().invitation.contact_methods.get(t.contact_method),
        "contact_value": t.contact_value,
        "conditions": dict(t.conditions),
        "sha256": t.sha256,
        "verified": t.verify(),
    }


def _timers(inv: DInv, at: datetime) -> dict[str, Any]:
    check_at = outcome_check_at(inv, cfg())
    return {
        "respond_by": inv.respond_by if inv.status in (S.SENT, S.VIEWED) else None,
        "respond_by_text": rel(inv.respond_by, at) if inv.status in (S.SENT, S.VIEWED) else None,
        "counter_respond_by": inv.counter_respond_by if inv.status == S.COUNTERED else None,
        "counter_respond_by_text": rel(inv.counter_respond_by, at) if inv.status == S.COUNTERED else None,
        "contact_due_at": inv.contact_due_at if inv.contacted_at is None else None,
        "contact_due_text": rel(inv.contact_due_at, at) if inv.contact_due_at and inv.contacted_at is None else None,
        "outcome_check_at": check_at if inv.outcome_check is None and inv.status in (S.ACCEPTED, S.CLOSED) else None,
    }


_API_ACTIONS = {
    "view": "view",
    "accept": "accept",
    "decline": "decline",
    "counter": "counter",
    "withdraw": "withdraw",
    "accept_counter": "counter/accept",
    "decline_counter": "counter/decline",
    "close": "outcome",
    "revoke_consent": "revoke-consent",
    "grant_consent": "grant-consent",
    "mark_interview": "interview",
    "mark_contacted": "contacted",
    "dispute_contact": "dispute",
    "outcome_check": "check",
}


def actions_for(inv: DInv, actor: str) -> list[str]:
    return [_API_ACTIONS[str(a)] for a in available_actions(inv.status, actor) if str(a) in _API_ACTIONS]


def _counter_view(inv: DInv, at: datetime) -> dict[str, Any] | None:
    if inv.counter is None:
        return None
    first = inv.terms[0]
    c = inv.counter
    return {
        "salary_from": c.salary_from,
        "format": c.format,
        "start_date": c.start_date,
        "comment": c.comment or None,
        "auto_disclose": c.auto_disclose if c.auto_disclose is not None else cfg().counter_offer.auto_disclose_default,
        "summary": counter_summary(c, first, at, cfg()),
        "within_range": c.salary_from is not None and c.salary_from <= first.salary_to,
        "countered_at": inv.countered_at,
    }


def employer_events(inv: DInv) -> list[dict[str, Any]]:
    """Журнал для работодателя: без id участника (DECISIONS 20) — только роль."""
    out = []
    for e in inv.events:
        payload = {k: v for k, v in dict(e.payload).items() if k not in ("consent_text_ver",)}
        out.append(
            {
                "seq": e.seq,
                "at": e.at,
                "action": e.action,
                "from_status": e.from_status,
                "to_status": e.to_status,
                "label": e.label,
                "actor_role": e.actor_role,
                "payload": jsonable(payload),
            }
        )
    return out


def employer_card(
    ctx: Ctx, row: Invitation, inv: DInv | None = None, at: datetime | None = None, ident: identity.Ident | None = None
) -> dict[str, Any]:
    at = at or now()
    inv = inv or to_domain(ctx.db, row)
    t = inv.current_terms
    ident = ident or identity.alias(ctx.db, ctx.secret, row.company_id, row.candidate_id)
    return {
        "id": row.id,
        "pseudonym": row.pseudonym,
        "candidate_ref": ident.ref,
        "need_id": row.need_id,
        "vacancy_id": row.vacancy_id,
        "status": str(inv.status),
        "status_label": status_label(inv.status, cfg()),
        "title": t.title,
        "salary": _salary(t),
        "sent_at": inv.sent_at,
        "sent_text": rel(inv.sent_at, at),
        "viewed": inv.viewed_at is not None,
        "contacts_open": inv.contacts_open(at),
        "contacted": inv.contacted_at is not None,
        "contact_overdue": inv.contact_overdue,
        "disputed": inv.disputed,
        "stage": inv.stage,
        "outcome": inv.outcome,
        "flags": list(inv.flags),
        "score": (row.score_snapshot or {}).get("score"),
        "rank_at_send": row.rank_at_send,
        "counter": _counter_view(inv, at),
        "decline_reason": inv.decline_reason,
        "decline_reason_label": cfg().reasons.decline.get(inv.decline_reason) if inv.decline_reason else None,
        "decline_comment": inv.decline_comment or None,
        "timers": _timers(inv, at),
        "actions": actions_for(inv, "employer"),
    }


def employer_detail(ctx: Ctx, row: Invitation) -> dict[str, Any]:
    at = now()
    inv = to_domain(ctx.db, row)
    card = employer_card(ctx, row, inv, at)
    card["terms"] = [_terms_dict(t) for t in inv.terms]
    card["why_you"] = list(inv.why_you)
    card["promises"] = promises_of(row.flags)
    card["caution"] = list(inv.caution)
    card["score_snapshot"] = row.score_snapshot or None
    card["events"] = employer_events(inv)
    card["candidate"] = candidate_for_employer(
        ctx.db,
        candidate_id=row.candidate_id,
        company_id=row.company_id,
        pseudonym=row.pseudonym,
        at=at,
        offer_salary_to=inv.current_terms.salary_to,
        need_id=row.need_id,
        ref=card.get("candidate_ref"),
    )
    return card


def employer_cards(ctx: Ctx, rows: list[Invitation], at: datetime) -> list[dict[str, Any]]:
    """Карточки списка: условия/события/раскрытия и псевдонимы грузятся пакетом, число запросов не зависит от длины."""
    if not rows:
        return []
    with prefetched(ctx.db, rows):
        idents = identity.aliases(ctx.db, ctx.secret, rows[0].company_id, [r.candidate_id for r in rows])
        return [employer_card(ctx, r, None, at, idents.get(r.candidate_id)) for r in rows]


def _funnel_stats(db: Session, company_id: uuid.UUID, need_id: uuid.UUID | None) -> dict[str, Any]:
    """Колонки, воронка и причины отказов по ВСЕМ приглашениям (а не по странице): один запрос-агрегат."""
    q = select(
        Invitation.need_id,
        Invitation.status,
        Invitation.decline_reason,
        func.count(),
        func.count(Invitation.viewed_at),
        func.count(Invitation.countered_at),
        func.count(Invitation.accepted_at),
        func.count(Invitation.contacted_at),
        func.sum(case((Invitation.outcome == "hired", 1), else_=0)),
        func.sum(case((Invitation.rank_at_send > 0, Invitation.rank_at_send), else_=0)),
        func.sum(case((Invitation.rank_at_send > 0, 1), else_=0)),
    ).where(Invitation.company_id == company_id)
    q = _filters(q, need_id=need_id).group_by(Invitation.need_id, Invitation.status, Invitation.decline_reason)
    by_status: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    funnel: Counter[str] = Counter()
    per_need: dict[str, dict[str, int]] = {}
    rank_sum = rank_n = 0
    for need, st, reason, n, viewed, countered, accepted, contacted, hired, rsum, rn in db.execute(q):
        by_status[st] += n
        if need is not None:
            f = per_need.setdefault(str(need), {"sent": 0, "viewed": 0, "countered": 0, "accepted": 0})
            f["sent"] += n
            f["viewed"] += viewed
            f["countered"] += n if st == "countered" else 0
            f["accepted"] += accepted
        if st == "declined" and reason:
            reasons[reason] += n
        funnel.update(
            {"sent": n, "viewed": viewed, "countered": countered, "accepted": accepted, "contacted": contacted, "hired": int(hired or 0)}
        )
        rank_sum += int(rsum or 0)
        rank_n += int(rn or 0)
    return {"by_status": by_status, "reasons": reasons, "funnel": funnel, "rank_sum": rank_sum, "rank_n": rank_n, "per_need": per_need}


def kanban(
    ctx: Ctx,
    company_id: uuid.UUID,
    need_id: uuid.UUID | None,
    rows: list[Invitation],
    next_cursor: str | None = None,
) -> dict[str, Any]:
    """Р8: колонки по статусам (встречные — наверху), цифры воронки, сводка причин отказов с подсказкой.

    `rows` — одна страница карточек; счётчики колонок, воронка и сводка считаются агрегатом по всем приглашениям."""
    at = now()
    c = cfg()
    cards = employer_cards(ctx, rows, at)
    st = _funnel_stats(ctx.db, company_id, need_id)
    order = ["countered", "sent", "viewed", "accepted", "closed", "declined", "counter_declined", "expired", "withdrawn", "consent_revoked"]
    columns = []
    for status in order:
        items = [x for x in cards if x["status"] == status]
        columns.append({"status": status, "label": c.status_labels.invitation[status], "count": st["by_status"].get(status, 0), "items": items})
    funnel = {k: int(st["funnel"].get(k, 0)) for k in ("sent", "viewed", "countered", "accepted", "contacted", "hired")}
    reasons = st["reasons"]
    summary = [{"reason": k, "label": c.reasons.decline.get(k, k), "count": n} for k, n in reasons.most_common()]
    hint = None
    total = sum(reasons.values())
    if total and reasons.most_common(1)[0][0] in c.reasons.decline_suggest_counter:
        n = reasons.most_common(1)[0][1]
        hint = f"{n} из {total} отказов — из-за зарплаты: поднимите верх вилки, чтобы охватить больше кандидатов"
    return {
        "columns": columns,
        "funnel": funnel,
        "decline_summary": summary,
        "decline_hint": hint,
        "avg_rank_at_send": round(st["rank_sum"] / st["rank_n"], 1) if st["rank_n"] else None,
        "per_need": [{"need_id": uuid.UUID(k), **v} for k, v in st["per_need"].items()],
        "next_cursor": next_cursor,
    }


def _salary_vs_min(db: Session, candidate_id: uuid.UUID, t: Any) -> str | None:
    profile = db.get(CandidateProfile, candidate_id)
    if profile is None or not profile.salary_min:
        return None
    to = t.salary_to
    if profile.salary_type != t.salary_type:
        # Приводим к основанию минимума кандидата по ставке НДФЛ из конфига подбора (scoring.yaml → U.ndfl_rate)
        from app.core.configs import load_config

        ndfl = float(load_config("scoring")["U"]["ndfl_rate"])
        to = to * (1 - ndfl) if t.salary_type == "gross" else to / (1 - ndfl)
    pct = round((to / profile.salary_min - 1) * 100)
    if pct >= 0:
        return f"Верх вилки выше вашего минимума на {pct}%"
    return f"Верх вилки ниже вашего минимума на {-pct}%"


def _feedback_for_candidate(db: Session, row: Invitation) -> list[dict[str, Any]]:
    from .feedback import reason_label, skill_links

    out = []
    for f in db.scalars(
        select(Feedback).where(Feedback.target_type == "invitation", Feedback.target_id == row.id, Feedback.direction == "company_to_candidate")
    ):
        out.append(
            {
                "reason": f.reason_code,
                "label": reason_label(f.reason_code, "invitation"),
                "comment": f.comment,
                "links": skill_links(list(f.skill_ids or [])),
            }
        )
    return out


def candidate_cards(ctx: Ctx, rows: list[Invitation], at: datetime) -> list[dict[str, Any]]:
    """Карточки входящих пакетом: приглашения и компании с надёжностью — по одному запросу на страницу."""
    if not rows:
        return []
    ids = list({r.company_id for r in rows})
    companies = {c.id: c for c in ctx.db.scalars(select(Company).where(Company.id.in_(ids)))}
    events = events_by_company(ctx.db, ids)
    trust = trust_of_many(ctx.db, ids)
    comps = {cid: company_card(ctx.db, c, at, events.get(cid, []), trust.get(cid)) for cid, c in companies.items()}
    with prefetched(ctx.db, rows):
        return [candidate_card(ctx, r, None, at, comps.get(r.company_id), preloaded=True) for r in rows]


def candidate_card(
    ctx: Ctx,
    row: Invitation,
    inv: DInv | None = None,
    at: datetime | None = None,
    comp: dict[str, Any] | None = None,
    *,
    preloaded: bool = False,
) -> dict[str, Any]:
    """К9: вилка первой строкой, компания и надёжность, срок."""
    at = at or now()
    inv = inv or to_domain(ctx.db, row)
    t = inv.current_terms
    if not preloaded:
        company = ctx.db.get(Company, row.company_id)
        comp = company_card(ctx.db, company, at) if company else None
    return {
        "id": row.id,
        "salary": _salary(t),
        "title": t.title,
        "company": {
            "id": comp["id"],
            "name": comp["name"],
            "personal_email_label": comp["personal_email_label"],
            "reliability": comp["reliability"]["display"],
            "r": comp["reliability"]["r"],
            "discrepancy_badge": comp["reliability"]["discrepancy_badge"],
            "trust": comp["trust"],
        }
        if comp
        else None,
        "status": str(inv.status),
        "status_label": status_label(inv.status, cfg()),
        "is_new": inv.viewed_at is None and inv.status == S.SENT,
        "caution": cfg().antifraud.label if inv.caution else None,
        "sent_text": rel(inv.sent_at, at),
        "timers": _timers(inv, at),
    }


def candidate_detail(ctx: Ctx, row: Invitation) -> dict[str, Any]:
    """К10: условия, сравнение с минимумом, «почему вы», надёжность с разложением, плашка осторожности,
    кнопки ответа; после «да» — контакты компании, «должна связаться …», «со мной не связались»."""
    at = now()
    c = cfg()
    inv = to_domain(ctx.db, row)
    t = inv.current_terms
    company = ctx.db.get(Company, row.company_id)
    card = candidate_card(ctx, row, inv, at)
    card["company"] = company_card(ctx.db, company, at) if company else None
    card["terms"] = _terms_dict(t)
    card["terms_history"] = [_terms_dict(x) for x in inv.terms[:-1]]
    card["why_you"] = list(inv.why_you)
    card["promises"] = promises_of(row.flags)
    card["team"] = team_of(ctx.db, row.need_id)
    card["salary_vs_min"] = _salary_vs_min(ctx.db, row.candidate_id, t)
    card["caution_reasons"] = list(inv.caution)
    card["counter"] = _counter_view(inv, at)
    card["counter_limits"] = (
        {
            "salary_min": t.salary_from,
            "salary_max": counter_salary_cap(t.salary_to, c),
            "salary_step": c.counter_offer.salary_step,
            "max_conditions": c.counter_offer.max_conditions,
            "comment_max_len": c.counter_offer.comment_max_len,
            "conditions": dict(c.counter_offer.conditions),
            "auto_disclose_default": c.counter_offer.auto_disclose_default,
        }
        if inv.status == S.VIEWED and inv.counter is None
        else None
    )
    card["decline_reasons"] = [
        {
            "code": k,
            "label": v,
            "suggest_counter": k in c.reasons.decline_suggest_counter,
            "hint": decline_hint(inv, k, c),
        }
        for k, v in c.reasons.decline.items()
    ]
    card["consent"] = {
        "text_version": c.disclosure.consent_text_version,
        "default_fields": list(c.disclosure.default_fields),
        "contact_fields": dict(c.disclosure.contact_fields),
    }
    card["contacts_open"] = inv.contacts_open(at)
    card["contacted"] = inv.contacted_at is not None
    # Флаг совпадает с guard перехода (срок первого контакта прошёл): раньше горел сразу после «да», а запрос давал 409
    card["can_dispute"] = domain_can_dispute(inv, at)
    card["outcome"] = inv.outcome
    card["outcome_label"] = c.reasons.outcome.get(inv.outcome) if inv.outcome else None
    card["outcome_check_options"] = dict(c.reasons.outcome_check) if inv.accepted_at else None
    card["outcome_check"] = inv.outcome_check
    card["feedback"] = _feedback_for_candidate(ctx.db, row)
    card["actions"] = actions_for(inv, "candidate")
    card["reported"] = _complaint(ctx.db, row) is not None
    card["complaint_reasons"] = dict(c.reasons.complaint)
    card["events"] = [
        {"seq": e.seq, "at": e.at, "action": e.action, "to_status": e.to_status, "label": e.label, "actor_role": e.actor_role}
        for e in inv.events
    ]
    return card


def mark_viewed_if_new(ctx: Ctx, row: Invitation, user: User) -> None:
    """GET кандидатом = «кандидат открыл» (sent → viewed). Истёкшее не открывается — его покажет refresh."""
    refresh_rows(ctx, [row])
    if row.status == S.SENT:
        act(ctx, row, "view", "candidate", user.id)


# --- «Пожаловаться» (антифейк, концепция §8.1: запись и счётчик) ---------------------------------

def _complaint(db: Session, row: Invitation) -> TrustComplaint | None:
    return trust_complaints.find(db, row.candidate_id, "invitation", row.id)


def report(ctx: Ctx, row: Invitation, reason: str, comment: str | None) -> None:
    """Жалоба кандидата на приглашение. Одна на приглашение; компания о ней не уведомляется (защита кандидата).
    Жалоба попадает в очередь модерации и учитывается в квоте компании (app/trust/complaints.py, TRUST_AND_SAFETY.md)."""
    trust_complaints.file_complaint(
        ctx.db,
        ctx.db.get(User, row.candidate_id),
        target_type="invitation",
        target_id=row.id,
        company_id=row.company_id,
        reason=reason,
        comment=comment,
        at=now(),
    )


def complaints_count(db: Session, company_id: uuid.UUID, days: int) -> int:
    """Жалобы кандидатов на компанию за окно (кроме отклонённых модератором)."""
    since = now() - timedelta(days=days)
    return int(
        db.scalar(
            select(func.count())
            .select_from(TrustComplaint)
            .where(
                TrustComplaint.company_id == company_id,
                TrustComplaint.status.in_(trust_complaints.COUNTED),
                TrustComplaint.created_at >= since,
            )
        )
        or 0
    )