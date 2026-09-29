"""services/bank/agency_effect.shrink — the estimator, pure (no DB).
The SQL half (fetch_cells over the scoped view) is exercised in tests/pg."""
from __future__ import annotations

from datetime import date

import pytest

from app.ml.empirical_bayes import MULTIPLIER_BOUNDS, EmpiricalBayesAgentAdjuster, eb_shrink
from app.services.bank.agency_effect import SMOOTHING_K, ScorecardCell, _month_back, shrink

M1, M2, M3 = date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1)


def cell(agency, region, month=M3, got=0.0, due=100.0, n=10):
    return ScorecardCell(month, agency, region, got, due, n)


def by(effects):
    return {(e.agency_id, e.region_id, e.month_start): e for e in effects}


def test_the_shrunk_rate_sits_between_the_agency_and_its_regional_peers():
    # A: 60/100 with n=10; B: 20/100 with n=30. Peer = 80/200 = 0.40.
    out = by(shrink([cell("A", "R", got=60, n=10), cell("B", "R", got=20, n=30)], pooled=False))
    a, b = out[("A", "R", M3)], out[("B", "R", M3)]
    assert a.peer_rate == b.peer_rate == pytest.approx(0.40)
    w = 10 / (10 + SMOOTHING_K)
    assert a.shrunk_rate == pytest.approx(w * 0.60 + (1 - w) * 0.40)
    assert b.raw_rate < b.shrunk_rate < b.peer_rate                   # 0.20 < shrunk < 0.40
    assert a.index == pytest.approx(round(100 * a.shrunk_rate, 1))


def test_a_thin_agency_is_pulled_harder_to_its_peers_than_a_large_one():
    cells = [cell("THIN", "R", got=90, due=100, n=1), cell("BIG", "R", got=90, due=100, n=200),
             cell("PEER", "R", got=10, due=800, n=100)]
    out = by(shrink(cells, pooled=False))
    thin, big = out[("THIN", "R", M3)], out[("BIG", "R", M3)]
    assert thin.raw_rate == big.raw_rate == pytest.approx(0.9)
    assert abs(thin.shrunk_rate - thin.peer_rate) < abs(big.shrunk_rate - big.peer_rate)


def test_peers_are_the_same_region_only():
    out = by(shrink([cell("A", "R1", got=50), cell("B", "R2", got=10)], pooled=False))
    assert out[("A", "R1", M3)].peer_rate == pytest.approx(0.5)
    assert out[("B", "R2", M3)].peer_rate == pytest.approx(0.1)


def test_the_multiplier_is_bounded_and_neutral_without_evidence():
    lo, hi = MULTIPLIER_BOUNDS
    out = by(shrink([cell("STAR", "R", got=100, n=1000), cell("DUD", "R", got=0, n=1000),
                     cell("NODUE", "R", got=0, due=0, n=3)], pooled=False))
    assert out[("STAR", "R", M3)].multiplier == hi
    assert out[("DUD", "R", M3)].multiplier == lo
    nodue = out[("NODUE", "R", M3)]
    assert (nodue.raw_rate, nodue.index, nodue.multiplier) == (None, None, 1.0)


def test_rates_are_clamped_to_zero_one():
    """Collections above what was due (arrears cleared early) cap at 100%."""
    e = shrink([cell("A", "R", got=250, due=100)], pooled=False)[0]
    assert e.raw_rate == 1.0 and e.index <= 100.0


def test_pooling_folds_the_window_into_one_row_per_agency_region():
    cells = [cell("A", "R", M1, got=10, due=100, n=5), cell("A", "R", M2, got=30, due=100, n=5),
             cell("A", "R", M3, got=50, due=100, n=5), cell("B", "R", M3, got=0, due=100, n=5)]
    pooled = by(shrink(cells, pooled=True))
    a = pooled[("A", "R", M3)]                           # keyed on the latest month
    assert (a.months, a.n, a.raw_rate) == (3, 15, pytest.approx(0.3))
    assert a.peer_rate == pytest.approx(90 / 400)
    assert len(shrink(cells, pooled=False)) == 4


def test_a_filter_on_one_agency_would_not_change_its_peers():
    """Peers come from every cell passed in; agency_effect() filters AFTER shrink."""
    both = by(shrink([cell("A", "R", got=60), cell("B", "R", got=20)], pooled=False))
    alone = by(shrink([cell("A", "R", got=60)], pooled=False))
    assert both[("A", "R", M3)].peer_rate != alone[("A", "R", M3)].peer_rate


@pytest.mark.parametrize("m,n,expected", [
    (date(2026, 3, 1), 0, date(2026, 3, 1)),
    (date(2026, 3, 1), 2, date(2026, 1, 1)),
    (date(2026, 1, 1), 1, date(2025, 12, 1)),
    (date(2026, 2, 1), 14, date(2024, 12, 1)),
])
def test_month_back_crosses_years(m, n, expected):
    assert _month_back(m, n) == expected


def test_a_month_with_unknown_due_is_skipped_not_read_as_zero_due():
    """The view writes collectible_due NULL when it cannot know it (B13b). Its
    collections must not count against a smaller denominator."""
    cells = [cell("A", "R", M2, got=40, due=100, n=5), cell("A", "R", M3, got=500, due=None, n=5)]
    a = shrink(cells, pooled=True)[0]
    assert (a.raw_rate, a.n, a.months_unread) == (pytest.approx(0.4), 5, 1)
    only_unknown = shrink([cell("B", "R", got=10, due=None)], pooled=False)[0]
    assert (only_unknown.raw_rate, only_unknown.index, only_unknown.multiplier) == (None, None, 1.0)


@pytest.mark.parametrize("n,own,prior", [(1, 0.9, 0.4), (10, 0.6, 0.4), (500, 0.1, 0.5), (7, 0.3, 0.01)])
def test_the_agent_adjuster_and_the_agency_effect_shrink_identically(n, own, prior):
    """One formula, two consumers (coordinator 2026-09-29): the agent EB
    adjuster and agency_effect give the same shrunk rate and multiplier on
    the same evidence."""
    adj = EmpiricalBayesAgentAdjuster(smoothing_k=SMOOTHING_K, min_sample_threshold=1)
    adj.segment_priors[("PERSONAL", "BUCKET_2")] = prior
    adj.agent_observations[("AG", "PERSONAL", "BUCKET_2")] = {"n": n, "recovered": own * 1000, "target": 1000}
    agent_shrunk, _, agent_mult = adj.get_segment_multiplier("AG", "PERSONAL", "BUCKET_2")

    # Both are eb_shrink on their own inputs: the agent's (own, segment prior)
    # and the agency's (rate, regional peer rate).
    eb = eb_shrink(n, SMOOTHING_K, own, prior)
    cells = [cell("A", "R", got=own * 1000, due=1000, n=n), cell("PEER", "R", got=prior * 5000, due=5000, n=1)]
    assert agent_shrunk == pytest.approx(eb["shrunk"]) and agent_mult == pytest.approx(eb["multiplier"])
    a = next(e for e in shrink(cells, pooled=False) if e.agency_id == "A")
    expected = eb_shrink(n, SMOOTHING_K, a.raw_rate, a.peer_rate)
    assert a.shrunk_rate == pytest.approx(expected["shrunk"]) and a.multiplier == pytest.approx(round(expected["multiplier"], 4))
