"""js_core: асинхронность — цепочки промисов, порядок event loop, ошибки async/await.

Ключи вычисляются исполнением в Node. Для bug_line исполняются исходный код и КАЖДЫЙ вариант
исправления со скрытой обвязкой (моками API): правильное исправление обязано дать ожидаемый вывод,
неверные — нет; иначе генерация падает (ключ не может разойтись с поведением кода).
"""

from __future__ import annotations

import json
import random
import re

from app.testing.bank.base import Instance, Option, Rendered, canonical, register
from app.testing.bank.families.frontend_js._common import (
    JsExecFamily,
    JsSpec,
    explain,
    fill,
    norm_line_choice,
    norm_output,
    pick,
    text_distractors,
)

# =====================================================================================
# js.promise_chain — throw пропускает then до catch, catch восстанавливает цепочку,
# then без return даёт undefined, finally не меняет значение
# =====================================================================================

_PC_NAMES = [("loadPrice", "price"), ("fetchStock", "stock"), ("getBalance", "balance"),
             ("loadScore", "score"), ("fetchQuota", "quota"), ("loadLimit", "limit")]
_PC_MSG = ["too large", "limit exceeded", "overflow", "not allowed"]

_PC_TEMPLATE = """
function <<load>>(id) {
  return Promise.resolve(id * <<m>>);
}

<<load>>(<<id>>)
  .then((<<v>>) => {
    console.log(<<v>>);
    if (<<v>> > <<lim>>) throw new Error("<<msg>>");
    return <<v>> + <<p>>;
  })
  .then((<<v>>) => {
    console.log(<<v>>);
    return <<v>> * 2;
  })
  .catch(() => <<r>>)
  .then((<<v>>) => {
    console.log(<<v>>);<<ret>>
  })
  <<fin>>(() => <<f>>)
  .then((<<v>>) => console.log(<<v>>));
"""

_PC_HINTS = {
    "finally_replaces_value": "finally не меняет значение цепочки: то, что вернул его колбэк, игнорируется.",
    "then_passes_value": "then без return возвращает undefined — следующий обработчик получит undefined.",
    "catch_stops_chain": "catch возвращает значение и превращает цепочку обратно в успешную: следующие then выполнятся.",
}


@register
class PromiseChain(JsExecFamily):
    id = "js.promise_chain"
    title = "Цепочка промисов: throw, catch, finally"
    specs = ("frontend_js",)
    module = "js_core"
    format = "predict_output"
    target_grade = "middle"
    position = 0.0
    c = 0.0
    time_limit_s = 120
    is_anchor = True

    def normalize(self, answer):
        return norm_output(answer)

    @staticmethod
    def _render(p: dict, mode: str = "") -> str:
        q = dict(p)
        q["fin"] = ".then" if mode == "finally_replaces_value" else ".finally"
        q["ret"] = f"\n    return {p['v']};" if mode == "then_passes_value" else ""
        code = fill(_PC_TEMPLATE, **q).strip()
        if mode == "catch_stops_chain":
            code = code.replace(f".catch(() => {p['r']})", ".catch(() => new Promise(() => {}))")
        return code

    def build(self, rng: random.Random) -> JsSpec:
        load, v = pick(rng, _PC_NAMES)
        m = rng.randint(3, 9)
        ident = rng.randint(11, 99)
        x = ident * m
        throws = rng.random() < 0.5
        lim = x - rng.randint(1, x // 3) if throws else x + rng.randint(1, 200)
        p = {"load": load, "v": v, "m": m, "id": ident, "lim": lim, "msg": pick(rng, _PC_MSG),
             "p": rng.randint(2, 40), "r": rng.randint(1, 99), "f": rng.randint(100, 999), "throws": throws}
        code = self._render(p)
        progs = {"key": code, **{t: self._render(p, t) for t in _PC_HINTS}}
        return JsSpec(code, progs, p)

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        p = spec.data
        key = norm_output(outs["key"])
        dist = text_distractors(key, [(norm_output(outs[t]), t) for t in _PC_HINTS])
        x = p["id"] * p["m"]
        if p["throws"]:
            flow = (f"{x} > {p['lim']} — бросается ошибка, следующий then пропускается, catch возвращает {p['r']}, "
                    f"и цепочка снова успешна: then печатает {p['r']}")
        else:
            flow = (f"{x} ≤ {p['lim']} — ошибки нет: дальше {x} + {p['p']} = {x + p['p']}, его печатает второй then, "
                    f"catch пропускается, третий then получает {2 * (x + p['p'])} и печатает его")
        main = (
            f"Первый then печатает {x}. {flow}. Этот then ничего не возвращает, поэтому дальше идёт undefined; "
            f"finally не меняет значение (его {p['f']} игнорируется), последний then печатает undefined. "
            f"Вывод: «{key}»."
        )
        rendered = Rendered(
            prompt="Что выведет этот код?",
            code=spec.code,
            language="javascript",
            answer_mode="text",
            answer_hint="Все строки вывода по порядку через пробел",
        )
        return Instance(self.id, seed, rendered, key, dist, explain(main, dist, _PC_HINTS))


# =====================================================================================
# js.event_loop_order — синхронный код, микрозадачи (then, await, queueMicrotask), таймеры
# =====================================================================================

_EL_FN = ["loadUser", "saveDraft", "syncCart", "fetchFeed", "initApp", "trackView"]
_LATIN = "ABCDEFGH"
_CYR_TO_LAT = str.maketrans({"А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H",
                             "а": "A", "в": "B", "с": "C", "е": "E", "н": "H"})

_EL_HINTS = {
    "timers_first": "setTimeout(…, 0) — макрозадача: она выполнится только после того, как опустеет очередь микрозадач.",
    "executor_async": "функция-исполнитель new Promise(…) выполняется синхронно, сразу при создании промиса.",
    "await_sync": "код после await продолжается в микрозадаче, а не сразу: сначала доработает синхронный код.",
    "chain_flat": "следующий then цепочки ставится в очередь только когда выполнился предыдущий — на следующем «круге».",
    "source_order": "асинхронные колбэки выполняются не в порядке строк: сначала весь синхронный код, затем микрозадачи, затем таймеры.",
}


def _norm_labels(answer) -> str:
    s = str(answer).translate(_CYR_TO_LAT).upper()
    return re.sub(r"[^A-Z]", "", s)


@register
class EventLoopOrder(JsExecFamily):
    id = "js.event_loop_order"
    title = "Порядок вывода: синхронный код, микрозадачи, таймеры"
    specs = ("frontend_js",)
    module = "js_core"
    format = "predict_output"
    target_grade = "senior"
    position = -0.5
    c = 0.0
    time_limit_s = 180
    is_anchor = True

    def normalize(self, answer):
        return _norm_labels(answer)

    @staticmethod
    def _blocks(p: dict, mode: str = "") -> dict[str, list[str]]:
        L = p["labels"]
        chain_flat = mode == "chain_flat"
        if p["chain_form"] == "then":
            chain = (["Promise.resolve().then(() => {", f'  console.log("{L["chain0"]}");',
                      f'  console.log("{L["chain1"]}");', "});"] if chain_flat else
                     ["Promise.resolve()", f'  .then(() => console.log("{L["chain0"]}"))',
                      f'  .then(() => console.log("{L["chain1"]}"));'])
        else:
            chain = (["queueMicrotask(() => {", f'  console.log("{L["chain0"]}");',
                      f'  console.log("{L["chain1"]}");', "});"] if chain_flat else
                     ["queueMicrotask(() => {", f'  console.log("{L["chain0"]}");',
                      f'  queueMicrotask(() => console.log("{L["chain1"]}"));', "});"])
        exec_first = (f'  queueMicrotask(() => console.log("{L["exec0"]}"));' if mode == "executor_async"
                      else f'  console.log("{L["exec0"]}");')
        return {
            "timer": [f'setTimeout(() => console.log("{L["timer"]}"), 0);'],
            "chain": chain,
            "exec": ["new Promise((resolve) => {", exec_first, "  resolve();",
                     f'}}).then(() => console.log("{L["exec1"]}"));'],
            "async": [f"{p['fn']}();"],
            "sync": [f'console.log("{L["sync"]}");'],
        }

    def _render(self, p: dict, mode: str = "") -> str:
        L = p["labels"]
        decl = [f"async function {p['fn']}() {{", f'  console.log("{L["async0"]}");']
        if mode != "await_sync":
            decl.append("  await Promise.resolve();")
        decl += [f'  console.log("{L["async1"]}");', "}"]
        blocks = self._blocks(p, mode)
        body: list[str] = []
        for b in p["order"]:
            body += blocks[b]
            if len(blocks[b]) > 1:
                body.append("")
        while body and body[-1] == "":
            body.pop()
        return "\n".join(decl + [""] + body)

    def build(self, rng: random.Random) -> JsSpec:
        slots = ["timer", "chain0", "chain1", "exec0", "exec1", "async0", "async1", "sync"]
        letters = list(_LATIN)
        rng.shuffle(letters)
        order = ["timer", "chain", "exec", "async", "sync"]
        rng.shuffle(order)
        p = {
            "labels": dict(zip(slots, letters, strict=True)),
            "order": order,
            "fn": pick(rng, _EL_FN),
            "chain_form": rng.choice(("then", "then", "qm")),
        }
        code = self._render(p)
        progs = {"key": code, **{t: self._render(p, t) for t in ("executor_async", "await_sync", "chain_flat")}}
        return JsSpec(code, progs, p)

    @staticmethod
    def _source_order(p: dict) -> str:
        L = p["labels"]
        seq = [L["async0"], L["async1"]]  # тело объявленной первой функции
        per_block = {"timer": ["timer"], "chain": ["chain0", "chain1"], "exec": ["exec0", "exec1"],
                     "async": [], "sync": ["sync"]}
        for b in p["order"]:
            seq += [L[s] for s in per_block[b]]
        return "".join(seq)

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        p = spec.data
        L = p["labels"]
        key = _norm_labels(outs["key"])
        sync_labels = {L["sync"], L["exec0"], L["async0"]}
        sync_part = [c for c in key if c in sync_labels]
        rest = [c for c in key if c not in sync_labels and c != L["timer"]]
        timers_first = "".join(sync_part + [L["timer"]] + rest)
        wrong = [(_norm_labels(outs[t]), t) for t in ("executor_async", "await_sync", "chain_flat")]
        wrong += [(timers_first, "timers_first"), (self._source_order(p), "source_order")]
        dist = text_distractors(key, wrong)
        micro = "".join(c for c in key if c not in sync_labels and c != L["timer"])
        main = (
            f"Сначала выполняется весь синхронный код: тело {p['fn']}() до await, исполнитель new Promise "
            f"и обычный console.log — {''.join(sync_part)}. Затем очередь микрозадач в порядке постановки: "
            f"продолжение после await, then и queueMicrotask; вложенные then/queueMicrotask встают в конец "
            f"очереди — {micro}. Последним выполняется таймер — {L['timer']}. Вывод: «{key}»."
        )
        rendered = Rendered(
            prompt="В каком порядке будут выведены метки? Код выполняется в Node.js или в браузере.",
            code=spec.code,
            language="javascript",
            answer_mode="text",
            answer_hint="Метки (латинские буквы) в порядке вывода, например: B A C … — пробелы не важны",
        )
        return Instance(self.id, seed, rendered, key, dist, explain(main, dist, _EL_HINTS))


# =====================================================================================
# js.async_await_errors — bug_line: forEach с async, return без await в try, промис в условии
# =====================================================================================

_AE_HINTS = {
    "await_foreach": "forEach всегда возвращает undefined и не ждёт асинхронные колбэки — await перед ним ничего не даёт.",
    "drop_async": "без await счётчик посчитается, но функция вернётся раньше, чем завершатся сохранения.",
    "await_return": "ошибка не в return: к моменту return колбэки forEach ещё ждут ответа сервера.",
    "wrap_resolve": "Promise.resolve(p) возвращает тот же отклоняемый промис — try/catch его по-прежнему не ловит.",
    "await_no_return": "await без return ловит ошибку, но в успешном случае функция вернёт undefined вместо данных.",
    "then_passthrough": "then не обрабатывает отклонение: промис по-прежнему отклоняется мимо catch.",
    "compare_promise": "функция возвращает промис, а промис никогда не равен true — проверка всегда ложна.",
    "coerce_promise": "любой промис — объект, а значит, всегда истинен, даже если внутри false.",
    "then_in_condition": "then возвращает новый промис, и он тоже всегда истинен — условие не ждёт результат.",
}

_AE_PADS = {
    "foreach": ["  if (!api) throw new Error(\"api is required\");",
                "  if (<<items>>.length === 0) return 0;",
                "  // сохраняем каждую позицию на сервере"],
    "try": ["  if (!api) throw new Error(\"api is required\");",
            "  // профиль может отсутствовать — тогда показываем гостя"],
    "cond": ["  if (<<cart>>.length === 0) return \"empty\";",
             "  // заблокированным пользователям оформление запрещено"],
}

_AE_NAMES = {
    "foreach": [
        {"fn": "syncCart", "items": "items", "it": "item", "save": "saveItem", "cnt": "saved"},
        {"fn": "uploadPhotos", "items": "photos", "it": "photo", "save": "upload", "cnt": "uploaded"},
        {"fn": "sendInvites", "items": "emails", "it": "email", "save": "invite", "cnt": "sent"},
        {"fn": "archiveTasks", "items": "tasks", "it": "task", "save": "archive", "cnt": "archived"},
    ],
    "try": [
        {"fn": "loadProfile", "id": "userId", "fetch": "fetchProfile", "fallback": "guest"},
        {"fn": "loadCompany", "id": "companyId", "fetch": "fetchCompany", "fallback": "unknown"},
        {"fn": "loadAuthor", "id": "authorId", "fetch": "fetchAuthor", "fallback": "anonymous"},
    ],
    "cond": [
        {"fn": "checkout", "user": "user", "cart": "cart", "check": "isBlocked"},
        {"fn": "placeOrder", "user": "buyer", "cart": "items", "check": "isBanned"},
        {"fn": "bookSlot", "user": "client", "cart": "services", "check": "isSuspended"},
    ],
}


@register
class AsyncAwaitErrors(JsExecFamily):
    id = "js.async_await_errors"
    title = "Ошибки async/await: найти строку и исправление"
    specs = ("frontend_js",)
    module = "js_core"
    format = "bug_line"
    target_grade = "senior"
    position = 0.0
    c = 0.25  # выбор исправления из 4 (строка + исправление; угадывание оцениваем по исправлению)
    time_limit_s = 150

    def normalize(self, answer):
        return norm_line_choice(answer)

    def build(self, rng: random.Random) -> JsSpec:
        kind = pick(rng, ["foreach", "try", "cond"])
        n = dict(pick(rng, _AE_NAMES[kind]))
        pads = [fill(x, **n) for x in _AE_PADS[kind] if rng.random() < 0.5]
        if kind == "foreach":
            body_cb = f"async ({n['it']}) => {{ await api.{n['save']}({n['it']}); {n['cnt']} += 1; }}"
            lines = [f"async function {n['fn']}({n['items']}, api) {{", f"  let {n['cnt']} = 0;", *pads,
                     f"  {n['items']}.forEach({body_cb});", f"  return {n['cnt']};", "}"]
            bug = 3 + len(pads)  # 1-based номер строки
            ret = bug + 1
            fixes = [
                (bug, f"  await Promise.all({n['items']}.map({body_cb}));", None),
                (bug, f"  await {n['items']}.forEach({body_cb});", "await_foreach"),
                (bug, f"  {n['items']}.forEach(({n['it']}) => {{ api.{n['save']}({n['it']}); {n['cnt']} += 1; }});",
                 "drop_async"),
                (ret, f"  return await {n['cnt']};", "await_return"),
            ]
            vals = rng.sample(["a1", "b2", "c3", "d4", "e5", "f6", "g7"], rng.randint(2, 5))
            harness = [
                "const __done = [];",
                f"const __api = {{ {n['save']}: (x) => new Promise((r) => setTimeout(() => {{ __done.push(x); r(); }}, 0)) }};",
                f"{n['fn']}({json.dumps(vals)}, __api).then((n) => console.log(n, __done.length));",
            ]
            expected = f"{len(vals)} {len(vals)}"
            prompt = (f"Функция {n['fn']} должна дождаться сохранения всех элементов и вернуть их количество, "
                      f"но возвращает 0, а часть запросов ещё не завершена. Найдите строку с ошибкой и выберите исправление.")
        elif kind == "try":
            lines = [f"async function {n['fn']}({n['id']}, api) {{", *pads, "  try {",
                     f"    return api.{n['fetch']}({n['id']});", "  } catch (err) {",
                     f"    return {{ id: {n['id']}, name: \"{n['fallback']}\" }};", "  }", "}"]
            bug = 3 + len(pads)
            fixes = [
                (bug, f"    return await api.{n['fetch']}({n['id']});", None),
                (bug, f"    return Promise.resolve(api.{n['fetch']}({n['id']}));", "wrap_resolve"),
                (bug, f"    await api.{n['fetch']}({n['id']});", "await_no_return"),
                (bug, f"    return api.{n['fetch']}({n['id']}).then((data) => data);", "then_passthrough"),
            ]
            good = rng.randint(2, 999)
            harness = [
                f"const __api = {{ {n['fetch']}: (id) => (id > 0 ? Promise.resolve({{ id, name: \"user\" + id }}) "
                f": Promise.reject(new Error(\"not found\"))) }};",
                f"Promise.all([{n['fn']}({good}, __api), {n['fn']}(-1, __api)])",
                "  .then(([a, b]) => console.log(a.name, b.name))",
                '  .catch(() => console.log("rejected"));',
            ]
            expected = f"user{good} {n['fallback']}"
            prompt = (f"Если запись не найдена, {n['fn']} должна вернуть запасной объект «{n['fallback']}», "
                      "но вызывающий код получает отклонённый промис. Найдите строку с ошибкой и выберите исправление.")
        else:
            lines = [f"async function {n['fn']}({n['user']}, {n['cart']}, api) {{", *pads,
                     f"  if (api.{n['check']}({n['user']}.id)) {{", '    return "blocked";', "  }",
                     f"  const total = {n['cart']}.reduce((sum, item) => sum + item.price, 0);",
                     "  return `ok ${total}`;", "}"]
            bug = 2 + len(pads)
            call = f"api.{n['check']}({n['user']}.id)"
            fixes = [
                (bug, f"  if (await {call}) {{", None),
                (bug, f"  if ({call} === true) {{", "compare_promise"),
                (bug, f"  if (Boolean({call})) {{", "coerce_promise"),
                (bug, f"  if ({call}.then((blocked) => blocked)) {{", "then_in_condition"),
            ]
            bad, ok = rng.sample(range(10, 99), 2)
            p1, p2 = rng.randrange(100, 900, 10), rng.randrange(100, 900, 10)
            harness = [
                f"const __api = {{ {n['check']}: async (id) => id === {bad} }};",
                f"const __cart = [{{ price: {p1} }}, {{ price: {p2} }}];",
                f"Promise.all([{n['fn']}({{ id: {bad} }}, __cart, __api), {n['fn']}({{ id: {ok} }}, __cart, __api)])",
                '  .then((r) => console.log(r.join(" ")));',
            ]
            expected = f"blocked ok {p1 + p2}"
            prompt = (f"Заблокированные пользователи должны получать «blocked», остальные — «ok» и сумму, "
                      f"но {n['fn']} возвращает «blocked» всем. Найдите строку с ошибкой и выберите исправление.")
        rng.shuffle(fixes)
        code = "\n".join(lines)
        progs = {"orig": code + "\n\n" + "\n".join(harness)}
        for i, (ln, text, _tag) in enumerate(fixes):
            patched = list(lines)
            patched[ln - 1] = text
            progs[f"fix{i}"] = "\n".join(patched) + "\n\n" + "\n".join(harness)
        return JsSpec(code, progs, {"kind": kind, "lines": lines, "fixes": fixes, "bug": bug,
                                    "expected": expected, "prompt": prompt, "n": n})

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        d = spec.data
        expected = d["expected"]
        # Проверка исполнением: баг воспроизводится, верное исправление чинит, неверные — нет.
        if outs["orig"].strip() == expected:
            raise ValueError("исходный код не воспроизводит ошибку")
        ids = "abcd"
        options = []
        key = None
        dist: dict[str, str] = {}
        for i, (ln, text, tag) in enumerate(d["fixes"]):
            got = outs[f"fix{i}"].strip()
            if (tag is None) != (got == expected):
                raise ValueError(f"исправление {tag or 'верное'} ведёт себя не так, как задумано: {got!r}")
            options.append((ids[i], text.strip()))
            if tag is None:
                key = {"line": ln, "fix": ids[i]}
            else:
                dist[canonical({"line": ln, "fix": ids[i]})] = tag
        if key is None:
            raise ValueError("нет верного исправления")
        lines = tuple(d["lines"])
        bug_text = d["lines"][d["bug"] - 1].strip()
        fix_text = dict(options)[key["fix"]]
        main = {
            "foreach": "forEach не ждёт промисы, которые возвращает async-колбэк: функция доходит до return сразу, "
                       "пока запросы ещё в полёте. Promise.all(items.map(async …)) возвращает промис, который "
                       "выполняется, когда завершены все колбэки, и await дожидается его.",
            "try": "return промиса без await внутри try возвращает промис как есть: отклонение произойдёт уже после "
                   "выхода из try, и catch его не увидит. «return await» дожидается результата внутри try.",
            "cond": "async-функция всегда возвращает промис, а любой промис в условии истинен. Нужно дождаться "
                    "значения через await.",
        }[d["kind"]]
        main += f"\n\nОшибка в строке {d['bug']}: «{bug_text}». Исправление: «{fix_text}»."
        opt_text = dict(options)
        show = {}
        for k in dist:
            ans = json.loads(k)
            show[k] = f"строка {ans['line']}, исправление «{opt_text[ans['fix']]}»"
        rendered = Rendered(
            prompt=d["prompt"],
            code="\n".join(lines),
            language="javascript",
            lines=lines,
            options=tuple(Option(i, t) for i, t in options),
            answer_mode="line_choice",
            answer_hint="Выберите строку с ошибкой и вариант исправления для неё",
        )
        return Instance(self.id, seed, rendered, key, dist, explain(main, dist, _AE_HINTS, show))
