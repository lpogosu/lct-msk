"""react: эффекты, ключи списков, memo/useCallback, производное состояние (bug_line).

Как и в react_state.py: точные модели конкретных сценариев React 18 (createRoot, production,
без StrictMode). Ключевые правила, на которых стоят модели:
- useEffect: после коммита сначала выполняются ВСЕ очистки эффектов, у которых изменились зависимости
  (в порядке объявления), затем все их тела (в порядке объявления); без массива зависимостей —
  после каждого рендера; [] — только при монтировании; сравнение зависимостей — Object.is.
- Ключи: состояние экземпляра привязано к паре (тип, key) в той же позиции дерева.
- memo: повторный рендер, только если хотя бы один проп изменился по Object.is.
"""

from __future__ import annotations

import random

from app.testing.bank.base import Family, Instance, Option, Rendered, canonical, register
from app.testing.bank.families.frontend_js._common import (
    explain,
    fill,
    make_choice,
    norm_choice,
    norm_line_choice,
    pick,
)

ASSUME = "React 18 (createRoot), production-сборка без StrictMode."


def _opt_show(ch) -> dict[str, str]:
    return {oid: next(o.text for o in ch.options if o.id == oid) for oid in ch.distractors}


# =====================================================================================
# react.effect_deps — какие эффекты перезапустятся после клика и в каком порядке
# =====================================================================================

# тип эффекта → (зависимости в коде, перезапускается ли при смене tab)
_EFFECT_KINDS = {
    "tab": "[tab]",
    "nodeps": None,  # без массива зависимостей
    "obj": "[options]",  # options — объект, создаваемый в теле компонента заново
    "empty": "[]",
    "prop": "[<<prop>>]",
}

_EFFECT_LABELS = {
    "tab": [("track", "untrack"), ("highlight", "unhighlight"), ("scroll", "unscroll")],
    "nodeps": [("title", "restore title"), ("measure", "unmeasure"), ("log view", "end view")],
    "obj": [("fetch", "abort"), ("load", "cancel"), ("query", "drop query")],
    "empty": [("connect", "disconnect"), ("subscribe", "unsubscribe"), ("open socket", "close socket")],
    "prop": [("load user", "abort user"), ("join room", "leave room"), ("watch", "unwatch")],
}

_ED_DOMAINS = [
    {"comp": "UserPage", "prop": "userId", "tabs": ["profile", "posts", "photos", "friends"]},
    {"comp": "ProjectView", "prop": "projectId", "tabs": ["board", "list", "timeline", "files"]},
    {"comp": "ChatRoom", "prop": "roomId", "tabs": ["messages", "members", "media", "pins"]},
    {"comp": "OrderCard", "prop": "orderId", "tabs": ["items", "delivery", "payment", "history"]},
]

_ED_HINTS = {
    "interleaved": "React сначала выполняет ВСЕ очистки перезапускаемых эффектов, и только потом — все их тела.",
    "no_deps_once": "эффект без массива зависимостей выполняется после КАЖДОГО рендера.",
    "object_dep_stable": "объект, созданный в теле компонента, при каждом рендере новый — Object.is считает зависимость изменившейся.",
    "deps_ignored": "эффект с [] или с неизменившейся зависимостью после клика не перезапускается.",
    "cleanup_after_body": "очистка предыдущего запуска выполняется ДО нового запуска эффекта.",
    "effects_before_render": "эффекты выполняются после рендера и коммита, а не до него.",
}


def _model_effects(effects: list[dict], flags: frozenset[str] = frozenset()) -> list[str]:
    """Журнал после клика, меняющего tab: render, затем очистки, затем тела перезапускаемых эффектов."""
    rerun = []
    for e in effects:
        k = e["kind"]
        runs = k in ("tab", "nodeps", "obj")
        if k == "nodeps" and "no_deps_once" in flags:
            runs = False
        if k == "obj" and "object_dep_stable" in flags:
            runs = False
        if "deps_ignored" in flags:
            runs = True
        if runs:
            rerun.append(e)
    cleanups = [e["cleanup"] for e in rerun if e["cleanup"]]
    bodies = [e["body"] for e in rerun]
    if "interleaved" in flags:
        effects_log = []
        for e in rerun:
            if e["cleanup"]:
                effects_log.append(e["cleanup"])
            effects_log.append(e["body"])
    elif "cleanup_after_body" in flags:
        effects_log = bodies + cleanups
    else:
        effects_log = cleanups + bodies
    if "effects_before_render" in flags:
        return effects_log + ["render"]
    return ["render"] + effects_log


@register
class EffectDeps(Family):
    id = "react.effect_deps"
    title = "useEffect: зависимости, очистка и порядок запуска"
    specs = ("frontend_js",)
    module = "react"
    format = "predict_output"
    target_grade = "middle"
    position = -0.3
    c = 0.25
    time_limit_s = 150
    enemies = ("react.derived_state",)

    def normalize(self, answer):
        return norm_choice(answer)

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        dom = pick(rng, _ED_DOMAINS)
        t0, t1 = rng.sample(dom["tabs"], 2)
        # состав постоянной сложности: всегда эффект на [tab], один «перезапускается неожиданно»,
        # один «не перезапускается», иногда четвёртый
        for _ in range(100):
            kinds = ["tab", pick(rng, ["nodeps", "obj"]), pick(rng, ["empty", "prop"])]
            rest = [k for k in _EFFECT_KINDS if k not in kinds]
            if rng.random() < 0.5:
                kinds.append(pick(rng, rest))
            rng.shuffle(kinds)
            effects = []
            for k in kinds:
                body, cleanup = pick(rng, _EFFECT_LABELS[k])
                has_cleanup = k != "nodeps" or rng.random() < 0.5
                effects.append({"kind": k, "body": body, "cleanup": cleanup if has_cleanup else None})
            # ловушка «все очистки раньше всех тел» должна быть в каждом клоне: если журнал «вперемешку»
            # совпадает с верным (одна очистка у первого эффекта), клон проверял бы меньше
            if _model_effects(effects) != _model_effects(effects, frozenset({"interleaved"})):
                break
        else:  # pragma: no cover
            raise ValueError("не удалось подобрать набор эффектов")
        prop = dom["prop"]
        lines = [f"function {dom['comp']}({{ {prop} }}) {{", f'  const [tab, setTab] = useState("{t0}");']
        if "obj" in kinds:
            lines.append(f"  const options = {{ {prop}, limit: 20 }};")
        lines.append('  console.log("render");')
        for e in effects:
            lines.append("")
            lines.append("  useEffect(() => {")
            lines.append(f'    console.log("{e["body"]}");')
            if e["cleanup"]:
                lines.append(f'    return () => console.log("{e["cleanup"]}");')
            deps = _EFFECT_KINDS[e["kind"]]
            lines.append("  });" if deps is None else f"  }}, {fill(deps, prop=prop)});")
        lines += ["", f'  return <button onClick={{() => setTab("{t1}")}}>{t1}</button>;', "}"]
        code = "\n".join(lines)

        def seq(flags: frozenset[str] = frozenset()) -> str:
            return " → ".join(_model_effects(effects, flags))

        correct = seq()
        tags = ["interleaved", "no_deps_once", "object_dep_stable", "deps_ignored", "cleanup_after_body",
                "effects_before_render"]
        wrong = [(seq(frozenset({t})), t) for t in tags]
        pair = [(seq(frozenset({"interleaved", t})), t) for t in ("no_deps_once", "object_dep_stable",
                                                                  "deps_ignored")]
        ch = make_choice(rng, correct, wrong + pair, k=4)
        reason = {
            "tab": "зависимость tab изменилась",
            "nodeps": "у эффекта нет массива зависимостей — он запускается после каждого рендера",
            "obj": "options — новый объект при каждом рендере, Object.is видит изменение",
            "empty": "[] — только при монтировании",
            "prop": f"{prop} не менялся",
        }
        runs = [f"«{e['body']}» — {reason[e['kind']]}" for e in effects if e["kind"] in ("tab", "nodeps", "obj")]
        skips = [f"«{e['body']}» — {reason[e['kind']]}" for e in effects if e["kind"] in ("empty", "prop")]
        why = (
            f"Клик меняет tab → рендер (render). Перезапускаются: {'; '.join(runs)}. "
            f"Не перезапускаются: {'; '.join(skips)}. После коммита React сначала вызывает очистки "
            f"предыдущих запусков (в порядке объявления), затем все новые тела. Порядок: {correct}."
        )
        prompt = (f"{ASSUME} Компонент смонтирован с tab = \"{t0}\". Пользователь нажимает кнопку. "
                  f"Какие сообщения и в каком порядке появятся в консоли ПОСЛЕ нажатия?")
        rendered = Rendered(prompt=prompt, code=code, language="javascript", options=ch.options,
                            answer_mode="choice")
        return Instance(self.id, seed, rendered, ch.key, ch.distractors,
                        explain(why, ch.distractors, _ED_HINTS, _opt_show(ch)))


# =====================================================================================
# react.list_keys — состояние строк списка при key={index} и key={item.id}
# =====================================================================================

_LK_TITLES = ["Купить молоко", "Позвонить в банк", "Оплатить интернет", "Забрать посылку", "Записаться к врачу",
              "Продлить полис", "Сдать отчёт", "Полить цветы", "Заказать такси", "Обновить резюме",
              "Починить кран", "Купить билеты"]
_LK_NAMES = [("TodoList", "todos", "setTodos", "TodoItem"), ("Checklist", "tasks", "setTasks", "TaskRow"),
             ("ShoppingList", "items", "setItems", "ItemRow"), ("Planner", "plans", "setPlans", "PlanRow")]
NONE_CHECKED = "Ни одна строка не отмечена"

_LK_HINTS = {
    "follows_item": "с key={index} React сопоставляет строки по позиции: состояние остаётся на месте, а данные «переезжают».",
    "stays_at_position": "с key={item.id} состояние привязано к элементу и переезжает вместе с ним.",
    "reset": "строки с тем же key не пересоздаются: их состояние сохраняется.",
    "other": "проверьте, какой key был у отмеченной строки до и после изменения.",
}


def _model_keys(titles: list[str], ids: list[int], checked: int, op: str, new: tuple[int, str],
                key_kind: str) -> list[str]:
    """Какие заголовки отмечены после операции. Состояние строки живёт по ключу."""
    def key_of(i: int, item_id: int):
        return i if key_kind == "index" else item_id

    state = {key_of(checked, ids[checked]): True}
    items = list(zip(ids, titles, strict=True))
    if op == "prepend":
        items = [new, *items]
    elif op == "remove_first":
        items = items[1:]
    elif op == "reverse":
        items = items[::-1]
    else:  # remove_checked
        items = [it for it in items if it[0] != ids[checked]]
    return [t for i, (iid, t) in enumerate(items) if state.get(key_of(i, iid))]


@register
class ListKeys(Family):
    id = "react.list_keys"
    title = "Ключи в списках: чьё состояние у строки"
    specs = ("frontend_js",)
    module = "react"
    format = "predict_output"
    target_grade = "middle"
    position = 0.0
    c = 0.25
    time_limit_s = 120
    is_anchor = True

    def normalize(self, answer):
        return norm_choice(answer)

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        lst, arr, setter, row = pick(rng, _LK_NAMES)
        n = rng.randint(4, 5)
        pool = rng.sample(_LK_TITLES, n + 1)
        titles, new_title = pool[:n], pool[n]
        ids = sorted(rng.sample(range(10, 99), n + 1))
        new_id, ids = ids[-1], ids[:n]
        rng.shuffle(ids)
        op = pick(rng, ["prepend", "remove_first", "reverse", "remove_checked"])
        checked = rng.randint(0, n - 2) if op != "remove_first" else rng.randint(1, n - 2)
        while op == "reverse" and 2 * checked == n - 1:
            # средняя строка при перевороте остаётся на месте — key={index} и key={id} дали бы один ответ,
            # и клон проверял бы меньше, чем остальные (постоянная сложность)
            checked = rng.randint(0, n - 2)
        key_kind = "index" if rng.random() < 0.6 else "id"
        op_code = {
            "prepend": f'{setter}([{{ id: {new_id}, title: "{new_title}" }}, ...{arr}])',
            "remove_first": f"{setter}({arr}.slice(1))",
            "reverse": f"{setter}([...{arr}].reverse())",
            "remove_checked": f"{setter}({arr}.filter((item) => item.id !== {ids[checked]}))",
        }[op]
        op_text = {
            "prepend": f"«Добавить» (новая строка «{new_title}» встаёт в начало)",
            "remove_first": "«Удалить первую»",
            "reverse": "«Перевернуть список»",
            "remove_checked": f"«Удалить» у строки «{titles[checked]}»",
        }[op]
        items_js = ",\n".join(f'    {{ id: {i}, title: "{t}" }}' for i, t in zip(ids, titles, strict=True))
        key_expr = "index" if key_kind == "index" else "item.id"
        code = "\n".join([
            f"function {lst}() {{",
            f"  const [{arr}, {setter}] = useState([",
            items_js + ",",
            "  ]);",
            "",
            "  return (",
            "    <>",
            f"      <button onClick={{() => {op_code}}}>Изменить</button>",
            "      <ul>",
            f"        {{{arr}.map((item, index) => (",
            f"          <{row} key={{{key_expr}}} title={{item.title}} />",
            "        ))}",
            "      </ul>",
            "    </>",
            "  );",
            "}",
            "",
            f"function {row}({{ title }}) {{",
            "  const [checked, setChecked] = useState(false);",
            "  return (",
            "    <li>",
            "      <input type=\"checkbox\" checked={checked} onChange={() => setChecked(!checked)} />",
            "      {title}",
            "    </li>",
            "  );",
            "}",
        ])
        new = (new_id, new_title)

        def ans(kind: str) -> str:
            res = _model_keys(titles, ids, checked, op, new, kind)
            return ", ".join(res) if res else NONE_CHECKED

        correct = ans(key_kind)
        other_kind = "id" if key_kind == "index" else "index"
        wrong = [(ans(other_kind), "follows_item" if key_kind == "index" else "stays_at_position"),
                 (NONE_CHECKED, "reset")]
        # «прочие» варианты — только строки, которые есть в коде: заголовок, которого в списке нет,
        # отбрасывался бы без понимания ключей
        visible = titles + [new_title] if op == "prepend" else list(titles)
        wrong += [(t, "other") for t in rng.sample(visible, len(visible))]
        ch = make_choice(rng, correct, wrong, k=4)
        if key_kind == "index":
            why = (f"key={{index}}: React сопоставляет строки по позиции. Состояние checked было у строки с "
                   f"индексом {checked}; после изменения на этой позиции (если она есть) оказывается другой "
                   f"элемент — отмеченным покажется он. Ответ: {correct}.")
        else:
            why = (f"key={{item.id}}: состояние привязано к элементу с id {ids[checked]} и следует за ним "
                   f"(а при его удалении удаляется вместе с ним). Ответ: {correct}.")
        prompt = (f"{ASSUME} Пользователь отметил чекбокс у строки «{titles[checked]}», затем нажал кнопку, "
                  f"которая делает {op_text}. Какая строка будет отмечена?")
        rendered = Rendered(prompt=prompt, code=code, language="javascript", options=ch.options,
                            answer_mode="choice")
        return Instance(self.id, seed, rendered, ch.key, ch.distractors,
                        explain(why, ch.distractors, _LK_HINTS, _opt_show(ch)))


# =====================================================================================
# react.memo_callback — сколько раз отрендерится строка, обёрнутая в memo
# =====================================================================================

_MC_CB = {
    "cb_empty": ("useCallback((id) => setSelectedId(id), [])", ()),
    "cb_query": ("useCallback((id) => { track(query); setSelectedId(id); }, [query])", ("query",)),
    "cb_selected": ("useCallback((id) => setSelectedId(id === selectedId ? null : id), [selectedId])",
                    ("selectedId",)),
}
_MC_STYLE = {
    "st_const": ("ROW_STYLE", ()),
    "st_memo_empty": ("useMemo(() => ({ padding: 8 }), [])", ()),
    "st_memo_query": ("useMemo(() => ({ padding: 8, opacity: query ? 0.6 : 1 }), [query])", ("query",)),
    "st_memo_selected": ("useMemo(() => ({ padding: selectedId ? 4 : 8 }), [selectedId])", ("selectedId",)),
}

_MC_HINTS = {
    "memo_ignored": "memo пропускает рендер, если все пропсы равны по Object.is — повторный рендер родителя не всегда перерисовывает строку.",
    "memo_blocks_all": "memo не «замораживает» компонент: изменившийся проп вызывает повторный рендер.",
    "deps_ignored": "useCallback/useMemo возвращают новое значение, когда меняется что-то из массива зависимостей.",
    "functions_ignored": "функции сравниваются по ссылке так же, как остальные пропсы.",
    "active_ignored": "проп active меняется, когда выделение переходит на эту строку или уходит с неё.",
    "mount_excluded": "первый рендер строки тоже печатает сообщение.",
    "other": "пройдите по каждому рендеру List и сравните пропсы строки с предыдущими.",
}


def _memo_trace(cb: str, st: str, events: list[tuple[str, object]], tracked: int) -> list[str]:
    """Для разбора: какие пропсы строки tracked меняются на каждом рендере List."""
    state: dict[str, object] = {"query": "", "selectedId": None}

    def props() -> dict[str, object]:
        return {
            "onSelect": tuple(state[d] for d in _MC_CB[cb][1]),
            "style": tuple(state[d] for d in _MC_STYLE[st][1]),
            "active": state["selectedId"] == tracked,
        }

    out = []
    prev = props()
    for field, value in events:
        state[field] = value
        cur = props()
        changed = [k for k in cur if cur[k] != prev[k]]
        what = f"query = «{value}»" if field == "query" else f"выбрана строка {value}"
        out.append(f"{what}: " + (f"меняется {', '.join(changed)} → рендер" if changed else "пропсы те же → пропуск"))
        prev = cur
    return out


def _model_memo(cb: str, st: str, events: list[tuple[str, object]], tracked: int,
                flags: frozenset[str] = frozenset()) -> int:
    """Рендеры memo-строки tracked: 1 (монтирование) + рендеры List, где изменился хоть один проп."""
    state = {"query": "", "selectedId": None}

    def props(render_no: int) -> tuple:
        cb_deps = _MC_CB[cb][1]
        st_deps = _MC_STYLE[st][1]
        if "deps_ignored" in flags:
            cb_deps, st_deps = (), ()
        cb_tok = ("cb",) + tuple(state[d] for d in cb_deps)
        st_tok = ("st",) + tuple(state[d] for d in st_deps)
        if "functions_ignored" in flags:
            cb_tok = ("cb",)
        active = False if "active_ignored" in flags else (state["selectedId"] == tracked)
        return cb_tok, st_tok, active

    renders = 0 if "mount_excluded" in flags else 1
    prev = props(0)
    for i, (field, value) in enumerate(events, start=1):
        state[field] = value
        cur = props(i)
        if "memo_ignored" in flags or cur != prev:
            renders += 1
        prev = cur
    if "memo_blocks_all" in flags:
        return 1
    return renders


@register
class MemoCallback(Family):
    id = "react.memo_callback"
    title = "memo, useCallback, useMemo: когда строка перерисуется"
    specs = ("frontend_js",)
    module = "react"
    format = "predict_output"
    target_grade = "senior"
    position = -0.3
    c = 0.25
    time_limit_s = 180
    is_anchor = True
    enemies = ("react.render_count",)

    def normalize(self, answer):
        return norm_choice(answer)

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        for _ in range(200):
            cb = pick(rng, list(_MC_CB))
            st = pick(rng, list(_MC_STYLE))
            ids = sorted(rng.sample(range(1, 30), 4))
            tracked = pick(rng, ids)
            typed = "".join(pick(rng, "абвгдежзиклмнопрст") for _ in range(rng.randint(2, 4)))
            clicks = []
            cur = None
            for _ in range(rng.randint(2, 3)):
                nxt = pick(rng, [i for i in ids if i != cur])
                clicks.append(nxt)
                cur = nxt
            events: list[tuple[str, object]] = [("query", typed[: j + 1]) for j in range(len(typed))]
            sel = None
            for cid in clicks:
                # обработчик cb_selected снимает выделение при повторном клике — повторов подряд нет
                sel = cid
                events.append(("selectedId", sel))
            correct = _model_memo(cb, st, events, tracked)
            total = 1 + len(events)
            if 1 < correct < total:  # постоянная сложность: ответ — не «все» и не «ни одного»
                break
        else:  # pragma: no cover
            raise ValueError("не удалось подобрать сценарий")
        style_decl = ["const ROW_STYLE = { padding: 8 };", ""] if st == "st_const" else []
        code = "\n".join([
            *style_decl,
            "const Row = memo(function Row({ item, onSelect, style, active }) {",
            '  console.log("render", item.id);',
            "  return (",
            "    <li style={style} className={active ? \"active\" : \"\"} onClick={() => onSelect(item.id)}>",
            "      {item.title}",
            "    </li>",
            "  );",
            "});",
            "",
            "function List({ items, track }) {",
            '  const [query, setQuery] = useState("");',
            "  const [selectedId, setSelectedId] = useState(null);",
            f"  const handleSelect = {_MC_CB[cb][0]};",
            f"  const rowStyle = {_MC_STYLE[st][0]};",
            "",
            "  return (",
            "    <>",
            "      <input value={query} onChange={(e) => setQuery(e.target.value)} />",
            "      <ul>",
            "        {items.map((item) => (",
            "          <Row",
            "            key={item.id}",
            "            item={item}",
            "            onSelect={handleSelect}",
            "            style={rowStyle}",
            "            active={item.id === selectedId}",
            "          />",
            "        ))}",
            "      </ul>",
            "    </>",
            "  );",
            "}",
        ])
        wrong = []
        for t in ("memo_ignored", "deps_ignored", "functions_ignored", "active_ignored", "memo_blocks_all",
                  "mount_excluded"):
            wrong.append((str(_model_memo(cb, st, events, tracked, frozenset({t}))), t))
        wrong += [(str(correct + d), "other") for d in (1, 2, -1)]
        ch = make_choice(rng, str(correct), wrong, k=4)
        clicked = ", ".join(map(str, clicks))
        trace = "; ".join(_memo_trace(cb, st, events, tracked))
        why = (
            f"memo перерисует строку {tracked} только если какой-то проп изменился по Object.is. item — та же "
            f"ссылка; onSelect и style меняются лишь при смене зависимостей useCallback/useMemo (константа вне "
            f"компонента — никогда); active — когда выделение приходит на строку или уходит с неё. "
            f"По шагам: {trace}. Итого 1 (монтирование) + {correct - 1} = {correct}."
        )
        prompt = (
            f"{ASSUME} items — массив из 4 объектов с id {', '.join(map(str, ids))}, ссылка на него не меняется. "
            f"Пользователь по одному вводит в поле поиска символы «{typed}», затем кликает по "
            f"строкам с id {clicked} (по порядку). Сколько раз всего в консоли появится «render {tracked}», "
            f"считая первый рендер?"
        )
        rendered = Rendered(prompt=prompt, code=code, language="javascript", options=ch.options,
                            answer_mode="choice")
        return Instance(self.id, seed, rendered, ch.key, ch.distractors,
                        explain(why, ch.distractors, _MC_HINTS, _opt_show(ch)))


# =====================================================================================
# react.derived_state — bug_line: состояние, скопированное из пропсов, «застывает»
# =====================================================================================

_DS_HINTS = {
    "lazy_init": "ленивая инициализация useState(() => …) тоже выполняется только при монтировании.",
    "ref_snapshot": "useRef(…).current хранит значение первого рендера и при смене пропсов не обновляется.",
    "key_on_child": "key на внутреннем элементе пересоздаёт только его; состояние самого компонента остаётся.",
    "new_object_prop": "новый объект в пропсе не сбрасывает состояние ребёнка: тип и позиция те же, экземпляр тот же.",
    "derive_breaks_editing": "если вычислять значение из пропса, поле перестанет редактироваться: сеттера состояния больше нет.",
}

_DS_PRICE = [
    {"comp": "PriceTag", "a": "price", "b": "currency", "label": "label", "set": "setLabel", "fn": "formatMoney",
     "what": "цену товара"},
    {"comp": "DistanceBadge", "a": "meters", "b": "units", "label": "text", "set": "setText", "fn": "formatDistance",
     "what": "расстояние до точки"},
    {"comp": "DueDate", "a": "date", "b": "locale", "label": "caption", "set": "setCaption", "fn": "formatDate",
     "what": "срок задачи"},
    {"comp": "Temperature", "a": "celsius", "b": "scale", "label": "shown", "set": "setShown", "fn": "formatTemp",
     "what": "температуру"},
]

_DS_FORM = [
    {"page": "UserEditor", "form": "UserForm", "prop": "user", "field": "name", "set": "setName",
     "what": "пользователя"},
    {"page": "ProductEditor", "form": "ProductForm", "prop": "product", "field": "title", "set": "setTitle",
     "what": "товара"},
    {"page": "TaskEditor", "form": "TaskForm", "prop": "task", "field": "summary", "set": "setSummary",
     "what": "задачи"},
    {"page": "ContactEditor", "form": "ContactForm", "prop": "contact", "field": "email", "set": "setEmail",
     "what": "контакта"},
]


@register
class DerivedState(Family):
    id = "react.derived_state"
    title = "Производное состояние: найти строку и исправление"
    specs = ("frontend_js",)
    module = "react"
    format = "bug_line"
    target_grade = "senior"
    position = 0.0
    c = 0.25
    time_limit_s = 150
    enemies = ("react.effect_deps",)

    def normalize(self, answer):
        return norm_line_choice(answer)

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        if rng.random() < 0.5:
            n = pick(rng, _DS_PRICE)
            hover = rng.random() < 0.5
            lines = [f"function {n['comp']}({{ {n['a']}, {n['b']} }}) {{",
                     f"  const [{n['label']}, {n['set']}] = useState({n['fn']}({n['a']}, {n['b']}));"]
            bug = 2
            if hover:
                lines.append("  const [hovered, setHovered] = useState(false);")
                cls = '{hovered ? "tag tag-hover" : "tag"} onMouseEnter={() => setHovered(true)}'
            else:
                cls = '"tag"'
            ret_line = len(lines) + 1
            lines += [f"  return <span className={cls}>{{{n['label']}}}</span>;", "}"]
            call = f"{n['fn']}({n['a']}, {n['b']})"
            fixes = [
                (bug, f"  const {n['label']} = {call};", None),
                (bug, f"  const [{n['label']}, {n['set']}] = useState(() => {call});", "lazy_init"),
                (bug, f"  const {n['label']} = useRef({call}).current;", "ref_snapshot"),
                (ret_line, f"  return <span key={{{n['a']}}} className={cls}>{{{n['label']}}}</span>;",
                 "key_on_child"),
            ]
            prompt = (f"{n['comp']} показывает {n['what']}. Когда родитель передаёт новые {n['a']} или {n['b']}, "
                      "на экране остаётся старое значение. Найдите строку с ошибкой и выберите исправление.")
            why = (f"useState(начальное значение) использует аргумент только при монтировании — дальше "
                   f"{n['label']} не зависит от пропсов. Значение, которое целиком вычисляется из пропсов, не "
                   f"нужно хранить в состоянии: достаточно вычислить его при рендере.")
        else:
            n = pick(rng, _DS_FORM)
            comment = rng.random() < 0.5
            lines = [f"function {n['page']}({{ {n['prop']} }}) {{"]
            if comment:
                lines.append(f"  // {n['prop']} меняется, когда в списке слева выбирают другую запись")
            bug = len(lines) + 1
            lines += [f"  return <{n['form']} {n['prop']}={{{n['prop']}}} />;", "}", ""]
            lines.append(f"function {n['form']}({{ {n['prop']} }}) {{")
            touched = rng.random() < 0.5
            if touched:
                lines.append("  const [touched, setTouched] = useState(false);")
            state_line = len(lines) + 1
            lines.append(f"  const [{n['field']}, {n['set']}] = useState({n['prop']}.{n['field']});")
            on_change = (f"(e) => {{ {n['set']}(e.target.value); setTouched(true); }}" if touched
                         else f"(e) => {n['set']}(e.target.value)")
            lines += [f"  return <input value={{{n['field']}}} onChange={{{on_change}}} />;", "}"]
            fixes = [
                (bug, f"  return <{n['form']} key={{{n['prop']}.id}} {n['prop']}={{{n['prop']}}} />;", None),
                (bug, f"  return <{n['form']} {n['prop']}={{{{ ...{n['prop']} }}}} />;", "new_object_prop"),
                (state_line, f"  const [{n['field']}, {n['set']}] = useState(() => {n['prop']}.{n['field']});",
                 "lazy_init"),
                (state_line, f"  const {n['field']} = {n['prop']}.{n['field']};", "derive_breaks_editing"),
            ]
            prompt = (f"{n['page']} показывает форму редактирования {n['what']}. После выбора другой записи в поле "
                      f"остаётся значение предыдущей. Найдите строку с ошибкой и выберите исправление.")
            why = (f"{n['form']} остаётся тем же экземпляром (тот же тип в той же позиции), поэтому его состояние "
                   f"{n['field']} сохраняется, а useState игнорирует новый начальный аргумент. key={{{n['prop']}.id}} "
                   f"говорит React, что для другой записи нужен новый экземпляр формы — состояние создаётся заново.")
        rng.shuffle(fixes)
        ids = "abcd"
        options = tuple(Option(ids[i], text.strip()) for i, (_, text, _) in enumerate(fixes))
        key = None
        dist: dict[str, str] = {}
        show: dict[str, str] = {}
        for i, (ln, text, tag) in enumerate(fixes):
            if tag is None:
                key = {"line": ln, "fix": ids[i]}
            else:
                k = canonical({"line": ln, "fix": ids[i]})
                dist[k] = tag
                show[k] = f"строка {ln}, исправление «{text.strip()}»"
        if key is None:  # pragma: no cover
            raise ValueError("нет верного исправления")
        fix_text = next(o.text for o in options if o.id == key["fix"])
        main = f"{why}\n\nОшибка в строке {key['line']}. Исправление: «{fix_text}»."
        rendered = Rendered(
            prompt=f"{prompt} {ASSUME}",
            code="\n".join(lines),
            language="javascript",
            lines=tuple(lines),
            options=options,
            answer_mode="line_choice",
            answer_hint="Выберите строку с ошибкой и вариант исправления для неё",
        )
        return Instance(self.id, seed, rendered, key, dist, explain(main, dist, _DS_HINTS, show))

