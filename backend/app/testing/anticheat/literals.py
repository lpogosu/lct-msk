"""Поиск литералов данных в показанном коде и подбор их новых значений для двойника.

Двойник = то же задание с ОДНИМ изменённым литералом данных (spec-antiassist §2.1, каталог мутаций M1).
Разрешены `data_literal` (элемент списка, значение словаря/объекта, множитель, присваиваемое значение)
и `table_value` (ячейка таблицы SQL). Запрещено всё, что воспроизводит типичную человеческую ошибку:
индексы и срезы, границы range, пороги сравнений, делители и модули, задержки таймеров. Эти контексты
определяются синтаксически по потоку токенов (Python — tokenize, JavaScript — собственный лёгкий разбор).

Правила выбора нового значения (все — для «малозаметности»):
- значение меняется на другое «того же вида»: то же число знаков (±1), та же «круглость» (1500 → 1900, не 1517);
  относительное изменение 12–80%, не «±1» (это похоже на ошибку на единицу);
- не пересекает пороги: для каждого порога p знак (x − p) сохраняется (граница не пересекается);
- сохраняет остатки по модулям из кода (x % 3 == 0 остаётся истинным), если это возможно;
- сохраняет порядок в отсортированном списке и не совпадает с другими числами кода (нет новых «ничьих»);
- не меняет значение, которое встречается в коде дважды (ничьи в данных задуманы автором);
- пары глифов 1/7, 3/8, 5/6, 0/8/9, l/1, O/0 не подставляются друг вместо друга.
"""

from __future__ import annotations

import bisect
import io
import random
import re
import tokenize
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.testing.anticheat.config import TwinParams
from app.testing.anticheat.executor import SqlTable

# ----------------------------------------------------------------------------- токены


@dataclass(frozen=True)
class Tok:
    kind: str  # num | str | name | op
    text: str
    start: int  # смещение в тексте кода
    end: int
    line: int  # с 1
    col: int  # с 0
    meta: str = ""  # для str: префикс (python) или кавычка/«template»/«regex» (js)


def _line_starts(code: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(code):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _pos(starts: list[int], offset: int) -> tuple[int, int]:
    line = bisect.bisect_right(starts, offset)
    return line, offset - starts[line - 1]


_PY_SKIP = {
    tokenize.NEWLINE, tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER,
}
_PY_FSTRING = {getattr(tokenize, n) for n in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END") if hasattr(tokenize, n)}


def tokenize_python(code: str) -> list[Tok] | None:
    starts = _line_starts(code)
    out: list[Tok] = []
    try:
        for t in tokenize.generate_tokens(io.StringIO(code).readline):
            if t.type in _PY_SKIP:
                continue
            start = starts[t.start[0] - 1] + t.start[1]
            if t.type == tokenize.NUMBER:
                kind, meta = "num", ""
            elif t.type == tokenize.STRING:
                kind, meta = "str", re.match(r"[A-Za-z]*", t.string).group(0).lower()  # type: ignore[union-attr]
            elif t.type == tokenize.NAME:
                kind, meta = "name", ""
            elif t.type == tokenize.OP:
                kind, meta = "op", ""
            elif t.type in _PY_FSTRING:
                kind, meta = "str", "f"
            else:
                continue
            out.append(Tok(kind, t.string, start, start + len(t.string), t.start[0], t.start[1], meta))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return out


_JS_NUM = re.compile(
    r"0[xX][0-9a-fA-F_]+n?|0[bB][01_]+n?|0[oO][0-7_]+n?|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?n?|\.\d+(?:[eE][+-]?\d+)?"
)
_JS_NAME = re.compile(r"[A-Za-z_$\u0080-￿][\w$\u0080-￿]*")
_JS_PUNCT = (
    ">>>=", "...", "===", "!==", "**=", "<<=", ">>=", ">>>", "&&=", "||=", "??=", "=>", "==", "!=", "<=", ">=",
    "&&", "||", "??", "?.", "++", "--", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "**", "<<", ">>",
)
_JS_REGEX_AFTER = frozenset({"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do"})


def tokenize_js(code: str) -> list[Tok]:
    n = len(code)
    starts = _line_starts(code)
    out: list[Tok] = []
    i = 0

    def add(kind: str, s: int, e: int, meta: str = "") -> None:
        line, col = _pos(starts, s)
        out.append(Tok(kind, code[s:e], s, e, line, col, meta))

    while i < n:
        c = code[i]
        if c.isspace():
            i += 1
        elif code.startswith("//", i):
            j = code.find("\n", i)
            i = n if j < 0 else j
        elif code.startswith("/*", i):
            j = code.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "\"'":
            j = i + 1
            while j < n and code[j] != c and code[j] != "\n":
                j += 2 if code[j] == "\\" else 1
            j = min(j + 1, n)
            add("str", i, j, c)
            i = j
        elif c == "`":
            j, depth = i + 1, 0
            while j < n:
                ch = code[j]
                if ch == "\\":
                    j += 2
                    continue
                if depth == 0 and ch == "`":
                    j += 1
                    break
                if ch == "$" and code.startswith("${", j):
                    depth += 1
                    j += 2
                    continue
                if depth > 0 and ch == "{":
                    depth += 1
                elif depth > 0 and ch == "}":
                    depth -= 1
                j += 1
            add("str", i, min(j, n), "template")
            i = min(j, n)
        elif c.isdigit() or (c == "." and i + 1 < n and code[i + 1].isdigit()):
            m = _JS_NUM.match(code, i)
            end = m.end() if m else i + 1
            add("num", i, end)
            i = end
        elif (m := _JS_NAME.match(code, i)) is not None:
            add("name", i, m.end())
            i = m.end()
        elif c == "/" and _regex_allowed(out):
            j, in_class = i + 1, False
            while j < n and code[j] != "\n":
                ch = code[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    break
                j += 1
            j = min(j + 1, n)
            while j < n and code[j].isalpha():
                j += 1
            add("str", i, j, "regex")
            i = j
        else:
            for p in _JS_PUNCT:
                if code.startswith(p, i):
                    add("op", i, i + len(p))
                    i += len(p)
                    break
            else:
                add("op", i, i + 1)
                i += 1
    return out


def _regex_allowed(out: list[Tok]) -> bool:
    if not out:
        return True
    prev = out[-1]
    if prev.kind == "op":
        return prev.text not in (")", "]", "}")
    return prev.kind == "name" and prev.text in _JS_REGEX_AFTER


def tokenize_code(code: str, language: str) -> list[Tok] | None:
    if language == "python":
        return tokenize_python(code)
    if language == "javascript":
        return tokenize_js(code)
    return None


# ----------------------------------------------------------------------------- глифы


def glyph_ok(old: str, new: str, pairs: frozenset[frozenset[str]]) -> bool:
    """Гейт 6: при равной длине ни одна позиция не меняет символ на путаемый (1/7, 3/8, 5/6, 0/8/9, l/1, O/0).

    При разной длине сравниваем выровненные справа символы: «98» → «108» не должно превращать 9 в 0/8.
    """
    a, b = old, new
    k = min(len(a), len(b))
    return all(frozenset((x, y)) not in pairs for x, y in zip(a[len(a) - k :], b[len(b) - k :], strict=True) if x != y)


# ----------------------------------------------------------------------------- описание числового литерала


@dataclass(frozen=True)
class NumSpec:
    """Всё, что нужно для подбора нового значения: сам литерал и ограничения на новое."""

    text: str  # как записан в коде (без знака)
    value: float  # модуль
    is_int: bool
    decimals: int
    sign: int  # -1, если перед числом унарный минус
    lo: float | None = None  # новый (со знаком) должен быть строго больше lo
    hi: float | None = None  # и строго меньше hi (сохранение порядка в отсортированном списке)

    @property
    def signed(self) -> float:
        return self.sign * self.value


@dataclass(frozen=True)
class NumLiteral:
    index: int  # индекс токена
    tok: Tok
    role: str
    tier: int
    spec: NumSpec


@dataclass(frozen=True)
class StrLiteral:
    index: int
    tok: Tok
    role: str  # string_digits | string_label
    start: int  # абсолютные смещения изменяемого фрагмента (серии цифр или буквы) в коде
    end: int
    text: str
    swap: bool = False  # однобуквенная метка, встречающаяся в коде несколько раз: меняем на ДРУГУЮ существующую


@dataclass
class Analysis:
    numbers: list[NumLiteral] = field(default_factory=list)
    strings: list[StrLiteral] = field(default_factory=list)
    pivots: list[float] = field(default_factory=list)  # пороги: их сторону сохраняем
    # «параметры»: последний числовой аргумент вызова f(данные, 60) — возможная скрытая граница (limit, n).
    # Другие литералы её не пересекают, но сам литерал остаётся кандидатом (индекс токена исключается при подборе).
    soft_pivots: list[tuple[int, float]] = field(default_factory=list)
    mods: list[int] = field(default_factory=list)  # модули `% m`: остатки сохраняем
    existing_abs: set[float] = field(default_factory=set)  # все числа кода (модули): новое на них не похоже
    labels: set[str] = field(default_factory=set)  # однобуквенные строки кода (в нижнем регистре)
    label_pool: set[str] = field(default_factory=set)  # они же как записаны в коде: для замены метки на существующую


def parse_number(text: str) -> tuple[float, bool, int] | None:
    """(модуль, целое?, число знаков после точки); None для 0x.., экспонент, «_» и ведущих нулей."""
    if re.fullmatch(r"(?:0|[1-9]\d*)", text):
        return float(int(text)), True, 0
    if re.fullmatch(r"(?:0|[1-9]\d*)\.\d+", text):
        return float(text), False, len(text.split(".")[1])
    return None


# ----------------------------------------------------------------------------- контекст токенов


_NON_CALL = frozenset(
    "if elif while for in not and or return yield lambda assert del with as is else await switch case of typeof "
    "catch throw".split()
)
_NON_VALUE = frozenset(
    "return in and or not if else elif yield await lambda is typeof case of del assert while for with as from "
    "import raise".split()
)


@dataclass
class _Frame:
    opener: str
    idx: int
    kind: str  # subscript | list | call | def | group | brace
    callee: str = ""


def _opener_kind(tokens: list[Tok], i: int) -> tuple[str, str]:
    t = tokens[i].text
    prev = tokens[i - 1] if i > 0 else None
    if t == "[":
        if prev and (
            (prev.kind in ("name", "str", "num") and prev.text not in _NON_VALUE) or prev.text in (")", "]")
        ):
            return "subscript", ""
        return "list", ""
    if t == "(":
        if prev and prev.kind == "name" and prev.text not in _NON_CALL:
            pp = tokens[i - 2] if i >= 2 else None
            if pp and pp.kind == "name" and pp.text in ("def", "function"):
                return "def", prev.text
            return "call", prev.text
        if prev and prev.text == "function":
            return "def", ""
        if prev and prev.text in (")", "]"):
            return "call", "?"
        return "group", ""
    return "brace", ""


def _match_brackets(tokens: list[Tok]) -> dict[int, int]:
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for i, t in enumerate(tokens):
        if t.kind != "op":
            continue
        if t.text in "([{":
            stack.append(i)
        elif t.text in ")]}" and stack:
            pairs[stack.pop()] = i
    return pairs


def _split_items(tokens: list[Tok], opener: int, closer: int) -> list[list[int]]:
    """Сегменты между запятыми верхнего уровня внутри скобок (индексы токенов)."""
    items: list[list[int]] = [[]]
    depth = 0
    for j in range(opener + 1, closer):
        t = tokens[j]
        if t.kind == "op" and t.text in "([{":
            depth += 1
        elif t.kind == "op" and t.text in ")]}":
            depth -= 1
        if depth == 0 and t.kind == "op" and t.text == ",":
            items.append([])
        else:
            items[-1].append(j)
    return [it for it in items if it]


def _signed_numeric_item(tokens: list[Tok], item: list[int]) -> float | None:
    if len(item) == 1 and tokens[item[0]].kind == "num":
        p = parse_number(tokens[item[0]].text)
        return p[0] if p else None
    if len(item) == 2 and tokens[item[0]].text == "-" and tokens[item[1]].kind == "num":
        p = parse_number(tokens[item[1]].text)
        return -p[0] if p else None
    return None


def _sorted_bounds(
    tokens: list[Tok], frame: _Frame, closer: int | None, num_idx: int
) -> tuple[float | None, float | None]:
    """Для числа в отсортированном списке чисел (≥ 3 элементов) — границы соседей, иначе (None, None)."""
    if closer is None:
        return None, None
    items = _split_items(tokens, frame.idx, closer)
    vals = [_signed_numeric_item(tokens, it) for it in items]
    if len(items) < 3 or any(v is None for v in vals):
        return None, None
    seq = [float(v) for v in vals if v is not None]
    asc = all(a <= b for a, b in zip(seq, seq[1:], strict=False))
    desc = all(a >= b for a, b in zip(seq, seq[1:], strict=False))
    if not (asc or desc):
        return None, None
    pos = next((k for k, it in enumerate(items) if num_idx in it), None)
    if pos is None:
        return None, None
    before = seq[pos - 1] if pos > 0 else None
    after = seq[pos + 1] if pos < len(seq) - 1 else None
    return (before, after) if asc else (after, before)


def _is_trailing_param(tokens: list[Tok], frame: _Frame, closer: int | None, i: int) -> bool:
    """Число — последний аргумент вызова, а первый аргумент — не литерал: f(items, 60), take(stream, 2)."""
    if closer is None:
        return False
    items = _split_items(tokens, frame.idx, closer)
    if len(items) < 2:
        return False
    last, first = items[-1], items[0]
    if i not in last or len(last) > 2:
        return False
    return not (len(first) == 1 and tokens[first[0]].kind in ("num", "str"))


def analyze_code(tokens: list[Tok], params: TwinParams) -> Analysis:
    """Классифицировать числовые и строковые литералы кода: кандидаты на подмену, пороги, модули."""
    an = Analysis()
    match = _match_brackets(tokens)
    cmp_ops, struct_ops = params.compare_ops, params.structural_ops

    # innermost frame на каждый токен
    frame_of: list[_Frame | None] = []
    stack: list[_Frame] = []
    for i, t in enumerate(tokens):
        if t.kind == "op" and t.text in ")]}":
            if stack:
                stack.pop()
            frame_of.append(stack[-1] if stack else None)
            continue
        frame_of.append(stack[-1] if stack else None)
        if t.kind == "op" and t.text in "([{":
            kind, callee = _opener_kind(tokens, i)
            stack.append(_Frame(t.text, i, kind, callee))

    def is_struct_frame(fr: _Frame | None) -> bool:
        return fr is not None and (fr.kind == "subscript" or (fr.kind == "call" and fr.callee in params.excluded_callees))

    def structural_use(j: int) -> bool:
        """Имя в токене j выступает границей: модуль/делитель или сравнивается НЕ с числом (amount > limit).

        Переменная, сравниваемая с числовой константой (count > 26), — данные; граница там — константа,
        и она уже в pivots (знак относительно неё сохраняется).
        """
        prev = tokens[j - 1].text if j > 0 else ""
        nxt = tokens[j + 1].text if j + 1 < len(tokens) else ""
        if prev in struct_ops or nxt in struct_ops:
            return True
        if nxt in cmp_ops:
            k = j + 2
            if k < len(tokens) and tokens[k].text in ("-", "+"):
                k += 1
            if not (k < len(tokens) and tokens[k].kind == "num"):
                return True
        if prev in cmp_ops and not (j >= 2 and tokens[j - 2].kind == "num"):
            return True
        return False

    def name_is_structural(name: str, binding_idx: int) -> bool:
        """Переменная, которой присвоен литерал, где-то используется как граница/индекс/модуль/порог."""
        for j, t in enumerate(tokens):
            if t.kind != "name" or t.text != name or j == binding_idx:
                continue
            if j > 0 and tokens[j - 1].text == ".":
                if not (j > 1 and tokens[j - 2].text in ("self", "this")):
                    continue
            if structural_use(j) or is_struct_frame(frame_of[j]):
                return True
        return False

    value_counts: dict[float, int] = {}
    for t in tokens:
        if t.kind != "num":
            continue
        parsed = parse_number(t.text)
        if parsed is None:
            continue
        value_counts[parsed[0]] = value_counts.get(parsed[0], 0) + 1

    for i, t in enumerate(tokens):
        if t.kind != "num":
            continue
        parsed = parse_number(t.text)
        if parsed is None:
            continue
        value, is_int, decimals = parsed
        an.existing_abs.add(round(value, 9))
        # унарный знак
        li = i - 1
        sign = 1
        if li >= 0 and tokens[li].kind == "op" and tokens[li].text in ("-", "+"):
            pp = tokens[li - 1] if li >= 1 else None
            if pp is None or (pp.kind == "op" and pp.text not in (")", "]", "}")) or pp.text in _NON_VALUE:
                sign = -1 if tokens[li].text == "-" else 1
                li -= 1
        left = tokens[li] if li >= 0 else None
        right = tokens[i + 1] if i + 1 < len(tokens) else None
        lt = left.text if left is not None and left.kind == "op" else ""
        rt = right.text if right is not None and right.kind == "op" else ""
        fr = frame_of[i]
        signed = sign * value

        role = _classify_number(tokens, i, li, fr, lt, rt, params)
        if role in ("assign", "kwarg", "default"):
            b = tokens[li - 1] if li >= 1 else None
            if b is not None and b.kind == "name" and name_is_structural(b.text, li - 1):
                role = "structural_var"

        if role in ("compare", "struct_op", "struct_arg", "structural_var"):
            # Пороги — только сравнения и переменные-границы: их сторону данные не пересекают. Модуль, делитель,
            # аргумент range/slice/таймера — не порог для чужих литералов; у модуля сохраняется остаток (mods).
            if role in ("compare", "structural_var"):
                an.pivots.append(signed)
            if lt == "%" and is_int and value >= 2:
                an.mods.append(int(value))
            continue
        if role in ("index",):
            continue
        # кандидат
        if (is_int and value < params.min_value) or value <= 0 or value > params.max_value or "_" in t.text:
            continue
        lo = hi = None
        if role == "element" and fr is not None and fr.kind in ("list", "group", "brace"):
            lo, hi = _sorted_bounds(tokens, fr, match.get(fr.idx), i)
        tier = params.role_tier.get(role, 3)
        if value_counts.get(value, 0) >= 2:
            tier = max(tier, params.dup_tier)  # ничьи в данных задуманы автором: трогаем в последнюю очередь
        spec = NumSpec(t.text, value, is_int, decimals, sign, lo, hi)
        an.numbers.append(NumLiteral(i, t, role, tier, spec))
        if role == "arg" and fr is not None and _is_trailing_param(tokens, fr, match.get(fr.idx), i):
            an.soft_pivots.append((i, signed))

    # пороги внутри кандидатов: значение, равное порогу, трогать нельзя
    pivot_vals = set(an.pivots)
    an.numbers = [n for n in an.numbers if n.spec.signed not in pivot_vals]

    _collect_strings(tokens, an, params, name_is_structural)
    return an


def _classify_number(
    tokens: list[Tok], i: int, li: int, fr: _Frame | None, lt: str, rt: str, params: TwinParams
) -> str:
    cmp_ops, struct_ops = params.compare_ops, params.structural_ops
    if lt in struct_ops or rt in struct_ops:
        return "struct_op"  # «x % 3 == 0»: у тройки соседи % и ==, но это модуль, а не порог сравнения
    if lt in cmp_ops or rt in cmp_ops:
        return "compare"
    if fr is not None and fr.kind == "subscript":
        return "index"
    if fr is not None and fr.kind == "call" and fr.callee in params.excluded_callees:
        return "struct_arg"
    # значение в литерале словаря/объекта: «ключ: число»
    if (
        lt == ":"
        and fr is not None
        and fr.kind == "brace"
        and li >= 2
        and tokens[li - 2].text in ("{", ",")
        and tokens[li - 1].kind in ("name", "str", "num")
    ):
        return "value"
    if lt == "*" or rt == "*":
        return "multiplier"
    sep_l = lt in ("(", "[", "{", ",")
    sep_r = rt in (",", ")", "]", "}")
    if sep_l and sep_r and fr is not None:
        if fr.kind in ("list", "group", "brace"):
            return "element"
        if fr.kind == "call":
            return "arg"
    if lt == "=":
        if fr is None:
            return "assign" if rt not in ("+", "-", "/") else "arith"
        if fr.kind == "call":
            return "kwarg"
        return "default"
    if lt == "*=":
        return "multiplier"
    if lt in ("+=", "-=", "/=", "+", "-", "/") or rt in ("+", "-", "/"):
        return "arith"
    if sep_l and sep_r:
        return "arg"
    return "other"


_CYR = re.compile(r"[А-Яа-яЁё]")


def _collect_strings(
    tokens: list[Tok], an: Analysis, params: TwinParams, name_is_structural: Callable[[str, int], bool]
) -> None:
    body_counts: dict[str, int] = {}
    bodies: dict[int, tuple[str, int]] = {}
    for i, t in enumerate(tokens):
        if t.kind != "str":
            continue
        body = _str_body(t)
        if body is None:
            continue
        bodies[i] = body
        body_counts[body[0]] = body_counts.get(body[0], 0) + 1
        if re.fullmatch(r"[A-Za-z]", body[0]):
            an.labels.add(body[0].lower())
            an.label_pool.add(body[0])
    for i, (lit, offset) in bodies.items():
        t = tokens[i]
        is_label = re.fullmatch(r"[A-Za-z]", lit) is not None
        if (body_counts[lit] >= 2 and not is_label) or len(lit) > params.string_max_len:
            continue
        if "\\" in lit or any(ch.isspace() for ch in lit) or _CYR.search(lit):
            continue
        prev = tokens[i - 1] if i > 0 else None
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if (prev and prev.text in params.compare_ops) or (nxt and nxt.text in params.compare_ops):
            continue
        if nxt is not None and nxt.text == ":":  # ключ словаря/объекта: переименование ломает программу
            continue
        if prev is not None and prev.text == "=" and i >= 2 and tokens[i - 2].kind == "name":
            if name_is_structural(tokens[i - 2].text, i - 2):
                continue
        base = t.start + offset
        if is_label:
            an.strings.append(StrLiteral(i, t, "string_label", base, base + 1, lit, swap=body_counts[lit] >= 2))
            continue
        for m in re.finditer(r"\d+", lit):
            an.strings.append(StrLiteral(i, t, "string_digits", base + m.start(), base + m.end(), m.group(0)))


def _str_body(t: Tok) -> tuple[str, int] | None:
    """(содержимое строки, смещение содержимого от начала токена); f/b-строки, шаблоны и regex пропускаем."""
    s = t.text
    if t.meta in ("template", "regex"):
        return None
    if t.meta in ("f", "b", "rb", "br", "fr", "rf") or (t.meta and t.meta not in ("'", '"', "u", "r")):
        return None
    prefix = 0 if t.meta in ("'", '"') else len(t.meta)
    quote = s[prefix : prefix + 3]
    qlen = 3 if quote in ('"""', "'''") else 1
    if len(s) < prefix + 2 * qlen:
        return None
    return s[prefix + qlen : len(s) - qlen], prefix + qlen


# ----------------------------------------------------------------------------- подбор новых значений


def _trailing_zeros(n: int) -> int:
    z = 0
    while n and n % 10 == 0:
        n //= 10
        z += 1
    return z


def propose_number(
    spec: NumSpec,
    *,
    pivots: list[float],
    mods: list[int],
    existing_abs: set[float],
    rng: random.Random,
    params: TwinParams,
    draws: int = 48,
) -> list[str]:
    """Новые записи числа в порядке предпочтения (малозаметность: длина, остатки, детерминированный rng)."""
    scale = 10**spec.decimals
    x = round(spec.value * scale)
    if x <= 0:
        return []
    g = 10 ** min(_trailing_zeros(x), max(len(str(x)) - 2, 0))
    k0 = x // g
    bounded = spec.lo is not None or spec.hi is not None
    # в отсортированном списке допустим и небольшой сдвиг внутри «щели» между соседями (порядок сохраняется)
    lo_k = max(0 if bounded else round(params.rel_change_min * k0), 2 if g == 1 else 1)
    hi_k = max(round(params.rel_change_max * k0), lo_k + 1)
    xs = spec.signed
    units: list[int] = []
    anchors: list[int] = []
    if spec.is_int and spec.lo is not None and spec.hi is not None:
        gap = [v for v in range(int(spec.lo) + 1, int(spec.hi)) if v != spec.sign * x]
        units = [abs(v) for v in gap[:400] if v * spec.sign > 0 and abs(abs(v) - x) >= (2 if g == 1 else 1)]
        rng.shuffle(units)
        units = units[:draws]
    else:
        for _ in range(draws):
            units.append((k0 + rng.choice((-1, 1)) * rng.randint(lo_k, hi_k)) * g)
        # «через соседа»: сразу за другим числом кода — так меняется порядок (сортировка, ранг, ORDER BY … LIMIT)
        for z in sorted(existing_abs):
            zu = round(z * scale)
            if zu <= 0 or zu == x:
                continue
            anchors += [c for c in (zu + g, zu - g) if abs(c - x) >= lo_k * g]
        rng.shuffle(anchors)

    seen: set[int] = set()

    def keep(batch: list[int]) -> list[tuple[int, int, int, str]]:
        out: list[tuple[int, int, int, str]] = []
        for n, y_units in enumerate(batch):
            if y_units == x or y_units in seen or y_units <= 0:
                continue
            y = y_units / scale
            if spec.is_int:
                if y < params.min_value or y > params.max_value:
                    continue
                text = str(y_units)
            else:
                if y_units < 2 or y > params.max_value:
                    continue
                text = f"{y:.{spec.decimals}f}"
                if text.endswith("0") and not spec.text.endswith("0"):
                    continue
            if abs(len(text) - len(spec.text)) > params.max_length_delta:
                continue
            if not glyph_ok(spec.text, text, params.confusable_pairs) or round(y, 9) in existing_abs:
                continue
            ys = spec.sign * y
            if any((xs - p) * (ys - p) <= 0 for p in pivots):
                continue
            if (spec.lo is not None and not ys > spec.lo) or (spec.hi is not None and not ys < spec.hi):
                continue
            seen.add(y_units)
            penalty = sum(1 for m in mods if spec.is_int and int(y) % m != int(spec.value) % m)
            out.append((abs(len(text) - len(spec.text)), penalty, n, text))
        return sorted(out)

    # приоритет: сначала подходящие по длине и остаткам; случайное значение (умеренное изменение) и «через соседа» чередуем
    random_part = [t for _, _, _, t in keep(units)]
    anchor_part = [t for _, _, _, t in keep(anchors)]
    merged: list[str] = []
    for i in range(max(len(random_part), len(anchor_part))):
        if i < len(random_part):
            merged.append(random_part[i])
        if i < len(anchor_part):
            merged.append(anchor_part[i])
    return merged


def propose_digit_run(run: str, rng: random.Random, params: TwinParams, limit: int = 8) -> list[str]:
    """Серия цифр внутри кодоподобной строки: меняем РОВНО одну цифру (длина та же)."""
    out: list[str] = []
    positions = list(range(len(run)))
    rng.shuffle(positions)
    digits = list("0123456789")
    for pos in positions:
        rng.shuffle(digits)
        for d in digits:
            if d == run[pos] or (pos == 0 and len(run) > 1 and d == "0"):
                continue
            if frozenset((run[pos], d)) in params.confusable_pairs:
                continue
            out.append(run[:pos] + d + run[pos + 1 :])
            if len(out) >= limit:
                return out
    return out


def propose_label(
    letter: str, used: set[str], rng: random.Random, params: TwinParams, swap_pool: set[str] | None = None
) -> list[str]:
    """Однобуквенная метка. Уникальная — на другую букву того же регистра, которой нет среди меток кода;
    повторяющаяся (вершина графа, значение из нескольких мест) — на ДРУГУЮ существующую метку (swap_pool)."""
    if swap_pool is not None:
        cands = sorted(c for c in swap_pool if c != letter and c.lower() != letter.lower())
        cands = [c for c in cands if frozenset((letter.upper(), c.upper())) not in params.confusable_pairs]
        rng.shuffle(cands)
        return cands
    pool = [c for c in params.label_letters if c.lower() not in used and c.lower() != letter.lower()]
    pool = [c for c in pool if frozenset((letter.upper(), c)) not in params.confusable_pairs]
    rng.shuffle(pool)
    return [c if letter.isupper() else c.lower() for c in pool]


# ----------------------------------------------------------------------------- SQL


@dataclass(frozen=True)
class CellCandidate:
    table: str
    row: int
    col: int
    column: str
    spec: NumSpec
    tier: int = 1


_SQL_NUM = re.compile(r"(?<![\w.'])(\d+(?:\.\d+)?)(?![\w'])")


def _strip_sql_strings(q: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", q)


def sql_pivots(query: str) -> list[float]:
    """Числовые константы запроса (пороги WHERE/HAVING, LIMIT/OFFSET): их сторону данные не пересекают."""
    return [float(m.group(1)) for m in _SQL_NUM.finditer(_strip_sql_strings(query))]


def sql_key_columns(query: str) -> set[str]:
    """Столбцы-ключи: GROUP BY / PARTITION BY / USING, равенства «столбец = столбец» (ON) и «столбец = число / IN (…)».

    Менять их значения нельзя: это меняет состав групп, пары соединения или попадание строки под фильтр.
    """
    q = _strip_sql_strings(query)
    cols: set[str] = set()
    for m in re.finditer(
        r"(?:GROUP|PARTITION)\s+BY\s+(.+?)(?=HAVING|ORDER|LIMIT|ROWS|RANGE|\)|;|$)", q, re.I | re.S
    ):
        for part in m.group(1).split(","):
            idents = re.findall(r"\w+", part)
            if idents:
                cols.add(idents[-1].lower())
    for m in re.finditer(r"USING\s*\(([^)]*)\)", q, re.I):
        cols.update(c.strip().lower() for c in m.group(1).split(",") if c.strip())
    for m in re.finditer(r"(?:\w+\.)?(\w+)\s*=\s*(?:\w+\.)?(\w+)", q):
        cols.update((m.group(1).lower(), m.group(2).lower()))
    for m in re.finditer(r"(?:\w+\.)?(\w+)\s*(?:=|!=|<>)\s*\d", q):
        cols.add(m.group(1).lower())
    for m in re.finditer(r"(?:\w+\.)?(\w+)\s+(?:NOT\s+)?IN\s*\(", q, re.I):
        cols.add(m.group(1).lower())
    return cols


def sql_cell_candidates(
    tables: Sequence[SqlTable], query: str, params: TwinParams
) -> tuple[list[CellCandidate], list[float]]:
    """Числовые ячейки таблиц, пригодные для подмены: не ключи, не id, не равные порогам, без ничьих в столбце."""
    pivots = sql_pivots(query)
    keycols = sql_key_columns(query)
    id_res = [re.compile(p, re.I) for p in params.sql_id_patterns]
    out: list[CellCandidate] = []
    for t in tables:
        for j, col in enumerate(t.columns):
            if col.lower() in keycols or any(r.search(col) for r in id_res):
                continue
            column = [r[j] for r in t.rows]
            nums = [v for v in column if isinstance(v, int | float) and not isinstance(v, bool)]
            if len(nums) < 2:
                continue
            for i, v in enumerate(column):
                if not isinstance(v, int | float) or isinstance(v, bool) or v is None:
                    continue
                if v < params.min_value or v > params.max_value or nums.count(v) >= 2 or float(v) in pivots:
                    continue
                is_int = isinstance(v, int)
                decimals = 0
                text = str(v)
                if not is_int:
                    if "e" in text.lower():
                        continue
                    decimals = len(text.split(".")[1]) if "." in text else 0
                    if decimals == 0:
                        continue
                spec = NumSpec(text, float(v), is_int, decimals, 1)
                out.append(CellCandidate(t.name, i, j, col, spec, params.role_tier.get("cell", 1)))
    return out, pivots
