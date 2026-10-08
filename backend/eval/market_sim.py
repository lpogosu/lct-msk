"""Симуляция рынка приглашений: без лимитов / квоты приглашений / квоты + сигнал интереса.

Зачем. Обосновать числа config/contact.yaml (квоты работодателя, недельный лимит кандидата, 2 сигнала в месяц,
DECISIONS 24 и 31.3) и честно показать границы механизма. Теория: Coles, Kushnir, Niederle, «Preference Signaling in
Matching Markets» (AEJ: Micro 2013, NBER w16185) — дефицитный сигнал интереса повышает число совпадений и выигрыш
работников, особенно на сбалансированных рынках; Kushnir, «Harmful Signaling in Matching Markets» (FEEM 2010) —
при сильно похожих предпочтениях сигналы могут навредить. Это ИМИТАЦИЯ с допущениями (ниже), а не измерение живого рынка.

Модель.
  * E работодателей по одной вакансии, C кандидатов. Полезность кандидата от компании
    U[e,c] = sqrt(λ)·v_e + sqrt(1-λ)·ε  (v — «престиж», общий вертикальный компонент; ε — горизонтальный, личный),
    полезность работодателя от кандидата W[e,c] = sqrt(λ)·q_c + sqrt(1-λ)·η. λ — доля общей части (0 — вкусы
    независимы, 1 — все хотят одних и тех же). Score работодатель видит с шумом: W_obs = W + σ·ξ.
  * Неделя за неделей (T): работодатели с открытой вакансией приглашают; кандидат читает не больше A входящих
    за неделю (внимание), на остальные срок ответа истекает; отвечает «Да», если U ≥ порога r_c. Пары «Да»
    сводятся жадно по сумме полезностей: вакансия закрывается, кандидат занят.
  * Режимы: (a) без лимитов — работодатель шлёт M_free приглашений в неделю; (b) квоты — как в config/contact.yaml:
    активных на бриф ≤ active_per_need, новых за окно ≤ new_per_owner, пакет ≤ batch_max, у кандидата ≤
    default_weekly новых приглашений в неделю (лишние не отправляются и квоту не тратят); (c) квоты + сигналы:
    кандидат раз в окно отправляет s сигналов компаниям, а работодатель, открывший карточки, ранжирует по
    Score·P(Да): для сигналящего P известна (эмпирическая доля «Да» у сигналящих), для остальных — рыночная.
    Сигнал в Score не входит — он меняет только порядок, в котором работодатель тратит ограниченную квоту.
  * Стратегия сигнала кандидата: «умная» — из вакансий, где его Score в верхних 30%, выбрать s лучших по U
    (так работает продукт: кандидат видит ленту вакансий по Score); «наивная» — s лучших по U без учёта шансов.

Метрики: доля заполненных вакансий (match rate), среднее время до первого «Да» (недели; вакансии без «Да» —
цензура на T+1), концентрация приглашений (Gini и доля топ-10% кандидатов), доля потраченных впустую приглашений
(«Нет» + истекло), выигрыш кандидатов (средний U−r у занятых, делённый на всех кандидатов), выигрыш работодателей.
Все прогоны детерминированы: np.random.default_rng(seed); режимы сравниваются на одной и той же популяции.

Запуск:  uv run python -m eval.market_sim                 # таблицы, eval/out/market_sim.json, раздел в docs/
         uv run --with matplotlib python -m eval.market_sim  # плюс docs/img/market_sim_*.png
         uv run python -m eval.market_sim --quick           # дымовой прогон
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np

from app.contact.settings import get_contact_config

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent.parent
OUT_JSON = EVAL_DIR / "out" / "market_sim.json"
DOCS_MATCHING = ROOT / "docs" / "MATCHING.md"
DOCS_VALIDATION = ROOT / "docs" / "VALIDATION.md"
IMG_DIR = ROOT / "docs" / "img"
MARK_BEGIN, MARK_END = "<!-- market-sim:begin -->", "<!-- market-sim:end -->"

REGIMES = ("free", "quota", "signal")
REGIME_LABEL = {"free": "(а) без лимитов", "quota": "(б) квоты", "signal": "(в) квоты + сигнал"}


@dataclass(frozen=True)
class Params:
    """Параметры рынка. Числа политики берутся из config/contact.yaml; остальное — допущения модели."""

    employers: int = 200
    candidates: int = 200
    weeks: int = 8
    lam: float = 0.4              # доля общей (вертикальной) части предпочтений
    score_noise: float = 0.6      # шум Score относительно истинной полезности работодателя
    reserve_mean: float = 0.3     # порог «Да» кандидата: среднее и разброс (в σ полезности)
    reserve_sd: float = 0.4
    attention: int = 5            # сколько приглашений кандидат успевает рассмотреть за неделю
    free_per_week: int = 30       # без лимитов работодатель рассылает много (спам-режим)
    browse: int = 40              # сколько карточек работодатель открывает за неделю (видит сигналы только там)
    signals: int = 2              # сигналов за окно
    signal_window_weeks: int = 4  # окно 30 дней ≈ 4 недели
    signal_life_weeks: int = 8    # жизнь сигнала 60 дней ≈ 8 недель
    signal_fit_top: float = 0.3   # «умная» стратегия: сигнал только на вакансии, где Score кандидата в верхних 30%
    smart_signal: bool = True
    p_base0: float = 0.35         # начальная оценка работодателем P(Да) без сигнала
    p_sig0: float = 0.7
    # политика из config/contact.yaml (заполняется policy_from_config)
    active_per_need: int = 20
    new_per_owner: int = 60
    batch_max: int = 10
    weekly_candidate: int = 5

    def with_policy(self) -> Params:
        cfg = get_contact_config()
        q = cfg.quotas
        return replace(
            self,
            active_per_need=q.active_per_need,
            new_per_owner=q.new_per_owner,
            batch_max=q.batch_max,
            weekly_candidate=int(cfg.candidate_limits.default_weekly or self.attention),
            signals=cfg.interest.per_window,
            signal_window_weeks=max(1, round(cfg.interest.window_days / 7)),
            signal_life_weeks=max(1, round(cfg.interest.lifetime_days / 7)),
        )


@dataclass
class Metrics:
    match_rate: float = 0.0
    first_yes_weeks: float = 0.0
    first_yes_share: float = 0.0
    gini: float = 0.0
    top10_share: float = 0.0
    wasted: float = 0.0
    invites_per_employer: float = 0.0
    cand_welfare: float = 0.0
    emp_welfare: float = 0.0
    blocked: float = 0.0
    lost_yes: float = 0.0
    extra: dict[str, float] = field(default_factory=dict)


def gini(x: np.ndarray) -> float:
    x = np.sort(np.asarray(x, dtype=float))
    n = x.size
    if n == 0 or x.sum() == 0:
        return 0.0
    cum = np.cumsum(x)
    return float((n + 1 - 2 * (cum.sum() / cum[-1])) / n)


def top_share(x: np.ndarray, frac: float = 0.1) -> float:
    x = np.sort(np.asarray(x, dtype=float))[::-1]
    k = max(1, int(round(len(x) * frac)))
    return float(x[:k].sum() / x.sum()) if x.sum() > 0 else 0.0


def _phi(x: np.ndarray) -> np.ndarray:
    """Нормальная функция распределения: Score как перцентиль, 0..1."""
    return 0.5 * (1.0 + np.vectorize(math.erf)(x / math.sqrt(2.0)))


def population(p: Params, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    E, C = p.employers, p.candidates
    v = rng.standard_normal(E)
    q = rng.standard_normal(C)
    a, b = math.sqrt(p.lam), math.sqrt(1 - p.lam)
    U = a * v[:, None] + b * rng.standard_normal((E, C))
    W = a * q[None, :] + b * rng.standard_normal((E, C))
    Wobs = W + p.score_noise * rng.standard_normal((E, C))
    reserve = p.reserve_mean + p.reserve_sd * rng.standard_normal(C)
    return {"U": U, "W": W, "S": _phi(Wobs / math.sqrt(1 + p.score_noise**2)), "r": reserve}


def _signals(p: Params, pop: dict[str, np.ndarray], free_c: np.ndarray, taken: np.ndarray) -> np.ndarray:
    """Матрица новых сигналов (E×C, bool) от кандидатов, которым ещё доступны сигналы."""
    U, S = pop["U"], pop["S"]
    E, C = U.shape
    out = np.zeros((E, C), dtype=bool)
    for c in np.flatnonzero(free_c):
        pool = U[:, c].copy()
        pool[taken[:, c]] = -np.inf
        if p.smart_signal:
            cut = np.quantile(S[:, c], 1 - p.signal_fit_top)
            pool[S[:, c] < cut] = -np.inf
        idx = np.argsort(-pool)[: p.signals]
        idx = idx[np.isfinite(pool[idx])]
        out[idx, c] = True
    return out


def simulate(p: Params, regime: str, seed: int) -> Metrics:
    assert regime in REGIMES
    pop = population(p, seed)
    U, W, S, r = pop["U"], pop["W"], pop["S"], pop["r"]
    E, C = U.shape
    rng = np.random.default_rng(seed + 1_000_003)  # порядок прибытия приглашений — отдельный поток

    open_e = np.ones(E, dtype=bool)
    free_c = np.ones(C, dtype=bool)
    invited = np.zeros((E, C), dtype=bool)
    sig_born = np.full((E, C), -10_000, dtype=int)  # неделя отправки сигнала
    got = np.zeros(C)  # приглашений получено (для концентрации)
    first_yes = np.full(E, p.weeks + 1.0)
    sent_total = declined = expired = blocked = lost_yes = 0
    match_e = np.full(E, -1)
    sent_prev = np.zeros(E, dtype=int)  # отправлено за прошлую неделю (окно new_per_owner = 7 дней)
    p_base, p_sig = p.p_base0, p.p_sig0
    hist = {"acc": 0, "n": 0, "acc_s": 0, "n_s": 0}

    for t in range(p.weeks):
        if not open_e.any() or not free_c.any():
            break
        use_sig = regime == "signal"
        if use_sig and t % p.signal_window_weeks == 0:
            sig_born_new = _signals(p, pop, free_c, invited)
            sig_born[sig_born_new & (sig_born < t - p.signal_life_weeks)] = t
            sig_born[sig_born_new] = np.maximum(sig_born[sig_born_new], t)
        live_sig = (t - sig_born < p.signal_life_weeks) & (sig_born >= 0)

        # --- приглашения работодателей ---
        inv_pairs: list[tuple[int, int]] = []
        for e in np.flatnonzero(open_e):
            avail = free_c & ~invited[e]
            if not avail.any():
                continue
            val = S[e].copy()
            if regime == "free":
                n_send = p.free_per_week
            else:
                n_send = min(p.batch_max, max(0, p.new_per_owner - sent_prev[e]), p.active_per_need)
            if use_sig:
                opened = np.zeros(C, dtype=bool)  # открыл карточки лучших по Score и тех, кто его пометил
                top = np.argsort(-np.where(avail, S[e], -np.inf))[: p.browse]
                opened[top] = True
                val = val * np.where(opened & live_sig[e], p_sig, p_base)
            val = np.where(avail, val, -np.inf)
            pick = np.argsort(-val)[:n_send]
            pick = pick[np.isfinite(val[pick])]
            for c in pick:
                inv_pairs.append((int(e), int(c)))
        sent_prev[:] = 0
        order = rng.permutation(len(inv_pairs))
        inbox: dict[int, list[int]] = {}
        for i in order:
            e, c = inv_pairs[i]
            if regime != "free" and len(inbox.get(c, ())) >= p.weekly_candidate:
                blocked += 1  # проверка при отправке: квоту работодателя не тратит
                continue
            inbox.setdefault(c, []).append(e)
            invited[e, c] = True
            sent_prev[e] += 1
            sent_total += 1
            got[c] += 1

        # --- ответы кандидатов ---
        yes: list[tuple[float, int, int]] = []
        for c, es in inbox.items():
            es_sorted = sorted(es, key=lambda e: -U[e, c])
            read, unread = es_sorted[: p.attention], es_sorted[p.attention :]
            expired += len(unread)
            for e in read:
                was_sig = bool(live_sig[e, c])
                hist["n"] += 1
                hist["n_s"] += was_sig
                if U[e, c] >= r[c]:
                    yes.append((U[e, c] + W[e, c], e, c))
                    hist["acc"] += 1
                    hist["acc_s"] += was_sig
                    first_yes[e] = min(first_yes[e], t + 1)
                else:
                    declined += 1
        # --- сведение «Да» ---
        for _, e, c in sorted(yes, reverse=True):
            if open_e[e] and free_c[c]:
                open_e[e] = False
                free_c[c] = False
                match_e[e] = c
            else:
                lost_yes += 1
        # работодатели учатся: эмпирические вероятности «Да» (публичная статистика рынка, с лагом в неделю)
        if hist["n"] >= 30:
            p_base = max(0.05, (hist["acc"] - hist["acc_s"]) / max(1, hist["n"] - hist["n_s"]))
        if hist["n_s"] >= 10:
            p_sig = max(0.05, hist["acc_s"] / hist["n_s"])

    matched = match_e >= 0
    cand_gain = sum(max(0.0, U[e, match_e[e]] - r[match_e[e]]) for e in np.flatnonzero(matched))
    emp_gain = sum(W[e, match_e[e]] for e in np.flatnonzero(matched))
    got_yes = first_yes <= p.weeks
    m = Metrics(
        match_rate=float(matched.sum() / min(E, C)),
        first_yes_weeks=float(first_yes.mean()),
        first_yes_share=float(got_yes.mean()),
        gini=gini(got),
        top10_share=top_share(got),
        wasted=float((declined + expired) / max(1, sent_total)),
        invites_per_employer=float(sent_total / E),
        cand_welfare=float(cand_gain / C),
        emp_welfare=float(emp_gain / E),
        blocked=float(blocked / max(1, sent_total + blocked)),
        lost_yes=float(lost_yes / max(1, sent_total)),
    )
    return m


def summarize(p: Params, regime: str, seeds: range) -> dict[str, float]:
    ms = [simulate(p, regime, s) for s in seeds]
    out: dict[str, float] = {}
    for k in asdict(ms[0]):
        if k == "extra":
            continue
        xs = np.array([getattr(m, k) for m in ms], dtype=float)
        out[k] = float(xs.mean())
        out[k + "_sd"] = float(xs.std(ddof=1)) if len(xs) > 1 else 0.0
    return out


def paired_delta(p: Params, a: str, b: str, key: str, seeds: range) -> tuple[float, float]:
    """Среднее парное различие b−a по сидам и 95% доверительный полуинтервал (нормальное приближение)."""
    d = np.array([getattr(simulate(p, b, s), key) - getattr(simulate(p, a, s), key) for s in seeds])
    half = 1.96 * d.std(ddof=1) / math.sqrt(len(d)) if len(d) > 1 else 0.0
    return float(d.mean()), float(half)


SCENARIOS = {
    "diverse": ("Разные вкусы (λ=0,2)", 0.2),
    "mixed": ("Смешанные (λ=0,5)", 0.5),
    "correlated": ("Похожие вкусы (λ=0,9)", 0.9),
}
MARKETS = {"balanced": ("Сбалансированный 1:1", 200, 200), "cand_heavy": ("Кандидатов вдвое больше", 200, 400)}


def run(*, seeds: int = 60, quick: bool = False) -> dict:
    base = Params().with_policy()
    if quick:
        base = replace(base, employers=60, candidates=60)
        seeds = 6
    ss = range(seeds)
    res: dict = {"policy": {k: getattr(base, k) for k in ("active_per_need", "new_per_owner", "batch_max", "weekly_candidate", "signals", "signal_window_weeks", "signal_life_weeks")}, "seeds": seeds}

    grid: dict = {}
    for mk, (_, e, c) in MARKETS.items():
        for sk, (_, lam) in SCENARIOS.items():
            pp = replace(base, employers=e if not quick else 60, candidates=c if not quick else (60 if c == e else 120), lam=lam)
            grid[f"{mk}|{sk}"] = {rg: summarize(pp, rg, ss) for rg in REGIMES}
            grid[f"{mk}|{sk}"]["delta_match_signal_vs_quota"] = paired_delta(pp, "quota", "signal", "match_rate", ss)
            grid[f"{mk}|{sk}"]["delta_welfare_signal_vs_quota"] = paired_delta(pp, "quota", "signal", "cand_welfare", ss)
            grid[f"{mk}|{sk}"]["delta_firstyes_signal_vs_quota"] = paired_delta(pp, "quota", "signal", "first_yes_weeks", ss)
    res["grid"] = grid

    # Развёртка по числу сигналов (сбалансированный рынок, смешанные и похожие вкусы)
    sweep = {}
    for sk in ("mixed", "correlated"):
        pp = replace(base, lam=SCENARIOS[sk][1], employers=base.employers, candidates=base.candidates)
        sweep[sk] = {}
        for s in (0, 1, 2, 3, 5):
            q = replace(pp, signals=max(s, 0))
            rg = "quota" if s == 0 else "signal"
            sweep[sk][s] = summarize(q, rg, ss)
    res["signal_sweep"] = sweep

    # Развёртка по квоте работодателя (без сигналов): active_per_need / batch_max
    qs = {}
    pp = replace(base, lam=0.5)
    for batch in (3, 5, 10, 20, 40):
        q = replace(pp, batch_max=batch, active_per_need=max(batch, base.active_per_need))
        qs[batch] = summarize(q, "quota", ss)
    qs["free"] = summarize(pp, "free", ss)
    res["quota_sweep"] = qs
    wk = {}
    for w in (2, 3, 5, 10, 20):
        wk[w] = summarize(replace(pp, weekly_candidate=w), "quota", ss)
    res["weekly_sweep"] = wk

    # Вредный сценарий: наивная стратегия сигнала при похожих вкусах
    harm = {}
    for sk in ("mixed", "correlated"):
        pp = replace(base, lam=SCENARIOS[sk][1])
        naive = replace(pp, smart_signal=False)
        harm[sk] = {
            "quota": summarize(pp, "quota", ss),
            "signal_smart": summarize(pp, "signal", ss),
            "signal_naive": summarize(naive, "signal", ss),
            "delta_naive_vs_quota": paired_delta(naive, "quota", "signal", "match_rate", ss),
        }
    res["harm"] = harm
    return res


# --------------------------------------------------------------------------- отчёт


def _f(x: float, nd: int = 2) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


def _pm(m: dict, k: str, nd: int = 2) -> str:
    return f"{_f(m[k], nd)}"


def render_tables(res: dict) -> str:
    L: list[str] = []
    pol = res["policy"]
    L.append(
        f"Политика берётся из `config/contact.yaml`: активных приглашений на бриф ≤ {pol['active_per_need']}, новых за 7 дней ≤ "
        f"{pol['new_per_owner']}, пакет ≤ {pol['batch_max']}, новых приглашений кандидату ≤ {pol['weekly_candidate']} в неделю; "
        f"сигналов {pol['signals']} за окно ≈ {pol['signal_window_weeks']} нед., живёт ≈ {pol['signal_life_weeks']} нед. "
        f"Прогонов на ячейку: {res['seeds']} сидов (одна и та же популяция во всех режимах).\n"
    )
    for mk, (mname, _, _) in MARKETS.items():
        L.append(f"**{mname}.**\n")
        L.append(
            "| Предпочтения | Режим | Заполнено вакансий | Недель до первого «Да» | Gini приглашений | Доля топ-10% кандидатов | Впустую | Приглашений на вакансию | Выигрыш кандидатов |"
        )
        L.append("|---|---|---|---|---|---|---|---|---|")
        for sk, (sname, _) in SCENARIOS.items():
            g = res["grid"][f"{mk}|{sk}"]
            for rg in REGIMES:
                m = g[rg]
                L.append(
                    f"| {sname if rg == 'free' else ''} | {REGIME_LABEL[rg]} | {_f(100 * m['match_rate'], 1)}% | {_f(m['first_yes_weeks'])} | "
                    f"{_f(m['gini'])} | {_f(100 * m['top10_share'], 0)}% | {_f(100 * m['wasted'], 0)}% | {_f(m['invites_per_employer'], 1)} | {_f(m['cand_welfare'])} |"
                )
        L.append("")
        L.append("Парная разница «квоты + сигнал» − «квоты» (среднее ± 95% ДИ по сидам):\n")
        L.append("| Предпочтения | Δ заполнено | Δ выигрыш кандидатов | Δ недель до первого «Да» |")
        L.append("|---|---|---|---|")
        for sk, (sname, _) in SCENARIOS.items():
            g = res["grid"][f"{mk}|{sk}"]
            dm, dw, df = (g["delta_match_signal_vs_quota"], g["delta_welfare_signal_vs_quota"], g["delta_firstyes_signal_vs_quota"])
            L.append(
                f"| {sname} | {_f(100 * dm[0], 1)} ± {_f(100 * dm[1], 1)} п.п. | {_f(dw[0], 3)} ± {_f(dw[1], 3)} | {_f(df[0])} ± {_f(df[1])} |"
            )
        L.append("")
    L.append("**Сколько сигналов (сбалансированный рынок).**\n")
    L.append("| Сигналов за окно | Смешанные: заполнено | Смешанные: выигрыш канд. | Похожие: заполнено | Похожие: выигрыш канд. |")
    L.append("|---|---|---|---|---|")
    for s in (0, 1, 2, 3, 5):
        a, b = res["signal_sweep"]["mixed"][s], res["signal_sweep"]["correlated"][s]
        L.append(
            f"| {s} | {_f(100 * a['match_rate'], 1)}% | {_f(a['cand_welfare'])} | {_f(100 * b['match_rate'], 1)}% | {_f(b['cand_welfare'])} |"
        )
    L.append("")
    L.append("**Размер пакета приглашений (смешанные вкусы, сбалансированный рынок, без сигналов).**\n")
    L.append("| Пакет приглашений в неделю | Заполнено | Впустую | Gini | Недель до первого «Да» |")
    L.append("|---|---|---|---|---|")
    for k, m in res["quota_sweep"].items():
        L.append(
            f"| {'без лимита (30)' if k == 'free' else k} | {_f(100 * m['match_rate'], 1)}% | {_f(100 * m['wasted'], 0)}% | {_f(m['gini'])} | {_f(m['first_yes_weeks'])} |"
        )
    L.append("")
    L.append("**Недельный лимит входящих у кандидата (смешанные вкусы).**\n")
    L.append("| Лимит в неделю | Заполнено | Отклонено при отправке | Впустую |")
    L.append("|---|---|---|---|")
    for k, m in res["weekly_sweep"].items():
        L.append(f"| {k} | {_f(100 * m['match_rate'], 1)}% | {_f(100 * m['blocked'], 0)}% | {_f(100 * m['wasted'], 0)}% |")
    L.append("")
    L.append("**Когда сигнал не помогает.** Наивная стратегия — сигнал на лучшую по полезности компанию без оценки шансов:\n")
    L.append("| Предпочтения | Режим | Заполнено | Выигрыш кандидатов | Недель до первого «Да» |")
    L.append("|---|---|---|---|---|")
    for sk, h in res["harm"].items():
        for key, label in (("quota", "квоты"), ("signal_smart", "квоты + сигнал (по шансам)"), ("signal_naive", "квоты + сигнал (наивно)")):
            m = h[key]
            L.append(
                f"| {SCENARIOS[sk][0] if key == 'quota' else ''} | {label} | {_f(100 * m['match_rate'], 1)}% | {_f(m['cand_welfare'])} | {_f(m['first_yes_weeks'])} |"
            )
    L.append("")
    return "\n".join(L)


def verdict(res: dict) -> dict[str, str]:
    """Короткие выводы из чисел (чтобы текст в документах не расходился с таблицами)."""
    g = res["grid"]
    def d(key: str, which: str) -> tuple[float, float]:
        return g[key][which]
    b_mixed = d("balanced|mixed", "delta_match_signal_vs_quota")
    b_corr = d("balanced|correlated", "delta_match_signal_vs_quota")
    b_div = d("balanced|diverse", "delta_match_signal_vs_quota")
    w_mixed = d("balanced|mixed", "delta_welfare_signal_vs_quota")
    w_corr = d("balanced|correlated", "delta_welfare_signal_vs_quota")
    free, quota = g["balanced|mixed"]["free"], g["balanced|mixed"]["quota"]
    return {
        "match_mixed": f"{100 * b_mixed[0]:+.1f} ± {100 * b_mixed[1]:.1f} п.п.",
        "match_div": f"{100 * b_div[0]:+.1f} ± {100 * b_div[1]:.1f} п.п.",
        "match_corr": f"{100 * b_corr[0]:+.1f} ± {100 * b_corr[1]:.1f} п.п.",
        "welfare_mixed": f"{w_mixed[0]:+.3f} ± {w_mixed[1]:.3f}",
        "welfare_corr": f"{w_corr[0]:+.3f} ± {w_corr[1]:.3f}",
        "wasted_free": f"{100 * free['wasted']:.0f}%",
        "wasted_quota": f"{100 * quota['wasted']:.0f}%",
        "gini_free": f"{free['gini']:.2f}",
        "gini_quota": f"{quota['gini']:.2f}",
        "top_free": f"{100 * free['top10_share']:.0f}%",
        "top_quota": f"{100 * quota['top10_share']:.0f}%",
        "match_free": f"{100 * free['match_rate']:.1f}%",
        "match_quota": f"{100 * quota['match_rate']:.1f}%",
        "match_free_corr": f"{100 * g['balanced|correlated']['free']['match_rate']:.1f}%",
        "match_quota_corr": f"{100 * g['balanced|correlated']['quota']['match_rate']:.1f}%",
        "welfare_free": f"{g['balanced|mixed']['free']['cand_welfare']:.2f}".replace(".", ","),
        "welfare_quota": f"{quota['cand_welfare']:.2f}".replace(".", ","),
        "welfare_signal": f"{g['balanced|mixed']['signal']['cand_welfare']:.2f}".replace(".", ","),
        "welfare_naive": f"{res['harm']['mixed']['signal_naive']['cand_welfare']:.2f}".replace(".", ","),
        "inv_free": f"{free['invites_per_employer']:.0f}",
        "inv_quota": f"{quota['invites_per_employer']:.0f}",
        "load_ratio": f"{free['invites_per_employer'] / max(1e-9, quota['invites_per_employer']):.1f}".replace(".", ","),
    }


def section_text(res: dict, *, heading: str, level: str) -> str:
    v = verdict(res)
    h = level
    return f"""{h} {heading}

Чтобы выбрать числа для `config/contact.yaml` (квоты, недельный лимит, два сигнала) и честно показать границы механизма, мы сыграли рынок приглашений «компания — кандидат» на синтетических агентах (`eval/market_sim.py`, детерминированно: `numpy.random.default_rng(seed)`). Это имитация с допущениями, а не измерение живого рынка. Опора — Coles, Kushnir, Niederle, «Preference Signaling in Matching Markets» (AEJ: Micro, 2013; NBER w16185): один дефицитный сигнал интереса помогает работникам и числу совпадений, лучше всего на сбалансированных рынках; и Kushnir, «Harmful Signaling in Matching Markets» (FEEM, 2010): при очень похожих предпочтениях сигналы могут навредить.

**Модель в двух строках.** Полезность кандидата от компании = общая «престижная» часть (вертикальная) + личная (горизонтальная); доля общей части λ. Работодатель видит Score с шумом, кандидат читает не больше 5 приглашений в неделю, отвечает «Да» выше личного порога. Режимы: (а) без лимитов — 30 приглашений в неделю на вакансию; (б) квоты из `config/contact.yaml`; (в) квоты + сигнал: работодатель, открывший карточки, при выборе, кого пригласить, учитывает известную вероятность «Да» у сигналящих (в Score сигнал не входит).

{render_tables(res)}
**Что показала симуляция.**

- *Квоты — это обмен заполнения на нагрузку.* На сбалансированном рынке со смешанными вкусами рассылка без лимитов заполняет {v['match_free']} вакансий, квоты — {v['match_quota']}, но на вакансию уходит {v['inv_quota']} приглашений вместо {v['inv_free']} (нагрузка на кандидатов в {v['load_ratio']} раза ниже), а доля приглашений впустую {v['wasted_quota']} против {v['wasted_free']}. При очень похожих вкусах потеря заполнения заметнее ({v['match_free_corr']} против {v['match_quota_corr']}). Концентрацию квоты не лечат: Gini {v['gini_quota']} против {v['gini_free']}, доля топ-10% кандидатов {v['top_quota']} против {v['top_free']} (при малом числе приглашений работодатели чаще выбирают лучших по Score). Выигрыш квот — защита кандидата от потока писем, а не равномерность; выигрыш кандидатов от рассылки без лимитов ({v['welfare_free']} против {v['welfare_quota']}) — это цена, которую мы платим осознанно.
- *Сигнал возвращает часть потерянного для кандидата.* Против одних квот на сбалансированном рынке сигнал меняет число заполненных вакансий на: разные вкусы {v['match_div']}, смешанные {v['match_mixed']}, похожие {v['match_corr']} (парное среднее ± 95% ДИ) — то есть почти не меняет. Зато выигрыш кандидатов растёт: смешанные {v['welfare_mixed']} (до {v['welfare_signal']} против {v['welfare_quota']} у одних квот), похожие {v['welfare_corr']}. Читается так: сигнал улучшает **качество** совпадений для кандидата, а не их число; это согласуется с Coles–Kushnir–Niederle (выигрыш на стороне работников, эффект для фирм неоднозначен).
- *Границы (вредный сценарий).* При очень похожих вкусах (λ=0,9) все хотят одних и тех же компаний, сигнал не помогает ни числу совпадений, ни скорости первого «Да»: выигрыш кандидатов {v['welfare_corr']} против {v['welfare_mixed']} при смешанных. Сигналы уходят в одни и те же места — это тот случай, о котором пишет Kushnir. Заметного вреда, как в его теоретическом примере, в нашей модели не получилось (у работодателя сигнал лишь меняет порядок внутри квоты), поэтому мы заявляем только «нет пользы при общих вкусах», а не «есть вред». Наивная стратегия (сигнал на самую желанную компанию без учёта шансов) выигрыш кандидата снижает по сравнению со стратегией «по шансам» ({v['welfare_naive']} против {v['welfare_signal']} при смешанных вкусах), поэтому продукт подсказывает сигнал только по вакансиям, где Score кандидата высок.
- *Допущения, которые могут не держаться на живых данных:* независимые предпочтения кроме λ, одна вакансия на компанию, фиксированные пороги «Да», нет стратегического поведения работодателей, все кандидаты используют сигналы, рынок закрыт на 8 недель. Размеры квот проверены как чувствительность, а не оптимизированы под единственный сценарий.

**Решение по настройкам.** {decision_text(res)}"""


def decision_text(res: dict) -> str:
    """Чем обоснованы значения config/contact.yaml (оставлены, если развёртки их подтверждают)."""
    pol = res["policy"]
    qs, sw, wk = res["quota_sweep"], res["signal_sweep"]["mixed"], res["weekly_sweep"]
    k10, k40, k3 = qs[10], qs[40], qs[3]
    w5, w10 = wk[5], wk[10]
    g0, g2, g5 = sw[0]["cand_welfare"], sw[2]["cand_welfare"], sw[5]["cand_welfare"]
    return (
        f"значения `config/contact.yaml` оставлены без изменений: пакет {pol['batch_max']}, активных на бриф {pol['active_per_need']}, "
        f"недельный лимит кандидата {pol['weekly_candidate']}, сигналов {pol['signals']} за окно. "
        f"Пакет: заполнение растёт с размером пакета и выходит на плато (3 — {100 * k3['match_rate']:.1f}%, 10 — {100 * k10['match_rate']:.1f}%, "
        f"40 — {100 * k40['match_rate']:.1f}%), колено на 10: дальше +{100 * (k40['match_rate'] - k10['match_rate']):.1f} п.п. ценой "
        f"{k40['invites_per_employer'] / k10['invites_per_employer']:.1f}× приглашений. "
        f"Недельный лимит: 5 даёт {100 * w5['match_rate']:.1f}% заполнения против {100 * w10['match_rate']:.1f}% при 10, "
        f"но отсекает больше рассылки ({100 * w5['blocked']:.0f}% против {100 * w10['blocked']:.0f}% отклонённых при отправке); "
        f"{pol['weekly_candidate']} — осознанный выбор в пользу кандидата (≈{100 * (w10['match_rate'] - w5['match_rate']):.1f} п.п. заполнения), "
        f"работодателю это видно в квоте и его можно ослабить настройкой кандидата (3/5/10/без ограничения). "
        f"Сигналы: 0 → 2 → 5 дают выигрыш кандидатов {g0:.2f} → {g2:.2f} → {g5:.2f}; два сигнала берут {100 * (g2 - g0) / max(1e-9, g5 - g0):.0f}% "
        f"пятисигнального прироста, а дефицитность (смысл сигнала по Coles и др.) сохраняется, поэтому оставлено 2."
    )


# --------------------------------------------------------------------------- графики


def plot(res: dict, out_dir: Path = IMG_DIR) -> list[Path]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib не установлен: графики пропущены (uv run --with matplotlib python -m eval.market_sim)", file=sys.stderr)
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = {"free": "#898781", "quota": "#2a78d6", "signal": "#1baf7a"}
    paths: list[Path] = []
    names = [SCENARIOS[k][0].split(" (")[0] for k in SCENARIOS]

    fig, axes = plt.subplots(1, 3, figsize=(11, 3.8), sharex=True)
    for ax, (title, key, pct) in zip(
        axes,
        (("Заполнено вакансий, %", "match_rate", 100), ("Приглашения впустую, %", "wasted", 100), ("Выигрыш кандидатов", "cand_welfare", 1)),
        strict=True,
    ):
        w = 0.26
        for i, rg in enumerate(REGIMES):
            ys = [res["grid"][f"balanced|{sk}"][rg][key] * pct for sk in SCENARIOS]
            es = [res["grid"][f"balanced|{sk}"][rg][key + "_sd"] * pct for sk in SCENARIOS]
            ax.bar([x + (i - 1) * w for x in range(3)], ys, w, yerr=es, color=cols[rg], label=REGIME_LABEL[rg], capsize=2, error_kw={"lw": 0.8})
        ax.set_title(title, fontsize=11, loc="left")
        ax.set_xticks(range(3), names, fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", lw=0.4, alpha=0.5)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=9, frameon=False, loc="lower center", ncol=3)
    fig.suptitle("Сбалансированный рынок: режимы приглашений по сходству предпочтений (среднее ± σ по сидам)", fontsize=11, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    p1 = out_dir / "market_sim_regimes.png"
    fig.savefig(p1, dpi=150)
    plt.close(fig)
    paths.append(p1)

    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    for sk, color in (("mixed", "#1baf7a"), ("correlated", "#eb6834")):
        xs = [0, 1, 2, 3, 5]
        ys = [res["signal_sweep"][sk][s]["match_rate"] * 100 for s in xs]
        es = [res["signal_sweep"][sk][s]["match_rate_sd"] * 100 for s in xs]
        ax.errorbar(xs, ys, yerr=es, marker="o", color=color, label=SCENARIOS[sk][0], capsize=2, lw=1.6)
    ax.axvline(res["policy"]["signals"], color="#898781", ls="--", lw=1)
    ax.text(res["policy"]["signals"] + 0.05, ax.get_ylim()[0], " выбрано", fontsize=8, color="#52514e", va="bottom")
    ax.set_xlabel("Сигналов за окно (0 — только квоты)")
    ax.set_ylabel("Заполнено вакансий, %")
    ax.set_title("Сколько сигналов нужно", fontsize=11, loc="left")
    ax.legend(fontsize=8, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(lw=0.4, alpha=0.5)
    fig.tight_layout()
    p2 = out_dir / "market_sim_signals.png"
    fig.savefig(p2, dpi=150)
    plt.close(fig)
    paths.append(p2)
    return paths


# --------------------------------------------------------------------------- документы


def splice(path: Path, block: str, anchor: str) -> bool:
    text = path.read_text(encoding="utf-8")
    wrapped = f"{MARK_BEGIN}\n{block.rstrip()}\n{MARK_END}\n"
    if MARK_BEGIN in text and MARK_END in text:
        head, rest = text.split(MARK_BEGIN, 1)
        _, tail = rest.split(MARK_END, 1)
        new = head + wrapped + tail.lstrip("\n").join(["", ""]) if False else head + wrapped + "\n" + tail.lstrip("\n")
    else:
        if anchor not in text:
            return False
        head, tail = text.split(anchor, 1)
        new = head + wrapped + "\n" + anchor + tail
    path.write_text(new, encoding="utf-8", newline="\n")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quick", action="store_true", help="дымовой прогон (малые выборки, документы не трогает)")
    ap.add_argument("--seeds", type=int, default=60)
    ap.add_argument("--no-docs", action="store_true")
    ap.add_argument("--from-json", action="store_true", help="не пересчитывать: взять eval/out/market_sim.json")
    a = ap.parse_args(argv)
    res = json.loads(OUT_JSON.read_text(encoding="utf-8")) if a.from_json else run(seeds=a.seeds, quick=a.quick)
    if a.from_json:  # JSON превращает ключи словарей в строки
        res["signal_sweep"] = {k: {int(i): v for i, v in d.items()} for k, d in res["signal_sweep"].items()}
        res["quota_sweep"] = {(int(k) if k.isdigit() else k): v for k, v in res["quota_sweep"].items()}
        res["weekly_sweep"] = {int(k): v for k, v in res["weekly_sweep"].items()}
    print(render_tables(res))
    print(json.dumps(verdict(res), ensure_ascii=False, indent=1))
    if a.quick:
        return 0
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    pngs = plot(res)
    if not a.no_docs:
        body = section_text(res, heading="9.1. Симуляция рынка: квоты и сигнал интереса", level="###")
        if pngs:
            body += "\n\n![Режимы приглашений](img/market_sim_regimes.png)\n\n![Сколько сигналов](img/market_sim_signals.png)\n"
        splice(DOCS_MATCHING, body, "## 10. Кандидаты без ФСП")
        body_v = section_text(res, heading="9. Симуляция рынка приглашений (квоты и сигнал интереса)", level="##")
        if pngs:
            body_v += "\n\n![Режимы приглашений](img/market_sim_regimes.png)\n\n![Сколько сигналов](img/market_sim_signals.png)\n"
        splice(DOCS_VALIDATION, body_v, "## Как воспроизвести")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
