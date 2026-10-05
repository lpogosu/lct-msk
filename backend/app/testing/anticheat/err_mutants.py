"""Мутанты типичных ошибок: K_err(X) для гейта 3 генератора двойников (spec-antiassist §2.1, §7 err_mutants).

K_err(X) — множество ключей, которые получаются ИСПОЛНЕНИЕМ показанного задания X с типичной человеческой
ошибкой: на единицу в границе или в range, `//` вместо `/` и наоборот, пропуск первого или последнего элемента,
перезапись вместо накопления, отрицание условия, «забыл множитель», регистр строк, сортировка не в ту сторону,
позднее связывание (var вместо let), NULL в агрегатах и т. д. Ключ двойника не должен входить в K_err(X): иначе
честный кандидат, ошибившийся типичным образом, дал бы ответ, неотличимый от ответа на текст из буфера обмена.

Мутаторы — общие, работают по потоку токенов (literals.tokenize_code), без участия автора семейства. Каждый даёт
правку исходника; мутант исполняется тем же исполнителем, что и ключи (run_python / run_js / run_sql). Ничего
вручную не вводится, LLM не вызывается. Порядок мутантов детерминирован: при превышении лимита берутся по кругу
разные типы ошибок, чтобы набор покрывал все типы.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterator, Sequence
from dataclasses import dataclass

from app.testing.anticheat import literals as lit
from app.testing.anticheat.config import ErrParams
from app.testing.anticheat.executor import ExecOutcome, SqlTable, project_rows, run_many, run_query
from app.testing.bank.base import Family, canonical

Edit = tuple[int, int, str]  # [начало, конец) → замена


@dataclass(frozen=True)
class ErrMutant:
    """Исходник с одной типичной ошибкой. label — тип ошибки (для отчёта и разбора), не пользовательский текст."""

    label: str
    source: str


def _apply(code: str, edits: Sequence[Edit]) -> str:
    out = code
    for start, end, repl in sorted(edits, key=lambda e: -e[0]):
        out = out[:start] + repl + out[end:]
    return out


# ----------------------------------------------------------------------------- контекст токенов


@dataclass
class _Ctx:
    code: str
    toks: list[lit.Tok]
    language: str
    match: dict[int, int]  # индекс открывающей скобки → индекс закрывающей
    frame_kind: list[str]  # вид внутреннего фрейма токена: subscript | list | call | def | group | brace | ""
    depth: list[int]  # глубина вложенности скобок перед токеном

    @property
    def py(self) -> bool:
        return self.language == "python"


def _context(code: str, language: str) -> _Ctx | None:
    toks = lit.tokenize_code(code, language)
    if toks is None:
        return None
    match = lit._match_brackets(toks)  # noqa: SLF001 — тот же пакет, общий разбор скобок
    kinds: list[str] = []
    depth: list[int] = []
    stack: list[str] = []
    for i, t in enumerate(toks):
        if t.kind == "op" and t.text in ")]}":
            if stack:
                stack.pop()
            kinds.append(stack[-1] if stack else "")
            depth.append(len(stack))
            continue
        kinds.append(stack[-1] if stack else "")
        depth.append(len(stack))
        if t.kind == "op" and t.text in "([{":
            stack.append(lit._opener_kind(toks, i)[0])  # noqa: SLF001
    return _Ctx(code, toks, language, match, kinds, depth)


# ----------------------------------------------------------------------------- мутаторы Python / JavaScript

Mutator = Callable[[_Ctx], Iterator[tuple[str, list[Edit]]]]

_BOUNDARY = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}
_VALUE_END = frozenset({")", "]"})


def _is_value_end(t: lit.Tok) -> bool:
    return t.kind in ("num", "name", "str") or t.text in _VALUE_END


def _m_boundary(c: _Ctx) -> Iterator[tuple[str, list[Edit]]]:
    """Ошибка на единицу в сравнении: < ↔ <=, > ↔ >=."""
    for t in c.toks:
        if t.kind == "op" and t.text in _BOUNDARY:
            yield "boundary", [(t.start, t.end, _BOUNDARY[t.text])]


def _stop_item(c: _Ctx, opener: int) -> list[int] | None:
    closer = c.match.get(opener)
    if closer is None:
        return None
    items = lit._split_items(c.toks, opener, closer)  # noqa: SLF001
    if not items:
        return None
    return items[1] if len(items) >= 2 else items[0]


def _m_range(c: _Ctx) -> Iterator[tuple[str, list[Edit]]]:
    """range(a, b): граница включена / недобрана на единицу, начало сдвинуто (только Python)."""
    if not c.py:
        return
    for i, t in enumerate(c.toks[:-1]):
        if t.kind != "name" or t.text != "range" or c.toks[i + 1].text != "(":
            continue
        stop = _stop_item(c, i + 1)
        if not stop or any(c.toks[j].text in ("if", "or", "and", "not", "lambda", "else") for j in stop):
            continue
        end = c.toks[stop[-1]].end
        yield "range_inclusive", [(end, end, " + 1")]
        yield "range_short", [(end, end, " - 1")]
        closer = c.match[i + 1]
        items = lit._split_items(c.toks, i + 1, closer)  # noqa: SLF001
        if len(items) >= 2 and len(items[0]) == 1 and c.toks[items[0][0]].kind == "num":
            first = c.toks[items[0][0]]
            if first.text.isdigit():
                n = int(first.text)
                yield "range_start", [(first.start, first.end, str(n + 1))]
                if n >= 1:
                    yield "range_start", [(first.start, first.end, str(n - 1))]


def _m_index_shift(c: _Ctx) -> Iterator[tuple[str, list[Edit]]]:
    """Индекс или граница среза сдвинуты на единицу: a[0] → a[1], a[:3] → a[:2], a[:4]."""
    for i, t in enumerate(c.toks):
        if t.kind == "num" and c.frame_kind[i] == "subscript" and t.text.isdigit():
            n = int(t.text)
            yield "index_shift", [(t.start, t.end, str(n + 1))]
            if n >= 1:
                yield "index_shift", [(t.start, t.end, str(n - 1))]


def _iter_end(c: _Ctx, start: int) -> int:
    """Индекс токена, на котором заканчивается выражение-итератор `for x in <expr>` (Python): не включительно."""
    depth, k = 0, start
    while k < len(c.toks):
        tk = c.toks[k]
        if tk.kind == "op" and tk.text in "([{":
            depth += 1
        elif tk.kind == "op" and tk.text in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and (
            (tk.kind == "op" and tk.text in (":", ","))
            or (tk.kind == "name" and tk.text in ("if", "for", "else", "async"))
        ):
            break
        k += 1
    return k


def _m_skip(c: _Ctx) -> Iterator[tuple[str, list[Edit]]]:
    """Пропущен первый или последний элемент перебора."""
    toks = c.toks
    if c.py:
        for i, t in enumerate(toks):
            if t.kind != "name" or t.text != "for":
                continue
            j = next(
                (k for k in range(i + 1, len(toks)) if toks[k].kind == "name" and toks[k].text == "in"
                 and c.depth[k] == c.depth[i]),
                None,
            )
            if j is None or j + 1 >= len(toks):
                continue
            end_idx = _iter_end(c, j + 1)
            if end_idx <= j + 1:
                continue
            a, b = toks[j + 1].start, toks[end_idx - 1].end
            yield "skip_first", [(a, a, "("), (b, b, ")[1:]")]
            yield "skip_last", [(a, a, "("), (b, b, ")[:-1]")]
        return
    for i, t in enumerate(toks[:-1]):
        if t.kind == "name" and t.text == "for" and toks[i + 1].text == "(":
            closer = c.match.get(i + 1)
            of = next((k for k in range(i + 2, closer or 0) if toks[k].kind == "name" and toks[k].text == "of"), None)
            if closer is None or of is None or of + 1 >= closer:
                continue
            a, b = toks[of + 1].start, toks[closer - 1].end
            yield "skip_first", [(a, a, "("), (b, b, ").slice(1)")]
            yield "skip_last", [(a, a, "("), (b, b, ").slice(0, -1)")]
        elif (
            t.kind == "op" and t.text == "."
            and toks[i + 1].kind == "name"
            and toks[i + 1].text in ("map", "filter", "reduce", "forEach", "some", "every", "find", "flatMap")
            and i > 0 and _is_value_end(toks[i - 1])
        ):
            yield "skip_first", [(t.start, t.start, ".slice(1)")]
            yield "skip_last", [(t.start, t.start, ".slice(0, -1)")]


def _py_condition_end(c: _Ctx, start: int, statement: bool) -> int | None:
    """Индекс токена-конца условия: двоеточие инструкции (statement) или `for` / `if` / `else` / закрывающая скобка."""
    depth, k = 0, start
    while k < len(c.toks):
        tk = c.toks[k]
        if tk.kind == "op" and tk.text in "([{":
            depth += 1
        elif tk.kind == "op" and tk.text in ")]}":
            if depth == 0:
                return None if statement else k
            depth -= 1
        elif depth == 0:
            if statement and tk.kind == "op" and tk.text == ":":
                return k
            if not statement and tk.kind == "name" and tk.text in ("for", "if", "else"):
                return k
        k += 1
    return None if statement else k


def _m_negate(c: _Ctx) -> Iterator[tuple[str, list[Edit]]]:
    """Условие отрицано: if cond → if not (cond)."""
    toks = c.toks
    if c.py:
        for i, t in enumerate(toks[:-1]):
            if t.kind != "name" or t.text not in ("if", "elif", "while"):
                continue
            first_on_line = i == 0 or toks[i - 1].line < t.line
            statement = first_on_line and c.depth[i] == 0
            end = _py_condition_end(c, i + 1, statement)
            if end is None or end <= i + 1:
                continue
            a, b = toks[i + 1].start, toks[end - 1].end
            yield "negate", [(a, a, "not ("), (b, b, ")")]
            if t.text != "while":  # «взял все»: фильтр забыт
                yield "no_filter", [(a, b, "True")]
        return
    for i, t in enumerate(toks[:-1]):
        if t.kind == "name" and t.text in ("if", "while") and toks[i + 1].text == "(":
            closer = c.match.get(i + 1)
            if closer is not None and closer > i + 2:
                inner = (toks[i + 1].end, toks[closer].start)
                yield "negate", [(inner[0], inner[0], "!("), (inner[1], inner[1], ")")]
                if t.text == "if":
                    yield "no_filter", [(inner[0], inner[1], "true")]


def _m_overwrite(c: _Ctx) -> Iterator[tuple[str, list[Edit]]]:
    """Перезапись вместо накопления: x += y → x = y."""
    for t in c.toks:
        if t.kind == "op" and t.text == "+=":
            yield "overwrite", [(t.start, t.end, "=")]


def _m_multiplier(c: _Ctx) -> Iterator[tuple[str, list[Edit]]]:
    """«Забыл множитель» и «сложил вместо умножения»."""
    toks = c.toks
    for i, t in enumerate(toks):
        if t.kind != "op" or t.text != "*" or i == 0 or i + 1 >= len(toks) or not _is_value_end(toks[i - 1]):
            continue
        prev, nxt = toks[i - 1], toks[i + 1]
        yield "plus_for_times", [(t.start, t.end, "+")]
        after = toks[i + 2] if i + 2 < len(toks) else None
        if nxt.kind == "num" or (nxt.kind == "name" and not (after and after.text in ("(", ".", "["))):
            yield "forget_multiplier", [(prev.end, nxt.end, "")]
        elif nxt.text == "(" and i + 1 in c.match:
            yield "forget_multiplier", [(prev.end, toks[c.match[i + 1]].end, "")]
        if prev.kind == "num" and (i < 2 or toks[i - 2].text not in (".", "*")):
            yield "forget_multiplier", [(prev.start, nxt.start, "")]


def _m_operators(c: _Ctx) -> Iterator[tuple[str, list[Edit]]]:
    """Перепутан знак (+ ↔ -) и равенство (== ↔ !=)."""
    toks = c.toks
    for i, t in enumerate(toks):
        if t.kind != "op" or i == 0:
            continue
        if t.text in ("+", "-") and _is_value_end(toks[i - 1]) and i + 1 < len(toks):
            yield "plus_minus", [(t.start, t.end, "-" if t.text == "+" else "+")]
        elif t.text == "==" and c.py:
            yield "eq_swap", [(t.start, t.end, "!=")]
        elif t.text == "!=" and c.py:
            yield "eq_swap", [(t.start, t.end, "==")]


def _m_division(c: _Ctx) -> Iterator[tuple[str, list[Edit]]]:
    """// вместо / и наоборот (только Python)."""
    if not c.py:
        return
    for t in c.toks:
        if t.kind == "op" and t.text == "//":
            yield "true_division", [(t.start, t.end, "/")]
        elif t.kind == "op" and t.text == "/":
            yield "floor_division", [(t.start, t.end, "//")]


_SWAPS_PY = {"min": "max", "max": "min", "lower": "upper", "upper": "lower"}
_SWAPS_JS = {"min": "max", "max": "min", "toLowerCase": "toUpperCase", "toUpperCase": "toLowerCase"}


def _m_name_swaps(c: _Ctx) -> Iterator[tuple[str, list[Edit]]]:
    """min ↔ max и регистр строки (lower ↔ upper)."""
    swaps = _SWAPS_PY if c.py else _SWAPS_JS
    for i, t in enumerate(c.toks[:-1]):
        if t.kind == "name" and t.text in swaps and c.toks[i + 1].text == "(":
            label = "minmax" if t.text in ("min", "max") else "case_swap"
            if label == "case_swap" and not (i > 0 and c.toks[i - 1].text == "."):
                continue
            yield label, [(t.start, t.end, swaps[t.text])]


def _m_copy(c: _Ctx) -> Iterator[tuple[str, list[Edit]]]:
    """Забыта копия: x.copy() → x (Python); в JS — x.slice() → x."""
    toks = c.toks
    name = "copy" if c.py else "slice"
    for i in range(1, len(toks) - 2):
        if toks[i].text == "." and toks[i + 1].text == name and toks[i + 2].text == "(":
            closer = c.match.get(i + 2)
            if closer == i + 3:  # вызов без аргументов
                yield "no_copy", [(toks[i].start, toks[closer].end, "")]


def _m_sort_direction(c: _Ctx) -> Iterator[tuple[str, list[Edit]]]:
    """reverse=True ↔ reverse=False (Python)."""
    if not c.py:
        return
    toks = c.toks
    for i in range(len(toks) - 2):
        if toks[i].text == "reverse" and toks[i + 1].text == "=" and toks[i + 2].text in ("True", "False"):
            flipped = "False" if toks[i + 2].text == "True" else "True"
            yield "sort_direction", [(toks[i + 2].start, toks[i + 2].end, flipped)]


def _m_js_specific(c: _Ctx) -> Iterator[tuple[str, list[Edit]]]:
    """Позднее связывание (let → var) и нестрогое равенство (JavaScript)."""
    if c.py:
        return
    for t in c.toks:
        if t.kind == "name" and t.text == "let":
            yield "late_binding", [(t.start, t.end, "var")]
        elif t.kind == "op" and t.text == "===":
            yield "loose_equality", [(t.start, t.end, "==")]
        elif t.kind == "op" and t.text == "==":
            yield "loose_equality", [(t.start, t.end, "===")]


_CODE_MUTATORS: tuple[Mutator, ...] = (
    _m_boundary, _m_range, _m_index_shift, _m_skip, _m_negate, _m_overwrite, _m_multiplier, _m_operators,
    _m_division, _m_name_swaps, _m_copy, _m_sort_direction, _m_js_specific,
)


# ----------------------------------------------------------------------------- мутаторы SQL


def _mask_sql(q: str) -> str:
    """Запрос с обезличенными строковыми литералами: позиции сохраняются, текст внутри кавычек не ищется."""
    return re.sub(r"'(?:[^']|'')*'", lambda m: " " * len(m.group(0)), q)


_SQL_CMP = re.compile(r"<=|>=|<>|!=|<|>|=")
_RANK_FUNCS = ("RANK", "DENSE_RANK", "ROW_NUMBER")


def _sql_mutations(query: str, null_columns: Collection[str]) -> Iterator[tuple[str, list[Edit]]]:
    q = _mask_sql(query)
    for m in _SQL_CMP.finditer(q):
        op = m.group(0)
        if op in _BOUNDARY:
            yield "boundary", [(m.start(), m.end(), _BOUNDARY[op])]
        elif op == "=":
            yield "negate", [(m.start(), m.end(), "!=")]
        else:
            yield "negate", [(m.start(), m.end(), "=")]
    for m in re.finditer(r"\b(DESC|ASC)\b", q, re.I):
        yield "sort_direction", [(m.start(), m.end(), "ASC" if m.group(1).upper() == "DESC" else "DESC")]
    for m in re.finditer(r"\b(LIMIT|OFFSET)\s+(\d+)", q, re.I):
        n = int(m.group(2))
        yield "limit_shift", [(m.start(2), m.end(2), str(n + 1))]
        if n >= 1:
            yield "limit_shift", [(m.start(2), m.end(2), str(n - 1))]
    for m in re.finditer(r"\b(LEFT\s+(?:OUTER\s+)?JOIN|INNER\s+JOIN|JOIN)\b", q, re.I):
        is_left = m.group(1).upper().startswith("LEFT")
        yield "join_kind", [(m.start(), m.end(), "INNER JOIN" if is_left else "LEFT JOIN")]
    for m in re.finditer(r"\b(AND|OR)\b", q, re.I):
        yield "and_or", [(m.start(), m.end(), "OR" if m.group(1).upper() == "AND" else "AND")]
    for m in re.finditer(r"\b(AVG|SUM|MAX|MIN)\s*\(", q, re.I):
        swap = {"AVG": "SUM", "SUM": "AVG", "MAX": "MIN", "MIN": "MAX"}[m.group(1).upper()]
        yield "aggregate_swap", [(m.start(1), m.end(1), swap)]
    for m in re.finditer(r"\bAVG\s*\(\s*(\w+)\s*\)", q, re.I):
        yield "null_as_zero", [(m.start(1), m.end(1), f"COALESCE({m.group(1)}, 0)")]
    for m in re.finditer(r"\bCOUNT\s*\(\s*(\w+)\s*\)", q, re.I):
        if m.group(1).upper() != "DISTINCT":
            yield "count_star", [(m.start(1), m.end(1), "*")]
    for m in re.finditer(r"\bCOUNT\s*\(\s*\*\s*\)", q, re.I):
        for col in sorted(null_columns):
            yield "null_count", [(m.start() + q[m.start() :].index("*"), m.start() + q[m.start() :].index("*") + 1, col)]
    for m in re.finditer(r"\bDISTINCT\s+", q, re.I):
        yield "no_distinct", [(m.start(), m.end(), "")]
    for m in re.finditer(r"\bUNION\s+ALL\b|\bUNION\b", q, re.I):
        yield "union_all", [(m.start(), m.end(), "UNION" if "ALL" in m.group(0).upper() else "UNION ALL")]
    for m in re.finditer(r"\bNOT\s+IN\b", q, re.I):
        yield "negate", [(m.start(), m.end(), "IN")]
    for m in re.finditer(r"(?<![\w.])(\d+)(?![\w.])", q):
        before = q[: m.start()].rstrip().upper()
        if before.endswith(("LIMIT", "OFFSET")):
            continue
        n = int(m.group(1))
        yield "threshold_shift", [(m.start(), m.end(), str(n + 1))]
        if n >= 1:
            yield "threshold_shift", [(m.start(), m.end(), str(n - 1))]
    for m in re.finditer(r"\b(RANK|DENSE_RANK|ROW_NUMBER)\s*\(", q, re.I):
        cur = m.group(1).upper()
        for other in _RANK_FUNCS:
            if other != cur:
                yield "rank_fn", [(m.start(1), m.end(1), other)]


# ----------------------------------------------------------------------------- набор мутантов


def _pick(mutants: list[ErrMutant], limit: int) -> list[ErrMutant]:
    """Не больше limit мутантов; при превышении — по кругу по типам ошибок, порядок внутри типа сохраняется."""
    if len(mutants) <= limit:
        return mutants
    groups: dict[str, list[ErrMutant]] = {}
    for m in mutants:
        groups.setdefault(m.label, []).append(m)
    out: list[ErrMutant] = []
    depth = 0
    while len(out) < limit:
        layer = [g[depth] for g in groups.values() if depth < len(g)]
        if not layer:
            break
        out.extend(layer[: limit - len(out)])
        depth += 1
    return out


def limit_for(language: str, params: ErrParams) -> int:
    return {"python": params.max_python, "javascript": params.max_javascript, "sql": params.max_sql}.get(language, 0)


def error_mutants(
    code: str, language: str, params: ErrParams, *, null_columns: Collection[str] = ()
) -> list[ErrMutant]:
    """Мутанты типичных ошибок показанного кода (python / javascript) или запроса (sql). Порядок детерминирован."""
    edits: list[tuple[str, list[Edit]]] = []
    if language == "sql":
        edits = list(_sql_mutations(code, null_columns))
    else:
        ctx = _context(code, language)
        if ctx is None:
            return []
        for mut in _CODE_MUTATORS:
            edits.extend(mut(ctx))
    seen: set[str] = {code}
    out: list[ErrMutant] = []
    for label, eds in edits:
        src = _apply(code, eds)
        if src in seen:
            continue
        seen.add(src)
        out.append(ErrMutant(label, src))
    return _pick(out, limit_for(language, params))


# ----------------------------------------------------------------------------- ключи мутантов

_KEY_CACHE: dict[tuple, dict[str, str]] = {}
_KEY_CACHE_MAX = 20000


def _sql_null_columns(tables: Sequence[SqlTable]) -> list[str]:
    return sorted({col for t in tables for j, col in enumerate(t.columns) if any(r[j] is None for r in t.rows)})


def error_keys(
    fam: Family,
    language: str,
    code: str,
    params: ErrParams,
    *,
    tables: Sequence[SqlTable] = (),
    projection: str = "",
    exclude: Collection[str] = (),
    timeout: float = 4.0,
) -> dict[str, str]:
    """K_err: {канонический ключ мутанта: тип ошибки}. Ключ получен исполнением; не исполнившиеся мутанты пропускаются.

    exclude — ключи, которые ошибкой не считаются (ключ самого задания: мутант, не влияющий на результат).
    fam нужен только ради fam.normalize: ключи мутантов нормализуются так же, как ответы кандидата.
    """
    cache_key = (
        id(type(fam)), language, code, tuple((t.name, t.columns, t.rows) for t in tables), projection,
        tuple(sorted(exclude)), params,
    )
    hit = _KEY_CACHE.get(cache_key)
    if hit is not None:
        return dict(hit)
    normalize = fam.normalize
    null_cols = _sql_null_columns(tables) if language == "sql" else ()
    mutants = error_mutants(code, language, params, null_columns=null_cols)
    skip = set(exclude)
    keys: dict[str, str] = {}
    if language == "sql":
        for m in mutants:
            ok, rows, _ = run_query(list(tables), m.source)
            if not ok:
                continue
            k = canonical(normalize(project_rows(rows, projection)))
            if k and k not in skip and k not in keys:
                keys[k] = m.label
    else:
        outcomes: list[ExecOutcome] = run_many(
            language,
            [m.source for m in mutants],
            timeout=min(timeout, params.js_timeout_s) if language == "javascript" else timeout,
            batch_chars=params.batch_chars,
            batch_timeout=params.batch_timeout_s,
            mutant_timeout=params.mutant_timeout_s,
        )
        for m, o in zip(mutants, outcomes, strict=True):
            if not o.ok:
                continue
            k = canonical(normalize(o.output))
            if k and k not in skip and k not in keys:
                keys[k] = m.label
    if len(_KEY_CACHE) >= _KEY_CACHE_MAX:
        _KEY_CACHE.clear()
    _KEY_CACHE[cache_key] = dict(keys)
    return keys
