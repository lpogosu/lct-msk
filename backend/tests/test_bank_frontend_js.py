"""Тесты пакета семейств frontend_js (js_core, react, html_css).

1. Для каждого семейства: ключ проходит check(), неверный ответ и каждый дистрактор — нет (с тегом).
2. Контрольные примеры решателей: event loop в Node, контролируемый ввод, очередь обновлений React,
   сужение типов TS, flexbox, контексты наложения, специфичность, доступное имя, ключи списков, memo.
3. Метаданные банка: состав модулей, грейдов, якорей, доля mcq_concept, каталог.
"""

from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path

import pytest

from app.testing.bank.base import Instance, canonical
from app.testing.bank.families.frontend_js import (
    _common,
    css_cascade,
    css_layout,
    html_a11y,
    js_async,
    react_effects,
    react_state,
    ts_types,
)
from app.testing.bank.registry import all_families

PKG = "app.testing.bank.families.frontend_js"
FAMS = sorted((fid, f) for fid, f in all_families().items() if type(f).__module__.startswith(PKG))
SEEDS = (0, 1, 2, 3, 57, 199, 4242)


def _wrong_answer(inst: Instance):
    mode = inst.rendered.answer_mode
    if mode == "choice":
        return next(o.id for o in inst.rendered.options if o.id != inst.key)
    if mode == "line_choice":
        other = next(o.id for o in inst.rendered.options if o.id != inst.key["fix"])
        return {"line": inst.key["line"], "fix": other}
    if mode == "order":
        return list(reversed(inst.key))
    return "заведомо неверный ответ 0"


def test_package_has_expected_families():
    ids = [fid for fid, _ in FAMS]
    assert len(ids) == 25
    assert all(fid.startswith(("js.", "react.", "css.")) for fid in ids)
    by_module: dict[str, int] = {}
    for _, f in FAMS:
        by_module[f.module] = by_module.get(f.module, 0) + 1
        assert f.specs == ("frontend_js",)
    assert by_module == {"js_core": 12, "react": 8, "html_css": 5}


def test_grade_mix_anchors_and_mcq_share():
    grades = [f.target_grade for _, f in FAMS]
    share = {g: grades.count(g) / len(grades) for g in ("intern", "junior", "middle", "senior")}
    # ≈ 15 / 30 / 30 / 25 % (spec-testing §3.8)
    assert 0.10 <= share["intern"] <= 0.20
    assert 0.25 <= share["junior"] <= 0.35
    assert 0.25 <= share["middle"] <= 0.35
    assert 0.20 <= share["senior"] <= 0.30
    mcq = sum(1 for _, f in FAMS if f.format == "mcq_concept")
    assert mcq / len(FAMS) <= 0.30
    for module in ("js_core", "react", "html_css"):
        anchors = {f.target_grade for _, f in FAMS if f.module == module and f.is_anchor}
        assert len(anchors) >= 3, module  # якорь почти у каждого порога модуля
    for _, f in FAMS:
        assert 60 <= f.time_limit_s <= 180
        assert -1.0 <= f.position <= 1.0
        if f.format in ("mcq_concept",) or f.c:
            assert f.c == pytest.approx(0.25)


def test_enemies_are_symmetric_and_known():
    ids = {fid for fid, _ in FAMS}
    for fid, f in FAMS:
        for e in f.enemies:
            assert e in ids
            assert fid in dict(FAMS)[e].enemies


@pytest.mark.parametrize("fid,fam", FAMS, ids=[f for f, _ in FAMS])
def test_key_passes_wrong_fails(fid, fam):
    for seed in SEEDS:
        inst = fam.generate(seed)
        ok, tag = fam.check(inst, inst.key)
        assert ok and tag is None, (fid, seed)
        ok, _ = fam.check(inst, _wrong_answer(inst))
        assert not ok, (fid, seed)
        for wrong, tag in inst.distractors.items():
            answer = json.loads(wrong) if inst.rendered.answer_mode == "line_choice" else wrong
            ok, got_tag = fam.check(inst, answer)
            assert not ok and got_tag == tag, (fid, seed, wrong)
        assert re.search(r"[А-Яа-яЁё]", inst.explanation)
        assert inst.rendered.prompt and inst.rendered.code
        if inst.rendered.answer_mode == "line_choice":
            assert 1 <= inst.key["line"] <= len(inst.rendered.lines)
            assert len(inst.rendered.options) == 4
        if inst.rendered.answer_mode == "choice":
            assert len(inst.rendered.options) == 4
            assert len({o.text for o in inst.rendered.options}) == 4


@pytest.mark.parametrize("fid,fam", FAMS, ids=[f for f, _ in FAMS])
def test_text_answers_are_normalized_robustly(fid, fam):
    inst = fam.generate(11)
    if inst.rendered.answer_mode != "text":
        pytest.skip("не текстовый ответ")
    key = inst.key
    assert fam.normalize(key) == key  # идемпотентность нормализации ключа
    assert fam.check(inst, f"  {key}  ")[0]
    if fid not in ("react.controlled_input", "js.event_loop_order"):
        assert fam.check(inst, key.replace(" ", ", "))[0]


def test_generation_is_fast_and_deterministic():
    fam = all_families()["js.event_loop_order"]
    _common._JS_CACHE.clear()
    t0 = time.perf_counter()
    first = [fam.generate(s) for s in range(1000, 1100)]
    per_instance = (time.perf_counter() - t0) / 100
    assert per_instance < 0.05
    again = [fam.generate(s) for s in range(1000, 1100)]
    assert [canonical(i.key) for i in first] == [canonical(i.key) for i in again]


# ----------------------------------------------------------------------------- контрольные примеры


def test_event_loop_reference_program_in_node():
    prog = (
        'console.log("A"); setTimeout(() => console.log("B"), 0);'
        'Promise.resolve().then(() => console.log("C")).then(() => console.log("D"));'
        'queueMicrotask(() => console.log("E"));'
        '(async () => { console.log("F"); await null; console.log("G"); })();'
        'new Promise((r) => { console.log("H"); r(); }).then(() => console.log("I"));'
    )
    out = _common.run_js_many([prog, 'console.log(1, "x", -0, undefined, null, true)'])
    assert out[0].split("\n") == ["A", "F", "H", "C", "E", "G", "I", "D", "B"]
    assert out[1] == "1 x -0 undefined null true"


def test_event_loop_label_normalization_accepts_cyrillic_lookalikes():
    fam = all_families()["js.event_loop_order"]
    assert fam.normalize("А, В → С е") == "ABCE"  # кириллические А В С Е
    assert fam.normalize("b a c") == "BAC"


def test_controlled_input_model():
    m = react_state._model_input
    assert m("trim", "ab cd", 0) == "abcd"
    assert m("trim", "ab cd", 0, frozenset({"trim_final_only"})) == "ab cd"
    assert m("digits", "8 (912) 34", 4) == "8912"
    assert m("reject", "12a345", 4) == "1234"
    assert m("upper", "ab-c 1d", 4) == "ABC1"
    assert m("upper", "ab-c 1d", 4, frozenset({"filter_before_upper"})) == "1"


def test_react_update_queue_model():
    ops = [("F*", 2), ("V", 5), ("F+", 9)]
    assert react_state._apply_click(15, ops)[0] == 29  # setX(x*2); setX(15+5); setX(x+9)
    assert react_state._apply_click(15, ops, "all_functional")[0] == 44
    assert react_state._apply_click(15, [("V", 1), ("V", 1), ("V", 1)])[0] == 16  # классика count+1 ×3


def test_render_count_model():
    m = react_state._model_render_count
    assert m("sidebar", 3, 2) == 4
    assert m("counter", 3, 2) == 6
    assert m("sidebar", 3, 2, frozenset({"no_props_no_rerender"})) == 1


def test_effect_order_model():
    effects = [{"kind": "tab", "body": "track", "cleanup": "untrack"},
               {"kind": "empty", "body": "connect", "cleanup": "disconnect"},
               {"kind": "nodeps", "body": "title", "cleanup": "restore"}]
    assert react_effects._model_effects(effects) == ["render", "untrack", "restore", "track", "title"]
    assert react_effects._model_effects(effects, frozenset({"interleaved"})) == [
        "render", "untrack", "track", "restore", "title"]


def test_list_keys_model():
    titles, ids = ["A", "B", "C", "D"], [11, 12, 13, 14]
    m = react_effects._model_keys
    assert m(titles, ids, 1, "prepend", (99, "N"), "index") == ["A"]
    assert m(titles, ids, 1, "prepend", (99, "N"), "id") == ["B"]
    assert m(titles, ids, 1, "remove_checked", (99, "N"), "index") == ["C"]
    assert m(titles, ids, 1, "remove_checked", (99, "N"), "id") == []
    assert m(titles, ids, 0, "reverse", (99, "N"), "index") == ["D"]


def test_memo_model():
    events = [("query", "а"), ("query", "аб"), ("selectedId", 3), ("selectedId", 5)]
    # стабильный колбэк, стиль-константа: строка 5 перерисуется только когда active станет true
    assert react_effects._model_memo("cb_empty", "st_const", events, 5) == 2
    # колбэк с [query]: +2 рендера на ввод, +1 за active
    assert react_effects._model_memo("cb_query", "st_const", events, 5) == 4
    assert react_effects._model_memo("cb_query", "st_const", events, 5, frozenset({"memo_ignored"})) == 5


def test_ts_narrowing_solver():
    n, show = ts_types.narrow, ts_types.show
    T = ts_types
    assert show(n([T.STRING, T.STR_ARR, T.NULL], ("typeof", "object"), True)) == "string[] | null"
    assert show(n([T.STRING, T.STR_ARR, T.NULL], ("typeof", "object"), True, {"typeof_null"})) == "string[]"
    assert show(n([T.STRING, T.NUMBER, T.NULL, T.UNDEF], ("truthy",), True)) == "string | number"
    assert show(n([T.NUMBER, T.NULL, T.UNDEF], ("eq_null_loose",), False)) == "number"
    assert show(n([T.NUMBER, T.NULL, T.UNDEF], ("eq_null_strict",), False)) == "number | undefined"
    assert show([T.UNDEF, T.STRING, T.NULL]) == "string | null | undefined"


def test_box_model_control_example():
    fam = all_families()["css.box_model"]
    for seed in range(40):
        inst = fam.generate(seed)
        code = inst.rendered.code
        width = int(re.search(r"width: (\d+)px", code).group(1))
        pad_tokens = re.search(r"padding: ([^;]+);", code).group(1).split()
        left, right = css_layout._box_lr([int(t.replace("px", "")) for t in pad_tokens])
        bw = int(re.search(r"border: (\d+)px", code).group(1))
        content = width - left - right - 2 * bw if "border-box" in code else width
        assert inst.key == f"{content} {content + left + right + 2 * bw}"


def test_flex_solver_control_example():
    items = [css_layout._FlexItem("brand", "flexn", 1, 0, width=110),
             css_layout._FlexItem("nav", "auto", 1, 210, width=210),
             css_layout._FlexItem("user", "width_grow", 2, 60, width=60)]
    assert css_layout._flex_solve(items, 478, 12) == [46, 256, 152]
    assert css_layout._flex_solve(items, 478, 12, "gap_ignored") == [52, 262, 164]


def test_stacking_solver_control_example():
    B = css_layout._Box
    badge = B("badge", positioned=True, pos_kind="fixed", z=19, order=0)
    toast = B("toast", effect="opacity", order=1)
    modal = B("modal", positioned=True, pos_kind="absolute", z=62, order=2)
    menu = B("menu", positioned=True, order=3)
    dropdown = B("dropdown", positioned=True, pos_kind="absolute", z=46, order=4)
    paths = {"badge": [badge], "modal": [toast, modal], "dropdown": [menu, dropdown], "toast": [toast]}
    key = css_layout._paint_key
    best = max(paths, key=lambda n: key(paths[n], frozenset()))
    assert best == "dropdown"  # .menu без z-index контекст не создаёт, 46 > 19; .modal заперт в .toast
    assert key(paths["modal"], frozenset()) > key(paths["toast"], frozenset())  # потомок над корнем контекста


def test_specificity_solver_control_example():
    C, S, R = css_cascade.Compound, css_cascade.Selector, css_cascade.Rule
    path = [css_cascade.Node("div", classes=frozenset({"page"})),
            css_cascade.Node("nav", id="nav", classes=frozenset({"menu"})),
            css_cascade.Node("a", classes=frozenset({"link", "active"}))]
    rules = [R(S([C(classes=("page",)), C(classes=("menu",)), C(classes=("link",))], [" ", " "]), "teal", order=0),
             R(S([C(id="nav"), C(tag="a")], [" "]), "navy", order=1),
             R(S([C(classes=("page",)), C(tag="a")], [">"]), "red", important=True, order=2)]
    assert rules[0].sel.spec() == (0, 3, 0) and rules[1].sel.spec() == (1, 0, 1)
    assert not rules[2].sel.matches(path)  # a — не прямой потомок .page
    assert css_cascade._winner(rules, path).color == "navy"
    assert css_cascade._winner(rules, path, "matching_ignored").color == "red"


def test_accessible_name_solver():
    El = html_a11y.El
    h = El("h2", {"id": "t"}, ["Корзина"])
    btn = El("button", {"aria-labelledby": "t", "aria-label": "Удалить"}, ["×"])
    assert html_a11y.acc_name(btn, [h, btn]) == "Корзина"
    icon = El("button", {"title": "Поделиться"}, [El("svg", {"aria-hidden": "true"}, [El("title", {}, ["x"])])])
    assert html_a11y.acc_name(icon, [icon]) == "Поделиться"
    img = El("button", {}, [El("img", {"src": "cart.svg", "alt": "Корзина"}), " Оплатить"])
    assert html_a11y.acc_name(img, [img]) == "Корзина Оплатить"
    inp = El("input", {"id": "e", "placeholder": "a@b.ru"})
    lab = El("label", {"for": "e"}, ["Почта"])
    assert html_a11y.acc_name(inp, [lab, inp]) == "Почта"
    assert html_a11y.acc_name(inp, [inp]) == "a@b.ru"


def test_async_bug_line_fixes_are_verified_by_execution():
    fam = all_families()["js.async_await_errors"]
    kinds = set()
    for seed in range(30):
        spec = fam.build(random.Random(seed))
        outs = _common.js_outputs(fam, seed)
        kinds.add(spec.data["kind"])
        assert outs["orig"].strip() != spec.data["expected"]
        good = [i for i, (_, _, tag) in enumerate(spec.data["fixes"]) if tag is None]
        assert len(good) == 1 and outs[f"fix{good[0]}"].strip() == spec.data["expected"]
    assert kinds == {"foreach", "try", "cond"}


@pytest.mark.parametrize("fid,fam", FAMS, ids=[f for f, _ in FAMS])
def test_text_keys_are_typeable(fid, fam):
    """Ключ текстового ответа набирается с обычной клавиатуры: ASCII и кириллица, без «—», «₽» и т. п."""
    if fam.generate(0).rendered.answer_mode != "text":
        pytest.skip("не текстовый ответ")
    typeable = re.compile(r"^[\x20-\x7EА-Яа-яЁё]*$")
    for seed in range(200):
        inst = fam.generate(seed)
        assert typeable.match(inst.key), (fid, seed, inst.key)


def test_norm_output_accepts_typographic_dashes():
    # автозамена дефиса на тире/минус не должна делать верный ответ неверным
    assert _common.norm_output("20 — 11") == _common.norm_output("20 - 11") == "20 - 11"
    assert _common.norm_output("−5") == "-5"


def test_list_keys_options_come_from_the_code_and_keys_matter():
    fam = all_families()["react.list_keys"]
    for seed in range(300):
        inst = fam.generate(seed)
        code = inst.rendered.code
        for o in inst.rendered.options:
            # вариант-заголовок, которого нет в списке, отбрасывался бы без понимания ключей
            assert o.text == react_effects.NONE_CHECKED or f'"{o.text}"' in code, (seed, o.text)
        # в каждом клоне key={index} и key={id} дают разные ответы
        tags = set(inst.distractors.values())
        assert {"follows_item", "stays_at_position"} & tags, seed


def test_effect_deps_always_contains_cleanup_order_trap():
    fam = all_families()["react.effect_deps"]
    for seed in range(300):
        assert "interleaved" in fam.generate(seed).distractors.values(), seed


def test_controlled_input_upper_has_slice_misconception():
    m = react_state._model_input
    assert m("upper", "ab-c 1d", 3) == "ABC"
    assert m("upper", "ab-c 1d", 3, frozenset({"slice_ignored"})) == "ABC1D"
    fam = all_families()["react.controlled_input"]
    for seed in range(200):
        inst = fam.generate(seed)
        if "toUpperCase" in inst.rendered.code:
            assert "filter_without_max" not in inst.distractors.values(), seed


def test_ts_union_display_order_matches_tsc():
    T = ts_types
    # tsc: встроенные string < number < boolean, затем объектные типы, затем null и undefined
    assert T.show([T.STR_ARR, T.NUMBER, T.UNDEF, T.STRING, T.NULL]) == "string | number | string[] | null | undefined"
    assert T.show([T.DATE, T.BOOLEAN]) == "boolean | Date"


def test_catalog_lists_every_family():
    catalog = (Path(js_async.__file__).parent / "CATALOG.md").read_text(encoding="utf-8")
    for fid, fam in FAMS:
        row = next((ln for ln in catalog.splitlines() if f"`{fid}`" in ln), None)
        assert row is not None, fid
        assert fam.module in row and fam.format in row and fam.target_grade in row
