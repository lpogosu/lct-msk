"""Реестр всех семейств: импортирует все модули пакета families.* (они регистрируются через @register)."""

from __future__ import annotations

import importlib
import pkgutil

from app.testing.bank import families
from app.testing.bank.base import REGISTRY, Family

_loaded = False


def all_families() -> dict[str, Family]:
    global _loaded
    if not _loaded:
        for mod in pkgutil.walk_packages(families.__path__, prefix=families.__name__ + "."):
            importlib.import_module(mod.name)
        _loaded = True
    return REGISTRY