"""html_css: доступность — какое доступное имя получит элемент управления (mcq_concept).

Ключ — точный решатель по подмножеству алгоритма Accessible Name (accname 1.2) и HTML-AAM для
конструкций, где браузеры единодушны: aria-labelledby > aria-label > нативная подпись (label для
input, содержимое для button/a) > title > placeholder (только input). Содержимое: текст, alt у img,
элементы с aria-hidden="true" пропускаются. aria-describedby даёт описание, а не имя.
Разметка строится из той же структуры, которую решает решатель.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from app.testing.bank.base import Family, Instance, Rendered, register
from app.testing.bank.families.frontend_js._common import explain, make_choice, norm_choice, pick

NO_NAME = "Имени нет — скринридер назовёт только роль"


@dataclass
class El:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[El | str] = field(default_factory=list)

    def html(self) -> str:
        attrs = "".join(f' {k}="{v}"' for k, v in self.attrs.items())
        if self.tag in ("img", "input"):
            return f"<{self.tag}{attrs}>"
        inner = "".join(c if isinstance(c, str) else c.html() for c in self.children)
        return f"<{self.tag}{attrs}>{inner}</{self.tag}>"


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _text(n: El | str, flags: frozenset[str], skip: El | None = None) -> str:
    """Текст из содержимого (accname шаг 2F) для нашего подмножества разметки."""
    if isinstance(n, str):
        return n
    if n is skip:
        return ""
    if n.attrs.get("aria-hidden") == "true" and "hidden_included" not in flags:
        return ""
    if n.tag == "img":
        return "" if "alt_ignored" in flags else n.attrs.get("alt", "")
    if n.tag == "svg":
        return "".join(_text(c, flags) for c in n.children) if "hidden_included" in flags else ""
    return "".join(_text(c, flags, skip) for c in n.children)


def acc_name(target: El, doc: list[El], flags: frozenset[str] = frozenset()) -> str:
    by_id: dict[str, El] = {}

    def index(n: El | str) -> None:
        if isinstance(n, El):
            if "id" in n.attrs:
                by_id[n.attrs["id"]] = n
            for c in n.children:
                index(c)

    for n in doc:
        index(n)
    a = target.attrs
    if "describedby_is_name" in flags and "aria-describedby" in a:
        return _ws(_text(by_id[a["aria-describedby"]], flags))
    if "title_first" in flags and a.get("title"):
        return _ws(a["title"])
    if "aria-labelledby" in a and "labelledby_ignored" not in flags:
        return _ws(" ".join(_text(by_id[i], flags) for i in a["aria-labelledby"].split()))
    native = ""
    if target.tag == "input":
        if "placeholder_first" in flags and a.get("placeholder"):
            return _ws(a["placeholder"])
        if "label_ignored" not in flags:
            for n in doc:
                label = _find_label(n, target)
                if label is not None:
                    native = _ws(_text(label, flags, skip=target))
                    break
    else:
        native = _ws(_text(target, flags))
    if "content_first" in flags and native:
        return native
    if a.get("aria-label", "").strip():
        return _ws(a["aria-label"])
    if native:
        return native
    if a.get("title"):
        return _ws(a["title"])
    if target.tag == "input" and a.get("placeholder"):
        return _ws(a["placeholder"])
    return ""


def _find_label(n: El | str, target: El) -> El | None:
    if isinstance(n, str):
        return None
    if n.tag == "label":
        if n.attrs.get("for") and n.attrs.get("for") == target.attrs.get("id"):
            return n
        if not n.attrs.get("for") and _contains(n, target):
            return n
    for c in n.children:
        r = _find_label(c, target)
        if r is not None:
            return r
    return None


def _contains(n: El | str, target: El) -> bool:
    if n is target:
        return True
    return isinstance(n, El) and any(_contains(c, target) for c in n.children)


_A11Y_HINTS = {
    "content_first": "aria-label заменяет видимый текст в доступном имени — он важнее содержимого.",
    "title_first": "title — последний запасной источник имени: он используется, только если других нет.",
    "labelledby_ignored": "aria-labelledby имеет наивысший приоритет — имя берётся из указанных элементов.",
    "alt_ignored": "alt у img внутри кнопки входит в её имя.",
    "hidden_included": "элементы с aria-hidden=\"true\" скрыты от вспомогательных технологий и в имя не входят.",
    "describedby_is_name": "aria-describedby задаёт описание (читается после имени), а не само имя.",
    "placeholder_first": "placeholder — запасной вариант: связанный <label> важнее.",
    "label_ignored": "<label>, внутри которого лежит поле, тоже подписывает его — for не обязателен.",
    "no_name_wrong": "у элемента есть источник имени — проверьте title и атрибуты.",
    "other": "пройдите по приоритету: aria-labelledby → aria-label → label/содержимое → title → placeholder.",
}

_BTN = ["Удалить", "Сохранить", "Закрыть окно", "Добавить в корзину", "Поделиться", "Отправить", "Обновить",
        "Скачать отчёт", "Найти", "Оплатить"]
_ICON_TEXT = ["★", "×", "→", "♥", "✓", "⋯"]
_HEAD = ["Корзина", "Новый заказ", "Настройки профиля", "Оплата", "Доставка", "Уведомления"]
# (файл, alt, видимый текст): alt рядом с текстом попадает в имя — частая ошибка «двойного» объявления
_ICONS = [("trash.svg", "Корзина", "Удалить"), ("cart.svg", "Тележка", "В корзину"),
          ("download.svg", "Стрелка вниз", "Скачать"), ("share.svg", "Поделиться", "Поделиться"),
          ("search.svg", "Лупа", "Найти"), ("bell.svg", "Колокольчик", "Уведомления")]
_ALTS = [alt for _, alt, _ in _ICONS]
# (видимый глагол, контекст для aria-label, подсказка title)
_ACTIONS = [("Удалить", ["заказ №{n}", "адрес доставки", "карту •• {n}"], "Действие нельзя отменить"),
            ("Скачать", ["счёт №{n}", "отчёт за март", "договор №{n}"], "Файл PDF"),
            ("Изменить", ["адрес доставки", "заказ №{n}", "профиль"], "Откроется форма"),
            ("Отменить", ["заказ №{n}", "подписку", "бронь №{n}"], "Деньги вернутся на карту"),
            ("Открыть", ["чат с продавцом", "заказ №{n}", "карточку товара"], "Откроется в новом окне")]
_CARDS = ["Тариф «Бизнес»", "Тариф «Старт»", "Курс по React", "Квартира на Лесной", "Ноутбук X15"]
_FIELDS = [("email", "Электронная почта", "name@example.ru", "Мы не передаём адрес третьим лицам"),
           ("phone", "Телефон", "+7 900 000-00-00", "Позвоним для подтверждения заказа"),
           ("city", "Город доставки", "Например, Казань", "Курьер работает в 40 городах"),
           ("promo", "Промокод", "SUMMER25", "Скидка применится к корзине"),
           ("inn", "ИНН организации", "10 или 12 цифр", "Проверим по реестру ФНС")]


def _svg_icon(hidden: bool, title: str | None = None) -> El:
    attrs = {"aria-hidden": "true"} if hidden else {}
    children: list[El | str] = [El("title", {}, [title])] if title else []
    children.append(El("path", {"d": "M4 4h16"}))
    return El("svg", attrs, children)


@register
class A11yName(Family):
    id = "css.a11y_name"
    title = "Доступное имя кнопки и поля"
    specs = ("frontend_js",)
    module = "html_css"
    format = "mcq_concept"
    target_grade = "junior"
    position = 0.5
    c = 0.25
    time_limit_s = 90

    def normalize(self, answer):
        return norm_choice(answer)

    def _scenario(self, rng: random.Random) -> tuple[list[El], El, list[str], str]:
        """Вернуть (документ, целевой элемент, применимые теги заблуждений, пояснение)."""
        kind = pick(rng, ["btn_aria", "btn_labelledby", "btn_img", "btn_hidden", "icon_title",
                          "input_for", "input_wrapped", "input_aria"])
        if kind == "btn_aria":
            verb, objects, title = pick(rng, _ACTIONS)
            label = f"{verb} {pick(rng, objects)}".format(n=rng.randint(100, 9999))
            t = El("button", {"type": "button", "aria-label": label, "title": title},
                   [_svg_icon(True), f" {verb}"])
            return [t], t, ["content_first", "title_first"], "aria-label задан и не пуст — он и есть имя; видимый текст и title не используются."
        if kind == "btn_labelledby":
            card = pick(rng, _CARDS)
            hid = f"card-{rng.randint(1, 99)}-title"
            h = El("h3", {"id": hid}, [card])
            t = El("button", {"type": "button", "aria-labelledby": hid, "title": "Подробнее о товаре"}, ["Подробнее"])
            return [h, t], t, ["labelledby_ignored", "title_first"], "aria-labelledby — самый приоритетный источник: имя = текст элемента #" + hid + ", а не видимое «Подробнее»."
        if kind == "btn_img":
            src, alt, visible = pick(rng, _ICONS)
            t = El("button", {"type": "button"}, [El("img", {"src": src, "alt": alt}), f" {visible}"])
            return [t], t, ["alt_ignored"], ("Имя кнопки складывается из содержимого: alt картинки + текст. Поэтому "
                                             "декоративной иконке рядом с текстом ставят alt=\"\" — иначе скринридер читает лишнее.")
        if kind == "btn_hidden":
            visible = pick(rng, _BTN)
            icon = pick(rng, _ICON_TEXT)
            hint = pick(rng, ["Действие нельзя отменить", "Откроется в новом окне", "Займёт пару минут",
                              "Нужна авторизация"])
            hid = pick(rng, ["hint", "help-text", "note", "desc"])
            t = El("button", {"type": "button", "aria-describedby": hid},
                   [El("span", {"aria-hidden": "true"}, [icon]), f" {visible}"])
            p = El("p", {"id": hid}, [hint])
            return [t, p], t, ["hidden_included", "describedby_is_name"], "Значок скрыт через aria-hidden, aria-describedby даёт описание — имя берётся из видимого текста."
        if kind == "icon_title":
            src, svg_title, title = pick(rng, _ICONS)
            t = El("button", {"type": "button", "title": title}, [_svg_icon(True, svg_title)])
            return [t], t, ["hidden_included", "no_name_wrong"], "Содержимого для имени нет (svg скрыт), aria-* нет — используется запасной источник title."
        field_id, label_text, placeholder, hint = pick(rng, _FIELDS)
        if kind == "input_for":
            hid = f"{field_id}-hint"
            lab = El("label", {"for": field_id}, [label_text])
            inp = El("input", {"id": field_id, "type": "text", "placeholder": placeholder, "aria-describedby": hid})
            small = El("small", {"id": hid}, [hint])
            return [lab, inp, small], inp, ["placeholder_first", "describedby_is_name"], "Поле подписано через <label for> — это имя; placeholder — лишь запасной вариант, aria-describedby — описание."
        if kind == "input_wrapped":
            inp = El("input", {"type": "text", "placeholder": placeholder})
            lab = El("label", {}, [f"{label_text} ", inp])
            return [lab], inp, ["placeholder_first", "label_ignored"], "Поле лежит внутри <label> — его текст и есть имя (for не нужен)."
        aria = pick(rng, [f"{label_text} получателя", f"{label_text} для чека", f"{label_text} (обязательно)"])
        lab = El("label", {"for": field_id}, [label_text])
        inp = El("input", {"id": field_id, "type": "text", "aria-label": aria, "placeholder": placeholder})
        return [lab, inp], inp, ["content_first", "placeholder_first"], "aria-label важнее связанного <label>: имя берётся из aria-label."

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        doc, target, tags, why = self._scenario(rng)
        correct = acc_name(target, doc) or NO_NAME
        wrong: list[tuple[str, str]] = []
        for t in tags:
            if t == "no_name_wrong":
                wrong.append((NO_NAME, "no_name_wrong"))
                continue
            wrong.append((acc_name(target, doc, frozenset({t})) or NO_NAME, t))
        # правдоподобные «другие» варианты — строки из той же разметки
        pool: list[str] = []
        for n in doc:
            for k in ("aria-label", "title", "placeholder", "alt"):
                if isinstance(n, El) and n.attrs.get(k):
                    pool.append(n.attrs[k])
            txt = _ws(_text(n, frozenset({"hidden_included"})))
            if txt:
                pool.append(txt)
        rng.shuffle(pool)
        wrong += [(p, "other") for p in pool]
        if target.tag == "button":
            fillers = rng.sample(_BTN, 3)
        else:
            fillers = [f[1] for f in rng.sample(_FIELDS, 3)]
        wrong += [(NO_NAME, "other")] + [(f, "other") for f in fillers]
        ch = make_choice(rng, correct, wrong, k=4)
        role = "кнопки" if target.tag == "button" else "поля ввода"
        code = "\n".join(n.html() for n in doc)
        show = {oid: next(o.text for o in ch.options if o.id == oid) for oid in ch.distractors}
        main = f"{why} Доступное имя: «{correct}»."
        rendered = Rendered(
            prompt=f"Какое доступное имя (accessible name) получит элемент управления — что скринридер прочитает как название {role}?",
            code=code,
            language="html",
            options=ch.options,
            answer_mode="choice",
        )
        return Instance(self.id, seed, rendered, ch.key, ch.distractors,
                        explain(main, ch.distractors, _A11Y_HINTS, show))
