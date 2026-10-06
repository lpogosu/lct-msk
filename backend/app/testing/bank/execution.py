"""Офлайн-исполнение эталонного кода для вычисления ключей заданий.

Используется ТОЛЬКО при генерации пула экземпляров (CI/seed), никогда в рантайме теста
и никогда для кода кандидата. Исполняется наш собственный сгенерированный код.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from typing import Any


class ExecutionError(RuntimeError):
    pass


def run_python(code: str, timeout: float = 5.0) -> str:
    """Выполнить Python-код в изолированном подпроцессе (-I) и вернуть stdout без завершающего перевода строки."""
    # `-I` игнорирует PYTHON*-переменные (в т.ч. PYTHONIOENCODING), и в Windows дочерний Python пишет в канал в cp1251,
    # а читаем мы UTF-8 (кириллица в print → UnicodeDecodeError). Поэтому кодировку задаём флагом `-X utf8`: он работает и с `-I`.
    proc = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", "-c", code],
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        env={"PYTHONIOENCODING": "utf-8", "PYTHONHASHSEED": "0"},
    )
    if proc.returncode != 0:
        raise ExecutionError(f"python exit {proc.returncode}: {proc.stderr.strip()[-500:]}")
    return proc.stdout.rstrip("\n")


def run_js(code: str, timeout: float = 5.0) -> str:
    """Выполнить JavaScript в Node.js и вернуть stdout без завершающего перевода строки."""
    node = shutil.which("node")
    if node is None:
        raise ExecutionError("node не найден в PATH")
    proc = subprocess.run(
        [node, "-e", code],
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise ExecutionError(f"node exit {proc.returncode}: {proc.stderr.strip()[-500:]}")
    return proc.stdout.rstrip("\n")


def run_sql(setup_sql: str, query: str) -> list[tuple[Any, ...]]:
    """Выполнить setup (CREATE/INSERT) и запрос в in-memory SQLite, вернуть строки результата."""
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(setup_sql)
        return [tuple(r) for r in con.execute(query).fetchall()]
    finally:
        con.close()
