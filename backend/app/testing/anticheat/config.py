"""Конфиг «Антиподсказчика»: config/anticheat.yaml → типизированные параметры и тексты.

Все пороги и тексты живут в YAML (правило проекта: магических чисел в коде нет). Здесь только чтение
и тонкая обёртка с форматированием текстов. Для тестов конфиг можно подменить: AntiCheatConfig.from_dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.core.configs import load_config


@dataclass(frozen=True)
class ErrParams:
    """K_err(X): мутанты типичных ошибок (gate 3, spec-antiassist §2.1)."""

    enabled: bool
    max_python: int
    max_javascript: int
    max_sql: int
    js_timeout_s: float
    batch_chars: int
    batch_timeout_s: float
    mutant_timeout_s: float


@dataclass(frozen=True)
class TwinParams:
    version: str
    supported_formats: tuple[str, ...]
    classes_mvp: tuple[str, ...]
    eligible_languages: tuple[str, ...]
    max_literals: int
    max_values_per_literal: int
    max_attempts: int
    max_values_per_literal_sql: int
    max_attempts_sql: int
    min_value: int
    max_value: int
    max_length_delta: int
    max_key_decimals: int
    rel_change_min: float
    rel_change_max: float
    exec_timeout_s: float
    confusable_pairs: frozenset[frozenset[str]]
    excluded_callees: frozenset[str]
    compare_ops: frozenset[str]
    structural_ops: frozenset[str]
    role_tier: dict[str, int]
    dup_tier: int
    string_max_len: int
    label_letters: str
    sql_id_patterns: tuple[str, ...]
    err: ErrParams


@dataclass(frozen=True)
class EpsParams:
    default: dict[str, float]
    tier_multiplier: dict[int, float]
    reject_inflation: float
    floor: float
    cap: float


@dataclass(frozen=True)
class GateParams:
    n: int
    min_twin_share: float
    max_honest_collision: float
    max_natural_collision: float
    min_family_share: float


@dataclass(frozen=True)
class EvidenceParams:
    trust_weight: float
    calibrated: bool
    twin_rate_per_session: int
    one_hit_per: str
    review_p_threshold: float
    rounding_tolerance: float
    review_min_hits: int
    review_min_after_warning: int
    per_10k: int
    levels: dict[str, str]
    cap_before_warning: str
    cap_a11y_mode: str
    auto_ban: bool


@dataclass(frozen=True)
class VerificationParams:
    n_trace_questions: int
    max_skill_questions: int
    hard_grades: tuple[str, ...]
    hard_b_min: float
    fast_ratio: float
    matches_min_ratio: float
    above_min_ratio: float
    rubric_max: int
    skill_aliases: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class SandboxParams:
    n_items: int
    language_order: tuple[str, ...]
    max_build_attempts: int


@dataclass(frozen=True)
class AntiCheatConfig:
    twins: TwinParams
    eps: EpsParams
    gate: GateParams
    evidence: EvidenceParams
    verification: VerificationParams
    sandbox: SandboxParams
    texts: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AntiCheatConfig:
        t, e, g, ev, v, s = d["twins"], d["eps"], d["gate"], d["evidence"], d["verification"], d["sandbox"]
        twins = TwinParams(
            version=str(t["version"]),
            supported_formats=tuple(t["supported_formats"]),
            classes_mvp=tuple(t["classes_mvp"]),
            eligible_languages=tuple(t["eligible_languages"]),
            max_literals=int(t["max_literals"]),
            max_values_per_literal=int(t["max_values_per_literal"]),
            max_attempts=int(t["max_attempts"]),
            max_values_per_literal_sql=int(t["max_values_per_literal_sql"]),
            max_attempts_sql=int(t["max_attempts_sql"]),
            min_value=int(t["min_value"]),
            max_value=int(t["max_value"]),
            max_length_delta=int(t["max_length_delta"]),
            max_key_decimals=int(t["max_key_decimals"]),
            rel_change_min=float(t["rel_change"]["min"]),
            rel_change_max=float(t["rel_change"]["max"]),
            exec_timeout_s=float(t["exec_timeout_s"]),
            confusable_pairs=frozenset(frozenset(p) for p in t["confusable_pairs"]),
            excluded_callees=frozenset(t["excluded_callees"]),
            compare_ops=frozenset(t["compare_ops"]),
            structural_ops=frozenset(t["structural_ops"]),
            role_tier={str(k): int(v_) for k, v_ in t["role_tier"].items()},
            dup_tier=int(t["dup_tier"]),
            string_max_len=int(t["string_literals"]["max_len"]),
            label_letters=str(t["string_literals"]["label_letters"]),
            sql_id_patterns=tuple(t["sql"]["id_column_patterns"]),
            err=ErrParams(
                enabled=bool(t["err"]["enabled"]),
                max_python=int(t["err"]["max_python"]),
                max_javascript=int(t["err"]["max_javascript"]),
                max_sql=int(t["err"]["max_sql"]),
                js_timeout_s=float(t["err"]["js_timeout_s"]),
                batch_chars=int(t["err"]["batch_chars"]),
                batch_timeout_s=float(t["err"]["batch_timeout_s"]),
                mutant_timeout_s=float(t["err"]["mutant_timeout_s"]),
            ),
        )
        eps = EpsParams(
            default={str(k): float(v_) for k, v_ in e["default"].items()},
            tier_multiplier={int(k): float(v_) for k, v_ in e["tier_multiplier"].items()},
            reject_inflation=float(e["reject_inflation"]),
            floor=float(e["floor"]),
            cap=float(e["cap"]),
        )
        gate = GateParams(
            n=int(g["n"]),
            min_twin_share=float(g["min_twin_share"]),
            max_honest_collision=float(g["max_honest_collision"]),
            max_natural_collision=float(g["max_natural_collision"]),
            min_family_share=float(g["min_family_share"]),
        )
        evidence = EvidenceParams(
            trust_weight=float(ev["trust_weight"]),
            calibrated=bool(ev["calibrated"]),
            twin_rate_per_session=int(ev["twin_rate_per_session"]),
            one_hit_per=str(ev["one_hit_per"]),
            review_p_threshold=float(ev["review_p_threshold"]),
            rounding_tolerance=float(ev["rounding_tolerance"]),
            review_min_hits=int(ev["review_min_hits"]),
            review_min_after_warning=int(ev["review_min_after_warning"]),
            per_10k=int(ev["per_10k"]),
            levels={str(k): str(v_) for k, v_ in ev["levels"].items()},
            cap_before_warning=str(ev["cap_before_warning"]),
            cap_a11y_mode=str(ev["cap_a11y_mode"]),
            auto_ban=bool(ev["auto_ban"]),
        )
        verification = VerificationParams(
            n_trace_questions=int(v["n_trace_questions"]),
            max_skill_questions=int(v["max_skill_questions"]),
            hard_grades=tuple(v["hard_grades"]),
            hard_b_min=float(v["hard_b_min"]),
            fast_ratio=float(v["fast_ratio"]),
            matches_min_ratio=float(v["matches_min_ratio"]),
            above_min_ratio=float(v["above_min_ratio"]),
            rubric_max=int(v["rubric_max"]),
            skill_aliases={str(k): tuple(x) for k, x in v["skill_aliases"].items()},
        )
        sandbox = SandboxParams(
            n_items=int(s["n_items"]),
            language_order=tuple(s["language_order"]),
            max_build_attempts=int(s["max_build_attempts"]),
        )
        return cls(twins, eps, gate, evidence, verification, sandbox, dict(d.get("texts", {})))

    # --- тексты: путь через точку, например cfg.text("verdict.twin_exact", answer="30")
    def raw_text(self, path: str) -> Any:
        node: Any = self.texts
        for part in path.split("."):
            node = node[part]
        return node

    def text(self, path: str, **kw: Any) -> str:
        """Строка-шаблон из texts.* с подстановкой {поле}; ключ «from» передаётся как **{"from": ...}."""
        raw = " ".join(str(self.raw_text(path)).split())
        return raw.format(**kw) if kw else raw


@lru_cache(maxsize=1)
def load_anticheat_config() -> AntiCheatConfig:
    return AntiCheatConfig.from_dict(load_config("anticheat"))
