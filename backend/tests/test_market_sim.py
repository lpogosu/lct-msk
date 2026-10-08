"""Симуляция рынка (eval/market_sim.py): детерминизм, инварианты метрик, чтение политики из config/contact.yaml."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from app.contact.settings import get_contact_config
from eval import market_sim as ms

SMALL = replace(ms.Params().with_policy(), employers=40, candidates=40, weeks=6)


def test_policy_comes_from_config():
    cfg = get_contact_config()
    p = ms.Params().with_policy()
    assert (p.active_per_need, p.new_per_owner, p.batch_max) == (
        cfg.quotas.active_per_need,
        cfg.quotas.new_per_owner,
        cfg.quotas.batch_max,
    )
    assert p.signals == cfg.interest.per_window


@pytest.mark.parametrize("regime", ms.REGIMES)
def test_deterministic_and_bounded(regime):
    a, b = ms.simulate(SMALL, regime, 7), ms.simulate(SMALL, regime, 7)
    assert a == b
    assert 0 <= a.match_rate <= 1 and 0 <= a.wasted <= 1 and 0 <= a.gini <= 1 and 0 < a.top10_share <= 1
    assert 1 <= a.first_yes_weeks <= SMALL.weeks + 1


def test_quota_cuts_load_and_free_sends_more():
    free = ms.simulate(SMALL, "free", 1)
    quota = ms.simulate(SMALL, "quota", 1)
    assert quota.invites_per_employer < free.invites_per_employer


def test_no_signal_changes_nothing_without_signals():
    # 0 сигналов в режиме «квоты + сигнал» совпадает с квотами по выбору приглашаемых
    p0 = replace(SMALL, signals=0)
    assert ms.simulate(p0, "signal", 3).match_rate == pytest.approx(ms.simulate(p0, "quota", 3).match_rate, abs=0.05)


def test_gini_and_top_share():
    assert ms.gini(np.array([1, 1, 1, 1])) == pytest.approx(0)
    assert ms.gini(np.array([0, 0, 0, 10])) == pytest.approx(0.75)
    assert ms.top_share(np.array([1] * 9 + [11]), 0.1) == pytest.approx(0.55)
