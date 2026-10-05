"""Сервисный слой подбора: связывает чистые движки app/matching/* (разбор брифа, скоринг, объяснения,
версии подборки, охват) с БД и HTTP. Формулы здесь НЕ повторяются — только хранение, сборка входов и DTO.

Источники: _internal/design/01-concept.md §3.2 шаги 3–7, §4.1–4.5, §5.5; spec-contact §3.1–3.6;
DECISIONS п. 1, 6, 7, 14, 16, 20, 21.

Контракты для других модулей (импортируйте отсюда, а не из внутренних файлов):

- ``build_candidate_features(db, user_id) -> list[CandidateFeatures]`` — признаки кандидата для скоринга
  (строка на каждую подтверждённую специализацию + строка ``fullstack`` при двух подтверждённых, грейд —
  меньший). Ничего не пишет в БД.
- ``refresh_candidate_features(db, user_id)`` — пересобрать кэш признаков сейчас (после теста, задачи, синхронизации ФСП).
- ``invalidate_features(db, user_id)`` — дешёвый хук «данные кандидата изменились»: кэш пересоберётся при
  следующей подборке/банке. Вызывать необязательно: перед каждой подборкой и банком кэш сверяется с отпечатком
  источников (профиль К13, квалификации, согласия, ФСП, задачи, опрос) и пересобирается сам. Хуки уже
  подключены: отзыв согласия (app.core.consents) и изменение квалификации (register_qualification_listener).
- ``get_alias(db, company_id, candidate_id) -> Alias`` — псевдоним «К-4821» и непрозрачная ссылка ``ref``
  кандидата в рамках компании (сохраняется при первом показе). Приглашения пишут ``Invitation.pseudonym``
  отсюда, чтобы подпись совпадала с подборкой.
- ``resolve_ref(db, company_id, ref) -> uuid.UUID | None`` — обратное преобразование ссылки из API.
- ``card_opened(db, company_id, candidate_id) -> bool`` — DECISIONS п. 7: карточку открывали (панель Р6)
  ИЛИ кандидат в шортлисте любого брифа компании. Условие «пригласить можно только того, чью карточку открывали».
- ``candidate_profile_view(db, company_id=…, candidate_id=…, need_id=None) -> dict`` — карточка кандидата
  для работодателя с учётом раскрытия: слепая (CandidateBlind) без действующего ContactDisclosure,
  раскрытая (CandidateDisclosed) — с ним. В слепой нет ПДн, внутренних id, ЕКП и ссылок на протоколы.
  Всегда словарь (кандидат скрыт или без категории — минимальная карточка с псевдонимом и available=False).
- ``score_snapshot(db, need_id, candidate_id) -> dict | None`` — строка кандидата из текущей версии
  подборки брифа (балл, вклады, «за»/«против», «почему вы») для Invitation.score_snapshot / why_you.

Формат NeedVersion.items (его читает и «как меня видят», К15): список словарей с ключами candidate_id, spec,
grade, score, score_display, parts, components, rank, category_rank, reasons [{kind, code, text, evidence}],
why_you, chips [{skill_id, kind, state, source, ref, label, v, declared, untestable}], hidden_by, x (данные
карточки). ImpressionLog — показы в «Общем топе» (≤ 50 на версию).
"""

from app.services.matching.features import (
    build_candidate_features,
    invalidate_features,
    refresh_candidate_features,
)
from app.services.matching.needs import score_snapshot
from app.services.matching.privacy import (
    Alias,
    candidate_profile_view,
    card_opened,
    get_alias,
    resolve_ref,
)

__all__ = [
    "Alias",
    "build_candidate_features",
    "candidate_profile_view",
    "card_opened",
    "get_alias",
    "invalidate_features",
    "refresh_candidate_features",
    "resolve_ref",
    "score_snapshot",
]
