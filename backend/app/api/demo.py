"""Раздел «Демо-режим» (DEMO_MODE, концепция §7.3, §11; DECISIONS п. 4, 25; 02-compliance G2, RK2).

Все маршруты отвечают 404, пока DEMO_MODE выключен: на боевом стенде этих инструментов нет. Часы «+1 день / +3 дня / сброс»
двигают сроки приглашений (app.core.clock); `reset` быстро возвращает мир к состоянию seed; автоответчик синтетических
кандидатов отвечает на приглашения жюри за 30–90 с; «режим жюри» подсвечивает верный ответ и помечает сессию demo_assisted;
`webhook-sink` принимает вебхуки ATS и показывает последние 20 событий.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.api.deps import (
    DbSession,
    LimiterDep,
    MetaDep,
    SettingsDep,
    bearer_scheme,
    get_current_user,
    require_roles,
)
from app.api.schemas import TokenResponse
from app.core.access import ensure_role
from app.core.errors import ApiError, error_responses, forbidden, not_found, unauthorized
from app.db.session import get_sessionmaker
from app.models.enums import UserRole
from app.models.matching import CandidateAlias
from app.models.users import User
from app.services.demo import jury, loop, sink, world
from app.services.demo import models as demo_models  # noqa: F401 — регистрирует таблицы демо в Base.metadata
from app.services.demo.cfg import section
from app.services.demo.clockctl import describe
from app.services.demo.models import DemoPersona
from app.services.demo.state import get_state

log = logging.getLogger("vzaimno.demo")


def require_demo_mode(request: Request) -> None:
    """DEMO_MODE выключен → маршрутов демо «нет» (404 в общем формате ошибок)."""
    if not request.app.state.settings.demo_mode:
        raise not_found("not_found", "Ресурс не найден")


@asynccontextmanager
async def demo_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """DEMO_MODE: подменить транспорт вебхуков на встроенный приёмник и запустить фоновый цикл (автоответчик).
    Фабрика сессий берётся из app.state.demo_session_factory (тесты подставляют свою) или из настроек БД."""
    settings = app.state.settings
    if not settings.demo_mode or not getattr(app.state, "demo_loop", True):
        yield
        return
    previous = sink.install()
    factory = getattr(app.state, "demo_session_factory", None) or get_sessionmaker()
    task = asyncio.create_task(loop.run(app, factory))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        sink.uninstall(previous)


router = APIRouter(
    prefix="/demo",
    tags=["Администрирование"],
    responses=error_responses(),
    dependencies=[Depends(require_demo_mode)],
    lifespan=demo_lifespan,
)

AnyUser = Annotated[User, Depends(require_roles(UserRole.candidate, UserRole.employer, UserRole.methodist, UserRole.admin))]

DEMO_KEY_HEADER = "X-Demo-Key"
_DEMO_ROLES = (UserRole.candidate.value, UserRole.employer.value, UserRole.methodist.value, UserRole.admin.value)


def _optional_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)], db: DbSession, settings: SettingsDep) -> User | None:
    return get_current_user(credentials, db, settings) if credentials is not None else None


def demo_controller(
    user: Annotated[User | None, Depends(_optional_user)],
    settings: SettingsDep,
    demo_key: Annotated[str | None, Header(alias=DEMO_KEY_HEADER, description="Ключ управления демо (VZ_DEMO_CONTROL_KEY)")] = None,
) -> User | None:
    """Кто может двигать общие часы, сбрасывать мир, включать автоответчик и «режим жюри» (они общие для всех).

    Вне prod (локальный стенд жюри) — любой вошедший, как и раньше: жюри в любой роли пользуется кнопками. В prod
    (публичный стенд с DEMO_MODE) — только роль «администратор» или заголовок X-Demo-Key = VZ_DEMO_CONTROL_KEY: иначе
    любой зарегистрировавшийся посетитель сбросил бы мир другим или включил подсказки всем кандидатам. Остальное в демо
    (состояние, список аккаунтов, подсказка владельцу сессии при включённом режиме) открыто, как прежде.
    """
    if not settings.demo_controls_restricted:
        if user is None:
            raise unauthorized("not_authenticated", "Требуется вход в систему")
        ensure_role(user, *_DEMO_ROLES)
        return user
    key = settings.demo_control_key
    if key is not None and demo_key and hmac.compare_digest(demo_key.encode(), key.get_secret_value().encode()):
        return user
    if user is None:
        raise unauthorized("not_authenticated", "Требуется вход в систему")
    if user.role != UserRole.admin.value:
        raise forbidden(
            "demo_controls_restricted",
            "На этом стенде часы, сброс и режим жюри переключает администратор (или ключ управления демо)",
        )
    return user


ControlUser = Annotated[User | None, Depends(demo_controller)]
CandidateUser = Annotated[User, Depends(require_roles(UserRole.candidate))]
EmployerUser = Annotated[User, Depends(require_roles(UserRole.employer))]


# --------------------------------------------------------------------------- схемы


class ClockOut(BaseModel):
    now: datetime = Field(description="Текущее время по часам приложения (с демо-смещением)")
    offset_seconds: int = Field(description="Смещение часов относительно настенных, секунды")
    offset_days: float = Field(description="То же в днях")
    shifted: bool = Field(description="Часы сдвинуты")


class ClockShiftIn(BaseModel):
    preset: Literal["plus_1d", "plus_3d", "reset"] = Field(
        description="plus_1d — «+1 день», plus_3d — «+3 дня», reset — сбросить смещение"
    )


class ClockShiftOut(ClockOut):
    swept: dict[str, Any] = Field(description="Что обновил проход по срокам: приглашения, отклики, повторы вебхуков")
    kept_alive: int = Field(description="Сколько незавершённых сессий персон освежено (не обрываются за 30 минут)")


class DemoStateOut(BaseModel):
    demo_mode: bool
    clock: ClockOut
    jury_mode: bool = Field(description="Режим жюри включён: подсказка к заданию доступна кандидату")
    jury_mode_since: datetime | None = None
    autoresponder: bool = Field(description="Автоответчик синтетических кандидатов включён")
    seeded_at: datetime | None = None
    scenario_built_at: datetime | None = Field(
        default=None, description="Автоответчик отвечает только на приглашения, отправленные позже этого момента"
    )
    synthetic_candidates: int
    presets: list[dict[str, str]] = Field(description="Кнопки часов: код и подпись")


class DemoAccountOut(BaseModel):
    key: str
    role: str = Field(description="candidate | employer | methodist | admin")
    email: str = Field(description="Логин. Пароль здесь не отдаётся")
    title: str = Field(description="Кто это и зачем")
    where: str = Field(description="Где использовать (сценарий демо)")


class DemoAccountsOut(BaseModel):
    accounts: list[DemoAccountOut]
    registration_email: str = Field(description="Email «Марии» для регистрации вживую: аккаунт заранее не создан")
    hint: str = Field(description="Где взять пароли")
    credentials_file: str = Field(description="Файл с логинами и паролями (на сервере, в .gitignore)")


class ResetOut(BaseModel):
    seconds: float
    counts: dict[str, int] = Field(description="Что есть в демо-мире после сброса")
    scenario: dict[str, Any] = Field(description="Сколько брифов, приглашений, откликов и задач создано; что пропущено")
    credentials_file: str


class JuryModeIn(BaseModel):
    enabled: bool


class AutoresponderIn(BaseModel):
    enabled: bool | None = Field(default=None, description="Включить или выключить автоответчик (не задано — не менять)")
    respond_now: bool = Field(default=False, description="Ответить на все ждущие приглашения синтетическим кандидатам сейчас")


class AutoresponderOut(BaseModel):
    enabled: bool
    answered: int = 0
    accepted: int = 0
    countered: int = 0
    declined: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class JuryHintOut(BaseModel):
    session_id: uuid.UUID
    seq: int = Field(description="Номер задания в сессии")
    answer_mode: str = Field(description="Формат ответа задания")
    answer: Any = Field(description="Верный ответ в том формате, в котором его принимает POST …/answer")
    answer_text: str | None = Field(default=None, description="Текст верного варианта (для выбора из списка)")
    demo_assisted: bool
    note: str


class SyntheticOut(BaseModel):
    label: str = Field(description="Подпись метки в карточке кандидата (Р6)")
    refs: list[str] = Field(description="Ссылки `ref` синтетических кандидатов, которых компания уже видела")


class SinkEventOut(BaseModel):
    received_at: datetime | None = None
    source: str = Field(description="platform — доставка платформы на демо-хост ATS; direct — прямой POST на приёмник")
    event_id: str | None = None
    event_type: str | None = None
    company: str | None = None
    signature: str | None = Field(default=None, description="Заголовок X-Vzaimno-Signature")
    signature_valid: bool | None = Field(default=None, description="Подпись совпала с ключом компании (нет данных — null)")
    attempts: int = 1
    status: str = "delivered"
    payload: Any = None


class SinkOut(BaseModel):
    url_path: str = Field(description="Путь приёмника")
    host: str = Field(description="Вымышленный хост ATS, на который демо-компания шлёт вебхуки")
    items: list[SinkEventOut]


class SinkAckOut(BaseModel):
    ok: bool
    event_id: str | None = None
    signature_valid: bool | None = None


# --------------------------------------------------------------------------- состояние и аккаунты


@router.get(
    "/state",
    response_model=DemoStateOut,
    summary="Состояние демо-режима",
    description=(
        "Часы приложения и смещение, режим жюри, автоответчик, отметки сборки мира, кнопки сдвига часов. Открытый маршрут: "
        "экран «Д» читает его до входа. 404 — DEMO_MODE выключен."
    ),
)
def state(db: DbSession) -> dict[str, Any]:
    st = get_state(db)
    n = int(db.scalar(select(func.count()).select_from(DemoPersona).where(DemoPersona.kind == "synthetic")) or 0)
    db.commit()
    return {
        "demo_mode": True,
        "clock": describe(),
        "jury_mode": st.jury_mode,
        "jury_mode_since": st.jury_mode_since,
        "autoresponder": st.autoresponder,
        "seeded_at": st.seeded_at,
        "scenario_built_at": st.scenario_built_at,
        "synthetic_candidates": n,
        "presets": [{"code": k, "label": str(v["label"])} for k, v in section("clock")["presets"].items()]
        + [{"code": "reset", "label": "Сбросить смещение"}],
    }


@router.get(
    "/accounts",
    response_model=DemoAccountsOut,
    summary="Демо-аккаунты",
    description=(
        "Логины ступенчатых кандидатов (зарегистрирован / опрос пройден / середина теста / с категорией), персон сценария "
        "(Анна, Илья), работодателей, жюри, методиста и администратора — без паролей. Пароли лежат на сервере в файле "
        "`backend/var/demo_accounts.md` (создаёт `make seed`). Открытый маршрут: экран входа показывает подсказку."
    ),
)
def accounts_list(settings: SettingsDep) -> dict[str, Any]:
    creds = world.credentials(settings)
    cfg = section("accounts")
    return {
        "accounts": [{"key": c.key, "role": c.role, "email": c.email, "title": c.title, "where": c.where} for c in creds],
        "registration_email": cfg["fresh_registration_email"],
        "hint": (
            "Пароли — в файле backend/var/demo_accounts.md на сервере (создаётся командой `make seed`; восстановить: "
            "`make demo-accounts`). Письма с кодами — в Mailpit: http://localhost:8025"
        ),
        "credentials_file": str(cfg["credentials_file"]),
    }


# --------------------------------------------------------------------------- часы и сброс


@router.post(
    "/clock",
    response_model=ClockShiftOut,
    summary="Сдвинуть часы демо",
    description=(
        "«+1 день», «+3 дня» или сброс смещения (app.core.clock). Двигает сроки приглашений, откликов и свежесть тестов; сразу "
        "применяет сроки (истёкшие приглашения, «нет ответа»), смещение переживает перезапуск. Нужен вход под любой ролью; "
        "на боевом стенде (VZ_ENV=prod) — только администратор или заголовок X-Demo-Key."
    ),
)
def clock_shift(body: ClockShiftIn, _: ControlUser, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    return loop.shift_clock(db, settings, body.preset)


@router.post(
    "/reset",
    response_model=ResetOut,
    summary="Сбросить демо-мир",
    description=(
        "Быстро (секунды) возвращает мир к состоянию seed: часы, приглашения, отклики, брифы, персоны (Анна, Илья, ступенчатые "
        "аккаунты), «Мария» удаляется, чтобы зарегистрироваться заново. Синтетическая популяция с тестами не пересоздаётся. "
        "Режим жюри сохраняет своё значение (сброс его не выключает). Нужен вход под любой ролью; на боевом стенде "
        "(VZ_ENV=prod) — только администратор или заголовок X-Demo-Key."
    ),
)
def reset(_: ControlUser, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    out = world.reset_world(settings, db)
    return {
        "seconds": out["seconds"],
        "counts": out["counts"],
        "scenario": out.get("scenario", {}),
        "credentials_file": out["credentials_file"],
    }


# --------------------------------------------------------------------------- автоответчик и режим жюри


@router.post(
    "/autoresponder",
    response_model=AutoresponderOut,
    summary="Автоответчик синтетических кандидатов",
    description=(
        "Включить/выключить автоответчик и/или ответить прямо сейчас (`respond_now`). Правила: «Да», если верхняя граница вилки "
        "не ниже минимума и формат подходит; «Да, если: от min», если минимум выше вилки не более чем в 1,2 раза; иначе «Нет» "
        "с причиной из персоны. В штатном режиме отвечает через 30–90 с после отправки. Нужен вход под любой ролью; "
        "на боевом стенде — только администратор или X-Demo-Key."
    ),
)
def autoresponder_control(body: AutoresponderIn, _: ControlUser, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    st = get_state(db)
    if body.enabled is not None:
        st.autoresponder = body.enabled
        db.commit()
    out: dict[str, Any] = {"enabled": st.autoresponder}
    if body.respond_now:
        from app.services.demo import autoresponder as ar

        res = ar.tick(db, secret=settings.pseudonym_secret.get_secret_value(), force=True)
        db.commit()
        out.update(res.as_dict())
    return out


@router.post(
    "/jury-mode",
    response_model=DemoStateOut,
    summary="Режим жюри",
    description=(
        "Включает или выключает «режим жюри» (RK2): кандидату доступна подсказка к текущему заданию, сессии, начатые в этом "
        "режиме, помечаются demo_assisted и исключаются из метрик. Нужен вход под любой ролью; на боевом стенде "
        "(VZ_ENV=prod) переключает только администратор или X-Demo-Key — подсказка при включённом режиме доступна владельцу сессии."
    ),
)
def jury_mode(body: JuryModeIn, _: ControlUser, db: DbSession) -> dict[str, Any]:
    jury.set_mode(db, body.enabled)
    db.commit()
    return state(db)


@router.get(
    "/jury/hint",
    response_model=JuryHintOut,
    summary="Подсказка жюри к заданию",
    description=(
        "Верный ответ на выданное задание сессии кандидата — только в режиме жюри и только владельцу сессии. Сессия сразу "
        "помечается demo_assisted. 409 — режим выключен или задание не выдано."
    ),
)
def jury_hint(
    user: CandidateUser,
    db: DbSession,
    session_id: Annotated[uuid.UUID, Query(description="Сессия теста")],
) -> dict[str, Any]:
    out = jury.hint(db, user, session_id)
    db.commit()
    return out


# --------------------------------------------------------------------------- вход жюри (/jury)


class JuryStartIn(BaseModel):
    mode: Literal["express", "full"] = Field(description="express — короткая версия (≈ 4 минуты), full — полный тест")
    specialization: str | None = Field(default=None, max_length=32, description="Код специализации (по умолчанию из config/demo.yaml)")
    grade: str | None = Field(default=None, max_length=16, description="Заявленный грейд (по умолчанию из config/demo.yaml)")


class JuryStartOut(TokenResponse):
    session_id: uuid.UUID = Field(description="Начатая сессия теста")
    mode: Literal["express", "full"]
    path: str = Field(description="Куда вести фронтенд: страница теста")


class JuryPersonaIn(BaseModel):
    persona: Literal["employer", "lab"] = Field(description="employer — HR демо-компании; lab — лаборатория методиста (жюри)")


class JuryPersonaOut(TokenResponse):
    persona: Literal["employer", "lab"]
    path: str = Field(description="Куда вести фронтенд")


class JuryOptionOut(BaseModel):
    code: str
    name: str


class JuryPathsOut(BaseModel):
    express_items_max: int = Field(description="Потолок заданий экспресс-теста")
    express_minutes: int = Field(description="Бюджет времени экспресса, минут (округление вверх)")
    specializations: list[JuryOptionOut] = Field(description="Специализации с готовым тестом")
    grades: list[JuryOptionOut]
    default_specialization: str
    default_grade: str
    employer_available: bool = Field(description="Демо-работодатель собран (seed выполнен)")
    lab_available: bool = Field(description="Аккаунт жюри для лаборатории собран")


@router.get(
    "/jury/paths",
    response_model=JuryPathsOut,
    summary="Что доступно на странице входа жюри",
    description="Открытый маршрут для страницы /jury: параметры экспресс-теста, специализации и грейды, собран ли демо-мир.",
)
def jury_paths(db: DbSession) -> dict[str, Any]:
    from app.services.demo import jury_entry as je
    from app.services.testing import labels as L
    from app.testing.cat import express_cat_config, load_express_config

    c = je.cfg()
    scale_grades = L.load_scale().grades
    specs = [s for s in L.taxonomy()["specializations"] if L.spec_testable(s["code"])]

    def exists(persona: str) -> bool:
        try:
            je.persona_user(db, persona)
            return True
        except ApiError:
            return False

    return {
        "express_items_max": express_cat_config().n_max,
        "express_minutes": -(-load_express_config().time_budget_s // 60),
        "specializations": [{"code": s["code"], "name": s["name"]} for s in specs],
        "grades": [{"code": g, "name": L.grade_name(g)} for g in scale_grades],
        "default_specialization": c["default_specialization"],
        "default_grade": c["default_grade"],
        "employer_available": exists("employer"),
        "lab_available": exists("lab"),
    }


@router.post(
    "/jury/start",
    response_model=JuryStartOut,
    status_code=201,
    summary="Пройти тест самому: одноразовый кандидат",
    description=(
        "Одной кнопкой: создаёт одноразового кандидата (без письма и пароля), заполняет опрос по выбранной специализации, "
        "ставит согласие на журналирование, начинает экспресс- или полный тест и входит (access-токен + refresh-cookie). "
        "Сессия помечена demo_assisted и в калибровку и метрики не входит. Открытый маршрут, лимит — как у регистрации (по IP). "
        "Экспресс: до 8 заданий, ≈ 4 минуты, тот же движок и банк, категория не присваивается."
    ),
)
def jury_start(
    body: JuryStartIn, response: Response, db: DbSession, settings: SettingsDep, limiter: LimiterDep, meta: MetaDep
) -> JuryStartOut:
    from app.api.auth import _token_response
    from app.core import auth_service
    from app.services.demo import jury_entry as je

    c = je.cfg()
    limiter.check("demo_jury_start", meta.ip or "unknown", je.start_rate())
    spec = body.specialization or str(c["default_specialization"])
    grade = body.grade or str(c["default_grade"])
    je.check_spec(spec)
    user = je.create_guest(db, settings, spec=spec, grade=grade)
    sid = je.start_for_guest(db, user, mode=body.mode, spec=spec, grade=grade, ip=meta.ip, ua=meta.user_agent)
    issued = auth_service.start_session(db, settings, user, meta)
    tr = _token_response(issued, response, settings)
    path = str(c["paths"]["candidate_express" if body.mode == "express" else "candidate_full"]).format(session_id=sid)
    return JuryStartOut(**tr.model_dump(), session_id=sid, mode=body.mode, path=path)


@router.post(
    "/jury/persona",
    response_model=JuryPersonaOut,
    summary="Войти как демо-работодатель или жюри лаборатории",
    description=(
        "Вход без пароля в существующий демо-аккаунт: HR демо-компании («Северный код») или «жюри» (методист, лаборатория). "
        "Пароли не читаются и не возвращаются; администратора здесь нет (его пароль — в backend/var/demo_accounts.md). "
        "404 — демо-мир не собран (`make seed`)."
    ),
)
def jury_persona(
    body: JuryPersonaIn, response: Response, db: DbSession, settings: SettingsDep, limiter: LimiterDep, meta: MetaDep
) -> JuryPersonaOut:
    from app.api.auth import _token_response
    from app.core import auth_service
    from app.services.demo import jury_entry as je

    limiter.check("demo_jury_persona", meta.ip or "unknown", settings.rate("login_ip"))
    user = je.persona_user(db, body.persona)
    issued = auth_service.start_session(db, settings, user, meta)
    tr = _token_response(issued, response, settings)
    path = str(je.cfg()["paths"][body.persona])
    return JuryPersonaOut(**tr.model_dump(), persona=body.persona, path=path)


@router.get(
    "/synthetic",
    response_model=SyntheticOut,
    summary="Какие кандидаты синтетические",
    description=(
        "Для метки «синтетический кандидат» в слепой карточке (Р6, G2): ссылки `ref` тех кандидатов компании, которых "
        "создал демо-мир и за которых отвечает автоответчик. Только роль «Нанимаю»; настоящих людей в списке нет."
    ),
)
def synthetic_refs(user: EmployerUser, db: DbSession) -> dict[str, Any]:
    refs = db.scalars(
        select(CandidateAlias.ref)
        .join(DemoPersona, DemoPersona.user_id == CandidateAlias.candidate_id)
        .where(CandidateAlias.company_id == user.company_id, DemoPersona.kind == "synthetic")
        .order_by(CandidateAlias.created_at)
    ).all()
    return {"label": str(section("autoresponder")["review_note"]), "refs": list(refs)}


# --------------------------------------------------------------------------- приёмник вебхуков


@router.get(
    "/webhook-sink",
    response_model=SinkOut,
    summary="Последние события вебхуков ATS",
    description=(
        "Последние 20 событий, которые демо-компании отправили на встроенный приёмник (вымышленный хост ATS), и прямые POST "
        "на этот же адрес: тип, id события, подпись и её проверка, тело. Открытый маршрут."
    ),
)
def webhook_sink_list(db: DbSession) -> dict[str, Any]:
    return {"url_path": section("webhook_sink")["path"], "host": sink.sink_host(), "items": sink.recent(db)}


@router.post(
    "/webhook-sink",
    response_model=SinkAckOut,
    summary="Принять вебхук ATS",
    description=(
        "Приёмник для внешней ATS жюри: принимает JSON с заголовками X-Vzaimno-Event, X-Vzaimno-Event-Id и X-Vzaimno-Signature "
        "(sha256=HMAC ключом компании), проверяет подпись и запоминает событие (последние 20). Всегда отвечает 200."
    ),
)
async def webhook_sink_receive(request: Request, db: DbSession) -> dict[str, Any]:
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    from app.services.contact import webhooks

    wc = webhooks.cfg().webhooks
    norm = {
        wc.event_header: headers.get(wc.event_header.lower(), ""),
        wc.event_id_header: headers.get(wc.event_id_header.lower(), ""),
        wc.signature_header: headers.get(wc.signature_header.lower(), ""),
    }
    entry = await run_in_threadpool(sink.record_direct, db, {k: v for k, v in norm.items() if v}, body)
    return {"ok": True, "event_id": entry.get("event_id"), "signature_valid": entry.get("signature_valid")}
