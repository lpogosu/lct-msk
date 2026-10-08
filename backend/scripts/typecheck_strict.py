"""Строгая проверка типов ядер (mypy --strict) по списку из pyproject.toml [tool.vzaimno.typecheck_strict].

    cd backend && uv run python scripts/typecheck_strict.py

`--follow-imports=silent`: ошибки соседних модулей (общий долг, см. `uv run mypy`) не мешают гейту ядер.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    files: list[str] = cfg["tool"]["vzaimno"]["typecheck_strict"]["files"]
    cmd = [sys.executable, "-m", "mypy", "--strict", "--follow-imports=silent", "--ignore-missing-imports", *files]
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
