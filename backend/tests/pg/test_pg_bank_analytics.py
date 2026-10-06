"""The Analytics tabs (plan §5.4, task C04, lane L6) on the committed demo
book: Exposure, Migration, Agencies, Compliance — the four built this pass.
Field Operations and Recovery's target chart are NOT built (no beats data,
no target concept anywhere in the schema); not tested here because there is
nothing to abstain-test against yet beyond what Overview already covers.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.pg.test_pg_demo_fixture import db, sql_text  # noqa: F401 — the restored fixture

pytestmark = pytest.mark.filterwarnings("ignore")


def _session(engine, bank_id: str) -> Session:
    s = Session(bind=engine)
    s.execute(text("SELECT set_config('app.bank_id', :b, false), set_config('app.scope', 'BANK', false)"),
              {"b": bank_id})
    return s


@pytest.fixture(scope="module")
def views(db):  # noqa: F811
    with db.connect() as c:
        have = c.execute(text("SELECT count(*) FROM information_schema.views WHERE table_schema = 'analytics' "
                              "AND table_name LIKE '%\\_scoped'")).scalar()
    if have < 5:
        pytest.skip("the fixture does not carry the five analytics *_scoped views yet (43's v2_0013)")
    return db


def test_exposure_funnel_is_real_and_monotone(views):
    from app.demo.roster import BANK
    from app.services.bank.analytics_catalog import compute_tab
    from app.services.bank.kpi_filter import KpiFilter
    with _session(views, BANK["id"]) as s:
        out = compute_tab(s, s, "exposure", BANK["id"], KpiFilter())
    assert out["available"], out["reason"]
    funnel = out["panels"]["funnel"]
    assert funnel["book"] > funnel["delinquent"] > funnel["placed"] > funnel["npa"] > 0
    assert sum(r["accounts"] for r in out["panels"]["dpd_ladder"]) > 0
    assert out["panels"]["product_bucket_heat"] and out["panels"]["security_cover"]


def test_migration_matrix_covers_every_seen_from_state(views):
    from app.demo.roster import BANK
    from app.services.bank.analytics_catalog import compute_tab
    from app.services.bank.kpi_filter import KpiFilter
    with _session(views, BANK["id"]) as s:
        out = compute_tab(s, s, "migration", BANK["id"], KpiFilter())
    assert out["available"], out["reason"]
    matrix = out["panels"]["transition_matrix"]
    assert matrix and all(r["exposure"] is not None for r in matrix)
    assert out["panels"]["trajectory_12m"]


def test_agencies_tab_lists_every_active_agency_with_a_real_scorecard(views):
    from app.demo.roster import BANK
    from app.services.bank.analytics_catalog import compute_tab
    from app.services.bank.kpi_filter import KpiFilter
    with _session(views, BANK["id"]) as s:
        out = compute_tab(s, s, "agencies", BANK["id"], KpiFilter())
    assert out["available"]
    cards = out["panels"]["scorecards"]
    codes = {c["code"] for c in cards}
    # Every agency appears, any status — same precedent as the /bank/filters
    # route's own agency list (status is shown, not filtered); a suspended
    # agency's scorecard history does not disappear with it.
    assert {"AGY-ARAVALLI", "AGY-AWADH", "AGY-HOOGHLY", "AGY-SAHYADRI"} <= codes
    awadh = next(c for c in cards if c["code"] == "AGY-AWADH")
    assert awadh["status"] == "SUSPENDED"
    aravalli = next(c for c in cards if c["code"] == "AGY-ARAVALLI")
    # Every ratio in compute_metrics() is independently None-if-no-denominator
    # (agency_scorecard._ratio) — "the latest month with ANY bank activity"
    # (agency_effect.latest_month, MAX over every agency) can be a quiet one
    # for a GIVEN agency even when the bank-wide figure is real (that one
    # sums known rows across all agencies, kpi_catalog's own
    # collection_efficiency test covers it). Which specific agency's ratio
    # is non-null in that month is not something to hard-code here — found
    # the hard way, twice, re-verifying this test. The one guarantee this
    # test owns, true in any month: collection_efficiency is None for the
    # one agency with no opening reading, ever — the thing lane L6 fixed.
    assert aravalli["collection_efficiency"] is None


def test_agencies_filter_by_id_returns_only_that_agency(views):
    from app.demo.roster import BANK, SAHYADRI
    from app.services.bank.analytics_catalog import compute_tab
    from app.services.bank.kpi_filter import KpiFilter
    with _session(views, BANK["id"]) as s:
        out = compute_tab(s, s, "agencies", BANK["id"], KpiFilter(agency=SAHYADRI.id))
    assert out["available"]
    assert [c["code"] for c in out["panels"]["scorecards"]] == ["AGY-SAHYADRI"]


def test_compliance_breaches_are_real_and_fraud_matches_the_manifest(views):
    """Awadh's evidence-integrity story (fabricated_photo_rate, latent.py) is
    GENERATOR-ONLY truth — it shapes visit flags but is never written as a
    FraudReview row for a generated agency (no generator code creates one;
    confirmed by reading app/demo/books.py). The 31 CONFIRMED fraud_reviews
    in the book are v1's, copied onto Aravalli by migrate_v1_to_v2.py
    (T.copy("fraud_reviews")) — that is where this panel's real data is."""
    from app.demo.roster import AGENCY, BANK  # AGENCY = Aravalli
    from app.services.bank.analytics_catalog import compute_tab
    from app.services.bank.kpi_filter import KpiFilter
    with _session(views, BANK["id"]) as s:
        out = compute_tab(s, s, "compliance", BANK["id"], KpiFilter())
    assert out["available"], out["reason"]
    by_month = out["panels"]["breaches_over_time"]
    assert by_month and sum(r["out_of_hours"] for r in by_month) > 0
    assert sum(r["geofence"] for r in by_month) > 0
    by_agency = {r["agency_id"]: r for r in out["panels"]["by_agency"]}
    assert by_agency[AGENCY["id"]]["fraud_confirmed"] > 0            # Aravalli's v1-copied reviews
    assert sum(r["fraud_confirmed"] for r in out["panels"]["by_agency"]) == sum(
        r["fraud_confirmed"] for r in by_month)
    # Not the raw id (C04 task #3): a real name, same lookup _agencies() uses.
    assert by_agency[AGENCY["id"]]["agency_name"] not in (None, AGENCY["id"])
    assert all(r["agency_name"] != r["agency_id"] for r in out["panels"]["by_agency"])


def test_a_session_for_another_bank_sees_no_figures_on_any_tab(views):
    """Exposure/migration/compliance read only analytics.*_scoped views, so a
    mismatched session gets nothing at all (same guarantee
    test_pg_bank_overview.py already pins for the Overview KPIs).

    Agencies is different, and the difference is deliberate, not a gap: the
    ROSTER comes from a plain tenancy.agencies query — bank_id here is always
    _bank_of(ctx), the caller's own bank (bank.py), never attacker-supplied,
    so this mirrors /bank/filters' existing agency list, which has the same
    property and is not considered a leak. What must still hold, and does,
    is that every card's FIGURES are empty (n_rows == 0): the scorecard
    itself comes from the RLS-protected scoped view and refuses the
    mismatch, same as every other tab."""
    from app.demo.roster import BANK, KUMAON_BANK
    from app.services.bank.analytics_catalog import compute_tab
    from app.services.bank.kpi_filter import KpiFilter
    with _session(views, KUMAON_BANK["id"]) as s:
        exposure = compute_tab(s, s, "exposure", BANK["id"], KpiFilter())
        agencies = compute_tab(s, s, "agencies", BANK["id"], KpiFilter())
    assert not exposure["available"]
    assert agencies["available"]
    assert agencies["panels"]["scorecards"]                          # the roster, not RLS-gated
    assert all(c["n_rows"] == 0 for c in agencies["panels"]["scorecards"])   # the DATA is


def test_an_unsupported_dimension_abstains_the_whole_tab_not_a_silent_ignore(views):
    """Agencies' only scoped view, agency_scorecard_monthly_scoped, carries no
    `bucket` dimension (kpi_filter.VIEW_DIMENSIONS) — filtering by bucket must
    refuse, not silently return the unfiltered table under a filter the UI
    believes is applied."""
    from app.demo.roster import BANK
    from app.services.bank.analytics_catalog import compute_tab
    from app.services.bank.kpi_filter import KpiFilter
    with _session(views, BANK["id"]) as s:
        out = compute_tab(s, s, "agencies", BANK["id"], KpiFilter(bucket="NPA"))
    assert not out["available"] and "bucket" in out["reason"].lower()
