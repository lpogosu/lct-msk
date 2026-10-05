"""Профиль кандидата: ручная форма и резюме (К7), стандартизированный PDF-профиль (К8), приватность и поиск
с живым превью «так вас видят» и журналом согласий (К13), «Как меня видят» (К15).

Концепция §3.1 шаги 7, 8, 10, 16; §3.1.1–3.1.2; §4.5; DECISIONS п. 1, 20. Всё, что кандидат пишет о себе
или загружает в резюме, — «заявлено»: на категорию и место в подборке не влияет. Доступ — только к своему
профилю (маршруты /me); работодатель получает полный PDF только после раскрытия контактов.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Path, Query, Response, UploadFile, status

from app.api.deps import DbSession, LimiterDep, MetaDep, SettingsDep, require_roles
from app.core.clock import clock
from app.core.errors import ErrorResponse, error_responses, not_found
from app.llm.gateway import LLMGateway, get_gateway
from app.models.enums import UserRole
from app.models.users import User
from app.services.pdf import issue as pdf_issue
from app.services.pdf.schemas import ProfilePdfInfoOut
from app.services.profile import service as profiles
from app.services.profile.schemas import (
    CompanySearchOut,
    ConsentJournalOut,
    PreviewIn,
    PreviewOut,
    PrivacyOut,
    PrivacyUpdateIn,
    ProfileOut,
    ProfileUpdateIn,
    VisibilityStatsOut,
)
from app.services.profile.settings import get_profile_settings
from app.services.resume import flow as resume_flow
from app.services.resume.schemas import ResumeConfirmIn, ResumeDraftOut
from app.services.resume.settings import ResumeSettings

router = APIRouter(prefix="/candidates", tags=["Кандидат"], responses=error_responses())

CandidateUser = Annotated[User, Depends(require_roles(UserRole.candidate))]
EmployerUser = Annotated[User, Depends(require_roles(UserRole.employer))]
GatewayDep = Annotated[LLMGateway, Depends(get_gateway)]
VariantQ = Annotated[
    Literal["blind", "full"],
    Query(description="blind — слепая версия (псевдоним, без контактов; можно отправить самому), full — полная"),
]

_PDF_RESPONSES = {
    200: {
        "description": "PDF-файл профиля",
        "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
    }
}
_RESUME_ERRORS = {
    413: {"model": ErrorResponse, "description": "Файл больше 5 МБ (`resume_too_large`)"},
    415: {"model": ErrorResponse, "description": "Это не PDF (`resume_not_pdf`)"},
}


def _pdf_response(issued: pdf_issue.IssuedPdf) -> Response:
    return Response(
        content=issued.pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{issued.filename}"',
            "Cache-Control": "no-store",
            "X-Verify-Code": issued.code,
        },
    )


# --------------------------------------------------------------------------- профиль (К7)


@router.get(
    "/me",
    response_model=ProfileOut,
    summary="Мой профиль",
    description=(
        "Ручная часть профиля: ФИО и контакты (раскрываются только после взаимного «да»), город, заявленные "
        "навыки, роли, софт-скиллы, грейд «как заявлен», стаж, отрасли, состояние резюме и что ещё стоит заполнить. "
        "Всё это — «заявлено» и не влияет на категорию и Score. Только кандидат."
    ),
)
def get_me(user: CandidateUser, db: DbSession) -> ProfileOut:
    prof = profiles.get_profile_row(db, user)
    db.commit()
    return profiles.profile_out(db, user, prof, clock.now())


@router.put(
    "/me",
    response_model=ProfileOut,
    summary="Сохранить профиль",
    description=(
        "Ручная форма (К7). Передайте изменённые поля: не переданные не меняются, null — очистить. Навыки можно "
        "передать кодами или названиями («FastAPI», «PostgreSQL»): они нормализуются по справочнику; неизвестные — "
        "422 с перечнем. Телефон приводится к +7XXXXXXXXXX, Telegram — к @нику. Город из справочника сам "
        "определяет субъект РФ (его видит работодатель до раскрытия)."
    ),
)
def put_me(body: ProfileUpdateIn, user: CandidateUser, db: DbSession, meta: MetaDep) -> ProfileOut:
    now = clock.now()
    prof = profiles.update_profile(db, user, body, meta, now)
    return profiles.profile_out(db, user, prof, now)


# --------------------------------------------------------------------------- резюме (К7)


@router.post(
    "/me/resume",
    status_code=status.HTTP_201_CREATED,
    response_model=ResumeDraftOut,
    summary="Загрузить резюме (PDF)",
    description=(
        "PDF до 5 МБ с текстовым слоем. Разбор: разделы по заголовкам → email, телефон, Telegram, даты → ФИО "
        "(словари имён и окончаний) → навыки по справочнику платформы → роли и софт-скиллы → стаж по периодам "
        "без двойного счёта пересечений. Каждое поле приходит с цитатой (смещения в `text`) для подсветки. "
        "Языковая модель по умолчанию выключена; если включена — ПДн маскируются до вызова, а поле без "
        "проверенной цитаты отбрасывается. Результат — черновик: подтвердите поля через "
        "`POST /api/candidates/me/resume/confirm`. Ошибки: 422 `resume_no_text` (скан без текста — заполните "
        "вручную), `resume_broken`, `resume_protected`; 413 `resume_too_large`; 415 `resume_not_pdf`; 429."
    ),
    responses={**error_responses(), **_RESUME_ERRORS},
)
def upload_resume(
    user: CandidateUser,
    db: DbSession,
    meta: MetaDep,
    limiter: LimiterDep,
    gateway: GatewayDep,
    file: Annotated[UploadFile, File(description="Резюме в формате PDF, до 5 МБ")],
) -> ResumeDraftOut:
    s = ResumeSettings()
    limiter.check("resume_upload_user", str(user.id), s.rate("upload"))
    data = file.file.read(s.max_bytes + 1)
    return resume_flow.upload(db, user, data, now=clock.now(), meta=meta, gateway=gateway, settings=s)


@router.get(
    "/me/resume",
    response_model=ResumeDraftOut,
    summary="Черновик распознанного резюме",
    description="Распознанные поля с цитатами и текст резюме — пока кандидат их не подтвердил. 404 `resume_draft_not_found`.",
)
def get_resume_draft(user: CandidateUser, db: DbSession) -> ResumeDraftOut:
    return resume_flow.current_draft(db, user, clock.now())


@router.post(
    "/me/resume/confirm",
    response_model=ProfileOut,
    summary="Подтвердить поля из резюме",
    description=(
        "Кандидат принимает или исправляет каждое поле. Значение, совпавшее с распознанным, сохраняется с "
        "источником «резюме» и цитатой, исправленное — «вручную». Списки навыков, ролей и софт-скиллов — итоговые. "
        "Всё попадает в «заявлено»: серые чипы, стаж — подсказка при выборе грейда; в категорию и Score не идёт. "
        "404 `resume_draft_not_found`; 422 — неизвестные коды или неверный формат."
    ),
)
def confirm_resume(body: ResumeConfirmIn, user: CandidateUser, db: DbSession, meta: MetaDep) -> ProfileOut:
    now = clock.now()
    resume_flow.confirm(db, user, body, now=now, meta=meta)
    return profiles.profile_out(db, user, profiles.get_profile_row(db, user), now)


@router.delete(
    "/me/resume",
    response_model=ProfileOut,
    summary="Удалить резюме",
    description="Удаляет текст резюме, черновик и цитаты. Поля профиля, которые вы уже подтвердили, остаются.",
)
def delete_resume(user: CandidateUser, db: DbSession, meta: MetaDep) -> ProfileOut:
    resume_flow.delete(db, user, meta=meta)
    return profiles.profile_out(db, user, profiles.get_profile_row(db, user), clock.now())


# --------------------------------------------------------------------------- PDF-профиль (К8)


@router.get(
    "/me/profile.pdf",
    response_class=Response,
    summary="Скачать PDF-профиль",
    description=(
        "Стандартизированный PDF-профиль в стиле ФСП: категория и «линейка» 300–600 с интервалом, модули ✓/✓✓, "
        "заявленные навыки серым, ФСП, последние задачи, актуальность, QR и ссылка на страницу сверки /v/{код}. "
        "`variant=blind` — псевдоним вместо имени, без контактов, ФСП обобщённо (можно отправить самому); "
        "`variant=full` — ФИО, контакты, точный балл, опыт, выписка ФСП с номером ЕКП. Код сверки связывает "
        "содержимое листа и дату выдачи: изменённый PDF сверку не пройдёт. 429 — не чаще 30 раз за 10 минут."
    ),
    responses={**error_responses(), **_PDF_RESPONSES},
)
def my_profile_pdf(
    user: CandidateUser, db: DbSession, settings: SettingsDep, limiter: LimiterDep, variant: VariantQ = "blind"
) -> Response:
    limiter.check("profile_pdf_user", str(user.id), get_profile_settings().rate("pdf"))
    profiles.get_profile_row(db, user)
    return _pdf_response(pdf_issue.issue(db, user, variant, clock.now(), settings))


@router.get(
    "/me/profile-pdf",
    response_model=ProfilePdfInfoOut,
    summary="Ссылка сверки PDF-профиля",
    description=(
        "Код сверки и ссылка /v/{код} для выбранной версии (экран К8: превью, QR, «скопировать ссылку»). "
        "Содержимое и код — те же, что у PDF, выданного сегодня."
    ),
)
def my_profile_pdf_info(
    user: CandidateUser, db: DbSession, settings: SettingsDep, limiter: LimiterDep, variant: VariantQ = "blind"
) -> ProfilePdfInfoOut:
    limiter.check("profile_pdf_user", str(user.id), get_profile_settings().rate("pdf"))
    profiles.get_profile_row(db, user)
    issued = pdf_issue.issue(db, user, variant, clock.now(), settings, render=False)
    if variant == "blind":
        includes = ["Псевдоним", "Категория и линейка с интервалом", "Модули теста", "Субъект РФ", "ФСП обобщённо", "Задачи", "Заявленные навыки"]
        hidden = ["ФИО", "Контакты", "Город", "Компании и вуз", "Точный балл", "Номер ЕКП и место"]
    else:
        includes = ["ФИО и контакты", "Категория, точный балл и интервал", "Модули теста", "Опыт и образование", "Выписка ФСП с ЕКП", "Задачи", "Заявленные навыки"]
        hidden = []
    return ProfilePdfInfoOut(
        variant=variant,
        code=issued.code,
        verify_url=issued.url,
        issued_on=issued.sheet.issued_on,
        download_url=f"/api/candidates/me/profile.pdf?variant={variant}",
        includes=includes,
        hidden=hidden,
    )


@router.get(
    "/disclosed/{ref}/profile.pdf",
    response_class=Response,
    summary="Полный PDF-профиль кандидата для работодателя",
    description=(
        "Только для работодателя и только после взаимного «да»: при действующем согласии кандидата на раскрытие "
        "контактов вашей компании. В документ попадают лишь поля, которые кандидат разрешил (ФИО, контакты, "
        "текущий работодатель, вуз). `ref` — непрозрачная ссылка на кандидата из подборки или приглашения. "
        "Без раскрытия — 404 (неотличимо от несуществующего кандидата)."
    ),
    responses={**error_responses(), **_PDF_RESPONSES},
)
def employer_profile_pdf(
    user: EmployerUser,
    db: DbSession,
    settings: SettingsDep,
    limiter: LimiterDep,
    ref: Annotated[str, Path(min_length=4, max_length=64, description="Ссылка на кандидата в рамках компании")],
) -> Response:
    limiter.check("profile_pdf_employer", str(user.id), get_profile_settings().rate("pdf"))
    if user.company_id is None:
        raise not_found("profile_not_disclosed", "Профиль недоступен: кандидат не раскрывал контакты вашей компании")
    candidate_id = None
    try:
        from app.services.matching import resolve_ref

        candidate_id = resolve_ref(db, user.company_id, ref)
    except ImportError:  # pragma: no cover — модуль подбора не собран
        candidate_id = None
    if candidate_id is None:
        raise not_found("profile_not_disclosed", "Профиль недоступен: кандидат не раскрывал контакты вашей компании")
    issued = pdf_issue.employer_profile_pdf(
        db, company_id=user.company_id, candidate_id=candidate_id, settings=settings, now=clock.now()
    )
    return _pdf_response(issued)


# --------------------------------------------------------------------------- приватность и поиск (К13)


@router.get(
    "/me/privacy",
    response_model=PrivacyOut,
    summary="Приватность и режим поиска",
    description=(
        "Режим поиска (активно ищу / открыт / не ищу), минимум зарплаты и его видимость (по умолчанию работодатель "
        "видит только значок «в вилке ✓ / выше»), лимит новых приглашений в неделю 3/5/10/∞ (по умолчанию 5), "
        "форматы, регион и город, блок-лист компаний, какие поля компания увидит после взаимного «да», "
        "видны ли вы в банке и почему нет."
    ),
)
def get_privacy(user: CandidateUser, db: DbSession) -> PrivacyOut:
    prof = profiles.get_profile_row(db, user)
    db.commit()
    return profiles.privacy_out(db, user, prof, clock.now())


@router.put(
    "/me/privacy",
    response_model=PrivacyOut,
    summary="Сохранить настройки приватности и поиска",
    description=(
        "Передайте изменённые поля; не переданные не меняются. `weekly_invite_limit`: 3, 5, 10 или null (без "
        "ограничений). `blocked_company_ids` — эти компании вас не видят. `field_visibility` — что раскрывать после "
        "«да»; хотя бы один способ связи обязателен (400 `contact_field_required`). Минимум зарплаты — с шагом 1000 ₽."
    ),
)
def put_privacy(body: PrivacyUpdateIn, user: CandidateUser, db: DbSession, meta: MetaDep) -> PrivacyOut:
    now = clock.now()
    prof = profiles.update_privacy(db, user, body, meta, now)
    return profiles.privacy_out(db, user, prof, now)


@router.get(
    "/me/preview",
    response_model=PreviewOut,
    summary="Так вас видят",
    description=(
        "Живое превью: слепая карточка (как работодатель видит вас до взаимного «да» — псевдоним, категория, "
        "навыки с источниками, полоса теста, обобщённое ФСП, регион, значок зарплаты, без ПДн) и раскрытая "
        "(после «да» и согласия — с полями, которые вы разрешили). Та же матрица видимости, что и в API работодателя."
    ),
)
def get_preview(user: CandidateUser, db: DbSession, settings: SettingsDep, limiter: LimiterDep) -> PreviewOut:
    limiter.check("profile_preview_user", str(user.id), get_profile_settings().rate("preview"))
    out = profiles.preview(db, user, settings, PreviewIn(), clock.now())
    db.commit()
    return out


@router.post(
    "/me/preview",
    response_model=PreviewOut,
    summary="Так вас видят — с черновиком настроек",
    description=(
        "То же превью, но с несохранёнными настройками К13 (`privacy`) — экран показывает результат до нажатия "
        "«Сохранить». `offer_salary_to` — верх вилки примерного приглашения, чтобы увидеть значок «в вилке ✓ / выше». "
        "Ничего не сохраняет."
    ),
)
def post_preview(
    body: PreviewIn, user: CandidateUser, db: DbSession, settings: SettingsDep, limiter: LimiterDep
) -> PreviewOut:
    limiter.check("profile_preview_user", str(user.id), get_profile_settings().rate("preview"))
    out = profiles.preview(db, user, settings, body, clock.now())
    db.commit()  # черновик настроек не пишется в профиль: превью строится на копии
    return out


@router.get(
    "/me/blocklist/search",
    response_model=list[CompanySearchOut],
    summary="Найти компанию для блок-листа",
    description="Поиск компаний по названию (от 2 символов, до 10 результатов) — чтобы добавить их в блок-лист К13.",
)
def search_companies(
    user: CandidateUser,
    db: DbSession,
    limiter: LimiterDep,
    q: Annotated[str, Query(min_length=2, max_length=100, description="Часть названия компании")],
) -> list[CompanySearchOut]:
    limiter.check("profile_search_user", str(user.id), get_profile_settings().rate("search"))
    return profiles.company_search(db, user, q)


@router.get(
    "/me/consents",
    response_model=list[ConsentJournalOut],
    summary="Журнал согласий",
    description=(
        "Все ваши согласия с названиями документов, версиями текста и тем, что произойдёт при отзыве. "
        "Отозвать — `POST /api/consents/{id}/revoke` (запись остаётся в журнале с датой отзыва)."
    ),
)
def my_consents(user: CandidateUser, db: DbSession) -> list[ConsentJournalOut]:
    return profiles.consent_journal(db, user, clock.now())


@router.get(
    "/me/visibility-stats",
    response_model=VisibilityStatsOut,
    summary="Как меня видят",
    description=(
        "За 7 дней: показы в подборках, в скольких — в топ-10, медианная позиция, сколько компаний открыли "
        "карточку, приглашения, навык, которого чаще всего не хватает (со ссылкой на задачу или тест), причины "
        "отказов компаний. При < 5 показах — «данных пока мало»."
    ),
)
def visibility_stats(user: CandidateUser, db: DbSession) -> VisibilityStatsOut:
    return profiles.visibility_stats(db, user, clock.now())
