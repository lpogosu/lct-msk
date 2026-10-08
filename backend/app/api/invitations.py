"""Приглашения — ядро механики контакта (концепция §3.1 шаги 11–12, §3.2 шаги 8–10, §4.3, §4.5; spec-contact §2–§4).

Работодатель приглашает одного кандидата или пачку из шортлиста (≤ 10) с обязательной вилкой; кандидат отвечает
«Да» (с отдельным согласием на передачу контактов), «Да, если…» (≤ 2 условия, один раз) или «Нет» с причиной.
Контакты раскрываются только после взаимного «да». Сроки применяются лениво при каждом чтении (часы платформы —
DEMO_MODE «+1 день» их двигает). Решения принимает движок app/contact; здесь — доступ, хранение и DTO.

До раскрытия работодатель видит только псевдоним «К-…» и непрозрачную ссылку (DECISIONS 20): внутреннего id
кандидата и id участника событий в его ответах нет.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbSession, LimiterDep, MailerDep, SettingsDep, require_roles
from app.core.errors import error_responses
from app.core.settings import Settings
from app.mail.sender import Mailer
from app.models.enums import UserRole
from app.models.users import User
from app.services.contact import invitations as svc
from app.services.contact.common import cfg, check_rate, now
from app.services.contact.company import company_of, quota_dict
from app.services.contact.schemas import (
    AcceptIn,
    CheckIn,
    CounterIn,
    DeclineIn,
    DisputeIn,
    GrantConsentIn,
    InvitationBatchIn,
    InvitationCandidateDetail,
    InvitationCreateIn,
    InvitationEmployerDetail,
    InvitationFormIn,
    InvitationInboxOut,
    InvitationPreviewOut,
    KanbanOut,
    OutcomeIn,
    ReasonIn,
    ReportIn,
    SendResultOut,
)
from app.services.contact.store import Ctx, refresh_rows

router = APIRouter(prefix="/invitations", tags=["Приглашения"], responses=error_responses(400, 401, 403, 404, 409, 422, 429))

EmployerUser = Annotated[User, Depends(require_roles(UserRole.employer))]
CandidateUser = Annotated[User, Depends(require_roles(UserRole.candidate))]


def _ctx(db: DbSession, mailer: Mailer | None, settings: Settings) -> Ctx:
    return Ctx(db=db, mailer=mailer, secret=settings.pseudonym_secret.get_secret_value())


def _spec(body: InvitationFormIn, refs: list[str], why: dict[str, list[str]]) -> svc.SendSpec:
    return svc.SendSpec(
        refs=refs,
        need_id=body.need_id,
        vacancy_id=body.vacancy_id,
        title=body.title,
        description=body.description,
        salary_from=body.salary_from,
        salary_to=body.salary_to,
        salary_type=body.salary_type,
        format=body.format,
        start_date=body.start_date,
        contact_method=body.contact_method,
        contact_value=body.contact_value,
        response_days=body.response_days,
        why_you=why,
    )


def _send_out(ctx: Ctx, user: User, result: svc.SendResult) -> dict[str, Any]:
    at = now()
    company = company_of(ctx.db, user)
    return {
        "sent": [svc.employer_card(ctx, row, None, at) for row in result.sent],
        "rejected": result.rejected,
        "warnings": result.warnings,
        "notes": result.notes,
        "quota": quota_dict(ctx.db, company, user, at),
    }


def _limit_send(limiter: LimiterDep, user: User) -> None:
    check_rate(limiter, "invite_send", user.company_id or user.id, cfg().quotas.rate_limit_per_minute)


def _limit_respond(limiter: LimiterDep, user: User) -> None:
    check_rate(limiter, "invite_respond", user.id, cfg().http_limits.respond_per_minute)


# --- работодатель: отправка -------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SendResultOut,
    summary="Пригласить кандидата",
    description=(
        "Приглашение одному кандидату (Р7). `candidate` — псевдоним «К-4821» или ссылка `ref` из подборки; "
        "пригласить можно только того, чью карточку открывали или кто в шортлисте (DECISIONS 7). Пустые поля "
        "формы заполняются из брифа (`need_id`) или вакансии; без брифа — заголовок, описание ≥ 50 символов и вилка. "
        "Проверки: профиль компании заполнен, вилка (шире 1,6× — предупреждение, шире 2× — отказ), квота 60 новых "
        "за 7 дней на домен (личная почта — на аккаунт), ≤ 20 активных на бриф, лимит кандидата в неделю, пауза пары, "
        "одно активное приглашение на пару. Слабое соответствие брифу (Score < 40) — предупреждение, не отказ. "
        "400 — форма заполнена неверно, 409 — квота, лимит, пауза или профиль компании (причина словами в `message`)."
    ),
)
def create(
    body: InvitationCreateIn,
    user: EmployerUser,
    db: DbSession,
    mailer: MailerDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> dict[str, Any]:
    _limit_send(limiter, user)
    ctx = _ctx(db, mailer, settings)
    result = svc.send(ctx, user, _spec(body, [body.candidate], {body.candidate: body.why_you} if body.why_you else {}), batch=False)
    ctx.finish()
    return _send_out(ctx, user, result)


@router.post(
    "/batch",
    response_model=SendResultOut,
    summary="Пригласить пачку из шортлиста",
    description=(
        "До 10 кандидатов одним действием с одной формой; «почему вы» — своё для каждого (`why_you`: кандидат → "
        "пункты, по умолчанию — из строки подборки). Кому можно — приглашение уходит, остальным — понятная причина "
        "в `rejected` (квота, лимит, пауза…). Одобренные расходуют квоту для следующих в пачке. "
        "400 `batch_too_large` — больше 10."
    ),
)
def create_batch(
    body: InvitationBatchIn,
    user: EmployerUser,
    db: DbSession,
    mailer: MailerDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> dict[str, Any]:
    _limit_send(limiter, user)
    ctx = _ctx(db, mailer, settings)
    result = svc.send(ctx, user, _spec(body, list(body.candidates), dict(body.why_you)), batch=True)
    ctx.finish()
    return _send_out(ctx, user, result)


@router.post(
    "/preview",
    response_model=InvitationPreviewOut,
    summary="Превью «так увидит кандидат»",
    description=(
        "Ничего не отправляет: показывает карточку приглашения глазами кандидата (вилка, компания и её надёжность, "
        "плашка «Требует осторожности» при стоп-словах) и результаты проверок для каждого кандидата словами "
        "(«у К-4821 исчерпан лимит приглашений на эту неделю — снова можно через 3 дня»)."
    ),
)
def preview(
    body: InvitationBatchIn,
    user: EmployerUser,
    db: DbSession,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> dict[str, Any]:
    _limit_send(limiter, user)
    ctx = _ctx(db, None, settings)
    out = svc.preview(ctx, user, _spec(body, list(body.candidates), dict(body.why_you)))
    ctx.finish()
    return out


# --- работодатель: воронка ----------------------------------------------------------------------


@router.get(
    "",
    response_model=KanbanOut,
    summary="Воронка приглашений",
    description=(
        "Канбан по статусам (Р8) с таймерами; встречные условия — первой колонкой с карточкой разницы. Цифры "
        "воронки, сводка причин отказов с подсказкой по вилке, средняя позиция приглашённых в выдаче, квота. "
        "Фильтры: `need_id`, `status`."
    ),
)
def kanban(
    user: EmployerUser,
    db: DbSession,
    settings: SettingsDep,
    need_id: Annotated[uuid.UUID | None, Query(description="Только приглашения по этому брифу")] = None,
    status_: Annotated[str | None, Query(alias="status", description="Только приглашения в этом статусе")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Размер страницы карточек (по убыванию даты отправки)")] = 50,
    cursor: Annotated[str | None, Query(description="`next_cursor` предыдущего ответа")] = None,
) -> dict[str, Any]:
    ctx = _ctx(db, None, settings)
    company = company_of(db, user)
    rows, next_cursor = svc.employer_page(db, company.id, need_id, status_, limit, cursor)
    refresh_rows(ctx, rows)
    ctx.finish()
    out = svc.kanban(ctx, company.id, need_id, rows, next_cursor)
    out["quota"] = quota_dict(db, company, user, now())
    return out


@router.get(
    "/inbox",
    response_model=InvitationInboxOut,
    summary="Мои приглашения",
    description=(
        "Входящие приглашения кандидата (К9): вилка первой строкой, компания и надёжность, обратный отсчёт. "
        "Фильтр `status`. Истёкшие сроки применяются при чтении."
    ),
)
def inbox(
    user: CandidateUser,
    db: DbSession,
    settings: SettingsDep,
    status_: Annotated[str | None, Query(alias="status", description="Только приглашения в этом статусе")] = None,
    limit: Annotated[int | None, Query(ge=1, le=200, description="Размер страницы; без него — все (у кандидата их немного)")] = None,
    cursor: Annotated[str | None, Query(description="`next_cursor` предыдущего ответа")] = None,
) -> dict[str, Any]:
    ctx = _ctx(db, None, settings)
    rows, next_cursor = svc.candidate_page(db, user.id, status_, limit, cursor)
    refresh_rows(ctx, rows)
    ctx.finish()
    items = svc.candidate_cards(ctx, rows, now())
    return {"items": items, "new_count": svc.candidate_new_count(db, user.id), "next_cursor": next_cursor}


@router.get(
    "/inbox/{invitation_id}",
    response_model=InvitationCandidateDetail,
    summary="Приглашение (кандидат)",
    description=(
        "Карточка приглашения (К10): условия и их хеш, сравнение с вашим минимумом, «почему вы», надёжность "
        "компании с разложением, плашка осторожности, кнопки ответа. Открытие отмечает приглашение просмотренным. "
        "После «да» — обязательство компании «свяжется …» и «со мной не связались»."
    ),
)
def inbox_detail(
    invitation_id: uuid.UUID, user: CandidateUser, db: DbSession, mailer: MailerDep, settings: SettingsDep
) -> dict[str, Any]:
    ctx = _ctx(db, mailer, settings)
    row = svc.get_for_candidate(db, user, invitation_id)
    svc.mark_viewed_if_new(ctx, row, user)
    ctx.finish()
    return svc.candidate_detail(ctx, row)


@router.get(
    "/{invitation_id}",
    response_model=InvitationEmployerDetail,
    summary="Приглашение (работодатель)",
    description=(
        "Карточка приглашения для работодателя: снимки условий (v1 и v2 после встречного) с хешем, журнал "
        "событий без id участников, карточка кандидата — слепая до взаимного «да», раскрытая при действующем "
        "согласии, таймеры «ответ до», «свяжитесь до»."
    ),
)
def employer_detail(invitation_id: uuid.UUID, user: EmployerUser, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    ctx = _ctx(db, None, settings)
    row = svc.get_for_employer(db, user, invitation_id)
    refresh_rows(ctx, [row])
    ctx.finish()
    return svc.employer_detail(ctx, row)


# --- действия кандидата ---------------------------------------------------------------------------


def _candidate_act(
    invitation_id: uuid.UUID,
    user: User,
    db: DbSession,
    mailer: Mailer,
    settings: Settings,
    limiter: LimiterDep,
    action: str,
    **payload: Any,
) -> dict[str, Any]:
    _limit_respond(limiter, user)
    ctx = _ctx(db, mailer, settings)
    row = svc.get_for_candidate(db, user, invitation_id)
    if action in _ANSWERS:
        # Ответ на приглашение = кандидат его открыл: «Отправлено» → «Просмотрено» → ответ (два события),
        # даже если клиент ответил из списка, не открывая карточку
        svc.mark_viewed_if_new(ctx, row, user)
    svc.act(ctx, row, action, "candidate", user.id, **payload)
    ctx.finish()
    return svc.candidate_detail(ctx, row)


_ANSWERS = frozenset({"accept", "decline", "counter"})


def _employer_act(
    invitation_id: uuid.UUID,
    user: User,
    db: DbSession,
    mailer: Mailer,
    settings: Settings,
    limiter: LimiterDep,
    action: str,
    **payload: Any,
) -> dict[str, Any]:
    _limit_respond(limiter, user)
    ctx = _ctx(db, mailer, settings)
    row = svc.get_for_employer(db, user, invitation_id)
    svc.act(ctx, row, action, "employer", user.id, **payload)
    ctx.finish()
    return svc.employer_detail(ctx, row)


@router.post(
    "/{invitation_id}/view",
    response_model=InvitationCandidateDetail,
    summary="Отметить просмотренным",
    description="Кандидат открыл приглашение (то же делает GET /invitations/inbox/{id}). 409 — срок истёк.",
)
def view(
    invitation_id: uuid.UUID, user: CandidateUser, db: DbSession, mailer: MailerDep, settings: SettingsDep, limiter: LimiterDep
) -> dict[str, Any]:
    _limit_respond(limiter, user)
    ctx = _ctx(db, mailer, settings)
    row = svc.get_for_candidate(db, user, invitation_id)
    svc.refresh_rows(ctx, [row])
    if row.status == "sent":
        svc.act(ctx, row, "view", "candidate", user.id)
    ctx.finish()
    return svc.candidate_detail(ctx, row)


@router.post(
    "/{invitation_id}/accept",
    response_model=InvitationCandidateDetail,
    summary="Ответить «Да»",
    description=(
        "«Да» с отдельной галочкой «передать мои контакты компании» (`consent: true`, 152-ФЗ). Создаётся запись "
        "раскрытия и согласие в журнале; компания должна связаться за 3 рабочих дня. `fields` — какие поля "
        "раскрыть (по умолчанию — из настроек приватности), хотя бы один канал связи обязателен."
    ),
)
def accept(
    invitation_id: uuid.UUID,
    body: AcceptIn,
    user: CandidateUser,
    db: DbSession,
    mailer: MailerDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> dict[str, Any]:
    fields = svc.candidate_fields(db, user.id, body.fields)
    return _candidate_act(invitation_id, user, db, mailer, settings, limiter, "accept", consent=body.consent, fields=fields)


@router.post(
    "/{invitation_id}/decline",
    response_model=InvitationCandidateDetail,
    summary="Ответить «Нет»",
    description=(
        "Причина обязательна: salary, stack, format, company, not_looking, other_process, other. При причине "
        "«зарплата» в карточке есть подсказка ответить «Да, если…». Комментарий ≤ 300 символов уходит компании до "
        "раскрытия, поэтому контакты и ссылки в нём запрещены (DECISIONS 20). Пауза пары — 30 дней."
    ),
)
def decline(
    invitation_id: uuid.UUID,
    body: DeclineIn,
    user: CandidateUser,
    db: DbSession,
    mailer: MailerDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> dict[str, Any]:
    return _candidate_act(
        invitation_id, user, db, mailer, settings, limiter, "decline", reason=body.reason, comment=body.comment or ""
    )


@router.post(
    "/{invitation_id}/counter",
    response_model=InvitationCandidateDetail,
    summary="Ответить «Да, если…»",
    description=(
        "Одно встречное условие, один раз: до 2 условий из списка (зарплата от X, формат, дата выхода) и комментарий "
        "≤ 300 символов без контактов. Зарплата: from ≤ X ≤ 1,3·to, шаг 5000. Галочка «раскрыть контакты, если "
        "примут» (`auto_disclose`) стоит по умолчанию. Компания отвечает за 3 дня."
    ),
)
def counter(
    invitation_id: uuid.UUID,
    body: CounterIn,
    user: CandidateUser,
    db: DbSession,
    mailer: MailerDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> dict[str, Any]:
    fields = svc.candidate_fields(db, user.id, body.fields)
    offer = svc.make_counter(
        salary_from=body.salary_from,
        fmt=body.format,
        start_date=body.start_date,
        comment=body.comment,
        auto_disclose=body.auto_disclose,
        fields=fields,
    )
    return _candidate_act(invitation_id, user, db, mailer, settings, limiter, "counter", counter=offer, comment=body.comment or "")


@router.post(
    "/{invitation_id}/dispute",
    response_model=InvitationCandidateDetail,
    summary="«Со мной не связались»",
    description=(
        "Доступно после срока первого контакта (3 рабочих дня) или после отметки компании «Связались». "
        "Записывает «не связались» в надёжность компании (один раз на приглашение)."
    ),
)
def dispute(
    invitation_id: uuid.UUID,
    body: DisputeIn,
    user: CandidateUser,
    db: DbSession,
    mailer: MailerDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> dict[str, Any]:
    return _candidate_act(invitation_id, user, db, mailer, settings, limiter, "dispute_contact", comment=body.comment or "")


@router.post(
    "/{invitation_id}/check",
    response_model=InvitationCandidateDetail,
    summary="«Условия совпали?»",
    description=(
        "Через 10 дней после «да» один вопрос: match | lower_salary | no_vacancy | other. «Зарплата ниже» и "
        "«вакансии нет» снижают надёжность компании; от двух разных кандидатов за 90 дней — бейдж «есть "
        "расхождения условий» и квота ×0,5."
    ),
)
def check(
    invitation_id: uuid.UUID,
    body: CheckIn,
    user: CandidateUser,
    db: DbSession,
    mailer: MailerDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> dict[str, Any]:
    return _candidate_act(invitation_id, user, db, mailer, settings, limiter, "outcome_check", answer=body.answer)


@router.post(
    "/{invitation_id}/revoke-consent",
    response_model=InvitationCandidateDetail,
    summary="Отозвать согласие на передачу контактов",
    description=(
        "Контакты у компании снова скрыты; согласие в журнале отмечается отозванным; пауза пары 14 дней "
        "(DECISIONS 22). Из «Принято» приглашение переходит в «Согласие отозвано»."
    ),
)
def revoke_consent(
    invitation_id: uuid.UUID, user: CandidateUser, db: DbSession, mailer: MailerDep, settings: SettingsDep, limiter: LimiterDep
) -> dict[str, Any]:
    return _candidate_act(invitation_id, user, db, mailer, settings, limiter, "revoke_consent")


@router.post(
    "/{invitation_id}/grant-consent",
    response_model=InvitationCandidateDetail,
    summary="Подтвердить согласие после встречного",
    description=(
        "Если во «Да, если…» галочка автораскрытия была снята, а компания приняла условия, контакты открываются "
        "только после этого отдельного согласия."
    ),
)
def grant_consent(
    invitation_id: uuid.UUID,
    body: GrantConsentIn,
    user: CandidateUser,
    db: DbSession,
    mailer: MailerDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> dict[str, Any]:
    fields = svc.candidate_fields(db, user.id, body.fields)
    return _candidate_act(invitation_id, user, db, mailer, settings, limiter, "grant_consent", consent=body.consent, fields=fields)


@router.post(
    "/{invitation_id}/report",
    response_model=InvitationCandidateDetail,
    summary="Пожаловаться на приглашение",
    description=(
        "Антифейк (концепция §8.1): жалоба с причиной (fake_vacancy, payment_request, contacts_spam, "
        "misleading_terms, other). Одна на приглашение; компания не уведомляется, счётчик жалоб виден в её "
        "профиле. 409 — жалоба уже есть."
    ),
)
def report(
    invitation_id: uuid.UUID,
    body: ReportIn,
    user: CandidateUser,
    db: DbSession,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> dict[str, Any]:
    _limit_respond(limiter, user)
    ctx = _ctx(db, None, settings)
    row = svc.get_for_candidate(db, user, invitation_id)
    svc.report(ctx, row, body.reason, body.comment)
    ctx.finish()
    return svc.candidate_detail(ctx, row)


# --- действия работодателя -----------------------------------------------------------------------


@router.post(
    "/{invitation_id}/withdraw",
    response_model=InvitationEmployerDetail,
    summary="Отозвать приглашение",
    description=(
        "Пока кандидат не ответил (или ждёт ответа на встречное). Причина обязательна: position_closed, "
        "chose_other, sent_by_mistake, other. Пауза пары — 14 дней (DECISIONS 22)."
    ),
)
def withdraw(
    invitation_id: uuid.UUID,
    body: ReasonIn,
    user: EmployerUser,
    db: DbSession,
    mailer: MailerDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> dict[str, Any]:
    return _employer_act(invitation_id, user, db, mailer, settings, limiter, "withdraw", reason=body.reason, comment=body.comment or "")


@router.post(
    "/{invitation_id}/counter/accept",
    response_model=InvitationEmployerDetail,
    summary="Согласиться на встречное условие",
    description=(
        "Новый неизменяемый снимок условий v2 (from' = max(from, X)); если кандидат оставил галочку "
        "автораскрытия — контакты открываются сразу, и начинается срок первого контакта."
    ),
)
def counter_accept(
    invitation_id: uuid.UUID, user: EmployerUser, db: DbSession, mailer: MailerDep, settings: SettingsDep, limiter: LimiterDep
) -> dict[str, Any]:
    return _employer_act(invitation_id, user, db, mailer, settings, limiter, "accept_counter")


@router.post(
    "/{invitation_id}/counter/decline",
    response_model=InvitationEmployerDetail,
    summary="Отказать во встречном условии",
    description="Причина обязательна: salary, format, start_date, other. Пауза пары — 30 дней (раньше — с вилкой от 1,15× прошлого верха).",
)
def counter_decline(
    invitation_id: uuid.UUID,
    body: ReasonIn,
    user: EmployerUser,
    db: DbSession,
    mailer: MailerDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> dict[str, Any]:
    return _employer_act(
        invitation_id, user, db, mailer, settings, limiter, "decline_counter", reason=body.reason, comment=body.comment or ""
    )


@router.post(
    "/{invitation_id}/contacted",
    response_model=InvitationEmployerDetail,
    summary="«Связались»",
    description="Отметка первого контакта. В срок (3 рабочих дня) — плюс к надёжности.",
)
def contacted(
    invitation_id: uuid.UUID, user: EmployerUser, db: DbSession, mailer: MailerDep, settings: SettingsDep, limiter: LimiterDep
) -> dict[str, Any]:
    return _employer_act(invitation_id, user, db, mailer, settings, limiter, "mark_contacted")


@router.post(
    "/{invitation_id}/interview",
    response_model=InvitationEmployerDetail,
    summary="«Собеседование»",
    description="Этап «собеседование». Если «Связались» ещё не отмечено, контакт отмечается вместе с ним.",
)
def interview(
    invitation_id: uuid.UUID, user: EmployerUser, db: DbSession, mailer: MailerDep, settings: SettingsDep, limiter: LimiterDep
) -> dict[str, Any]:
    return _employer_act(invitation_id, user, db, mailer, settings, limiter, "mark_interview")


@router.post(
    "/{invitation_id}/outcome",
    response_model=InvitationEmployerDetail,
    summary="Итог: найм или «не сложилось»",
    description=(
        "`hired` — только после контакта. `not_matched` — с обязательной причиной (skills_missing с `skill_ids` "
        "навыков брифа, position_closed, chose_other). Причина уходит кандидату со ссылкой на короткую задачу по "
        "модулю теста или на интервью. Закрыть «не сложилось», так и не связавшись, — минус к надёжности."
    ),
)
def outcome(
    invitation_id: uuid.UUID,
    body: OutcomeIn,
    user: EmployerUser,
    db: DbSession,
    mailer: MailerDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> dict[str, Any]:
    return _employer_act(
        invitation_id,
        user,
        db,
        mailer,
        settings,
        limiter,
        "close",
        outcome=body.outcome,
        reason=body.reason,
        skill_ids=list(body.skill_ids),
        comment=body.comment or "",
    )

