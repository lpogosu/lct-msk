"""Раздел «Тестирование»: брифинг, адаптивный тест, результат, разбор, «Мой грейд» (spec-testing §2.2–2.5, §4;
концепция §3.1 шаги 4–6 и 15, §5; экраны К3–К6).

Все правила (выбор задания, стоп, решение, доборы, 14/30/90 дней, доверие) — в app/testing; HTTP-слой
только проверяет роль и владельца, ограничивает частоту и отдаёт DTO. Ключи экземпляров и теги
дистракторов в ответы не попадают никогда; разбор ошибок — только своего экземпляра и только после решения.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import select

from app.api.deps import DbSession, LimiterDep, MetaDep, SettingsDep, require_roles
from app.core.clock import clock
from app.core.errors import ApiError, bad_request, conflict, error_responses, not_found
from app.models.enums import FlagStatus, UserRole
from app.models.testing import Flag as FlagRow
from app.models.testing import TestSession as SessionRow
from app.models.users import User
from app.services.testing import labels as L
from app.services.testing import limits, pool, views
from app.services.testing import sessions as svc
from app.services.testing.qualification import get_row, load_domain
from app.services.testing.schemas import (
    AnswerIn,
    AnswerOut,
    BriefingOut,
    CandidateFlagOut,
    DisputeIn,
    EventsIn,
    EventsOut,
    ExtensionIn,
    ExtensionOut,
    NextOut,
    QualificationOut,
    ResultOut,
    ReviewOut,
    SessionCreateIn,
    SessionOut,
)
from app.testing.session import CLOSED_STATUSES, STATUS_LABELS_RU, SessionStatus

router = APIRouter(prefix="/tests", tags=["Тестирование"], responses=error_responses())

Candidate = Annotated[User, Depends(require_roles(UserRole.candidate))]

# Решение принято — результат и разбор можно показывать (на проверке/прерван/аннулирован — скрыты)
_DECIDED = (SessionStatus.DECIDED, SessionStatus.EXTENSION_OFFERED, SessionStatus.FINAL)


def _known_spec(code: str) -> None:
    info = L.spec_info(code)
    if info is None:
        raise bad_request("unknown_specialization", "Неизвестная специализация", {"code": code})
    if info.get("status") == "derived":
        raise bad_request(
            "derived_specialization",
            f"Отдельного теста «{info['name']}» нет: категория присваивается, когда подтверждены обе квалификации",
        )


# ---------------------------------------------------------------------------
# Брифинг и создание сессии
# ---------------------------------------------------------------------------


@router.get(
    "/briefing",
    response_model=BriefingOut,
    summary="Выбор грейда и брифинг",
    description=(
        "Экран К3: карточки грейдов с поведенческими якорями и порогами линейки, подсказка по стажу "
        "(ни к чему не обязывает), мягкое предупреждение при выборе Senior и стаже меньше 24 месяцев, правила "
        "теста, состояние согласия на журналирование событий теста, можно ли начать сейчас и дата следующей "
        "смены грейда. Для подтверждённого грейда — режим теста на повышение."
    ),
)
def briefing(
    user: Candidate,
    db: DbSession,
    specialization: Annotated[str, Query(max_length=32, description="Код специализации")],
) -> BriefingOut:
    _known_spec(specialization)
    svc.refresh_user_sessions(db, user.id)
    row = get_row(db, user.id, specialization)
    out = views.briefing_out(db, user, specialization, load_domain(row) if row is not None else None)
    db.commit()
    return out


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    response_model=SessionOut,
    summary="Начать тест",
    description=(
        "Создаёт сессию адаптивного теста: первичный тест или повтор (по правилам 14 дней и не больше 3 попыток "
        "за 90 дней) либо тест на повышение на одну ступень (не раньше чем через 90 дней после смены грейда, "
        "после неудачи — через 30 дней). Нужна отдельная галочка согласия на журналирование событий теста "
        "(`consent_test_logging`), если оно ещё не дано. Одна активная сессия на аккаунт. Ошибки правил — 409 "
        "с кодом и `details.available_at`."
    ),
)
def create_session(
    body: SessionCreateIn, user: Candidate, db: DbSession, limiter: LimiterDep, meta: MetaDep, settings: SettingsDep
) -> SessionOut:
    limiter.check("tests_create", str(user.id), limits.RL_SESSION_CREATE)
    if body.express and not settings.demo_mode:
        # Экспресс-версия существует только в DEMO_MODE: на боевом стенде её «нет» (как и остальных демо-инструментов)
        raise not_found("not_found", "Ресурс не найден")
    _known_spec(body.specialization)
    warm = pool.readiness(db, body.specialization)
    if not warm.ready:
        # #14: не ошибка сервера, а ожидание — экран брифинга уже показывает прогресс
        raise ApiError(503, "pool_warming_up", warm.message, {"progress": warm.progress, "eta_s": warm.eta_s})
    ld = svc.create_session(
        db,
        user,
        spec=body.specialization,
        grade=body.grade,
        upgrade=body.upgrade,
        consent_test_logging=body.consent_test_logging,
        ip=meta.ip,
        user_agent=meta.user_agent,
        express=bool(body.express),
    )
    out = views.session_out(ld)
    db.commit()
    return out


@router.get(
    "/sessions/{session_id}",
    response_model=SessionOut,
    summary="Состояние сессии",
    description="Статус, прогресс «задание 7 · обычно 12–20», связь с добором. Только своя сессия (чужая — 404).",
)
def get_session(session_id: uuid.UUID, user: Candidate, db: DbSession) -> SessionOut:
    ld = svc.load(db, session_id, user)
    svc.tick(db, ld)
    svc.save(db, ld)
    out = views.session_out(ld)
    db.commit()
    return out


# ---------------------------------------------------------------------------
# Ход теста
# ---------------------------------------------------------------------------


@router.get(
    "/sessions/{session_id}/next",
    response_model=NextOut,
    summary="Текущее задание",
    description=(
        "Экран К4: одно задание — только то, что видит кандидат (формулировка, код, варианты, строки, таблица), "
        "код варианта и серверный дедлайн (лимит + 5 с). Идемпотентно: пока на задание не ответили, возвращается "
        "оно же. Если дедлайн прошёл — задание засчитывается неверным и выдаётся следующее. `item = null` — тест "
        "завершён, смотрите результат. Кнопки «назад» нет."
    ),
)
def next_item(
    session_id: uuid.UUID, user: Candidate, db: DbSession, limiter: LimiterDep, background: BackgroundTasks
) -> NextOut:
    limiter.check("tests_next", str(user.id), limits.RL_NEXT)
    ld, item = svc.next_item(db, user, session_id)
    if item is not None:
        out = NextOut(session=views.session_out(ld), item=views.item_out(ld, item), message=views.NO_BACK_NOTE)
        # Пул: 200 экземпляров на семейство, догенерация при остатке < 50 (spec-testing §2.5) — после ответа
        if pool.needs_top_up(db, item.instance.family_id):
            background.add_task(pool.top_up_background, db.get_bind(), [item.instance.family_id])
    else:
        out = NextOut(
            session=views.session_out(ld),
            item=None,
            message=f"Заданий больше нет: тест {STATUS_LABELS_RU[ld.sess.status]}. Смотрите результат",
        )
    db.commit()
    return out


@router.post(
    "/sessions/{session_id}/answer",
    response_model=AnswerOut,
    summary="Ответить на задание",
    description=(
        "Ответ на текущее задание по его номеру `seq`. Идемпотентно: повтор того же `seq` ничего не меняет "
        "и возвращает `duplicate = true`. Ответ после серверного дедлайна засчитывается как неверный "
        "(`timed_out = true`). `answer = null` — пропуск. Верность ответа во время теста не сообщается."
    ),
)
def answer(session_id: uuid.UUID, body: AnswerIn, user: Candidate, db: DbSession, limiter: LimiterDep) -> AnswerOut:
    limiter.check("tests_answer", str(user.id), limits.RL_ANSWER)
    res = svc.submit_answer(db, user, session_id, body.seq, body.answer)
    st = res.ld.sess.status
    out = AnswerOut(
        seq=res.seq,
        accepted=True,
        duplicate=res.duplicate,
        timed_out=res.timed_out,
        status=str(st),
        status_ru=STATUS_LABELS_RU[st],
        finished=not res.ld.sess.is_open,
    )
    db.commit()
    return out


@router.post(
    "/sessions/{session_id}/events",
    response_model=EventsOut,
    summary="События страницы теста",
    description=(
        "Пачка событий фронта (уход со вкладки и возврат, вставка, копирование, вырезание, перетаскивание, "
        "вставка своего текста, копирование в обход обработчика, переподключение) — до 50 за раз. Записываются "
        "только при действующем согласии на журналирование и только для идущего теста (после 30 минут тишины "
        "тест прерывается и события не принимаются); по ним при решении работают детекторы (больше 3 уходов "
        "дольше 10 с, вставка в поле ответа → мягкий флаг) и защита от подсказчика."
    ),
)
def events(session_id: uuid.UUID, body: EventsIn, user: Candidate, db: DbSession, limiter: LimiterDep) -> EventsOut:
    limiter.check("tests_events", str(user.id), limits.RL_EVENTS)
    res = svc.add_events(db, user, session_id, [e.model_dump() for e in body.events])
    db.commit()
    return EventsOut(accepted=res.accepted, ignored=res.ignored, status=res.status)


@router.post(
    "/sessions/{session_id}/extension",
    response_model=ExtensionOut,
    summary="Добор вверх или вниз",
    description=(
        "Развилка после решения (в течение 24 часов): принять добор вверх (Senior сейчас, до 8 заданий, повышает "
        "только при уверенности 90%) или вниз (до 8 заданий; если ответов уже достаточно — подтверждение одним "
        "нажатием), прошлые ответы засчитываются. Отказ закрывает попытку."
    ),
)
def extension(session_id: uuid.UUID, body: ExtensionIn, user: Candidate, db: DbSession) -> ExtensionOut:
    ld, child = svc.resolve_extension(
        db, user, session_id, direction=body.direction, accept=body.decision == "accept"
    )
    if child is None:
        message = "Добор отклонён: попытка закрыта"
    elif child.sess.is_open:
        message = "Добор начат: продолжайте тест"
    else:
        message = "Готово: решение по уже данным ответам"
    out = ExtensionOut(
        session=views.session_out(ld), child=views.session_out(child) if child is not None else None, message=message
    )
    db.commit()
    return out


# ---------------------------------------------------------------------------
# Результат и разбор
# ---------------------------------------------------------------------------


@router.get(
    "/sessions/{session_id}/result",
    response_model=ResultOut,
    summary="Результат теста",
    description=(
        "Экран К5: решение и строка «Middle подтверждён · 542 · уверенность 94%», балл на линейке 300–600 "
        "с 80%-интервалом и порогами, «Насколько мы уверены» по полосам грейдов, «решено по уровням», модули "
        "✓/✓✓/~, предложения добора, превью «так вас видит работодатель». На проверке у методиста, после "
        "обрыва или аннулирования результат скрыт с пояснением. До решения — 409."
    ),
)
def result(session_id: uuid.UUID, user: Candidate, db: DbSession) -> ResultOut:
    ld = svc.load(db, session_id, user)
    svc.tick(db, ld)
    svc.save(db, ld)
    if ld.sess.decision is None and ld.sess.status not in CLOSED_STATUSES:
        db.commit()
        raise conflict("result_not_ready", "Тест ещё идёт: результат появится после последнего задания")
    out = views.result_out(db, user, ld)
    db.commit()
    return out


@router.get(
    "/sessions/{session_id}/review",
    response_model=ReviewOut,
    summary="Разбор ошибок",
    description=(
        "По каждому ошибочному заданию — свой экземпляр, свой ответ, верный ответ и объяснение заблуждения. "
        "Доступно только после решения: экземпляр больше никому не выдаётся, поэтому показывать ответ безопасно."
    ),
)
def review(session_id: uuid.UUID, user: Candidate, db: DbSession) -> ReviewOut:
    ld = svc.load(db, session_id, user)
    svc.tick(db, ld)
    svc.save(db, ld)
    db.commit()
    if ld.sess.decision is None or ld.sess.status not in _DECIDED:
        if ld.sess.status in (SessionStatus.UNDER_REVIEW, SessionStatus.ABANDONED, SessionStatus.INVALIDATED):
            raise conflict("review_unavailable", f"Разбор недоступен: тест {STATUS_LABELS_RU[ld.sess.status]}")
        raise conflict("result_not_ready", "Разбор появится после последнего задания")
    return views.review_out(db, ld)


# ---------------------------------------------------------------------------
# «Мой грейд»
# ---------------------------------------------------------------------------


@router.get(
    "/qualification",
    response_model=list[QualificationOut],
    summary="Мой грейд",
    description=(
        "Экран К6 по всем специализациям: статус, категория, балл с интервалом (с поправкой на давность), "
        "уверенность, доверие, актуальность, подтверждённые и заявленные модули, дата следующей смены грейда "
        "и попытки, история смен и попыток, свои флаги с кнопкой «оспорить». Работодатель этого не видит."
    ),
)
def qualifications(user: Candidate, db: DbSession) -> list[QualificationOut]:
    svc.refresh_user_sessions(db, user.id)
    out = views.qualifications_out(db, user)
    db.commit()
    return out


@router.get(
    "/qualification/{specialization}",
    response_model=QualificationOut,
    summary="Мой грейд по специализации",
    description="То же, что «Мой грейд», для одной специализации. Нет квалификации (опрос не пройден) — 404.",
)
def qualification(specialization: str, user: Candidate, db: DbSession) -> QualificationOut:
    svc.refresh_user_sessions(db, user.id)
    row = get_row(db, user.id, specialization)
    if row is None:
        raise not_found("qualification_not_found", "Квалификация по этой специализации не найдена: пройдите опрос")
    out = views.qualification_out(db, user, specialization, load_domain(row))
    db.commit()
    return out


@router.post(
    "/qualification/{specialization}/downgrade",
    response_model=QualificationOut,
    summary="Перейти на грейд ниже",
    description=(
        "Добровольное понижение на одну ступень. Считается сменой грейда: вне первых 24 часов после теста — "
        "не чаще раза в 90 дней. Принудительно грейд не понижается никогда."
    ),
)
def downgrade(specialization: str, user: Candidate, db: DbSession) -> QualificationOut:
    row = svc.voluntary_downgrade(db, user, specialization)
    out = views.qualification_out(db, user, specialization, load_domain(row))
    db.commit()
    return out


@router.post(
    "/flags/{flag_id}/dispute",
    response_model=CandidateFlagOut,
    summary="Оспорить флаг",
    description=(
        "Возражение кандидата по своему флагу целостности (например, «вставлял из своего черновика»). Флаг уходит "
        "методисту первым в очереди. Оспорить можно один раз и только открытый флаг; чужой флаг — 404."
    ),
)
def dispute(flag_id: uuid.UUID, body: DisputeIn, user: Candidate, db: DbSession, limiter: LimiterDep) -> CandidateFlagOut:
    limiter.check("tests_dispute", str(user.id), limits.RL_DISPUTE)
    row = db.execute(
        select(FlagRow, SessionRow.candidate_id)
        .join(SessionRow, SessionRow.id == FlagRow.session_id)
        .where(FlagRow.id == flag_id)
        .with_for_update(of=FlagRow)
    ).first()
    if row is None or row[1] != user.id:
        raise not_found("flag_not_found", "Флаг не найден")
    f = row[0]
    if f.status != FlagStatus.open.value:
        raise conflict("flag_already_resolved", "Флаг уже разобран методистом")
    if f.disputed_at is not None:
        raise conflict("flag_already_disputed", "Возражение по этому флагу уже отправлено")
    f.dispute_text = body.text.strip()
    f.disputed_at = clock.now()
    db.flush()
    out = next(x for x in views.candidate_flags(db, user.id) if x.id == f.id)
    db.commit()
    return out
