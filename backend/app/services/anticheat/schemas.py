"""DTO API «Антиподсказчика»: лента и акт сверки (лаборатория), покрытие двойниками, комплект «Подтверждение за 5 минут»,
песочница жюри. Работодателю — только комплект (VerificationKitOut без причин выбора заданий)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ----------------------------------------------------------------------------- лента сверок


class ReconciliationRowOut(BaseModel):
    id: str = Field(description="Идентификатор сверки: uuid улики сессии либо `sb-<тест>-<номер>` для песочницы жюри")
    source: Literal["session", "sandbox"] = Field(description="session — сессия кандидата; sandbox — «Попробуй списать» (demo_assisted)")
    session_id: uuid.UUID | None = Field(default=None, description="Сессия (для песочницы — нет)")
    label: str = Field(description="Подпись: «Сессия кандидата Илья К., задание 7, вариант 7F3A»")
    seq: int = Field(description="Номер задания")
    variant_code: str = Field(description="Код варианта")
    family_id: str = Field(description="Семейство заданий")
    family_title: str = Field(description="Название семейства")
    mutation_class: str = Field(description="Класс подмены: data_literal | table_value")
    answer: str = Field(description="Ответ кандидата")
    kind: str = Field(description="correct | twin_trace | misconception | other")
    matched: str = Field(description="С чем совпал ответ: key | twin_key | twin_err | twin_token | distractor | none")
    matched_ru: str = Field(description="То же словами")
    strength: str | None = Field(default=None, description="exact (ключ копии) | err_twin | token")
    headline_ru: str = Field(description="Что нашли, одной фразой")
    phase: str = Field(description="before_warning | after_warning | unknown")
    phase_ru: str = Field(description="До/после предупреждения словами")
    session_state: str = Field(description="Состояние сессии по двойникам: none | warning | evidence | review_candidate")
    session_state_ru: str = Field(description="Состояние словами")
    review_status: str = Field(description="open | verification_assigned | dismissed")
    review_status_ru: str = Field(description="Решение методиста словами")
    answered_at: datetime | None = Field(default=None, description="Когда ответили")
    demo_assisted: bool = Field(description="Сессия жюри: исключена из метрик")


class ReconciliationCountsOut(BaseModel):
    open: int = Field(description="Ждут решения методиста")
    verification_assigned: int = Field(description="Назначено подтверждение")
    dismissed: int = Field(description="Отметка снята")


class ReconciliationListOut(BaseModel):
    items: list[ReconciliationRowOut] = Field(description="Сверки, новые сверху")
    counts: ReconciliationCountsOut = Field(description="Сколько сверок в каждом статусе (по всей ленте, без учёта фильтра)")
    note_ru: str = Field(description="Что здесь показано и что не показано (ответы двойника — уже неверные; вес 0; автобана нет)")


# ----------------------------------------------------------------------------- акт сверки


class ActLineOut(BaseModel):
    no: int = Field(description="Номер строки внутри блока (с 1)")
    shown: str = Field(description="Строка, показанная на экране")
    clipboard: str = Field(description="Та же строка в буфере обмена")
    changed: bool = Field(description="Строка отличается")
    shown_span: list[int] | None = Field(default=None, description="Колонки подсвеченного токена на экране [от, до)")
    clipboard_span: list[int] | None = Field(default=None, description="Колонки подсвеченного токена в копии [от, до)")


class ActStatOut(BaseModel):
    id: str = Field(description="probability | phase | scored | weight")
    value: str = Field(description="Крупное значение")
    caption: str = Field(description="Подпись")
    tone: Literal["neutral", "brand", "danger"] = Field(default="neutral", description="Окраска значения")


class ChecklistItemOut(BaseModel):
    id: str = Field(description="self_paste | misconception | glyphs | stale_clip | a11y_mode")
    title_ru: str = Field(description="Альтернативное объяснение")
    status: str = Field(description="ok | attention | excluded | unknown")
    status_ru: str = Field(description="Статус словами")
    note_ru: str = Field(description="Пояснение")


class SessionTwinOut(BaseModel):
    state: str = Field(description="none | warning | evidence | review_candidate")
    state_ru: str = Field(description="Состояние словами")
    n_twin_items: int = Field(description="Заданий с двойником, на которые дан ответ")
    n_counted_hits: int = Field(description="Попаданий в ключ копии (не больше одного на класс подмены)")
    p_honest_text: str = Field(description="Вероятность такого совпадения у честного кандидата по всем заданиям с двойником")
    proposed_level_ru: str = Field(description="Предлагаемый уровень доверия (до решения методиста внешне не меняется)")
    suggested_level_ru: str = Field(description="Уровень, который предложили бы правила при откалиброванной ε")
    context_only: bool = Field(description="ε не откалибрована: вес 0, только контекст")
    trust_weight: float = Field(description="Вес улики в доверии")
    decision: str = Field(description="Кто решает")


class ActOut(BaseModel):
    id: str = Field(description="Идентификатор сверки")
    source: Literal["session", "sandbox"] = Field(description="session | sandbox")
    session_id: uuid.UUID | None = Field(default=None, description="Сессия")
    label: str = Field(description="Подпись над заголовком")
    headline_ru: str = Field(description="Заголовок акта")
    lead_ru: str = Field(description="Что такое копия с изменённым числом")
    title_ru: str = Field(description="Название документа: «Акт сверки: показано и скопировано»")
    variant_code: str = Field(description="Код варианта")
    family_id: str = Field(description="Семейство")
    family_title: str = Field(description="Название семейства")
    mutation_class: str = Field(description="Класс подмены")
    mutation_subclass: str = Field(description="Класс и роль литерала")
    changed_literal: dict[str, Any] = Field(description="Изменённый литерал: было, стало, строка, роль, блок")
    block_name: str = Field(description="Блок с подменой: code | table:<имя>")
    context_ru: str = Field(description="Остальной текст условия над блоком (формулировка), без подсветки")
    lines: list[ActLineOut] = Field(description="Строки блока: слева «показано», справа «попало в буфер»")
    keys: dict[str, str] = Field(description="Ключи: shown — для экрана, clipboard — для копии (оба получены исполнением)")
    candidate_answer: str = Field(description="Ответ кандидата")
    kind: str = Field(description="correct | twin_trace | misconception | other")
    matched: str = Field(description="С чем совпал ответ")
    matched_ru: str = Field(description="То же словами")
    strength: str | None = Field(default=None, description="exact | err_twin | token")
    verdict_ru: str = Field(description="Вердикт без обвинений: только наблюдаемые факты")
    phase: str = Field(description="before_warning | after_warning | unknown")
    phase_ru: str = Field(description="До/после предупреждения словами")
    copy_at: datetime | None = Field(default=None, description="Когда скопировали (по журналу событий)")
    answered_at: datetime | None = Field(default=None, description="Когда ответили")
    warned_at: datetime | None = Field(default=None, description="Когда показано предупреждение (первое копирование в сессии)")
    p_honest: float = Field(description="Вероятность честного совпадения на этом задании (модельная оценка)")
    p_honest_text: str = Field(description="То же с «ожидаемо N на 10 000 сессий»")
    stats: list[ActStatOut] = Field(description="Четыре числа под ключами: шанс, впервые/повторно, засчитано, вес")
    checklist: list[ChecklistItemOut] = Field(description="Альтернативные объяснения (spec §2.1, M7)")
    means_title: str = Field(description="Заголовок «Что это значит»")
    means_ru: str = Field(description="Что это значит")
    session: SessionTwinOut | None = Field(default=None, description="Сводка по сессии (для песочницы нет)")
    review_status: str = Field(description="open | verification_assigned | dismissed")
    review_status_ru: str = Field(description="Решение словами")
    review_note: str | None = Field(default=None, description="Комментарий методиста")
    reviewed_at: datetime | None = Field(default=None, description="Когда принято решение")
    actions: list[dict[str, str]] = Field(description="Доступные решения: id и подпись")
    demo_assisted: bool = Field(description="Сессия жюри: исключена из метрик")
    auto_ban: bool = Field(default=False, description="Всегда false: автоматического бана нет")


class ReconciliationReviewIn(_In):
    action: Literal["assign_verification", "dismiss", "reopen"] = Field(
        description="Назначить подтверждение за 5 минут, снять отметку или вернуть в очередь"
    )
    note: str | None = Field(default=None, max_length=500, description="Комментарий методиста")


# ----------------------------------------------------------------------------- покрытие и предрасчёт


class TwinCoverageFamilyOut(BaseModel):
    family_id: str = Field(description="Семейство")
    title: str = Field(description="Название")
    format: str = Field(description="Формат задания")
    language: str = Field(description="Язык показанного кода")
    mechanism: Literal["own", "generic", "none"] = Field(
        description="own — свой Family.twin() семейства; generic — общий мутатор (код/SQL); none — двойник невозможен по форме"
    )
    reason_ru: str | None = Field(default=None, description="Почему двойника нет по форме")
    pool_free: int = Field(description="Свободных экземпляров в пуле")
    twins_ok: int = Field(description="Экземпляров с готовым двойником")
    twins_none: int = Field(description="Экземпляров, для которых двойник построить не удалось")
    pending: int = Field(description="Свободных экземпляров без записи (двойник ещё не считался)")
    shown: int = Field(description="Двойников выдано кандидатам")
    share: float | None = Field(default=None, description="Доля готовых двойников среди посчитанных")
    top_reasons: list[dict[str, Any]] = Field(default_factory=list, description="Частые причины «двойника нет»")


class TwinCoverageOut(BaseModel):
    families: list[TwinCoverageFamilyOut] = Field(description="Семейства банка")
    totals: dict[str, int] = Field(description="Итоги: семейств, с двойником, свободных, готовых, ждут расчёта, выдано")
    note_ru: str = Field(description="Как получить двойников для пула")
    version: str = Field(description="Версия формата двойников")


class PrecomputeIn(_In):
    family_id: str | None = Field(default=None, max_length=64, description="Только это семейство (по умолчанию — все)")
    per_family: int | None = Field(default=None, ge=1, le=500, description="Сколько свободных экземпляров семейства обработать")


class PrecomputeOut(BaseModel):
    accepted: bool = Field(description="Расчёт запущен в фоне")
    families: int = Field(description="Семейств в работе")
    message: str = Field(description="Что произойдёт дальше")


# ----------------------------------------------------------------------------- «Подтверждение за 5 минут»


class RubricLevelOut(BaseModel):
    score: int = Field(description="Балл рубрики 0–2")
    text: str = Field(description="Критерий")


class VerificationQuestionOut(BaseModel):
    id: str = Field(description="q1…q3 — по трассе; s1, s2 — по навыкам")
    kind: Literal["trace", "skill"] = Field(description="trace — вопрос по собственному заданию кандидата; skill — шаблонный по навыку")
    prompt_ru: str = Field(description="Что спросить")
    rubric: list[RubricLevelOut] = Field(description="Рубрика 0–2")
    listen_for_ru: str = Field(description="Что слушать")
    calibrated: bool = Field(description="Откалибровано (шаблонные вопросы по навыкам — нет)")
    label_ru: str | None = Field(default=None, description="Пометка «не откалибровано»")
    variant_code: str | None = Field(default=None, description="Вариант кандидата, по которому вопрос")
    method: str | None = Field(default=None, description="literal_mutation | family_reseed | template")
    verified_by: str = Field(description="execution | generator | none — чем получен ожидаемый ответ")
    candidate_answer: str | None = Field(default=None, description="Что ответил кандидат (если цитирование безопасно)")
    changed: dict[str, Any] | None = Field(default=None, description="Что изменено: было, стало, строка")
    variant_text: str | None = Field(default=None, description="Текст изменённого задания")
    expected_answer: str | None = Field(default=None, description="Ожидаемый ответ, вычисленный исполнением")


class VerificationKitOut(BaseModel):
    kit_id: str = Field(description="Идентификатор комплекта (детерминирован по сессии)")
    version: str = Field(description="Версия формата комплекта")
    title_ru: str = Field(description="«Подтверждение за 5 минут»")
    subtitle_ru: str = Field(description="Подзаголовок")
    questions: list[VerificationQuestionOut] = Field(description="3 вопроса по трассе и до 2 по навыкам")
    shortfall: int = Field(description="Сколько вопросов по трассе не удалось построить")


class LabVerificationQuestionOut(VerificationQuestionOut):
    family_id: str | None = Field(default=None, description="Семейство (только методисту)")
    seed: int | None = Field(default=None, description="Seed экземпляра (только методисту)")
    selection_reason: str | None = Field(default=None, description="Почему выбрано это задание (только методисту)")


class LabVerificationKitOut(BaseModel):
    kit_id: str = Field(description="Идентификатор комплекта")
    version: str = Field(description="Версия формата комплекта")
    title_ru: str = Field(description="«Подтверждение за 5 минут»")
    subtitle_ru: str = Field(description="Подзаголовок")
    questions: list[LabVerificationQuestionOut] = Field(description="Вопросы с причинами выбора")
    shortfall: int = Field(description="Сколько вопросов по трассе не удалось построить")
    session_id: uuid.UUID = Field(description="Сессия кандидата")


# ----------------------------------------------------------------------------- песочница жюри


class SandboxClipOut(BaseModel):
    render_id: str = Field(description="Идентификатор показа")
    clipboard_text: str = Field(description="Что обработчик копирования кладёт в буфер обмена")


class SandboxItemOut(BaseModel):
    no: int = Field(description="Номер задания 1…4")
    variant_code: str = Field(description="Код варианта")
    prompt: str = Field(description="Формулировка")
    code: str | None = Field(default=None, description="Код")
    language: str | None = Field(default=None, description="Язык")
    table: dict[str, Any] | None = Field(default=None, description="Таблица для SQL")
    answer_mode: str = Field(description="Режим ответа")
    answer_hint: str | None = Field(default=None, description="Что ввести")
    clip: SandboxClipOut = Field(description="Данные для обработчика копирования")


class SandboxStartOut(BaseModel):
    test_id: str = Field(description="Идентификатор мини-теста")
    banner_ru: str = Field(description="Баннер: «Списывайте как угодно…»")
    readme_note_ru: str = Field(description="Оговорка про ноутбук (поведение copy на мобильных не проверено)")
    demo_assisted: bool = Field(description="Сессия помечена demo_assisted и исключена из метрик")
    items: list[SandboxItemOut] = Field(description="4 задания, у каждого есть двойник")


class SandboxAnswersIn(_In):
    answers: dict[str, str] = Field(max_length=8, description="Ответы по номерам заданий: {\"1\": \"…\"}")
    copied: list[int] = Field(default_factory=list, max_length=8, description="Номера заданий, где проверяющий копировал текст (со слов страницы)")


class SandboxDiagnosisOut(BaseModel):
    no: int = Field(description="Номер задания")
    variant_code: str = Field(description="Код варианта")
    answer: str = Field(description="Ответ проверяющего")
    kind: str = Field(description="correct | twin_trace | misconception | other | blank")
    matched: str = Field(description="С чем совпал ответ")
    channel: str = Field(description="clipboard_text | screen_or_retyped | misconception | unknown")
    channel_ru: str = Field(description="Канал словами")
    caught: bool = Field(description="Поймано двойником")
    copied: bool = Field(description="Проверяющий копировал текст (по событию страницы; только контекст)")
    diagnosis_ru: str = Field(description="Диагноз по заданию")
    key_shown: str = Field(description="Ключ экрана")
    key_clipboard: str = Field(description="Ключ копии")
    act: ActOut | None = Field(default=None, description="Акт сверки для пойманного (или со следом) задания")


class SandboxResultOut(BaseModel):
    test_id: str = Field(description="Идентификатор мини-теста")
    demo_assisted: bool = Field(description="Исключена из метрик")
    total: int = Field(description="Заданий")
    caught: int = Field(description="Поймано двойником")
    correct_uncaught: int = Field(description="Верных ответов, которые двойник не ловит")
    summary_ru: str = Field(description="Итог словами")
    next_step_ru: str = Field(description="Что ловит то, что двойник не ловит")
    items: list[SandboxDiagnosisOut] = Field(description="Диагноз по заданиям")


# ----------------------------------------------------------------------------- отчёт валидации


class ValidationReportOut(BaseModel):
    source: str = Field(description="Файл прогона относительно backend/")
    file_time: datetime | str = Field(description="Время файла прогона")
    sections: list[str] = Field(description="Разделы результата")
    meta: dict[str, Any] = Field(description="Паспорт прогона: seed, коммит, длительности")
    result: dict[str, Any] = Field(description="Полный JSON последнего `make validate`")
