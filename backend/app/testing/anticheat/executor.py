"""Исполнение показанного кода и SQL для ключей двойников.

Ключ двойника, как и основной ключ, получается ТОЛЬКО исполнением (bank/execution.py: run_python, run_js,
run_sql), руками он не вводится никогда (spec-antiassist §2.1). Здесь — тонкая обёртка: кеш по тексту
программы, таймаут, единый результат ExecOutcome, проекция результата запроса в «текст ответа» и
параллельный прогон (запуск подпроцесса — десятки миллисекунд, потоки не конфликтуют).

Известное ограничение окружения: run_python запускает подпроцесс с урезанным окружением, в Windows без
SYSTEMROOT не работает asyncio (сокет цикла событий). Для кода с asyncio, и только для него, используем
тот же in-process исполнитель, что и сами семейства (виртуальное время); источник фиксируется в
ExecOutcome.via, а значит и в Twin.executor.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, TypeVar

from app.testing.bank.base import Family, Instance, canonical
from app.testing.bank.execution import run_js, run_python, run_sql

T = TypeVar("T")
R = TypeVar("R")

EXEC_WORKERS = 12


@dataclass(frozen=True)
class ExecOutcome:
    ok: bool
    output: str = ""
    error: str = ""
    via: str = ""  # run_python | run_js | run_sql | exec_py


# ----------------------------------------------------------------------------- код


def _exec_py_fallback(code: str, timeout: float) -> ExecOutcome:
    from app.testing.bank.families.backend_python._common import exec_py  # noqa: PLC0415 — только для asyncio

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(exec_py, code)
        try:
            return ExecOutcome(True, fut.result(timeout=timeout), via="exec_py")
        except FutureTimeout:
            return ExecOutcome(False, error="timeout", via="exec_py")
        except Exception as e:  # noqa: BLE001 — любая ошибка эталонного кода = «не исполняется»
            return ExecOutcome(False, error=f"{type(e).__name__}: {e}"[:300], via="exec_py")


@lru_cache(maxsize=20000)
def _run_code_cached(language: str, code: str, timeout: float) -> ExecOutcome:
    if language == "python" and "asyncio" in code and sys.platform == "win32":
        return _exec_py_fallback(code, timeout)  # run_python на Windows с урезанным окружением asyncio не запускает
    try:
        if language == "python":
            return ExecOutcome(True, run_python(code, timeout=timeout), via="run_python")
        if language == "javascript":
            return ExecOutcome(True, run_js(code, timeout=timeout), via="run_js")
    except subprocess.TimeoutExpired:
        return ExecOutcome(False, error="timeout", via=f"run_{language[:2]}")
    except Exception as e:  # noqa: BLE001 — ExecutionError, а также сбой чтения stderr в урезанном окружении
        if language == "python" and "asyncio" in code:
            return _exec_py_fallback(code, timeout)
        return ExecOutcome(False, error=f"{type(e).__name__}: {e}"[:300], via=f"run_{language[:2]}")
    return ExecOutcome(False, error=f"язык {language!r} не исполняется", via="")


def run_code(language: str, code: str, timeout: float = 4.0) -> ExecOutcome:
    """Исполнить программу на python / javascript. Результаты кешируются по (язык, текст, таймаут)."""
    return _run_code_cached(language, code, timeout)


# ----------------------------------------------------------------------------- пакетный прогон мутантов (K_err)

# Мутантов типичных ошибок десятки на задание; запуск интерпретатора на каждый — секунды. Python-мутанты
# исполняются ОДНИМ запуском run_python: программа-обвязка выполняет каждый исходник в чистом пространстве имён
# и возвращает вывод каждого. Ключи самих двойников и заданий так не считаются (только run_code): пакет нужен
# лишь для гейта «ключ двойника не совпал с типичной ошибкой».
_PY_HARNESS = """import contextlib, ctypes, io, json, sys, threading
SRC = json.loads({payload})
LIMIT = {limit}
class Stop(BaseException):
    pass
def kill(tid):
    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), ctypes.py_object(Stop))
res = []
for src in SRC:
    buf = io.StringIO()
    ok, err = True, ""
    timer = threading.Timer(LIMIT, kill, (threading.get_ident(),))
    try:
        code = compile(src, "<mutant>", "exec")
        timer.start()
        with contextlib.redirect_stdout(buf):
            exec(code, {{"__name__": "__main__"}})
        timer.cancel()
    except Stop:
        ok, err = False, "timeout"
    except SystemExit:
        timer.cancel()
    except BaseException as e:
        timer.cancel()
        ok, err = False, type(e).__name__
    res.append([ok, buf.getvalue().rstrip("
") if ok else "", err])
sys.__stdout__.write(json.dumps(res))
"""

_BATCH_CACHE: dict[tuple[str, str], ExecOutcome] = {}
_BATCH_CACHE_MAX = 40000


def _needs_single_run(source: str) -> bool:
    """asyncio на Windows run_python не запускает (см. заголовок модуля): такие исходники идут по одному через run_code."""
    return "asyncio" in source and sys.platform == "win32"


def _run_python_batch(sources: Sequence[str], timeout: float, mutant_timeout: float) -> list[ExecOutcome] | None:
    harness = _PY_HARNESS.format(payload=repr(json.dumps(list(sources))), limit=mutant_timeout)
    try:
        raw = run_python(harness, timeout=timeout)
        rows = json.loads(raw)
    except Exception:  # noqa: BLE001 — пакет упал или завис: вызывающий код исполнит исходники по одному
        return None
    if not isinstance(rows, list) or len(rows) != len(sources):
        return None
    return [
        ExecOutcome(True, str(out), via="run_python") if ok else ExecOutcome(False, error=str(err), via="run_python")
        for ok, out, err in rows
    ]


def run_many(
    language: str,
    sources: Sequence[str],
    *,
    timeout: float = 4.0,
    batch_chars: int = 14000,
    batch_timeout: float = 40.0,
    mutant_timeout: float = 1.0,
) -> list[ExecOutcome]:
    """Исполнить много программ (мутанты). Python — пакетами через run_python, JavaScript — по одному через run_js.

    Порядок результатов совпадает с порядком sources; результаты кешируются по тексту исходника. Мутант, который
    не уложился в mutant_timeout (бесконечный цикл), помечается как не исполнившийся. Если пакет не отработал
    (зависание в C-коде, лимит командной строки), исходники исполняются по одному.
    """
    out: list[ExecOutcome | None] = [None] * len(sources)
    pending: list[int] = []
    for i, src in enumerate(sources):
        hit = _BATCH_CACHE.get((language, src))
        if hit is not None:
            out[i] = hit
        else:
            pending.append(i)

    def store(i: int, res: ExecOutcome) -> None:
        out[i] = res
        if len(_BATCH_CACHE) >= _BATCH_CACHE_MAX:
            _BATCH_CACHE.clear()
        _BATCH_CACHE[(language, sources[i])] = res

    if language == "python":
        singles = [i for i in pending if _needs_single_run(sources[i]) or len(sources[i]) > batch_chars]
        batchable = [i for i in pending if i not in set(singles)]
        chunk: list[int] = []
        size = 0
        chunks: list[list[int]] = []
        for i in batchable:
            cost = len(json.dumps(sources[i]))
            if chunk and size + cost > batch_chars:
                chunks.append(chunk)
                chunk, size = [], 0
            chunk.append(i)
            size += cost
        if chunk:
            chunks.append(chunk)
        for ch in chunks:
            res = _run_python_batch([sources[i] for i in ch], batch_timeout, mutant_timeout)
            if res is None:
                singles.extend(ch)
                continue
            for i, r in zip(ch, res, strict=True):
                store(i, r)
        for i in singles:
            store(i, run_code("python", sources[i], timeout))
    else:
        for i in pending:
            store(i, run_code(language, sources[i], timeout))
    return [o if o is not None else ExecOutcome(False, error="not executed") for o in out]


# ----------------------------------------------------------------------------- SQL


@dataclass(frozen=True)
class SqlTable:
    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    @property
    def types(self) -> tuple[str, ...]:
        return tuple(_infer_type([r[j] for r in self.rows]) for j in range(len(self.columns)))


def _infer_type(values: Sequence[Any]) -> str:
    vals = [v for v in values if v is not None]
    if vals and all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
        return "INTEGER"
    if vals and all(isinstance(v, int | float) and not isinstance(v, bool) for v in vals):
        return "REAL"
    return "TEXT"


def tables_from_rendered(table: dict[str, Any] | None) -> list[SqlTable]:
    """Rendered.table → таблицы: либо «tables» (несколько), либо сама name/columns/rows."""
    if not table:
        return []
    items = table.get("tables") or [table]
    return [
        SqlTable(str(t["name"]), tuple(t["columns"]), tuple(tuple(r) for r in t["rows"])) for t in items
    ]


def _lit(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return str(v)


def setup_sql(tables: Iterable[SqlTable]) -> str:
    """Скрипт CREATE/INSERT из показанных таблиц (типы столбцов выводятся из значений)."""
    parts: list[str] = []
    for t in tables:
        cols = ", ".join(f"{c} {ty}" for c, ty in zip(t.columns, t.types, strict=True))
        parts.append(f"CREATE TABLE {t.name} ({cols});")
        if t.rows:
            values = ", ".join("(" + ", ".join(_lit(v) for v in r) + ")" for r in t.rows)
            parts.append(f"INSERT INTO {t.name} VALUES {values};")
    return "\n".join(parts)


@lru_cache(maxsize=10000)
def _run_sql_cached(setup: str, query: str) -> tuple[bool, tuple[tuple[Any, ...], ...], str]:
    try:
        return True, tuple(run_sql(setup, query)), ""
    except Exception as e:  # noqa: BLE001 — sqlite3.Error, ValueError и т.п.
        return False, (), f"{type(e).__name__}: {e}"[:300]


def run_query(tables: Sequence[SqlTable], query: str) -> tuple[bool, tuple[tuple[Any, ...], ...], str]:
    return _run_sql_cached(setup_sql(tables), query)


def _num(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


# Как результат запроса превращается в текст ответа. Единой формулы у семейств нет (где-то нужна вся строка,
# где-то только число), поэтому проекцию подбираем по ПОКАЗАННОМУ экземпляру: подходит та, что воспроизводит его ключ.
PROJECTIONS: dict[str, Callable[[Sequence[Sequence[Any]]], str]] = {
    "all_cells": lambda rows: " ".join(_num(v) for r in rows for v in r),
    "numeric_cells": lambda rows: " ".join(_num(v) for r in rows for v in r if _is_number(v)),
    "last_column": lambda rows: " ".join(_num(r[-1]) for r in rows if r),
    "first_cell": lambda rows: _num(rows[0][0]) if rows and rows[0] else "",
}


def project_rows(rows: Sequence[Sequence[Any]], projection: str) -> str:
    return PROJECTIONS[projection](rows)


def find_projection(fam: Family, inst: Instance, rows: Sequence[Sequence[Any]]) -> str | None:
    """Имя проекции, при которой нормализованный текст результата равен ключу экземпляра."""
    for name in PROJECTIONS:
        if canonical(fam.normalize(project_rows(rows, name))) == canonical(inst.key):
            return name
    return None


# ----------------------------------------------------------------------------- параллельный прогон


def parallel_map(fn: Callable[[T], R], items: Iterable[T], workers: int = EXEC_WORKERS) -> list[R]:
    """map с потоками: исполнение — подпроцессы, GIL не мешает. Порядок результатов сохраняется."""
    seq = list(items)
    if len(seq) <= 1 or workers <= 1:
        return [fn(x) for x in seq]
    with ThreadPoolExecutor(max_workers=min(workers, len(seq))) as pool:
        return list(pool.map(fn, seq))
