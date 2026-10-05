"""html_css: каскад — какой цвет получит ссылка (специфичность, !important, порядок, совпадение селектора).

Селекторы строятся структурно (составные селекторы + комбинаторы « » и «>»), поэтому и совпадение
с элементом, и специфичность (a, b, c) вычисляются, а не размечаются вручную (Selectors Level 4 §16,
CSS Cascade 4 §6). В каждом клоне есть !important-правило (иногда не совпадающее с элементом)
и «сильный» селектор, который НЕ совпадает, — постоянная сложность: нужно проверить совпадение.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.testing.bank.base import Family, Instance, Rendered, register
from app.testing.bank.families.frontend_js._common import explain, make_choice, norm_choice, pick


@dataclass(frozen=True)
class Node:
    tag: str
    id: str | None = None
    classes: frozenset[str] = frozenset()
    attrs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Compound:
    tag: str | None = None
    id: str | None = None
    classes: tuple[str, ...] = ()
    attrs: tuple[tuple[str, str], ...] = ()

    def matches(self, n: Node) -> bool:
        return ((self.tag is None or self.tag == n.tag) and (self.id is None or self.id == n.id)
                and set(self.classes) <= n.classes and all(a in n.attrs for a in self.attrs))

    def text(self) -> str:
        s = self.tag or ""
        if self.id:
            s += f"#{self.id}"
        s += "".join(f".{c}" for c in self.classes)
        s += "".join(f'[{k}="{v}"]' for k, v in self.attrs)
        return s or "*"

    def spec(self) -> tuple[int, int, int]:
        return (1 if self.id else 0, len(self.classes) + len(self.attrs), 1 if self.tag else 0)


@dataclass
class Selector:
    parts: list[Compound]  # слева направо
    combs: list[str] = field(default_factory=list)  # между частями: " " или ">"

    def text(self) -> str:
        out = self.parts[0].text()
        for comb, part in zip(self.combs, self.parts[1:], strict=True):
            out += " > " + part.text() if comb == ">" else " " + part.text()
        return out

    def spec(self) -> tuple[int, int, int]:
        a = b = c = 0
        for p in self.parts:
            x, y, z = p.spec()
            a, b, c = a + x, b + y, c + z
        return a, b, c

    def matches(self, path: list[Node]) -> bool:
        """path — от корня к целевому элементу; сопоставление справа налево."""

        def rec(pi: int, ni: int) -> bool:
            if not self.parts[pi].matches(path[ni]):
                return False
            if pi == 0:
                return True
            comb = self.combs[pi - 1]
            if comb == ">":
                return ni > 0 and rec(pi - 1, ni - 1)
            return any(rec(pi - 1, k) for k in range(ni - 1, -1, -1))

        return rec(len(self.parts) - 1, len(path) - 1)


@dataclass
class Rule:
    sel: Selector
    color: str
    important: bool = False
    order: int = 0


_COLORS = ["crimson", "teal", "darkorange", "royalblue", "seagreen", "purple", "goldenrod", "slategray",
           "tomato", "navy", "olive", "orchid"]

_DOMAINS = [
    {"page": "page", "nav_id": "main-nav", "nav_cls": "menu", "c1": "link", "c2": "active", "c3": "disabled",
     "href": "/catalog", "text": "Каталог"},
    {"page": "layout", "nav_id": "top-bar", "nav_cls": "toolbar", "c1": "item", "c2": "current", "c3": "muted",
     "href": "/orders", "text": "Заказы"},
    {"page": "app", "nav_id": "sidebar", "nav_cls": "side-menu", "c1": "nav-link", "c2": "selected",
     "c3": "hidden", "href": "/reports", "text": "Отчёты"},
    {"page": "shell", "nav_id": "breadcrumbs", "nav_cls": "crumbs", "c1": "crumb", "c2": "last", "c3": "first",
     "href": "/profile", "text": "Профиль"},
]

_CASCADE_HINTS = {
    "last_rule_wins": "порядок в файле решает только при равной специфичности; сначала сравнивается специфичность.",
    "important_ignored": "объявление с !important побеждает обычные объявления независимо от специфичности.",
    "matching_ignored": "правило применяется, только если селектор совпадает с элементом — проверьте классы и комбинатор «>».",
    "longest_selector": "специфичность — это (id, классы и атрибуты, теги), сравниваемые по порядку, а не длина селектора.",
    "other": "найдите совпадающие правила, затем сравните !important, специфичность и порядок.",
}


def _winner(rules: list[Rule], path: list[Node], mode: str = "") -> Rule:
    cands = rules if mode == "matching_ignored" else [r for r in rules if r.sel.matches(path)]
    if mode == "last_rule_wins":
        return max(cands, key=lambda r: r.order)
    if mode == "longest_selector":
        return max(cands, key=lambda r: (r.important, len(r.sel.text()), r.order))
    imp = (lambda r: False) if mode == "important_ignored" else (lambda r: r.important)
    return max(cands, key=lambda r: (imp(r), r.sel.spec(), r.order))


@register
class Specificity(Family):
    id = "css.specificity"
    title = "Каскад: специфичность, !important и порядок правил"
    specs = ("frontend_js",)
    module = "html_css"
    format = "predict_output"
    target_grade = "junior"
    position = 0.0
    c = 0.25
    time_limit_s = 120
    is_anchor = True

    def normalize(self, answer):
        return norm_choice(answer)

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        d = pick(rng, _DOMAINS)
        div = Node("div", classes=frozenset({d["page"]}))
        nav = Node("nav", id=d["nav_id"], classes=frozenset({d["nav_cls"]}))
        a = Node("a", classes=frozenset({d["c1"], d["c2"]}), attrs=(("href", d["href"]),))
        path = [div, nav, a]

        def S(*items) -> Selector:  # noqa: N802 — мини-DSL: Compound, комбинатор, Compound…
            return Selector([i for i in items if isinstance(i, Compound)], [i for i in items if isinstance(i, str)])

        A = Compound(tag="a")  # noqa: N806
        id_rules = [S(Compound(id=d["nav_id"]), " ", A),
                    S(Compound(id=d["nav_id"]), " ", Compound(classes=(d["c2"],)))]
        multi = [S(Compound(classes=(d["page"],)), " ", Compound(classes=(d["nav_cls"],)), " ",
                   Compound(classes=(d["c1"],))),
                 S(Compound(classes=(d["c1"], d["c2"]))),
                 S(Compound(classes=(d["page"],)), " ", Compound(tag="a", classes=(d["c2"],)))]
        generic = [S(A), S(Compound(classes=(d["c1"],))), S(Compound(classes=(d["nav_cls"],)), " ", A),
                   S(Compound(tag="nav"), ">", Compound(tag="a", classes=(d["c1"],))),
                   S(Compound(tag="a", attrs=(("href", d["href"]),))), S(Compound(tag="div"), " ", Compound(tag="nav"), " ", A)]
        strong_miss = [S(Compound(id=d["nav_id"]), " ", Compound(classes=(d["c3"],))),
                       S(Compound(classes=(d["nav_cls"],)), ">", Compound(classes=(d["c1"], d["c3"]))),
                       S(Compound(id=d["nav_id"]), ">", Compound(tag="a", classes=(d["c3"],)))]
        weak_miss = [S(Compound(classes=(d["page"],)), ">", A),
                     S(Compound(tag="div"), ">", Compound(tag="a", classes=(d["c1"],)))]
        weak_hit = [S(A), S(Compound(classes=(d["c1"],))), S(Compound(classes=(d["nav_cls"],)), " ", A)]
        for _ in range(300):
            colors = rng.sample(_COLORS, 5)
            imp_sel = pick(rng, weak_hit) if rng.random() < 0.5 else pick(rng, weak_miss)
            gen = pick(rng, [g for g in generic if g.text() != imp_sel.text()])
            sels = [pick(rng, id_rules), pick(rng, multi), gen, pick(rng, strong_miss)]
            rules = [Rule(s, colors[i]) for i, s in enumerate(sels)] + [Rule(imp_sel, colors[4], important=True)]
            rng.shuffle(rules)
            for i, r in enumerate(rules):
                r.order = i
            correct = _winner(rules, path).color
            mis = {m: _winner(rules, path, m).color for m in
                   ("last_rule_wins", "important_ignored", "matching_ignored", "longest_selector")}
            if sum(1 for c in set(mis.values()) if c != correct) >= 2:
                break
        else:  # pragma: no cover
            raise ValueError("не удалось подобрать набор правил")
        wrong = [(mis[m], m) for m in ("matching_ignored", "important_ignored", "last_rule_wins", "longest_selector")]
        wrong += [(r.color, "other") for r in rules]
        ch = make_choice(rng, correct, wrong, k=4)
        css = [f"  {r.sel.text()} {{ color: {r.color}{' !important' if r.important else ''}; }}" for r in rules]
        html = [
            f'<div class="{d["page"]}">',
            f'  <nav id="{d["nav_id"]}" class="{d["nav_cls"]}">',
            f'    <a class="{d["c1"]} {d["c2"]}" href="{d["href"]}">{d["text"]}</a>',
            "  </nav>",
            "</div>",
        ]
        code = "\n".join(["<style>", *css, "</style>", "", *html])
        lines = []
        for r in rules:
            hit = r.sel.matches(path)
            sp = ",".join(map(str, r.sel.spec()))
            lines.append(f"{r.sel.text()} — {'совпадает' if hit else 'НЕ совпадает'}, специфичность ({sp})"
                         + (", !important" if r.important else ""))
        win = _winner(rules, path)
        main = ("Правила по порядку:\n" + "\n".join(f"• {x}" for x in lines)
                + f"\n\nСреди совпадающих сначала побеждает !important, затем большая специфичность (id, классы, "
                  f"теги), при равенстве — последнее в файле. Победитель: «{win.sel.text()}» → {correct}.")
        show = {oid: next(o.text for o in ch.options if o.id == oid) for oid in ch.distractors}
        rendered = Rendered(
            prompt=f"Какого цвета будет текст ссылки «{d['text']}»? Других стилей на странице нет.",
            code=code,
            language="html",
            options=ch.options,
            answer_mode="choice",
        )
        return Instance(self.id, seed, rendered, ch.key, ch.distractors,
                        explain(main, ch.distractors, _CASCADE_HINTS, show))
