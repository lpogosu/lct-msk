"""js_core: области видимости и привязки — var/let/const и TDZ, замыкания в циклах, this, прототипы.

Ключ — stdout кода в Node. Дистракторы — stdout мутантов (заблуждение, воплощённое в коде) либо точная
правка вывода, когда заблуждение затрагивает одну строку (например, «TDZ даёт undefined»).
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
    text_distractors,
)

HINT_LINES = "Все строки вывода по порядку через пробел, кавычки не нужны"


def _dd(s: str) -> str:
    return textwrap.dedent(s).strip("\n")


# =====================================================================================
# js.scope_tdz — подъём var, блочная область let, временная мёртвая зона const
# =====================================================================================

_SCOPE_NAMES = [
    {"g": "level", "m": "mode", "fn": "configure", "lim": "limit"},
    {"g": "count", "m": "label", "fn": "render", "lim": "maxItems"},
    {"g": "total", "m": "currency", "fn": "checkout", "lim": "fee"},
    {"g": "score", "m": "status", "fn": "evaluate", "lim": "bonus"},
    {"g": "width", "m": "align", "fn": "layout", "lim": "gutter"},
    {"g": "retries", "m": "channel", "fn": "connect", "lim": "backoff"},
]
_SCOPE_WORDS = ["light", "dark", "compact", "wide", "print", "mobile", "draft", "final", "left",
                "center", "stable", "beta", "ok", "online", "pending"]

_TDZ_BLOCK = """
  try {
    console.log(<<lim>>);
    const <<lim>> = <<D>>;
  } catch (e) {
    console.log("error");
  }"""

_HOIST_BLOCK = """
  console.log(<<g>>);
  var <<g>> = <<B>>;
  if (<<g>> > <<T>>) {
    let <<m>> = "<<M2>>";
    <<g>> += <<m>>.length;
  }
  console.log(<<g>>, <<m>>);"""

_SCOPE_HINTS = {
    "var_not_hoisted": "объявление var поднимается в начало функции: внутри неё переменная уже есть, но равна undefined.",
    "block_let_leaks": "let внутри блока if создаёт новую переменную только для этого блока; внешняя не меняется.",
    "tdz_undefined": "до строки с const/let переменная находится во «временной мёртвой зоне»: обращение бросает ReferenceError.",
    "inner_var_global": "var внутри функции — локальная переменная функции, глобальная остаётся прежней.",
}


@register
class ScopeTdz(JsExecFamily):
    id = "js.scope_tdz"
    title = "var, let, const: подъём и временная мёртвая зона"
    specs = ("frontend_js",)
    module = "js_core"
    format = "predict_output"
    target_grade = "junior"
    position = 0.0
    c = 0.0
    time_limit_s = 120
    enemies = ("js.closure_loop",)

    def normalize(self, answer):
        return norm_output(answer)

    def build(self, rng: random.Random) -> JsSpec:
        names = pick(rng, _SCOPE_NAMES)
        m1, m2 = rng.sample(_SCOPE_WORDS, 2)
        b = rng.randint(20, 90)
        p = {
            **names,
            "A": rng.randint(1, 19),
            "B": b,
            "T": b - rng.randint(1, 15),  # ветка if выполняется всегда — одинаковая сложность у клонов
            "D": rng.randint(2, 99),
            "M1": m1,
            "M2": m2,
        }
        blocks = [_HOIST_BLOCK, _TDZ_BLOCK] if rng.random() < 0.6 else [_TDZ_BLOCK, _HOIST_BLOCK]
        body = "\n".join(fill(x, **p).strip("\n") for x in blocks)
        code = "\n".join([
            fill('var <<g>> = <<A>>;\nlet <<m>> = "<<M1>>";\n', **p),
            f"function {p['fn']}() {{",
            body,
            "}",
            "",
            f"{p['fn']}();",
            f"console.log({p['g']});",
        ])
        return JsSpec(code, {"key": code}, p)

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        p = spec.data
        lines = outs["key"].split("\n")
        inner = p["B"] + len(p["M2"])

        def edited(i: int, new: str) -> str:
            ls = list(lines)
            ls[i] = new
            return norm_output(" ".join(ls))

        i_undef = lines.index("undefined")
        i_err = lines.index("error")
        i_pair = lines.index(f"{inner} {p['M1']}")
        wrong = [
            (edited(i_undef, str(p["A"])), "var_not_hoisted"),
            (edited(i_pair, f"{inner} {p['M2']}"), "block_let_leaks"),
            (edited(i_err, "undefined"), "tdz_undefined"),
            (edited(len(lines) - 1, str(inner)), "inner_var_global"),
        ]
        key = norm_output(outs["key"])
        dist = text_distractors(key, wrong)
        main = (
            f"Внутри {p['fn']} объявление «var {p['g']}» поднимается в начало функции, поэтому первый вывод — "
            f"undefined, а не глобальное {p['A']}. Условие истинно, «let {p['m']}» живёт только в блоке if: "
            f"{p['g']} = {p['B']} + {len(p['M2'])} = {inner}, а снаружи {p['m']} = \"{p['M1']}\". "
            f"Обращение к {p['lim']} до объявления через const бросает ReferenceError, catch печатает error. "
            f"Глобальная {p['g']} не менялась. Вывод: «{key}»."
        )
        rendered = Rendered(
            prompt="Что выведет этот код?",
            code=spec.code,
            language="javascript",
            answer_mode="text",
            answer_hint=HINT_LINES,
        )
        return Instance(self.id, seed, rendered, key, dist, explain(main, dist, _SCOPE_HINTS))


# =====================================================================================
# js.closure_loop — замыкания в цикле: var против let, захват переменной, а не значения
# =====================================================================================

_CL_ARR = ["handlers", "callbacks", "tasks", "renderers", "listeners", "jobs"]
_CL_ACC = ["offset", "total", "delay", "budget", "cursor", "weight"]

_CL_ARROW = """
const <<arr>> = [];
for (<<kw>> i = <<s>>; i < <<e>>; i++) {
  <<arr>>.push(() => i * <<k>> + <<b>>);
}
console.log(<<arr>>.map((fn) => fn()).join(" "));
"""

_CL_TIMEOUT = """
for (<<kw>> i = <<s>>; i < <<e>>; i++) {
  setTimeout(() => console.log(i * <<k>> + <<b>>), 0);
}
"""

_CL_OUTER = """
const <<arr>> = [];
let <<acc>> = <<a0>>;
for (<<kw>> i = 0; i < <<n>>; i++) {
  <<acc>> += <<step>>;
  <<arr>>.push(<<pushed>>);
}
console.log(<<arr>>.map((fn) => fn()).join(" "));
"""

_CL_HINTS = {
    "captures_value": "замыкание хранит ссылку на переменную, а не снимок её значения на момент создания функции.",
    "shared_binding": "let в заголовке for создаёт новую переменную i на каждой итерации — у каждой функции своя.",
    "last_index": "после цикла с var переменная i равна значению, на котором условие стало ложным, а не последнему индексу.",
}


@register
class ClosureLoop(JsExecFamily):
    id = "js.closure_loop"
    title = "Замыкания в цикле: var, let и общая переменная"
    specs = ("frontend_js",)
    module = "js_core"
    format = "predict_output"
    target_grade = "junior"
    position = 0.7
    c = 0.0
    time_limit_s = 90
    enemies = ("js.scope_tdz",)

    def normalize(self, answer):
        return norm_output(answer)

    @staticmethod
    def _render(p: dict, kw: str, snapshot: bool = False) -> str:
        v = p["variant"]
        q = {**p, "kw": kw}
        if v == "arrow":
            return fill(_CL_ARROW, **q).strip()
        if v == "timeout":
            return fill(_CL_TIMEOUT, **q).strip()
        acc = p["acc"]
        q["pushed"] = f"((snap) => () => snap + i)({acc})" if snapshot else f"() => {acc} + i"
        return fill(_CL_OUTER, **q).strip()

    def build(self, rng: random.Random) -> JsSpec:
        variant = pick(rng, ["arrow", "arrow", "timeout", "outer"])
        s = rng.randint(0, 3)
        n = rng.randint(3, 4)
        p = {
            "variant": variant,
            "arr": pick(rng, _CL_ARR),
            "acc": pick(rng, _CL_ACC),
            "s": s,
            "e": s + n,
            "n": n,
            "k": rng.randint(2, 29),
            # шире диапазон b — меньше совпадений ключей у разных клонов (детектор «чужого ключа», §3.10)
            "b": rng.randint(1, 999),
            "a0": rng.randrange(10, 1000, 3),
            "step": rng.randint(2, 25),
            "kw": rng.choice(("var", "let")) if variant != "outer" else "let",
        }
        code = self._render(p, p["kw"])
        progs = {"key": code}
        if variant == "outer":
            progs["captures_value"] = self._render(p, "let", snapshot=True)
            progs["shared_binding"] = self._render(p, "var")
        elif p["kw"] == "var":
            progs["captures_value"] = self._render(p, "let")
        else:
            progs["shared_binding"] = self._render(p, "var")
        return JsSpec(code, progs, p)

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        p = spec.data
        key = norm_output(outs["key"])
        wrong = [(norm_output(outs[t]), t) for t in ("captures_value", "shared_binding") if t in outs]
        if p["variant"] != "outer" and p["kw"] == "var":
            last = (p["e"] - 1) * p["k"] + p["b"]
            wrong.append((" ".join([str(last)] * p["n"]), "last_index"))
        dist = text_distractors(key, wrong)
        if p["variant"] == "outer":
            main = (
                f"let в заголовке for даёт каждой итерации свою i, но переменная {p['acc']} одна на все функции. "
                f"Функции вызываются после цикла, когда {p['acc']} = {p['a0']} + {p['n']}·{p['step']} = "
                f"{p['a0'] + p['n'] * p['step']}. Вывод: «{key}»."
            )
        elif p["kw"] == "var":
            main = (
                f"var создаёт одну переменную i на весь цикл. Функции вызываются после цикла, когда i = {p['e']} "
                f"(условие i < {p['e']} стало ложным), поэтому все результаты одинаковы: "
                f"{p['e']}·{p['k']} + {p['b']}. Вывод: «{key}»."
            )
        else:
            main = (
                f"let в заголовке for создаёт новую переменную i на каждой итерации, и каждая функция помнит свою: "
                f"i = {p['s']}…{p['e'] - 1}. Вывод: «{key}»."
            )
        rendered = Rendered(
            prompt="Что выведет этот код?",
            code=spec.code,
            language="javascript",
            answer_mode="text",
            answer_hint="Числа по порядку через пробел",
        )
        return Instance(self.id, seed, rendered, key, dist, explain(main, dist, _CL_HINTS))


# =====================================================================================
# js.this_binding — метод, стрелочное поле класса, bind против call, «потерянный» this
# =====================================================================================

_THIS_NAMES = [
    {"cls": "Counter", "field": "value", "meth": "increase", "arrow": "handleIncrease", "a": "counter"},
    {"cls": "Wallet", "field": "balance", "meth": "deposit", "arrow": "handleDeposit", "a": "wallet"},
    {"cls": "Score", "field": "points", "meth": "add", "arrow": "handleAdd", "a": "score"},
    {"cls": "Cart", "field": "total", "meth": "addItem", "arrow": "handleAddItem", "a": "cart"},
    {"cls": "Timer", "field": "elapsed", "meth": "tick", "arrow": "handleTick", "a": "timer"},
    {"cls": "Stock", "field": "units", "meth": "restock", "arrow": "handleRestock", "a": "stock"},
]
_THIS_OTHER = ["other", "widget", "proxy", "mirror", "copy", "panel"]

_THIS_HINTS = {
    "this_is_owner": "обычный метод не помнит объект, где он объявлен: this — объект слева от точки при вызове.",
    "arrow_dynamic": "стрелочная функция берёт this из места создания (экземпляр класса), а не из вызова.",
    "call_overrides_bind": "функцию, полученную через bind, нельзя перепривязать через call: bind сильнее.",
    "lost_this_global": "тело класса всегда в строгом режиме: у функции без объекта this = undefined, и обращение к полю бросает TypeError.",
}


@register
class ThisBinding(JsExecFamily):
    id = "js.this_binding"
    title = "this: метод, стрелочное поле, bind и call"
    specs = ("frontend_js",)
    module = "js_core"
    format = "predict_output"
    target_grade = "middle"
    position = -0.5
    c = 0.0
    time_limit_s = 150

    def normalize(self, answer):
        return norm_output(answer)

    @staticmethod
    def _render(p: dict, mode: str = "") -> str:
        n = p["names"]
        cls, f, m, ar, a, o = n["cls"], n["field"], n["meth"], n["arrow"], n["a"], p["other"]
        if mode == "arrow_dynamic":
            arrow_def = [
                f"  {ar}(amount) {{",
                f"    this.{f} += amount;",
                f"    return this.{f};",
                "  }",
            ]
        else:
            arrow_def = [
                f"  {ar} = (amount) => {{",
                f"    this.{f} += amount;",
                f"    return this.{f};",
                "  };",
            ]
        lines = [
            f"class {cls} {{",
            f"  {f} = {p['s1']};",
            "",
            f"  {m}(amount) {{",
            f"    this.{f} += amount;",
            f"    return this.{f};",
            "  }",
            "",
            *arrow_def,
            "}",
            "",
            f"const {a} = new {cls}();",
            f"const {o} = {{",
            f"  {f}: {p['s2']},",
            f"  {m}: {a}.{m}{'.bind(' + a + ')' if mode == 'this_is_owner' else ''},",
            f"  {ar}: {a}.{ar},",
            "};",
            "",
        ]
        bind = "" if mode == "call_overrides_bind" else f".bind({a})"
        ops = {
            "method": [f"console.log({o}.{m}({p['x']}));"],
            "arrow": [f"console.log({o}.{ar}({p['y']}));"],
            "bound": [f"const bound = {a}.{m}{bind};", f"console.log(bound.call({o}, {p['z']}));"],
            "detached": [
                f"const detached = {a}.{m};",
                "try {",
                f"  detached({p['w']});",
                "} catch (e) {",
                '  console.log("error");',
                "}",
            ],
        }
        for op in p["order"]:
            lines += ops[op]
        lines.append(f"console.log({a}.{f}, {o}.{f});")
        return "\n".join(lines)

    def build(self, rng: random.Random) -> JsSpec:
        order = ["method", "arrow", "bound", "detached"]
        rng.shuffle(order)
        p = {
            "names": pick(rng, _THIS_NAMES),
            "other": pick(rng, _THIS_OTHER),
            "s1": rng.randint(1, 60),
            "s2": rng.randrange(100, 900, 10),
            "x": rng.randint(1, 9),
            "y": rng.randint(1, 9),
            "z": rng.randint(1, 9),
            "w": rng.randint(1, 9),
            "order": order,
        }
        code = self._render(p)
        progs = {"key": code, **{t: self._render(p, t) for t in ("this_is_owner", "arrow_dynamic",
                                                                  "call_overrides_bind")}}
        return JsSpec(code, progs, p)

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        p = spec.data
        key = norm_output(outs["key"])
        wrong = [(norm_output(outs[t]), t) for t in ("this_is_owner", "arrow_dynamic", "call_overrides_bind")]
        no_err = [ln for ln in outs["key"].split("\n") if ln != "error"]
        wrong.append((norm_output(" ".join(no_err)), "lost_this_global"))
        dist = text_distractors(key, wrong)
        n = p["names"]
        main = (
            f"{p['other']}.{n['meth']}(…) — обычный метод, this = {p['other']} (объект слева от точки). "
            f"{n['arrow']} — стрелочное поле класса: this навсегда равен экземпляру {n['a']}. "
            f"bound создан через bind({n['a']}), и call его не перепривязывает. "
            f"detached вызывается без объекта; код класса строгий, this = undefined → TypeError → «error». "
            f"Вывод: «{key}»."
        )
        rendered = Rendered(
            prompt="Что выведет этот код?",
            code=spec.code,
            language="javascript",
            answer_mode="text",
            answer_hint=HINT_LINES,
        )
        return Instance(self.id, seed, rendered, key, dist, explain(main, dist, _THIS_HINTS))


# =====================================================================================
# js.prototype_chain — чтение через цепочку, запись создаёт своё свойство, общий объект на прототипе
# =====================================================================================

_PROTO_CTOR = [
    {"ctor": "Widget", "list": "events", "count": "clicks", "track": "track", "arg": "event",
     "items": ["open", "scroll", "close", "submit", "hover"]},
    {"ctor": "Player", "list": "history", "count": "plays", "track": "play", "arg": "song",
     "items": ["intro", "solo", "outro", "remix", "live"]},
    {"ctor": "Tab", "list": "visited", "count": "opens", "track": "open", "arg": "url",
     "items": ["/home", "/cart", "/faq", "/news", "/help"]},
    {"ctor": "Logger", "list": "entries", "count": "writes", "track": "write", "arg": "message",
     "items": ["start", "retry", "fail", "done", "skip"]},
    {"ctor": "Form", "list": "errors", "count": "submits", "track": "submit", "arg": "field",
     "items": ["email", "phone", "name", "city", "zip"]},
]

_PROTO_OBJ = [
    {"base": "defaults", "num": "retries", "obj": "headers", "init": "accept: \"json\"",
     "keys": ["auth", "traceId", "locale", "version"]},
    {"base": "baseConfig", "num": "timeout", "obj": "params", "init": "page: 1",
     "keys": ["limit", "sort", "query", "region"]},
    {"base": "proto", "num": "attempts", "obj": "meta", "init": "source: \"web\"",
     "keys": ["tag", "owner", "team", "stage"]},
    {"base": "template", "num": "priority", "obj": "labels", "init": "kind: \"task\"",
     "keys": ["sprint", "author", "epic", "area"]},
]

_PROTO_HINTS = {
    "proto_copied": "свойства прототипа не копируются в объект при создании: объекты читают их по цепочке.",
    "assign_through": "присваивание (в том числе +=) создаёт собственное свойство объекта, прототип не меняется.",
    "copy_on_write": "push и запись в поле вложенного объекта меняют тот самый общий объект на прототипе.",
}


@register
class PrototypeChain(JsExecFamily):
    id = "js.prototype_chain"
    title = "Прототипы: общее состояние и собственные свойства"
    specs = ("frontend_js",)
    module = "js_core"
    format = "predict_output"
    target_grade = "senior"
    position = 0.3
    c = 0.0
    time_limit_s = 150

    def normalize(self, answer):
        return norm_output(answer)

    @staticmethod
    def _render_ctor(p: dict, mode: str = "") -> str:
        d = p["d"]
        C, L, N, T, A = d["ctor"], d["list"], d["count"], d["track"], d["arg"]
        ctor_body = ["  this.name = name;"]
        if mode == "proto_copied":
            ctor_body += [f"  this.{L} = [];", f"  this.{N} = {p['c0']};"]
        push = f"  this.{L} = [...this.{L}, {A}];" if mode == "copy_on_write" else f"  this.{L}.push({A});"
        inc = (f"  Object.getPrototypeOf(this).{N} += 1;" if mode == "assign_through"
               else f"  this.{N} += 1;")
        lines = [
            f"function {C}(name) {{",
            *ctor_body,
            "}",
            f"{C}.prototype.{L} = [];",
            f"{C}.prototype.{N} = {p['c0']};",
            f"{C}.prototype.{T} = function ({A}) {{",
            push,
            inc,
            "};",
            "",
            f'const {p["a"]} = new {C}("{p["a"]}");',
            f'const {p["b"]} = new {C}("{p["b"]}");',
        ]
        lines += [f'{who}.{T}("{item}");' for who, item in p["calls"]]
        lines += [
            "",
            f"console.log({p['a']}.{L}.length, {p['b']}.{L}.length);",
            f"console.log({p['a']}.{N}, {p['b']}.{N}, {C}.prototype.{N});",
            f'console.log(Object.keys({p["b"]}).length, "{N}" in {p["b"]}, {p["b"]}.hasOwnProperty("{L}"));',
        ]
        return "\n".join(lines)

    @staticmethod
    def _render_obj(p: dict, mode: str = "") -> str:
        d = p["d"]
        base, num, obj = d["base"], d["num"], d["obj"]
        c1, c2 = p["c1"], p["c2"]
        if mode == "proto_copied":
            mk1 = f"const {c1} = JSON.parse(JSON.stringify({base}));"
            mk2 = f"const {c2} = JSON.parse(JSON.stringify({base}));"
        else:
            mk1 = f"const {c1} = Object.create({base});"
            mk2 = f"const {c2} = Object.create({base});"
        inc = (f"Object.getPrototypeOf({c1}).{num} += {p['d1']};" if mode == "assign_through"
               else f"{c1}.{num} += {p['d1']};")
        if mode == "copy_on_write":
            w1 = f'{c1}.{obj} = {{ ...{c1}.{obj}, {p["k1"]}: "{p["v1"]}" }};'
            w2 = f'{c2}.{obj} = {{ ...{c2}.{obj}, {p["k2"]}: "{p["v2"]}" }};'
        else:
            w1 = f'{c1}.{obj}.{p["k1"]} = "{p["v1"]}";'
            w2 = f'{c2}.{obj}.{p["k2"]} = "{p["v2"]}";'
        lines = [
            f"const {base} = {{",
            f"  {num}: {p['r0']},",
            f"  {obj}: {{ {d['init']} }},",
            "};",
            mk1,
            mk2,
            "",
            inc,
            w1,
            w2,
            f"{base}.{num} = {p['r1']};",
            "",
            f"console.log({c1}.{num}, {c2}.{num});",
            f"console.log(Object.keys({c2}.{obj}).length, Object.keys({c1}).length);",
            f'console.log({c1}.{obj} === {c2}.{obj}, "{num}" in {c2}, {c2}.hasOwnProperty("{num}"));',
        ]
        return "\n".join(lines)

    def build(self, rng: random.Random) -> JsSpec:
        if rng.random() < 0.5:
            d = pick(rng, _PROTO_CTOR)
            a, b = rng.sample(["first", "second", "main", "side", "left", "right"], 2)
            ka, kb = rng.randint(1, 4), rng.randint(1, 3)
            who = [a] * ka + [b] * kb
            rng.shuffle(who)
            calls = [(w, pick(rng, d["items"])) for w in who]
            p = {"kind": "ctor", "d": d, "a": a, "b": b, "c0": rng.randint(0, 400), "calls": calls,
                 "ka": ka, "kb": kb}
            render = self._render_ctor
        else:
            d = pick(rng, _PROTO_OBJ)
            k1, k2 = rng.sample(d["keys"], 2)
            c1, c2 = rng.sample(["admin", "client", "worker", "guest", "mobile", "desktop"], 2)
            p = {"kind": "obj", "d": d, "c1": c1, "c2": c2, "k1": k1, "k2": k2,
                 "v1": pick(rng, ["x1", "on", "a7", "ok", "v2"]), "v2": pick(rng, ["y3", "off", "b9", "no", "v5"]),
                 "r0": rng.randint(1, 60), "d1": rng.randint(2, 40), "r1": rng.randint(61, 199)}
            render = self._render_obj
        code = render(p)
        progs = {"key": code, **{t: render(p, t) for t in _PROTO_HINTS}}
        return JsSpec(code, progs, p)

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        p = spec.data
        key = norm_output(outs["key"])
        dist = text_distractors(key, [(norm_output(outs[t]), t) for t in _PROTO_HINTS])
        d = p["d"]
        if p["kind"] == "ctor":
            main = (
                f"Массив {d['list']} лежит на прототипе и общий для всех объектов: push меняет его, поэтому "
                f"длина у обоих = {p['ka'] + p['kb']}. «this.{d['count']} += 1» читает значение по цепочке, а "
                f"записывает уже собственное свойство объекта; на прототипе остаётся {p['c0']}. Собственные "
                f"ключи {p['b']}: name и {d['count']}; оператор in видит и унаследованные свойства, "
                f"hasOwnProperty — только собственные. Вывод: «{key}»."
            )
        else:
            main = (
                f"{p['c1']} += создаёт собственное {d['num']} = {p['r0']} + {p['d1']}; у {p['c2']} своего "
                f"свойства нет, он читает {d['base']}.{d['num']}, которое потом стало {p['r1']}. Объект "
                f"{d['obj']} один на всех: обе записи попали в него. Вывод: «{key}»."
            )
        rendered = Rendered(
            prompt="Что выведет этот код?",
            code=spec.code,
            language="javascript",
            answer_mode="text",
            answer_hint=HINT_LINES,
        )
        return Instance(self.id, seed, rendered, key, dist, explain(main, dist, _PROTO_HINTS))
