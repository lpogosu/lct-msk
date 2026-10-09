"""Офлайн-исполнение эталонного кода для вычисления ключей заданий.

Используется ТОЛЬКО при генерации пула экземпляров (CI/seed), никогда в рантайме теста
и никогда для кода кандидата. Исполняется наш собственный сгенерированный код.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from typing import Any


class ExecutionError(RuntimeError):
    pass


def _python_env() -> dict[str, str]:
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONHASHSEED": "0"}
    # В Windows без SYSTEMROOT не импортируется asyncio/socket (Winsock) → падали семейства с fastapi/starlette.
    for k in ("SYSTEMROOT", "SystemRoot"):
        if k in os.environ:
            env[k] = os.environ[k]
    return env


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """Убить процесс вместе с потомками: иначе внук держит конец канала и communicate() после kill() виснет."""
    if os.name == "nt":
        subprocess.run(  # noqa: S603
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15, check=False
        )
    else:
        import signal

        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()


def _run_bounded(cmd: list[str], timeout: float, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """subprocess.run с жёстким таймаутом, который действительно срабатывает (в т.ч. при внуках, держащих каналы).

    Причина: subprocess.run при TimeoutExpired убивает только прямого потомка и затем без лимита ждёт EOF
    на каналах; в Windows это давало вечное зависание полного прогона pytest.
    """
    popen_kw: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {}
    proc = subprocess.Popen(  # noqa: S603
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
        **popen_kw,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        # TimeoutExpired, как у subprocess.run: на него опирается app/testing/anticheat/executor.py.
        raise subprocess.TimeoutExpired(cmd[:3], timeout) from None
    return proc.returncode, out, err


def run_python(code: str, timeout: float = 5.0) -> str:
    """Выполнить Python-код в изолированном подпроцессе (-I) и вернуть stdout без завершающего перевода строки."""
    # `-I` игнорирует PYTHON*-переменные (в т.ч. PYTHONIOENCODING), и в Windows дочерний Python пишет в канал в cp1251,
    # а читаем мы UTF-8 (кириллица в print → UnicodeDecodeError). Поэтому кодировку задаём флагом `-X utf8`: он работает и с `-I`.
    rc, out, err = _run_bounded([sys.executable, "-I", "-X", "utf8", "-c", code], timeout, _python_env())
    if rc != 0:
        raise ExecutionError(f"python exit {rc}: {err.strip()[-500:]}")
    return out.rstrip("\n")


def run_js(code: str, timeout: float = 5.0) -> str:
    """Выполнить JavaScript в Node.js и вернуть stdout без завершающего перевода строки."""
    node = shutil.which("node")
    if node is None:
        raise ExecutionError("node не найден в PATH")
    rc, out, err = _run_bounded([node, "-e", code], timeout)
    if rc != 0:
        raise ExecutionError(f"node exit {rc}: {err.strip()[-500:]}")
    return out.rstrip("\n")


def run_sql(setup_sql: str, query: str) -> list[tuple[Any, ...]]:
    """Выполнить setup (CREATE/INSERT) и запрос в in-memory SQLite, вернуть строки результата."""
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(setup_sql)
        return [tuple(r) for r in con.execute(query).fetchall()]
    finally:
        con.close()
