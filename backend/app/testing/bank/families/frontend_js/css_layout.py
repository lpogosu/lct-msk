"""html_css: раскладка — блочная модель, распределение места во flex, контексты наложения.

Ключи — точные решатели по CSS-спецификациям (CSS Box Sizing 3, CSS Flexbox 1 §9.7, CSS 2.1 Appendix E)
для ограниченных сценариев, где результат однозначен: пустые flex-элементы без padding/border
(min-content = 0, ограничения min/max не срабатывают), позиционированные блоки с целыми z-index.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.testing.bank.base import Family, Instance, Rendered, register
from app.testing.bank.families.frontend_js._common import (
    explain,
    fmt_num,
    make_choice,
    norm_choice,
    norm_output,
    pick,
    text_distractors,
)

# =====================================================================================
# css.box_model — ширина содержимого и offsetWidth при content-box / border-box
# =====================================================================================

_BM_CLASSES = ["card", "panel", "tile", "banner", "modal", "notice", "badge", "toast", "promo", "widget"]

_BM_HINTS = {
    "sizing_swapped": "при border-box width задаёт ширину вместе с padding и border; при content-box — только содержимого.",
    "default_border_box": "по умолчанию box-sizing: content-box — width задаёт только ширину содержимого.",
    "margin_included": "offsetWidth — ширина по внешнему краю рамки: margin в неё не входит.",
    "single_side": "padding и border есть с обеих сторон: по горизонтали считаются левый и правый.",
    "shorthand_misread": "в записи из двух значений первое — вертикальные отступы (сверху/снизу), второе — горизонтальные.",
}


def _box_lr(values: list[int]) -> tuple[int, int]:
    """Левое и правое значение из сокращённой записи padding/margin (1, 2 или 4 значения)."""
    if len(values) == 1:
        return values[0], values[0]
    if len(values) == 2:
        return values[1], values[1]
    return values[3], values[1]  # top right bottom left


@register
class BoxModel(Family):
    id = "css.box_model"
    title = "Блочная модель: ширина содержимого и offsetWidth"
    specs = ("frontend_js",)
    module = "html_css"
    format = "predict_output"
    target_grade = "intern"
    position = 0.5
    c = 0.0
    time_limit_s = 90
    is_anchor = True

    def normalize(self, answer):
        return norm_output(str(answer).replace("px", " "))

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        cls = pick(rng, _BM_CLASSES)
        sizing = pick(rng, ["border-box", "content-box", None])
        width = rng.randrange(160, 520, 2)
        n_pad = pick(rng, [1, 2, 4])
        pad = [rng.randrange(4, 34, 2) for _ in range(n_pad)]
        if n_pad == 2:
            while pad[0] == pad[1]:
                pad[1] = rng.randrange(4, 34, 2)
        bw = rng.randint(1, 6)
        margin = [rng.randrange(0, 42, 2) for _ in range(pick(rng, [2, 4]))]
        pl, pr = _box_lr(pad)
        ml, mr = _box_lr(margin)

        def solve(border_box: bool, *, pad_lr=(pl, pr), sides: int = 2, with_margin: bool = False) -> str:
            ph = pad_lr[0] + pad_lr[1] if sides == 2 else pad_lr[0]
            b = 2 * bw if sides == 2 else bw
            content = width - ph - b if border_box else width
            offset = content + ph + b + ((ml + mr) if with_margin else 0)
            return f"{content} {offset}"

        is_bb = sizing == "border-box"
        key = solve(is_bb)
        wrong = [(solve(not is_bb), "default_border_box" if sizing is None else "sizing_swapped"),
                 (solve(is_bb, with_margin=True), "margin_included"),
                 (solve(is_bb, sides=1), "single_side")]
        if n_pad == 2:
            wrong.append((solve(is_bb, pad_lr=(pad[0], pad[0])), "shorthand_misread"))
        dist = text_distractors(key, wrong)
        lines = [f".{cls} {{", "  display: inline-block;"]
        if sizing:
            lines.append(f"  box-sizing: {sizing};")
        lines += [
            f"  width: {width}px;",
            f"  padding: {' '.join(f'{v}px' if v else '0' for v in pad)};",
            f"  border: {bw}px solid #d0d5dd;",
            f"  margin: {' '.join(f'{v}px' if v else '0' for v in margin)};",
            "}",
        ]
        content, offset = key.split()
        if is_bb:
            calc = (f"border-box: width включает padding и border, содержимое = {width} − {pl} − {pr} − 2·{bw} = "
                    f"{content}; offsetWidth = width = {offset}.")
        else:
            src = "box-sizing не указан — по умолчанию content-box" if sizing is None else "content-box"
            calc = (f"{src}: width — это ширина содержимого ({content}); offsetWidth = {content} + {pl} + {pr} + "
                    f"2·{bw} = {offset}.")
        main = f"{calc} margin в offsetWidth не входит. Ответ: «{key}»."
        rendered = Rendered(
            prompt=(f"Чему равны ширина области содержимого элемента .{cls} и его offsetWidth (в px)? "
                    "Глобальных стилей, меняющих box-sizing, нет."),
            code="\n".join(lines),
            language="css",
            answer_mode="text",
            answer_hint="Два числа через пробел: ширина содержимого, затем offsetWidth",
        )
        return Instance(self.id, seed, rendered, key, dist, explain(main, dist, _BM_HINTS))


# =====================================================================================
# css.flex_distribution — flex-grow / flex-basis / gap, в том числе ловушка «flex: 1 → basis 0»
# =====================================================================================

_FX_SETS = [
    ("toolbar", ["logo", "search", "actions", "profile"]),
    ("header", ["brand", "nav", "cart", "user"]),
    ("row", ["photo", "info", "price", "buy"]),
    ("footer", ["links", "news", "contacts", "social"]),
    ("panel", ["icon", "title", "meta", "menu"]),
]

_FX_HINTS = {
    "flex_n_keeps_width": "flex: N — это flex: N 1 0%: базовый размер 0, а width при заданном flex-basis не используется.",
    "gap_ignored": "gap уменьшает свободное место: из ширины контейнера вычитаются промежутки между элементами.",
    "equal_split": "свободное место делится пропорционально flex-grow, а не поровну.",
    "basis_ignored": "flex-grow распределяет только СВОБОДНОЕ место — то, что осталось после базовых размеров.",
}


@dataclass
class _FlexItem:
    cls: str
    kind: str
    grow: int
    basis: int  # px; для flex: N — 0
    width: int | None = None  # объявленный width (для «ловушки» и flex: none)
    shrink: int = 1


def _flex_solve(items: list[_FlexItem], container: int, gap: int, mode: str = "") -> list[float]:
    """Flexbox §9.7 для пустых элементов (min-content 0, без min/max): рост или сжатие по базе."""
    bases = []
    for it in items:
        b = it.basis
        if mode == "flex_n_keeps_width" and it.kind == "flexn":
            b = it.width or 0
        if mode == "basis_ignored" and it.grow > 0:
            b = 0
        bases.append(b)
    gaps = 0 if mode == "gap_ignored" else gap * (len(items) - 1)
    free = container - gaps - sum(bases)
    if free >= 0:
        total_grow = sum(it.grow for it in items)
        if total_grow == 0:
            return [float(b) for b in bases]
        if mode == "equal_split":
            n_grow = sum(1 for it in items if it.grow > 0)
            return [b + (free / n_grow if it.grow > 0 else 0) for b, it in zip(bases, items, strict=True)]
        return [b + free * it.grow / total_grow for b, it in zip(bases, items, strict=True)]
    weights = [it.shrink * b for it, b in zip(items, bases, strict=True)]
    tw = sum(weights)
    if tw == 0:
        return [float(b) for b in bases]
    return [b + free * w / tw for b, w in zip(bases, weights, strict=True)]


@register
class FlexDistribution(Family):
    id = "css.flex_distribution"
    title = "Flexbox: как делится свободное место"
    specs = ("frontend_js",)
    module = "html_css"
    format = "predict_output"
    target_grade = "middle"
    position = 0.0
    c = 0.0
    time_limit_s = 150
    is_anchor = True

    def normalize(self, answer):
        return norm_output(str(answer).replace("px", " "))

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        cont, names = pick(rng, _FX_SETS)
        classes = rng.sample(names, 3)
        kinds = ["flexn", pick(rng, ["basis", "width_grow", "auto"]),
                 pick(rng, ["none", "basis", "width_grow", "auto"])]
        rng.shuffle(kinds)
        items: list[_FlexItem] = []
        for cls, kind in zip(classes, kinds, strict=True):
            if kind == "flexn":
                items.append(_FlexItem(cls, kind, rng.randint(1, 3), 0, width=rng.randrange(80, 260, 10)))
            elif kind == "basis":
                items.append(_FlexItem(cls, kind, rng.randint(1, 3), rng.randrange(60, 220, 10)))
            elif kind == "width_grow":
                w = rng.randrange(60, 220, 10)
                items.append(_FlexItem(cls, kind, rng.randint(1, 3), w, width=w))
            elif kind == "auto":
                w = rng.randrange(60, 220, 10)
                items.append(_FlexItem(cls, kind, 1, w, width=w))
            else:
                w = rng.randrange(40, 160, 8)
                items.append(_FlexItem(cls, kind, 0, w, width=w, shrink=0))
        gap = pick(rng, [0, 8, 10, 12, 16, 20, 24])
        unit = rng.randint(8, 70)
        container = sum(it.basis for it in items) + gap * 2 + unit * sum(it.grow for it in items)
        css_rules = [f".{cont} {{ display: flex; width: {container}px; gap: {gap}px; }}"]
        for it in items:
            decl = {
                "flexn": f"width: {it.width}px; flex: {it.grow};",
                "basis": f"flex: {it.grow} 1 {it.basis}px;",
                "width_grow": f"width: {it.width}px; flex-grow: {it.grow};",
                "auto": f"width: {it.width}px; flex: auto;",
                "none": f"width: {it.width}px; flex: none;",
            }[it.kind]
            css_rules.append(f".{it.cls} {{ {decl} }}")
        html = [f'<div class="{cont}">', *[f'  <div class="{it.cls}"></div>' for it in items], "</div>"]
        code = "\n".join(["<style>", *[f"  {r}" for r in css_rules], "</style>", "", *html])

        def answer(mode: str = "") -> str:
            return " ".join(fmt_num(x) for x in _flex_solve(items, container, gap, mode))

        key = answer()
        dist = text_distractors(key, [(answer(m), m) for m in _FX_HINTS])
        bases = " + ".join(str(it.basis) for it in items)
        free = container - 2 * gap - sum(it.basis for it in items)
        main = (
            f"Базовые размеры: {', '.join(f'.{it.cls} = {it.basis}' for it in items)} (у flex: N база 0, "
            f"flex: auto и flex-grow без flex-basis берут width, flex: none не растёт). Свободное место: "
            f"{container} − 2·{gap} (gap) − ({bases}) = {free}. Оно делится пропорционально flex-grow "
            f"({' : '.join(str(it.grow) for it in items)}), по {unit}px на единицу. Ответ: «{key}»."
        )
        order = " ".join(f".{it.cls}" for it in items)
        rendered = Rendered(
            prompt=(f"Какой будет ширина каждого дочернего блока (в px)? Блоки пустые, без padding и border. "
                    f"Ответ — в порядке {order}."),
            code=code,
            language="html",
            answer_mode="text",
            answer_hint="Три числа через пробел в порядке блоков в разметке",
        )
        return Instance(self.id, seed, rendered, key, dist, explain(main, dist, _FX_HINTS))


# =====================================================================================
# css.stacking_context — какой блок окажется сверху (CSS 2.1 Appendix E)
# =====================================================================================


@dataclass
class _Box:
    name: str
    positioned: bool = False
    pos_kind: str = "relative"
    z: int | None = None  # None = auto
    effect: str | None = None  # "opacity" | "transform" — создают контекст без позиционирования
    children: list[_Box] = field(default_factory=list)
    order: int = 0


def _creates_ctx(b: _Box, flags: frozenset[str]) -> bool:
    if b.positioned and b.z is not None:
        return True
    if b.positioned and b.pos_kind == "fixed":
        return True  # fixed всегда создаёт контекст наложения
    if b.effect and "opacity_no_context" not in flags:
        return True
    if b.positioned and "auto_creates_context" in flags:
        return True
    return False


def _level(b: _Box, flags: frozenset[str]) -> tuple[int, int, int]:
    """(слой, z, порядок в дереве) внутри родительского контекста наложения."""
    if b.positioned or _creates_ctx(b, flags):
        z = b.z or 0
        rank = 0 if z < 0 else (2 if z == 0 else 3)
        return rank, z, b.order
    return 1, 0, b.order  # обычный блок в потоке — ниже позиционированных


def _paint_key(path: list[_Box], flags: frozenset[str]) -> list[tuple[int, int, int]]:
    """Ключ порядка отрисовки: уровни во всех контекстах от корня до элемента (больше — выше)."""
    key = []
    for i, b in enumerate(path):
        last = i == len(path) - 1
        if last or _creates_ctx(b, flags):
            key.append(_level(b, flags))
    return key


_SC_NAMES = ["modal", "header", "sidebar", "card", "dropdown", "tooltip", "toast", "badge", "popover",
             "overlay", "banner", "menu"]

_SC_HINTS = {
    "global_z": "z-index сравниваются только внутри одного контекста наложения: потомок не может выйти выше границ контекста родителя.",
    "auto_creates_context": "position без z-index (auto) контекст наложения не создаёт — z-index детей сравниваются на уровне выше.",
    "opacity_no_context": "opacity меньше 1 и transform создают контекст наложения, даже без position и z-index.",
    "dom_order": "порядок в разметке решает только при равных уровнях наложения.",
    "other": "сначала найдите контексты наложения, затем сравнивайте z-index внутри общего контекста.",
}


@register
class StackingContext(Family):
    id = "css.stacking_context"
    title = "z-index и контексты наложения"
    specs = ("frontend_js",)
    module = "html_css"
    format = "predict_output"
    target_grade = "senior"
    position = -0.3
    c = 0.25
    time_limit_s = 150

    def normalize(self, answer):
        return norm_choice(answer)

    @staticmethod
    def _parent(rng: random.Random, name: str) -> _Box:
        kind = pick(rng, ["z", "z", "auto", "opacity", "transform"])
        if kind == "z":
            return _Box(name, positioned=True, z=rng.randint(1, 6))
        if kind == "auto":
            return _Box(name, positioned=True)
        return _Box(name, effect=kind)

    @staticmethod
    def _css(b: _Box) -> str:
        parts = []
        if b.positioned:
            parts.append(f"position: {b.pos_kind};")
        if b.z is not None:
            parts.append(f"z-index: {b.z};")
        if b.effect == "opacity":
            parts.append("opacity: 0.9;")
        if b.effect == "transform":
            parts.append("transform: translateX(0);")
        return " ".join(parts)

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        for _ in range(500):
            names = rng.sample(_SC_NAMES, 5)
            p1, p2 = self._parent(rng, names[0]), self._parent(rng, names[1])
            c1 = _Box(names[2], positioned=True, pos_kind="absolute", z=rng.randint(1, 99))
            c2 = _Box(names[3], positioned=True, pos_kind="absolute", z=rng.randint(1, 99))
            p1.children, p2.children = [c1], [c2]
            top = _Box(names[4], positioned=True, pos_kind="fixed", z=rng.randint(1, 20))
            roots = [p1, p2]
            roots.insert(rng.randint(0, 2), top)
            paths: dict[str, list[_Box]] = {}
            n = 0
            for r in roots:
                r.order = n
                n += 1
                paths[r.name] = [r]
                for ch in r.children:
                    ch.order = n
                    n += 1
                    paths[ch.name] = [r, ch]
            extra = pick(rng, [p1, p2])
            candidates = [c1, c2, top, extra]

            def winner(flags: frozenset[str] = frozenset(), cands=candidates, ps=paths) -> str:
                return max(cands, key=lambda b: _paint_key(ps[b.name], flags)).name

            correct = winner()
            global_z = max(candidates, key=lambda b: (b.z or 0, b.order)).name
            if correct != global_z:  # ловушка контекста присутствует в каждом клоне
                break
        else:  # pragma: no cover
            raise ValueError("не удалось подобрать сценарий")
        dom_order = max(candidates, key=lambda b: b.order).name
        tag_for: dict[str, str] = {}
        for name, tag in ((global_z, "global_z"), (winner(frozenset({"auto_creates_context"})), "auto_creates_context"),
                          (winner(frozenset({"opacity_no_context"})), "opacity_no_context"), (dom_order, "dom_order")):
            if name != correct and name not in tag_for:
                tag_for[name] = tag
        wrong = [(c.name, tag_for.get(c.name, "other")) for c in candidates if c.name != correct]
        wrong.sort(key=lambda t: t[1] == "other")
        ch = make_choice(rng, f".{correct}", [(f".{w}", t) for w, t in wrong], k=4)

        css = []
        html = []
        for r in roots:
            css.append(f"  .{r.name} {{ {self._css(r)} }}")
            for c in r.children:
                css.append(f"  .{c.name} {{ {self._css(c)} }}")
            if r.children:
                html += [f'<div class="{r.name}">', *[f'  <div class="{c.name}"></div>' for c in r.children], "</div>"]
            else:
                html.append(f'<div class="{r.name}"></div>')
        code = "\n".join(["<style>", *css, "</style>", "", *html])

        def ctx_text(b: _Box) -> str:
            if b.positioned and b.z is not None:
                return f".{b.name} создаёт контекст наложения (z-index: {b.z})"
            if b.effect:
                return f".{b.name} создаёт контекст наложения ({'opacity < 1' if b.effect == 'opacity' else 'transform'}) на уровне z = 0"
            return f".{b.name} — position без z-index: контекст не создаёт, его потомки соревнуются на корневом уровне"

        main = (
            f"{ctx_text(p1)}; {ctx_text(p2)}; .{top.name} — position: fixed с z-index {top.z}. "
            "z-index потомка сравнивается только внутри контекста его родителя; снаружи весь контекст "
            f"участвует как единый слой с z-index родителя. Сверху окажется .{correct}."
        )
        show = {oid: next(o.text for o in ch.options if o.id == oid) for oid in ch.distractors}
        rendered = Rendered(
            prompt=("Все блоки имеют размеры и фон и перекрываются в одной точке экрана. Какой из перечисленных "
                    "блоков будет виден сверху в этой точке?"),
            code=code,
            language="html",
            options=ch.options,
            answer_mode="choice",
        )
        return Instance(self.id, seed, rendered, ch.key, ch.distractors,
                        explain(main, ch.distractors, _SC_HINTS, show))
