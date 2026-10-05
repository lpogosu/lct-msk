"""js_core: сужение типов TypeScript (mcq_concept, ключ — точный решатель).

Компилятор tsc в банке не запускаем; вместо него — точная модель сужения для ограниченного набора
конструкций, где поведение TS однозначно и документировано (Handbook → Narrowing):
typeof (в т.ч. typeof null === "object"), истинность, == null / === null, оператор in по
обязательному свойству, дискриминант в switch. Тип boolean в проверках истинности не используем:
там TS сужает его до true, это уже деталь реализации, а не понимание сужения.
Дистракторы — тот же решатель с «ошибочным» правилом (флаг заблуждения).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.testing.bank.base import Family, Instance, Rendered, register
from app.testing.bank.families.frontend_js._common import explain, make_choice, norm_choice, pick


@dataclass(frozen=True)
class TsType:
    name: str
    tof: str  # результат typeof во время выполнения
    nullish: bool = False
    array: bool = False
    props: frozenset[str] = field(default_factory=frozenset)


STRING = TsType("string", "string")
NUMBER = TsType("number", "number")
BOOLEAN = TsType("boolean", "boolean")
NULL = TsType("null", "object", nullish=True)
UNDEF = TsType("undefined", "undefined", nullish=True)
STR_ARR = TsType("string[]", "object", array=True)
NUM_ARR = TsType("number[]", "object", array=True)
DATE = TsType("Date", "object")


def _typeof(t: TsType, flags: set[str]) -> str:
    if "typeof_null" in flags and t.name == "null":
        return "null"
    if "array_not_object" in flags and t.array:
        return "array"
    return t.tof


def narrow(members: list[TsType], guard: tuple, positive: bool, flags: set[str] = frozenset()) -> list[TsType]:
    """Члены объединения, остающиеся в ветке, где guard истинен (positive) или ложен."""
    kind = guard[0]
    if kind == "typeof":
        pred = lambda t: _typeof(t, flags) == guard[1]  # noqa: E731
    elif kind == "truthy":
        # ветка «истинно» убирает null/undefined; string/number остаются (TS не сужает их до непустых)
        pred = lambda t: not t.nullish  # noqa: E731
        if "only_undefined" in flags:
            pred = lambda t: t.name != "undefined"  # noqa: E731
        if "only_null" in flags:
            pred = lambda t: t.name != "null"  # noqa: E731
    elif kind == "eq_null_loose":
        pred = (lambda t: t.name == "null") if "loose_like_strict" in flags else (lambda t: t.nullish)
    elif kind == "eq_null_strict":
        pred = (lambda t: t.nullish) if "strict_like_loose" in flags else (lambda t: t.name == "null")
    elif kind == "in":
        pred = lambda t: guard[1] in t.props  # noqa: E731
    elif kind == "is":
        pred = lambda t: t.name == guard[1]  # noqa: E731
    else:  # pragma: no cover
        raise ValueError(kind)
    return [t for t in members if pred(t) == positive]


# Порядок, в котором TS печатает объединение (checker: члены union отсортированы по id типа, а
# formatUnionTypes переносит null и undefined в конец): встроенные string < number < boolean созданы
# раньше любых объектных типов; объектные — в порядке объявления.
_TS_RANK = {"string": 0, "number": 1, "boolean": 2, "null": 4, "undefined": 5}


def _canon(members: list[TsType]) -> list[TsType]:
    return sorted(members, key=lambda t: _TS_RANK.get(t.name, 3))  # sorted устойчив


def show(members: list[TsType]) -> str:
    """Как печатает TS: string, number, boolean, затем объектные типы по объявлению, затем null, undefined."""
    if not members:
        return "never"
    return " | ".join(t.name for t in _canon(members))


_FN = ["describe", "format", "render", "normalize", "toLabel", "preview", "serialize"]
_VAR = ["value", "input", "raw", "payload", "field", "data"]

_TS_HINTS = {
    "typeof_null": "typeof null === \"object\" — историческая особенность JS, и TS её учитывает: null остаётся в ветке.",
    "array_not_object": "для массива typeof тоже возвращает \"object\" — массивы проходят эту проверку.",
    "no_narrowing": "TS сужает тип после проверки: в этой ветке часть вариантов объединения уже невозможна.",
    "branch_confused": "после «if (…) return» дальше выполняется код, где условие было ЛОЖНЫМ.",
    "only_undefined": "отрицание !x истинно и для null, и для undefined — после return оба исключены.",
    "only_null": "отрицание !x истинно и для null, и для undefined — после return оба исключены.",
    "loose_like_strict": "== null (нестрогое) истинно и для null, и для undefined.",
    "strict_like_loose": "=== null (строгое) отсекает только null, undefined остаётся.",
    "never": "default в switch достижим: остались варианты, не перечисленные в case.",
    "other": "проверьте, какие варианты объединения удовлетворяют условию.",
}

_IN_DOMAINS = [
    {"types": [("CardPayment", "cardNumber: string; cvc: string", {"cardNumber", "cvc"}),
               ("SbpPayment", "phone: string", {"phone"}),
               ("CashPayment", "change: number", {"change"})], "arg": "payment"},
    {"types": [("EmailNote", "email: string; subject: string", {"email", "subject"}),
               ("SmsNote", "phone: string; text: string", {"phone", "text"}),
               ("PushNote", "deviceId: string", {"deviceId"})], "arg": "note"},
    {"types": [("Courier", "address: string; floor: number", {"address", "floor"}),
               ("Pickup", "pointId: string", {"pointId"}),
               ("Locker", "lockerId: string; cell: number", {"lockerId", "cell"})], "arg": "delivery"},
]

_SWITCH_DOMAINS = [
    {"alias": "Shape", "arg": "shape", "types": [("Circle", "circle", "radius: number"),
                                                 ("Square", "square", "side: number"),
                                                 ("Rect", "rect", "width: number; height: number"),
                                                 ("Triangle", "triangle", "base: number; height: number")]},
    # имена не пересекаются с глобальными типами lib.dom (Event, Request, FocusEvent…)
    {"alias": "UserAction", "arg": "action", "types": [("Tap", "tap", "x: number; y: number"),
                                                       ("KeyPress", "keypress", "code: string"),
                                                       ("Swipe", "swipe", "dx: number"),
                                                       ("LongPress", "longpress", "ms: number")]},
    {"alias": "LoadState", "arg": "state", "types": [("Loading", "loading", "startedAt: number"),
                                                     ("Loaded", "loaded", "data: string[]"),
                                                     ("Failed", "failed", "error: string"),
                                                     ("Idle", "idle", "since: number")]},
]


@register
class TsNarrowing(Family):
    id = "js.ts_narrowing"
    title = "TypeScript: сужение типов"
    specs = ("frontend_js",)
    module = "js_core"
    format = "mcq_concept"
    target_grade = "middle"
    position = 0.3
    c = 0.25
    time_limit_s = 90

    def normalize(self, answer):
        return norm_choice(answer)

    # каждый сценарий возвращает: код, правильный тип, список (неверный тип, тег), пояснение
    def _primitive_scenario(self, rng: random.Random) -> tuple[str, str, list[tuple[str, str]], str]:
        fn, v = pick(rng, _FN), pick(rng, _VAR)
        kind = pick(rng, ["typeof_object", "truthy", "eq_loose", "eq_strict"])
        prims = rng.sample([STRING, NUMBER], rng.randint(1, 2))
        if kind == "typeof_object":
            obj = pick(rng, [STR_ARR, NUM_ARR, DATE])
            if rng.random() < 0.5:
                prims = rng.sample([STRING, NUMBER, BOOLEAN], rng.randint(1, 2))
            members = prims + [obj]
            rng.shuffle(members)
            members = _canon(members + [NULL] + ([UNDEF] if rng.random() < 0.5 else []))
            guard = ("typeof", "object")
            head = f'  if (typeof {v} === "object") {{'
            body = [head, f"    const result = {v}; // ← ?", "  }"]
            correct = narrow(members, guard, True)
            wrong = [
                (show(narrow(members, guard, True, {"typeof_null"})), "typeof_null"),
                (show(narrow(members, guard, True, {"array_not_object"})), "array_not_object"),
                (show(members), "no_narrowing"),
                (show(narrow(members, guard, False)), "other"),
                (show([t for t in members if t.name != "null"]), "other"),
            ]
            why = (f"typeof {v} === \"object\" истинно для объектов, массивов и null (typeof null === \"object\"); "
                   "примитивы и undefined отсекаются.")
        elif kind == "truthy":
            extra = [STR_ARR] if rng.random() < 0.4 else []
            members = prims + extra
            rng.shuffle(members)
            members = _canon(members + [NULL, UNDEF])
            guard = ("truthy",)
            body = [f"  if (!{v}) {{", "    return;", "  }", f"  const result = {v}; // ← ?"]
            correct = narrow(members, guard, True)
            wrong = [
                (show(narrow(members, guard, True, {"only_undefined"})), "only_undefined"),
                (show(narrow(members, guard, True, {"only_null"})), "only_null"),
                (show([t for t in members if t.nullish]), "branch_confused"),
                (show(members), "no_narrowing"),
            ]
            why = (f"после «if (!{v}) return» значение истинно: null и undefined исключены. string и number "
                   "остаются — TS не выделяет отдельный тип для непустой строки или ненулевого числа.")
        elif kind == "eq_loose":
            members = prims + ([pick(rng, [STR_ARR, DATE])] if rng.random() < 0.4 else [])
            rng.shuffle(members)
            members = _canon(members + [NULL, UNDEF])
            guard = ("eq_null_loose",)
            dflt = '""' if STRING in prims else "0"
            body = [f"  if ({v} == null) {{", f"    return {dflt};", "  }", f"  const result = {v}; // ← ?"]
            correct = narrow(members, guard, False)
            wrong = [
                (show(narrow(members, guard, False, {"loose_like_strict"})), "loose_like_strict"),
                (show(narrow(members, guard, True)), "branch_confused"),
                (show(members), "no_narrowing"),
                (show([t for t in members if t.name != "undefined"]), "other"),
            ]
            why = f"{v} == null истинно и для null, и для undefined; после return остаются остальные варианты."
        else:
            members = prims + ([pick(rng, [STR_ARR, DATE])] if rng.random() < 0.4 else [])
            rng.shuffle(members)
            members = _canon(members + [NULL, UNDEF])
            guard = ("eq_null_strict",)
            dflt = '""' if STRING in prims else "0"
            body = [f"  if ({v} === null) {{", f"    return {dflt};", "  }", f"  const result = {v}; // ← ?"]
            correct = narrow(members, guard, False)
            wrong = [
                (show(narrow(members, guard, False, {"strict_like_loose"})), "strict_like_loose"),
                (show(narrow(members, guard, True)), "branch_confused"),
                (show(members), "no_narrowing"),
                (show([t for t in members if not t.nullish]), "other"),
            ]
            why = f"{v} === null отсекает только null; undefined — другое значение и остаётся в типе."
        code = "\n".join([f"function {fn}({v}: {show(members)}) {{", *body, "}"])
        return code, show(correct), wrong, why

    def _in_scenario(self, rng: random.Random) -> tuple[str, str, list[tuple[str, str]], str]:
        d = pick(rng, _IN_DOMAINS)
        types = list(d["types"])
        rng.shuffle(types)
        members = [TsType(n, "object", props=frozenset(p)) for n, _, p in types]
        target = pick(rng, types)
        prop = pick(rng, sorted(target[2]))
        guard = ("in", prop)
        fn = pick(rng, ["handle", "route", "submit", "dispatch", "checkout"])
        arg = d["arg"]
        decls = [f"type {n} = {{ {body} }};" for n, body, _ in types]
        code = "\n".join([
            *decls,
            "",
            f"function {fn}({arg}: {show(members)}) {{",
            f'  if ("{prop}" in {arg}) {{',
            "    return;",
            "  }",
            f"  const result = {arg}; // ← ?",
            "}",
        ])
        correct = narrow(members, guard, False)
        wrong = [
            (show(narrow(members, guard, True)), "branch_confused"),
            (show(members), "no_narrowing"),
            (show(correct[:1]), "other"),
            (show(correct[1:]), "other"),
        ]
        why = (f"«\"{prop}\" in {arg}» истинно только для типов, где свойство {prop} объявлено. После return "
               "остаются типы без этого свойства.")
        return code, show(correct), wrong, why

    def _switch_scenario(self, rng: random.Random) -> tuple[str, str, list[tuple[str, str]], str]:
        d = pick(rng, _SWITCH_DOMAINS)
        types = rng.sample(d["types"], 3)
        handled = rng.sample(types, 2)
        members = [TsType(n, "object") for n, _, _ in types]
        rest = members
        for n, _, _ in handled:
            rest = narrow(rest, ("is", n), False)
        arg = d["arg"]
        fn = pick(rng, ["area", "label", "summary", "render", "toText"])
        decls = [f'type {n} = {{ kind: "{k}"; {body} }};' for n, k, body in types]
        cases = []
        for _n, k, _ in handled:
            cases += [f'    case "{k}":', f"      return \"{k}\";"]
        code = "\n".join([
            *decls,
            f"type {d['alias']} = {show(members)};",
            "",
            f"function {fn}({arg}: {d['alias']}) {{",
            f"  switch ({arg}.kind) {{",
            *cases,
            "    default: {",
            f"      const result = {arg}; // ← ?",
            "      return \"other\";",
            "    }",
            "  }",
            "}",
        ])
        wrong = [
            ("never", "never"),
            (show(members), "no_narrowing"),
            (show(narrow(members, ("is", handled[0][0]), True)), "branch_confused"),
            (show(narrow(members, ("is", handled[1][0]), True)), "branch_confused"),
        ]
        why = (f"Поле kind — дискриминант: каждый case отсекает свой вариант, в default остаётся тот, "
               f"что не перечислен ({show(rest)}).")
        return code, show(rest), wrong, why

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        r = rng.random()
        if r < 0.6:
            code, correct, wrong, why = self._primitive_scenario(rng)
        elif r < 0.8:
            code, correct, wrong, why = self._in_scenario(rng)
        else:
            code, correct, wrong, why = self._switch_scenario(rng)
        wrong = [(w, t) for w, t in wrong if w != correct]
        fillers = [(x, "other") for x in ("unknown", "never", "any") if x != correct]
        ch = make_choice(rng, correct, wrong + fillers, k=4)
        main = f"{why} Ответ: {correct}."
        dist_show = {oid: next(o.text for o in ch.options if o.id == oid) for oid in ch.distractors}
        rendered = Rendered(
            prompt="Какой тип TypeScript выведет для result в строке с пометкой «← ?» (strictNullChecks включён)?",
            code=code,
            language="typescript",
            options=ch.options,
            answer_mode="choice",
        )
        return Instance(self.id, seed, rendered, ch.key, ch.distractors,
                        explain(main, ch.distractors, _TS_HINTS, dist_show))
