"""Валидация механики тестирования (spec-testing §6, концепция §7.2 «Тестирование»).

Все решения принимает прод-движок: сессии — `simulate.run_simulated_session` (TestSession + CAT), оценки
фиксированных форм — `irt.Posterior`, CUSUM — `detectors.cusum_scan` / `drift_probs`, калибровка —
`calibration.calibrate_family`, коллизии ключей — `detectors.key_collision_rate` по экземплярам реального
банка. Здесь только сценарии (кто отвечает и как списывает) и подсчёт метрик.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.testing.bank.registry import all_families
from app.testing.calibration import calibrate_family, point_biserial
from app.testing.cat import PASSED, FamilyMeta, info_at, load_blueprint, load_cat_config
from app.testing.detectors import cusum_scan, key_collision_rate, load_detector_config
from app.testing.irt import Posterior, Scale, load_scale
from app.testing.simulate import SimulatedExaminee
from app.testing.survey import suggest_grade
from eval import metrics as M
from eval.common import bank_for, grade_band, grade_index, quotas_for_length, stream
from eval.population import Candidate, Population, simulate_test

SPEC_LEAK = "backend_python"
FORM_LENGTH = 16
B2_INSTANCES = 20


# --------------------------------------------------------------------------- модели списывания и бейзлайнов


@dataclass
class CopyExaminee(SimulatedExaminee):
    """Наш тест, «наивный» списывающий: на слитое семейство вводит слитый ключ ЧУЖОГО экземпляра.

    Слитый экземпляр с вероятностью twin[f] — близнец своего по тексту (тот же ключ, измерено на реальном
    банке); иначе для задания с выбором ключ совпадает со своим с вероятностью c (вариант с тем же id), для
    ввода ответа — неверно. Флаг «чужой ключ» ставится, если доля коллизий ключей семейства < порога (§3.10).
    """

    leaked: frozenset[str] = frozenset()
    collision: Mapping[str, float] = field(default_factory=dict)
    twin: Mapping[str, float] = field(default_factory=dict)
    max_collision: float = 0.02
    caught: list[str] = field(default_factory=list)
    copied: int = 0

    def answer(self, meta: FamilyMeta, spec: str, scale: Scale, rng: random.Random) -> bool:
        if meta.id in self.leaked:
            self.copied += 1
            p_twin = self.twin.get(meta.id, 0.0)
            ok = rng.random() < p_twin + (1.0 - p_twin) * meta.c
            if not ok and self.collision.get(meta.id, 1.0) < self.max_collision:
                self.caught.append(meta.id)
            return ok
        return super().answer(meta, spec, scale, rng)


@dataclass
class SameInstanceExaminee(SimulatedExaminee):
    """B2 (CAT без клонов, 20 многоразовых экземпляров): копирует, только если достался слитый экземпляр."""

    leaked_instances: Mapping[str, frozenset[int]] = field(default_factory=dict)
    n_instances: int = B2_INSTANCES

    def answer(self, meta: FamilyMeta, spec: str, scale: Scale, rng: random.Random) -> bool:
        inst = rng.randrange(self.n_instances)
        if inst in self.leaked_instances.get(meta.id, frozenset()):
            return True
        return super().answer(meta, spec, scale, rng)


@dataclass
class LLMExaminee(SimulatedExaminee):
    """B3 «LLM под каждого»: экземпляр сгенерирован заново — его сложность = задуманная + N(0; σ²),
    и с вероятностью broken ключ неверен (верный ответ засчитывается как ошибка)."""

    sigma_llm: float = 0.5
    broken: float = 0.10

    def answer(self, meta: FamilyMeta, spec: str, scale: Scale, rng: random.Random) -> bool:
        delta = rng.gauss(0.0, self.sigma_llm)
        ok = rng.random() < self.p_correct(meta, spec, scale, delta)
        if rng.random() < self.broken:
            return False
        return ok


# --------------------------------------------------------------------------- фиксированные формы (B0, B1)


def b0_form(spec: str, target_grade: str, bank: Sequence[FamilyMeta], length: int = FORM_LENGTH) -> list[FamilyMeta]:
    """Фиксированная форма: по квотам blueprint самые информативные у порога семейства (одна на всех)."""
    scale = load_scale()
    t = scale.cut(target_grade)
    fams = bank_for(spec, bank)
    quotas = quotas_for_length(load_blueprint(spec), length)
    chosen: list[FamilyMeta] = []
    for m, q in quotas.items():
        mod = sorted((f for f in fams if f.module == m), key=lambda f: (-info_at(t, f.item_params(spec, scale)), f.id))
        chosen += mod[:q]
    rest = sorted((f for f in fams if f not in chosen), key=lambda f: (-info_at(t, f.item_params(spec, scale)), f.id))
    return chosen + rest[: max(0, length - len(chosen))]


def loft_form(spec: str, bank: Sequence[FamilyMeta], rng: random.Random, length: int = FORM_LENGTH) -> list[FamilyMeta]:
    """B1 LOFT: случайная форма той же длины по квотам blueprint."""
    fams = bank_for(spec, bank)
    quotas = quotas_for_length(load_blueprint(spec), length)
    chosen: list[FamilyMeta] = []
    for m, q in quotas.items():
        mod = sorted((f for f in fams if f.module == m), key=lambda f: f.id)
        chosen += rng.sample(mod, min(q, len(mod)))
    rest = sorted((f for f in fams if f not in chosen), key=lambda f: f.id)
    if len(chosen) < length and rest:
        chosen += rng.sample(rest, min(len(rest), length - len(chosen)))
    return chosen


def fixed_form_result(
    form: Sequence[FamilyMeta],
    spec: str,
    target_grade: str,
    examinee: SimulatedExaminee,
    rng: random.Random,
    leaked: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Пройти форму фиксированной длины. «Сдал» — P(уровень ≥ порог) ≥ 0,5 (как BORDERLINE_PASS у CAT)."""
    scale = load_scale()
    post = Posterior.prior(scale)
    for f in form:
        x = True if f.id in leaked else examinee.answer(f, spec, scale, rng)
        post.add(f.item_params(spec, scale), x)
    p = post.p_at_least(scale.cut(target_grade))
    probs = post.class_probs()
    grade = max(probs, key=lambda g: (probs[g], grade_index(g)))
    return {"passed": p >= 0.5, "p": p, "theta": post.mean, "grade": grade}


# --------------------------------------------------------------------------- помощники


def _matched(pop: Population) -> list[Candidate]:
    return [c for c in pop.candidates if c.matched and c.test is not None]


def _correct_decision(c: Candidate, passed: bool, grade: str | None = None) -> bool:
    scale = load_scale()
    return passed == (c.truth.theta >= scale.cut(grade or c.claimed_grade))


def _final_idx(grade: str | None) -> int:
    return grade_index(grade)


def _grade_metrics(true_idx: Sequence[int], pred_idx: Sequence[int]) -> dict[str, Any]:
    return {
        "exact": M.agreement(true_idx, pred_idx),
        "within1": (sum(1 for a, b in zip(true_idx, pred_idx, strict=True) if abs(a - b) <= 1) / len(true_idx))
        if true_idx
        else None,
        "kappa": M.cohen_kappa(true_idx, pred_idx),
        "qwk": M.quadratic_weighted_kappa(true_idx, pred_idx, -1, 3),
    }


def _examinee(c: Candidate) -> SimulatedExaminee:
    return SimulatedExaminee(theta=c.truth.theta, module_theta=c.truth.module_theta)


# --------------------------------------------------------------------------- T1–T2: точность и длина


def accuracy_section(pop: Population) -> dict[str, Any]:
    scale = load_scale()
    rows = _matched(pop)
    dec = [_correct_decision(c, c.test.passed) for c in rows]
    t_idx = [grade_index(c.truth.grade) for c in rows]
    f_idx = [_final_idx(c.test.final_grade) for c in rows]
    by_claim: dict[str, list[bool]] = {"верно": [], "на ступень выше": [], "на ступень ниже": []}
    for c, ok in zip(rows, dec, strict=True):
        d = grade_index(c.claimed_grade) - max(0, grade_index(c.truth.grade))
        key = "верно" if d == 0 else "на ступень выше" if d > 0 else "на ступень ниже"
        by_claim[key].append(ok)
    confusion: dict[str, dict[str, int]] = {}
    for t, f in zip(t_idx, f_idx, strict=True):
        tg, fg = _gname(t), _gname(f)
        confusion.setdefault(tg, {}).setdefault(fg, 0)
        confusion[tg][fg] += 1
    n_main = [c.test.n_main for c in rows]
    n_total = [c.test.n_total for c in rows]
    n_shown = [c.test.n_shown for c in rows]
    main_shown = [c.test.n_main_shown for c in rows]
    decisions = M.counts([c.test.decision for c in rows])
    # B4: грейд по стажу (подсказка опроса)
    b4_idx = [grade_index(suggest_grade(c.experience_months)[0]) for c in rows]
    # Ошибка в специализации: кандидат проходит чужой тест
    mism = [c for c in pop.candidates if c.mismatched and c.test is not None]
    mism_over = [
        _final_idx(c.test.final_grade) >= grade_index(c.truth.grade) and c.test.final_grade is not None for c in mism
    ]
    return {
        "n": len(rows),
        "decision_accuracy": M.mean([float(x) for x in dec]),
        "decision_accuracy_by_claim": {k: {"n": len(v), "accuracy": M.mean([float(x) for x in v])} for k, v in by_claim.items()},
        "grade": _grade_metrics(t_idx, f_idx),
        "grade_b4_experience": _grade_metrics(t_idx, b4_idx),
        "confusion": confusion,
        "decisions": {str(k): v for k, v in decisions.items()},
        "length": {
            "main_mean": M.mean(n_main),
            "main_p95": M.percentile(n_main, 95),
            "total_mean": M.mean(n_total),
            "total_p95": M.percentile(n_total, 95),
            "shown_max": max(n_shown) if n_shown else None,
            "main_shown_mean": M.mean(main_shown),
            "main_shown_p95": M.percentile(main_shown, 95),
            "main_shown_max": max(main_shown) if main_shown else None,
            "share_main_shown_over_20": M.mean([float(n > 20) for n in main_shown]),
            "share_main_at_max": M.mean([float(n >= 20) for n in n_main]),
            "pretest_items_per_session": M.mean([s - t for s, t in zip(n_shown, n_total, strict=True)]),
        },
        "mismatched": {
            "n": len(mism),
            "share_confirmed_at_true_grade_or_higher": M.mean([float(x) for x in mism_over]),
            "share_confirmed_any": M.mean([float(c.test.final_grade is not None) for c in mism]),
        },
        "scale_cuts": {g: scale.cut(g) for g in scale.grades},
    }


def _gname(idx: int) -> str:
    scale = load_scale()
    return "Ниже Стажёра / не подтверждён" if idx < 0 else scale.grade_name(scale.grades[idx])


def instance_overlap_section(pop: Population) -> dict[str, Any]:
    """Пересечение id экземпляров у пар кандидатов. Симулятор выдаёт каждый экземпляр одному человеку (как пул
    в проде), поэтому 0 здесь — свойство выдачи, а не измерение; совпадения по содержанию считает
    key_collision_rates (identical_pair)."""
    seen: dict[str, str] = {}
    dup = 0
    total = 0
    for c in pop.candidates:
        for o in (c.test, c.retest):
            if o is None:
                continue
            for inst in o.instances:
                total += 1
                if inst in seen and seen[inst] != c.id:
                    dup += 1
                seen.setdefault(inst, c.id)
    return {"instances": total, "shared_between_candidates": dup}


# --------------------------------------------------------------------------- T3: пересдача и гистерезис


def retest_section(pop: Population) -> dict[str, Any]:
    rows = [c for c in _matched(pop) if c.retest is not None]
    a = [c.test.passed for c in rows]
    b = [c.retest.passed for c in rows]
    ga = [_final_idx(c.test.final_grade) for c in rows]
    gb = [_final_idx(c.retest.final_grade) for c in rows]
    return {
        "n": len(rows),
        "decision_agreement": M.agreement(a, b),
        "decision_kappa": M.cohen_kappa(a, b),
        "grade_exact": M.agreement(ga, gb),
        "grade_within1": M.mean([float(abs(x - y) <= 1) for x, y in zip(ga, gb, strict=True)]),
        "grade_qwk": M.quadratic_weighted_kappa(ga, gb, -1, 3),
        "theta_r": M.pearson([c.test.theta for c in rows], [c.retest.theta for c in rows]),
        "retest_n_mean": M.mean([c.retest.n_total for c in rows]),
        "retest_decision_accuracy": M.mean([float(_correct_decision(c, c.retest.passed)) for c in rows]),
    }


def hysteresis_section(pop: Population, limit: int) -> dict[str, Any]:
    """Ложное повышение: тест на g+1 (непересекающиеся семейства) сдан на PASSED, хотя истинный грейд ≤ g."""
    scale = load_scale()
    rows = [
        c
        for c in _matched(pop)
        if c.test.final_grade is not None
        and scale.next_grade(c.test.final_grade) is not None
        and grade_index(c.truth.grade) <= grade_index(c.test.final_grade)
    ][:limit]
    false_up = []
    for c in rows:
        nxt = scale.next_grade(c.test.final_grade)
        o = simulate_test(
            theta=c.truth.theta,
            module_theta=c.truth.module_theta,
            spec=c.claimed_spec,
            claimed_grade=nxt,
            rng=stream(f"upgrade:{c.id}", pop.cfg.seed),
            bank=pop.bank,
            seen_families=c.test.families,
            follow_extensions=False,
            session_id=f"up-{c.id}",
        )
        false_up.append(o.decision == PASSED)
    return {"n": len(rows), "false_upgrade_rate": M.mean([float(x) for x in false_up])}


# --------------------------------------------------------------------------- T4: ошибка экспертов (S2)


def expert_error_section(pop: Population, n: int, sigmas: Sequence[float], a_values: Sequence[float]) -> dict[str, Any]:
    """S2. Общие случайные числа: у кандидата во всех условиях один и тот же поток (`s2:<id>`), меняются
    только истинные параметры семейств — разница между условиями не тонет в шуме выборки."""
    rows = _matched(pop)[:n]
    out: dict[str, Any] = {"n": len(rows), "conditions": []}
    fams = sorted({f.id: f for f in pop.bank}.values(), key=lambda f: f.id)
    scale = load_scale()

    def run(label: str, sigma: float, a_val: float, make: Callable[[Candidate], SimulatedExaminee]) -> None:
        dec, exact = [], []
        for c in rows:
            o = simulate_test(theta=c.truth.theta, module_theta=c.truth.module_theta, spec=c.claimed_spec,
                              claimed_grade=c.claimed_grade, rng=stream(f"s2:{c.id}", pop.cfg.seed),
                              bank=pop.bank, examinee=make(c), session_id=f"s2-{c.id}")
            dec.append(_correct_decision(c, o.passed))
            exact.append(grade_index(c.truth.grade) == _final_idx(o.final_grade))
        out["conditions"].append({"label": label, "sigma_b": sigma, "A_true": a_val,
                                  "decision_accuracy": M.mean([float(x) for x in dec]),
                                  "grade_exact": M.mean([float(x) for x in exact])})

    for sigma in sigmas:
        # Ошибка эксперта — одна на семейство (систематическая), на всех кандидатов одинаковая.
        r = stream(f"s2:b:{sigma}", pop.cfg.seed)
        true_b = {f.id: f.scale_param(f.specs[0], scale).b + r.gauss(0.0, sigma) for f in fams}
        label = "σ = 0, A = 1,5 (модель верна)" if sigma == 0 else f"σ = {_fmt(sigma)}"
        run(label, sigma, scale.A, lambda c, tb=true_b: SimulatedExaminee(
            theta=c.truth.theta, module_theta=c.truth.module_theta, true_b=tb))
    for a_val in a_values:
        r = stream(f"s2:a:{a_val}", pop.cfg.seed)
        a_true = {f.id: a_val * math.exp(r.gauss(0.0, 0.25)) for f in fams}
        run(f"истинная крутизна A = {_fmt(a_val)}", 0.0, a_val, lambda c, at=a_true: SimulatedExaminee(
            theta=c.truth.theta, module_theta=c.truth.module_theta, a_true=at))
    return out


def _fmt(x: float) -> str:
    return f"{x:g}".replace(".", ",")


# --------------------------------------------------------------------------- коллизии ключей банка


def identical_pair_share(hashes: Sequence[str]) -> float:
    """Вероятность, что два РАЗНЫХ экземпляра пула (выдача без возвращения) совпадают по содержанию:
    Σ nᵢ(nᵢ − 1) / (N(N − 1)) по группам одинаковых хешей."""
    n = len(hashes)
    if n < 2:
        return 0.0
    counts: dict[str, int] = {}
    for h in hashes:
        counts[h] = counts.get(h, 0) + 1
    return sum(k * (k - 1) for k in counts.values()) / (n * (n - 1))


def _content_hash(inst: Any) -> str:
    """Хеш того, что видит кандидат (Rendered.to_public): совпадение — «тот же экземпляр» по существу."""
    blob = json.dumps(inst.rendered.to_public(), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def key_collision_rates(n_instances: int, families: Sequence[str] | None = None) -> dict[str, Any]:
    """Доля коллизий ключей по семейству: генерируем n экземпляров реального банка (как CI-гейт).

    Заодно — совпадения по СОДЕРЖАНИЮ: id экземпляров в симуляции уникальны по построению (как выдача пула
    в проде), поэтому настоящий вопрос «увидят ли двое одно и то же задание» — это доля одинаковых по тексту
    экземпляров среди разных сидов семейства."""
    fams = all_families()
    ids = sorted(families) if families is not None else sorted(fams)
    rates: dict[str, float] = {}
    identical: dict[str, float] = {}
    errors: dict[str, str] = {}
    for fid in ids:
        try:
            insts = [fams[fid].generate(s) for s in range(n_instances)]
            rates[fid] = key_collision_rate([i.key_hash for i in insts])
            identical[fid] = identical_pair_share([_content_hash(i) for i in insts])
        except Exception as e:  # noqa: BLE001 — семейство без node и т.п.: детектор для него считаем неактивным
            errors[fid] = type(e).__name__
    return {"n_instances": n_instances, "rates": rates, "identical_pair": identical, "errors": errors}


# --------------------------------------------------------------------------- T5: утечки (S4)


def _leak_people(n: int, rng: random.Random, theta_range: tuple[float, float] | None = None) -> list[tuple[float, dict[str, float]]]:
    """Люди для сценариев утечки: (уровень, уровни модулей Backend)."""
    scale = load_scale()
    mods = list(load_blueprint(SPEC_LEAK))
    grades = [("intern", 0.15), ("junior", 0.3), ("middle", 0.3), ("senior", 0.25)]
    out = []
    for _ in range(n):
        if theta_range is None:
            u, acc, g = rng.random(), 0.0, grades[-1][0]
            for gg, w in grades:
                acc += w
                if u < acc:
                    g = gg
                    break
            lo, hi = grade_band(g, scale)
            th = rng.uniform(lo, hi)
        else:
            th = rng.uniform(*theta_range)
        out.append((th, {m: th + rng.gauss(0.0, 0.35) for m in mods}))
    return out


def leak_section(
    pop: Population,
    *,
    n_cheaters: int,
    coalitions: Sequence[int],
    collision: Mapping[str, float],
    deltas: Sequence[float],
    b3_broken: float,
    b3_sigma: float,
    twin: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """S4: «явные Junior» заявляют Middle; коалиции честных кандидатов сливают всё, что видели.

    twin — доля одинаковых по тексту пар экземпляров семейства (key_collision_rates → identical_pair)."""
    twin = dict(twin or {})
    seed = pop.cfg.seed
    scale = load_scale()
    det = load_detector_config()
    bank = bank_for(SPEC_LEAK, pop.bank)
    max_k = max(coalitions)
    members = _leak_people(max_k, stream("leak:members", seed))
    # Что видели члены коалиции в каждой системе
    ours_fams: list[set[str]] = []
    b2_inst: list[dict[str, int]] = []
    b1_forms: list[set[str]] = []
    for j, (th, mth) in enumerate(members):
        r = stream(f"leak:member:{j}", seed)
        o = simulate_test(theta=th, module_theta=mth, spec=SPEC_LEAK, claimed_grade=scale.grade_of(th)
                          if scale.grade_of(th) in scale.grades else "intern", rng=r, bank=pop.bank,
                          session_id=f"m-{j}")
        ours_fams.append(set(o.families))
        b2_inst.append({f: r.randrange(B2_INSTANCES) for f in o.families})
        b1_forms.append({f.id for f in loft_form(SPEC_LEAK, pop.bank, stream(f"leak:loft:{j}", seed))})
    form0 = b0_form(SPEC_LEAK, "middle", pop.bank)
    cheaters = _leak_people(n_cheaters, stream("leak:cheaters", seed), (-0.8, -0.3))

    def run(label: str, examinee_for: Callable[[int, float, dict[str, float]], SimulatedExaminee]) -> list[tuple[bool, SimulatedExaminee]]:
        res = []
        for i, (th, mth) in enumerate(cheaters):
            ex = examinee_for(i, th, mth)
            o = simulate_test(theta=th, module_theta=mth, spec=SPEC_LEAK, claimed_grade="middle",
                              rng=stream(f"leak:{label}:{i}", seed), bank=pop.bank, examinee=ex,
                              follow_extensions=False, session_id=f"ch-{label}-{i}")
            res.append((o.passed, ex))
        return res

    honest = run("honest", lambda i, th, m: SimulatedExaminee(theta=th, module_theta=m))
    honest_rate = M.mean([float(p) for p, _ in honest])
    b3 = run("b3", lambda i, th, m: LLMExaminee(theta=th, module_theta=m, sigma_llm=b3_sigma, broken=b3_broken))
    b3_rate = M.mean([float(p) for p, _ in b3])
    series: dict[str, list[float | None]] = {"ours_naive": [], "ours_careful": [], "b0": [], "b1": [], "b2": [], "b3": []}
    caught: list[float | None] = []
    leaked_share: list[float] = []
    for k in coalitions:
        lf = frozenset().union(*ours_fams[:k]) if k else frozenset()
        leaked_share.append(len(lf) / len(bank))
        if k == 0:
            series["ours_naive"].append(honest_rate)
            caught.append(None)
        else:
            naive = run(f"naive{k}", lambda i, th, m, lf=lf: CopyExaminee(
                theta=th, module_theta=m, leaked=lf, collision=collision, twin=twin,
                max_collision=det.foreign_key_max_collision))
            series["ours_naive"].append(M.mean([float(p) for p, _ in naive]))
            # пойман — хотя бы раз ввёл чужой ключ в семействе с коллизиями < 2% (среди тех, кто списывал)
            copied = [ex for _, ex in naive if ex.copied]
            caught.append(M.mean([float(bool(ex.caught)) for ex in copied]) if copied else None)
        series["ours_careful"].append(honest_rate)
        series["b3"].append(b3_rate)
        # B2: экземпляры, которые видела коалиция
        inst: dict[str, set[int]] = {}
        for d in b2_inst[:k]:
            for f, idx in d.items():
                inst.setdefault(f, set()).add(idx)
        frozen = {f: frozenset(v) for f, v in inst.items()}
        if k == 0:
            series["b2"].append(honest_rate)
        else:
            b2 = run(f"b2_{k}", lambda i, th, m, fr=frozen: SameInstanceExaminee(theta=th, module_theta=m, leaked_instances=fr))
            series["b2"].append(M.mean([float(p) for p, _ in b2]))
        # B0: одна форма на всех — после первого же участника коалиции слита целиком
        leaked0 = frozenset(f.id for f in form0) if k else frozenset()
        b0 = [fixed_form_result(form0, SPEC_LEAK, "middle", SimulatedExaminee(theta=th, module_theta=m),
                                stream(f"leak:b0:{k}:{i}", seed), leaked0)["passed"] for i, (th, m) in enumerate(cheaters)]
        series["b0"].append(M.mean([float(x) for x in b0]))
        lf1 = frozenset().union(*b1_forms[:k]) if k else frozenset()
        b1 = []
        for i, (th, m) in enumerate(cheaters):
            form = loft_form(SPEC_LEAK, pop.bank, stream(f"leak:b1form:{i}", seed))
            b1.append(fixed_form_result(form, SPEC_LEAK, "middle", SimulatedExaminee(theta=th, module_theta=m),
                                        stream(f"leak:b1:{k}:{i}", seed), lf1)["passed"])
        series["b1"].append(M.mean([float(x) for x in b1]))
    # Утечка метода: коалиция 20 знает идею своих семейств → семейство легче на Δb
    k_m = 20 if 20 in coalitions else max(coalitions)
    lf_m = frozenset().union(*ours_fams[:k_m])
    method = []
    for d in deltas:
        if d == 0:
            method.append({"delta_b": d, "false_middle": honest_rate})
            continue
        true_b = {f.id: f.scale_param(SPEC_LEAK, scale).b - d for f in bank if f.id in lf_m}
        res = run(f"method{d}", lambda i, th, m, tb=true_b: SimulatedExaminee(theta=th, module_theta=m, true_b=tb))
        method.append({"delta_b": d, "false_middle": M.mean([float(p) for p, _ in res])})
    active = [f.id for f in bank if collision.get(f.id, 1.0) < det.foreign_key_max_collision]
    return {
        "n_cheaters": n_cheaters,
        "cheater_theta_range": [-0.8, -0.3],
        "claimed": "middle",
        "coalitions": list(coalitions),
        "leaked_bank_share": leaked_share,
        "false_middle": series,
        "caught_naive": caught,
        "method_leak": {"coalition": k_m, "leaked_bank_share": len(lf_m) / len(bank), "rows": method},
        "detector_active_families": {"n": len(active), "of": len(bank)},
        "b3_assumptions": {"sigma": b3_sigma, "broken_keys": b3_broken},
    }


# --------------------------------------------------------------------------- B0/B1/B3: точность и пересдача


def baselines_section(pop: Population, n: int, b3_variants: Sequence[tuple[str, float, float, bool]]) -> dict[str, Any]:
    """Точность у порога и пересдача для B0, B1, B3 на подвыборке популяции.

    b3_variants: (подпись, σ, доля битых ключей, считать ли пересдачу).
    """
    seed = pop.cfg.seed
    rows = _matched(pop)[:n]
    out: dict[str, Any] = {"n": len(rows), "rows": []}
    ours_dec = [_correct_decision(c, c.test.passed) for c in rows]
    ours_retest = [c for c in rows if c.retest is not None]
    out["rows"].append({
        "system": "Наш CAT (клоны)",
        "decision_accuracy": M.mean([float(x) for x in ours_dec]),
        "grade_exact": M.mean([float(grade_index(c.truth.grade) == _final_idx(c.test.final_grade)) for c in rows]),
        "length": M.mean([c.test.n_main for c in rows]),
        "retest_agreement": M.agreement([c.test.passed for c in ours_retest], [c.retest.passed for c in ours_retest])
        if ours_retest else None,
    })
    forms0: dict[tuple[str, str], list[FamilyMeta]] = {}
    acc0, acc1, ret1, ex0, ex1 = [], [], [], [], []
    for c in rows:
        key = (c.claimed_spec, c.claimed_grade)
        if key not in forms0:
            forms0[key] = b0_form(c.claimed_spec, c.claimed_grade, pop.bank)
        r0 = fixed_form_result(forms0[key], c.claimed_spec, c.claimed_grade, _examinee(c), stream(f"b0:{c.id}", seed))
        acc0.append(_correct_decision(c, r0["passed"]))
        ex0.append(grade_index(c.truth.grade) == grade_index(r0["grade"]))
        fa = loft_form(c.claimed_spec, pop.bank, stream(f"b1form:{c.id}", seed))
        r1 = fixed_form_result(fa, c.claimed_spec, c.claimed_grade, _examinee(c), stream(f"b1:{c.id}", seed))
        fb = loft_form(c.claimed_spec, pop.bank, stream(f"b1form2:{c.id}", seed))
        r1b = fixed_form_result(fb, c.claimed_spec, c.claimed_grade, _examinee(c), stream(f"b1b:{c.id}", seed))
        acc1.append(_correct_decision(c, r1["passed"]))
        ex1.append(grade_index(c.truth.grade) == grade_index(r1["grade"]))
        ret1.append(r1["passed"] == r1b["passed"])
    out["rows"].append({"system": "B0 фиксированная форма (16)", "decision_accuracy": M.mean([float(x) for x in acc0]),
                        "grade_exact": M.mean([float(x) for x in ex0]), "length": FORM_LENGTH, "retest_agreement": None})
    out["rows"].append({"system": "B1 LOFT (16)", "decision_accuracy": M.mean([float(x) for x in acc1]),
                        "grade_exact": M.mean([float(x) for x in ex1]), "length": FORM_LENGTH,
                        "retest_agreement": M.mean([float(x) for x in ret1])})
    for label, sigma, broken, with_retest in b3_variants:
        dec, exact, agree, lens = [], [], [], []
        for c in rows:
            ex = LLMExaminee(theta=c.truth.theta, module_theta=c.truth.module_theta, sigma_llm=sigma, broken=broken)
            o = simulate_test(theta=c.truth.theta, module_theta=c.truth.module_theta, spec=c.claimed_spec,
                              claimed_grade=c.claimed_grade, rng=stream(f"b3:{label}:{c.id}", seed), bank=pop.bank,
                              examinee=ex, session_id=f"b3-{c.id}")
            dec.append(_correct_decision(c, o.passed))
            exact.append(grade_index(c.truth.grade) == _final_idx(o.final_grade))
            lens.append(o.n_main)
            if with_retest:
                ex2 = LLMExaminee(theta=c.truth.theta, module_theta=c.truth.module_theta, sigma_llm=sigma, broken=broken)
                o2 = simulate_test(theta=c.truth.theta, module_theta=c.truth.module_theta, spec=c.claimed_spec,
                                   claimed_grade=c.claimed_grade, rng=stream(f"b3r:{label}:{c.id}", seed),
                                   bank=pop.bank, examinee=ex2, seen_families=o.families, session_id=f"b3r-{c.id}")
                agree.append(o.passed == o2.passed)
        out["rows"].append({
            "system": label,
            "sigma": sigma,
            "broken_keys": broken,
            "decision_accuracy": M.mean([float(x) for x in dec]),
            "grade_exact": M.mean([float(x) for x in exact]),
            "length": M.mean(lens),
            "retest_agreement": M.mean([float(x) for x in agree]) if agree else None,
        })
    return out


# --------------------------------------------------------------------------- T6: CUSUM (S5)


@dataclass(frozen=True)
class _Obs:
    p_honest: float
    p_knower: float
    p0: float
    p1: float


_GH_NODES = (-2.0201828705, -0.9585724646, 0.0, 0.9585724646, 2.0201828705)
_GH_WEIGHTS = (0.0199532421, 0.3936193232, 0.9453087205, 0.3936193232, 0.0199532421)


def _p_with_clone_noise(ex: SimulatedExaminee, meta: FamilyMeta, spec: str, scale: Scale) -> float:
    """P(верно) с усреднением по разбросу клона δ ~ N(0; σ_inst²) (Гаусс — Эрмит, 5 узлов).

    Сама модель ответа — SimulatedExaminee.p_correct (тот же симулянт, что в сессиях)."""
    s = scale.sigma_inst
    tot = 0.0
    for x, w in zip(_GH_NODES, _GH_WEIGHTS, strict=True):
        tot += w * ex.p_correct(meta, spec, scale, math.sqrt(2.0) * s * x)
    return tot / math.sqrt(math.pi)


def cusum_section(
    pop: Population,
    *,
    replicates: int,
    h_grid: Sequence[float],
    sessions_per_phase: int = 3000,
    n_leaked: int = 12,
    shares: Sequence[float] = (0.2, 0.5),
    delta: float = 0.5,
) -> dict[str, Any]:
    """S5: поток ответов по каждому семейству Backend; апостериоры π₋ⱼ и p0/p1 — из сессий популяции."""
    seed = pop.cfg.seed
    scale = load_scale()
    det = load_detector_config()
    spec = SPEC_LEAK
    by_id = {f.id: f for f in pop.bank}
    sessions = [c for c in pop.candidates if c.test is not None and c.test.spec == spec]
    pools: dict[str, list[_Obs]] = {}
    for c in sessions:
        for r in c.test.responses:
            if r.is_pretest:
                continue
            meta = by_id[r.family_id]
            th = c.truth.module_theta.get(meta.module, c.truth.theta)
            honest = SimulatedExaminee(theta=th)
            knower = SimulatedExaminee(theta=th, true_b={meta.id: meta.scale_param(spec, scale).b - delta})
            pools.setdefault(r.family_id, []).append(
                _Obs(_p_with_clone_noise(honest, meta, spec, scale), _p_with_clone_noise(knower, meta, spec, scale),
                     r.p0, r.p1)
            )
    n_sess = max(1, len(sessions))
    rates = {f: len(v) / n_sess for f, v in pools.items()}
    fams = sorted(f for f in pools if round(rates[f] * sessions_per_phase) >= 1)
    rl = stream("cusum:leaked", seed)
    leaked = set(rl.sample(fams, min(n_leaked, len(fams))))
    scenarios = [("control", 0.0)] + [(f"share{int(round(s * 100))}", s) for s in shares]
    res: dict[str, Any] = {"h_grid": list(h_grid), "h_config": det.cusum_h, "delta_b": delta,
                           "sessions_per_phase": sessions_per_phase, "replicates": replicates,
                           "n_families": len(fams), "n_leaked": len(leaked), "scenarios": {}}
    example: dict[str, Any] = {}
    for name, share in scenarios:
        alarms_false = {h: 0 for h in h_grid}
        family_runs_false = 0
        detected = {h: 0 for h in h_grid}
        delays: dict[float, list[int]] = {h: [] for h in h_grid}
        leaked_runs = 0
        for rep in range(replicates):
            rng = stream(f"cusum:{name}:{rep}", seed)
            for f in fams:
                pool = pools[f]
                n_phase = max(1, round(rates[f] * sessions_per_phase))
                is_leaked = share > 0 and f in leaked
                obs = []
                for t in range(2 * n_phase):
                    o = pool[rng.randrange(len(pool))]
                    knows = is_leaked and t >= n_phase and rng.random() < share
                    p = o.p_knower if knows else o.p_honest
                    obs.append((rng.random() < p, o.p0, o.p1))
                path, _ = cusum_scan(obs, h=det.cusum_h)
                for h in h_grid:
                    first = next((i for i, s in enumerate(path) if s > h), None)
                    if is_leaked:
                        if first is not None and first >= n_phase:
                            detected[h] += 1
                            delays[h].append(first - n_phase + 1)
                        elif first is not None:
                            alarms_false[h] += 1  # тревога до начала слива — ложная
                    elif first is not None:
                        alarms_false[h] += 1
                if is_leaked:
                    leaked_runs += 1
                else:
                    family_runs_false += 1
                if rep == 0 and name == scenarios[-1][0] and (f in leaked) and "leaked" not in example:
                    example["leaked"] = {"family_id": f, "n_phase": n_phase, "path": [round(s, 3) for s in path]}
                if rep == 0 and name == "control" and "honest" not in example:
                    example["honest"] = {"family_id": f, "n_phase": n_phase, "path": [round(s, 3) for s in path]}
        total_runs = family_runs_false + (leaked_runs if share > 0 else 0)
        res["scenarios"][name] = {
            "share_knowers": share,
            "by_h": [
                {
                    "h": h,
                    "detected": (detected[h] / leaked_runs) if leaked_runs else None,
                    "median_delay": M.median(delays[h]),
                    "false_quarantine": alarms_false[h] / max(1, total_runs),
                }
                for h in h_grid
            ],
        }
    res["example"] = example
    return res


# --------------------------------------------------------------------------- T7: калибровка


def calibration_section(pop: Population, *, replicates: int, ns: Sequence[int], sigma: float = 0.5) -> dict[str, Any]:
    """RMSE сложности: экспертный приор (σ = 0,5) → MAP-уточнение по n ответам (calibration.calibrate_family)."""
    seed = pop.cfg.seed
    scale = load_scale()
    spec = SPEC_LEAK
    by_id = {f.id: f for f in pop.bank}
    pools: dict[str, list[tuple[float, float]]] = {}
    for c in pop.candidates:
        if c.test is None or c.test.spec != spec:
            continue
        for r in c.test.responses:
            if r.is_pretest:
                continue
            meta = by_id[r.family_id]
            pools.setdefault(r.family_id, []).append((c.truth.module_theta.get(meta.module, c.truth.theta), r.loo_mean))
    fams = sorted(f for f, v in pools.items() if len(v) >= 10 and not by_id[f].is_anchor)
    errs: dict[int, list[float]] = {n: [] for n in ns}
    for rep in range(replicates):
        rng = stream(f"calib:{rep}", seed)
        for f in fams:
            meta = by_id[f]
            b_prior = meta.scale_param(spec, scale).b
            b_true = b_prior + rng.gauss(0.0, sigma)
            for n in ns:
                if n == 0:
                    errs[n].append(b_prior - b_true)
                    continue
                th, xs = [], []
                for _ in range(n):
                    th_true, loo = pools[f][rng.randrange(len(pools[f]))]
                    ex = SimulatedExaminee(theta=th_true, true_b={f: b_true})
                    xs.append(ex.answer(meta, spec, scale, rng))
                    th.append(loo)
                res = calibrate_family(meta, spec, th, xs, scale=scale, prior_sd=sigma)
                errs[n].append(res.b - b_true)
    return {
        "sigma_expert": sigma,
        "families": len(fams),
        "replicates": replicates,
        "curve": [{"n": n, "rmse": M.rmse(errs[n])} for n in ns],
    }


# --------------------------------------------------------------------------- теги модулей (✓ / ✓✓)


def tags_section(pop: Population) -> dict[str, Any]:
    """Точность и полнота тегов модулей против истинного уровня модуля (концепция §7.2, «теги ✓»).

    ✓ (confirmed или strong) утверждает «уровень модуля не ниже порога Junior» (cat.module_tag), ✓✓ — «не ниже
    порога итогового грейда». Истина — уровень модуля генератора. Полнота — по всем модулям blueprint теста,
    включая те, где заданий было мало («~»): кандидату важно, сколько его сильных модулей отмечено."""
    scale = load_scale()
    floor = scale.cut(load_cat_config().confirmed_floor_grade)
    tp = fp = fn = 0
    tp2 = fp2 = 0
    insufficient = total = 0
    for c in _matched(pop):
        if c.test.final_grade is None:
            continue
        cut_g = scale.cut(c.test.final_grade)
        for m, s in c.test.subscores.items():
            truth = c.truth.module_theta.get(m, c.truth.theta)
            tag = str(s["tag"])
            total += 1
            insufficient += tag == "insufficient"
            ok = tag in ("confirmed", "strong")
            if ok and truth >= floor:
                tp += 1
            elif ok:
                fp += 1
            elif truth >= floor:
                fn += 1
            if tag == "strong":
                if truth >= cut_g:
                    tp2 += 1
                else:
                    fp2 += 1
    return {
        "modules": total,
        "share_insufficient": insufficient / total if total else None,
        "check": M.prf_from_counts(tp, fp, fn),
        "strong_precision": tp2 / (tp2 + fp2) if tp2 + fp2 else None,
        "strong_n": tp2 + fp2,
        "concept_targets": {"precision": 0.90, "recall": 0.75, "preregistered": False},
    }


# --------------------------------------------------------------------------- T8: дискриминативность


def discrimination_section(pop: Population, min_n: int = 30, min_group: int = 10) -> dict[str, Any]:
    scale = load_scale()
    per_spec: dict[str, Any] = {}
    for spec in ("backend_python", "frontend_js"):
        data: dict[str, list[tuple[bool, float]]] = {}
        for c in pop.candidates:
            if c.test is None or c.test.spec != spec:
                continue
            for r in c.test.responses:
                if not r.is_pretest:
                    data.setdefault(r.family_id, []).append((r.correct, r.loo_mean))
        rows = []
        for f, v in sorted(data.items()):
            if len(v) < min_n:
                continue
            xs = [x for x, _ in v]
            th = [t for _, t in v]
            mid = [x for x, t in v if scale.grade_of(t) == "middle"]
            jun = [x for x, t in v if scale.grade_of(t) == "junior"]
            dp = (sum(mid) / len(mid) - sum(jun) / len(jun)) if len(mid) >= min_group and len(jun) >= min_group else None
            rows.append({"family_id": f, "n": len(v), "r_pb": point_biserial(xs, th), "delta_p_mid_jun": dp})
        rpb = [r["r_pb"] for r in rows]
        dps = [r["delta_p_mid_jun"] for r in rows if r["delta_p_mid_jun"] is not None]
        per_spec[spec] = {
            "families": len(rows),
            "r_pb_median": M.median(rpb),
            "share_r_pb_ge_020": M.mean([float(x >= 0.20) for x in rpb]),
            "flag_r_pb_lt_010": sorted(r["family_id"] for r in rows if r["r_pb"] < 0.10),
            "delta_p_median": M.median(dps),
            "share_delta_p_ge_020": M.mean([float(x >= 0.20) for x in dps]),
            "delta_p_families": len(dps),
        }
    rows_m = _matched(pop)
    aucs = {}
    for lo, hi in (("intern", "junior"), ("junior", "middle"), ("middle", "senior")):
        a = [c.test.theta for c in rows_m if c.truth.grade == lo]
        b = [c.test.theta for c in rows_m if c.truth.grade == hi]
        aucs[f"{lo}_{hi}"] = M.auc(b, a)
    th = [c.test.theta for c in rows_m]
    var = M.mean([(t - M.mean(th)) ** 2 for t in th]) if th else None
    rel = 1.0 - M.mean([c.test.se ** 2 for c in rows_m]) / var if var else None
    return {"by_spec": per_spec, "auc_adjacent": aucs, "reliability": rel}
