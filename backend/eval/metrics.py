"""Метрики валидации — чистые функции без зависимостей от движков.

Классификация: доля совпадений, κ Коэна, взвешенная квадратичная κ (QWK), AUC, macro-F1.
Ранжирование: P@k, nDCG@k, Recall@k, MRR, overlap@k, Жаккар. Статистика: корреляция Пирсона,
RMSE, перцентили, бутстреп-ДИ по брифам (в том числе парный). Всё детерминировано: бутстреп
получает явный генератор.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Hashable, Mapping, Sequence
from typing import Any

# --------------------------------------------------------------------------- базовая статистика


def mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def percentile(xs: Sequence[float], q: float) -> float | None:
    """Перцентиль с линейной интерполяцией (как numpy.percentile по умолчанию), q ∈ [0, 100]."""
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    pos = (len(s) - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    return float(s[lo] + (s[hi] - s[lo]) * (pos - lo))


def median(xs: Sequence[float]) -> float | None:
    return percentile(xs, 50.0)


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys):
        raise ValueError("Длины рядов различаются")
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return sxy / math.sqrt(sxx * syy)


def _ranks(xs: Sequence[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    return pearson(_ranks(xs), _ranks(ys))


def rmse(errors: Sequence[float]) -> float | None:
    if not errors:
        return None
    return math.sqrt(sum(e * e for e in errors) / len(errors))


# --------------------------------------------------------------------------- классификация


def agreement(a: Sequence[Hashable], b: Sequence[Hashable]) -> float | None:
    if len(a) != len(b):
        raise ValueError("Длины рядов различаются")
    if not a:
        return None
    return sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(a)


def cohen_kappa(a: Sequence[Hashable], b: Sequence[Hashable]) -> float | None:
    """κ Коэна для двух разметок одних и тех же объектов."""
    if len(a) != len(b):
        raise ValueError("Длины рядов различаются")
    n = len(a)
    if n == 0:
        return None
    po = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    labels = set(a) | set(b)
    pe = sum((sum(1 for x in a if x == lab) / n) * (sum(1 for y in b if y == lab) / n) for lab in labels)
    if abs(1.0 - pe) < 1e-12:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


def quadratic_weighted_kappa(a: Sequence[int], b: Sequence[int], k_min: int, k_max: int) -> float | None:
    """QWK для порядковых шкал (грейды как целые k_min..k_max)."""
    if len(a) != len(b):
        raise ValueError("Длины рядов различаются")
    n = len(a)
    if n == 0:
        return None
    k = k_max - k_min + 1
    obs = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b, strict=True):
        obs[x - k_min][y - k_min] += 1
    ha = [sum(obs[i]) for i in range(k)]
    hb = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = ((i - j) ** 2) / ((k - 1) ** 2) if k > 1 else 0.0
            num += w * obs[i][j]
            den += w * ha[i] * hb[j] / n
    if den <= 0:
        return 1.0 if num == 0 else 0.0
    return 1.0 - num / den


def auc(pos: Sequence[float], neg: Sequence[float]) -> float | None:
    """AUC = P(score положительного > score отрицательного), ничьи — наполовину (Манн — Уитни)."""
    if not pos or not neg:
        return None
    ranks = _ranks(list(pos) + list(neg))
    r_pos = sum(ranks[: len(pos)])
    u = r_pos - len(pos) * (len(pos) + 1) / 2.0
    return u / (len(pos) * len(neg))


def f1_scores(truth: Sequence[Hashable], pred: Sequence[Hashable]) -> dict[Hashable, dict[str, float]]:
    """Точность, полнота и F1 по каждому классу из truth ∪ pred."""
    if len(truth) != len(pred):
        raise ValueError("Длины рядов различаются")
    out: dict[Hashable, dict[str, float]] = {}
    for lab in sorted(set(truth) | set(pred), key=str):
        tp = sum(1 for t, p in zip(truth, pred, strict=True) if t == lab and p == lab)
        fp = sum(1 for t, p in zip(truth, pred, strict=True) if t != lab and p == lab)
        fn = sum(1 for t, p in zip(truth, pred, strict=True) if t == lab and p != lab)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out[lab] = {"precision": prec, "recall": rec, "f1": f1, "support": float(tp + fn)}
    return out


def macro_f1(truth: Sequence[Hashable], pred: Sequence[Hashable], labels: Sequence[Hashable] | None = None) -> float:
    """macro-F1 по классам истины (или по явному списку меток)."""
    per = f1_scores(truth, pred)
    labs = list(labels) if labels is not None else sorted(set(truth), key=str)
    if not labs:
        return 0.0
    return sum(per.get(lab, {"f1": 0.0})["f1"] for lab in labs) / len(labs)


def set_prf(truth: set[Any], pred: set[Any]) -> tuple[int, int, int]:
    """(tp, fp, fn) для множеств — для микро-F1 навыков брифа."""
    return len(truth & pred), len(pred - truth), len(truth - pred)


def prf_from_counts(tp: int, fp: int, fn: int) -> dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": p, "recall": r, "f1": f}


# --------------------------------------------------------------------------- ранжирование


def precision_at_k(ranked_rel: Sequence[int], k: int, threshold: int = 2) -> float:
    """Доля релевантных (rel ≥ threshold) в первых k; пустые места считаются нерелевантными."""
    if k <= 0:
        raise ValueError("k должно быть > 0")
    return sum(1 for r in ranked_rel[:k] if r >= threshold) / k


def dcg(rels: Sequence[int]) -> float:
    return sum((2.0**r - 1.0) / math.log2(i + 2) for i, r in enumerate(rels))


def ndcg_at_k(ranked_rel: Sequence[int], all_rel: Sequence[int], k: int) -> float:
    """nDCG@k с выигрышем 2^rel − 1; идеал — по всем кандидатам популяции, а не только по выдаче."""
    ideal = dcg(sorted(all_rel, reverse=True)[:k])
    if ideal <= 0:
        return 0.0
    return dcg(list(ranked_rel[:k])) / ideal


def recall_at_k(ranked_rel: Sequence[int], total_relevant: int, k: int, threshold: int = 2) -> float | None:
    if total_relevant <= 0:
        return None
    return sum(1 for r in ranked_rel[:k] if r >= threshold) / total_relevant


def reciprocal_rank(ranked_rel: Sequence[int], threshold: int = 2) -> float:
    for i, r in enumerate(ranked_rel):
        if r >= threshold:
            return 1.0 / (i + 1)
    return 0.0


def overlap_at_k(a: Sequence[Hashable], b: Sequence[Hashable], k: int) -> float:
    """|топ-k(a) ∩ топ-k(b)| / k."""
    return len(set(a[:k]) & set(b[:k])) / k


def kuncheva_index(a: Sequence[Hashable], b: Sequence[Hashable], k: int, n: int) -> float:
    """Индекс Кунчевой — overlap@k с поправкой на случайность: (r·n − k²) / (k·(n − k)), r = |топ-k(a) ∩ топ-k(b)|.

    0 — столько же совпадений, сколько у двух случайных k-подмножеств из n, 1 — полное совпадение
    (Kuncheva, «A stability index for feature selection», 2007). Обычный overlap@k в категории из сотен кандидатов
    сравнивать с 0,70 некорректно: случайный выбор дал бы k/n."""
    if not 0 < k < n:
        raise ValueError("нужно 0 < k < n")
    r = len(set(a[:k]) & set(b[:k]))
    return (r * n - k * k) / (k * (n - k))


def rank_biased_overlap(a: Sequence[Hashable], b: Sequence[Hashable], p: float = 0.9, depth: int = 50) -> float:
    """RBO (Webber, Moffat, Zobel, 2010), усечённый на глубине depth и нормированный на максимум: взвешивает совпадения
    у верха выдачи сильнее, вес позиции d — p^(d−1). 1 — одинаковые списки, 0 — нет общих элементов."""
    seen_a: set[Hashable] = set()
    seen_b: set[Hashable] = set()
    acc = norm = 0.0
    for d in range(1, depth + 1):
        if d <= len(a):
            seen_a.add(a[d - 1])
        if d <= len(b):
            seen_b.add(b[d - 1])
        w = p ** (d - 1)
        acc += w * len(seen_a & seen_b) / d
        norm += w
    return acc / norm


def jaccard(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


# --------------------------------------------------------------------------- бутстреп


def bootstrap_ci(
    values: Sequence[float],
    rng: random.Random,
    *,
    n_boot: int = 2000,
    alpha: float = 0.05,
    stat: Callable[[Sequence[float]], float] | None = None,
) -> tuple[float | None, float | None]:
    """Перцентильный бутстреп-ДИ среднего (или stat) по объектам (брифам)."""
    if not values:
        return None, None
    f = stat or (lambda xs: sum(xs) / len(xs))
    n = len(values)
    reps = []
    for _ in range(n_boot):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        reps.append(f(sample))
    return percentile(reps, 100 * alpha / 2), percentile(reps, 100 * (1 - alpha / 2))


def paired_bootstrap_diff(
    a: Sequence[float], b: Sequence[float], rng: random.Random, *, n_boot: int = 2000, alpha: float = 0.05
) -> dict[str, float | bool | None]:
    """Разница средних a − b по одним и тем же брифам с парным бутстрепом."""
    if len(a) != len(b):
        raise ValueError("Длины рядов различаются")
    diffs = [x - y for x, y in zip(a, b, strict=True)]
    lo, hi = bootstrap_ci(diffs, rng, n_boot=n_boot, alpha=alpha)
    m = mean(diffs)
    return {
        "diff": m,
        "ci_low": lo,
        "ci_high": hi,
        "significant": bool(lo is not None and hi is not None and (lo > 0 or hi < 0)),
    }


def summarize(values: Sequence[float], rng: random.Random, *, n_boot: int = 2000) -> dict[str, float | None]:
    """Среднее и 95% бутстреп-ДИ — форма, в которой метрики подбора уходят в JSON."""
    lo, hi = bootstrap_ci(values, rng, n_boot=n_boot)
    return {"mean": mean(values), "ci_low": lo, "ci_high": hi, "n": float(len(values))}


def counts(items: Sequence[Hashable]) -> dict[Hashable, int]:
    out: dict[Hashable, int] = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return out


def safe_round(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(float(x), nd)


def round_tree(obj: Any, nd: int = 4) -> Any:
    """Округлить все float во вложенной структуре (для компактного и стабильного JSON)."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, nd)
    if isinstance(obj, Mapping):
        return {str(k): round_tree(v, nd) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [round_tree(v, nd) for v in obj]
    return obj
