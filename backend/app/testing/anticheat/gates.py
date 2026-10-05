"""Гейты двойников на уровне семейства и реестра (расширение CI-проверок банка; bank/gates.py не трогаем).

twin_gate(family, n=50): у «пригодного по форме» семейства (predict_output / sql_result, ответ вводится текстом,
язык исполняется офлайн) доля экземпляров с валидным двойником ≥ 0.9; twin_key ≠ key всегда; доля двойников,
ключ которых совпал с ключом соседнего экземпляра пула (гейт 4 из spec-antiassist §2.1), не выше 1%;
двойник детерминирован по seed. Семейства непригодные по форме (выбор из вариантов, язык без исполнителя)
гейт не проваливают: их причина фиксируется в отчёте, двойник для них строит автор семейства через Family.twin().

registry_coverage(n): покрытие по всему реестру; пороги — config/anticheat.yaml → gate.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.testing.anticheat.config import AntiCheatConfig, load_anticheat_config
from app.testing.anticheat.executor import EXEC_WORKERS, parallel_map
from app.testing.anticheat.twins import TwinResult, hash_answer, reason_text, structural_reason, try_make_twin
from app.testing.bank.base import Family, Instance, canonical
from app.testing.bank.registry import all_families


@dataclass(frozen=True)
class FamilyTwinStats:
    family_id: str
    title: str
    format: str
    language: str
    answer_mode: str
    eligible: bool  # пригодно по форме: text-ответ, исполняемый язык, формат из supported_formats
    ineligible_reason: str | None
    n: int
    valid: int
    reasons: dict[str, int]  # причины отсутствия двойника (по экземплярам)
    rejects: dict[str, int]  # отклонённые кандидаты по гейтам (метрика качества каталога)
    mean_attempts: float
    pool_collision: float  # после гейта 4: доля двойников, чей ключ совпал с ответом соседнего экземпляра пула (должна быть 0)
    natural_collision: float  # эмпирическая доля кандидатов, чей ключ совпал бы с ответом соседа (плотность ключей семейства)
    honest_mean: float  # средняя модельная вероятность честного совпадения
    deterministic: bool = True
    key_equals_twin: int = 0
    k_err_mean: float = 0.0  # среднее число различных ключей типичных ошибок (K_err), с которыми сверён двойник

    @property
    def share(self) -> float:
        return self.valid / self.n if self.n else 0.0


@dataclass(frozen=True)
class TwinGateResult:
    family_id: str
    applicable: bool  # формат из supported_formats
    eligible: bool
    stats: FamilyTwinStats
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class RegistryCoverage:
    n: int
    stats: tuple[FamilyTwinStats, ...]
    cfg_min_twin_share: float
    cfg_min_family_share: float

    @property
    def total(self) -> int:
        return len(self.stats)

    @property
    def applicable(self) -> list[FamilyTwinStats]:
        return [s for s in self.stats if s.format in ("predict_output", "sql_result")]

    @property
    def eligible(self) -> list[FamilyTwinStats]:
        return [s for s in self.stats if s.eligible]

    @property
    def passing(self) -> list[FamilyTwinStats]:
        return [s for s in self.eligible if s.share >= self.cfg_min_twin_share]

    @property
    def family_share(self) -> float:
        return len(self.passing) / len(self.eligible) if self.eligible else 0.0

    @property
    def meets_threshold(self) -> bool:
        return self.family_share >= self.cfg_min_family_share

    @property
    def instance_share(self) -> float:
        """Доля экземпляров с двойником среди всех экземпляров пригодных семейств."""
        tot = sum(s.n for s in self.eligible)
        return sum(s.valid for s in self.eligible) / tot if tot else 0.0


# ----------------------------------------------------------------------------- статистика


def _generate(fam: Family, n: int) -> tuple[list[Instance], int]:
    out: list[Instance] = []
    failed = 0
    for seed in range(n):
        try:
            out.append(fam.generate(seed))
        except Exception:  # noqa: BLE001 — семейство с исключением на сиде: считаем как «нет экземпляра»
            failed += 1
    return out, failed


def pool_answer_hashes(fam: Family, instances: Sequence[Instance]) -> list[frozenset[str]]:
    """Для каждого экземпляра — хеши ответов (ключ и известные заблуждения) ДРУГИХ экземпляров пула (гейт 4, spec §2.1).

    Ключ двойника не должен совпасть ни с одним из них: иначе детектор «чужого ключа» и улика двойника
    путались бы, а у честного кандидата совпадение стало бы правдоподобнее.
    """
    own = [{hash_answer(fam.id, i.key), *(hash_answer(fam.id, d) for d in i.distractors)} for i in instances]
    counts = Counter(h for hs in own for h in hs)
    return [frozenset(h for h in counts if counts[h] - (1 if h in hs else 0) > 0) for hs in own]


def _assemble(
    fam: Family,
    instances: list[Instance],
    failed: int,
    results: Sequence[TwinResult],
    cfg: AntiCheatConfig,
) -> FamilyTwinStats:
    first = instances[0] if instances else None
    why = structural_reason(fam, first, cfg) if first else "no_code"
    language = (first.rendered.language or "") if first else ""
    mode = first.rendered.answer_mode if first else ""
    n = len(instances) + failed
    if why is not None:
        return FamilyTwinStats(
            fam.id, fam.title, fam.format, language, mode, False, why, n, 0, {why: len(instances)}, {}, 0.0, 0.0, 0.0,
            0.0,
        )
    twins = [(i, r.twin) for i, r in zip(instances, results, strict=True) if r.twin is not None]
    reasons: Counter[str] = Counter(r.reason for r in results if r.twin is None)
    if failed:
        reasons["generate_failed"] = failed
    rejects: Counter[str] = Counter()
    for r in results:
        rejects.update(r.rejects)
    pools = pool_answer_hashes(fam, instances)
    collisions = sum(
        1 for k, r in enumerate(results) if r.twin is not None and r.twin.twin_key_hash in pools[k]
    )
    rejected_pool = rejects.get("pool_collision", 0)
    return FamilyTwinStats(
        family_id=fam.id,
        title=fam.title,
        format=fam.format,
        language=language,
        answer_mode=mode,
        eligible=True,
        ineligible_reason=None,
        n=n,
        valid=len(twins),
        reasons=dict(reasons),
        rejects=dict(rejects),
        mean_attempts=(sum(t.attempts for _, t in twins) / len(twins)) if twins else 0.0,
        pool_collision=(collisions / len(twins)) if twins else 0.0,
        natural_collision=(rejected_pool / (rejected_pool + len(twins))) if (rejected_pool + len(twins)) else 0.0,
        honest_mean=(sum(t.honest_collision_estimate for _, t in twins) / len(twins)) if twins else 0.0,
        key_equals_twin=sum(1 for inst, t in twins if t.twin_key == canonical(inst.key)),
        k_err_mean=(sum(t.k_err_size for _, t in twins) / len(twins)) if twins else 0.0,
    )


@dataclass(frozen=True)
class FamilyRun:
    """Прогон генератора двойников по семейству: экземпляры сидов 0…n−1 и результат для каждого (если пригодно по форме).

    Двойники строятся как в продукте — с гейтом 4: ключ не совпадает с ключами и заблуждениями других экземпляров пула.
    """

    family: Family
    instances: tuple[Instance, ...]
    failed: int  # сидов, на которых generate() упал
    eligible: bool
    results: tuple[TwinResult, ...]  # в порядке instances; пусто, если семейство не пригодно по форме


def collect_runs(
    families: Sequence[Family], n: int, cfg: AntiCheatConfig | None = None, workers: int = EXEC_WORKERS
) -> list[FamilyRun]:
    """Экземпляры генерируются последовательно, двойники строятся параллельно (исполнение — подпроцессы)."""
    cfg = cfg or load_anticheat_config()
    prepared: list[tuple[Family, list[Instance], int, bool]] = []
    jobs: list[tuple[Family, Instance]] = []
    for fam in families:
        instances, failed = _generate(fam, n)
        eligible = bool(instances) and structural_reason(fam, instances[0], cfg) is None
        prepared.append((fam, instances, failed, eligible))
        if eligible:
            jobs.extend((fam, inst) for inst in instances)
    pools: dict[tuple[str, int], frozenset[str]] = {}
    for fam, instances, _failed, eligible in prepared:
        if eligible:
            for inst, pool in zip(instances, pool_answer_hashes(fam, instances), strict=True):
                pools[(fam.id, inst.seed)] = pool
    results = parallel_map(
        lambda j: try_make_twin(j[0], j[1], cfg=cfg, pool_key_hashes=pools[(j[0].id, j[1].seed)]), jobs, workers
    )
    by_family: dict[str, list[TwinResult]] = {}
    for (fam, _), res in zip(jobs, results, strict=True):
        by_family.setdefault(fam.id, []).append(res)
    return [
        FamilyRun(fam, tuple(instances), failed, eligible, tuple(by_family.get(fam.id, [])))
        for fam, instances, failed, eligible in prepared
    ]


def compute_stats(
    families: Sequence[Family], n: int, cfg: AntiCheatConfig | None = None, workers: int = EXEC_WORKERS
) -> list[FamilyTwinStats]:
    cfg = cfg or load_anticheat_config()
    return [stats_of(run, cfg) for run in collect_runs(families, n, cfg, workers)]


def stats_of(run: FamilyRun, cfg: AntiCheatConfig | None = None) -> FamilyTwinStats:
    cfg = cfg or load_anticheat_config()
    return _assemble(run.family, list(run.instances), run.failed, run.results, cfg)


@lru_cache(maxsize=4)
def registry_runs(n: int) -> tuple[FamilyRun, ...]:
    """Прогон по всему реестру (кешируется по n: тесты и отчёт не повторяют дорогое исполнение)."""
    reg = all_families()
    return tuple(collect_runs([reg[i] for i in sorted(reg)], n))


def twin_gate(
    fam: Family, n: int | None = None, *, cfg: AntiCheatConfig | None = None, workers: int = EXEC_WORKERS
) -> TwinGateResult:
    """Гейт семейства: доля двойников, twin_key ≠ key, честные коллизии пула, детерминизм."""
    cfg = cfg or load_anticheat_config()
    n = cfg.gate.n if n is None else n
    stats = compute_stats([fam], n, cfg, workers)[0]
    applicable = fam.format in cfg.twins.supported_formats
    errors: list[str] = []
    if applicable and stats.eligible:
        if stats.share < cfg.gate.min_twin_share:
            top = ", ".join(f"{k}: {v}" for k, v in sorted(stats.reasons.items(), key=lambda kv: -kv[1])[:3])
            errors.append(
                f"доля экземпляров с двойником {stats.share:.2f} < {cfg.gate.min_twin_share} ({top or 'нет причин'})"
            )
        if stats.key_equals_twin:
            errors.append(f"twin_key == key у {stats.key_equals_twin} двойников")
        if stats.pool_collision > cfg.gate.max_honest_collision:
            errors.append(
                f"доля двойников с ключом соседнего экземпляра пула {stats.pool_collision:.3f} > {cfg.gate.max_honest_collision}"
            )
        if stats.natural_collision > cfg.gate.max_natural_collision:
            errors.append(
                f"плотность ключей семейства {stats.natural_collision:.3f} > {cfg.gate.max_natural_collision}: "
                "ключ двойника слишком часто совпадал бы с ответами соседних экземпляров"
            )
        # детерминизм: тот же экземпляр → тот же двойник (первые сиды)
        for seed in range(min(3, n)):
            try:
                inst = fam.generate(seed)
            except Exception:  # noqa: BLE001
                continue
            a, b = try_make_twin(fam, inst, cfg=cfg).twin, try_make_twin(fam, inst, cfg=cfg).twin
            if (a is None) != (b is None) or (a and b and (a.twin_key != b.twin_key or a.clipboard_text != b.clipboard_text)):
                errors.append(f"seed={seed}: двойник недетерминирован")
                break
    return TwinGateResult(fam.id, applicable, stats.eligible, stats, errors)


def registry_coverage(
    n: int | None = None,
    registry: Mapping[str, Family] | None = None,
    cfg: AntiCheatConfig | None = None,
    workers: int = EXEC_WORKERS,
) -> RegistryCoverage:
    """Покрытие двойниками по всему реестру (для реестра по умолчанию прогон кешируется по n)."""
    cfg = cfg or load_anticheat_config()
    n = cfg.gate.n if n is None else n
    if registry is None:
        stats = [stats_of(r, cfg) for r in registry_runs(n)]
    else:
        stats = compute_stats([registry[i] for i in sorted(registry)], n, cfg, workers)
    return RegistryCoverage(n, tuple(stats), cfg.gate.min_twin_share, cfg.gate.min_family_share)


def describe_reason(reason: str, cfg: AntiCheatConfig | None = None) -> str:
    return reason_text(reason, cfg)


def stats_to_dict(s: FamilyTwinStats) -> dict[str, Any]:
    return {
        "family_id": s.family_id,
        "format": s.format,
        "language": s.language,
        "answer_mode": s.answer_mode,
        "eligible": s.eligible,
        "ineligible_reason": s.ineligible_reason,
        "n": s.n,
        "valid": s.valid,
        "share": round(s.share, 4),
        "mean_attempts": round(s.mean_attempts, 2),
        "pool_collision": round(s.pool_collision, 4),
        "natural_collision": round(s.natural_collision, 4),
        "honest_mean": round(s.honest_mean, 5),
        "k_err_mean": round(s.k_err_mean, 2),
        "reasons": s.reasons,
        "rejects": s.rejects,
    }
