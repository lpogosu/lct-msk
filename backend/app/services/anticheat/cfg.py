"""Параметры сервиса «Антиподсказчика» из config/anticheat_runtime.yaml (магических чисел в коде нет)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.core.configs import load_config
from app.core.settings import RateRule


@dataclass(frozen=True)
class RuntimeConfig:
    lazy_budget_s: float
    lazy_workers: int
    clip_version: str
    render_id_prefix: str
    precompute_workers: int
    precompute_workers_cap: int
    precompute_per_family: int
    precompute_batch_commit: int
    copy_types: tuple[str, ...]
    self_paste_type: str
    list_limit: int
    list_limit_max: int
    note_max_chars: int
    sandbox_build_budget_s: float
    sandbox_build_workers: int
    sandbox_candidates_per_slot: int
    sandbox_bank_per_language: int
    sandbox_bank_warm_budget_s: float
    sandbox_start_rate: RateRule
    sandbox_score_rate: RateRule
    sandbox_max_answer_chars: int
    sandbox_retention_hours: int
    raw: dict[str, Any]

    def raw_text(self, path: str) -> Any:
        node: Any = self.raw["texts"]
        for part in path.split("."):
            node = node[part]
        return node

    def text(self, path: str, **kw: Any) -> str:
        """Строка из texts.* (путь через точку) с подстановкой {поле}."""
        raw = " ".join(str(self.raw_text(path)).split())
        return raw.format(**kw) if kw else raw


def _rule(d: dict[str, Any]) -> RateRule:
    return RateRule(int(d["capacity"]), int(d["period_s"]))


@lru_cache(maxsize=1)
def runtime_config() -> RuntimeConfig:
    raw = load_config("anticheat_runtime")
    issue, pre, ev, lab, sb = raw["issue"], raw["precompute"], raw["events"], raw["lab"], raw["sandbox"]
    return RuntimeConfig(
        lazy_budget_s=float(issue["lazy_budget_s"]),
        lazy_workers=int(issue["lazy_workers"]),
        clip_version=str(issue["clip_version"]),
        render_id_prefix=str(issue["render_id_prefix"]),
        precompute_workers=int(pre["workers"]),
        precompute_workers_cap=int(pre["workers_cap"]),
        precompute_per_family=int(pre["per_family_default"]),
        precompute_batch_commit=int(pre["batch_commit"]),
        copy_types=tuple(ev["copy_types"]),
        self_paste_type=str(ev["self_paste_type"]),
        list_limit=int(lab["list_limit"]),
        list_limit_max=int(lab["list_limit_max"]),
        note_max_chars=int(lab["note_max_chars"]),
        sandbox_build_budget_s=float(sb["build_budget_s"]),
        sandbox_build_workers=int(sb["build_workers"]),
        sandbox_candidates_per_slot=int(sb["candidates_per_slot"]),
        sandbox_bank_per_language=int(sb["bank_per_language"]),
        sandbox_bank_warm_budget_s=float(sb["bank_warm_budget_s"]),
        sandbox_start_rate=_rule(sb["start_rate"]),
        sandbox_score_rate=_rule(sb["score_rate"]),
        sandbox_max_answer_chars=int(sb["max_answer_chars"]),
        sandbox_retention_hours=int(sb["result_retention_hours"]),
        raw=raw,
    )
