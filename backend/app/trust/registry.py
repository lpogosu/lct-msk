"""Реестр юридических лиц (ЕГРЮЛ/ЕГРИП): интерфейс и учебная реализация на вымышленных компаниях.

Платформа не ходит во внешние реестры в MVP: `EgrulProvider` — точка подключения реального сервиса ФНС без
изменения проверки. Мок читает app/trust/data/mock_egrul.json (только вымышленные организации).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Protocol

_DATA = Path(__file__).parent / "data" / "mock_egrul.json"


@dataclass(frozen=True)
class RegistryRecord:
    name: str
    short_name: str
    inn: str
    ogrn: str
    status: str
    domains: tuple[str, ...]


class EgrulProvider(Protocol):
    def lookup(self, inn: str) -> RegistryRecord | None: ...


class MockEgrul:
    def __init__(self, records: list[RegistryRecord]) -> None:
        self._by_inn = {r.inn: r for r in records}

    def lookup(self, inn: str) -> RegistryRecord | None:
        return self._by_inn.get(inn)


@cache
def mock_registry() -> MockEgrul:
    raw = json.loads(_DATA.read_text(encoding="utf-8"))
    return MockEgrul(
        [
            RegistryRecord(
                name=c["name"],
                short_name=c.get("short_name") or c["name"],
                inn=c["inn"],
                ogrn=c["ogrn"],
                status=c["status"],
                domains=tuple(d.lower() for d in c.get("domains", ())),
            )
            for c in raw["companies"]
        ]
    )
