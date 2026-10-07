"""The Analytics tabs (plan §5.4, task C04, lane L6) on the committed demo
book: Exposure, Migration, Agencies, Recovery, Cost to Collect, Compliance.
Field Operations is NOT built (queued separately); not tested here because
there is nothing to abstain-test against yet beyond what Overview already
covers.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.pg.test_pg_analytics_b import A2, B1, CUST, IST, _insert, _refresh, book  # noqa: F401 — the hand-built book
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


def test_recovery_vs_expected_abstains_honestly_on_the_real_book(views):
    """C04 task #5 (owner ruling 2026-10-06): the target is recovery_risk's
    predicted recovery (expected_recovery_inr, written once per placement),
    bank-wide by month — not a bank-set number, since none exists.

    Measured against the restored dump (2026-10-07): collections.placements
    has 9,764 rows for Girivan and expected_recovery_inr is NULL on every
    one -- the generator never prices a placement at creation time, for any
    agency. So every month's ratio is honestly None here: abstaining,
    exactly as ADR 0005 requires, not a bug. The mechanism that computes a
    real ratio WHEN something is priced is covered on a hand-built book
    below, since this one has nothing to compute it from."""
    from app.demo.roster import BANK
    from app.services.bank.analytics_catalog import compute_tab
    from app.services.bank.kpi_filter import KpiFilter
    with _session(views, BANK["id"]) as s:
        out = compute_tab(s, s, "recovery", BANK["id"], KpiFilter())
    assert out["available"], out["reason"]
    by_month = out["panels"]["by_month"]
    assert by_month and sum(r["actual_inr"] for r in by_month) > 0
    assert all(r["recovery_vs_expected"] is None for r in by_month)


def test_recovery_vs_expected_computes_a_real_ratio_once_something_is_priced(book):  # noqa: F811
    """P1's placement (test_pg_analytics_b.book) carries
    expected_recovery_inr=20_000 -- the one placement anywhere in this
    suite's fixtures that IS priced. Proves _recovery()'s arithmetic, which
    the real book (above) never exercises."""
    from app.core import database
    from app.services.bank.analytics_catalog import compute_tab
    from app.services.bank.kpi_filter import KpiFilter
    _refresh(book)
    with book.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL ROLE tiq_app"))
            database._set_tenant(conn, {"user_id": "t", "bank_id": B1, "agency_id": None, "scope": "BANK"})
            out = compute_tab(Session(bind=conn), Session(bind=conn), "recovery", B1, KpiFilter())
    assert out["available"], out["reason"]
    by_month = out["panels"]["by_month"]
    jan = next(r for r in by_month if r["month_start"] == date(2025, 1, 1))
    with book.connect() as conn:
        expected_direct = conn.execute(text(
            "SELECT sum(expected_recovery_inr) FROM collections.placements "
            "WHERE bank_id = :b AND date_trunc('month', placed_on) = '2025-01-01'"), {"b": B1}).scalar()
    assert expected_direct == 60_000   # P1, P2, P3 all placed 2025-01-05, all priced at 20,000
    assert jan["expected_inr"] == expected_direct
    assert float(jan["recovery_vs_expected"]) == round(float(jan["actual_inr"]) / float(expected_direct), 4)
    assert all(r["recovery_vs_expected"] is None for r in by_month if r["expected_inr"] is None)


def test_recovery_vs_expected_pairs_its_numerator_and_denominator_over_the_same_rows(book):  # noqa: F811
    """A second agency's own placement this month has no price (the view
    coalesces its expected_recovery_inr cell to 0 -- measured, see the
    fix's own comment in _recovery()) but DOES have real collections.
    Plain SUM(actual) kept counting them in the ratio's numerator while
    the restricted SUM(expected) already drops that agency's row from the
    denominator -- inflating recovery_vs_expected relative to
    agency_scorecard.py:140's own same-rows rule for collection_efficiency,
    not applied here before this fix (coordinator audit, 2026-10-07)."""
    from app.core import database
    from app.services.bank.analytics_catalog import compute_tab
    from app.services.bank.kpi_filter import KpiFilter

    with book.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        loan2, case2, agent2, user2 = (str(uuid.uuid4()) for _ in range(4))
        _insert(conn, "lending.loans", id=loan2, bank_id=B1, customer_id=CUST, loan_type="PERSONAL",
                branch_code="NONE", npa_since=None)
        _insert(conn, "collections.placements", id=str(uuid.uuid4()), bank_id=B1, agency_id=A2, loan_id=loan2,
                status="ACTIVE", placed_on=date(2025, 1, 5), ended_on=None,
                exposure_at_placement=50_000, expected_recovery_inr=None,
                sla_first_visit_due=date(2025, 1, 12), dpd_bucket_at_placement="BUCKET_1")
        _insert(conn, "workforce.agents", id=agent2, bank_id=B1, agency_id=A2, user_id=user2, exited_on=None)
        _insert(conn, "collections.cases", id=case2, bank_id=B1, agency_id=A2, loan_id=loan2, customer_id=CUST,
                placement_id=None, agent_id=agent2)
        visit_at = datetime(2025, 1, 12, 11, 0, tzinfo=IST)
        _insert(conn, "collections.visits", bank_id=B1, agency_id=A2, case_id=case2, agent_id=agent2,
                check_in_time=visit_at, customer_met=True, within_contact_hours=True, geo_verified=True,
                consent_given=True)
        _insert(conn, "collections.payments", bank_id=B1, agency_id=A2, case_id=case2, loan_id=loan2,
                agent_id=agent2, amount=5_000, mode="CASH", status="VERIFIED",
                payment_date=visit_at + timedelta(days=1), settlement_offer_id=None)
    _refresh(book)

    with book.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL ROLE tiq_app"))
            database._set_tenant(conn, {"user_id": "t", "bank_id": B1, "agency_id": None, "scope": "BANK"})
            out = compute_tab(Session(bind=conn), Session(bind=conn), "recovery", B1, KpiFilter())
    assert out["available"], out["reason"]
    jan = next(r for r in out["panels"]["by_month"] if r["month_start"] == date(2025, 1, 1))

    with book.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL ROLE tiq_app"))
            database._set_tenant(conn, {"user_id": "t", "bank_id": B1, "agency_id": None, "scope": "BANK"})
            a2_collected = conn.execute(text(
                "SELECT verified_collections + bank_direct_collections FROM analytics.agency_scorecard_monthly_scoped "
                "WHERE bank_id = :b AND agency_id = :a AND month_start = '2025-01-01'"), {"b": B1, "a": A2}).scalar()
    assert a2_collected and float(a2_collected) > 0   # A2 really did contribute unpriced, nonzero collections
    restricted_actual = float(jan["actual_inr"]) - float(a2_collected)
    assert float(jan["recovery_vs_expected"]) == round(restricted_actual / float(jan["expected_inr"]), 4)


def test_cost_to_collect_breaks_commission_from_field_cost_by_month_and_agency(views):
    """C04 task #4: the Agencies scorecard already blends commission + field
    cost into one cost_per_100_inr; this tab breaks the two components out,
    bank-wide by month and by agency, off the same view."""
    from app.demo.roster import BANK
    from app.services.bank.analytics_catalog import compute_tab
    from app.services.bank.kpi_filter import KpiFilter
    with _session(views, BANK["id"]) as s:
        out = compute_tab(s, s, "cost", BANK["id"], KpiFilter())
    assert out["available"], out["reason"]
    by_month, by_agency = out["panels"]["by_month"], out["panels"]["by_agency"]
    assert by_month and sum(r["commission_inr"] for r in by_month) > 0
    assert by_agency and all(r["agency_name"] != r["agency_id"] for r in by_agency)
    priced = [r for r in by_agency if r["field_cost_inr"] is not None]
    assert priced and any(r["cost_per_100_inr"] is not None for r in priced)


def test_cost_per_100_pairs_its_numerator_and_denominator_over_the_same_rows(views):
    """by_agency sums every (agency, month) row of agency_scorecard_monthly_
    scoped for that agency. A month with no FIELD_VISIT rate in force has
    field_cost NULL on that one row while its own collections are still
    real -- SUM(field_cost) already drops it from the numerator, but plain
    SUM(verified_collections)+SUM(bank_direct_collections) kept counting
    that month's collections in the denominator, understating cost_per_100
    (coordinator audit, 2026-10-07; agency_scorecard.py:140's same-rows rule,
    not applied here before this fix). Verified against the view's own raw
    per-row data, independent of any number this test would otherwise have
    to hardcode -- and `found_mixed` proves an agency with BOTH a priced and
    an unpriced month actually exists in the fixture, so the assertion is
    not vacuously true of a book where every agency is uniformly priced or
    uniformly not."""
    from app.demo.roster import BANK
    from app.services.bank.analytics_catalog import compute_tab
    from app.services.bank.kpi_filter import KpiFilter
    with _session(views, BANK["id"]) as s:
        out = compute_tab(s, s, "cost", BANK["id"], KpiFilter())
        raw = s.execute(text(
            "SELECT agency_id, commission_accrued, field_cost, verified_collections, bank_direct_collections "
            "FROM analytics.agency_scorecard_monthly_scoped WHERE bank_id = :b"), {"b": BANK["id"]}).mappings().all()
    assert out["available"], out["reason"]

    commission, field_cost, known_collected, total_collected = {}, {}, {}, {}
    for r in raw:
        a = r["agency_id"]
        collected = float(r["verified_collections"] or 0) + float(r["bank_direct_collections"] or 0)
        commission[a] = commission.get(a, 0.0) + float(r["commission_accrued"] or 0)
        total_collected[a] = total_collected.get(a, 0.0) + collected
        if r["field_cost"] is not None:
            field_cost[a] = field_cost.get(a, 0.0) + float(r["field_cost"])
            known_collected[a] = known_collected.get(a, 0.0) + collected

    found_mixed = False
    for row in out["panels"]["by_agency"]:
        a = row["agency_id"]
        if a not in field_cost:
            assert row["cost_per_100_inr"] is None
            continue
        if known_collected[a] != total_collected[a]:
            found_mixed = True
        expected = (round((commission[a] + field_cost[a]) / known_collected[a] * 100, 2)
                   if known_collected[a] else None)
        assert row["cost_per_100_inr"] == expected, a
    assert found_mixed, "fixture drifted: no agency mixes a priced and an unpriced month any more"


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


def test_fraud_counts_respect_the_same_agency_filter_the_breach_columns_do(views):
    """fraud_by_month/fraud_by_agency used to query collections.fraud_reviews
    with no agency clause at all while breaches_over_time/by_agency honoured
    KpiFilter.agency on the same request -- a bank filtering the compliance
    tab to one agency saw that agency's own breach counts next to every
    agency's fraud count (coordinator audit, 2026-10-07). Aravalli (AGENCY)
    is the only agency with any CONFIRMED fraud review in this book (the 31
    rows are v1's, copied onto it alone -- see the test above); Sahyadri has
    none, so filtering to Sahyadri must show zero, not Aravalli's 31."""
    from app.demo.roster import AGENCY, BANK, SAHYADRI
    from app.services.bank.analytics_catalog import compute_tab
    from app.services.bank.kpi_filter import KpiFilter
    with _session(views, BANK["id"]) as s:
        unfiltered = compute_tab(s, s, "compliance", BANK["id"], KpiFilter())
        sahyadri = compute_tab(s, s, "compliance", BANK["id"], KpiFilter(agency=SAHYADRI.id))
        aravalli = compute_tab(s, s, "compliance", BANK["id"], KpiFilter(agency=AGENCY["id"]))
    assert unfiltered["available"] and sahyadri["available"], sahyadri["reason"]
    total_unfiltered = sum(r["fraud_confirmed"] for r in unfiltered["panels"]["breaches_over_time"])
    assert total_unfiltered > 0   # Aravalli's 31, vacuous otherwise
    assert sum(r["fraud_confirmed"] for r in sahyadri["panels"]["breaches_over_time"]) == 0
    assert sum(r["fraud_confirmed"] for r in sahyadri["panels"]["by_agency"]) == 0
    # Filtering TO Aravalli itself must still show its own count, unchanged.
    assert sum(r["fraud_confirmed"] for r in aravalli["panels"]["breaches_over_time"]) == total_unfiltered


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
        recovery = compute_tab(s, s, "recovery", BANK["id"], KpiFilter())
        cost = compute_tab(s, s, "cost", BANK["id"], KpiFilter())
        agencies = compute_tab(s, s, "agencies", BANK["id"], KpiFilter())
    assert not exposure["available"]
    assert recovery["available"] and recovery["panels"]["by_month"] == []   # the scoped view, same as exposure
    assert cost["available"] and cost["panels"]["by_month"] == [] and cost["panels"]["by_agency"] == []
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
