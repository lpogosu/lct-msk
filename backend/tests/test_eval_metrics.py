"""Метрики валидации, лексические бейзлайны, SVG-графики и плитки отчёта — на игрушечных входах."""

from __future__ import annotations

import math
import random
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from eval import charts
from eval import metrics as M
from eval.report import _passes, tiles
from eval.textsearch import BM25, TfIdf, rank_positions, tokenize

# --------------------------------------------------------------------------- классификация


def test_agreement_and_kappa_perfect_and_chance():
    a = [0, 1, 1, 0, 1, 0]
    assert M.agreement(a, a) == 1.0
    assert M.cohen_kappa(a, a) == pytest.approx(1.0)
    # Классический пример: po = 0,7, pe = 0,5 → κ = 0,4
    x = [1] * 35 + [1] * 15 + [0] * 15 + [0] * 35
    y = [1] * 35 + [0] * 15 + [1] * 15 + [0] * 35
    assert M.agreement(x, y) == pytest.approx(0.7)
    assert M.cohen_kappa(x, y) == pytest.approx(0.4)


def test_qwk_perfect_reversed_and_partial():
    a = [-1, 0, 1, 2, 3]
    assert M.quadratic_weighted_kappa(a, a, -1, 3) == pytest.approx(1.0)
    assert M.quadratic_weighted_kappa(a, list(reversed(a)), -1, 3) < 0
    off_by_one = [min(3, x + 1) for x in a]
    q = M.quadratic_weighted_kappa(a, off_by_one, -1, 3)
    assert 0.5 < q < 1.0


def test_auc_ranks_and_ties():
    assert M.auc([3, 4], [1, 2]) == 1.0
    assert M.auc([1, 2], [3, 4]) == 0.0
    assert M.auc([1, 1], [1, 1]) == 0.5
    assert M.auc([], [1]) is None


def test_f1_and_macro_f1():
    truth = ["a", "a", "b", "b", "c"]
    pred = ["a", "b", "b", "b", "a"]
    per = M.f1_scores(truth, pred)
    assert per["a"]["precision"] == pytest.approx(0.5)
    assert per["b"]["recall"] == pytest.approx(1.0)
    assert per["c"]["f1"] == 0.0
    assert M.macro_f1(truth, pred) == pytest.approx((0.5 + 0.8 + 0.0) / 3)
    assert M.macro_f1(truth, truth) == 1.0


def test_set_prf():
    tp, fp, fn = M.set_prf({"a", "b", "c"}, {"b", "c", "d"})
    assert (tp, fp, fn) == (2, 1, 1)
    assert M.prf_from_counts(tp, fp, fn)["f1"] == pytest.approx(2 / 3)


# --------------------------------------------------------------------------- ранжирование


def test_precision_ndcg_recall_mrr():
    ranked = [3, 0, 2, 1, 2]
    assert M.precision_at_k(ranked, 5) == pytest.approx(3 / 5)
    assert M.precision_at_k(ranked[:2], 10) == pytest.approx(1 / 10)  # пустые места — нерелевантны
    assert M.ndcg_at_k([3, 2, 1], [1, 2, 3], 3) == pytest.approx(1.0)
    assert M.ndcg_at_k([1, 2, 3], [1, 2, 3], 3) < 1.0
    assert M.ndcg_at_k([0, 0], [0, 0], 2) == 0.0
    assert M.recall_at_k(ranked, 4, 5) == pytest.approx(3 / 4)
    assert M.recall_at_k(ranked, 0, 5) is None
    assert M.reciprocal_rank([0, 1, 2]) == pytest.approx(1 / 3)
    assert M.reciprocal_rank([0, 0]) == 0.0
    with pytest.raises(ValueError):
        M.precision_at_k(ranked, 0)


def test_overlap_and_jaccard():
    assert M.overlap_at_k(["a", "b", "c"], ["c", "x", "a"], 3) == pytest.approx(2 / 3)
    assert M.jaccard(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)
    assert M.jaccard([], []) == 1.0


# --------------------------------------------------------------------------- статистика


def test_percentile_matches_numpy():
    rng = random.Random(1)
    xs = [rng.gauss(0, 1) for _ in range(101)]
    for q in (0, 5, 50, 95, 100):
        assert M.percentile(xs, q) == pytest.approx(float(np.percentile(xs, q)))
    assert M.percentile([], 50) is None


def test_pearson_spearman_rmse():
    xs = [1.0, 2.0, 3.0, 4.0]
    assert M.pearson(xs, [2 * x + 1 for x in xs]) == pytest.approx(1.0)
    assert M.spearman(xs, [x**3 for x in xs]) == pytest.approx(1.0)
    assert M.pearson([1, 1], [1, 2]) is None
    assert M.rmse([3.0, -4.0]) == pytest.approx(math.sqrt(12.5))


def test_bootstrap_is_deterministic_and_paired_detects_difference():
    vals = [0.1 * i for i in range(20)]
    a = M.bootstrap_ci(vals, random.Random(7), n_boot=300)
    b = M.bootstrap_ci(vals, random.Random(7), n_boot=300)
    assert a == b
    assert a[0] < M.mean(vals) < a[1]
    better = [v + 0.2 for v in vals]
    d = M.paired_bootstrap_diff(better, vals, random.Random(3), n_boot=300)
    assert d["diff"] == pytest.approx(0.2)
    assert d["significant"] is True
    same = M.paired_bootstrap_diff(vals, vals, random.Random(3), n_boot=300)
    assert same["significant"] is False


def test_round_tree_drops_nan():
    out = M.round_tree({"a": 1.234567, "b": [float("nan"), 2.0], "c": {"d": float("inf")}}, 2)
    assert out == {"a": 1.23, "b": [None, 2.0], "c": {"d": None}}


# --------------------------------------------------------------------------- текстовые бейзлайны


def test_tokenize_normalizes_and_stems():
    toks = tokenize("Ищем Разработчика: Python, PostgreSQL; ёлка")
    assert "python" in toks and "postgresql" in toks
    assert "разра" in toks  # кириллица обрезается до 5 букв
    assert "елка" in toks


def test_bm25_and_tfidf_rank_matching_doc_first():
    docs = {"a": "Python Django PostgreSQL", "b": "React TypeScript CSS", "c": "Python React"}
    assert BM25(docs).rank("Django Python")[0] == "a"
    assert TfIdf(docs).rank("React TypeScript")[0] == "b"
    assert TfIdf(docs).score("Python Django PostgreSQL")["a"] == pytest.approx(1.0)
    assert rank_positions(["x", "y"]) == {"x": 1, "y": 2}


# --------------------------------------------------------------------------- графики и плитки


def _parse(svg: str) -> ET.Element:
    return ET.fromstring(svg)


def test_charts_are_valid_svg_without_theta():
    s1 = charts.line_chart("Тест", ["0", "5", "20"], [("A", [0.1, 0.5, None]), ("B", [0.2, 0.3, 0.4])],
                           y_label="доля", x_title="коалиция", threshold=(0.15, "цель"))
    s2 = charts.path_chart("Путь", [("x", [0.0, 1.0, 2.5, 0.5])], y_label="S", x_title="n", threshold=(6.0, "h"),
                           marker_x=(2, "начало"))
    s3 = charts.ci_chart("ДИ", [("наш", 0.5, 0.4, 0.6, True), ("B0", 0.3, None, None, False)], x_label="P@10")
    for s in (s1, s2, s3):
        root = _parse(s)
        assert root.tag.endswith("svg")
        assert "θ" not in s
        assert "prefers-color-scheme: dark" in s


def test_identical_pair_share_and_twin_copying():
    from app.testing.irt import load_scale
    from eval.common import real_bank
    from eval.testing_eval import CopyExaminee, identical_pair_share

    assert identical_pair_share(["a", "b", "c"]) == 0.0
    assert identical_pair_share(["a", "a"]) == 1.0
    assert identical_pair_share(["a", "a", "b", "b"]) == pytest.approx(4 / 12)
    assert identical_pair_share(["a"]) == 0.0
    meta = next(f for f in real_bank() if f.c == 0)  # ввод ответа: чужой ключ неверен, если экземпляр не близнец
    scale, rng = load_scale(), random.Random(5)
    spec = meta.specs[0]
    never = CopyExaminee(theta=-3.0, leaked=frozenset({meta.id}), collision={meta.id: 0.0})
    assert not any(never.answer(meta, spec, scale, rng) for _ in range(50)) and len(never.caught) == 50
    twins = CopyExaminee(theta=-3.0, leaked=frozenset({meta.id}), collision={meta.id: 0.0}, twin={meta.id: 1.0})
    assert all(twins.answer(meta, spec, scale, rng) for _ in range(50)) and not twins.caught


def test_tile_pass_logic_and_tiles_on_partial_result():
    assert _passes(0.9, "ge", 0.85) is True
    assert _passes(0.2, "le", 0.15) is False
    assert _passes(1.0, "between", (0.8, 1.25)) is True
    assert _passes(None, "ge", 0.5) is None
    assert _passes(0.3, "show", None) is None
    ts = tiles({"testing": {"accuracy": {"decision_accuracy": 0.9}}})
    by = {t["id"]: t for t in ts}
    assert by["t_decision"]["pass"] is True
    assert by["m_p10"]["pass"] is None  # раздела нет — статус «справочно», а не ошибка
    assert all("θ" not in t["label"] for t in ts)
