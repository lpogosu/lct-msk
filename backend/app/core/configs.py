"""Загрузка YAML-конфигов из каталога config/ в корне репозитория (переопределяется VZ_CONFIG_DIR)."""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(os.environ.get("VZ_CONFIG_DIR", Path(__file__).resolve().parents[3] / "config"))


@cache
def load_config(name: str) -> dict[str, Any]:
    """load_config("taxonomy") → содержимое config/taxonomy.yaml."""
    with open(CONFIG_DIR / f"{name}.yaml", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f)
        return data