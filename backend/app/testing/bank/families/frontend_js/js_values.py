"""js_core: значения и данные — методы массивов, приведение типов, ?? / ?., деструктуризация и spread.

Ключ — stdout сгенерированного кода в Node (execution.run_js, пачками — см. _common).
Дистракторы — stdout МУТАНТОВ того же кода, каждый моделирует одно заблуждение (spec-testing §3.8).
"""

from __future__ import annotations

import random
import textwrap

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.frontend_js._common import (
    JsExecFamily,
    JsSpec,
    explain,
    fill,
    norm_output,
    pick,
    plural_ru,
    text_distractors,
)

HINT_OUTPUT = "Вывод одной строкой: значения по порядку через пробел, кавычки не нужны"


def _dd(s: str) -> str:
    return textwrap.dedent(s).strip("\n")


# =====================================================================================
# js.array_methods — filter / map / reduce на списке заказов
# =====================================================================================

_ARR_DOMAINS = [
    {"arr": "orders", "it": "o", "st": "status", "ok": "paid", "bad": ["cancelled", "pending"],
     "price": "total", "qty": "qty", "sel": "paid", "acc": "revenue"},
    {"arr": "tickets", "it": "t", "st": "state", "ok": "sold", "bad": ["reserved", "refunded"],
     "price": "price", "qty": "seats", "sel": "sold", "acc": "income"},
    {"arr": "shipments", "it": "s", "st": "status", "ok": "delivered", "bad": ["lost", "returned"],
     "price": "cost", "qty": "boxes", "sel": "delivered", "acc": "billed"},
    {"arr": "cartItems", "it": "item", "st": "stock", "ok": "available", "bad": ["soldOut", "preorder"],
     "price": "price", "qty": "count", "sel": "available", "acc": "total"},
    {"arr": "invoices", "it": "inv", "st": "status", "ok": "approved", "bad": ["draft", "rejected"],
     "price": "amount", "qty": "copies", "sel": "approved", "acc": "sum"},
    {"arr": "bookings", "it": "b", "st": "status", "ok": "confirmed", "bad": ["waitlist", "cancelled"],
     "price": "rate", "qty": "nights", "sel": "confirmed", "acc": "earned"},
]

_ARR_V1 = """
const <<arr>> = [
<<rows>>
];

const <<sel>> = <<filtered>>;
const <<acc>> = <<sel>>
  .map((<<it>>) => <<mapped>>)
  .reduce((sum, x) => sum + x, 0);

console.log(<<sel>>.length, <<acc>>);
"""

_ARR_V2 = """
const <<arr>> = [
<<rows>>
];

const <<acc>> = <<arr>>.reduce(
  (sum, <<it>>) => (<<cond>> ? sum + <<mapped>> : sum),
  0,
);
const <<sel>>Count = <<counted>>.length;

console.log(<<sel>>Count, <<acc>>);
"""

_ARR_HINTS = {
    "filter_ignored": "filter не пропущен: в сумму попадают только элементы, прошедшие условие.",
    "filter_removes": "filter ОСТАВЛЯЕТ элементы, для которых колбэк вернул true, а не удаляет их.",
    "map_partial": "map применяет выражение целиком — цена умножается на количество для каждого элемента.",
}


@register
class ArrayMethods(JsExecFamily):
    id = "js.array_methods"
    title = "filter → map → reduce по списку заказов"
    specs = ("frontend_js",)
    module = "js_core"
    format = "predict_output"
    target_grade = "intern"
    position = -0.3
    c = 0.0
    time_limit_s = 90
    is_anchor = True

    def normalize(self, answer):
        return norm_output(answer)

    def _render(self, d: dict, rows: list[dict], variant: int, *, ignore_filter=False, invert=False,
                no_qty=False) -> str:
        it, st, ok = d["it"], d["st"], d["ok"]
        op = "!==" if invert else "==="
        cond = f'{it}.{st} {op} "{ok}"'
        mapped = f"{it}.{d['price']}" if no_qty else f"{it}.{d['price']} * {it}.{d['qty']}"
        row_lines = ",\n".join(
            f'  {{ id: {r["id"]}, {st}: "{r["st"]}", {d["price"]}: {r["price"]}, {d["qty"]}: {r["qty"]} }}'
            for r in rows
        )
        if variant == 1:
            filtered = d["arr"] if ignore_filter else f"{d['arr']}.filter(({it}) => {cond})"
            return fill(_ARR_V1, rows=row_lines, filtered=filtered, mapped=mapped, **d).strip()
        cond2 = "true" if ignore_filter else cond
        counted = d["arr"] if ignore_filter else f"{d['arr']}.filter(({it}) => {cond})"
        return fill(_ARR_V2, rows=row_lines, cond=cond2, mapped=mapped, counted=counted, **d).strip()

    def build(self, rng: random.Random) -> JsSpec:
        d = dict(pick(rng, _ARR_DOMAINS))
        n = rng.randint(5, 6)
        n_ok = rng.randint(2, n - 2)
        flags = [True] * n_ok + [False] * (n - n_ok)
        rng.shuffle(flags)
        next_id = rng.randrange(100, 900, 10)
        rows = []
        for good in flags:
            next_id += rng.randint(1, 4)
            rows.append({
                "id": next_id,
                "st": d["ok"] if good else pick(rng, d["bad"]),
                "price": rng.randrange(101, 1999),
                "qty": rng.randint(1, 4),
            })
        variant = rng.choice((1, 2))
        code = self._render(d, rows, variant)
        progs = {
            "key": code,
            "filter_ignored": self._render(d, rows, variant, ignore_filter=True),
            "filter_removes": self._render(d, rows, variant, invert=True),
            "map_partial": self._render(d, rows, variant, no_qty=True),
        }
        return JsSpec(code, progs, {"d": d, "rows": rows})

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        key = norm_output(outs["key"])
        d, rows = spec.data["d"], spec.data["rows"]
        wrong = text_distractors(key, [(norm_output(outs[t]), t) for t in _ARR_HINTS])
        good = [r for r in rows if r["st"] == d["ok"]]
        parts = " + ".join(f"{r['price']}·{r['qty']}" for r in good)
        main = (
            f"Условие «{d['st']} === \"{d['ok']}\"» проходят {len(good)} "
            f"{plural_ru(len(good), ('элемент', 'элемента', 'элементов'))}; "
            f"для каждого берётся {d['price']} × {d['qty']}: {parts}. reduce с начальным значением 0 "
            f"складывает их. Итог вывода: «{key}»."
        )
        rendered = Rendered(
            prompt=f"Что выведет этот код? Массив {d['arr']} — данные, пришедшие с сервера.",
            code=spec.code,
            language="javascript",
            answer_mode="text",
            answer_hint=HINT_OUTPUT,
        )
        return Instance(self.id, seed, rendered, key, wrong, explain(main, wrong, _ARR_HINTS))


# =====================================================================================
# js.equality_coercion — строки из полей формы, +, *, ==, ===
# =====================================================================================

_COERCE_CTX = [
    ("quantity", "price", "delivery"),
    ("seats", "ticketPrice", "serviceFee"),
    ("nights", "roomRate", "cityTax"),
    ("boxes", "boxPrice", "shipping"),
    ("months", "monthlyFee", "setupFee"),
    ("licenses", "licensePrice", "supportFee"),
    ("guests", "menuPrice", "tips"),
]

_COERCE_HINTS = {
    "string_as_number": "значение из поля формы — строка \"…\", а не число: «+» со строкой склеивает.",
    "left_to_right": "«+» вычисляется слева направо: сначала складываются два числа, потом результат склеивается со строкой.",
    "loose_no_coercion": "«==» приводит строку к числу перед сравнением, поэтому \"3\" == 3 — true.",
    "strict_coerces": "«===» не приводит типы: строка не равна числу.",
}


@register
class EqualityCoercion(JsExecFamily):
    id = "js.equality_coercion"
    title = "Строки из формы: +, *, == и ==="
    specs = ("frontend_js",)
    module = "js_core"
    format = "predict_output"
    target_grade = "intern"
    position = 0.5
    c = 0.0
    time_limit_s = 90

    def normalize(self, answer):
        return norm_output(answer)

    @staticmethod
    def _render(p: dict, *, q_number=False, concat_all=False, loose_strict=False, strict_loose=False) -> str:
        q, pr, dv = p["names"]
        qv = str(p["qv"]) if q_number else f'"{p["qv"]}"'
        lines = []
        for kind in p["order"]:
            if kind == "arith":
                lines.append(f"console.log({q} * {pr});" if p["arith"] == "mul" else f"console.log({pr} - {q});")
            elif kind == "concat":
                lines.append(f"console.log({q} + {pr});" if p["concat_first"] else f"console.log({pr} + {q});")
            elif kind == "ltr":
                pre = '"" + ' if concat_all else ""
                if p["ltr_num_first"]:
                    lines.append(f"console.log({pre}{pr} + {dv} + {q});")
                else:
                    lines.append(f"console.log({q} + {pr} + {dv});")
            elif kind == "loose":
                op = "===" if loose_strict else "=="
                lines.append(f"console.log({q} {op} {p['qv']});")
            elif kind == "strict":
                op = "==" if strict_loose else "==="
                lines.append(f"console.log({q} {op} {p['qv']});")
        head = [
            "// значение input.value из формы — всегда строка",
            f"const {q} = {qv};",
            f"const {pr} = {p['pv']};",
            f"const {dv} = {p['dv']};",
            "",
        ]
        return "\n".join(head + lines)

    def build(self, rng: random.Random) -> JsSpec:
        order = ["arith", "concat", "ltr", "loose", "strict"]
        rng.shuffle(order)
        p = {
            "names": pick(rng, _COERCE_CTX),
            "qv": rng.randint(2, 9),
            "pv": rng.randrange(110, 990, 10),
            "dv": rng.randrange(15, 95, 5),
            "order": order,
            "arith": rng.choice(("mul", "sub")),
            "concat_first": rng.random() < 0.5,
            "ltr_num_first": rng.random() < 0.6,
        }
        code = self._render(p)
        progs = {
            "key": code,
            "string_as_number": self._render(p, q_number=True),
            "left_to_right": self._render(p, concat_all=True),
            "loose_no_coercion": self._render(p, loose_strict=True),
            "strict_coerces": self._render(p, strict_loose=True),
        }
        return JsSpec(code, progs, p)

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        key = norm_output(outs["key"])
        wrong = text_distractors(key, [(norm_output(outs[t]), t) for t in _COERCE_HINTS])
        q = spec.data["names"][0]
        main = (
            f"{q} — строка \"{spec.data['qv']}\". Арифметические операторы * и - приводят строку к числу, "
            "а «+» при наличии строки склеивает; выражение из нескольких «+» считается слева направо. "
            "«==» сравнивает с приведением типов, «===» — без. "
            f"Вывод: «{key}»."
        )
        rendered = Rendered(
            prompt="Что выведет этот код? Каждый console.log печатает одну строку.",
            code=spec.code,
            language="javascript",
            answer_mode="text",
            answer_hint=HINT_OUTPUT + " (все строки вывода подряд)",
        )
        return Instance(self.id, seed, rendered, key, wrong, explain(main, wrong, _COERCE_HINTS))


# =====================================================================================
# js.optional_nullish — ?? против ||, опциональная цепочка
# =====================================================================================

# Значения по умолчанию правдоподобны для своего поля: (lo, hi, step) — число, список — готовые литералы JS.
# Многие строки — настоящие баги «|| вместо ??»: скидка 0% превращается в 10%, порт 0 — в 8080.
_NULLISH_DOMAINS = [
    {"obj": "settings", "zero": "retries", "zero_def": (2, 9, 1), "nul": "timeout", "nul_def": (1000, 9500, 500),
     "flag": "compact", "str": "title", "str_def": ["untitled", "draft", "new page"],
     "nest": "theme", "leaf": "fontSize", "leaf_val": (11, 24, 1), "leaf_def": (12, 20, 2),
     "miss": "locale", "mleaf": "lang", "miss_def": ['"ru"', '"en"', '"auto"']},
    {"obj": "player", "zero": "volume", "zero_def": (30, 90, 10), "nul": "startAt", "nul_def": (5, 120, 5),
     "flag": "autoplay", "str": "caption", "str_def": ["no caption", "untitled", "clip"],
     "nest": "quality", "leaf": "bitrate", "leaf_val": (96, 320, 32), "leaf_def": (128, 512, 64),
     "miss": "subtitles", "mleaf": "track", "miss_def": ['"off"', '"ru"', '"en"']},
    {"obj": "profile", "zero": "rating", "zero_def": (3, 5, 1), "nul": "avatar", "nul_def": ['"default.png"', '"anon.svg"'],
     "flag": "verified", "str": "nickname", "str_def": ["guest", "user", "anonymous"],
     "nest": "stats", "leaf": "posts", "leaf_val": (1, 400, 1), "leaf_def": (0, 0, 1),
     "miss": "company", "mleaf": "name", "miss_def": ['"freelance"', '"none"']},
    {"obj": "config", "zero": "port", "zero_def": (3000, 9000, 1000), "nul": "proxy", "nul_def": ['"direct"', '"none"'],
     "flag": "cache", "str": "prefix", "str_def": ["/api", "/v1", "/v2"],
     "nest": "limits", "leaf": "rps", "leaf_val": (10, 200, 5), "leaf_def": (50, 500, 50),
     "miss": "auth", "mleaf": "token", "miss_def": ['"anonymous"', '"guest"']},
    # значения по умолчанию — только то, что кандидат наберёт с клавиатуры (без «—» и т. п.)
    {"obj": "cart", "zero": "discount", "zero_def": (5, 30, 5), "nul": "coupon", "nul_def": ['"NONE"', '"N/A"'],
     "flag": "giftWrap", "str": "comment", "str_def": ["no comment", "-", "empty"],
     "nest": "delivery", "leaf": "days", "leaf_val": (1, 14, 1), "leaf_def": (3, 10, 1),
     "miss": "pickup", "mleaf": "point", "miss_def": ['"courier"', '"post"', '"locker"']},
    {"obj": "order", "zero": "tips", "zero_def": (5, 15, 5), "nul": "promo", "nul_def": ['"NOPROMO"', '"EMPTY"'],
     "flag": "contactless", "str": "note", "str_def": ["no note", "-", "empty"],
     "nest": "address", "leaf": "floor", "leaf_val": (1, 25, 1), "leaf_def": (1, 1, 1),
     "miss": "recipient", "mleaf": "phone", "miss_def": ['"same"', '"unknown"']},
]


def _lit(rng: random.Random, spec) -> str:
    if isinstance(spec, tuple):
        lo, hi, step = spec
        return str(rng.randrange(lo, hi + 1, step))
    return pick(rng, spec)


_NULLISH_HINTS = {
    "operators_swapped": "«??» подставляет значение по умолчанию только для null и undefined, а «||» — для любого ложного (0, \"\", false).",
    "nullish_only_undefined": "«??» срабатывает и на null, и на undefined.",
    "or_like_nullish": "«||» заменяет любое ложное значение, в том числе 0 и false.",
}


@register
class OptionalNullish(JsExecFamily):
    id = "js.optional_nullish"
    title = "?? против || и опциональная цепочка ?."
    specs = ("frontend_js",)
    module = "js_core"
    format = "predict_output"
    target_grade = "junior"
    position = -0.5
    c = 0.0
    time_limit_s = 90
    is_anchor = True

    def normalize(self, answer):
        return norm_output(answer)

    @staticmethod
    def _op(op: str, mode: str) -> str:
        if mode == "swap":
            return "||" if op == "??" else "??"
        if mode == "or_like_nullish" and op == "||":
            return "??"
        return op

    def _render(self, p: dict, mode: str = "") -> str:
        d = p["d"]
        o = d["obj"]

        def expr(lhs: str, op: str, default: str) -> str:
            real = self._op(op, mode)
            if real == "??" and mode == "nullish_only_undefined":
                return f"({lhs} !== undefined ? {lhs} : {default})"
            return f"{lhs} {real} {default}"

        fields = {
            "zero": f"  {d['zero']}: 0,",
            "nul": f"  {d['nul']}: null,",
            "third": f"  {d['flag']}: false," if p["third"] == "false" else f'  {d["str"]}: "",',
            "nest": f"  {d['nest']}: {{ {d['leaf']}: {p['leafv']} }},",
        }
        obj_lines = [fields[k] for k in p["field_order"]]
        third_name = d["flag"] if p["third"] == "false" else d["str"]
        third_default = "true" if p["third"] == "false" else f'"{p["strdef"]}"'
        miss = o + "." + d["miss"] + "?." + d["mleaf"]
        lhs_zero = o + "." + d["zero"]
        lhs_nul = o + "." + d["nul"]
        lhs_third = o + "." + third_name
        lhs_nest = o + "." + d["nest"] + "?." + d["leaf"]
        miss_expr = expr(miss, "??", p["d_miss"]) if p["miss_default"] else miss
        stmts = {
            "zero": f"const {d['zero']} = {expr(lhs_zero, p['op_zero'], str(p['d_zero']))};",
            "nul": f"const {d['nul']} = {expr(lhs_nul, p['op_nul'], str(p['d_nul']))};",
            "third": f"const {third_name} = {expr(lhs_third, p['op_third'], third_default)};",
            "nest": f"const {d['leaf']} = {expr(lhs_nest, '??', str(p['d_leaf']))};",
            "miss": f"const {d['mleaf']} = {miss_expr};",
        }
        names = {"zero": d["zero"], "nul": d["nul"], "third": third_name, "nest": d["leaf"], "miss": d["mleaf"]}
        lines = [f"const {o} = {{", *obj_lines, "};", ""]
        lines += [stmts[k] for k in p["stmt_order"]]
        lines.append("")
        lines.append(f"console.log({', '.join(names[k] for k in p['stmt_order'])});")
        return "\n".join(lines)

    def build(self, rng: random.Random) -> JsSpec:
        d = pick(rng, _NULLISH_DOMAINS)
        third = rng.choice(("false", "empty"))
        p = {
            "d": d,
            "third": third,
            "op_zero": rng.choice(("??", "||")),
            "op_nul": rng.choice(("??", "||")),
            # пустая строка через ?? напечаталась бы пустотой — неотличимо в ответе, поэтому для "" только ||
            "op_third": "||" if third == "empty" else rng.choice(("??", "||")),
            "d_zero": _lit(rng, d["zero_def"]),
            "d_nul": _lit(rng, d["nul_def"]),
            "leafv": _lit(rng, d["leaf_val"]),
            "d_leaf": _lit(rng, d["leaf_def"]),
            "strdef": pick(rng, d["str_def"]),
            "miss_default": rng.random() < 0.6,
            "d_miss": _lit(rng, d["miss_def"]),
            "field_order": rng.sample(["zero", "nul", "third", "nest"], 4),
            "stmt_order": rng.sample(["zero", "nul", "third", "nest", "miss"], 5),
        }
        code = self._render(p)
        progs = {
            "key": code,
            "operators_swapped": self._render(p, "swap"),
            "nullish_only_undefined": self._render(p, "nullish_only_undefined"),
            "or_like_nullish": self._render(p, "or_like_nullish"),
        }
        return JsSpec(code, progs, p)

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        key = norm_output(outs["key"])
        wrong = text_distractors(key, [(norm_output(outs[t]), t) for t in _NULLISH_HINTS])
        main = (
            "«a ?? b» возвращает b только если a — null или undefined; 0, false и \"\" остаются. "
            "«a || b» возвращает b для любого ложного a. Опциональная цепочка «x?.y» не бросает ошибку, "
            f"если x — undefined, а возвращает undefined. Вывод: «{key}»."
        )
        rendered = Rendered(
            prompt=f"Что выведет этот код? Объект {spec.data['d']['obj']} пришёл с сервера.",
            code=spec.code,
            language="javascript",
            answer_mode="text",
            answer_hint=HINT_OUTPUT,
        )
        return Instance(self.id, seed, rendered, key, wrong, explain(main, wrong, _NULLISH_HINTS))


# =====================================================================================
# js.destructuring_spread — порядок spread, undefined перезаписывает, значения по умолчанию
# =====================================================================================

_DS_DOMAINS = [
    {"n1": "pageSize", "s": "sortBy", "b": "dense", "n2": "columns",
     "svals": ["date", "name", "price", "rating", "views"]},
    {"n1": "volume", "s": "quality", "b": "autoplay", "n2": "bufferSec",
     "svals": ["auto", "hd", "sd", "uhd", "low"]},
    {"n1": "fontSize", "s": "theme", "b": "wordWrap", "n2": "tabSize",
     "svals": ["light", "dark", "solar", "mono", "night"]},
    {"n1": "zoom", "s": "layer", "b": "traffic", "n2": "maxMarkers",
     "svals": ["scheme", "satellite", "hybrid", "relief", "transit"]},
    {"n1": "height", "s": "palette", "b": "legend", "n2": "ticks",
     "svals": ["warm", "cold", "pastel", "neon", "gray"]},
]

_DS_HINTS = {
    "spread_skips_undefined": "свойство со значением undefined при spread копируется и перезаписывает предыдущее.",
    "default_on_null": "значение по умолчанию при деструктуризации подставляется только для undefined, null остаётся null.",
    "spread_order": "при { ...a, ...b } побеждают свойства из b — того, что раскрыт позже.",
    "array_hole": "пропуск «, ,» в деструктуризации массива пропускает элемент: третий элемент массива отсутствует.",
}


@register
class DestructuringSpread(JsExecFamily):
    id = "js.destructuring_spread"
    title = "Слияние настроек: spread и значения по умолчанию"
    specs = ("frontend_js",)
    module = "js_core"
    format = "predict_output"
    target_grade = "junior"
    position = 0.3
    c = 0.0
    time_limit_s = 120

    def normalize(self, answer):
        return norm_output(answer)

    @staticmethod
    def _render(p: dict, mode: str = "") -> str:
        d = p["d"]
        defaults_fields = {
            "n1": f"{d['n1']}: {p['n1_def']}",
            "s": f'{d["s"]}: "{p["s_def"]}"',
            "b": f"{d['b']}: false",
            "n2": f"{d['n2']}: {p['n2_def']}",
        }
        saved_fields = {
            "n1": f"{d['n1']}: {p['n1_saved']}",
            "s": f"{d['s']}: undefined",
            "b": f"{d['b']}: {'undefined' if mode == 'default_on_null' else 'null'}",
        }
        if mode == "spread_skips_undefined":
            saved_fields.pop("s")
        dflt = ", ".join(defaults_fields[k] for k in p["def_order"])
        saved = ", ".join(saved_fields[k] for k in p["saved_order"] if k in saved_fields)
        merge = "{ ...saved, ...defaults }" if mode == "spread_order" else "{ ...defaults, ...saved }"
        destr = {
            "n1": d["n1"],
            "s": f'{d["s"]} = "{p["s_fallback"]}"',
            "b": f"{d['b']} = true",
            "n2": f"{d['n2']} = {p['n2_fallback']}",
        }
        destr_s = ", ".join(destr[k] for k in p["destr_order"])
        arr_pat = "[first, third = " if mode == "array_hole" else "[first, , third = "
        lines = [
            f"const defaults = {{ {dflt} }};",
            f"const saved = {{ {saved} }};",
            "",
            f"const options = {merge};",
            f"const {{ {destr_s} }} = options;",
            f"const {arr_pat}{p['third_def']}] = [{p['a0']}, {p['a1']}];",
            "",
            f"console.log({', '.join(d[k] for k in p['destr_order'])}, first, third);",
        ]
        return "\n".join(lines)

    def build(self, rng: random.Random) -> JsSpec:
        d = pick(rng, _DS_DOMAINS)
        s_def, s_fallback = rng.sample(d["svals"], 2)
        n1_def = rng.randrange(10, 60, 2)
        n1_saved = rng.randrange(61, 160, 3)
        n2_def = rng.randint(3, 14)
        p = {
            "d": d,
            "s_def": s_def,
            "s_fallback": s_fallback,
            "n1_def": n1_def,
            "n1_saved": n1_saved,
            "n2_def": n2_def,
            "n2_fallback": n2_def + rng.randint(1, 9),
            "a0": rng.randint(1, 49),
            "a1": rng.randint(50, 99),
            "third_def": rng.randint(100, 999),
            "def_order": rng.sample(["n1", "s", "b", "n2"], 4),
            "saved_order": rng.sample(["n1", "s", "b"], 3),
            "destr_order": rng.sample(["n1", "s", "b", "n2"], 4),
        }
        code = self._render(p)
        progs = {"key": code, **{t: self._render(p, t) for t in _DS_HINTS}}
        return JsSpec(code, progs, p)

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        key = norm_output(outs["key"])
        wrong = text_distractors(key, [(norm_output(outs[t]), t) for t in _DS_HINTS])
        d = spec.data["d"]
        main = (
            "В { ...defaults, ...saved } поля saved перезаписывают defaults — даже если там явно записан "
            f"undefined (поле {d['s']}). Затем при деструктуризации значение по умолчанию подставляется "
            f"только вместо undefined: {d['s']} получает запасное значение, а {d['b']} остаётся null. "
            f"Поле {d['n2']} есть в объекте, поэтому его запасное значение не используется. "
            f"В [first, , third] пропущен второй элемент, третьего нет — сработал default. Вывод: «{key}»."
        )
        rendered = Rendered(
            prompt="Что выведет этот код? Так часто сливают настройки по умолчанию с сохранёнными пользователем.",
            code=spec.code,
            language="javascript",
            answer_mode="text",
            answer_hint=HINT_OUTPUT,
        )
        return Instance(self.id, seed, rendered, key, wrong, explain(main, wrong, _DS_HINTS))
