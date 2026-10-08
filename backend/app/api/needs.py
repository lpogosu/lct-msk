"""Раздел «Потребности и подборка» (концепция §3.2 шаги 3–7, §4.1–4.5, §5.5; spec-contact §2.1, §3.1–3.6).

Бриф с обязательной вилкой → разбор на правилах (чипы с источниками и цитатами, один уточняющий вопрос) →
рекомендованные категории и охват вилки с защитой приватности → «Как мы проверим» → неизменяемые версии
подборки v1, v2, … (неразрушающие фильтры, «Скрыто фильтрами (N)», бейджи diff, откат) → шортлист 📌 с жёлтой
меткой → слепая карточка Р6 (открытие записывается: условие приглашения, DECISIONS п. 7).

Только роль «Нанимаю» и только брифы своей компании: чужой бриф неотличим от несуществующего (404).
Кандидат во всех ответах — псевдоним «К-4821» и непрозрачная ссылка `ref` в рамках компании (DECISIONS п. 20).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import DbSession, LimiterDep, MailerDep, SettingsDep, require_roles
from app.contact.settings import get_contact_config
from app.core.errors import ApiError, error_responses
from app.core.rate_limit import RateLimiter
from app.core.settings import RateRule
from app.matching.scoring import ScoringConfig
from app.models.enums import UserRole
from app.models.matching import Need
from app.models.users import User
from app.services.matching import needs as svc
from app.services.matching import privacy
from app.services.matching.schemas import (
    BriefParseIn,
    CandidateCardOut,
    CategoriesOut,
    CheckOut,
    MarketSalaryOut,
    NeedCreateIn,
    NeedListOut,
    NeedOut,
    NeedPatchIn,
    NeedPatchOut,
    NeighborOut,
    ParseOut,
    PinIn,
    RollbackIn,
    ShortlistOut,
    VersionCreateIn,
    VersionListOut,
    VersionViewOut,
)

router = APIRouter(prefix="/needs", tags=["Потребности и подборка"], responses=error_responses())

Employer = Annotated[User, Depends(require_roles(UserRole.employer))]
NeedId = Annotated[uuid.UUID, Path(description="Идентификатор брифа")]
Ref = Annotated[str, Path(min_length=4, max_length=32, description="Непрозрачная ссылка на кандидата (поле ref)")]
VersionN = Annotated[int, Path(ge=1, le=32767, description="Номер версии подборки")]
Tab = Annotated[
    Literal["overall", "category"], Query(description="Вкладка: overall — «Общий топ», category — «По категориям»")
]
CategoryQ = Annotated[
    str | None, Query(max_length=64, description="Категория вкладки «По категориям»: spec:grade, например backend_python:middle")
]
PageQ = Annotated[int, Query(ge=1, le=100, description="Страница (по 10 строк)")]
HiddenPageQ = Annotated[int, Query(ge=1, le=100, description="Страница группы «Скрыто фильтрами»")]
CompareQ = Annotated[int | None, Query(ge=1, le=32767, description="Сравнить с версией n (бейджи diff); по умолчанию — с родительской")]


def _secret(settings: SettingsDep) -> str:
    return settings.pseudonym_secret.get_secret_value()


Secret = Annotated[str, Depends(_secret)]


def _limit(limiter: RateLimiter, scope: str, user: User) -> None:
    """Защита от перебора (вычисление минимумов подбором вилки, выкачивание подборок): N запросов в минуту."""
    per_minute = int(get_contact_config().quotas.rate_limit_per_minute)
    limiter.check(f"matching_{scope}", str(user.id), RateRule(per_minute, 60))


def _brief_input(body: BriefParseIn | NeedCreateIn) -> svc.BriefInput:
    return svc.BriefInput(
        text=body.text,
        salary_from=body.salary_from,
        salary_to=body.salary_to,
        salary_net=body.salary_net,
        spec=body.spec,
        grade=body.grade,
        must=body.must,
        nice=body.nice,
        formats=list(body.formats) if body.formats is not None else None,
        city=body.city,
        clear_city=body.clear_city,
        team=team.model_dump() if (team := getattr(body, "team", None)) is not None else None,
    )


def _need(db: DbSession, user: User, need_id: uuid.UUID) -> Need:
    return svc.get_need(db, user, need_id)


def _category(raw: str | None) -> tuple[str, str] | None:
    if not raw:
        return None
    spec, _, grade = raw.partition(":")
    if not spec or not grade:
        raise ApiError(422, "category_invalid", "Категория указывается как «специализация:грейд»")
    return spec, grade


def _view(db: DbSession, need: Need, nv, secret: str, tab: str, category: str | None, page: int, hidden_page: int, compare_to: int | None) -> dict:
    return svc.version_view(
        db,
        need,
        nv,
        secret=secret,
        tab=tab,
        category=_category(category),
        page_no=page,
        hidden_page=hidden_page,
        compare_to=compare_to,
    )


# --------------------------------------------------------------------------- бриф


@router.post(
    "/parse",
    response_model=ParseOut,
    summary="Разобрать бриф (живая карточка)",
    description=(
        "Детерминированный разбор на правилах (≤ 300 мс): вилка, навыки must/nice с источником и цитатой, "
        "вероятности специализации и грейда, формат и город, не больше одного уточняющего вопроса, проверка вилки "
        "(DECISIONS п. 6: 1,6–2,0× — предупреждение, > 2,0× — блок) и рекомендованные категории. Ничего не сохраняет. "
        "Правки работодателя (spec, grade, must, nice, formats, city) применяются поверх разбора с источником «вручную». "
        "Если включён шлюз LLM (по умолчанию выключен), он только добавляет пропущенные правилами навыки — с цитатой, "
        "найденной в тексте, и источником «YandexGPT» / «GigaChat»; в ранжировании LLM не участвует. "
        "422 `brief_invalid` — недопустимая правка (например, больше 5 обязательных навыков)."
    ),
)
def parse_brief(body: BriefParseIn, user: Employer, limiter: LimiterDep) -> ParseOut:
    _limit(limiter, "parse", user)
    return ParseOut.model_validate(svc.parse_preview(_brief_input(body)))


@router.get(
    "/market-salary",
    response_model=MarketSalaryOut,
    summary="Рыночная вилка для направления и грейда",
    description=(
        "Подсказка в форме брифа: квантили зарплат (p10–p90) по открытым данным вакансий «Работа в России» для "
        "направления × грейда (если ячейка тонкая — по направлению целиком), в двух основаниях: `gross` — до НДФЛ "
        "(так указано в источнике) и `net` — на руки. Выборка региональная и занижена относительно Москвы — "
        "см. `note`. Если передать `salary_to` и `salary_net`, вернётся отношение верхней границы вилки к нижнему "
        "квартилю рынка. Только подсказка: в Score и подбор не входит, вилку не блокирует."
    ),
)
def market_salary(
    user: Employer,
    specialization: Annotated[str, Query(min_length=2, max_length=32, description="Код специализации, например backend_python")],
    grade: Annotated[Literal["intern", "junior", "middle", "senior"] | None, Query(description="Грейд")] = None,
    salary_to: Annotated[int | None, Query(ge=0, le=10_000_000, description="Верхняя граница вилки, ₽/мес")] = None,
    salary_net: Annotated[bool, Query(description="Вилка указана на руки (true) или до НДФЛ (false)")] = True,
) -> MarketSalaryOut:
    return MarketSalaryOut.model_validate(svc.market_salary(specialization, grade, salary_to, salary_net))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=NeedOut,
    summary="Создать бриф",
    description=(
        "Сохраняет бриф с обязательной вилкой (₽/мес, от ≥ 30 000, шаг 1000). Вилка шире 2,0× — 422 `salary_blocked`; "
        "1,6–2,0× — сохраняется с предупреждением `salary_wide` в `warnings`; без вилки — 422 `salary_missing` / "
        "`salary_incomplete`; некорректная — 422 `salary_invalid`. Из рекомендованных категорий по умолчанию выбрана "
        "основная (если она пока не тестируется — первая тестируемая); предложенные и смежная включаются вручную "
        "(POST …/versions с categories). 409 `company_required` — у аккаунта нет компании."
    ),
)
def create_need(body: NeedCreateIn, user: Employer, db: DbSession, limiter: LimiterDep) -> NeedOut:
    _limit(limiter, "write", user)
    need = svc.create_need(db, user, _brief_input(body), title=body.title, is_internship=body.is_internship)
    out = NeedOut.model_validate(svc.need_detail(db, need))
    db.commit()
    return out


@router.get(
    "",
    response_model=NeedListOut,
    summary="Мои брифы",
    description="Брифы компании текущего работодателя, новые сверху. Фильтр по статусу — необязательный.",
)
def list_needs(
    user: Employer,
    db: DbSession,
    status_filter: Annotated[
        Literal["draft", "parsed", "active", "paused", "closed"] | None,
        Query(alias="status", description="Статус брифа"),
    ] = None,
) -> NeedListOut:
    return NeedListOut.model_validate({"items": svc.list_needs(db, user, status_filter)})


@router.get(
    "/{need_id}",
    response_model=NeedOut,
    summary="Бриф",
    description="Бриф с разбором, правками, предупреждениями и выбором категорий. Чужой бриф — 404.",
)
def get_need(need_id: NeedId, user: Employer, db: DbSession) -> NeedOut:
    return NeedOut.model_validate(svc.need_detail(db, _need(db, user, need_id)))


@router.patch(
    "/{need_id}",
    response_model=NeedPatchOut,
    summary="Изменить бриф",
    description=(
        "Правка текста, вилки, навыков, формата, города, названия, флага стажировки или статуса. Если подборка уже "
        "собиралась и бриф поменялся — создаётся новая неизменяемая версия подборки (прежние доступны для отката). "
        "status=closed отзывает ждущие ответа приглашения «позиция закрыта». Проверки вилки — как при создании. "
        "409 `need_closed` — бриф уже закрыт."
    ),
)
def patch_need(
    need_id: NeedId,
    body: NeedPatchIn,
    user: Employer,
    db: DbSession,
    limiter: LimiterDep,
    secret: Secret,
    mailer: MailerDep,
) -> NeedPatchOut:
    _limit(limiter, "write", user)
    need = _need(db, user, need_id)
    inp = svc.patch_input(
        need,
        text=body.text,
        salary_from=body.salary_from,
        salary_to=body.salary_to,
        salary_net=body.salary_net,
        spec=body.spec,
        grade=body.grade,
        must=body.must,
        nice=body.nice,
        formats=list(body.formats) if body.formats is not None else None,
        city=body.city,
        clear_city=body.clear_city,
        team=body.team.model_dump() if body.team is not None else None,
    )
    res = svc.update_need(
        db,
        user,
        need,
        inp,
        title=body.title,
        is_internship=body.is_internship,
        status=body.status,
        close_reason=body.close_reason,
        secret=secret,
        mailer=mailer,
    )
    out = NeedPatchOut.model_validate(
        {
            "need": svc.need_detail(db, need),
            "version": svc.version_meta(res.version, need) if res.version is not None else None,
            "withdrawn_invitations": res.withdrawn,
        }
    )
    if res.finish is not None:
        res.finish.finish()  # commit + вебхуки ATS об отзыве приглашений
    else:
        db.commit()
    return out


# --------------------------------------------------------------------------- категории и «как мы проверим»


@router.get(
    "/{need_id}/categories",
    response_model=CategoriesOut,
    summary="Категории и охват вилки",
    description=(
        "Р4: 1–3 рекомендованные категории (P ≥ 0,10) и смежная при N основной < 15; для каждой — N, ползунок "
        "«верх вилки → охват» (показывается при N ≥ 10, приросты — ступенями не меньше 3 человек, чтобы нельзя было "
        "вычислить минимум отдельного кандидата), P25–P75 ожиданий с шагом 10 тыс., доля с ФСП и доля активных."
    ),
)
def need_categories(need_id: NeedId, user: Employer, db: DbSession, limiter: LimiterDep) -> CategoriesOut:
    _limit(limiter, "read", user)
    out = CategoriesOut.model_validate(svc.categories_view(db, _need(db, user, need_id)))
    db.commit()
    return out


@router.get(
    "/{need_id}/check",
    response_model=CheckOut,
    summary="Как мы проверим",
    description=(
        "Покрытие навыков брифа модулями стандартного теста категории (он под вакансию не меняется), серый список "
        "«тестом не проверяется — проверьте на интервью», веса модулей в компоненте «стек» и план проверочного "
        "набора по вакансии: обязательные навыки → модули → 3–5 откалиброванных семейств, не дольше 15 минут."
    ),
)
def need_check(need_id: NeedId, user: Employer, db: DbSession) -> CheckOut:
    return CheckOut.model_validate(svc.check_view(_need(db, user, need_id)))


# --------------------------------------------------------------------------- версии подборки


@router.post(
    "/{need_id}/versions",
    status_code=status.HTTP_201_CREATED,
    response_model=VersionViewOut,
    summary="Собрать новую версию подборки",
    description=(
        "Создаёт неизменяемую версию v(n+1) от текущей: незаданные параметры (фильтры, пресет, категории) "
        "наследуются; пустое тело — «пересчитать на свежих данных». Фильтр никого не удаляет: отфильтрованные "
        "уходят в «Скрыто фильтрами (N)» с причиной. В ответе первая страница «Общего топа»: Score с полосой из 5 "
        "вкладов, 3 «за» и 1 «против» со ссылками на доказательства, бейджи `новый` / `▲3` / `▼2` / `скрыт` "
        "относительно предыдущей версии, шортлист с жёлтой меткой. 422 `filter_invalid`, `preset_invalid`, "
        "`categories_empty`; 409 `need_closed`."
    ),
)
def create_version(
    need_id: NeedId, body: VersionCreateIn, user: Employer, db: DbSession, limiter: LimiterDep, secret: Secret
) -> VersionViewOut:
    _limit(limiter, "write", user)
    need = _need(db, user, need_id)
    nv = svc.create_version(
        db,
        user,
        need,
        secret=secret,
        filters=[f.model_dump() for f in body.filters] if body.filters is not None else None,
        preset=body.preset,
        categories=[(c.spec, c.grade) for c in body.categories] if body.categories is not None else None,
    )
    out = VersionViewOut.model_validate(svc.version_view(db, need, nv, secret=secret))
    db.commit()
    return out


@router.get(
    "/{need_id}/versions",
    response_model=VersionListOut,
    summary="Версии подборки",
    description="Цепочка версий брифа v1 → v2 → … с фильтрами, пресетом и счётчиками; какие доступны для отката.",
)
def list_versions(need_id: NeedId, user: Employer, db: DbSession) -> VersionListOut:
    need = _need(db, user, need_id)
    versions = svc.list_versions(db, need)
    cur = svc.current_version(db, need)
    depth = int(ScoringConfig.load().section("selection")["rollback_depth"])
    return VersionListOut.model_validate(
        {
            "items": [svc.version_meta(v, need) for v in versions],
            "current": cur.n if cur else None,
            "rollback_targets": [v.n for v in versions][-depth:],
        }
    )


@router.get(
    "/{need_id}/selection",
    response_model=VersionViewOut,
    summary="Текущая подборка",
    description=(
        "Р5: текущая версия подборки — страница «Общего топа» или вкладки категории (≤ 50 на категорию, по 10 "
        "строк), «Скрыто фильтрами (N)», шортлист с жёлтой меткой, бейджи diff. Кандидат, который после сборки "
        "отозвал согласие на показ, ушёл в «не ищу» или заблокировал компанию, из версии сразу пропадает (места "
        "остальных не пересчитываются; в шортлисте — строка без данных с жёлтой меткой). 404 `selection_not_built` — "
        "подборку ещё не собирали."
    ),
)
def current_selection(
    need_id: NeedId,
    user: Employer,
    db: DbSession,
    limiter: LimiterDep,
    secret: Secret,
    tab: Tab = "overall",
    category: CategoryQ = None,
    page: PageQ = 1,
    hidden_page: HiddenPageQ = 1,
    compare_to: CompareQ = None,
) -> VersionViewOut:
    _limit(limiter, "read", user)
    need = _need(db, user, need_id)
    nv = svc.current_version(db, need)
    if nv is None:
        raise ApiError(404, "selection_not_built", "Подборку по этому брифу ещё не собирали")
    out = VersionViewOut.model_validate(_view(db, need, nv, secret, tab, category, page, hidden_page, compare_to))
    db.commit()
    return out


@router.get(
    "/{need_id}/versions/{n}",
    response_model=VersionViewOut,
    summary="Версия подборки",
    description=(
        "Любая версия брифа — как была собрана (без пересчёта), кроме кандидатов, которые с тех пор отозвали "
        "согласие на показ, ушли в «не ищу» или заблокировали компанию. Параметры страниц и сравнения — как у текущей."
    ),
)
def get_version(
    need_id: NeedId,
    n: VersionN,
    user: Employer,
    db: DbSession,
    limiter: LimiterDep,
    secret: Secret,
    tab: Tab = "overall",
    category: CategoryQ = None,
    page: PageQ = 1,
    hidden_page: HiddenPageQ = 1,
    compare_to: CompareQ = None,
) -> VersionViewOut:
    _limit(limiter, "read", user)
    need = _need(db, user, need_id)
    nv = svc.get_version(db, need, n)
    out = VersionViewOut.model_validate(_view(db, need, nv, secret, tab, category, page, hidden_page, compare_to))
    db.commit()
    return out


@router.post(
    "/{need_id}/rollback",
    response_model=VersionViewOut,
    summary="Откатиться к версии",
    description=(
        "Перенос указателя текущей версии без пересчёта на одну из последних 10 версий; бриф (текст, вилка, "
        "навыки, категории, флаг стажировки и живая карточка разбора) возвращается к снимку этой версии. "
        "Шортлист не меняется. 409 `rollback_too_old`; 409 `need_closed` — бриф закрыт."
    ),
)
def rollback(
    need_id: NeedId, body: RollbackIn, user: Employer, db: DbSession, limiter: LimiterDep, secret: Secret
) -> VersionViewOut:
    _limit(limiter, "write", user)
    need = _need(db, user, need_id)
    nv = svc.rollback(db, need, body.n)
    out = VersionViewOut.model_validate(svc.version_view(db, need, nv, secret=secret))
    db.commit()
    return out


@router.get(
    "/{need_id}/versions/{n}/neighbors/{rank}",
    response_model=NeighborOut,
    summary="Почему выше соседа",
    description="Для пары #rank и #rank+1 «Общего топа»: разница баллов, главный плюс и главный минус верхнего.",
)
def neighbor(
    need_id: NeedId,
    n: VersionN,
    rank: Annotated[int, Path(ge=1, le=500, description="Место верхнего в «Общем топе»")],
    user: Employer,
    db: DbSession,
) -> NeighborOut:
    need = _need(db, user, need_id)
    return NeighborOut.model_validate(svc.neighbor_view(svc.get_version(db, need, n), rank))


# --------------------------------------------------------------------------- шортлист и карточка


def _shortlist(db: DbSession, need: Need, secret: str) -> ShortlistOut:
    limit = int(ScoringConfig.load().section("selection")["shortlist_max"])
    return ShortlistOut.model_validate({"items": svc.shortlist_out(db, need, secret=secret), "limit": limit})


@router.get(
    "/{need_id}/shortlist",
    response_model=ShortlistOut,
    summary="Шортлист брифа",
    description=(
        "Закреплённые кандидаты (≤ 30) — живут отдельно от версий. Кандидат, не прошедший фильтры текущей версии "
        "или выпавший из неё, остаётся с жёлтой меткой и причиной."
    ),
)
def get_shortlist(need_id: NeedId, user: Employer, db: DbSession, secret: Secret) -> ShortlistOut:
    need = _need(db, user, need_id)
    out = _shortlist(db, need, secret)
    db.commit()
    return out


@router.put(
    "/{need_id}/shortlist/{ref}",
    response_model=ShortlistOut,
    summary="📌 Закрепить кандидата",
    description=(
        "Добавляет кандидата в шортлист брифа (из подборки или банка — по той же ссылке ref). Повторное "
        "закрепление ничего не меняет. Закреплённый считается «открывавшим карточку» (DECISIONS п. 7). "
        "404 — ссылка чужая или кандидат недоступен; 409 `shortlist_full` — уже 30; 409 `need_closed` — бриф закрыт."
    ),
)
def pin(
    need_id: NeedId, ref: Ref, user: Employer, db: DbSession, limiter: LimiterDep, secret: Secret, body: PinIn | None = None
) -> ShortlistOut:
    _limit(limiter, "write", user)
    need = _need(db, user, need_id)
    svc.pin(db, user, need, ref, note=body.note if body else None)
    out = _shortlist(db, need, secret)
    db.commit()
    return out


@router.delete(
    "/{need_id}/shortlist/{ref}",
    response_model=ShortlistOut,
    summary="Открепить кандидата",
    description="Убирает кандидата из шортлиста брифа. 404 `pin_not_found` — его там нет.",
)
def unpin(need_id: NeedId, ref: Ref, user: Employer, db: DbSession, limiter: LimiterDep, secret: Secret) -> ShortlistOut:
    _limit(limiter, "write", user)
    need = _need(db, user, need_id)
    svc.unpin(db, need, ref)
    out = _shortlist(db, need, secret)
    db.commit()
    return out


@router.get(
    "/{need_id}/candidates/{ref}",
    response_model=CandidateCardOut,
    summary="Карточка кандидата (Р6)",
    description=(
        "Слепая карточка (CandidateBlind): категория и история грейда, полоса теста и интервал, модули ✓/✓✓, "
        "задачи, обобщённый ФСП, регион, сигнал по зарплате и полное разложение Score текущей версии. После "
        "взаимного «да» — раскрытая (CandidateDisclosed) с блоком `disclosed`. Открытие записывается: пригласить "
        "можно только того, чью карточку открывали (DECISIONS п. 7)."
    ),
)
def candidate_card(
    need_id: NeedId, ref: Ref, user: Employer, db: DbSession, limiter: LimiterDep, secret: Secret
) -> CandidateCardOut:
    _limit(limiter, "read", user)
    need = _need(db, user, need_id)
    view = svc.candidate_card(db, user, need, ref, secret=secret)
    out = CandidateCardOut.model_validate(privacy.card_dto(view))
    db.commit()
    return out

