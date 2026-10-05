"""Симуляция кандидатов на ТОМ ЖЕ движке, что и прод (spec-testing §6, concept §7).

Симулянт с истинным θ (опционально — свой θ по модулям) отвечает по модели ответа с шумом клонов
δ ~ N(0, σ_inst²). run_simulated_session прогоняет полную сессию через машину состояний TestSession,
включая доборы. Используется валидацией (S1–S5), превью «Два кандидата — два теста» и тестами.

Также здесь генератор синтетического банка (FamilyMeta) — чтобы движок можно было проверять и
показывать без реального банка семейств.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

from app.testing.cat import (
    KIND_SIM,
    OPERATIONAL,
    PRETEST,
    CatConfig,
    FamilyMeta,
    ScaleParam,
    load_blueprint,
    load_cat_config,
    trace,
)
from app.testing.irt import Scale, load_scale
from app.testing.session import FixedClock, SessionStatus, TestSession

SIM_EPOCH = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def as_random(rng: random.Random | np.random.Generator | int | None) -> random.Random:
    """Нормализовать источник случайности: всё случайное — только через явный seed."""
    if isinstance(rng, random.Random):
        return rng
    if isinstance(rng, np.random.Generator):
        return random.Random(int(rng.integers(0, 2**63 - 1)))
    if isinstance(rng, int):
        return random.Random(rng)
    raise TypeError("Нужен random.Random, numpy Generator или целый seed")


@dataclass
class SimulatedExaminee:
    """Истинная модель кандидата. true_b / a_true позволяют моделировать ошибку экспертов (S2)."""

    theta: float
    module_theta: Mapping[str, float] | None = None
    true_b: Mapping[str, float] | None = None  # family_id → истинная b (иначе — b движка)
    a_true: Mapping[str, float] | None = None  # family_id → истинная крутизна (иначе — A шкалы)
    A: float | None = None
    sigma_inst: float | None = None

    def theta_for(self, module: str) -> float:
        if self.module_theta and module in self.module_theta:
            return self.module_theta[module]
        return self.theta

    def p_correct(self, meta: FamilyMeta, spec: str, scale: Scale, delta: float = 0.0) -> float:
        b = self.true_b[meta.id] if self.true_b and meta.id in self.true_b else meta.scale_param(spec, scale).b
        a = self.a_true[meta.id] if self.a_true and meta.id in self.a_true else (self.A or scale.A)
        z = a * (self.theta_for(meta.module) - (b + delta))
        return meta.c + (1 - meta.c) / (1 + math.exp(-max(-35.0, min(35.0, z))))

    def answer(self, meta: FamilyMeta, spec: str, scale: Scale, rng: random.Random) -> bool:
        """Ответ на свой одноразовый экземпляр: клон сдвинут на δ ~ N(0, σ_inst²)."""
        sigma = scale.sigma_inst if self.sigma_inst is None else self.sigma_inst
        delta = rng.gauss(0.0, sigma) if sigma > 0 else 0.0
        return rng.random() < self.p_correct(meta, spec, scale, delta)


def run_simulated_session(
    theta_true: float,
    spec: str,
    claimed_grade: str,
    families_meta: Sequence[FamilyMeta],
    rng: random.Random | np.random.Generator | int,
    *,
    module_theta: Mapping[str, float] | None = None,
    examinee: SimulatedExaminee | None = None,
    follow_extensions: bool = True,
    accept_up: bool = True,
    seen_history: Mapping[str, datetime] | None = None,
    session_id: str = "sim",
    scale: Scale | None = None,
    cfg: CatConfig | None = None,
    answer_seconds: float = 45.0,
) -> dict[str, Any]:
    """Полная CAT-сессия симулянта через TestSession (тот же код, что в проде).

    Политика доборов симулянта: вниз — всегда принимает (в т.ч. «подтвердить одним нажатием»),
    вверх — если accept_up. Возвращает decision (основная часть), θ̂, SE, n, items, trace, final_grade.
    """
    scale = scale or load_scale()
    cfg = cfg or load_cat_config()
    r = as_random(rng)
    ex = examinee or SimulatedExaminee(theta=theta_true, module_theta=module_theta)
    by_id = {f.id: f for f in families_meta}
    clock = FixedClock(SIM_EPOCH)
    sess = TestSession.create(
        session_id=session_id,
        account_id=f"acc-{session_id}",
        spec=spec,
        claimed_grade=claimed_grade,
        clock=clock,
        rng=r,
        kind=KIND_SIM,
        scale=scale,
        cfg=cfg,
    )
    sess.start(clock)
    chain = [sess]
    extensions: list[dict[str, Any]] = []
    instances: list[str] = []
    current = sess
    ext_counter = 0
    while True:
        while current.status == SessionStatus.IN_PROGRESS:
            sel = current.next_item(families_meta, r, clock, seen_history=seen_history, scale=scale, cfg=cfg)
            if sel is None:
                break
            clock.advance(seconds=answer_seconds)
            ok = ex.answer(by_id[sel.family_id], spec, scale, r)
            inst = f"{sel.family_id}#{r.getrandbits(48):012x}"
            instances.append(inst)
            current.answer(sel.seq, ok, clock, instance_id=inst, scale=scale, cfg=cfg)
        if current is not sess:
            d = current.decision
            extensions[-1].update(
                {
                    "n_items": d.n_items if d else 0,
                    "decision": d.code if d else None,
                    "p_pass": d.p_pass if d else None,
                }
            )
        if not follow_extensions or current.status != SessionStatus.EXTENSION_OFFERED:
            break
        offer = next((o for o in current.offers if o.direction == "down"), None)
        if offer is None and accept_up:
            offer = next((o for o in current.offers if o.direction == "up"), None)
        if offer is None:
            break
        ext_counter += 1
        extensions.append(
            {"direction": offer.direction, "target_grade": offer.target_grade, "instant": offer.instant}
        )
        current = current.accept_extension(
            offer.direction, clock, child_id=f"{session_id}-ext{ext_counter}", scale=scale, cfg=cfg
        )
        chain.append(current)
    if current.status in (SessionStatus.DECIDED, SessionStatus.EXTENSION_OFFERED):
        current.close(clock)

    main = sess.decision
    last = chain[-1]
    final_grade = last.granted_grade()
    post = last.cat.posterior(scale)
    claimed_cut = scale.cut(claimed_grade)
    passed = bool(main and main.passed)
    items = [
        {
            "seq": rr.seq,
            "phase": rr.phase,
            "family_id": rr.family_id,
            "instance_id": rr.instance_id,
            "module": rr.module,
            "family_grade": rr.family_grade,
            "b": round(rr.b, 3),
            "c": rr.c,
            "is_pretest": rr.is_pretest,
            "correct": rr.correct,
            "theta_after": rr.theta_after,
            "se_after": rr.se_after,
            "p_after": rr.p_after,
        }
        for rr in last.cat.responses
    ]
    return {
        "theta_true": theta_true,
        "true_grade": scale.grade_of(theta_true),
        "spec": spec,
        "claimed_grade": claimed_grade,
        "decision": main.code if main else None,
        "passed": passed,
        "correct_decision": passed == (theta_true >= claimed_cut),
        "p_pass": main.p_pass if main else None,
        "theta_hat": main.theta if main else None,
        "se": main.se if main else None,
        "n": main.n_items if main else 0,
        "n_pretest": sess.cat.n_pretest,
        "n_total": last.cat.n_scored,
        "theta_hat_final": round(post.mean, 4),
        "se_final": round(post.sd, 4),
        "final_grade": final_grade,
        "borderline": last.outcome(scale, cfg).borderline if final_grade else False,
        "class_probs": {k: round(v, 4) for k, v in post.class_probs().items()},
        "score": round(scale.score(post.mean)),
        "interval80": [round(x) for x in scale.score_interval(post.mean, post.sd)],
        "items": items,
        "instances": instances,
        "trace": trace(last.cat),
        "extensions": extensions,
        "log": list(last.cat.log),
    }


# ---------------------------------------------------------------------------
# Синтетический банк и популяция
# ---------------------------------------------------------------------------


def synthetic_families(
    specs: Sequence[str] = ("backend_python",),
    *,
    positions: Sequence[float] = (-1.0, 0.0, 1.0),
    per_cell: int = 1,
    choice_share: float = 0.25,
    sigma_b: float | None = None,
    pretest_per_module: int = 0,
    seed: int = 0,
    scale: Scale | None = None,
) -> list[FamilyMeta]:
    """Синтетические семейства: на каждый модуль blueprint — по грейду × положению внутри грейда.

    Общие модули (http_api, algo) получают обе специализации. Часть семейств — «выбор из 4» (c = 0.25).
    b задаётся экспертным приором (cut(g) + 0.35·pos); σ_b — из конфига, если не задан явно.
    """
    scale = scale or load_scale()
    rng = random.Random(seed)
    module_specs: dict[str, list[str]] = {}
    for spec in specs:
        for m in load_blueprint(spec):
            module_specs.setdefault(m, []).append(spec)
    out: list[FamilyMeta] = []
    for module, mspecs in module_specs.items():
        for grade in scale.grades:
            for pos in positions:
                for k in range(per_cell):
                    c = 0.25 if rng.random() < choice_share else 0.0
                    fid = f"syn.{module}.{grade}.{'m' if pos < 0 else 'p'}{abs(pos):g}.{k}"
                    params = {}
                    if sigma_b is not None:
                        b = scale.cut(grade) + scale.pos_step * pos
                        params = {s: ScaleParam(b=b, sigma_b=sigma_b) for s in mspecs}
                    out.append(
                        FamilyMeta(
                            id=fid,
                            module=module,
                            specs=tuple(mspecs),
                            target_grade=grade,
                            position=pos,
                            c=c,
                            status=OPERATIONAL,
                            params=params,
                            format="mcq_concept" if c else "predict_output",
                        )
                    )
        for k in range(pretest_per_module):
            grade = scale.grades[k % len(scale.grades)]
            out.append(
                FamilyMeta(
                    id=f"syn.{module}.pretest.{k}",
                    module=module,
                    specs=tuple(mspecs),
                    target_grade=grade,
                    position=0.0,
                    status=PRETEST,
                )
            )
    return out


def sample_claimed_grade(
    true_grade: str,
    rng: random.Random,
    scale: Scale | None = None,
    probs: tuple[float, float, float] = (0.6, 0.3, 0.1),
) -> str:
    """Заявка совпадает с истиной в 60%, на ступень выше в 30%, ниже в 10% (spec §6.1, S1)."""
    scale = scale or load_scale()
    grades = scale.grades
    i = grades.index(true_grade) if true_grade in grades else 0
    u = rng.random()
    if u < probs[0]:
        j = i
    elif u < probs[0] + probs[1]:
        j = i + 1
    else:
        j = i - 1
    return grades[max(0, min(len(grades) - 1, j))]


def decision_accuracy(results: Sequence[Mapping[str, Any]]) -> float:
    """Доля верных решений у заявленного порога."""
    return sum(1 for r in results if r["correct_decision"]) / max(len(results), 1)
