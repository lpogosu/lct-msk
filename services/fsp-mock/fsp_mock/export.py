"""Производные файлы из генератора демо-реестра (держим их в git, синхронность проверяют тесты).

    python -m fsp_mock export          — пересобрать все файлы ниже;
    python -m fsp_mock sync-contract   — скопировать backend/app/fsp/models.py → fsp_mock/contract.py.

  backend/app/fsp/data/demo_registry.json — снимок для встроенного провайдера бэкенда (InProcessFSPProvider);
  deploy/keycloak/realm-fsp.json          — realm «fsp» с демо-пользователями (id пользователя = sub);
  deploy/keycloak/README.md               — таблица «логин → sub → сценарий» между маркерами.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .generator import DEFAULT_PUBLIC_URL, SEED, build_dataset
from .keycloak import build_realm, demo_users, mapping_table

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT_PATH = ROOT / "backend" / "app" / "fsp" / "data" / "demo_registry.json"
REALM_PATH = ROOT / "deploy" / "keycloak" / "realm-fsp.json"
README_PATH = ROOT / "deploy" / "keycloak" / "README.md"
CONTRACT_SRC = ROOT / "backend" / "app" / "fsp" / "models.py"
CONTRACT_DST = Path(__file__).resolve().parent / "contract.py"
BEGIN, END = "<!-- demo-users:begin -->", "<!-- demo-users:end -->"


def render_snapshot(seed: int = SEED, public_url: str = DEFAULT_PUBLIC_URL) -> str:
    return build_dataset(seed=seed, public_url=public_url).model_dump_json(indent=1) + "\n"


def render_realm(seed: int = SEED) -> str:
    snap = build_dataset(seed=seed)
    return json.dumps(build_realm(snap, demo_users(snap)), ensure_ascii=False, indent=2) + "\n"


def render_readme(text: str, seed: int = SEED) -> str:
    table = mapping_table(demo_users(build_dataset(seed=seed)))
    head, sep, rest = text.partition(BEGIN)
    if not sep:
        return text
    _, sep2, tail = rest.partition(END)
    if not sep2:
        return text
    return f"{head}{BEGIN}\n{table}\n{END}{tail}"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    print(f"записано: {path}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m fsp_mock", description="Демо-реестр ФСП: экспорт файлов")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export", help="снимок для бэкенда, realm Keycloak и таблица пользователей")
    sub.add_parser("sync-contract", help="скопировать контракт из backend/app/fsp/models.py")
    args = parser.parse_args(argv)
    if args.cmd == "sync-contract":
        shutil.copyfile(CONTRACT_SRC, CONTRACT_DST)
        print(f"скопировано: {CONTRACT_SRC} → {CONTRACT_DST}")
        return
    _write(SNAPSHOT_PATH, render_snapshot())
    _write(REALM_PATH, render_realm())
    if README_PATH.exists():
        _write(README_PATH, render_readme(README_PATH.read_text(encoding="utf-8")))
