"""DTO разделов «Опрос», «Тестирование» и «Лаборатория». Описания полей попадают в OpenAPI — по-русски.

Правило DECISIONS п. 10: в текстах для людей нет «θ». Уровень кандидата наружу — только как балл
S = 500 + 100·уровень и интервал; внутренние «уровень»/«погрешность» — только в лаборатории.
Ключи экземпляров и теги дистракторов кандидату не отдаются никогда (кроме разбора СВОЕГО задания
после решения: верный ответ и объяснение заблуждения).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AnswerValue = str | int | list[str | int] | dict[str, str | int] | None


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodeLabel(BaseModel):
    code: str = Field(description="Код")
    label: str = Field(description="Подпись на русском")


class GradeRef(BaseModel):
    code: str = Field(description="Код грейда: intern | junior | middle | senior")
    name: str = Field(description="Название грейда")


class RuleCheckOut(BaseModel):
    ok: bool = Field(description="Действие доступно")
    code: str = Field(description="Машинный код причины (ok, retake_cooldown, change_interval, …)")
    message: str = Field(description="Пояснение для человека")
    available_at: datetime | None = Field(default=None, description="Когда станет доступно")
    available_in: str | None = Field(default=None, description="Когда станет доступно — относительно («через 14 дней»)")


class ConsentStateOut(BaseModel):
    purpose: str = Field(description="Цель согласия")
    given: bool = Field(description="Согласие действует")
    version: str = Field(description="Актуальная версия текста")
    title: str = Field(description="Название документа")
    summary: str = Field(description="Краткое содержание")


# ---------------------------------------------------------------------------
# Опрос
# ---------------------------------------------------------------------------


class SpecOption(BaseModel):
    code: str = Field(description="Код специализации")
    label: str = Field(description="Название")
    status: str = Field(description="active — тест работает; survey_only — лист ожидания; derived — производная категория")
    testable: bool = Field(description="Можно пройти тест")


class SurveyFormOut(BaseModel):
    version: int = Field(description="Версия анкеты и классификатора")
    industries: list[CodeLabel] = Field(description="Шаг 1: отрасли интереса")
    actions: list[CodeLabel] = Field(description="Шаг 2: 16 действий «что делали руками за 12 месяцев»")
    frequency_levels: list[CodeLabel] = Field(description="Шаг 2: не делал / иногда / регулярно")
    technologies: list[CodeLabel] = Field(description="Шаг 3: технологии")
    recency_levels: list[CodeLabel] = Field(description="Шаг 3: давность использования технологии")
    modules: list[CodeLabel] = Field(description="Шаг 3: модули для самооценки (идёт только в «заявлено»)")
    specializations: list[SpecOption] = Field(description="Шаг 4: специализации")


class SurveyIndustriesIn(_In):
    industries: list[str] = Field(default_factory=list, max_length=10, description="Коды отраслей (можно ни одной)")


class SurveyActionsIn(_In):
    actions: dict[str, Literal["never", "sometimes", "regularly"]] = Field(
        description="Действие → частота за 12 месяцев (неуказанные считаются «не делал»)"
    )


class SurveyStackIn(_In):
    technologies: dict[str, str] = Field(
        default_factory=dict, description="Технология → давность использования (now | last_year | one_three_years | older)"
    )
    experience_months: int = Field(ge=0, le=600, description="Стаж в ИТ, месяцев")
    roles: list[str] = Field(default_factory=list, max_length=10, description="Роли в команде (свободные подписи)")
    self_assessment: dict[str, int] = Field(
        default_factory=dict,
        description="Самооценка по модулям 0–3: только для блока «заявлено», в оценку не входит",
    )


class SurveySpecIn(_In):
    chosen_spec: str = Field(max_length=32, description="Желаемая специализация (код)")


class SpecProbability(BaseModel):
    code: str = Field(description="Код специализации")
    name: str = Field(description="Название")
    probability: float = Field(description="Вероятность по ответам опроса, 0–1")


class SurveyClassificationOut(BaseModel):
    probabilities: list[SpecProbability] = Field(description="Вероятности специализаций по убыванию")
    top: str = Field(description="Самая вероятная специализация по ответам")
    top_name: str = Field(description="Её название")
    chosen: str = Field(description="Выбор кандидата — он и итоговый")
    chosen_name: str = Field(description="Название выбранной специализации")
    hint: bool = Field(description="Показывать подсказку «по ответам вы ближе к …»")
    hint_text: str | None = Field(default=None, description="Текст подсказки (ни к чему не обязывает)")
    chosen_status: str = Field(description="Статус выбранной специализации: active | survey_only | derived")
    testable: bool = Field(description="По выбранной специализации есть тест")
    waitlist_text: str | None = Field(default=None, description="Для survey_only: «Вы в листе ожидания»")
    derived_text: str | None = Field(default=None, description="Для Fullstack: как присваивается производная категория")
    declared_modules: list[CodeLabel] = Field(description="Серые чипы «заявлено» по технологиям опроса (в оценку не входят)")
    suggested_grade: GradeRef | None = Field(default=None, description="Подсказка грейда по стажу")
    suggested_grade_text: str | None = Field(default=None, description="Текст подсказки по стажу")


class SurveyAnswersOut(BaseModel):
    industries: list[str] = Field(default_factory=list, description="Отрасли")
    actions: dict[str, str] = Field(default_factory=dict, description="Действия и частоты")
    technologies: dict[str, str] = Field(default_factory=dict, description="Технологии и давность")
    experience_months: int | None = Field(default=None, description="Стаж, месяцев")
    roles: list[str] = Field(default_factory=list, description="Роли в команде")
    self_assessment: dict[str, int] = Field(default_factory=dict, description="Самооценка по модулям")
    chosen_spec: str | None = Field(default=None, description="Выбранная специализация")


class SurveyStateOut(BaseModel):
    survey_id: uuid.UUID | None = Field(default=None, description="Идентификатор анкеты (null — опрос ещё не начат)")
    number: int = Field(description="Порядковый номер анкеты кандидата (1 — первая)")
    version: int = Field(description="Версия анкеты и классификатора")
    step: int = Field(description="Сколько шагов сохранено (0–4)")
    completed: bool = Field(description="Опрос завершён")
    completed_at: datetime | None = Field(default=None, description="Когда завершён")
    next_step: int | None = Field(default=None, description="Какой шаг заполнять дальше (null — всё заполнено)")
    answers: SurveyAnswersOut = Field(description="Сохранённые ответы (для продолжения после обрыва)")
    classification: SurveyClassificationOut | None = Field(default=None, description="Итог классификатора после шага 4")
    message: str | None = Field(default=None, description="Сообщение кандидату (лист ожидания и т. п.)")


class SurveyHistoryItemOut(BaseModel):
    survey_id: uuid.UUID = Field(description="Идентификатор анкеты")
    number: int = Field(description="Порядковый номер")
    version: int = Field(description="Версия анкеты")
    completed_at: datetime | None = Field(default=None, description="Когда завершена")
    completed_ago: str | None = Field(default=None, description="Когда — относительно")
    chosen_spec: str | None = Field(default=None, description="Выбранная специализация")
    chosen_name: str | None = Field(default=None, description="Её название")
    top: str | None = Field(default=None, description="Самая вероятная по ответам")
    top_name: str | None = Field(default=None, description="Её название")
    experience_months: int | None = Field(default=None, description="Стаж, месяцев")


# ---------------------------------------------------------------------------
# Тестирование: брифинг и сессии
# ---------------------------------------------------------------------------


class GradeCardOut(BaseModel):
    code: str = Field(description="Код грейда")
    name: str = Field(description="Название")
    score: int = Field(description="Порог грейда на линейке 300–600")
    anchor: str | None = Field(default=None, description="Поведенческий якорь для карточки")
    suggested: bool = Field(description="Подсказка по стажу указывает на этот грейд")
    available: bool = Field(default=True, description="Тест на этот грейд можно начать сейчас (иначе — ждём пересдачу)")
    note: str | None = Field(default=None, description="Почему грейд сейчас недоступен и когда станет доступен")


class PoolReadinessOut(BaseModel):
    ready: bool = Field(description="Пул одноразовых заданий готов (после `docker compose up` его греет сервис pool)")
    families_total: int = Field(description="Семейств заданий в проверке")
    families_ready: int = Field(description="Из них с достаточным запасом свободных экземпляров")
    progress: float = Field(description="Доля готовых семейств, 0…1 — для полосы прогресса")
    eta_s: int = Field(description="Ориентир ожидания, секунд (0 — готово)")
    message: str = Field(description="Текст для экрана: «Готовим одноразовые задания…»")


class BriefingOut(BaseModel):
    specialization: CodeLabel = Field(description="Специализация")
    testable: bool = Field(description="По специализации есть тест")
    survey_done: bool = Field(description="Опрос пройден")
    mode: Literal["test", "upgrade", "unavailable"] = Field(
        description="test — первичный тест или повтор; upgrade — тест на повышение; unavailable — сейчас нельзя"
    )
    grades: list[GradeCardOut] = Field(description="Карточки грейдов")
    experience_months: int | None = Field(default=None, description="Стаж из опроса или резюме")
    experience_hint: str | None = Field(default=None, description="Подсказка по стажу (ни к чему не обязывает)")
    senior_warning: str | None = Field(default=None, description="Мягкое предупреждение при выборе Senior и стаже < 24 мес.")
    rules: list[str] = Field(description="Правила теста для брифинга")
    consent: ConsentStateOut = Field(description="Согласие на журналирование событий теста — обязательно для старта")
    can_start: RuleCheckOut = Field(description="Можно ли начать сейчас и когда станет можно")
    retake_rules: list[str] = Field(
        default_factory=list, description="Правила пересдачи и повышения простым языком (значения сроков — из config/testing.yaml)"
    )
    retake_status: str | None = Field(
        default=None, description="Личный итог: когда можно пересдать, на какой грейд — сразу (после неудачи)"
    )
    current_grade: GradeRef | None = Field(default=None, description="Подтверждённый грейд")
    upgrade_target: GradeRef | None = Field(default=None, description="Грейд теста на повышение")
    next_change_allowed_at: datetime | None = Field(default=None, description="Ближайшая возможная смена грейда")
    next_change_in: str | None = Field(default=None, description="То же — относительно («через 90 дней»)")
    active_session_id: uuid.UUID | None = Field(default=None, description="Незавершённая сессия (продолжить)")
    pool: PoolReadinessOut | None = Field(default=None, description="Готовность пула заданий; пока не готов — can_start.code = pool_warming_up")


class SessionCreateIn(_In):
    specialization: str = Field(max_length=32, description="Специализация (backend_python | frontend_js)")
    grade: str | None = Field(default=None, max_length=16, description="Заявленный грейд (для повышения можно не указывать)")
    upgrade: bool = Field(default=False, description="Тест на повышение на одну ступень")
    consent_test_logging: bool = Field(
        default=False, description="Галочка брифинга: согласие на журналирование событий теста (записывается в журнал)"
    )
    express: bool | None = Field(
        default=None,
        description=(
            "Экспресс-версия для жюри (только DEMO_MODE, иначе 404): до 8 заданий и бюджет ≈ 4 минуты, тот же движок и "
            "банк, сессия demo_assisted, категория не присваивается"
        ),
    )


class ProgressOut(BaseModel):
    position: int = Field(description="Номер текущего задания (считаются все показанные)")
    text: str = Field(description="«задание 7 · от 8 до 20»")


class SessionOut(BaseModel):
    id: uuid.UUID = Field(description="Идентификатор сессии")
    kind: str = Field(description="initial | retake | upgrade | ext_up | ext_down")
    kind_ru: str = Field(description="Вид сессии по-русски")
    specialization: CodeLabel = Field(description="Специализация")
    claimed_grade: GradeRef = Field(description="Заявленный грейд")
    target_grade: GradeRef = Field(description="Грейд, порог которого проверяется сейчас")
    status: str = Field(description="CREATED | IN_PROGRESS | PAUSED | DECIDED | EXTENSION_OFFERED | FINAL | UNDER_REVIEW | INVALIDATED | ABANDONED")
    status_ru: str = Field(description="Статус по-русски")
    started_at: datetime | None = Field(default=None, description="Начало")
    progress: ProgressOut = Field(description="Прогресс")
    parent_session_id: uuid.UUID | None = Field(default=None, description="Для добора — основная сессия")
    child_session_id: uuid.UUID | None = Field(default=None, description="Принятый добор (продолжение)")
    finished: bool = Field(description="Ответы больше не принимаются")
    express: bool = Field(default=False, description="Экспресс-версия для жюри (DEMO_MODE): короткий тест, категория не присваивается")
    express_deadline_at: datetime | None = Field(
        default=None, description="Экспресс: когда истекает бюджет времени (после него решение по уже данным ответам)"
    )
    express_items_max: int | None = Field(default=None, description="Экспресс: потолок заданий")


class OptionOut(BaseModel):
    id: str = Field(description="Идентификатор варианта")
    text: str = Field(description="Текст варианта")


class ItemOut(BaseModel):
    seq: int = Field(description="Номер задания в цепочке — его передают в ответ")
    variant_code: str = Field(description="Код варианта в углу экрана (#7F3A)")
    prompt: str = Field(description="Формулировка")
    code: str | None = Field(default=None, description="Код (моноширинным шрифтом)")
    language: str | None = Field(default=None, description="Язык кода")
    options: list[OptionOut] = Field(default_factory=list, description="Варианты (выбор) или исправления (клик по строке)")
    lines: list[str] = Field(default_factory=list, description="Строки: клик по строке или фрагменты для упорядочивания")
    table: dict[str, Any] | None = Field(default=None, description="Мини-таблица для SQL-заданий (NULL — значение None)")
    answer_mode: str = Field(description="text | choice | line_choice | order")
    answer_hint: str | None = Field(default=None, description="Что именно ввести")
    answer_format: str = Field(description="Формат ответа: строка; id варианта; {\"line\": номер с 1, \"fix\": id}; список строк или индексов с 0")
    time_limit_s: int = Field(description="Лимит времени, секунд (сервер добавляет 5 с запаса)")
    deadline_at: datetime = Field(description="Серверный дедлайн: позже ответ засчитывается как неверный")
    seconds_left: int = Field(description="Секунд до дедлайна")
    clip: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Защита от подсказчика: данные для обработчика копирования на странице теста (вариант текста для "
            "буфера, карта подмены, render_id). null — защита для этого задания не включена"
        ),
    )


class NextOut(BaseModel):
    session: SessionOut = Field(description="Состояние сессии")
    item: ItemOut | None = Field(default=None, description="Текущее задание (null — тест завершён)")
    message: str | None = Field(default=None, description="Пояснение («без кнопки „назад“», «тест завершён» и т. п.)")


class AnswerIn(_In):
    seq: int = Field(ge=1, le=100, description="Номер задания из GET …/next")
    answer: AnswerValue = Field(default=None, description="Ответ; null — пропустить (засчитывается как неверный)")


class AnswerOut(BaseModel):
    seq: int = Field(description="Номер задания")
    accepted: bool = Field(description="Ответ записан")
    duplicate: bool = Field(description="Повтор того же seq: возвращён уже записанный ответ")
    timed_out: bool = Field(description="Ответ пришёл после дедлайна и засчитан как неверный")
    status: str = Field(description="Статус сессии после ответа")
    status_ru: str = Field(description="Статус по-русски")
    finished: bool = Field(description="Тест завершён — смотрите результат")


EventKey = Annotated[str, Field(min_length=1, max_length=32)]
EventValue = Annotated[str, Field(max_length=256)] | int | float | bool | None


class EventIn(_In):
    # cut / dragstart / self_paste / copy_bypass — события «двойника в буфере» (spec-antiassist §7):
    # пишутся в журнал и передаются хуку on_client_event; сами по себе флага не ставят
    type: Literal["blur", "focus", "paste", "copy", "cut", "dragstart", "self_paste", "copy_bypass", "reconnect"] = (
        Field(description="Событие страницы теста")
    )
    at: datetime | None = Field(default=None, description="Время события на клиенте (иначе — время приёма)")
    payload: dict[EventKey, EventValue] = Field(
        default_factory=dict,
        max_length=10,
        description="Подробности (до 10 полей, строки до 256 символов): например {\"field\": \"answer\"} для вставки",
    )


class EventsIn(_In):
    events: list[EventIn] = Field(min_length=1, max_length=50, description="Пачка событий (до 50)")


class EventsOut(BaseModel):
    accepted: int = Field(description="Сколько событий записано")
    ignored: int = Field(description="Сколько пропущено (тест закрыт или согласие на журналирование отозвано)")
    status: str = Field(description="Статус сессии")


class ExtensionIn(_In):
    decision: Literal["accept", "decline"] = Field(description="Принять добор или отказаться")
    direction: Literal["up", "down"] | None = Field(default=None, description="Направление добора (для accept)")


class ExtensionOut(BaseModel):
    session: SessionOut = Field(description="Основная сессия после решения")
    child: SessionOut | None = Field(default=None, description="Сессия добора (при accept)")
    message: str = Field(description="Что произошло")


# ---------------------------------------------------------------------------
# Результат и разбор
# ---------------------------------------------------------------------------


class RulerCutOut(BaseModel):
    grade: str = Field(description="Код грейда")
    name: str = Field(description="Название")
    score: int = Field(description="Порог на линейке")


class RulerOut(BaseModel):
    min: int = Field(description="Левый край линейки")
    max: int = Field(description="Правый край линейки")
    cuts: list[RulerCutOut] = Field(description="Пороги грейдов")


class CertaintyOut(BaseModel):
    grade: str = Field(description="Полоса грейда (below_intern — ниже Стажёра)")
    name: str = Field(description="Название")
    probability: float = Field(description="Вероятность, 0–1")
    percent: int = Field(description="Вероятность, %")


class LevelLineOut(BaseModel):
    grade: str = Field(description="Уровень заданий")
    name: str = Field(description="Название")
    correct: int = Field(description="Решено верно")
    total: int = Field(description="Всего зачётных заданий этого уровня")


class ModuleTagOut(BaseModel):
    module: str = Field(description="Код модуля")
    name: str = Field(description="Название модуля")
    n: int = Field(description="Зачётных заданий модуля")
    correct: int = Field(description="Верно")
    tag: str = Field(description="strong (✓✓) | confirmed (✓) | insufficient (~) | none")
    symbol: str = Field(description="✓✓ | ✓ | ~ | пусто")
    tag_ru: str = Field(description="Подпись тега")
    level_ru: str = Field(description="ниже / на уровне / выше грейда или «мало данных»")


class DecisionOut(BaseModel):
    code: str = Field(description="PASSED | FAILED | BORDERLINE_PASS | BORDERLINE_FAIL")
    label: str = Field(description="Решение по-русски")
    passed: bool = Field(description="Грейд подтверждён")
    borderline: bool = Field(description="На границе (20 заданий без уверенности 90%)")
    reason: str = Field(description="confidence | max_items | bank_exhausted")


class OfferOut(BaseModel):
    direction: str = Field(description="up — добор вверх; down — добор вниз")
    target_grade: GradeRef = Field(description="Грейд добора")
    instant: bool = Field(description="Подтверждение одним нажатием (0 заданий)")
    max_items: int = Field(description="Максимум заданий добора")
    confidence_pct: int = Field(description="Уверенность по уже данным ответам, %")
    text: str = Field(description="Текст предложения")


class BlindCardModuleOut(BaseModel):
    name: str = Field(description="Модуль")
    symbol: str = Field(description="✓ или ✓✓")


class BlindCardPreviewOut(BaseModel):
    pseudonym: str = Field(description="Псевдоним (у каждой компании свой; здесь — заглушка)")
    category: str | None = Field(default=None, description="Категория «специализация · грейд»")
    badge: str = Field(description="Строка бейджа, как в выдаче работодателя")
    score: int | None = Field(default=None, description="Балл")
    interval80: list[int] = Field(default_factory=list, description="80%-интервал балла")
    confidence_pct: int | None = Field(default=None, description="Уверенность, %")
    how_obtained: str = Field(description="Поповер «как получен»: решено по уровням")
    modules: list[BlindCardModuleOut] = Field(default_factory=list, description="Подтверждённые модули")
    trust: str | None = Field(default=None, description="Доверие: высокое / сниженное")
    freshness: str | None = Field(default=None, description="Актуальность")
    note: str = Field(description="Что работодатель НЕ видит")


class ExpressResultOut(BaseModel):
    """Честная подача результата экспресс-версии: оценка с широкой погрешностью, а не категория."""

    badge: str = Field(description="«Экспресс-версия для жюри»")
    verdict: str = Field(description="estimate_pass | estimate_below | inconclusive")
    headline: str = Field(description="Главная строка: оценка или «Грейд не определён точно — нужен полный тест»")
    note: str = Field(description="Что это значит")
    margin_note: str = Field(description="Погрешность: 80%-интервал балла и его ширина")
    interval_width: int = Field(description="Ширина 80%-интервала, баллов")
    n_items: int = Field(description="Заданий в экспрессе")
    stop_reason: str = Field(description="confidence | max_items | time_budget | bank_exhausted")
    full_test_title: str = Field(description="Что даёт полный тест")
    full_test: list[str] = Field(description="Чем полный тест отличается от экспресса")


class ResultOut(BaseModel):
    session: SessionOut = Field(description="Сессия")
    visible: bool = Field(description="Результат показан (false — на проверке, прерван или аннулирован)")
    message: str | None = Field(default=None, description="Почему результат скрыт / что дальше")
    headline: str | None = Field(default=None, description="«Middle подтверждён · 542 · уверенность 94%»")
    decision: DecisionOut | None = Field(default=None, description="Решение")
    stop_text: str | None = Field(default=None, description="«Тест остановился на 13-м задании: …»")
    confirmed_grade: GradeRef | None = Field(default=None, description="Грейд, который даёт эта сессия")
    category: CodeLabel | None = Field(default=None, description="Категория")
    score: int | None = Field(default=None, description="Балл на линейке 300–600")
    interval80: list[int] = Field(default_factory=list, description="80%-интервал балла")
    ruler: RulerOut | None = Field(default=None, description="Линейка с порогами")
    confidence_pct: int | None = Field(default=None, description="Уверенность в решении у порога, %")
    certainty: list[CertaintyOut] = Field(default_factory=list, description="«Насколько мы уверены»: вероятности полос грейдов")
    by_level: list[LevelLineOut] = Field(default_factory=list, description="Решено по уровням")
    by_level_text: str | None = Field(default=None, description="«Junior 3/3, Middle 6/8, Senior 1/2»")
    modules: list[ModuleTagOut] = Field(default_factory=list, description="Модули ✓/✓✓/~")
    n_shown: int = Field(description="Показано заданий (вместе с пробными)")
    offers: list[OfferOut] = Field(default_factory=list, description="Предложения добора")
    offers_expire_at: datetime | None = Field(default=None, description="До когда можно принять добор")
    offers_expire_in: str | None = Field(default=None, description="То же — относительно")
    review_available: bool = Field(description="Доступен разбор ошибок")
    blind_card_consent: ConsentStateOut = Field(description="Согласие на показ слепой карточки: без него кандидата нет в банке")
    blind_card_preview: BlindCardPreviewOut | None = Field(default=None, description="«Так вас видит работодатель»")
    express: ExpressResultOut | None = Field(default=None, description="Экспресс-версия для жюри: оценка вместо категории (иначе null)")


class ReviewItemOut(BaseModel):
    seq: int = Field(description="Номер задания")
    variant_code: str = Field(description="Код варианта")
    module: CodeLabel = Field(description="Модуль")
    prompt: str = Field(description="Формулировка вашего экземпляра")
    code: str | None = Field(default=None, description="Код вашего экземпляра")
    language: str | None = Field(default=None, description="Язык")
    options: list[OptionOut] = Field(default_factory=list, description="Варианты")
    lines: list[str] = Field(default_factory=list, description="Строки")
    table: dict[str, Any] | None = Field(default=None, description="Таблица")
    answer_mode: str = Field(description="Режим ответа")
    your_answer: AnswerValue = Field(default=None, description="Ваш ответ (null — не успели или пропустили)")
    your_answer_text: str | None = Field(default=None, description="Ваш ответ — текстом")
    correct_answer: AnswerValue = Field(default=None, description="Верный ответ вашего экземпляра")
    correct_answer_text: str = Field(description="Верный ответ — текстом")
    timed_out: bool = Field(description="Время вышло")
    explanation: str | None = Field(default=None, description="Объяснение решения")
    misconception: str | None = Field(default=None, description="Какое заблуждение выдаёт ваш ответ (если узнали)")


class ReviewOut(BaseModel):
    session_id: uuid.UUID = Field(description="Сессия")
    n_total: int = Field(description="Всего заданий")
    n_wrong: int = Field(description="Ошибочных")
    items: list[ReviewItemOut] = Field(description="Разбор ошибочных заданий")
    note: str = Field(description="Почему показывать ответы безопасно")


# ---------------------------------------------------------------------------
# Квалификация («Мой грейд»)
# ---------------------------------------------------------------------------


class CandidateFlagOut(BaseModel):
    id: uuid.UUID = Field(description="Идентификатор флага")
    session_id: uuid.UUID = Field(description="Сессия")
    kind: str = Field(description="foreign_key | lz | fast_correct | focus | paste | …")
    severity: str = Field(description="soft — мягкий; hard — жёсткий")
    message: str = Field(description="Что заметила система")
    status: str = Field(description="open | dismissed | confirmed")
    status_ru: str = Field(description="Статус по-русски")
    created_at: datetime = Field(description="Когда")
    created_ago: str | None = Field(default=None, description="Когда — относительно")
    dispute_text: str | None = Field(default=None, description="Ваше возражение")
    can_dispute: bool = Field(description="Можно оспорить")


class DisputeIn(_In):
    text: str = Field(min_length=3, max_length=1000, description="Почему флаг ошибочен")


class GradeChangeOut(BaseModel):
    from_grade: GradeRef | None = Field(default=None, description="Было")
    to_grade: GradeRef | None = Field(default=None, description="Стало")
    reason: str = Field(description="Причина (код)")
    reason_ru: str = Field(description="Причина по-русски")
    at: datetime = Field(description="Когда")
    ago: str | None = Field(default=None, description="Когда — относительно")
    counts_as_change: bool = Field(description="Считается сменой грейда (правило 90 дней)")


class AttemptOut(BaseModel):
    session_id: uuid.UUID = Field(description="Сессия")
    kind: str = Field(description="Вид")
    kind_ru: str = Field(description="Вид по-русски")
    status: str = Field(description="Статус")
    status_ru: str = Field(description="Статус по-русски")
    claimed_grade: GradeRef = Field(description="Заявленный грейд")
    decision: str | None = Field(default=None, description="Решение")
    decision_ru: str | None = Field(default=None, description="Решение по-русски")
    score: int | None = Field(default=None, description="Балл")
    started_at: datetime | None = Field(default=None, description="Начало")
    started_ago: str | None = Field(default=None, description="Начало — относительно")


class FreshnessOut(BaseModel):
    relevance: int = Field(description="Актуальность 0–100")
    band: str = Field(description="fresh | actual | aging | stale")
    band_ru: str = Field(description="свежий / актуальный / устаревает / устарел")
    since: str = Field(description="Последняя активность — относительно")


class QualificationOut(BaseModel):
    specialization: CodeLabel = Field(description="Специализация")
    status: str = Field(description="NONE | SURVEY_DONE | TESTING | CONFIRMED | NOT_CONFIRMED | UPGRADE_TESTING")
    status_ru: str = Field(description="Статус по-русски")
    grade: GradeRef | None = Field(default=None, description="Подтверждённый грейд")
    category: CodeLabel | None = Field(default=None, description="Категория")
    score: int | None = Field(default=None, description="Балл")
    interval80: list[int] = Field(default_factory=list, description="80%-интервал балла с поправкой на давность")
    confidence_pct: int | None = Field(default=None, description="Уверенность, %")
    borderline: bool = Field(description="Подтверждён на границе")
    trust: str | None = Field(default=None, description="Доверие: высокое / сниженное / на проверке")
    hidden: bool = Field(description="Скрыт из витрины до решения методиста")
    hidden_message: str | None = Field(default=None, description="«Результат скрыт до решения методиста, ≤ 7 дней»")
    freshness: FreshnessOut | None = Field(default=None, description="Актуальность")
    modules_confirmed: list[ModuleTagOut] = Field(default_factory=list, description="Подтверждено тестом")
    modules_declared: list[CodeLabel] = Field(default_factory=list, description="Заявлено в опросе (серым)")
    next_change_allowed_at: datetime | None = Field(default=None, description="Ближайшая смена грейда")
    next_change_in: str | None = Field(default=None, description="То же — относительно")
    next_attempt_allowed_at: datetime | None = Field(default=None, description="Ближайшая попытка")
    next_attempt_in: str | None = Field(default=None, description="То же — относительно")
    can_start: RuleCheckOut = Field(description="Можно ли начать тест")
    lower_grades_open: list[str] = Field(
        default_factory=list, description="Грейды ниже проваленного: тест доступен сразу, пересдачу не расходует"
    )
    can_upgrade: RuleCheckOut = Field(description="Можно ли пройти тест на повышение")
    history: list[GradeChangeOut] = Field(default_factory=list, description="Смены грейда")
    attempts: list[AttemptOut] = Field(default_factory=list, description="Попытки и доборы")
    flags: list[CandidateFlagOut] = Field(default_factory=list, description="Свои флаги с кнопкой «оспорить»")
    is_fullstack: bool = Field(default=False, description="Есть производная категория Fullstack")


# ---------------------------------------------------------------------------
# Лаборатория
# ---------------------------------------------------------------------------


class TraceStepOut(BaseModel):
    seq: int = Field(description="Номер задания")
    phase: int = Field(description="0 — основная часть, 1+ — доборы")
    family_id: str = Field(description="Семейство")
    module: str = Field(description="Модуль")
    family_grade: str = Field(description="Уровень семейства по экспертам")
    is_pretest: bool = Field(description="Пробное задание (в оценку не входит)")
    correct: bool = Field(description="Верно")
    timed_out: bool = Field(description="Таймаут")
    rt_ms: int | None = Field(default=None, description="Время ответа, мс")
    variant_code: str | None = Field(default=None, description="Код варианта экземпляра")
    difficulty: float = Field(description="Сложность семейства на шкале")
    target_grade: str = Field(description="Проверяемый порог")
    level: float | None = Field(default=None, description="Уровень после ответа (внутренняя шкала)")
    error: float | None = Field(default=None, description="Погрешность уровня после ответа")
    score: int | None = Field(default=None, description="Балл после ответа")
    p_pass: float | None = Field(default=None, description="Вероятность «выше порога» после ответа")
    reason: dict[str, Any] = Field(default_factory=dict, description="Почему выбрано: цель, модуль, дефицит, top-K с информацией")


class LabFlagOut(BaseModel):
    id: uuid.UUID = Field(description="Флаг")
    session_id: uuid.UUID = Field(description="Сессия")
    candidate_id: uuid.UUID | None = Field(default=None, description="Кандидат")
    session_status: str | None = Field(default=None, description="Статус сессии")
    kind: str = Field(description="Вид флага")
    severity: str = Field(description="soft | hard")
    value: float | None = Field(default=None, description="Значение статистики")
    message: str = Field(description="Описание")
    family_id: str | None = Field(default=None, description="Семейство (для «чужого ключа»)")
    source_session_id: uuid.UUID | None = Field(default=None, description="«Чужой ключ» → источник: сессия")
    evidence: dict[str, Any] | None = Field(
        default=None, description="Доказательства сигнала защиты от подсказчика (только для методиста)"
    )
    status: str = Field(description="open | dismissed | confirmed")
    dispute_text: str | None = Field(default=None, description="Возражение кандидата")
    created_at: datetime = Field(description="Когда")


class LabSessionOut(BaseModel):
    id: uuid.UUID = Field(description="Сессия")
    candidate_id: uuid.UUID | None = Field(default=None, description="Кандидат")
    specialization: str = Field(description="Специализация")
    kind: str = Field(description="Вид сессии")
    status: str = Field(description="Статус")
    claimed_grade: str = Field(description="Заявленный грейд")
    decision: str | None = Field(default=None, description="Решение")
    n_items: int = Field(description="Показано заданий")
    score: int | None = Field(default=None, description="Балл по итогам")
    trust: float | None = Field(default=None, description="Доверие")
    flags_open: int = Field(description="Открытых флагов")
    demo_assisted: bool = Field(description="Режим жюри с подсветкой ответов (исключается из метрик)")
    parent_session_id: uuid.UUID | None = Field(default=None, description="Основная сессия (для добора)")
    started_at: datetime | None = Field(default=None, description="Начало")
    finished_at: datetime | None = Field(default=None, description="Окончание")


class TraceOut(BaseModel):
    session_id: uuid.UUID = Field(description="Сессия")
    candidate_id: uuid.UUID | None = Field(default=None, description="Кандидат")
    specialization: str = Field(description="Специализация")
    kind: str = Field(description="Вид сессии")
    status: str = Field(description="Статус")
    claimed_grade: str = Field(description="Заявленный грейд")
    target_grade: str = Field(description="Проверяемый порог")
    target_level: float = Field(description="Порог на внутренней шкале")
    decision: str | None = Field(default=None, description="Решение")
    labels: dict[str, str] = Field(description="Подписи осей: уровень, погрешность")
    seed: int = Field(description="Seed выбора заданий (трасса воспроизводима)")
    steps: list[TraceStepOut] = Field(description="Шаги")
    journal: list[dict[str, Any]] = Field(default_factory=list, description="Журнал движка (снятие лимита экспозиции и т. п.)")
    flags: list[LabFlagOut] = Field(default_factory=list, description="Флаги сессии")


class FamilyScaleParamsOut(BaseModel):
    specialization: str = Field(description="Шкала")
    status: str = Field(description="draft | reviewed | pretest | operational | quarantine | retired")
    difficulty: float = Field(description="Сложность b")
    difficulty_sd: float = Field(description="Неуверенность в сложности σ_b")
    source: str = Field(description="prior — экспертная оценка; map — уточнено по ответам")
    n_responses: int = Field(description="Ответов при калибровке")
    r_pb: float | None = Field(default=None, description="Точечно-бисериальная корреляция")
    cusum: float = Field(description="Статистика CUSUM дрейфа")
    exposure_30d: float = Field(description="Экспозиция за 30 дней")
    params_version: int = Field(description="Версия параметров")
    calibrated_at: datetime | None = Field(default=None, description="Когда уточнено")


class PoolOut(BaseModel):
    total: int = Field(description="Экземпляров в пуле")
    free: int = Field(description="Свободных")
    issued: int = Field(description="Выдано")
    legacy_free: int = Field(
        default=0,
        description="Свободных экземпляров со старым предсказуемым seed — заменить: scripts/generate_pool.py --rotate-legacy",
    )
    key_collision_rate: float = Field(description="Доля совпадающих ключей (детектор «чужого ключа» работает при < 2%)")


class BankFamilyOut(BaseModel):
    family_id: str = Field(description="Семейство")
    title: str = Field(description="Название")
    module: CodeLabel = Field(description="Модуль")
    format: str = Field(description="Формат задания")
    specs: list[str] = Field(description="Шкалы")
    target_grade: str = Field(description="Уровень по экспертам")
    position: float = Field(description="Положение внутри уровня")
    guessing: float = Field(description="Вероятность угадывания c")
    time_limit_s: int = Field(description="Лимит времени")
    is_anchor: bool = Field(description="Якорь шкалы (сложность не обновляется)")
    enemies: list[str] = Field(description="Взаимоисключающие семейства")
    params: list[FamilyScaleParamsOut] = Field(description="Параметры по шкалам")
    pool: PoolOut = Field(description="Пул экземпляров")


class BankOut(BaseModel):
    specialization: str | None = Field(default=None, description="Фильтр по специализации")
    total: int = Field(description="Семейств")
    by_status: dict[str, int] = Field(description="Семейств по статусу")
    families: list[BankFamilyOut] = Field(description="Семейства")


class CloneOut(BaseModel):
    seed: int = Field(description="Seed клона (вне боевого пула)")
    variant_code: str = Field(description="Код варианта")
    prompt: str = Field(description="Формулировка")
    code: str | None = Field(default=None, description="Код")
    language: str | None = Field(default=None, description="Язык")
    options: list[OptionOut] = Field(default_factory=list, description="Варианты")
    lines: list[str] = Field(default_factory=list, description="Строки")
    table: dict[str, Any] | None = Field(default=None, description="Таблица")
    answer_mode: str = Field(description="Режим ответа")
    key: AnswerValue = Field(default=None, description="Ключ (только для методиста)")
    distractor_tags: list[str] = Field(default_factory=list, description="Теги дистракторов")
    explanation: str | None = Field(default=None, description="Объяснение")


class ClonesOut(BaseModel):
    family_id: str = Field(description="Семейство")
    title: str = Field(description="Название")
    difficulty: float | None = Field(default=None, description="Общая сложность семейства (одна на все клоны)")
    clones: list[CloneOut] = Field(description="Клоны рядом")


class FlagResolveIn(_In):
    decision: Literal["confirm", "dismiss"] = Field(description="Подтвердить или снять флаг")
    comment: str | None = Field(default=None, max_length=1000, description="Комментарий методиста")


class FlagResolveOut(BaseModel):
    flag: LabFlagOut = Field(description="Флаг после решения")
    session_status: str = Field(description="Статус сессии после решения")
    review_resolved: bool = Field(description="Проверка сессии завершена (все флаги разобраны)")
    message: str = Field(description="Что произошло")


class ValidationRunOut(BaseModel):
    id: uuid.UUID = Field(description="Прогон")
    kind: str = Field(description="Вид прогона")
    config: dict[str, Any] = Field(description="Конфигурация")
    seeds: list[Any] = Field(description="Seed")
    metrics: dict[str, Any] = Field(description="Метрики")
    git_sha: str | None = Field(default=None, description="Коммит")
    created_at: datetime = Field(description="Когда")


class ValidationOut(BaseModel):
    available: bool = Field(description="Есть ли прогоны")
    kinds: list[str] = Field(description="Виды прогонов в базе")
    run: ValidationRunOut | None = Field(default=None, description="Последний прогон")
    message: str | None = Field(default=None, description="Пояснение")


class CalibrateIn(_In):
    specialization: str | None = Field(default=None, max_length=32, description="Шкала (по умолчанию — все тестируемые)")


class CalibrateOut(BaseModel):
    run_id: uuid.UUID = Field(description="Запись CalibrationRun")
    specializations: list[str] = Field(description="Откалиброванные шкалы")
    n_responses: int = Field(description="Ответов использовано")
    families_updated: int = Field(description="Семейств с уточнённой сложностью")
    report: dict[str, Any] = Field(description="Отчёт по семействам")
