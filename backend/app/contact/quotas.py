"""Квоты работодателя, лимит кандидата, пауза для пары, «только после просмотра» (концепция §4.3).

Чистые функции над снимком фактов (SenderState, CandidateState), который собирает слой БД.
Все отказы и предупреждения — готовые русские фразы с относительными датами (DECISIONS 9).

Важно: статусы в SentRecord должны быть уже «освежены» ленивым применением сроков
(invitation.refresh) — иначе истёкшее без просмотра не вернёт слот.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from fractions import Fraction

from .clock import require_aware, to_local, week_start
from .reliability import Reliability
from .rules import Check, Issue, validate_salary_range
from .settings import ContactConfig, get_contact_config
from .statuses import PAIR_ACTIVE_STATUSES, PENDING_STATUSES, InvitationStatus
from .texts import days, relative, rub


@dataclass(frozen=True)
class SentRecord:
    """Отправленное приглашение владельца квоты (домена или аккаунта)."""

    invitation_id: str
    company_id: str
    candidate_id: str
    sent_at: datetime
    status: str
    viewed: bool = False
    need_id: str | None = None


@dataclass(frozen=True)
class Cooldown:
    """Пауза перед повторным приглашением пары компания—кандидат."""

    company_id: str
    candidate_id: str
    until: datetime
    reason: str  # declined | counter_declined | expired
    salary_unlock_from: int | None = None  # «можно раньше, если новый from ≥ 1.15·прошлый to»
    decline_reason: str | None = None


@dataclass(frozen=True)
class SenderState:
    company_id: str
    email: str
    account_id: str
    sends: tuple[SentRecord, ...] = ()
    reliability: Reliability | None = None
    # Жалобы кандидатов (config/trust.yaml -> complaints): множитель квоты и причина. 1.0 — жалоб меньше порога.
    complaint_factor: float = 1.0
    complaint_reason: str | None = None


@dataclass(frozen=True)
class CandidateState:
    candidate_id: str
    pseudonym: str
    search_mode: str = "active"  # active | open | hidden
    # None = без ограничения; по умолчанию — candidate_limits.default_weekly из config
    weekly_cap: int | None = field(
        default_factory=lambda: get_contact_config().candidate_limits.default_weekly
    )
    received_at: tuple[datetime, ...] = ()  # когда кандидат получал приглашения (от любых компаний)
    blocked_company_ids: frozenset[str] = frozenset()
    birth_year: int | None = None
    guardian_consent: bool = False  # согласие законного представителя (15–17 лет), config/contact.yaml -> minors
    card_opened: bool = False  # DECISIONS 7: открыта панель Р6 …
    shortlisted: bool = False  # … ИЛИ кандидат в шортлисте
    score: float | None = None
    pair_statuses: tuple[str, ...] = ()  # статусы приглашений ЭТОЙ компании этому кандидату
    cooldowns: tuple[Cooldown, ...] = ()


@dataclass(frozen=True)
class QuotaUsage:
    used: int
    limit: int
    base_limit: int
    factor: float
    next_slot_at: datetime | None
    reduced_reason: str | None = None

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def label(self) -> str:
        """Счётчик для экрана подборки: «осталось 57/60»."""
        return f"осталось {self.remaining}/{self.limit}"


@dataclass
class BatchCheck:
    errors: list[Issue] = field(default_factory=list)
    per_candidate: dict[str, Check] = field(default_factory=dict)

    @property
    def allowed(self) -> list[str]:
        return [cid for cid, c in self.per_candidate.items() if c.ok] if not self.errors else []


# --- владелец квоты -------------------------------------------------------------------------------


def email_domain(email: str) -> str:
    if "@" not in email:
        raise ValueError("Некорректный email")
    return email.rsplit("@", 1)[1].strip().lower()


def is_personal_email(email: str, cfg: ContactConfig | None = None) -> bool:
    cfg = cfg or get_contact_config()
    return email_domain(email) in cfg.quotas.personal_email_domains


def quota_owner_key(email: str, account_id: str, cfg: ContactConfig | None = None) -> str:
    """DECISIONS 8: личная почта (gmail, mail.ru, …) — квота на аккаунт, корпоративная — на домен.

    Иначе все работодатели с gmail делили бы одну квоту на 60 приглашений.
    """
    if is_personal_email(email, cfg):
        return f"account:{account_id}"
    return f"domain:{email_domain(email)}"


# --- квота владельца ------------------------------------------------------------------------------


def counts_against_quota(rec: SentRecord) -> bool:
    """Истёкшее без просмотра возвращает слот: массовая рассылка «в пустоту» не окупается."""
    return not (rec.status == InvitationStatus.EXPIRED and not rec.viewed)


def quota_usage(
    sender: SenderState,
    now: datetime,
    cfg: ContactConfig | None = None,
    planned: Sequence[SentRecord] = (),
) -> QuotaUsage:
    cfg = cfg or get_contact_config()
    q = cfg.quotas
    now = require_aware(now)
    since = now - timedelta(days=q.window_days)
    counted = [r for r in (*sender.sends, *planned) if since < r.sent_at <= now and counts_against_quota(r)]
    factor = 1.0
    reason = None
    rel = sender.reliability
    if rel is not None and rel.quota_factor < 1.0:
        factor = rel.quota_factor
        reason = rel.discrepancy_badge or q.low_reliability.label
    if sender.complaint_factor < factor:
        factor = sender.complaint_factor
        reason = sender.complaint_reason or reason
    limit = math.floor(q.new_per_owner * factor)
    # Когда освободится слот: окно должны покинуть (used − limit + 1) самых старых отправок. При квоте,
    # урезанной ×0.5, использовано может быть больше лимита — тогда ждать первой из них мало.
    times = sorted(r.sent_at for r in counted)
    idx = max(0, len(times) - limit)
    next_slot_at = times[idx] + timedelta(days=q.window_days) if idx < len(times) else None
    return QuotaUsage(len(counted), limit, q.new_per_owner, factor, next_slot_at, reason)


def need_active_count(sender: SenderState, need_id: str, planned: Sequence[SentRecord] = ()) -> int:
    return sum(1 for r in (*sender.sends, *planned) if r.need_id == need_id and r.status in PENDING_STATUSES)


# --- лимит кандидата ------------------------------------------------------------------------------


def candidate_week_usage(
    candidate: CandidateState, now: datetime, cfg: ContactConfig | None = None, planned: int = 0
) -> tuple[int, datetime]:
    """(получено на этой неделе, когда лимит обновится). Неделя — ISO, по Москве (InboxCounter.iso_week)."""
    start = week_start(now, cfg)
    received = sum(1 for t in candidate.received_at if to_local(t, cfg) >= start) + planned
    return received, start + timedelta(days=7)


# --- пауза для пары -------------------------------------------------------------------------------


def cooldown_after(
    *,
    company_id: str,
    candidate_id: str,
    status: str,
    at: datetime,
    decline_reason: str | None = None,
    last_salary_to: int | None = None,
    cfg: ContactConfig | None = None,
) -> Cooldown | None:
    """Пауза после финального статуса: отказ — 30 дней, истекло — 14.

    Отказ по причине «зарплата» снимается раньше, если новый from ≥ 1.15·прошлый to:
    «возвращайтесь с деньгами, а не с давлением» (spec-contact §2.4).
    """
    cfg = cfg or get_contact_config()
    pause = cfg.pair.pause_days.get(str(status))
    if not pause:
        return None
    unlock = None
    if decline_reason in cfg.pair.salary_exception_reasons and last_salary_to:
        unlock = math.ceil(Fraction(str(cfg.pair.salary_exception_factor)) * last_salary_to)
    return Cooldown(
        company_id=company_id,
        candidate_id=candidate_id,
        until=require_aware(at) + timedelta(days=pause),
        reason=str(status),
        salary_unlock_from=unlock,
        decline_reason=decline_reason,
    )


def cooldown_after_withdrawal(
    *,
    company_id: str,
    candidate_id: str,
    reason: str,
    at: datetime,
    cfg: ContactConfig | None = None,
) -> Cooldown | None:
    """DECISIONS 22: после отзыва приглашения (withdrawn) или отзыва согласия (consent_revoked) — пауза пары,
    как после expired. Без неё компания могла бы тут же пригласить снова, а у кандидата из защиты остался бы
    только блок-лист. Зарплатного исключения нет: условия здесь ни при чём."""
    cfg = cfg or get_contact_config()
    pause = cfg.pair.withdrawal_pause_days.get(str(reason))
    if not pause:
        return None
    return Cooldown(
        company_id=company_id,
        candidate_id=candidate_id,
        until=require_aware(at) + timedelta(days=pause),
        reason=str(reason),
    )


def _pause_message(cd: Cooldown, pseudonym: str, now: datetime) -> str:
    when = relative(cd.until, now)
    if cd.reason == InvitationStatus.EXPIRED:
        return f"Пауза после истёкшего приглашения: {pseudonym} можно пригласить снова {when}"
    if cd.reason == InvitationStatus.WITHDRAWN:
        return f"Пауза после отозванного приглашения: {pseudonym} можно пригласить снова {when}"
    if cd.reason == InvitationStatus.CONSENT_REVOKED:
        return f"Пауза после отзыва согласия: {pseudonym} можно пригласить снова {when}"
    if cd.reason == InvitationStatus.COUNTER_DECLINED:
        text = (
            f"Пауза после несостоявшегося встречного предложения: {pseudonym} можно пригласить снова {when}"
        )
    else:
        text = f"Пауза после отказа {pseudonym}: пригласить снова можно {when}"
    if cd.salary_unlock_from:
        text += f" или сразу, если нижняя граница вилки будет от {rub(cd.salary_unlock_from)}"
    return text


# --- главная проверка -----------------------------------------------------------------------------


def min_possible_age(birth_year: int, now: datetime, cfg: ContactConfig | None = None) -> int:
    """Храним только год рождения — берём минимально возможный возраст (осторожно в пользу несовершеннолетних)."""
    return to_local(now, cfg).year - birth_year - 1


def check_invite(
    sender: SenderState,
    candidate: CandidateState,
    *,
    need_id: str | None,
    salary_from: int,
    salary_to: int,
    salary_type: str = "net",
    is_internship: bool = False,
    now: datetime,
    cfg: ContactConfig | None = None,
    planned: Sequence[SentRecord] = (),
) -> Check:
    """Можно ли отправить приглашение: квота, лимит кандидата, пауза, одно активное на пару,
    просмотр карточки, вилка. planned — уже одобренные приглашения той же пачки."""
    cfg = cfg or get_contact_config()
    now = require_aware(now)
    p = candidate.pseudonym
    check = Check()

    # Недоступность не детализируем: причина (блок-лист, возраст, режим) — личное дело кандидата.
    min_age = min_possible_age(candidate.birth_year, now, cfg) if candidate.birth_year is not None else None
    unavailable = (
        candidate.search_mode == "hidden"
        or sender.company_id in candidate.blocked_company_ids
        or (
            min_age is not None
            and (
                min_age < cfg.minors.hidden_below_age
                or (min_age < cfg.minors.guardian_consent_below_age and not candidate.guardian_consent)
            )
        )
    )
    if unavailable:
        check.error("candidate_unavailable", f"{p} сейчас не принимает приглашения")
        return check
    if min_age is not None and min_age < cfg.minors.internship_only_below_age and not is_internship:
        # Кандидату 15–17 уже видно работодателю (согласие представителя есть), поэтому причину называем прямо:
        # карточка несёт ту же плашку (config/contact.yaml -> minors.employer_notice).
        check.error("minor_internship_only", f"{p}: {cfg.minors.invite_restriction}")
        return check

    check.merge(validate_salary_range(salary_from, salary_to, salary_type, cfg))

    if not (candidate.card_opened or candidate.shortlisted):
        check.error(
            "card_not_opened",
            f"Сначала откройте карточку {p}: пригласить можно только того, чью карточку вы смотрели "
            "или добавили в шортлист",
        )

    in_batch = sum(1 for r in planned if r.candidate_id == candidate.candidate_id)
    pair_active = [s for s in candidate.pair_statuses if s in PAIR_ACTIVE_STATUSES]
    if cfg.pair.one_active_per_pair and (pair_active or in_batch):
        if InvitationStatus.ACCEPTED in pair_active:
            check.error("pair_contact_open", f"Контакт с {p} уже открыт — новое приглашение не нужно")
        elif pair_active:
            label = cfg.status_labels.invitation[str(pair_active[0])]
            check.error("pair_active", f"У {p} уже есть активное приглашение от вас (статус «{label}»)")
        else:
            check.error("pair_in_batch", f"{p} уже есть в этой пачке")

    pauses = [c for c in candidate.cooldowns if c.company_id == sender.company_id and c.until > now]
    if pauses:
        cd = max(pauses, key=lambda c: c.until)
        if cd.salary_unlock_from and salary_from >= cd.salary_unlock_from:
            check.note(
                "pause_lifted_by_salary",
                f"Пауза снята: нижняя граница вилки не меньше {rub(cd.salary_unlock_from)}",
            )
        else:
            check.error("pair_pause", _pause_message(cd, p, now))

    cap = candidate.weekly_cap
    if cap is not None:
        received, resets = candidate_week_usage(candidate, now, cfg, planned=in_batch)
        if received >= cap:
            check.error(
                "candidate_weekly_limit",
                f"У {p} исчерпан лимит приглашений на эту неделю — снова можно {relative(resets, now)}",
            )

    if need_id is not None:
        active = need_active_count(sender, need_id, planned)
        limit = cfg.quotas.active_per_need
        if active >= limit:
            check.error(
                "need_active_limit",
                f"По этому брифу уже {active} активных приглашений из {limit} — "
                "дождитесь ответов или отзовите часть",
            )

    usage = quota_usage(sender, now, cfg, planned)
    if usage.reduced_reason:
        check.warn(
            "quota_reduced",
            f"Квота снижена до {usage.limit} новых приглашений за {days(cfg.quotas.window_days)}: "
            f"{usage.reduced_reason}",
        )
    if usage.used >= usage.limit:
        when = (
            f" Следующее можно будет отправить {relative(usage.next_slot_at, now)}."
            if usage.next_slot_at
            else ""
        )
        check.error(
            "owner_quota",
            f"Квота исчерпана: {usage.used} из {usage.limit} новых приглашений за "
            f"{days(cfg.quotas.window_days)}.{when} Истёкшие без просмотра приглашения возвращают слот",
        )

    if candidate.score is not None and candidate.score < cfg.matching.weak_score_below:
        check.warn(
            "weak_match",
            # floor, а не round: 39,6 не должно показываться как «40 из 100» при пороге «ниже 40»
            f"Слабое соответствие брифу: {math.floor(candidate.score)} из 100. Отправить можно, "
            "но шанс на «да» ниже",
        )
    if is_personal_email(sender.email, cfg):
        check.note("personal_email", f"Кандидат увидит пометку «{cfg.quotas.personal_email_label}»")
    return check


def check_batch(
    sender: SenderState,
    candidates: Sequence[CandidateState],
    *,
    need_id: str | None,
    salary_from: int,
    salary_to: int,
    salary_type: str = "net",
    is_internship: bool = False,
    now: datetime,
    cfg: ContactConfig | None = None,
) -> BatchCheck:
    """Пачка ≤ 10: проверяем по очереди, одобренные расходуют квоту для следующих."""
    cfg = cfg or get_contact_config()
    result = BatchCheck()
    if len(candidates) > cfg.quotas.batch_max:
        result.errors.append(
            Issue(
                "batch_too_large",
                f"В пачке — не больше {cfg.quotas.batch_max} приглашений: выбрано {len(candidates)}",
            )
        )
        return result
    planned: list[SentRecord] = []
    for cand in candidates:
        if cand.candidate_id in result.per_candidate:
            continue  # дубль в пачке: первая проверка остаётся в силе
        chk = check_invite(
            sender,
            cand,
            need_id=need_id,
            salary_from=salary_from,
            salary_to=salary_to,
            salary_type=salary_type,
            is_internship=is_internship,
            now=now,
            cfg=cfg,
            planned=planned,
        )
        result.per_candidate[cand.candidate_id] = chk
        if chk.ok:
            planned.append(
                SentRecord(
                    invitation_id=f"planned:{cand.candidate_id}",
                    company_id=sender.company_id,
                    candidate_id=cand.candidate_id,
                    sent_at=now,
                    status=InvitationStatus.SENT,
                    need_id=need_id,
                )
            )
    return result
