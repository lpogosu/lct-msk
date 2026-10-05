"""react: состояние — число рендеров, контролируемый ввод, очередь обновлений, устаревшие замыкания.

React без рендерера в банке не исполняем. Каждое семейство ограничено сценарием с однозначной
документированной семантикой React 18 (createRoot, production-сборка, без StrictMode) и решается
ТОЧНОЙ моделью этого сценария на Python (функции _model_*). Формулировка явно фиксирует допущения
(порядок событий, «каждое событие после завершения рендера»), чтобы ответ не зависел от таймингов.
"""

from __future__ import annotations

import random
import re

from app.testing.bank.base import Family, Instance, Rendered, register
from app.testing.bank.families.frontend_js._common import (
    explain,
    fill,
    make_choice,
    norm_choice,
    norm_exact,
    norm_output,
    pick,
    plural_ru,
    text_distractors,
    times_ru,
)

ASSUME = "React 18 (createRoot), production-сборка без StrictMode."


def _opt_show(ch) -> dict[str, str]:
    return {oid: next(o.text for o in ch.options if o.id == oid) for oid in ch.distractors}


# =====================================================================================
# react.render_count — кто перерисовывается при изменении состояния родителя и ребёнка
# =====================================================================================

_RC_NAMES = [
    {"app": "App", "count": "count", "set": "setCount", "header": "Header", "counter": "Counter",
     "sidebar": "Sidebar", "title": "Магазин"},
    {"app": "Shop", "count": "items", "set": "setItems", "header": "Logo", "counter": "CartButton",
     "sidebar": "Footer", "title": "Каталог"},
    {"app": "Dashboard", "count": "visits", "set": "setVisits", "header": "TopBar", "counter": "VisitCounter",
     "sidebar": "Menu", "title": "Отчёты"},
    {"app": "Profile", "count": "likes", "set": "setLikes", "header": "Avatar", "counter": "LikeButton",
     "sidebar": "Friends", "title": "Профиль"},
    {"app": "Board", "count": "tasks", "set": "setTasks", "header": "Toolbar", "counter": "TaskCounter",
     "sidebar": "Filters", "title": "Задачи"},
]

_RC_CODE = """
function <<app>>() {
  const [<<count>>, <<set>>] = useState(0);
  console.log("<<app>>");
  return (
    <main>
      <<<header>> title="<<title>>" />
      <<<counter>> value={<<count>>} onIncrement={() => <<set>>((c) => c + 1)} />
      <<<sidebar>> />
    </main>
  );
}

function <<header>>({ title }) {
  console.log("<<header>>");
  return <h1>{title}</h1>;
}

function <<counter>>({ value, onIncrement }) {
  const [expanded, setExpanded] = useState(false);
  console.log("<<counter>>");
  return (
    <section>
      <button onClick={onIncrement}>+1 ({value})</button>
      <button onClick={() => setExpanded((e) => !e)}>Подробнее</button>
      {expanded && <p>Всего: {value}</p>}
    </section>
  );
}

function <<sidebar>>() {
  console.log("<<sidebar>>");
  return <aside>Меню</aside>;
}
"""

_RC_HINTS = {
    "no_props_no_rerender": "без memo дочерний компонент перерисовывается при КАЖДОМ рендере родителя, даже если его пропсы не менялись.",
    "local_state_rerenders_all": "состояние expanded живёт в дочернем компоненте: его изменение перерисовывает только этот компонент и его детей.",
    "mount_excluded": "первый рендер (монтирование) тоже выводит сообщение — его нужно посчитать.",
    "parent_rerender_skips_child": "дочерний компонент перерисовывается и когда меняется состояние родителя.",
    "local_state_not_counted": "изменение собственного состояния компонента вызывает его повторный рендер.",
    "double_render": "в production-сборке без StrictMode один клик = один рендер затронутых компонентов.",
    "other": "посчитайте отдельно монтирование, клики «+1» и клики «Подробнее».",
}


def _model_render_count(target: str, n_inc: int, n_toggle: int, flags: frozenset[str] = frozenset()) -> int:
    """Число console.log(target): монтирование + рендеры от «+1» (состояние App) + от «Подробнее»."""
    renders = 0 if "mount_excluded" in flags else 1
    # «+1» меняет состояние App → рендер App и всех детей (memo нет)
    if target == "app":
        renders += n_inc
    elif target == "counter":
        renders += 0 if "parent_rerender_skips_child" in flags else n_inc
    else:
        renders += 0 if "no_props_no_rerender" in flags else n_inc
    # «Подробнее» меняет состояние Counter → рендер только Counter
    if target == "counter":
        renders += 0 if "local_state_not_counted" in flags else n_toggle
    elif "local_state_rerenders_all" in flags:
        renders += n_toggle
    return renders


@register
class RenderCount(Family):
    id = "react.render_count"
    title = "Сколько раз отрендерится компонент"
    specs = ("frontend_js",)
    module = "react"
    format = "predict_output"
    target_grade = "intern"
    position = 0.5
    c = 0.25
    time_limit_s = 90
    is_anchor = True
    enemies = ("react.memo_callback",)

    def normalize(self, answer):
        return norm_choice(answer)

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        names = pick(rng, _RC_NAMES)
        code = fill(_RC_CODE, **names).strip()
        n_inc = rng.randint(2, 6)
        n_toggle = rng.randint(1, 5)
        while n_toggle == n_inc:
            n_toggle = rng.randint(1, 5)
        target = pick(rng, ["app", "header", "counter", "sidebar"])
        correct = _model_render_count(target, n_inc, n_toggle)
        if target == "counter":
            tags = ["parent_rerender_skips_child", "local_state_not_counted", "mount_excluded"]
        elif target == "app":
            tags = ["local_state_rerenders_all", "mount_excluded"]
        else:
            tags = ["no_props_no_rerender", "local_state_rerenders_all", "mount_excluded"]
        wrong = [(str(_model_render_count(target, n_inc, n_toggle, frozenset({t}))), t) for t in tags]
        wrong.append((str(2 * correct - 1), "double_render"))
        wrong += [(str(correct + d), "other") for d in (1, -1, 2)]
        ch = make_choice(rng, str(correct), wrong, k=4)
        comp = names[target]
        if target == "counter":
            why = (f"{comp}: 1 (монтирование) + {n_inc} (каждый «+1» меняет состояние {names['app']}, а он "
                   f"перерисовывает всех детей) + {n_toggle} (собственное состояние expanded) = {correct}.")
        else:
            why = (f"{comp}: 1 (монтирование) + {n_inc} (каждый «+1» меняет состояние {names['app']} и "
                   f"перерисовывает его с детьми). Клики «Подробнее» меняют состояние только "
                   f"{names['counter']} — {comp} они не затрагивают. Итого {correct}.")
        prompt = (
            f"{ASSUME} После монтирования пользователь нажал «+1» {times_ru(n_inc)} и «Подробнее» — "
            f"{times_ru(n_toggle)}. Сколько раз всего в консоли появится «{comp}», считая первый рендер?"
        )
        rendered = Rendered(prompt=prompt, code=code, language="javascript", options=ch.options,
                            answer_mode="choice")
        return Instance(self.id, seed, rendered, ch.key, ch.distractors,
                        explain(why, ch.distractors, _RC_HINTS, _opt_show(ch)))


# =====================================================================================
# react.controlled_input — что окажется в контролируемом поле после посимвольного ввода
# =====================================================================================

_CI_VARIANTS = {
    "digits": {
        "names": [("PhoneField", "phone", "setPhone"), ("CardField", "card", "setCard"),
                  ("PinField", "pin", "setPin"), ("InnField", "inn", "setInn")],
        "body": """
function <<comp>>() {
  const [<<val>>, <<set>>] = useState("");

  function handleChange(e) {
    const digits = e.target.value.replace(/\\D/g, "");
    if (digits.length <= <<max>>) {
      <<set>>(digits);
    }
  }

  return <input value={<<val>>} onChange={handleChange} />;
}
""",
    },
    "reject": {
        "names": [("AmountField", "amount", "setAmount"), ("QtyField", "qty", "setQty"),
                  ("AgeField", "age", "setAge"), ("CodeField", "code", "setCode")],
        "body": """
function <<comp>>() {
  const [<<val>>, <<set>>] = useState("");

  function handleChange(e) {
    const next = e.target.value;
    if (/^\\d*$/.test(next) && next.length <= <<max>>) {
      <<set>>(next);
    }
  }

  return <input value={<<val>>} onChange={handleChange} />;
}
""",
    },
    "upper": {
        "names": [("PromoField", "promo", "setPromo"), ("TicketField", "ticket", "setTicket"),
                  ("PlateField", "plate", "setPlate"), ("VoucherField", "voucher", "setVoucher")],
        "body": """
function <<comp>>() {
  const [<<val>>, <<set>>] = useState("");

  function handleChange(e) {
    const next = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
    <<set>>(next.slice(0, <<max>>));
  }

  return <input value={<<val>>} onChange={handleChange} />;
}
""",
    },
    "trim": {
        "names": [("NameField", "name", "setName"), ("CityField", "city", "setCity"),
                  ("TitleField", "title", "setTitle"), ("TagField", "tag", "setTag")],
        "body": """
function <<comp>>() {
  const [<<val>>, <<set>>] = useState("");

  return (
    <input
      value={<<val>>}
      onChange={(e) => <<set>>(e.target.value.trim())}
    />
  );
}
""",
    },
}

_CI_HINTS = {
    "raw_typed": "поле контролируемое: на экране всегда значение состояния, а не то, что набрал пользователь.",
    "filter_without_max": "обновление, которое нарушает ограничение длины, отклоняется — поле остаётся прежним.",
    "slice_ignored": "slice(0, N) выполняется при каждом вводе: всё, что длиннее N символов, сразу отрезается.",
    "filter_before_upper": "сначала вызывается toUpperCase(), и только потом фильтр — строчные буквы не теряются.",
    "trim_final_only": "trim() вызывается на КАЖДОМ нажатии: пробел в конце строки срезается сразу, поэтому пробелы ввести нельзя.",
}

_WORDS = ["anna", "moscow", "neva", "kazan", "ural", "volga", "sever", "lena", "don", "oka", "sochi", "tula"]


def _model_input(kind: str, typed: str, mx: int, flags: frozenset[str] = frozenset()) -> str:
    """Посимвольный ввод в конец контролируемого поля: значение = state после каждого onChange."""
    if "raw_typed" in flags:
        return typed
    if kind == "trim" and "trim_final_only" in flags:
        return typed.strip()
    cur = ""
    for ch in typed:
        raw = cur + ch
        if kind in ("digits", "reject"):
            if kind == "digits":
                nxt = re.sub(r"\D", "", raw)
                ok = len(nxt) <= mx or "filter_without_max" in flags
            else:
                nxt = raw
                ok = bool(re.fullmatch(r"\d*", raw)) and (len(raw) <= mx or "filter_without_max" in flags)
            if ok:
                cur = nxt
        elif kind == "upper":
            if "filter_before_upper" in flags:
                nxt = re.sub(r"[^A-Z0-9]", "", raw).upper()
            else:
                nxt = re.sub(r"[^A-Z0-9]", "", raw.upper())
            cur = nxt if "slice_ignored" in flags else nxt[:mx]
        else:  # trim: JS trim() снимает пробельные символы с обоих концов
            cur = raw.strip()
    return cur


@register
class ControlledInput(Family):
    id = "react.controlled_input"
    title = "Контролируемое поле ввода: что увидит пользователь"
    specs = ("frontend_js",)
    module = "react"
    format = "predict_output"
    target_grade = "junior"
    position = -0.3
    c = 0.0
    time_limit_s = 90

    def normalize(self, answer):
        return norm_exact(answer)

    @staticmethod
    def _typed(rng: random.Random, kind: str, mx: int) -> str:
        if kind in ("digits", "reject"):
            n_digits = mx + rng.randint(1, 3)
            seps = "-() .+" if kind == "digits" else "a-,. "
            chars = [str(rng.randint(0, 9)) for _ in range(n_digits)]
            for _ in range(rng.randint(2, 4)):
                chars.insert(rng.randint(1, len(chars) - 1), pick(rng, seps))
            if chars[0] == "0":
                chars[0] = str(rng.randint(1, 9))
            return "".join(chars)
        if kind == "upper":
            alnum = "abcdefghjkmnprstuvwxyz0123456789"
            chars = [pick(rng, alnum) for _ in range(mx + rng.randint(2, 4))]
            for _ in range(rng.randint(1, 3)):
                chars.insert(rng.randint(1, len(chars) - 1), pick(rng, " -_"))
            return "".join(chars)
        words = rng.sample(_WORDS, rng.randint(2, 3))
        return " ".join(words)

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        kind = pick(rng, ["digits", "reject", "upper", "trim"])
        v = _CI_VARIANTS[kind]
        comp, val, setter = pick(rng, v["names"])
        mx = rng.randint(4, 8)
        typed = self._typed(rng, kind, mx)
        code = fill(v["body"], comp=comp, val=val, set=setter, max=mx).strip()
        key = norm_exact(_model_input(kind, typed, mx))
        tags = {"digits": ["raw_typed", "filter_without_max"], "reject": ["raw_typed", "filter_without_max"],
                "upper": ["raw_typed", "slice_ignored", "filter_before_upper"],
                "trim": ["raw_typed", "trim_final_only"]}[kind]
        wrong = [(norm_exact(_model_input(kind, typed, mx, frozenset({t}))), t) for t in tags]
        dist = text_distractors(key, wrong)
        why = {
            "digits": f"После каждого символа обработчик оставляет только цифры и принимает значение, пока цифр не "
                      f"больше {mx}. Нецифровой символ даёт ту же строку — поле не меняется; лишние цифры отклоняются.",
            "reject": f"Значение принимается только если оно целиком из цифр и не длиннее {mx}. Иначе setState не "
                      "вызывается, и React возвращает в поле прежнее значение состояния.",
            "upper": f"После каждого символа строка переводится в верхний регистр, из неё удаляется всё, кроме A–Z и "
                     f"0–9, и обрезается до {mx} символов.",
            "trim": "trim() вызывается при каждом вводе: введённый пробел оказывается в конце строки и сразу срезается, "
                    "поэтому слова склеиваются.",
        }[kind]
        main = f"{why} Итог: «{key}»."
        prompt = (
            f"Пользователь по одному вводит в пустое поле символы «{typed}» (каждый — в конец строки). "
            f"Что будет в поле после последнего символа? {ASSUME}"
        )
        rendered = Rendered(prompt=prompt, code=code, language="javascript", answer_mode="text",
                            answer_hint="Содержимое поля точно, с учётом регистра, без кавычек")
        return Instance(self.id, seed, rendered, key, dist, explain(main, dist, _CI_HINTS))


# =====================================================================================
# react.batched_updates — очередь обновлений: значение против функции, один рендер на клик
# =====================================================================================

_BU_NAMES = [("Counter", "count", "setCount", "c"), ("CartBadge", "items", "setItems", "n"),
             ("ScoreBoard", "score", "setScore", "s"), ("Stepper", "step", "setStep", "x"),
             ("Votes", "votes", "setVotes", "v"), ("Points", "points", "setPoints", "p")]

_BU_PATTERNS = [
    ["V", "F*", "F+"],
    ["F+", "V", "F*"],
    ["V", "F+", "V", "F*"],
    ["F*", "V", "F+"],
    ["V", "V", "F+"],
    ["F+", "F*", "V"],
]

_BU_HINTS = {
    "all_functional": "в setX(x + n) используется x из замыкания текущего рендера — он не меняется до следующего рендера.",
    "all_stale": "функция-обновление (prev) => … получает результат предыдущих обновлений в очереди, а не значение из рендера.",
    "render_per_update": "обновления внутри одного обработчика объединяются (batching): один клик — один рендер.",
    "mount_not_logged": "console.log стоит в теле компонента и срабатывает уже при первом рендере.",
}


def _apply_click(base: int, ops: list[tuple[str, int]], mode: str = "") -> tuple[int, list[int]]:
    """Очередь обновлений React: значение заменяет, функция применяется к результату предыдущих."""
    x = base
    steps = []
    for kind, n in ops:
        if kind == "V":
            x = (x + n) if mode == "all_functional" else (base + n)
        elif kind == "F+":
            x = (base + n) if mode == "all_stale" else (x + n)
        else:
            x = (base * n) if mode == "all_stale" else (x * n)
        steps.append(x)
    return x, steps


@register
class BatchedUpdates(Family):
    id = "react.batched_updates"
    title = "Очередь обновлений состояния и batching"
    specs = ("frontend_js",)
    module = "react"
    format = "predict_output"
    target_grade = "junior"
    position = 0.3
    c = 0.0
    time_limit_s = 120
    is_anchor = True
    enemies = ("react.stale_closure",)

    def normalize(self, answer):
        return norm_output(answer)

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        comp, s, setter, prev = pick(rng, _BU_NAMES)
        pattern = pick(rng, _BU_PATTERNS)
        ops = []
        for kind in pattern:
            ops.append((kind, rng.randint(2, 3) if kind == "F*" else rng.randint(1, 19)))
        init = rng.randint(0, 250)
        lines = []
        for kind, n in ops:
            if kind == "V":
                lines.append(f"    {setter}({s} + {n});")
            elif kind == "F+":
                lines.append(f"    {setter}(({prev}) => {prev} + {n});")
            else:
                lines.append(f"    {setter}(({prev}) => {prev} * {n});")
        code = "\n".join([
            f"function {comp}() {{",
            f"  const [{s}, {setter}] = useState({init});",
            f"  console.log({s});",
            "",
            "  function handleClick() {",
            *lines,
            "  }",
            "",
            f"  return <button onClick={{handleClick}}>{{{s}}}</button>;",
            "}",
        ])

        def run(mode: str = "") -> list[int]:
            log = [init]
            base = init
            for _ in range(2):
                base, steps = _apply_click(base, ops, mode)
                if mode == "render_per_update":
                    log += steps
                else:
                    log.append(base)
            return log

        key_log = run()
        key = norm_output(" ".join(map(str, key_log)))
        wrong = [(norm_output(" ".join(map(str, run(m)))), m) for m in ("all_functional", "all_stale")]
        wrong.append((norm_output(" ".join(map(str, run("render_per_update")))), "render_per_update"))
        wrong.append((norm_output(" ".join(map(str, key_log[1:]))), "mount_not_logged"))
        dist = text_distractors(key, wrong)
        b1, b2 = key_log[1], key_log[2]
        main = (
            f"Все обновления из обработчика ставятся в очередь и применяются за один рендер. "
            f"setX(значение) использует {s} из текущего рендера (замыкание), а setX(prev => …) — результат "
            f"предыдущих обновлений очереди. Первый рендер печатает {init}, первый клик → {b1}, второй "
            f"(уже с {s} = {b1}) → {b2}. Вывод: «{key}»."
        )
        prompt = (f"{ASSUME} Компонент смонтировался, затем пользователь дважды нажал кнопку (второй раз — "
                  f"после того как отрисовался результат первого). Что будет выведено в консоль?")
        rendered = Rendered(prompt=prompt, code=code, language="javascript", answer_mode="text",
                            answer_hint="Все выведенные числа по порядку через пробел")
        return Instance(self.id, seed, rendered, key, dist, explain(main, dist, _BU_HINTS))


# =====================================================================================
# react.stale_closure — подписка в useEffect([]) и устаревшее значение состояния
# =====================================================================================

_SC_NAMES = [
    {"comp": "SalesWidget", "feed": "orders", "ev": "order", "sum": "revenue", "setSum": "setRevenue",
     "cnt": "count", "setCnt": "setCount", "field": "amount", "unit": "₽"},
    {"comp": "TradeTicker", "feed": "trades", "ev": "trade", "sum": "volume", "setSum": "setVolume",
     "cnt": "deals", "setCnt": "setDeals", "field": "size", "unit": "лотов"},
    {"comp": "DonationBar", "feed": "donations", "ev": "donation", "sum": "raised", "setSum": "setRaised",
     "cnt": "donors", "setCnt": "setDonors", "field": "sum", "unit": "₽"},
    {"comp": "TrafficPanel", "feed": "visits", "ev": "visit", "sum": "seconds", "setSum": "setSeconds",
     "cnt": "sessions", "setCnt": "setSessions", "field": "duration", "unit": "с"},
]

_SC_HINTS = {
    "no_stale": "колбэк подписки создан при первом рендере (deps []), и переменная состояния в нём навсегда равна начальному значению.",
    "all_stale": "функциональное обновление (prev) => … не зависит от замыкания: оно получает актуальное значение.",
    "first_event": "колбэк вызывается для каждого события; каждый раз он считает от начального значения — побеждает последнее событие.",
}


@register
class StaleClosure(Family):
    id = "react.stale_closure"
    title = "Устаревшее замыкание в useEffect"
    specs = ("frontend_js",)
    module = "react"
    format = "predict_output"
    target_grade = "middle"
    position = 0.5
    c = 0.0
    time_limit_s = 120
    enemies = ("react.batched_updates",)

    def normalize(self, answer):
        return norm_output(answer)

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        n = pick(rng, _SC_NAMES)
        stale = pick(rng, ["sum", "cnt"])
        s0, c0 = rng.randrange(0, 5000, 10), rng.randint(0, 40)
        amounts = [rng.randrange(50, 1000, 5) for _ in range(rng.randint(3, 5))]
        ev = n["ev"]
        if stale == "sum":
            upd_sum = f"      {n['setSum']}({n['sum']} + {ev}.{n['field']});"
            upd_cnt = f"      {n['setCnt']}((prev) => prev + 1);"
        else:
            upd_sum = f"      {n['setSum']}((prev) => prev + {ev}.{n['field']});"
            upd_cnt = f"      {n['setCnt']}({n['cnt']} + 1);"
        upd = [upd_sum, upd_cnt] if rng.random() < 0.5 else [upd_cnt, upd_sum]
        code = "\n".join([
            f"function {n['comp']}({{ {n['feed']} }}) {{",
            f"  const [{n['sum']}, {n['setSum']}] = useState({s0});",
            f"  const [{n['cnt']}, {n['setCnt']}] = useState({c0});",
            "",
            "  useEffect(() => {",
            f"    const unsubscribe = {n['feed']}.subscribe(({ev}) => {{",
            *upd,
            "    });",
            "    return unsubscribe;",
            "  }, []);",
            "",
            f"  return <p>{{{n['cnt']}}} / {{{n['sum']}}}</p>;",
            "}",
        ])

        def model(mode: str = "") -> tuple[int, int]:
            # функциональное обновление видит актуальное значение; «значение из замыкания» — начальное
            fresh_sum, fresh_cnt = s0 + sum(amounts), c0 + len(amounts)
            if mode == "no_stale":
                return fresh_cnt, fresh_sum
            if mode == "all_stale":
                return c0 + 1, s0 + amounts[-1]
            last = amounts[0] if mode == "first_event" else amounts[-1]
            if stale == "sum":
                return fresh_cnt, s0 + last
            return c0 + 1, fresh_sum

        def fmt(t: tuple[int, int]) -> str:
            return f"{t[0]} {t[1]}"

        key = fmt(model())
        dist = text_distractors(key, [(fmt(model(m)), m) for m in ("no_stale", "all_stale", "first_event")])
        sv = n["sum"] if stale == "sum" else n["cnt"]
        main = (
            f"Эффект с [] выполняется один раз, и колбэк подписки замыкает {sv} из ПЕРВОГО рендера. "
            f"Поэтому каждое событие считает от начального значения, и остаётся результат последнего. "
            f"Функциональное обновление (prev) => … получает актуальное состояние и накапливает все события. "
            f"Итог: {n['cnt']} и {n['sum']} = «{key}»."
        )
        listed = ", ".join(str(a) for a in amounts)
        prompt = (
            f"{ASSUME} После монтирования {n['feed']} по очереди присылает {len(amounts)} "
            f"{plural_ru(len(amounts), ('событие', 'события', 'событий'))} с полем "
            f"{n['field']} = {listed} (каждое — после того, как отрисован результат предыдущего). Что покажет абзац?"
        )
        rendered = Rendered(prompt=prompt, code=code, language="javascript", answer_mode="text",
                            answer_hint=f"Два числа через пробел: сначала {n['cnt']}, затем {n['sum']}")
        return Instance(self.id, seed, rendered, key, dist, explain(main, dist, _SC_HINTS))
