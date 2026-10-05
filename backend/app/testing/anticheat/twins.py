"""«Двойник в буфере» (clipboard twin): генерация X′ по показанному заданию X.

Идея (spec-antiassist §2.1): на экране всегда истинный X, а в буфер при копировании кладётся почти такой же
текст X′, в котором изменён РОВНО ОДИН литерал данных. Ключ X′ вычисляется исполнением (run_python / run_js /
run_sql) так же, как основной ключ, — никаких «скрытых инструкций» модели нет: модель честно решает другую
задачу и возвращает ответ, который по экрану получить нельзя.

Для любого семейства с исполняемым кодом (predict_output на python/javascript, sql_result на sqlite) двойник
строится ОБЩИМ мутатором, без участия автора семейства:
  1. показанный код/таблица исполняются, результат нормализуется `Family.normalize` и должен совпасть с ключом
     экземпляра (иначе ключ не воспроизводится исполнением и двойник строить нельзя);
  2. из кода (или из таблицы) выбираются литералы данных (literals.py), детерминированно от seed экземпляра;
  3. для литерала подбирается новое значение; X′ исполняется; кандидат проходит гейты (spec §2.1):
     ключи различны · ключ X′ не равен ни одному известному дистрактору/заблуждению · правка малозаметна
     (длина ±1, нет путаемых глифов) · ровно один токен · токен не в «переводимой» строке · (на уровне пула:
     ключ X′ не совпадает с ключами других экземпляров);
  4. неудачные кандидаты отбрасываются, перебор идёт дальше (доля отклонённых — метрика качества каталога).
Нет безопасного двойника — возвращается None с кодом причины (TwinResult.reason).
"""

from __future__ import annotations

import dataclasses
import hashlib
import random
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.testing.anticheat import literals as lit
from app.testing.anticheat.config import AntiCheatConfig, load_anticheat_config
from app.testing.anticheat.err_mutants import error_keys
from app.testing.anticheat.executor import (
    SqlTable,
    find_projection,
    project_rows,
    run_code,
    run_query,
    tables_from_rendered,
)
from app.testing.bank.base import Family, Instance, Rendered, canonical

# ----------------------------------------------------------------------------- модель данных


@dataclass(frozen=True)
class ChangedLiteral:
    """Изменённый литерал. line (с 1) и col (с 0) — в shown_text; offset — абсолютный символ в нём."""

    line: int
    col: int
    from_: str
    to: str
    role: str
    offset: int
    block: str  # "code" | "table:<имя>"
    block_line: int  # то же внутри блока (кода или таблицы), с 1
    block_col: int
    ident: int = 0  # устойчивый идентификатор литерала внутри задания (для исключения при подборе других правок)
    column: str = ""  # для ячейки таблицы: имя столбца
    row: int = -1  # и номер строки (с 0)

    def __getitem__(self, key: str) -> Any:
        """Доступ как к словарю {line, col, from, to, …}: twin.changed_literal["from"]."""
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "col": self.col,
            "from": self.from_,
            "to": self.to,
            "role": self.role,
            "offset": self.offset,
            "block": self.block,
            "block_line": self.block_line,
            "block_col": self.block_col,
            "ident": self.ident,
            "column": self.column,
            "row": self.row,
        }


@dataclass(frozen=True)
class Twin:
    """Двойник экземпляра. Серверный объект: twin_key на клиент не уходит (client_clip() его не содержит)."""

    family_id: str
    seed: int
    variant_code: str
    mutation_class: str  # data_literal | table_value
    role: str  # element | value | multiplier | assign | kwarg | arg | arith | default | other | string_* | cell
    tier: int
    shown_text: str
    clipboard_text: str
    changed_literal: ChangedLiteral
    key: str
    key_hash: str
    twin_key: str
    twin_key_hash: str
    honest_collision_estimate: float
    twin_rendered: Rendered
    gates: dict[str, bool]
    attempts: int
    rejects: dict[str, int]
    executor: str
    version: str
    # K_err(X′) без пересечения с {key(X)} ∪ K_err(X) ∪ distractors(X): ответы, которые даёт типичная ошибка на ТЕКСТЕ ИЗ БУФЕРА
    # и которых нет среди ошибок на экране (killer-features §3.4, п. 3). Только серверный контекст, вес 0.
    err_trace_keys: tuple[str, ...] = ()
    k_err_size: int = 0  # сколько различных ключей типичных ошибок на экране (K_err(X)) проверено гейтом 3

    @property
    def mutation_subclass(self) -> str:
        """Класс для правила «не больше одного попадания на класс»: тип мутации + роль литерала."""
        return f"{self.mutation_class}:{self.role}"

    # Имена из spec-antiassist §7: Twin(clip_text, clip_map, key, mutation_class, twin_id)
    @property
    def clip_text(self) -> str:
        return self.clipboard_text

    @property
    def clip_map(self) -> list[dict[str, Any]]:
        return self.client_clip()["map"]

    @property
    def twin_id(self) -> str:
        """Устойчивый идентификатор двойника: семейство, экземпляр, позиция и новое значение."""
        c = self.changed_literal
        return hashlib.sha256(f"{self.family_id}|{self.seed}|{c.offset}|{c.to}|{self.version}".encode()).hexdigest()[:12]

    @property
    def clipboard_span(self) -> tuple[int, int]:
        c = self.changed_literal
        return c.offset, c.offset + len(c.to)

    @property
    def shown_span(self) -> tuple[int, int]:
        c = self.changed_literal
        return c.offset, c.offset + len(c.from_)

    def mutation_json(self) -> dict[str, Any]:
        """Для ItemInstance.twin_mutation (jsonb): класс, позиция, токены. Без ключей."""
        return {
            "class": self.mutation_class,
            "role": self.role,
            "tier": self.tier,
            "literal": self.changed_literal.to_dict(),
            "gates": dict(self.gates),
            "executor": self.executor,
            "version": self.version,
        }

    def client_clip(self, render_id: str | None = None) -> dict[str, Any]:
        """Данные для обработчика copy на клиенте (instance.rendered["clip"]): текст X′ и карта подмены.

        Ключ двойника сюда НЕ попадает (spec §7 «API»). Карта — диапазоны токена X↔X′ в shown_text/clip,
        чтобы подмена учитывала выделение: подставляется только тот фрагмент, который покрывает токен.
        """
        c = self.changed_literal
        return {
            "render_id": render_id,
            "variant_code": self.variant_code,
            "text": self.clipboard_text,
            "map": [
                {
                    "shown": [c.offset, c.offset + len(c.from_)],
                    "clip": [c.offset, c.offset + len(c.to)],
                    "from": c.from_,
                    "to": c.to,
                }
            ],
            "version": self.version,
        }

    def to_dict(self, *, include_keys: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "family_id": self.family_id,
            "seed": self.seed,
            "variant_code": self.variant_code,
            "mutation_class": self.mutation_class,
            "mutation_subclass": self.mutation_subclass,
            "role": self.role,
            "tier": self.tier,
            "shown_text": self.shown_text,
            "clipboard_text": self.clipboard_text,
            "changed_literal": self.changed_literal.to_dict(),
            "honest_collision_estimate": self.honest_collision_estimate,
            "gates": dict(self.gates),
            "attempts": self.attempts,
            "executor": self.executor,
            "version": self.version,
        }
        if include_keys:
            out.update(
                key=self.key, key_hash=self.key_hash, twin_key=self.twin_key, twin_key_hash=self.twin_key_hash,
                err_trace_keys=list(self.err_trace_keys),
            )
        return out


@dataclass(frozen=True)
class TwinResult:
    twin: Twin | None
    reason: str  # ok | format_unsupported | answer_mode_not_text | language_not_executable | no_code | ...
    attempts: int = 0
    rejects: dict[str, int] = field(default_factory=dict)
    executor: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.twin is not None


def reason_text(reason: str, cfg: AntiCheatConfig | None = None) -> str:
    cfg = cfg or load_anticheat_config()
    try:
        return str(cfg.raw_text(f"twin_reasons_ru.{reason}"))
    except KeyError:
        return reason


# ----------------------------------------------------------------------------- составной текст (экран / буфер)


@dataclass(frozen=True)
class ComposedText:
    """Плоский текст задания, каким он попадёт в буфер: условие, таблицы, код. Смещения блоков известны."""

    text: str
    blocks: dict[str, tuple[int, int]]
    cells: dict[tuple[str, int, int], tuple[int, int]]  # (таблица, строка, столбец) → [начало, конец) в тексте


def _table_block(t: SqlTable) -> tuple[str, dict[tuple[int, int], tuple[int, int]]]:
    lines = [f"Таблица {t.name}:", " | ".join(t.columns)]
    cells: dict[tuple[int, int], tuple[int, int]] = {}
    pos = len(lines[0]) + 1 + len(lines[1]) + 1
    for i, row in enumerate(t.rows):
        parts = ["NULL" if v is None else str(v) for v in row]
        col_pos = pos
        for j, p in enumerate(parts):
            cells[(i, j)] = (col_pos, col_pos + len(p))
            col_pos += len(p) + 3  # « | »
        line = " | ".join(parts)
        lines.append(line)
        pos += len(line) + 1
    return "\n".join(lines), cells


def compose_text(rendered: Rendered, tables: list[SqlTable] | None = None) -> ComposedText:
    """Условие + таблицы + код. Один и тот же порядок для показанной и скопированной версии."""
    tables = tables if tables is not None else tables_from_rendered(rendered.table)
    parts: list[tuple[str, str]] = [("prompt", rendered.prompt)]
    rel_cells: dict[str, dict[tuple[int, int], tuple[int, int]]] = {}
    for t in tables:
        text, cells = _table_block(t)
        parts.append((f"table:{t.name}", text))
        rel_cells[t.name] = cells
    if rendered.code:
        parts.append(("code", rendered.code))
    out: list[str] = []
    blocks: dict[str, tuple[int, int]] = {}
    pos = 0
    for name, text in parts:
        if out:
            out.append("\n\n")
            pos += 2
        blocks[name] = (pos, pos + len(text))
        out.append(text)
        pos += len(text)
    cell_abs: dict[tuple[str, int, int], tuple[int, int]] = {}
    for tname, cells in rel_cells.items():
        base = blocks[f"table:{tname}"][0]
        for (i, j), (a, b) in cells.items():
            cell_abs[(tname, i, j)] = (base + a, base + b)
    return ComposedText("".join(out), blocks, cell_abs)


def _line_col(text: str, offset: int, base: int = 0) -> tuple[int, int]:
    seg = text[base:offset]
    return seg.count("\n") + 1, offset - base - (seg.rfind("\n") + 1)


# ----------------------------------------------------------------------------- честная вероятность совпадения


def honest_collision_estimate(
    mutation_class: str, tier: int, rejected: int, tried: int, cfg: AntiCheatConfig | None = None
) -> float:
    """P(честный кандидат, не видя X′, получит ровно key(X′)). Модель, а не измерение (spec §2.1: ε не откалибрована).

    ε = ε_класса · множитель_яруса · (1 + доля_плотных_отклонений). «Плотное» отклонение — кандидат, ключ которого
    совпал с известным заблуждением или с ключом соседнего экземпляра: если таких много, область частых
    ошибок вокруг этого задания плотная и риск случайного совпадения выше. Литерал «прочей» роли (ярус 3) и
    значение с «ничьёй» (ярус 4) стоят ближе к границе, поэтому множитель яруса больше.
    """
    cfg = cfg or load_anticheat_config()
    e = cfg.eps
    base = e.default.get(mutation_class, max(e.default.values()))
    ratio = rejected / tried if tried else 0.0
    est = base * e.tier_multiplier.get(tier, 1.0) * (1.0 + e.reject_inflation * ratio)
    return min(e.cap, max(e.floor, est))


# ----------------------------------------------------------------------------- кандидаты


@dataclass
class _Cand:
    role: str
    tier: int
    mclass: str
    old: str
    start: int  # смещение в коде (для SQL — не используется)
    propose: Callable[[random.Random], list[str]]
    cell: lit.CellCandidate | None = None
    order_key: int = 0  # идентификатор для исключения (verification: не трогать уже использованный литерал)


def _code_candidates(code: str, language: str, cfg: AntiCheatConfig) -> list[_Cand] | None:
    params = cfg.twins
    tokens = lit.tokenize_code(code, language)
    if tokens is None:
        return None
    an = lit.analyze_code(tokens, params)
    out: list[_Cand] = []
    for n in an.numbers:
        pivots = an.pivots + [v for j, v in an.soft_pivots if j != n.index]

        def propose(
            rng: random.Random, n: lit.NumLiteral = n, pivots: list[float] = pivots, an: lit.Analysis = an
        ) -> list[str]:
            return lit.propose_number(
                n.spec, pivots=pivots, mods=an.mods, existing_abs=an.existing_abs, rng=rng, params=params
            )

        out.append(_Cand(n.role, n.tier, "data_literal", n.tok.text, n.tok.start, propose, order_key=n.tok.start))
    for s in an.strings:
        if s.role == "string_digits":

            def propose_digits(rng: random.Random, s: lit.StrLiteral = s) -> list[str]:
                return lit.propose_digit_run(s.text, rng, params)

            out.append(
                _Cand(s.role, params.role_tier.get(s.role, 3), "data_literal", s.text, s.start, propose_digits,
                      order_key=s.start)
            )
        else:

            def propose_letter(rng: random.Random, s: lit.StrLiteral = s, an: lit.Analysis = an) -> list[str]:
                return lit.propose_label(s.text, an.labels, rng, params, an.label_pool if s.swap else None)

            out.append(
                _Cand(s.role, params.role_tier.get(s.role, 3), "data_literal", s.text, s.start, propose_letter,
                      order_key=s.start)
            )
    return out


def _sql_candidates(tables: list[SqlTable], query: str, cfg: AntiCheatConfig) -> list[_Cand]:
    params = cfg.twins
    cells, pivots = lit.sql_cell_candidates(tables, query, params)
    relevant = _relevant_rows(tables, query)
    by_name = {t.name: t for t in tables}
    index = {t.name: k for k, t in enumerate(tables)}
    out: list[_Cand] = []
    for c in cells:
        t = by_name[c.table]
        column = {float(r[c.col]) for r in t.rows if isinstance(r[c.col], int | float) and r[c.col] is not None}
        existing = {round(v, 9) for v in column}

        def propose(
            rng: random.Random, c: lit.CellCandidate = c, existing: set[float] = existing, pivots: list[float] = pivots
        ) -> list[str]:
            return lit.propose_number(
                c.spec, pivots=pivots, mods=[], existing_abs=existing, rng=rng, params=params
            )

        # строка, без которой результат запроса тот же, на ответ не влияет: её значения пробуем в последнюю очередь
        tier = c.tier if (c.table, c.row) in relevant else c.tier + 1
        out.append(
            _Cand("cell", tier, "table_value", c.spec.text, 0, propose, cell=c,
                  order_key=index[c.table] * 10**6 + c.row * 10**3 + c.col)
        )
    return out


def _relevant_rows(tables: list[SqlTable], query: str) -> set[tuple[str, int]]:
    """Строки, удаление которых меняет результат запроса (дёшево: sqlite в памяти). Если запрос не выполнился — пусто."""
    ok, base, _ = run_query(tables, query)
    if not ok:
        return set()
    out: set[tuple[str, int]] = set()
    for ti, t in enumerate(tables):
        for i in range(len(t.rows)):
            cut = SqlTable(t.name, t.columns, t.rows[:i] + t.rows[i + 1 :])
            ok2, rows, _ = run_query([*tables[:ti], cut, *tables[ti + 1 :]], query)
            if not ok2 or rows != base:
                out.add((t.name, i))
    return out


def _replace_cell(tables: list[SqlTable], c: lit.CellCandidate, new_text: str, is_int: bool) -> list[SqlTable]:
    new_val: Any = int(new_text) if is_int else float(new_text)
    out: list[SqlTable] = []
    for t in tables:
        if t.name != c.table:
            out.append(t)
            continue
        rows = [list(r) for r in t.rows]
        rows[c.row][c.col] = new_val
        out.append(SqlTable(t.name, t.columns, tuple(tuple(r) for r in rows)))
    return out


def _rendered_with_tables(r: Rendered, tables: list[SqlTable]) -> Rendered:
    payload = {"name": tables[0].name, "columns": list(tables[0].columns), "rows": [list(x) for x in tables[0].rows]}
    if len(tables) > 1 or (r.table and "tables" in r.table):
        payload["tables"] = [
            {"name": t.name, "columns": list(t.columns), "rows": [list(x) for x in t.rows]} for t in tables
        ]
    return dataclasses.replace(r, table=payload)


# ----------------------------------------------------------------------------- построение двойника


def hash_answer(family_id: str, key: Any) -> str:
    """Та же формула, что Instance.key_hash и detectors.answer_hash: sha256(«семейство|канонический ответ»)."""
    return hashlib.sha256(f"{family_id}|{canonical(key)}".encode()).hexdigest()


_hash_key = hash_answer


def _messy_key(text: str, max_decimals: int) -> bool:
    return re.search(rf"\d+\.\d{{{max_decimals + 1},}}", text) is not None


def structural_reason(fam: Family, inst: Instance, cfg: AntiCheatConfig) -> str | None:
    """Код причины, если двойник невозможен по самой форме задания; None — форма подходит."""
    r = inst.rendered
    if fam.format not in cfg.twins.supported_formats:
        return "format_unsupported"
    if r.answer_mode != "text":
        # Выбор из вариантов: ключ X′ либо совпадёт с вариантом-дистрактором (гейт), либо его нет среди вариантов.
        return "answer_mode_not_text"
    if r.language not in cfg.twins.eligible_languages:
        return "language_not_executable"
    if not r.code:
        return "no_code"
    if r.language == "sql" and not r.table:
        return "no_code"
    return None


def try_make_twin(
    fam: Family,
    inst: Instance,
    *,
    seed: int | None = None,
    cfg: AntiCheatConfig | None = None,
    pool_key_hashes: Iterable[str] | None = None,
    strict: bool = True,
    avoid: Iterable[int] = (),
    salt: str | None = None,
) -> TwinResult:
    """Построить двойник экземпляра; при неудаче — TwinResult(twin=None, reason=…).

    strict=False — режим «вариант для подтверждения» (verification.py): без гейтов «не дистрактор»
    и «малозаметность», с исключением уже использованных литералов (avoid) и другой солью rng.
    """
    cfg = cfg or load_anticheat_config()
    p = cfg.twins
    seed = inst.seed if seed is None else seed
    why = structural_reason(fam, inst, cfg)
    if why:
        return TwinResult(None, why)
    r = inst.rendered
    language = r.language or ""
    code = r.code or ""
    rng_base = f"{fam.id}#{seed}#{salt or ('twin' if strict else 'variant')}"
    pool = set(pool_key_hashes or ())
    avoid_set = set(avoid)

    # --- 1. ключ показанного задания должен воспроизводиться исполнением
    tables: list[SqlTable] = []
    projection = ""
    executor = ""
    if language == "sql":
        tables = tables_from_rendered(r.table)
        ok, rows, err = run_query(tables, code)
        if not ok:
            return TwinResult(None, "executor_failed", detail=err)
        found = find_projection(fam, inst, rows)
        if found is None:
            return TwinResult(None, "key_not_reproducible", detail="sql")
        projection, executor = found, "run_sql"
        cands = _sql_candidates(tables, code, cfg)
    else:
        base = run_code(language, code, p.exec_timeout_s)
        if not base.ok:
            return TwinResult(None, "executor_failed", executor=base.via, detail=base.error)
        executor = base.via
        if canonical(fam.normalize(base.output)) != canonical(inst.key):
            return TwinResult(None, "key_not_reproducible", executor=executor)
        found_c = _code_candidates(code, language, cfg)
        if found_c is None:
            return TwinResult(None, "executor_failed", executor=executor, detail="tokenize")
        cands = found_c

    # классы мутаций, включённые в MVP (config: twins.classes_mvp), и литералы, которые просили не трогать
    cands = [c for c in cands if c.order_key not in avoid_set and c.mclass in p.classes_mvp]
    if not cands:
        return TwinResult(None, "no_candidates", executor=executor)

    # --- 2. детерминированный порядок: ярус → случайный порядок внутри яруса (rng от seed экземпляра)
    order_rng = random.Random(rng_base + "#order")
    keyed = [(c.tier, order_rng.random(), i) for i, c in enumerate(cands)]
    keyed.sort()
    ordered = [cands[i] for _, _, i in keyed][: p.max_literals]

    max_attempts = p.max_attempts_sql if language == "sql" else p.max_attempts
    max_values = p.max_values_per_literal_sql if language == "sql" else p.max_values_per_literal
    shown = compose_text(r, tables if language == "sql" else [])
    key_norm = canonical(inst.key)
    distractors = set(inst.distractors)
    rejects: Counter[str] = Counter()
    attempts = 0
    use_err = strict and p.err.enabled  # гейт 3 нужен только настоящим двойникам, не вариантам для подтверждения
    err_cache: dict[str, dict[str, str]] = {}

    def kerr_shown() -> dict[str, str]:
        """K_err(X): ключи типичных ошибок на показанном задании (исполнением, лениво: нужен лишь прошедшему дешёвые гейты)."""
        if "x" not in err_cache:
            err_cache["x"] = error_keys(
                fam, language, code, p.err, tables=tables, projection=projection, exclude={key_norm},
                timeout=p.exec_timeout_s,
            )
        return err_cache["x"]

    for ci, cand in enumerate(ordered):
        proposals = cand.propose(random.Random(f"{rng_base}#{ci}"))[:max_values]
        if not proposals:
            rejects["no_values"] += 1
            continue
        for new_text in proposals:
            if attempts >= max_attempts:
                break
            attempts += 1
            built = _build_variant(r, code, language, tables, cand, new_text)
            twin_rendered, twin_code, twin_tables = built
            # исполнение X′
            if language == "sql":
                ok, rows, _ = run_query(twin_tables, code)
                if not ok:
                    rejects["exec_failed"] += 1
                    continue
                raw = project_rows(rows, projection)
            else:
                out = run_code(language, twin_code, p.exec_timeout_s)
                if not out.ok:
                    rejects["exec_failed"] += 1
                    continue
                raw = out.output
            twin_key = fam.normalize(raw)
            tk = canonical(twin_key)
            if not tk:
                rejects["empty_key"] += 1
                continue
            if tk == key_norm:
                rejects["same_key"] += 1
                continue
            if _messy_key(tk, p.max_key_decimals) and not _messy_key(key_norm, p.max_key_decimals):
                rejects["messy_key"] += 1  # длинная дробь: чат-бот округлит иначе, след не опознать
                continue
            if strict and tk in distractors:
                rejects["in_distractors"] += 1
                continue
            if _hash_key(fam.id, twin_key) in pool:
                rejects["pool_collision"] += 1
                continue
            # --- тексты и гейты правки
            clip = compose_text(twin_rendered, twin_tables if language == "sql" else [])
            off = _locate(shown, cand, language)
            gates = _edit_gates(shown.text, clip.text, off, cand.old, new_text, cfg)
            if not gates["g5_exactly_one_token"] or (strict and not all(gates.values())):
                rejects["edit_gate"] += 1
                continue
            if use_err and tk in kerr_shown():  # гейт 3: ключ двойника — не ключ типичной ошибки
                rejects["in_k_err"] += 1
                continue
            line, col = _line_col(shown.text, off)
            blk_name = "code" if cand.cell is None else f"table:{cand.cell.table}"
            b_start = shown.blocks[blk_name][0]
            b_line, b_col = _line_col(shown.text, off, b_start)
            changed = ChangedLiteral(
                line, col, cand.old, new_text, cand.role, off, blk_name, b_line, b_col, cand.order_key,
                cand.cell.column if cand.cell else "", cand.cell.row if cand.cell else -1,
            )
            # подмены, упавшие на область ошибок (заблуждения, ключи соседей, типичные ошибки)
            dense = rejects["in_distractors"] + rejects["pool_collision"] + rejects["in_k_err"]
            est = honest_collision_estimate(cand.mclass, cand.tier, dense, attempts, cfg)
            err_trace: tuple[str, ...] = ()
            if use_err:
                on_copy = error_keys(
                    fam, language, twin_code, p.err, tables=twin_tables, projection=projection, exclude={tk},
                    timeout=p.exec_timeout_s,
                )
                known = {key_norm, *kerr_shown(), *distractors}
                err_trace = tuple(sorted(k for k in on_copy if k not in known))
            twin = Twin(
                family_id=fam.id,
                seed=inst.seed,
                variant_code=inst.variant_code,
                mutation_class=cand.mclass,
                role=cand.role,
                tier=cand.tier,
                shown_text=shown.text,
                clipboard_text=clip.text,
                changed_literal=changed,
                key=key_norm,
                key_hash=_hash_key(fam.id, inst.key),
                twin_key=tk,
                twin_key_hash=_hash_key(fam.id, twin_key),
                honest_collision_estimate=est,
                twin_rendered=twin_rendered,
                gates={
                    "g1_keys_differ": True,
                    "g2_not_in_distractors": tk not in distractors,
                    "g3_not_in_k_err": (tk not in kerr_shown()) if use_err else True,
                    **gates,
                },
                attempts=attempts,
                rejects=dict(rejects),
                executor=executor,
                version=p.version,
                err_trace_keys=err_trace,
                k_err_size=len(kerr_shown()) if use_err else 0,
            )
            return TwinResult(twin, "ok", attempts, dict(rejects), executor)
        if attempts >= max_attempts:
            break
    return TwinResult(None, "all_candidates_rejected", attempts, dict(rejects), executor)


def _build_variant(
    r: Rendered, code: str, language: str, tables: list[SqlTable], cand: _Cand, new_text: str
) -> tuple[Rendered, str, list[SqlTable]]:
    if cand.cell is not None:
        new_tables = _replace_cell(tables, cand.cell, new_text, cand.cell.spec.is_int)
        return _rendered_with_tables(r, new_tables), code, new_tables
    new_code = code[: cand.start] + new_text + code[cand.start + len(cand.old) :]
    return dataclasses.replace(r, code=new_code), new_code, tables


def _locate(shown: ComposedText, cand: _Cand, language: str) -> int:
    """Смещение изменяемого токена в составном тексте."""
    if cand.cell is not None:
        c = cand.cell
        return shown.cells[(c.table, c.row, c.col)][0]
    return shown.blocks["code"][0] + cand.start


def _edit_gates(shown: str, clip: str, off: int, old: str, new: str, cfg: AntiCheatConfig) -> dict[str, bool]:
    """Гейты правки: ровно один токен, длина ±1, не путаемые глифы, токен не в переводимой строке."""
    one_token = (
        shown[off : off + len(old)] == old
        and clip[off : off + len(new)] == new
        and shown[:off] == clip[:off]
        and shown[off + len(old) :] == clip[off + len(new) :]
    )
    return {
        "g5_exactly_one_token": one_token,
        "g6_no_confusable_glyphs": lit.glyph_ok(old, new, cfg.twins.confusable_pairs),
        "g_length_delta": abs(len(new) - len(old)) <= cfg.twins.max_length_delta,
        # Строки-кандидаты отобраны literals.py: без пробелов, кириллицы и длиннее max_len — это код/идентификатор.
        "g7_not_translatable_string": True,
    }


def make_twin(
    fam: Family,
    inst: Instance,
    *,
    seed: int | None = None,
    cfg: AntiCheatConfig | None = None,
    pool_key_hashes: Iterable[str] | None = None,
) -> Twin | None:
    """Двойник экземпляра или None (причину даёт try_make_twin). Реализация по умолчанию для Family.twin()."""
    return try_make_twin(fam, inst, seed=seed, cfg=cfg, pool_key_hashes=pool_key_hashes).twin
