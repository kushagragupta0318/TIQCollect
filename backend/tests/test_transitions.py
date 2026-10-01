"""E01: transition counts read from analytics (app/strategy/transitions.py).

No database: the scoped view is stood in for by a fake session that returns
mappings, so the evidence rules — pooling, abstention, exclusion reporting — are
tested on their own. tests/pg/ covers the SQL against the real view.

What this holds: a bank under MIN_BANK_MONTHS abstains with a typed code rather
than producing a matrix; a thin segment is pooled onto the bank-wide row and says
why; a state the engine does not model is reported, not folded into a neighbour;
the counts reaching the engine are the counts the view gave; and the posterior
over them still refuses a transition the state space forbids.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from app.core.errors import AppException, ErrorCode
from app.strategy import transitions as T
from app.strategy.monte_carlo import EngineConfig
from app.strategy.states import CURRENT, NPA_DOUBTFUL, NPA_SUB, SMA_0, SMA_1, STATES

BANK = "bank-1"


class _FakeResult:
    def __init__(self, rows): self._rows = rows
    def mappings(self): return self
    def all(self): return self._rows


class _FakeSession:
    """Returns the rows it was given and records what it was asked."""
    def __init__(self, rows): self.rows, self.params = rows, None
    def execute(self, _sql, params):
        self.params = params
        return _FakeResult(self.rows)


def _row(month: date, from_state: str, to_state: str, accounts: float, *,
         loan_type: str = "PERSONAL", region: str = "WEST", stale: float = 0.0,
         missing: float = 0.0, staleness: int = 0) -> dict:
    return {"month_end": month, "loan_type": loan_type, "region_id": region,
            "from_state": from_state, "to_state": to_state, "accounts": accounts,
            "stale": stale, "missing": missing, "staleness": staleness}


def _months(n: int) -> list[date]:
    return [date(2026, m, 28) for m in range(1, n + 1)]


def _book(n_months: int, accounts: float = 100.0, **kw) -> list[dict]:
    """A legal, well-evidenced book: CURRENT holds and rolls one rung."""
    rows = []
    for m in _months(n_months):
        rows.append(_row(m, "CURRENT", "CURRENT", accounts, **kw))
        rows.append(_row(m, "CURRENT", "SMA_0", accounts / 10, **kw))
    return rows


def test_a_bank_with_too_little_history_abstains_instead_of_producing_a_matrix():
    for n in range(0, T.MIN_BANK_MONTHS):
        with pytest.raises(AppException) as e:
            T.read_transitions(_FakeSession(_book(n)), BANK)
        assert e.value.code is ErrorCode.INSUFFICIENT_HISTORY
        assert e.value.status_code == 422
        assert str(n) in e.value.detail and str(T.MIN_BANK_MONTHS) in e.value.detail
    # At the threshold it reads, so the refusal is the stated rule and not a mood.
    ok = T.read_transitions(_FakeSession(_book(T.MIN_BANK_MONTHS)), BANK)
    assert ok.months == T.MIN_BANK_MONTHS and ok.matrices.n_segments == 1


def test_the_reading_is_bank_scoped_and_excludes_unread_months():
    s = _FakeSession(_book(T.MIN_BANK_MONTHS))
    T.read_transitions(s, BANK, as_of=date(2026, 6, 30))
    assert s.params["bank"] == BANK and s.params["as_of"] == date(2026, 6, 30)
    assert s.params["no_reading"] == T.NO_READING, "a NO_READING month is not a transition"


def test_only_the_most_recent_months_asked_for_are_used():
    reading = T.read_transitions(_FakeSession(_book(12)), BANK, months=7)
    assert reading.months == 7
    assert reading.month_ends[-1] == date(2026, 12, 28)      # newest kept
    assert reading.month_ends[0] == date(2026, 6, 28)        # older months dropped


def test_a_thin_segment_is_pooled_onto_the_bank_wide_row_and_says_why():
    rows = _book(8, 400.0, loan_type="PERSONAL", region="WEST")
    # A second segment seen in only two months, with few accounts: both reasons.
    rows += [_row(m, "CURRENT", "SMA_0", 3.0, loan_type="GOLD", region="EAST")
             for m in _months(2)]
    reading = T.read_transitions(_FakeSession(rows), BANK)
    by = {s.key: s for s in reading.segments}
    thick, thin = by[T.segment_key("PERSONAL", "WEST")], by[T.segment_key("GOLD", "EAST")]
    assert thick.status == T.OBSERVED and thick.reason == ""
    assert thin.status == T.POOLED
    assert f"2 month(s) < {T.MIN_MONTHS}" in thin.reason and "transitions <" in thin.reason
    assert reading.pooled_segments == (T.segment_key("GOLD", "EAST"),)
    # The pooled segment carries the bank-wide counts, the observed one its own.
    ix = reading.matrices.index_of([thin.key, thick.key])
    counts = reading.matrices.counts
    assert counts[ix[0]].sum() > counts[ix[1]].sum(), "pooled row is the whole book's"
    assert counts[ix[1], CURRENT, CURRENT] == 8 * 400.0


def test_a_state_the_engine_does_not_model_is_reported_not_folded_into_a_neighbour():
    rows = _book(T.MIN_BANK_MONTHS)
    rows += [_row(m, "CURRENT", "RESTRUCTURED", 25.0) for m in _months(3)]
    reading = T.read_transitions(_FakeSession(rows), BANK)
    assert reading.rows_outside_the_state_space == ("RESTRUCTURED",)
    # 25 accounts a month did NOT land anywhere in the matrix.
    assert reading.matrices.counts.sum() == T.MIN_BANK_MONTHS * (100.0 + 10.0)
    assert "RESTRUCTURED" in reading.as_dict()["rows_outside_the_state_space"]


def test_excluded_and_stale_pairs_are_reported_never_imputed():
    rows = [_row(m, "CURRENT", "CURRENT", 100.0, stale=4.0, missing=2.0, staleness=9)
            for m in _months(T.MIN_BANK_MONTHS)]
    reading = T.read_transitions(_FakeSession(rows), BANK)
    assert reading.excluded_stale_pairs == 4.0 * T.MIN_BANK_MONTHS
    assert reading.excluded_missing_pairs == 2.0 * T.MIN_BANK_MONTHS
    assert reading.max_staleness_days == 9
    d = reading.as_dict()
    assert d["excluded_stale_pairs"] == 24.0 and d["excluded_missing_pairs"] == 12.0
    # Reported, and NOT added back into any cell.
    assert reading.matrices.counts.sum() == 100.0 * T.MIN_BANK_MONTHS


def test_the_counts_reaching_the_engine_are_the_counts_the_view_gave():
    rows = []
    for m in _months(T.MIN_BANK_MONTHS):
        rows += [_row(m, "CURRENT", "CURRENT", 70.0), _row(m, "CURRENT", "SMA_0", 30.0),
                 _row(m, "SMA_0", "CURRENT", 11.0), _row(m, "NPA_SUB", "NPA_SUB", 40.0)]
    c = T.read_transitions(_FakeSession(rows), BANK).matrices.counts[0]
    n = T.MIN_BANK_MONTHS
    assert (c[CURRENT, CURRENT], c[CURRENT, SMA_0]) == (70.0 * n, 30.0 * n)
    assert (c[SMA_0, CURRENT], c[NPA_SUB, NPA_SUB]) == (11.0 * n, 40.0 * n)
    assert c.sum() == (70.0 + 30.0 + 11.0 + 40.0) * n


def test_a_forbidden_transition_in_the_view_still_cannot_reach_the_posterior():
    """The masking of mc-1.2.0 applies to counts that came from real analytics,
    which is the case it exists for: one re-classified account in the view must
    not teach the engine that an NPA can become an SMA."""
    rows = _book(T.MIN_BANK_MONTHS)
    rows += [_row(m, "NPA_SUB", "SMA_1", 50.0) for m in _months(T.MIN_BANK_MONTHS)]
    rows += [_row(m, "NPA_DOUBTFUL", "NPA_SUB", 7.0) for m in _months(T.MIN_BANK_MONTHS)]
    m = T.read_transitions(_FakeSession(rows), BANK).matrices
    # The counts are carried faithfully — transitions.py does not silently drop them...
    assert m.counts[0, NPA_SUB, SMA_1] == 50.0 * T.MIN_BANK_MONTHS
    # ...and the posterior refuses them, naming what it discarded.
    alpha, _ = m.dirichlet_alpha(EngineConfig())
    assert alpha[0, NPA_SUB, SMA_1] == 0.0 and alpha[0, NPA_DOUBTFUL, NPA_SUB] == 0.0
    discarded = {(d["from_state"], d["to_state"]): d["accounts"] for d in m.impossible_cells()}
    assert discarded == {("NPA_SUB", "SMA_1"): 50.0 * T.MIN_BANK_MONTHS,
                         ("NPA_DOUBTFUL", "NPA_SUB"): 7.0 * T.MIN_BANK_MONTHS}


def test_the_thresholds_are_the_ones_the_adr_states():
    """ADR 0013 names these. A change to them changes what the product refuses,
    so it should break a test rather than pass quietly."""
    assert (T.MIN_MONTHS, T.MIN_FROM_ACCOUNTS, T.DEFAULT_MONTHS) == (6, 30, 12)
    assert T.MIN_BANK_MONTHS == T.MIN_MONTHS
    assert T.VIEW == "analytics.bucket_transitions_monthly_scoped", "a *_scoped view only"


def test_the_segment_grain_is_loan_type_and_region_with_agency_aggregated_out():
    rows = []
    for m in _months(T.MIN_BANK_MONTHS):
        for agency in ("a1", "a2"):     # the view's agency rows collapse together
            rows.append({**_row(m, "CURRENT", "CURRENT", 50.0), "agency": agency})
    reading = T.read_transitions(_FakeSession(rows), BANK)
    assert reading.matrices.n_segments == 1
    assert reading.matrices.keys == (T.segment_key("PERSONAL", "WEST"),)
    assert reading.matrices.counts[0, CURRENT, CURRENT] == 100.0 * T.MIN_BANK_MONTHS
    assert reading.matrices.loan_types == ("PERSONAL",)


def test_the_source_names_the_view_and_the_window_it_read():
    reading = T.read_transitions(_FakeSession(_book(T.MIN_BANK_MONTHS)), BANK)
    assert reading.matrices.source.startswith(T.VIEW)
    assert "2026-01-28..2026-06-28" in reading.matrices.source
    assert reading.matrices.synthetic is False
    assert T.read_transitions(_FakeSession(_book(6)), BANK, synthetic=True).matrices.synthetic is True
