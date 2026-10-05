"""Доступ к config/demo.yaml: один словарь на процесс (кэш — в app.core.configs.load_config)."""

from __future__ import annotations

from typing import Any

from app.core.configs import load_config


def demo_cfg() -> dict[str, Any]:
    return load_config("demo")


def section(name: str) -> dict[str, Any]:
    return demo_cfg()[name]
