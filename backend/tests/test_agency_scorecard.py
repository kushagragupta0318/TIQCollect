# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# New file, 2026-09-29 (P3 D06). services/bank/agency_scorecard.py's pure
# arithmetic (compute_metrics) — fetch_rows/agency_scorecard/leaderboard need
# the real analytics.agency_scorecard_monthly_scoped view (Postgres only) and
# are covered by tests/pg/test_pg_agency_scorecard.py instead.
from __future__ import annotations

from datetime import date

from app.services.bank.agency_scorecard import ScorecardRow, compute_metrics, leaderboard

M1 = date(2026, 8, 1)


def row(**overrides) -> ScorecardRow:
    base = dict(
        month_start=M1, agency_id="A", region_id="R", placed_new=10, resolved_placements=4,
        collectible_due=100_000.0, verified_collections=60_000.0, bank_direct_collections=5_000.0,
        expected_recovery_inr=50_000.0, ptps_matured=20, ptps_honoured=12, visits=100, met_visits=70,
        first_visits_within_sla=40, placements_due_first_visit=50, agents_active=8, agents_contracted=10,
        agents_exited=1, agent_leave_days=15.0, commission_accrued=6_000.0, field_cost=2_000.0,
        breaches_out_of_hours=2, breaches_geofence=1, fraud_confirmed=1, consent_missing=3,
    )
    base.update(overrides)
    return ScorecardRow(**base)


def test_no_rows_reads_as_empty_not_an_error():
    assert compute_metrics([]) == {"n_rows": 0}


def test_collection_efficiency_is_verified_over_collectible_due():
    m = compute_metrics([row()])
    assert m["collection_efficiency"] == 0.6   # 60,000 / 100,000


def test_collection_efficiency_is_none_when_collectible_due_is_unknown_everywhere():
    m = compute_metrics([row(collectible_due=None)])
    assert m["collection_efficiency"] is None


def test_resolution_rate_is_resolved_over_placed():
    m = compute_metrics([row()])
    assert m["resolution_rate"] == 0.4   # 4 / 10


def test_recovery_vs_expected_is_not_clamped_and_can_exceed_one():
    """Documented cohort caveat: expected_recovery_inr is only the month's
    new placements, verified_collections is the whole active book — the
    ratio can legitimately read above 1.0, and must not be silently capped."""
    m = compute_metrics([row(verified_collections=80_000.0, bank_direct_collections=0.0,
                             expected_recovery_inr=50_000.0)])
    assert m["recovery_vs_expected"] == 1.6


def test_recovery_vs_expected_is_none_when_nothing_was_expected():
    m = compute_metrics([row(expected_recovery_inr=None)])
    assert m["recovery_vs_expected"] is None


def test_ptp_conversion_contact_rate_and_sla_adherence():
    m = compute_metrics([row()])
    assert m["ptp_conversion"] == 0.6      # 12 / 20
    assert m["contact_rate"] == 0.7        # 70 / 100
    assert m["sla_adherence"] == 0.8       # 40 / 50


def test_productivity_is_visits_per_active_agent_per_day():
    m = compute_metrics([row(visits=240, agents_active=8)])
    assert m["productivity_per_agent_per_day"] == 1.0   # 240 / 8 / 30


def test_productivity_is_none_with_no_active_agents():
    m = compute_metrics([row(agents_active=0)])
    assert m["productivity_per_agent_per_day"] is None


def test_cost_per_100_is_none_when_field_cost_is_unknown_not_zero():
    """43's own rule: field_cost NULL means no rate configured, never 0 —
    a NULL must not silently become a free field force in the cost figure."""
    m = compute_metrics([row(field_cost=None)])
    assert m["cost_per_100_inr"] is None


def test_cost_per_100_uses_commission_plus_field_cost_over_total_collected():
    m = compute_metrics([row(commission_accrued=6_000.0, field_cost=2_000.0,
                             verified_collections=60_000.0, bank_direct_collections=0.0)])
    assert m["cost_per_100_inr"] == 13.33   # (6,000 + 2,000) / 60,000 * 100


def test_evidence_integrity_is_fraud_confirmed_per_100_visits():
    m = compute_metrics([row(fraud_confirmed=1, visits=100)])
    assert m["evidence_integrity_per_100_visits"] == 1.0


def test_workforce_active_ratio_attrition_and_leave_rate():
    m = compute_metrics([row(agents_active=8, agents_contracted=10, agents_exited=1, agent_leave_days=15.0)])
    assert m["workforce_active_ratio"] == 0.8
    assert m["workforce_attrition_ratio"] == 0.1
    assert m["workforce_leave_rate"] == round(15.0 / (10 * 30), 4)


def test_workforce_ratios_not_clamped_to_one():
    """An agency can run more active agents than its nominal contracted seat
    count for a month (overstaffing, a contract amendment mid-month) — the
    ratio should say so, not silently cap at 1.0 the way a bounded rate does."""
    m = compute_metrics([row(agents_active=15, agents_contracted=10)])
    assert m["workforce_active_ratio"] == 1.5


def test_metrics_sum_across_rows_rather_than_average_per_row_rate():
    """Two months, one with a perfect rate on a tiny denominator and one with
    a poor rate on a large one — summing first must not let the tiny month
    dominate the way averaging per-row rates would."""
    rows = [
        row(month_start=M1, collectible_due=1_000.0, verified_collections=1_000.0),   # 100% on 1,000
        row(month_start=date(2026, 9, 1), collectible_due=99_000.0, verified_collections=9_000.0),  # ~9% on 99,000
    ]
    m = compute_metrics(rows)
    assert m["collection_efficiency"] == round(10_000.0 / 100_000.0, 10)   # summed, not averaged to ~55%


def test_a_row_with_unknown_due_does_not_poison_a_window_that_has_other_known_rows():
    rows = [row(collectible_due=None), row(collectible_due=100_000.0, verified_collections=60_000.0)]
    m = compute_metrics(rows)
    assert m["collection_efficiency"] == 0.6


def test_compliance_score_is_present_and_falls_as_breaches_and_missing_consent_rise():
    clean = compute_metrics([row(breaches_out_of_hours=0, breaches_geofence=0, consent_missing=0)])
    dirty = compute_metrics([row(breaches_out_of_hours=10, breaches_geofence=10, consent_missing=50)])
    assert clean["compliance_score"] > dirty["compliance_score"]
    assert 0 <= dirty["compliance_score"] <= 100


def test_leaderboard_sorts_by_index_descending_and_puts_unscored_last(monkeypatch):
    effects = [
        {"agency_id": "A", "index": 40.0}, {"agency_id": "B", "index": 90.0},
        {"agency_id": "C", "index": None}, {"agency_id": "D", "index": 65.0},
    ]
    import app.services.bank.agency_scorecard as mod
    monkeypatch.setattr(mod, "agency_effect", lambda *a, **k: effects)
    ranked = leaderboard(object(), bank_id="bank-1")
    assert [r["agency_id"] for r in ranked] == ["B", "D", "A", "C"]
