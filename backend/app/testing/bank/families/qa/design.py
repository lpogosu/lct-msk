"""qa_testing, тест-дизайн: граничные значения, классы эквивалентности, серьёзность и приоритет дефекта.

Граничные значения: ключ — точный решатель по ПОКАЗАННОЙ спецификации (разбор текста регуляркой, потом арифметика
границ); дистракторы — тот же решатель с включённым заблуждением; двойник в буфере меняет одно число в спецификации
(families/_scenario.py). Классы эквивалентности и серьёзность/приоритет — выбор варианта: ключ считается по параметрам
сценария формулой из условия задачи.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from app.testing.bank.base import Instance, register
from app.testing.bank.families.qa._common import (
    MODULE,
    SPECS,
    BankFamily,
    GenerationRetry,
    Scenario,
    ScenarioError,
    ScenarioFamily,
    plural,
)

# ============================================================== Стажёр: граничные значения

_FIELDS = [
    "Возраст клиента", "Количество товаров в заказе", "Длина пароля", "Скидка по промокоду, %",
    "Число гостей в бронировании", "Размер вложения, МБ", "Оценка курса", "Срок кредита, мес.",
    "Вес посылки, кг", "Число попыток ввода кода",
]
_BVA_PHRASES = {
    "incl": "от {lo} до {hi} включительно",
    "excl": "больше {lo} и меньше {hi}",
    "mixl": "не меньше {lo} и меньше {hi}",
    "mixr": "больше {lo} и не больше {hi}",
}
_BVA_RE = re.compile(
    r"(?P<lk>от|не меньше|больше)\s+(?P<lo>\d+)\s+(?:(?P<to>до)|и\s+(?P<hk>не больше|меньше))\s+(?P<hi>\d+)"
)


def _bva_valid_range(code: str, *, misread: bool = False) -> tuple[int, int]:
    """Границы допустимого диапазона по тексту спецификации; misread — «больше N» понято как «не меньше N»."""
    m = _BVA_RE.search(code)
    if not m:
        raise ScenarioError("не найден диапазон")
    lo, hi = int(m["lo"]), int(m["hi"])
    lo_incl = m["lk"] in ("от", "не меньше")
    hi_incl = bool(m["to"]) or m["hk"] == "не больше"
    if misread:
        lo_incl = hi_incl = True
    lo_v = lo if lo_incl else lo + 1
    hi_v = hi if hi_incl else hi - 1
    if hi_v - lo_v < 2:
        raise ScenarioError("слишком узкий диапазон")
    return lo_v, hi_v


@register
class BoundaryValues(ScenarioFamily):
    id = "qa.bva.points"
    title = "Граничные значения: тестовые значения для поля"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "intern"
    position = 0.3
    time_limit_s = 90
    is_anchor = True
    answer_kind = "ints"
    language = "text"
    measures = "Анализ граничных значений: последнее допустимое и ближайшее недопустимое значение на каждой границе."
    misconceptions = {
        "strict_bound_as_valid": "«больше 18» — значит 19 и выше: граница 18 сама недопустима, допустимое значение "
        "у границы — 19",
        "valid_only": "на границе проверяют обе стороны: и допустимое значение, и ближайшее недопустимое",
        "invalid_only": "недопустимые значения проверяют, но без допустимого значения у границы не видно, "
        "где граница на самом деле",
        "three_point": "по условию берут по два значения на границу (последнее допустимое и ближайшее "
        "недопустимое), а не по три",
    }
    answer_hint = "Введите тестовые значения через пробел, по возрастанию."

    def make(self, rng: random.Random) -> Scenario:
        kind = rng.choice(list(_BVA_PHRASES))
        lo = rng.randint(2, 60)
        hi = lo + rng.randint(6, 140)
        field = rng.choice(_FIELDS)
        phrase = _BVA_PHRASES[kind].format(lo=lo, hi=hi)
        code = f"Поле «{field}» — целое число.\nДопустимые значения: {phrase}."
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "Тестировщик проверяет поле формы по спецификации. По методу анализа граничных значений на каждой границе "
            "берут два значения: последнее допустимое и ближайшее недопустимое. Какие значения нужно ввести?"
        )

    def solve(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        lo_v, hi_v = _bva_valid_range(code, misread=mutant == "strict_bound_as_valid")
        if mutant == "strict_bound_as_valid":
            if _bva_valid_range(code) == (lo_v, hi_v):
                return None  # диапазон с включёнными границами: заблуждение неприменимо
            return f"{lo_v - 1} {lo_v} {hi_v} {hi_v + 1}"
        if mutant == "valid_only":
            return f"{lo_v} {hi_v}"
        if mutant == "invalid_only":
            return f"{lo_v - 1} {hi_v + 1}"
        if mutant == "three_point":
            return f"{lo_v - 1} {lo_v} {lo_v + 1} {hi_v - 1} {hi_v} {hi_v + 1}"
        return f"{lo_v - 1} {lo_v} {hi_v} {hi_v + 1}"

    def why(self, sc: Scenario, raw_key: str) -> str:
        lo_v, hi_v = _bva_valid_range(sc.code)
        return (
            f"Допустимый диапазон — от {lo_v} до {hi_v} (строгие неравенства «больше»/«меньше» исключают само число из "
            f"текста). Нижняя граница: {lo_v - 1} (недопустимо) и {lo_v} (допустимо); верхняя: {hi_v} (допустимо) "
            f"и {hi_v + 1} (недопустимо)."
        )


# ============================================================== Junior: классы эквивалентности (выбор)


@dataclass(frozen=True)
class _Field:
    name: str
    kind: str  # range | enum | optional | required
    text: str
    valid: int
    invalid: int


_RANGE_FIELDS = [
    ("Количество товаров", "целое от {lo} до {hi}"), ("Возраст покупателя", "целое от {lo} до {hi}"),
    ("Срок аренды, дней", "целое от {lo} до {hi}"), ("Число гостей", "целое от {lo} до {hi}"),
    ("Сумма перевода, руб.", "целое от {lo} до {hi}"), ("Оценка", "целое от {lo} до {hi}"),
]
_ENUM_FIELDS = [
    ("Способ доставки", ["курьер", "самовывоз", "почтомат", "экспресс"]),
    ("Тип тарифа", ["базовый", "стандарт", "премиум", "корпоративный"]),
    ("Способ оплаты", ["карта", "СБП", "наличные", "рассрочка"]),
    ("Роль пользователя", ["гость", "клиент", "менеджер", "администратор"]),
]
_OPT_FIELDS = [
    ("Промокод", "необязательное: пусто или ровно {n} {w}", ("символ", "символа", "символов")),
    ("Комментарий к заказу", "необязательное: пусто или до {n} символов", None),
    ("Индекс", "необязательное: пусто или {n} {w}", ("цифра", "цифры", "цифр")),
]
_REQ_FIELDS = [("Имя получателя", "обязательное, непустая строка"), ("Адрес", "обязательное, непустая строка")]


def _make_fields(rng: random.Random) -> list[_Field]:
    """3–5 полей: всегда диапазон и список (разные числа классов), остальные — по случаю."""
    kinds = ["range", "enum", *rng.sample(["range", "enum", "optional", "required"], rng.randint(1, 3))]
    fields: list[_Field] = []
    used: set[str] = set()
    for kind in kinds:
        if kind == "range":
            name, tpl_ = rng.choice([f for f in _RANGE_FIELDS if f[0] not in used])
            lo = rng.randint(1, 30)
            fields.append(_Field(name, kind, tpl_.format(lo=lo, hi=lo + rng.randint(5, 90)), 1, 2))
        elif kind == "enum":
            name, vals = rng.choice([f for f in _ENUM_FIELDS if f[0] not in used])
            k = rng.randint(2, 4)
            text = "одно из значений: " + ", ".join(vals[:k]) + " (для каждого своя логика)"
            fields.append(_Field(name, kind, text, k, 1))
        elif kind == "optional":
            name, tpl_, forms = rng.choice([f for f in _OPT_FIELDS if f[0] not in used])
            n = rng.randint(4, 12)
            word = plural(n, *forms).split(" ", 1)[1] if forms else ""
            fields.append(_Field(name, kind, tpl_.format(n=n, w=word), 2, 1))
        else:
            name, text = rng.choice([f for f in _REQ_FIELDS if f[0] not in used])
            fields.append(_Field(name, kind, text, 1, 1))
        used.add(fields[-1].name)
    rng.shuffle(fields)
    return fields


@register
class EquivalenceMinTests(BankFamily):
    id = "qa.equiv.min_tests"
    title = "Классы эквивалентности: минимальный набор тестов"
    specs = SPECS
    module = MODULE
    format = "mcq_concept"
    target_grade = "junior"
    position = -0.2
    c = 0.25
    time_limit_s = 120
    is_anchor = True
    answer_kind = "choice"
    measures = (
        "Разбиение на классы эквивалентности: допустимые классы разных полей объединяются в один тест, "
        "недопустимое значение берётся по одному на тест."
    )
    misconceptions = {
        "forget_invalid": "недопустимые классы тоже нужно проверить — каждый отдельным тестом: ошибка поля видна "
        "только когда остальные значения допустимы",
        "no_combining": "допустимые классы разных полей можно объединять в одном тесте: "
        "их нужно столько, сколько у самого «богатого» поля",
        "combine_invalid": "недопустимые значения нельзя объединять: при двух неверных полях в одном тесте не "
        "понять, какое из них вызвало отказ",
        "merge_range_sides": "у диапазона два недопустимых класса — ниже нижней границы и выше верхней, "
        "это разные тесты",
    }

    def build(self, rng: random.Random, seed: int) -> Instance:
        fields = _make_fields(rng)
        valid = [f.valid for f in fields]
        invalid = [f.invalid for f in fields]
        key = max(valid) + sum(invalid)
        n_range = sum(1 for f in fields if f.kind == "range")
        wrongs = [
            (str(max(valid)), "forget_invalid"),
            (str(sum(valid) + sum(invalid)), "no_combining"),
            (str(max(valid) + max(invalid)), "combine_invalid"),
            (str(key - n_range), "merge_range_sides"),
        ]
        rng.shuffle(wrongs)
        code = "Форма «Оформление заявки»\n" + "\n".join(f"{i}. {f.name} — {f.text}." for i, f in enumerate(fields, 1))
        return self.choice_instance(
            seed,
            rng,
            prompt=(
                "Классы эквивалентности: у диапазона один допустимый и два недопустимых (ниже и выше); у списка из k "
                "значений с разной логикой — k допустимых и один недопустимый; у необязательного поля с форматом — "
                "два допустимых (пусто и верный формат) и один недопустимый; у обязательной строки — допустимый "
                "и недопустимый (пустая). Допустимые классы разных полей можно объединять в одном тесте, недопустимое "
                "значение в тесте берут только одно. Какое минимальное число тестов покрывает все классы?"
            ),
            correct=str(key),
            wrongs=wrongs,
            code=code,
            language="text",
            why=(
                f"Допустимые классы объединяются: нужно столько тестов, сколько допустимых классов у самого "
                f"«богатого» поля — {max(valid)}. Недопустимые значения проверяют по одному: {sum(invalid)}. "
                f"Итого {max(valid)} + {sum(invalid)} = {key}."
            ),
        )


# ============================================================== Junior: серьёзность и приоритет (mcq_concept)

_SEVERITY = ["Critical", "Major", "Minor", "Trivial"]
_PRIORITY = ["High", "Medium", "Low"]
_NAIVE_PRIORITY = {"Critical": "High", "Major": "High", "Minor": "Medium", "Trivial": "Low"}
_NAIVE_SEVERITY = {"High": "Critical", "Medium": "Major", "Low": "Minor"}

_IMPACT = {
    "Critical": [
        "при оплате картой деньги списываются дважды, а заказ создаётся один",
        "после обновления профиля пропадают сохранённые адреса доставки пользователя",
        "приложение закрывается при попытке открыть корзину, обойти это нельзя",
        "кнопка «Подтвердить заказ» не реагирует, оформить покупку невозможно",
    ],
    "Major": [
        "фильтр каталога по цене не работает, но товары можно отсортировать по цене вручную",
        "письмо с подтверждением заказа приходит с задержкой в несколько часов, статус виден в кабинете",
        "поиск по каталогу не находит товары по артикулу, по названию находит",
        "в личном кабинете не открывается история заказов за прошлый год, за текущий — открывается",
    ],
    "Minor": [
        "кнопка «Назад» в мастере заказа возвращает на шаг раньше нужного, введённые данные сохраняются",
        "поле даты не принимает ввод с клавиатуры, но календарь работает",
        "в таблице заказов столбец суммы выровнен по левому краю, а не по правому",
        "после отправки формы поля очищаются не сразу, а через секунду",
    ],
    "Trivial": [
        "в тексте подвала опечатка в слове «контакты»",
        "иконка корзины на два пикселя ниже соседних иконок",
        "в подсказке к полю пропущена запятая",
        "цвет ссылки в футере чуть отличается от оттенка в макете",
    ],
}
_CONTEXT = {
    "High": [
        "Завтра запуск рекламной кампании: на эту страницу придёт основной поток покупателей",
        "Дефект затрагивает почти всех клиентов, релиз с акцией выходит в понедельник",
        "Пилотный клиент увидит этот экран на демонстрации в четверг",
    ],
    "Medium": [
        "Релиз через две недели, проблема затрагивает часть пользователей",
        "Экран используют несколько сотен клиентов в месяц, срочных обязательств нет",
        "Дефект нашли на регрессии в середине спринта, до релиза есть время",
    ],
    "Low": [
        "Раздел открывают пять сотрудников поддержки, релиз не горит",
        "Страницей почти не пользуются, в планах её переработка в следующем квартале",
        "Экран доступен только администраторам и нужен раз в месяц",
    ],
}


@register
class BugSeverityPriority(BankFamily):
    id = "qa.bug.severity_priority"
    title = "Баг-репорт: серьёзность и приоритет"
    specs = SPECS
    module = MODULE
    format = "mcq_concept"
    target_grade = "junior"
    position = -0.6
    c = 0.25
    time_limit_s = 90
    answer_kind = "choice"
    measures = "Различие серьёзности (влияние на продукт) и приоритета (срочность для бизнеса) при оформлении дефекта."
    misconceptions = {
        "priority_follows_severity": "приоритет определяет бизнес-контекст (сроки, аудитория), а не только тяжесть "
        "дефекта: опечатка перед запуском кампании срочная, а тяжёлый дефект в редком разделе может подождать",
        "severity_follows_priority": "серьёзность описывает влияние дефекта на продукт и не зависит от срочности: "
        "опечатка остаётся Trivial, даже если её надо чинить немедленно",
        "severity_overrated": "серьёзность не растёт от шума вокруг дефекта: если функция работает или есть обход, "
        "она ниже",
        "severity_underrated": "серьёзность определяют последствия: потеря данных, двойное списание или невозможность "
        "пройти ключевой сценарий — это максимум, даже если дефект редкий",
    }

    def build(self, rng: random.Random, seed: int) -> Instance:
        sev = rng.choice(_SEVERITY)
        pri = rng.choice(_PRIORITY)
        impact = rng.choice(_IMPACT[sev])
        context = rng.choice(_CONTEXT[pri])
        i = _SEVERITY.index(sev)

        def pair(s: str, p: str) -> str:
            return f"{s} / {p}"

        correct = pair(sev, pri)
        over = _SEVERITY[i - 1] if i > 0 else _SEVERITY[1]
        under = _SEVERITY[i + 1] if i < 3 else _SEVERITY[2]
        wrongs = [
            (pair(sev, _NAIVE_PRIORITY[sev]), "priority_follows_severity"),
            (pair(_NAIVE_SEVERITY[pri], pri), "severity_follows_priority"),
            (pair(over, pri), "severity_overrated" if i > 0 else "severity_underrated"),
            (pair(under, pri), "severity_underrated" if i < 3 else "severity_overrated"),
        ]
        wrongs = [(t, tag) for t, tag in wrongs if t != correct]
        if len({t for t, _ in wrongs}) < 3:
            raise GenerationRetry
        rng.shuffle(wrongs)
        code = f"Дефект: {impact}.\nКонтекст: {context}."
        return self.choice_instance(
            seed,
            rng,
            prompt=(
                "Серьёзность (severity): Critical — ключевой сценарий невозможен или портятся данные; Major — функция "
                "работает неверно, но есть обход; Minor — мелкое неудобство, функция работает; Trivial — косметика. "
                "Приоритет (priority) — как срочно чинить с точки зрения бизнеса: High — мешает релизу, кампании или "
                "большинству клиентов в ближайшие дни; Medium — в плановом порядке в этом спринте; Low — когда дойдут "
                "руки. Какую пару «серьёзность / приоритет» нужно указать в баг-репорте?"
            ),
            correct=correct,
            wrongs=wrongs,
            code=code,
            language="text",
            why=(
                f"Серьёзность зависит только от влияния дефекта на продукт: здесь это {sev}. Приоритет задаёт "
                f"бизнес-контекст: здесь {pri}. Серьёзность и приоритет — разные оси: высокая серьёзность не "
                "означает высокий приоритет, и наоборот."
            ),
        )
