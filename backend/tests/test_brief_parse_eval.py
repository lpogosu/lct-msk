"""Отложенная выборка разбора брифа (eval/brief_heldout.jsonl): целостность эталона и пороги качества."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.matching.skills import get_catalog
from eval.brief_parse_eval import evaluate, load_heldout

BEFORE = Path(__file__).resolve().parents[2] / "data" / "market" / "brief_parse_before.json"


@pytest.fixture(scope="module")
def result():
    return evaluate()


def test_heldout_size_and_gold_ids_are_valid():
    items = load_heldout()
    assert len(items) >= 60
    cat = get_catalog()
    for it in items:
        g = it["gold"]
        for s in [*g["must"], *g["nice"]]:
            assert s in cat.skills, (it["id"], s)
        assert not set(g["must"]) & set(g["nice"]), it["id"]


def test_parse_quality_thresholds(result):
    assert result["skills"]["f1"] >= 0.96
    assert result["spec_top1"] >= 0.95
    assert result["grade_exact"] >= 0.95
    assert result["salary_exact"] >= 0.9
    assert result["formats_exact"] >= 0.95


def test_dictionary_extension_improved_over_baseline(result):
    """«До» — замер на словаре без расширений из открытых данных (data/market/brief_parse_before.json)."""
    before = json.loads(BEFORE.read_text(encoding="utf-8"))
    assert result["skills"]["f1"] > before["skills"]["f1"]
    assert result["skills"]["recall"] > before["skills"]["recall"]
