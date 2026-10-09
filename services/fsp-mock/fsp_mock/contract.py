"""Контракт реестра ФСП (OpenAPI), который мы предлагаем Федерации спортивного программирования.

Этот модуль — единственный источник истины для формата данных реестра:
- бэкенд «Взаимно» разбирает ответы реестра этими моделями (app/fsp/provider.py);
- мок-сервис services/fsp-mock отдаёт данные в этом же формате (копия файла — fsp_mock/contract.py,
  синхронность проверяет tests/test_fsp_mock.py).

Поэтому здесь нельзя импортировать ничего, кроме stdlib и pydantic. Описания полей — на русском:
они попадают в OpenAPI-документацию мока.

Предметная область: research/brief.md §2 (дисциплины с кодами ВРВС, уровни и статусы по ЕВСК,
разряды и сроки их действия, роли), концепция §6.1. Вид спорта «спортивное программирование» — код ВРВС
1010001311Я, коды дисциплин — 10101…1811Я (config/fsp.yaml).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.0"

EKP = Annotated[
    str,
    Field(
        pattern=r"^\d{16}$",
        description="Номер соревнования в Едином календарном плане (ЕКП), 16 цифр",
        examples=["2025990000123456"],
    ),
]
DisciplineCode = Annotated[
    str,
    Field(
        pattern=r"^\d{10}[А-ЯA-Z]$",
        description="Код дисциплины по ВРВС, например 1010021811Я — программирование продуктовое",
        examples=["1010021811Я"],
    ),
]


class Level(StrEnum):
    """Уровень соревнования по иерархии ЕВСК."""

    international = "international"
    all_russian = "all_russian"
    interregional = "interregional"
    regional = "regional"
    municipal = "municipal"


class StatusType(StrEnum):
    """Статус соревнования (п. 11 Положения о ЕВСК)."""

    championship = "championship"  # чемпионат
    cup = "cup"  # кубок
    first = "first"  # первенство (с ограничением по возрасту)
    other_sport = "other_sport"  # другие официальные спортивные соревнования
    physical_culture_event = "physical_culture_event"  # физкультурное мероприятие
    student = "student"  # студенческие соревнования


class AgeGroup(StrEnum):
    """Возрастная группа соревнования."""

    adults = "adults"
    juniors_u26 = "juniors_u26"
    youth_u19 = "youth_u19"
    kids_u15 = "kids_u15"


class Stage(StrEnum):
    """Этап: отборочный, финальный или единственный."""

    qualifier = "qualifier"
    final = "final"
    single = "single"


class CompetitionFormat(StrEnum):
    """Формат зачёта: личный или командный."""

    personal = "personal"
    team = "team"


class ProtocolStatus(StrEnum):
    """Статус протокола: опубликован или ещё не сдан (до ~2 недель после соревнования)."""

    published = "published"
    pending = "pending"


class ResultRole(StrEnum):
    """Роль спортсмена в результате: личный зачёт, капитан, участник команды, запасной."""

    personal = "personal"
    captain = "captain"
    participant = "participant"
    reserve = "reserve"


class TeamMemberRole(StrEnum):
    """Роль в составе команды."""

    captain = "captain"
    participant = "participant"
    reserve = "reserve"


class ResultStatus(StrEnum):
    """Подтверждён протоколом или ожидает протокол."""

    confirmed = "confirmed"
    awaiting_protocol = "awaiting_protocol"


class RankCode(StrEnum):
    """Спортивный разряд или звание по ЕВСК.

    Нормы ЕВСК по спортивному программированию (Приложение № 24 к приказу Минспорта России № 450 от 22.04.2024):
    МС, КМС, I–III и юношеские I–III. Код MSMK оставлен в контракте на будущее, в нормах его нет:
    платформа его не учитывает и не показывает.
    """

    MSMK = "MSMK"  # мастер спорта международного класса — в нормах по спортивному программированию нет
    MS = "MS"  # мастер спорта России
    KMS = "KMS"  # кандидат в мастера спорта
    I = "I"  # noqa: E741 — так называется разряд
    II = "II"
    III = "III"
    I_y = "I_y"  # юношеские разряды
    II_y = "II_y"
    III_y = "III_y"


class RankStatus(StrEnum):
    """Состояние разряда на дату ответа реестра."""

    active = "active"
    expired = "expired"


class RoleKind(StrEnum):
    """Неспортивная роль: судья или тренер. В оценке силы не участвует — только бейдж."""

    judge = "judge"
    coach = "coach"


class JudgeCategory(StrEnum):
    """Судейская категория."""

    VK = "VK"  # всероссийская
    K1 = "1K"
    K2 = "2K"
    K3 = "3K"
    YuSS = "YuSS"  # юный спортивный судья


class Source(StrEnum):
    """Источник данных: API реестра ФСП, демо-реестр (мок) или загруженный диплом."""

    fsp_api = "fsp_api"
    fsp_mock = "fsp_mock"
    diploma = "diploma"


class _Model(BaseModel):
    # Лишние поля игнорируем: реестр может расширять контракт, не ломая клиентов.
    model_config = ConfigDict(extra="ignore")


class Athlete(_Model):
    """Спортсмен. Ключ — `sub` из ФСП ID (Keycloak, realm fsp)."""

    sub: str = Field(description="Идентификатор спортсмена = claim `sub` ФСП ID")
    last_name: str = Field(description="Фамилия")
    first_name: str = Field(description="Имя")
    middle_name: str | None = Field(default=None, description="Отчество")
    birth_date: date = Field(description="Дата рождения (нужна для политики по несовершеннолетним)")
    region_code: str = Field(description="Код субъекта РФ")
    region_name: str = Field(description="Название субъекта РФ")
    organization: str | None = Field(default=None, description="Региональное отделение или клуб")


class Competition(_Model):
    """Соревнование из ЕКП. Отбор и финал одного соревнования связаны через `parent_ekp`."""

    ekp: EKP
    title: str = Field(description="Название соревнования")
    discipline_code: DisciplineCode
    level: Level
    status_type: StatusType
    age_group: AgeGroup = AgeGroup.adults
    stage: Stage = Stage.single
    parent_ekp: EKP | None = Field(
        default=None, description="Для отборочного этапа — ЕКП финала (отбор и финал считаются одним результатом)"
    )
    format: CompetitionFormat
    online: bool = Field(default=False, description="Проводилось онлайн")
    start_date: date
    end_date: date
    region_code: str | None = Field(default=None, description="Код субъекта РФ места проведения")
    city: str | None = Field(default=None, description="Город проведения")
    participants_count: int = Field(ge=1, description="Число участников (человек)")
    teams_count: int | None = Field(default=None, ge=1, description="Число команд (для командного зачёта)")
    max_score: float | None = Field(default=None, gt=0, description="Максимально возможный балл, если есть")
    protocol_status: ProtocolStatus = ProtocolStatus.published
    protocol_url: str | None = Field(default=None, description="Ссылка на протокол")

    @property
    def field_size(self) -> int:
        """Размер поля N: число команд в командном зачёте, иначе число участников."""
        if self.format == CompetitionFormat.team and self.teams_count:
            return self.teams_count
        return self.participants_count


class TeamMember(_Model):
    """Участник команды."""

    athlete_sub: str = Field(description="sub участника")
    role: TeamMemberRole
    team_function: str | None = Field(
        default=None,
        description=(
            "Функциональная роль в команде из заявки: backend, frontend, ml, design, pm… В протоколе её нет: "
            "платформа показывает её как заявленную участником и в оценке не использует"
        ),
    )


class TeamRef(_Model):
    """Команда и её состав."""

    id: str
    name: str
    members: list[TeamMember] = Field(default_factory=list)


class Result(_Model):
    """Результат спортсмена (или его команды) на соревновании."""

    id: str
    ekp: EKP
    athlete_sub: str
    place: int | None = Field(default=None, ge=1, description="Место; пусто — участие без места")
    place_to: int | None = Field(default=None, ge=1, description="Конец диапазона мест (например, 5–8)")
    field_size: int = Field(ge=1, description="N — из скольких участников (команд) занято место")
    score: float | None = Field(default=None, ge=0, description="Итоговый балл, если есть")
    role: ResultRole
    team: TeamRef | None = Field(default=None, description="Команда — для командного зачёта")
    team_function: str | None = Field(
        default=None, description="Функциональная роль спортсмена в команде (по заявке участника, не из протокола)"
    )
    status: ResultStatus = ResultStatus.confirmed


class Rank(_Model):
    """Спортивный разряд или звание."""

    id: str
    athlete_sub: str
    code: RankCode
    discipline_code: DisciplineCode | None = Field(
        default=None, description="Дисциплина результата, за который присвоен разряд"
    )
    assigned_at: date = Field(description="Дата присвоения")
    valid_until: date | None = Field(
        default=None,
        description=(
            "Срок действия: КМС — 3 года, I–III и юношеские — 2 года (Положение о ЕВСК, п. 76); "
            "пусто — бессрочно (МС)"
        ),
    )
    order_no: str = Field(description="Номер приказа о присвоении")
    assigned_by: str = Field(description="Кем присвоен")
    basis_ekp: EKP | None = Field(default=None, description="ЕКП соревнования-основания")
    status: RankStatus = RankStatus.active


class RoleBadge(_Model):
    """Судейская или тренерская роль. Только бейдж: в оценку силы не входит."""

    id: str
    athlete_sub: str
    kind: RoleKind
    position: str | None = Field(default=None, description="Должность: судья-эксперт, главный судья, тренер…")
    category: JudgeCategory | None = Field(default=None, description="Судейская категория")
    discipline_code: DisciplineCode | None = None
    since: date | None = Field(default=None, description="С какой даты")


class Achievements(_Model):
    """Достижения спортсмена: результаты, упомянутые в них соревнования, разряды и роли."""

    athlete_sub: str
    as_of: datetime = Field(description="Момент, на который реестр отдал данные")
    source: Source = Source.fsp_api
    results: list[Result] = Field(default_factory=list)
    competitions: list[Competition] = Field(default_factory=list, description="Все соревнования из results")
    ranks: list[Rank] = Field(default_factory=list)
    roles: list[RoleBadge] = Field(default_factory=list)


class CompetitionProtocol(_Model):
    """Соревнование и выписка результатов спортсменов, которые есть в реестре."""

    competition: Competition
    results: list[Result] = Field(default_factory=list)


class RegistrySnapshot(_Model):
    """Полная выгрузка реестра (начальная синхронизация и встроенный резервный провайдер)."""

    contract_version: str = CONTRACT_VERSION
    seed: int | None = None
    data_as_of: date
    athletes: list[Athlete]
    competitions: list[Competition]
    results: list[Result]
    ranks: list[Rank]
    roles: list[RoleBadge]


class ErrorResponse(_Model):
    """Ошибка API реестра."""

    detail: str
