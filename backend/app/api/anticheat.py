"""«Антиподсказчик»: «Подтверждение за 5 минут» для работодателя и песочница жюри «Попробуй списать».

- Комплект работодателю — только после раскрытия контакта (активная ContactDisclosure пары «кандидат — компания»); без
  улик, флагов и причин выбора заданий (правило видимости M5). Методист получает комплект в лаборатории
  (`GET /api/lab/sessions/{id}/verification-kit`, с причинами выбора).
- Песочница жюри — публичные маршруты без входа, только при DEMO_MODE (иначе 404 в общем формате). Мини-тест из 4 заданий
  с двойниками, уникальный для каждого проверяющего; сессия demo_assisted, в метрики не входит. Ключей до отправки ответов
  клиент не получает. Лента сверок в лаборатории показывает и пойманных в песочнице.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI

from app.api.demo import require_demo_mode
from app.api.deps import DbSession, LimiterDep, MetaDep, require_roles
from app.core.audit import audit
from app.core.errors import error_responses, not_found
from app.models.contact import Invitation
from app.models.enums import UserRole
from app.models.users import User
from app.services.anticheat import kit as anticheat_kit
from app.services.anticheat import sandbox
from app.services.anticheat.cfg import runtime_config
from app.services.anticheat.schemas import (
    SandboxAnswersIn,
    SandboxResultOut,
    SandboxStartOut,
    VerificationKitOut,
)
from app.services.contact.company import company_of

log = logging.getLogger("vzaimno.anticheat")


def _warm_sandbox() -> None:
    try:
        sandbox.warm_bank()
    except Exception:  # noqa: BLE001 — прогрев не должен ронять сервер: мини-тест соберётся на месте
        log.exception("Прогрев запаса песочницы не удался")


@asynccontextmanager
async def anticheat_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """DEMO_MODE: в фоне собрать запас заданий песочницы с двойниками (у части семейств двойник считается десятки секунд),
    чтобы мини-тест «Попробуй списать» открывался сразу. Тесты платформы отключают это флагом app.state.demo_loop = False
    (как и фоновый цикл демо). Поток демон: остановке сервера он не мешает."""
    sandbox.AUTO_TOP_UP = bool(app.state.settings.demo_mode and getattr(app.state, "demo_loop", True))
    if app.state.settings.demo_mode and getattr(app.state, "demo_loop", True):
        threading.Thread(target=_warm_sandbox, name="sandbox-warm", daemon=True).start()
    yield


router = APIRouter(tags=["Антиподсказчик"], responses=error_responses(), lifespan=anticheat_lifespan)

EmployerUser = Annotated[User, Depends(require_roles(UserRole.employer))]
_RC = runtime_config()


@router.get(
    "/invitations/{invitation_id}/verification-kit",
    response_model=VerificationKitOut,
    summary="«Подтверждение за 5 минут» для работодателя",
    description=(
        "Три вопроса по собственным заданиям кандидата: в каждом изменён один параметр, ожидаемый ответ вычислен "
        "исполнением, к вопросу — рубрика 0–2 и подсказка «что слушать»; до двух шаблонных вопросов по must-навыкам, которых "
        "тест не покрывает (с пометкой «не откалибровано»). Доступно только после взаимного «да», когда контакты кандидата "
        "раскрыты (иначе 409 `kit_not_available`). Улик, флагов и причин выбора заданий в комплекте нет."
    ),
)
def employer_verification_kit(invitation_id: uuid.UUID, user: EmployerUser, db: DbSession) -> VerificationKitOut:
    company = company_of(db, user)
    inv = db.get(Invitation, invitation_id)
    if inv is None or inv.company_id != company.id:  # чужое приглашение неотличимо от несуществующего
        raise not_found("invitation_not_found", "Приглашение не найдено")
    kit, _session = anticheat_kit.employer_kit(db, inv)
    audit(
        db,
        "anticheat.kit_viewed",
        actor_id=user.id,
        actor_role=user.role,
        entity_type="invitation",
        entity_id=inv.id,
        kit_id=kit.kit_id,
    )
    db.commit()
    return VerificationKitOut.model_validate(kit.to_dict("employer"))


# ---------------------------------------------------------------------------
# Песочница жюри (DEMO_MODE)
# ---------------------------------------------------------------------------

jury = APIRouter(prefix="/jury/cheat-lab", dependencies=[Depends(require_demo_mode)])


@jury.post(
    "",
    status_code=201,
    response_model=SandboxStartOut,
    summary="Песочница жюри: начать мини-тест",
    description=(
        "Публичный маршрут (без входа), только при DEMO_MODE. Собирает мини-тест из 4 заданий разных семейств; у каждого есть "
        "«двойник в буфере». Набор уникален для проверяющего. В задании — условие и `clip`: что обработчик копирования "
        "кладёт в буфер. Ключей нет. Сессия помечена demo_assisted и исключена из метрик. Поведение копирования на "
        "мобильных не проверено — проверяйте с ноутбука."
    ),
)
def sandbox_start(db: DbSession, limiter: LimiterDep, meta: MetaDep) -> SandboxStartOut:
    limiter.check("jury_cheat_start", meta.ip or "-", _RC.sandbox_start_rate)
    out = sandbox.start(db)
    db.commit()
    return SandboxStartOut.model_validate(out)


@jury.post(
    "/{test_id}/answers",
    response_model=SandboxResultOut,
    summary="Песочница жюри: отправить ответы",
    description=(
        "Диагноз по каждому заданию: «вы копировали текст — пойман двойником» (ответ совпал с ключом копии) либо «ответ "
        "верный — скриншот и ручной ввод двойник не ловит, поэтому подтверждение за 5 минут»; для пойманного — акт сверки. "
        "Ответы принимаются один раз."
    ),
)
def sandbox_answers(test_id: str, body: SandboxAnswersIn, db: DbSession, limiter: LimiterDep, meta: MetaDep) -> SandboxResultOut:
    limiter.check("jury_cheat_score", meta.ip or "-", _RC.sandbox_score_rate)
    out = sandbox.score(db, test_id, body.answers, body.copied)
    audit(db, "anticheat.sandbox_scored", actor_id=None, actor_role=None, entity_type="sandbox", entity_id=None, test_id=test_id)
    db.commit()
    return SandboxResultOut.model_validate(out)


@jury.get(
    "/{test_id}/result",
    response_model=SandboxResultOut,
    summary="Песочница жюри: результат",
    description="Результат уже отправленного мини-теста (409 `sandbox_not_scored`, пока ответов нет).",
)
def sandbox_result(test_id: str, db: DbSession) -> SandboxResultOut:
    return SandboxResultOut.model_validate(sandbox.result(db, test_id))


router.include_router(jury)
