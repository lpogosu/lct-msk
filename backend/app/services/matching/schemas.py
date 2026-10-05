"""DTO разделов «Потребности и подборка» и «Банк кандидатов» (OpenAPI, описания — на русском).

Работодатель до раскрытия видит кандидата только как псевдоним «К-4821» и непрозрачную ссылку `ref` в рамках
своей компании (DECISIONS п. 20): внутренних id кандидата, id попыток теста, задач и записей ФСП, номеров ЕКП
и ссылок на протоколы в этих моделях нет. ПДн — только в блоке `disclosed` карточки, и только при действующем
согласии кандидата на эту компанию (CandidateDisclosed).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Grade = Literal["intern", "junior", "middle", "senior"]
Format = Literal["office", "hybrid", "remote"]
FilterCode = Literal[
    "has_fsp",
    "salary_fit",
    "format",
    "city",
    "must_confirmed",
    "skill",
    "min_score",
    "active",
    "industry",
    "grade",
    "confirmed_only",
    "exclude",
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- вход: бриф


class BriefEdits(_In):
    """Правки работодателя поверх разбора (источник «вручную»): переживают правку текста брифа."""

    spec: str | None = Field(default=None, max_length=32, description="Специализация (код), например backend_python")
    grade: Grade | None = Field(default=None, description="Грейд брифа")
    must: list[str] | None = Field(
        default=None, max_length=20, description="Обязательные навыки (id справочника) — желаемое состояние, не больше 5"
    )
    nice: list[str] | None = Field(default=None, max_length=30, description="Желательные навыки (id справочника)")
    formats: list[Format] | None = Field(default=None, description="Формат работы")
    city: str | None = Field(default=None, max_length=32, description="Город (код справочника), например msk")
    clear_city: bool = Field(default=False, description="Убрать город из брифа")


class BriefParseIn(BriefEdits):
    text: str = Field(min_length=1, max_length=3000, description="Текст брифа: 2–10 предложений обычным языком")
    salary_from: int | None = Field(default=None, ge=0, le=5_000_000, description="Вилка от, ₽/мес (поле формы)")
    salary_to: int | None = Field(default=None, ge=0, le=5_000_000, description="Вилка до, ₽/мес (поле формы)")
    salary_net: bool | None = Field(default=None, description="true — на руки, false — до НДФЛ")


class NeedCreateIn(BriefParseIn):
    title: str | None = Field(default=None, max_length=200, description="Название брифа (по умолчанию — категория)")
    is_internship: bool = Field(default=False, description="Стажировка: кандидаты 16–17 лет видны только таким брифам")


class NeedPatchIn(BriefEdits):
    text: str | None = Field(default=None, min_length=1, max_length=3000, description="Новый текст брифа")
    salary_from: int | None = Field(default=None, ge=0, le=5_000_000, description="Вилка от, ₽/мес")
    salary_to: int | None = Field(default=None, ge=0, le=5_000_000, description="Вилка до, ₽/мес")
    salary_net: bool | None = Field(default=None, description="true — на руки, false — до НДФЛ")
    title: str | None = Field(default=None, max_length=200, description="Название брифа")
    is_internship: bool | None = Field(default=None, description="Стажировка")
    status: Literal["active", "paused", "closed"] | None = Field(
        default=None, description="Статус: active, paused или closed (закрытие отзывает ждущие ответа приглашения)"
    )
    close_reason: Literal["filled", "cancelled"] | None = Field(
        default=None, description="Причина закрытия: filled — нашли, cancelled — отменён"
    )


# --------------------------------------------------------------------------- выход: разбор


class QuoteOut(BaseModel):
    start: int = Field(description="Начало цитаты в тексте брифа (символ)")
    end: int = Field(description="Конец цитаты (не включая)")
    text: str = Field(description="Цитата")


class SourcedOut(BaseModel):
    value: Any = Field(description="Значение поля")
    name: str | None = Field(default=None, description="Название для показа")
    source: str = Field(description="Источник: «правило» или «вручную»")
    quotes: list[QuoteOut] = Field(default_factory=list, description="Цитаты из брифа (подсветка)")


class SalaryParsedOut(BaseModel):
    salary_from: int | None = Field(description="От, ₽/мес")
    salary_to: int | None = Field(description="До, ₽/мес")
    net: bool | None = Field(description="На руки (true) / до НДФЛ (false) / не указано")
    yearly: bool = Field(description="В тексте была годовая сумма (пересчитана в месяц)")
    source: str = Field(description="Источник: «правило» или «вручную»")
    quotes: list[QuoteOut] = Field(default_factory=list)


class SalaryCheckOut(BaseModel):
    status: str = Field(description="ok | warn (1,6–2,0×) | blocked (> 2,0×) | invalid | incomplete | missing")
    message: str | None = Field(default=None, description="Пояснение для работодателя")
    ratio: float | None = Field(default=None, description="Отношение верха вилки к низу")


class SkillChipOut(BaseModel):
    skill_id: str = Field(description="Навык (id справочника)")
    name: str = Field(description="Название навыка")
    kind: Literal["must", "nice"] = Field(description="Обязательный или желательный")
    source: str = Field(description="Источник: «правило» или «вручную»")
    reason: str = Field(description="Почему такой вид: marker | list | section | default | overflow | manual")
    quote: QuoteOut | None = Field(default=None, description="Где навык найден в брифе")
    marker: QuoteOut | None = Field(default=None, description="Фраза-маркер («будут плюсом»)")
    untestable: bool = Field(description="Тестом не проверяется — проверьте на интервью")
    modules: list[str] = Field(default_factory=list, description="Модули теста, которые проверяют навык")


class ProbOut(BaseModel):
    code: str
    name: str
    p: float = Field(description="Вероятность 0–1")


class ProbsOut(BaseModel):
    top: str = Field(description="Самый вероятный вариант (код)")
    top_name: str = Field(description="Его название")
    probs: list[ProbOut] = Field(description="Распределение по убыванию")
    source: str = Field(description="Источник: «правило» или «вручную»")
    quotes: list[QuoteOut] = Field(default_factory=list, description="Цитаты-основания")


class QuestionOptionOut(BaseModel):
    code: str
    name: str


class QuestionOut(BaseModel):
    field: str = Field(description="О чём вопрос: spec | grade | salary | skills")
    text: str = Field(description="Уточняющий вопрос (не больше одного)")
    options: list[QuestionOptionOut] = Field(default_factory=list, description="Варианты ответа одним нажатием")


class CategoryRecOut(BaseModel):
    spec: str
    grade: str
    name: str = Field(description="Название категории, например «Backend (Python) · Middle»")
    p: float = Field(description="P(категория) = P(специализация)·P(грейд)")
    kind: str = Field(description="primary — основная, suggested — предложенная, adjacent — смежная, manual — вручную")
    status: str = Field(description="active | derived | survey_only (пока не тестируется)")
    n: int | None = Field(default=None, description="Сколько кандидатов в категории (для survey_only — лист ожидания)")
    note: str | None = Field(default=None, description="Пояснение")
    selected: bool | None = Field(default=None, description="Выбрана для подборки")


class ParseOut(BaseModel):
    parser_version: str = Field(description="Версия правил разбора")
    llm_used: bool = Field(default=False, description="LLM не использовалась: разбор на правилах")
    salary: SalaryParsedOut | None
    salary_check: SalaryCheckOut
    skills: list[SkillChipOut]
    spec: ProbsOut
    grade: ProbsOut
    formats: list[SourcedOut]
    city: SourcedOut | None
    relocation: SourcedOut | None
    experience_years: SourcedOut | None
    question: QuestionOut | None
    categories: list[CategoryRecOut] = Field(default_factory=list, description="Рекомендованные категории (1–3)")


# --------------------------------------------------------------------------- бриф


class WarningOut(BaseModel):
    code: str = Field(description="Код предупреждения")
    message: str = Field(description="Текст для работодателя")


class BriefOut(BaseModel):
    spec: str
    grade: str
    must: list[str] = Field(description="Обязательные навыки (id)")
    nice: list[str] = Field(description="Желательные навыки (id)")
    formats: list[str]
    city: str | None = Field(default=None, description="Город (код справочника)")
    salary_from: int
    salary_to: int
    salary_net: bool


class NeedSummaryOut(BaseModel):
    id: uuid.UUID = Field(description="Идентификатор брифа")
    title: str | None
    status: str = Field(description="draft | parsed | active | paused | closed")
    close_reason: str | None = None
    specialization: str | None
    grade: str | None
    category: str = Field(description="Основная категория брифа")
    salary_from: int
    salary_to: int
    salary_net: bool
    is_internship: bool
    current_version: int | None = Field(description="Номер текущей версии подборки (null — ещё не собиралась)")
    shortlist_count: int
    created_at: datetime
    updated_at: datetime


class NeedOut(NeedSummaryOut):
    text: str = Field(description="Текст брифа")
    brief: BriefOut = Field(description="Бриф в форме, по которой считается подборка")
    parse: ParseOut = Field(description="Разбор: чипы с источниками и цитатами, вероятности, уточняющий вопрос")
    warnings: list[WarningOut] = Field(default_factory=list, description="Мягкие предупреждения (широкая вилка)")
    categories: list[CategoryRecOut] = Field(default_factory=list, description="Категории брифа и их выбор")


class NeedListOut(BaseModel):
    items: list[NeedSummaryOut]


# --------------------------------------------------------------------------- категории и охват


class HistogramOut(BaseModel):
    step: int = Field(description="Шаг, ₽")
    bins: list[tuple[int, int]] = Field(
        description="[верх ступени, сколько кандидатов] — ступени склеены так, что в каждой не меньше 3 человек"
    )


class ReachPointOut(BaseModel):
    upper: int = Field(description="Верх вилки, ₽")
    reach: int = Field(description="Сколько кандидатов в охвате")


class ReachOut(BaseModel):
    shown: bool = Field(description="Ползунок показывается только при N ≥ 10")
    n: int
    reach_at_to: int | None = Field(description="Охват при текущем верхе вилки")
    histogram: HistogramOut | None
    points: list[ReachPointOut] = Field(description="Ползунок «верх вилки → охват» с шагом гистограммы")
    hint: str | None = Field(description="«Ваша вилка покрывает 11 из 38. При верхе 300 тыс. — 27 из 38»")
    p25: int | None
    p50: int | None
    p75: int | None
    note: str | None = None


class CategoryCardOut(CategoryRecOut):
    share_fsp: float | None = Field(description="Доля с достижениями ФСП")
    share_active: float | None = Field(description="Доля активных (задача за 30 дней)")
    reach: ReachOut


class CategoriesOut(BaseModel):
    need_id: uuid.UUID
    salary_from: int
    salary_to: int
    salary_net: bool
    categories: list[CategoryCardOut]


class ModuleRefOut(BaseModel):
    code: str
    name: str


class CoveredSkillOut(BaseModel):
    skill_id: str
    name: str
    kind: str | None
    modules: list[ModuleRefOut]
    text: str = Field(description="«PostgreSQL ✓ через модуль „SQL и базы данных“»")


class UntestableSkillOut(BaseModel):
    skill_id: str
    name: str
    kind: str | None
    text: str
    reason: str | None


class ModuleWeightOut(BaseModel):
    code: str
    name: str
    weight: float = Field(description="Вес модуля в компоненте «стек» (Σ = 1)")


class CheckSlotOut(BaseModel):
    module: str
    module_name: str
    skills: list[str] = Field(description="Обязательные навыки брифа, которые проверяет слот")
    family_id: str | None = Field(description="Откалиброванное семейство заданий типа A")
    minutes: float


class CheckSetOut(BaseModel):
    slots: list[CheckSlotOut] = Field(description="3–5 семейств, каждому из шортлиста — свои одноразовые экземпляры")
    untestable: list[str]
    total_minutes: float = Field(description="Не больше 15 минут")
    note: str | None


class CheckOut(BaseModel):
    need_id: uuid.UUID
    note: str
    covered: list[CoveredSkillOut]
    untestable: list[UntestableSkillOut] = Field(description="Серый список «тестом не проверяется — проверьте на интервью»")
    module_weights: list[ModuleWeightOut]
    check_set: CheckSetOut = Field(description="План «проверочного набора» по вакансии (DECISIONS п. 3)")


# --------------------------------------------------------------------------- подборка


class FilterIn(_In):
    code: FilterCode = Field(description="Код неразрушающего фильтра")
    value: Any = Field(
        default=None,
        description="Значение: навык(и) для skill, порог для min_score, отрасли для industry, грейды для grade, "
        "ссылки ref кандидатов для exclude",
    )


class CategoryRefIn(_In):
    spec: str = Field(max_length=32)
    grade: Grade


class VersionCreateIn(_In):
    filters: list[FilterIn] | None = Field(
        default=None, max_length=20, description="Фильтры новой версии (null — как в текущей версии)"
    )
    preset: Literal["balance", "stack"] | None = Field(
        default=None, description="Пресет весов: «Баланс» (balance) или «Стек важнее» (stack)"
    )
    categories: list[CategoryRefIn] | None = Field(
        default=None, max_length=8, description="Категории подборки (null — выбранные в брифе)"
    )


class RollbackIn(_In):
    n: int = Field(ge=1, description="Номер версии (одна из последних 10)")


class PinIn(_In):
    note: str | None = Field(default=None, max_length=300, description="Заметка к закреплённому кандидату")


class FilterOut(BaseModel):
    code: str
    value: Any = None
    label: str = Field(description="Название фильтра")


class CategoryNameOut(BaseModel):
    spec: str
    grade: str
    name: str


class HiddenSummaryOut(BaseModel):
    code: str
    label: str
    count: int = Field(description="Сколько кандидатов скрыл фильтр")


class VersionMetaOut(BaseModel):
    n: int = Field(description="Номер версии v1, v2, …")
    parent_n: int | None = Field(description="Версия, от которой построена")
    current: bool
    created_at: datetime
    preset: str
    weights: dict[str, float]
    scoring_config_version: str = Field(description="Версия конфига формулы (детерминизм)")
    filters: list[FilterOut]
    categories: list[CategoryNameOut]
    total_n: int = Field(description="Строк кандидат × категория в выбранных категориях")
    visible_n: int = Field(description="Видимых в «Общем топе»")
    hidden_n: int = Field(description="«Скрыто фильтрами (N)»")
    hidden_summary: list[HiddenSummaryOut]


class EvidenceOut(BaseModel):
    type: str = Field(description="test_session | task | fsp_result | prefs | module | brief_skill | category")
    ref: str = Field(description="Непрозрачная ссылка в рамках компании (или код модуля / навыка / категории)")
    label: str | None


class ReasonOut(BaseModel):
    kind: Literal["pro", "con"]
    code: str
    component: str | None = Field(description="Компонент балла: T, K, F, A, U")
    text: str
    evidence: list[EvidenceOut]


class ItemChipOut(BaseModel):
    skill_id: str
    name: str
    kind: str = Field(description="must | nice")
    state: str = Field(description="confirmed — подтверждено, weak — слабо, declared — заявлено (серый), no_data")
    source: str | None = Field(description="test | task | fsp")
    label: str = Field(description="«Тест: модуль SQL ✓✓», «Задача „…“ 4/5», «заявлено», «нет данных»")
    evidence_ref: str | None
    untestable: bool


class TestBandOut(BaseModel):
    band: str | None = Field(default=None, description="Полоса: топ-10% / топ-25% / топ-50% категории")
    interval: list[int] | None = Field(default=None, description="Интервал на линейке (80%), шаг 10")
    score: int | None = Field(default=None, description="Точный балл — только после раскрытия")
    percentile: float | None = Field(default=None, description="Перцентиль — только после раскрытия")


class FreshnessOut(BaseModel):
    relevance: int = Field(description="Актуальность 0–100")
    band: str
    label: str
    since: str = Field(description="Относительная дата последней активности")


class SalarySignalOut(BaseModel):
    signal: str | None = Field(default=None, description="«в вилке ✓» или «выше вилки»")
    value: int | None = Field(default=None, description="Минимум числом — только если кандидат выбрал «показывать число»")
    net: bool | None = None


class BadgeOut(BaseModel):
    kind: str = Field(description="new | up | down | same | hidden")
    delta: int
    label: str = Field(description="«новый», «▲3», «▼2», «скрыт»")


class SelectionItemOut(BaseModel):
    ref: str = Field(description="Непрозрачная ссылка на кандидата в рамках компании")
    pseudonym: str = Field(description="Псевдоним «К-4821»")
    category: str
    spec: str
    grade: str
    confirmed: bool = Field(description="Категория подтверждена тестом")
    score: float = Field(description="Score 0–100 (шаг 0,1)")
    parts: dict[str, float] = Field(description="Вклады T, K, F, A, U (Σ = Score)")
    components: dict[str, float] = Field(description="Компоненты 0–1")
    rank: int | None = Field(description="Место в «Общем топе» (null — скрыт фильтром)")
    category_rank: int | None
    pros: list[ReasonOut] = Field(description="До 3 «за»")
    con: ReasonOut | None = Field(description="1 «против» (отсутствие ФСП сюда не попадает никогда)")
    why_you: list[str] = Field(description="Те же «за» на «вы» — для приглашения")
    chips: list[ItemChipOut]
    hidden_by: list[str] = Field(description="Коды фильтров, скрывших строку")
    hidden_reasons: list[str]
    has_fsp: bool
    fsp_line: str | None = Field(description="Обобщённая строка ФСП или нейтральная серая строка")
    fsp_badges: list[str]
    test: TestBandOut
    freshness: FreshnessOut | None
    salary: SalarySignalOut | None
    flags: list[str] = Field(description="borderline | stale_test | unconfirmed")
    badge: BadgeOut | None = Field(description="Бейдж diff относительно сравниваемой версии")
    shortlisted: bool
    weak_match: bool = Field(description="Score < 40 — «слабое соответствие брифу» (предупреждение, не блок)")
    neighbor_below: str | None = Field(description="«Почему выше соседа» для пары #k и #k+1")
    invitation_status: str | None = Field(
        default=None, description="Код статуса активного приглашения этой компании: sent, viewed, countered, accepted"
    )
    invitation_label: str | None = Field(default=None, description="Подпись статуса: «Отправлено», «Просмотрено», …")


class HiddenGroupOut(BaseModel):
    count: int
    page: int
    items: list[SelectionItemOut]


class ShortlistEntryOut(BaseModel):
    ref: str
    pseudonym: str
    mark: Literal["ok", "yellow"] = Field(description="yellow — не прошёл фильтры или выпал из текущей версии")
    reason: str | None
    pinned_at: str | None
    note: str | None
    item: SelectionItemOut | None


class ShortlistOut(BaseModel):
    items: list[ShortlistEntryOut]
    limit: int = Field(description="Не больше 30 на бриф")


class VersionViewOut(VersionMetaOut):
    tab: Literal["overall", "category"]
    category: CategoryNameOut | None
    page: int
    page_size: int
    pages: int
    items_total: int
    items: list[SelectionItemOut]
    hidden: HiddenGroupOut = Field(description="«Скрыто фильтрами (N)» — по одному элементу на кандидата")
    shortlist: list[ShortlistEntryOut]
    compared_to: int | None = Field(description="С какой версией сравнивались бейджи")
    build_ms: float | None = Field(default=None, description="Время сборки версии, мс")


class VersionListOut(BaseModel):
    items: list[VersionMetaOut]
    current: int | None
    rollback_targets: list[int] = Field(description="Версии, на которые можно откатиться (последние 10)")


class NeighborPartOut(BaseModel):
    component: str
    name: str
    value: float


class NeighborOut(BaseModel):
    upper_rank: int
    lower_rank: int
    delta: float = Field(description="Разница баллов")
    plus: NeighborPartOut | None = Field(description="Главный плюс верхнего")
    minus: NeighborPartOut | None = Field(description="Главный минус верхнего")
    text: str = Field(description="«#2 выше #3 на 2,1: свежесть +4,0, тест −1,9»")


class NeedPatchOut(BaseModel):
    need: NeedOut
    version: VersionMetaOut | None = Field(description="Новая версия подборки, если бриф поменялся")
    withdrawn_invitations: int = Field(default=0, description="Сколько приглашений отозвано при закрытии брифа")


# --------------------------------------------------------------------------- карточка кандидата (Р6)


class CardCategoryOut(BaseModel):
    spec: str
    grade: str
    name: str
    borderline: bool
    fullstack: bool
    all: list[str] = Field(description="Все подтверждённые категории")


class GradeChangeOut(BaseModel):
    from_grade: str | None = Field(default=None, description="Был грейд")
    to_grade: str | None = Field(default=None, description="Стал грейд")
    reason: str = Field(description="Основание: тест, тест на повышение, …")
    when: str = Field(description="Относительная дата")


class CardModuleOut(BaseModel):
    module: str
    name: str
    state: str
    symbol: str


class DeclaredOut(BaseModel):
    skill_id: str
    name: str
    state: str = Field(description="declared — «заявлено», в Score не входит")


class CardTaskOut(BaseModel):
    title: str
    kind: str
    result: str = Field(description="Результат «4/5»")
    when: str
    ref: str = Field(description="Непрозрачная ссылка на задачу")


class FspPublicOut(BaseModel):
    discipline: str | None = None
    level: str | None = None
    year: int | None = None
    role: str | None = None
    place_band: str | None = Field(default=None, description="Диапазон места: топ-3 / топ-10 / участник")


class DisclosedOut(BaseModel):
    """Только при действующем согласии кандидата на эту компанию (ContactDisclosure)."""

    basis: str = Field(description="invitation | application")
    fields: list[str] = Field(description="Что разрешил показать кандидат")
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    telegram: str | None = None
    resume: str | None = None
    current_employer: str | None = None
    university: str | None = None
    city: str | None = None
    fsp_extract: str | None = Field(default=None, description="Выписка из протокола ФСП с номером ЕКП")


class CandidateCardOut(BaseModel):
    view: Literal["blind", "disclosed"] = Field(description="blind — CandidateBlind, disclosed — CandidateDisclosed")
    pseudonym: str
    ref: str
    available: bool = Field(description="Кандидат сейчас виден в подборе")
    note: str | None = None
    category: CardCategoryOut | None
    grade_history: list[GradeChangeOut] = Field(default_factory=list)
    test_date: str | None = None
    skills: list[CardModuleOut] = Field(default_factory=list, description="Модули теста ✓ / ✓✓")
    declared_skills: list[DeclaredOut] = Field(default_factory=list, description="Серые чипы «заявлено»")
    tasks: list[CardTaskOut] = Field(default_factory=list)
    freshness: FreshnessOut | None = None
    industries: list[str] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)
    search_mode: str | None = None
    has_fsp: bool = False
    test: TestBandOut | None = None
    fsp: list[FspPublicOut] = Field(default_factory=list, description="Обобщённые результаты ФСП")
    fsp_line: str | None = None
    fsp_badges: list[str] = Field(default_factory=list)
    fsp_source: str | None = None
    salary: SalarySignalOut | None = Field(default=None, description="«в вилке ✓ / выше вилки» или число")
    region: str | None = Field(default=None, description="Код субъекта РФ")
    selection: SelectionItemOut | None = Field(default=None, description="Строка текущей версии подборки брифа")
    disclosed: DisclosedOut | None = Field(default=None, description="Раскрытые данные (только после «да»)")


# --------------------------------------------------------------------------- банк


class BankModuleOut(BaseModel):
    module: str
    name: str
    symbol: str


class BankChipOut(BaseModel):
    skill_id: str
    name: str
    kind: str
    state: str
    source: str | None
    label: str
    evidence_ref: str | None
    untestable: bool


class BankItemOut(BaseModel):
    ref: str
    pseudonym: str
    category: str
    spec: str
    grade: str
    confirmed: bool
    score: float = Field(description="Балл банка 0,55·T + 0,25·F + 0,20·A (×100)")
    parts: dict[str, float] = Field(description="Вклады T, F, A")
    components: dict[str, float]
    test: TestBandOut
    freshness: FreshnessOut | None
    has_fsp: bool
    fsp_line: str | None
    fsp_badges: list[str]
    skills: list[BankChipOut] = Field(description="Запрошенные навыки стека и их подтверждение")
    modules: list[BankModuleOut] = Field(description="Подтверждённые модули теста")
    declared: list[str] = Field(description="Заявленные навыки — серые чипы, в балл не входят")
    industries: list[str]
    formats: list[str]
    search_mode: str | None
    region: str | None
    shortlisted_in: list[uuid.UUID] = Field(description="Брифы компании, в шортлисте которых кандидат")
    shortlisted: bool = Field(description="В шортлисте брифа need_id из запроса")
    invitation_status: str | None = Field(
        default=None, description="Код статуса активного приглашения этой компании: sent, viewed, countered, accepted"
    )
    invitation_label: str | None = Field(default=None, description="Подпись статуса: «Отправлено», «Просмотрено», …")
    hidden_by: list[str] = Field(default_factory=list, description="Фильтры, скрывшие кандидата (группа «Скрыто»)")


class BankHiddenOut(BaseModel):
    count: int = Field(description="«Скрыто фильтрами (N)»: не прошли фильтр «есть ФСП» — не удалены, а скрыты")
    page: int
    label: str | None = Field(default=None, description="Название фильтра, скрывшего кандидатов")
    items: list[BankItemOut]


class PinTargetOut(BaseModel):
    need_id: uuid.UUID
    title: str
    category: str


class BankOut(BaseModel):
    total: int
    page: int
    page_size: int
    pages: int
    items: list[BankItemOut]
    hidden: BankHiddenOut = Field(description="«Скрыто фильтрами (N)» — фильтр «есть ФСП» никого не удаляет")
    pin_targets: list[PinTargetOut] = Field(description="Брифы для кнопки «📌 в шортлист брифа X»")
    weights: dict[str, float]
