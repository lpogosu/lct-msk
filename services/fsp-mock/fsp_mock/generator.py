"""Детерминированный генератор синтетического реестра ФСП (демо).

Все люди вымышлены: фамилии собраны из ИТ-терминов («Байтов», «Кэшева»), реальных ФИО из протоколов нет
(бриф §2.4: «в seed — только структура»). Номер ЕКП — 16 цифр вида ГГГГ99XXXXXXXXXX: «99» после года
помечает синтетический номер. Один и тот же seed → байт-в-байт тот же реестр.

Что внутри (≈): 60 спортсменов, 25 соревнований в 5 дисциплинах на всех уровнях ЕВСК, отборы и финалы,
команды с капитанами и запасными, разряды по нормам ЕВСК со сроками действия, судьи и тренеры,
спортсмены без результатов, только с разрядом и с результатами, ждущими протокола.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from .contract import (
    AgeGroup,
    Athlete,
    Competition,
    CompetitionFormat,
    JudgeCategory,
    Level,
    ProtocolStatus,
    Rank,
    RankCode,
    RankStatus,
    RegistrySnapshot,
    Result,
    ResultRole,
    ResultStatus,
    RoleBadge,
    RoleKind,
    Stage,
    StatusType,
    TeamMember,
    TeamMemberRole,
    TeamRef,
)

SEED = 20261011
DATA_AS_OF = date(2026, 10, 5)
N_ATHLETES = 60
DEFAULT_PUBLIC_URL = "http://localhost:8001"  # порт мока в docker-compose.yml
_NS = uuid.UUID("6f1c3a52-2d4e-4f7a-9a51-0c0ffee20261")

DISCIPLINES = {
    "algo": "1010011811Я",
    "product": "1010021811Я",
    "bas": "1010031811Я",
    "robotics": "1010041811Я",
    "infosec": "1010051811Я",
}
_DISC_TITLE = {
    "algo": "программирование алгоритмическое",
    "product": "программирование продуктовое",
    "bas": "программирование беспилотных авиационных систем",
    "robotics": "программирование робототехники",
    "infosec": "программирование систем информационной безопасности",
}
_DISC_WEIGHTS = {"algo": 15, "product": 20, "bas": 7, "robotics": 8, "infosec": 10}

REGIONS = [
    ("77", "г. Москва", 14),
    ("78", "г. Санкт-Петербург", 8),
    ("16", "Республика Татарстан", 5),
    ("54", "Новосибирская область", 4),
    ("66", "Свердловская область", 4),
    ("52", "Нижегородская область", 3),
    ("63", "Самарская область", 3),
    ("23", "Краснодарский край", 3),
    ("02", "Республика Башкортостан", 2),
    ("70", "Томская область", 2),
    ("40", "Калужская область", 2),
    ("25", "Приморский край", 2),
]
_REGION_NAME = {code: name for code, name, _ in REGIONS}

# Фамилии из ИТ-терминов: русская морфология, но очевидно вымышленные. Женская форма — «+а».
_SURNAMES = [
    "Байтов", "Битмапов", "Кэшев", "Стеков", "Массивов", "Регистров", "Пикселев", "Векторов", "Тензоров",
    "Хэшев", "Мьютексов", "Семафоров", "Кортежев", "Лямбдин", "Сокетов", "Фреймов", "Модулев",
    "Компиляторов", "Рекурсин", "Итераторов", "Парсеров", "Деплоев", "Коммитов", "Мержев", "Ребейзов",
    "Пайплайнов", "Кластеров", "Шардов", "Индексов", "Курсоров", "Триггеров", "Логгеров", "Бэкапов",
    "Токенов", "Кворумов", "Хостов", "Пакетов", "Кернелов", "Скриптов", "Нейронов", "Буферов",
    "Драйверов", "Патчев", "Релизов", "Тредов", "Флагов", "Шифров", "Роутеров", "Сервлетов", "Дебагов",
]
_MALE = [
    "Алексей", "Артём", "Арсений", "Богдан", "Всеволод", "Глеб", "Даниил", "Егор", "Иван", "Кирилл",
    "Лев", "Максим", "Марк", "Матвей", "Никита", "Олег", "Павел", "Роман", "Семён", "Тимофей", "Фёдор",
    "Ярослав",
]
_FEMALE = [
    "Алиса", "Анна", "Валерия", "Варвара", "Вера", "Дарья", "Ева", "Екатерина", "Злата", "Ксения",
    "Мария", "Милана", "Полина", "София", "Таисия", "Ульяна",
]
_PATRONYMICS = [
    ("Алексеевич", "Алексеевна"), ("Андреевич", "Андреевна"), ("Дмитриевич", "Дмитриевна"),
    ("Игоревич", "Игоревна"), ("Олегович", "Олеговна"), ("Сергеевич", "Сергеевна"),
    ("Павлович", "Павловна"), ("Романович", "Романовна"), ("Викторович", "Викторовна"),
    ("Николаевич", "Николаевна"), ("Евгеньевич", "Евгеньевна"), ("Михайлович", "Михайловна"),
]
_FUNCTIONS = [("backend", 35), ("frontend", 25), ("fullstack", 15), ("ml", 10), ("design", 8), ("pm", 7)]
_TEAM_ADJ = [
    "Ламповый", "Быстрый", "Тихий", "Ночной", "Северный", "Рекурсивный", "Горячий", "Чистый", "Ленивый",
    "Асинхронный", "Белый", "Квантовый", "Стабильный", "Летучий",
]
_TEAM_NOUN = [
    "стек", "кэш", "деплой", "коммит", "байт", "указатель", "фикс", "бэкап", "поток", "токен", "граф",
    "кластер", "релиз", "сокет",
]


@dataclass(frozen=True)
class _Plan:
    key: str
    disc: str
    level: Level
    status: StatusType
    stage: Stage
    age_group: AgeGroup
    fmt: CompetitionFormat
    title: str
    start: date
    days: int
    region: str | None
    city: str | None
    n_range: tuple[int, int]
    team: tuple[int, int] | None = None
    parent: str | None = None
    pending: bool = False
    max_score: float | None = None
    online: bool = False
    knockout: bool = False  # сетка на выбывание: места 5–8, 9–16 делятся
    top_place: int | None = None  # сюжет демо: лучший участник из реестра занимает это место


def _t(disc: str, head: str, tail: str = "") -> str:
    return f"{head} по спортивному программированию — {_DISC_TITLE[disc]}{tail}"


_P, _T = CompetitionFormat.personal, CompetitionFormat.team
_AR, _RG, _IR, _MU, _IN = Level.all_russian, Level.regional, Level.interregional, Level.municipal, Level.international
_CH, _CUP, _FIRST = StatusType.championship, StatusType.cup, StatusType.first
_FIN, _QUAL, _ONE = Stage.final, Stage.qualifier, Stage.single
_AD, _U19 = AgeGroup.adults, AgeGroup.youth_u19

# 25 соревнований: по 5 на дисциплину. Названия — шаблоны ЕКП, события и люди синтетические.
PLANS: list[_Plan] = [
    # алгоритмическое
    _Plan("A1", "algo", _IN, StatusType.other_sport, _ONE, _AD, _P,
          "Международный турнир «Евразийский кубок кода» — программирование алгоритмическое",
          date(2025, 7, 12), 2, "16", "Казань", (150, 220), max_score=12),
    _Plan("A2", "algo", _AR, _CH, _FIN, _AD, _T, _t("algo", "Чемпионат России"),
          date(2025, 11, 21), 3, "77", "Москва", (55, 70), team=(3, 3), max_score=13),
    _Plan("A3", "algo", _AR, _CUP, _QUAL, _AD, _P, _t("algo", "Кубок России", ", отборочный этап"),
          date(2026, 3, 14), 1, None, None, (700, 950), parent="A4", max_score=8, online=True),
    _Plan("A4", "algo", _AR, _CUP, _FIN, _AD, _P, _t("algo", "Кубок России", ", финал"),
          date(2026, 5, 16), 2, "54", "Новосибирск", (70, 90), max_score=12),
    _Plan("A5", "algo", _RG, _CH, _ONE, _AD, _P, _t("algo", "Чемпионат Новосибирской области"),
          date(2024, 4, 20), 1, "54", "Новосибирск", (35, 50), max_score=10),
    # продуктовое
    _Plan("P1", "product", _AR, _CH, _FIN, _AD, _T, _t("product", "Чемпионат России"),
          date(2025, 10, 10), 4, "77", "Москва", (100, 130), team=(3, 5), max_score=100, top_place=1),
    _Plan("P2", "product", _AR, StatusType.physical_culture_event, _ONE, _AD, _T,
          "Всероссийский хакатон «Код города» — программирование продуктовое",
          date(2026, 6, 5), 3, None, None, (180, 240), team=(3, 5), online=True),
    _Plan("P3", "product", _AR, _CUP, _FIN, _AD, _T, _t("product", "Кубок России", ", финал"),
          date(2026, 4, 17), 3, "78", "Санкт-Петербург", (40, 55), team=(3, 5), max_score=100),
    _Plan("P4", "product", _RG, _CH, _ONE, _AD, _T, _t("product", "Чемпионат Республики Татарстан"),
          date(2025, 3, 21), 2, "16", "Казань", (25, 40), team=(3, 5)),
    _Plan("P5", "product", _IR, _CH, _ONE, _AD, _T, _t("product", "Чемпионат Приволжского федерального округа"),
          date(2026, 9, 25), 2, "52", "Нижний Новгород", (35, 45), team=(3, 5), pending=True),
    # БАС
    _Plan("B1", "bas", _AR, _CH, _FIN, _AD, _P, _t("bas", "Чемпионат России"),
          date(2025, 9, 12), 3, "40", "Калуга", (60, 75), max_score=300, top_place=1),
    _Plan("B2", "bas", _AR, _FIRST, _FIN, _U19, _P, _t("bas", "Первенство России"),
          date(2026, 4, 3), 3, "63", "Самара", (45, 60), max_score=300),
    _Plan("B3", "bas", _RG, _CH, _ONE, _AD, _P, _t("bas", "Чемпионат Свердловской области"),
          date(2025, 2, 15), 1, "66", "Екатеринбург", (20, 30)),
    _Plan("B4", "bas", _MU, StatusType.student, _ONE, AgeGroup.juniors_u26, _P,
          "Студенческий турнир г. Томска — программирование беспилотных авиационных систем",
          date(2026, 3, 6), 1, "70", "Томск", (15, 22)),
    _Plan("B5", "bas", _AR, _CUP, _QUAL, _AD, _P, _t("bas", "Кубок России", ", отборочный этап"),
          date(2026, 9, 24), 1, None, None, (120, 160), pending=True, online=True),
    # робототехника
    _Plan("R1", "robotics", _AR, _CH, _FIN, _AD, _T, _t("robotics", "Чемпионат России"),
          date(2025, 12, 5), 3, "77", "Москва", (32, 40), team=(2, 4), knockout=True),
    _Plan("R2", "robotics", _IR, _CH, _ONE, _AD, _T, _t("robotics", "Чемпионат Северо-Западного федерального округа"),
          date(2026, 2, 20), 2, "78", "Санкт-Петербург", (24, 32), team=(2, 4)),
    _Plan("R3", "robotics", _RG, _CH, _ONE, _AD, _T, _t("robotics", "Чемпионат Калужской области"),
          date(2024, 11, 15), 1, "40", "Калуга", (12, 18), team=(2, 4)),
    _Plan("R4", "robotics", _MU, _FIRST, _ONE, _U19, _P, _t("robotics", "Первенство г. Самары"),
          date(2025, 5, 17), 1, "63", "Самара", (18, 26)),
    _Plan("R5", "robotics", _IN, StatusType.other_sport, _ONE, _AD, _T,
          "Международный турнир «Северное сияние» — программирование робототехники",
          date(2024, 8, 22), 3, "78", "Санкт-Петербург", (30, 38), team=(2, 4)),
    # ИБ
    _Plan("I1", "infosec", _AR, _CH, _FIN, _AD, _T, _t("infosec", "Чемпионат России"),
          date(2025, 11, 7), 3, "77", "Москва", (40, 50), team=(3, 5), top_place=2),
    _Plan("I2", "infosec", _AR, _FIRST, _FIN, _U19, _P, _t("infosec", "Первенство России"),
          date(2026, 4, 24), 2, "78", "Санкт-Петербург", (80, 100), max_score=5000),
    _Plan("I3", "infosec", _AR, _CUP, _QUAL, _AD, _T, _t("infosec", "Кубок России", ", отборочный этап"),
          date(2026, 2, 7), 1, None, None, (250, 320), team=(3, 5), parent="I4", online=True),
    _Plan("I4", "infosec", _AR, _CUP, _FIN, _AD, _T, _t("infosec", "Кубок России", ", финал"),
          date(2026, 5, 29), 2, "77", "Москва", (25, 32), team=(3, 5)),
    _Plan("I5", "infosec", _RG, _CH, _ONE, _AD, _P, _t("infosec", "Чемпионат Приморского края"),
          date(2026, 9, 27), 1, "25", "Владивосток", (20, 30), pending=True),
]

_LEVEL_ORDER = {_MU: 0, _RG: 1, _IR: 2, _AR: 3, _IN: 4}
# Только разряды норм ЕВСК по спортивному программированию (Приложение № 24 к приказу Минспорта № 450
# от 22.04.2024): МС, КМС, I–III и юношеские. МСМК в этих нормах нет — генератор его не выдаёт.
_RANK_ORDER = {
    RankCode.III_y: 0, RankCode.II_y: 1, RankCode.I_y: 2, RankCode.III: 3, RankCode.II: 4,
    RankCode.I: 5, RankCode.KMS: 6, RankCode.MS: 7,
}
_RANK_MIN_AGE = {RankCode.MS: 16, RankCode.KMS: 15}  # остальные — с 14 (бриф §2.2)
# Сроки действия (Положение о ЕВСК, приказ Минспорта № 173 от 03.03.2025, п. 76): КМС — 3 года,
# I–III и юношеские — 2 года, МС — бессрочно. Совпадение с config/fsp.yaml проверяет tests/test_fsp_mock.py.
RANK_VALID_YEARS = {
    RankCode.KMS: 3, RankCode.I: 2, RankCode.II: 2, RankCode.III: 2,
    RankCode.I_y: 2, RankCode.II_y: 2, RankCode.III_y: 2,
}
_ASSIGNED_BY = {
    RankCode.MS: "Министерство спорта Российской Федерации",
    RankCode.KMS: "Орган исполнительной власти субъекта РФ в области физической культуры и спорта",
    RankCode.I: "Орган исполнительной власти субъекта РФ в области физической культуры и спорта",
    RankCode.II: "Орган местного самоуправления в области физической культуры и спорта",
    RankCode.III: "Орган местного самоуправления в области физической культуры и спорта",
}


@dataclass
class _Person:
    athlete: Athlete
    female: bool
    disciplines: tuple[str, ...]
    strength: float
    function: str
    scenario: str = "regular"  # regular | no_results | awaiting_only | rank_only
    guaranteed: str | None = None
    results: list[tuple[Result, Competition]] = field(default_factory=list)

    @property
    def sub(self) -> str:
        return self.athlete.sub

    def age_on(self, d: date) -> int:
        b = self.athlete.birth_date
        return d.year - b.year - ((d.month, d.day) < (b.month, b.day))


def _uid(*parts: object) -> str:
    return str(uuid.uuid5(_NS, ":".join(str(p) for p in parts)))


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 февраля
        return d.replace(year=d.year + years, day=28)


def _age_ok(p: _Person, plan: _Plan) -> bool:
    age = p.age_on(plan.start)
    if plan.age_group == AgeGroup.youth_u19:
        return 12 <= age < 19
    if plan.age_group == AgeGroup.juniors_u26:
        return 16 <= age <= 25
    return age >= 14


def _entry_prob(plan: _Plan, s: float) -> float:
    """Чем выше уровень, тем сильнее нужно быть, чтобы туда попасть."""
    if plan.level == _IN:
        return 0.7 if s >= 0.72 else 0.0
    if plan.level == _AR and plan.stage == _FIN:
        return 0.65 if s >= 0.5 else 0.05
    if plan.level == _AR:
        return 0.55 if s >= 0.2 else 0.2
    if plan.level == _IR:
        return 0.45
    if plan.level == _RG:
        return 0.5
    return 0.55 if s < 0.65 else 0.15


def _make_people(rng: random.Random) -> list[_Person]:
    people: list[_Person] = []
    surnames = rng.sample(_SURNAMES, len(_SURNAMES))
    used: set[tuple[str, str]] = set()
    special = rng.sample(range(N_ATHLETES), 11)
    scenario = {i: "no_results" for i in special[:6]}
    scenario.update({i: "awaiting_only" for i in special[6:9]})
    scenario.update({i: "rank_only" for i in special[9:]})
    awaiting_disc = iter(["product", "bas", "infosec"])
    rank_only_disc = iter(["algo", "infosec"])
    disc_keys = list(_DISC_WEIGHTS)
    for i in range(N_ATHLETES):
        sc = scenario.get(i, "regular")
        female = rng.random() < 0.27
        root = surnames[i % len(surnames)]
        last = root + "а" if female else root
        first = rng.choice(_FEMALE if female else _MALE)
        while (last, first) in used:
            first = rng.choice(_FEMALE if female else _MALE)
        used.add((last, first))
        middle = rng.choice(_PATRONYMICS)[1 if female else 0]
        minor = sc == "regular" and rng.random() < 0.2
        years = rng.randint(14, 17) if minor else rng.randint(19, 33)
        birth = DATA_AS_OF - timedelta(days=years * 365 + rng.randint(30, 330))
        code = rng.choices([r[0] for r in REGIONS], weights=[r[2] for r in REGIONS])[0]
        sub = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        if sc == "awaiting_only":
            primary = next(awaiting_disc)
        elif sc == "rank_only":
            primary = next(rank_only_disc)
        else:
            primary = rng.choices(disc_keys, weights=list(_DISC_WEIGHTS.values()))[0]
        discs = (primary,)
        if rng.random() < 0.3:
            discs += (rng.choice([d for d in disc_keys if d != primary]),)
        org = f"Региональное отделение ФСП — {_REGION_NAME[code]}" if rng.random() < 0.7 else None
        athlete = Athlete(
            sub=sub, last_name=last, first_name=first, middle_name=middle, birth_date=birth,
            region_code=code, region_name=_REGION_NAME[code], organization=org,
        )
        people.append(
            _Person(
                athlete=athlete, female=female, disciplines=discs,
                strength=round(rng.betavariate(2.2, 2.0), 4),
                function=rng.choices([f for f, _ in _FUNCTIONS], weights=[w for _, w in _FUNCTIONS])[0],
                scenario=sc,
            )
        )
    return people


def _assign_guaranteed(people: list[_Person]) -> None:
    """Каждому «обычному» спортсмену — хотя бы одно доступное соревнование основной дисциплины."""
    for p in people:
        if p.scenario != "regular":
            continue
        options = [pl for pl in PLANS if pl.disc == p.disciplines[0] and not pl.pending and _age_ok(p, pl)]
        options.sort(key=lambda pl: (_LEVEL_ORDER[pl.level], pl.stage != _QUAL, pl.start))
        if options:
            p.guaranteed = options[0].key


def _form_teams(entrants: list[_Person], lo: int, hi: int, rng: random.Random) -> list[list[_Person]]:
    order = sorted(entrants, key=lambda a: (-(a.strength + rng.gauss(0, 0.15)), a.sub))
    n = len(order)
    if n == 0:
        return []
    k = max(1, round(n / ((lo + hi) / 2)))
    while k > 1 and n / k < lo:
        k -= 1
    while n / k > hi:
        k += 1
    sizes = [n // k + (1 if i < n % k else 0) for i in range(k)]
    teams, pos = [], 0
    for size in sizes:
        teams.append(order[pos : pos + size])
        pos += size
    return teams


def _team_names(rng: random.Random) -> list[str]:
    names = [f"{a} {n}" for a in _TEAM_ADJ for n in _TEAM_NOUN]
    rng.shuffle(names)
    return names


def _simulate(people: list[_Person], rng: random.Random, public_url: str) -> list[Competition]:
    competitions: list[Competition] = []
    ekp_by_key: dict[str, str] = {}
    for plan in PLANS:  # ЕКП заранее: отбор ссылается на финал
        while True:
            ekp = f"{plan.start.year}99{rng.randrange(10**10):010d}"
            if ekp not in ekp_by_key.values():
                break
        ekp_by_key[plan.key] = ekp
    team_names = iter(_team_names(rng))

    for plan in sorted(PLANS, key=lambda pl: (pl.start, pl.key)):
        ekp = ekp_by_key[plan.key]
        entrants: list[_Person] = []
        for p in people:
            if plan.disc not in p.disciplines or not _age_ok(p, plan):
                continue
            if p.scenario in ("no_results", "rank_only"):
                continue
            if p.scenario == "awaiting_only":
                if plan.pending:
                    entrants.append(p)
                continue
            if p.guaranteed == plan.key:
                entrants.append(p)
                continue
            prob = _entry_prob(plan, p.strength)
            if plan.disc != p.disciplines[0]:
                prob *= 0.5
            if plan.pending:
                prob *= 0.5
            if rng.random() < prob:
                entrants.append(p)
        if not entrants and not plan.pending:
            # У каждого соревнования в реестре должен быть хоть кто-то: зовём «гостей» из других дисциплин.
            pool = [p for p in people if p.scenario == "regular" and _age_ok(p, plan)]
            entrants = rng.sample(pool, min(len(pool), plan.team[0] if plan.team else 2))

        if plan.team:
            units = _form_teams(entrants, plan.team[0], plan.team[1], rng)
        else:
            units = [[p] for p in sorted(entrants, key=lambda a: a.sub)]
        perf = [
            min(0.995, max(0.01, sum(m.strength for m in u) / len(u) + rng.gauss(0, 0.07))) for u in units
        ]
        n = max(rng.randint(*plan.n_range), len(units) + 2)
        raw = [1 + int(((1 - pf) ** 2) * (n - 1)) for pf in perf]
        if plan.top_place and units:
            raw[max(range(len(units)), key=lambda i: perf[i])] = plan.top_place
        order = sorted(range(len(units)), key=lambda i: (raw[i], -perf[i]))
        places: dict[int, tuple[int, int | None]] = {}
        prev = 0
        for i in order:
            pl = max(raw[i], prev + 1)
            prev = pl
            places[i] = (pl, None)
        n = max(n, prev)
        if plan.knockout:
            for i, (pl, _) in places.items():
                if 5 <= pl <= 8:
                    places[i] = (5, 8)
                elif 9 <= pl <= 16:
                    places[i] = (9, 16)
        scores: dict[int, float] = {}
        if plan.max_score:
            last = plan.max_score
            for i in order:
                val = min(last, plan.max_score * min(1.0, max(0.0, perf[i] * 0.95 + 0.03)))
                val = float(round(val)) if plan.max_score <= 20 else round(val, 1)
                scores[i] = last = val

        team_avg = (plan.team[0] + plan.team[1]) / 2 if plan.team else 1
        comp = Competition(
            ekp=ekp,
            title=f"{plan.title} ({plan.start.year})",
            discipline_code=DISCIPLINES[plan.disc],
            level=plan.level,
            status_type=plan.status,
            age_group=plan.age_group,
            stage=plan.stage,
            parent_ekp=ekp_by_key[plan.parent] if plan.parent else None,
            format=plan.fmt,
            online=plan.online,
            start_date=plan.start,
            end_date=plan.start + timedelta(days=plan.days - 1),
            region_code=plan.region,
            city=plan.city,
            participants_count=round(n * team_avg) if plan.team else n,
            teams_count=n if plan.team else None,
            max_score=plan.max_score,
            protocol_status=ProtocolStatus.pending if plan.pending else ProtocolStatus.published,
            protocol_url=None if plan.pending else f"{public_url.rstrip('/')}/protocols/{ekp}",
        )
        competitions.append(comp)

        for idx, unit in enumerate(units):
            place, place_to = places[idx]
            team_ref = None
            roles: dict[str, ResultRole] = {}
            if plan.team:
                members = []
                for j, m in enumerate(unit):
                    if j == 0:
                        role = TeamMemberRole.captain
                    elif j == len(unit) - 1 and len(unit) == plan.team[1] and rng.random() < 0.5:
                        role = TeamMemberRole.reserve
                    else:
                        role = TeamMemberRole.participant
                    roles[m.sub] = ResultRole(role.value)
                    members.append(
                        TeamMember(
                            athlete_sub=m.sub, role=role,
                            team_function=m.function if plan.disc == "product" else None,
                        )
                    )
                team_ref = TeamRef(id=_uid("team", ekp, idx), name=next(team_names), members=members)
            for m in unit:
                result = Result(
                    id=_uid("result", ekp, m.sub),
                    ekp=ekp,
                    athlete_sub=m.sub,
                    place=place,
                    place_to=place_to,
                    field_size=n,
                    score=scores.get(idx),
                    role=roles.get(m.sub, ResultRole.personal),
                    team=team_ref,
                    team_function=m.function if plan.disc == "product" else None,
                    status=ResultStatus.awaiting_protocol if plan.pending else ResultStatus.confirmed,
                )
                m.results.append((result, comp))
    competitions.sort(key=lambda c: (c.start_date, c.ekp))
    return competitions


def _earned_rank(comp: Competition, place: int) -> RankCode | None:
    """Упрощённые нормы ЕВСК (бриф §2.2, Приложение № 24): место → разряд."""
    if comp.stage == Stage.qualifier:
        return None
    table: list[tuple[int, RankCode]] = []
    if comp.level == Level.international:
        table = [(1, RankCode.MS), (8, RankCode.KMS)]
    elif comp.level == Level.all_russian:
        table = {
            StatusType.championship: [(1, RankCode.MS), (4, RankCode.KMS), (9, RankCode.I)],
            StatusType.cup: [(3, RankCode.KMS), (8, RankCode.I)],
            StatusType.first: [(1, RankCode.KMS), (8, RankCode.I)],
            StatusType.other_sport: [(2, RankCode.KMS), (4, RankCode.I)],
            StatusType.physical_culture_event: [(2, RankCode.II), (6, RankCode.III)],
        }.get(comp.status_type, [])
    elif comp.level == Level.interregional:
        table = [(2, RankCode.I), (4, RankCode.II), (8, RankCode.III)]
    elif comp.level == Level.regional:
        table = [(2, RankCode.I), (4, RankCode.II), (6, RankCode.III)]
    elif comp.level == Level.municipal and comp.status_type == StatusType.championship:
        table = [(2, RankCode.II), (4, RankCode.III)]
    for max_place, code in table:
        if place <= max_place:
            return code
    return None


def _ranks(people: list[_Person], rng: random.Random) -> list[Rank]:
    ranks: list[Rank] = []
    for p in people:
        best: tuple[RankCode, Competition, date] | None = None
        for result, comp in p.results:
            if result.status != ResultStatus.confirmed or result.place is None:
                continue
            code = _earned_rank(comp, result.place_to or result.place)
            if code is None or p.age_on(comp.end_date) < _RANK_MIN_AGE.get(code, 14):
                continue
            assigned = comp.end_date + timedelta(days=rng.randint(45, 120))
            if assigned > DATA_AS_OF:
                continue  # представление ещё не рассмотрено
            if best is None or (_RANK_ORDER[code], assigned) > (_RANK_ORDER[best[0]], best[2]):
                best = (code, comp, assigned)
        if best is not None:
            code, comp, assigned = best
            ranks.append(_rank(p, code, assigned, comp.discipline_code, comp.ekp, rng))
    # Только разряд, результатов в реестре нет (норматив выполнен до подключения реестра).
    only = [p for p in people if p.scenario == "rank_only"]
    plan = [(RankCode.KMS, date(2025, 3, 14)), (RankCode.I, date(2025, 6, 2))]
    for p, (code, assigned) in zip(only, plan, strict=True):
        ranks.append(_rank(p, code, assigned, DISCIPLINES[p.disciplines[0]], None, rng))
    # Истёкший разряд у одного «обычного» спортсмена: старый I разряд 2022 года.
    holders = {r.athlete_sub for r in ranks}
    old = date(2022, 2, 10)
    veteran = next(
        (p for p in people
         if p.scenario == "regular" and p.results and p.sub not in holders and p.age_on(old) >= 14),
        None,
    )
    if veteran is not None:
        ranks.append(_rank(veteran, RankCode.I, old, DISCIPLINES[veteran.disciplines[0]], None, rng))
    ranks.sort(key=lambda r: (r.athlete_sub, r.assigned_at, r.id))
    return ranks


def _rank(p: _Person, code: RankCode, assigned: date, disc: str, basis: str | None, rng: random.Random) -> Rank:
    years = RANK_VALID_YEARS.get(code)
    valid_until = _add_years(assigned, years) if years else None
    return Rank(
        id=_uid("rank", p.sub, code.value, assigned.isoformat()),
        athlete_sub=p.sub,
        code=code,
        discipline_code=disc,
        assigned_at=assigned,
        valid_until=valid_until,
        order_no=f"ДЕМО-{assigned.year}-{rng.randint(100, 999)}",
        assigned_by=_ASSIGNED_BY[code],
        basis_ekp=basis,
        status=RankStatus.active if valid_until is None or valid_until >= DATA_AS_OF else RankStatus.expired,
    )


def _roles(people: list[_Person], rng: random.Random) -> list[RoleBadge]:
    adults = [p for p in people if p.scenario == "regular" and p.age_on(DATA_AS_OF) >= 24 and p.results]
    picked = rng.sample(adults, 3)
    # Тренер без собственных результатов: только бейдж, F = 0 (DECISIONS п.2).
    coach_no_results = max(
        (p for p in people if p.scenario == "no_results"), key=lambda p: (p.age_on(DATA_AS_OF), p.sub)
    )
    specs = [
        (picked[0], RoleKind.judge, "судья-эксперт", JudgeCategory.K1, "product", date(2024, 3, 1)),
        (picked[1], RoleKind.judge, "главный судья", JudgeCategory.VK, "algo", date(2023, 9, 15)),
        (picked[2], RoleKind.coach, "тренер", None, "robotics", date(2024, 1, 10)),
        (coach_no_results, RoleKind.coach, "тренер", None, "algo", date(2022, 9, 1)),
    ]
    return [
        RoleBadge(
            id=_uid("role", p.sub, kind.value, disc), athlete_sub=p.sub, kind=kind, position=position,
            category=category, discipline_code=DISCIPLINES[disc], since=since,
        )
        for p, kind, position, category, disc, since in specs
    ]


def build_dataset(seed: int = SEED, public_url: str = DEFAULT_PUBLIC_URL) -> RegistrySnapshot:
    """Весь демо-реестр. Детерминирован по seed (и не зависит от текущей даты)."""
    rng = random.Random(seed)
    people = _make_people(rng)
    _assign_guaranteed(people)
    competitions = _simulate(people, rng, public_url)
    ranks = _ranks(people, rng)
    roles = _roles(people, rng)
    comps = {c.ekp: c for c in competitions}
    results = [r for p in people for r, _ in p.results]
    results.sort(key=lambda r: (comps[r.ekp].start_date, r.ekp, r.place or 10**9, r.athlete_sub))
    return RegistrySnapshot(
        seed=seed,
        data_as_of=DATA_AS_OF,
        athletes=[p.athlete for p in people],
        competitions=competitions,
        results=results,
        ranks=ranks,
        roles=roles,
    )
