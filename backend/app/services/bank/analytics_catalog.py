"""The bank portal's Analytics tabs (plan §5.4, task C04). Each tab is a
function over the same scoped views C01/C02 already use, taking the same
`KpiFilter`; a dimension the view cannot carry is refused by
`KpiFilter.unsupported()` upstream in the endpoint, same as the Overview.

One tab in the spec is NOT built here, by design, not oversight:
- Field Operations' beat-adherence and planned-vs-actual-km panels: queued
  separately (its three other metrics — visits/agent/day, met rate, SLA
  coverage — ARE built).

Recovery (C04 task #5, owner ruling 2026-10-06): there is no bank-set
collection target anywhere in the schema, and inventing one would be the
fabrication ADR 0005 exists to prevent — so the target IS the model-
predicted recovery, not a new field. `expected_recovery_inr`
(services/bank/expected_recovery.py, written once per placement from the
recovery_risk prediction in force then) is already the Agencies tab's
Recovery-vs-Expected denominator; this tab is the same ratio, bank-wide,
by month, off the same scorecard view — no new computation.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session


def numberize(obj):
    """Postgres ``numeric`` columns arrive as ``Decimal``. ``AnalyticsTabOut.panels``
    is an untyped ``dict``, and Pydantic v2 serialises a ``Decimal`` inside one as a
    JSON *string* ("2.63"); the frontend formats these as numbers (``.toFixed``),
    which throws on a string and white-screens the tab. Convert every ``Decimal`` to
    ``float`` so the wire matches the typed ``number`` contract the panels declare."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: numberize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [numberize(v) for v in obj]
    return obj

from app.services.bank.agency_scorecard import agency_scorecard, latest_month
from app.services.bank.kpi_catalog import FIELD, PORTFOLIO, SCORECARD, TRANSITIONS, available_views, latest_reading
from app.services.bank.kpi_filter import KpiFilter

#: Tab id -> the views its queries need. The endpoint abstains the whole tab
#: (same shape as a KPI: available=False, reason) if one is missing, rather
#: than returning some panels and silently omitting others.
TAB_VIEWS: dict[str, tuple[str, ...]] = {
    "exposure": (PORTFOLIO,),
    "migration": (TRANSITIONS,),
    "agencies": (SCORECARD,),
    "recovery": (SCORECARD,),
    "cost": (SCORECARD,),
    "compliance": (FIELD,),
}


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason, "panels": {}}


def _read_rows(db: Session, sql: str, params: dict) -> list[dict]:
    # psycopg2 hands back a native uuid.UUID for a uuid column; every id this
    # module compares or keys a dict by is a plain str (User.id's own type,
    # and every demo id). An unstrung id made exactly this class of bug once
    # already this lane (generate_demo_logins_doc.py) — str() every value of
    # that type here, once, rather than at each call site.
    return [{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in r.items()}
           for r in db.execute(text(sql), params).mappings().all()]


# Every analytics.* read below runs on `adb`: the scoped views are
# security_invoker and only return this bank's rows when the session the
# endpoint bound (app.bank_id etc.) is the one querying them (bank.py's own
# comment on compute_overview). The plain `db` session is for ordinary ORM
# reads against tenancy.* (Agency), which carry no such scoping.

# ── Exposure ─────────────────────────────────────────────────────────────────
def _exposure(adb: Session, bank_id: str, as_of: date, f: KpiFilter) -> dict:
    # mv_portfolio_daily's real columns (v2_0013): as_of_date, dpd_bucket (CURRENT |
    # BUCKET_1..3 | NPA — no "write-off candidate" bucket exists, so the funnel
    # stops at NPA; a write-off stage needs a threshold someone decides, not
    # invented here), accounts (the grain is already grouped, no loan_id).
    clause, fp = f.clause(PORTFOLIO)
    base = {"bank": bank_id, "d": as_of, **fp}
    funnel = _read_rows(adb, f"""
        SELECT SUM(total_outstanding) AS book, SUM(delinquent_exposure) AS delinquent,
               SUM(placed_exposure) AS placed, SUM(npa_exposure) AS npa
        FROM analytics.{PORTFOLIO} WHERE bank_id = :bank AND as_of_date = :d {clause}""", base)
    ladder = _read_rows(adb, f"""
        SELECT dpd_bucket, SUM(total_outstanding) AS outstanding, SUM(accounts) AS accounts
        FROM analytics.{PORTFOLIO} WHERE bank_id = :bank AND as_of_date = :d {clause}
        GROUP BY dpd_bucket ORDER BY dpd_bucket""", base)
    heat = _read_rows(adb, f"""
        SELECT loan_type AS product, dpd_bucket, SUM(total_outstanding) AS outstanding
        FROM analytics.{PORTFOLIO} WHERE bank_id = :bank AND as_of_date = :d {clause}
        GROUP BY loan_type, dpd_bucket""", base)
    security = _read_rows(adb, f"""
        SELECT d.security_class AS security, SUM(p.total_outstanding) AS outstanding
        FROM analytics.{PORTFOLIO} p JOIN analytics.dim_product d ON d.loan_type = p.loan_type
        WHERE p.bank_id = :bank AND p.as_of_date = :d {clause} GROUP BY d.security_class""", base)
    return {"available": True, "reason": None,
           "panels": {"funnel": funnel[0] if funnel else {}, "dpd_ladder": ladder,
                      "product_bucket_heat": heat, "security_cover": security}}


# ── Migration ────────────────────────────────────────────────────────────────
def _migration(adb: Session, bank_id: str, as_of: date, f: KpiFilter) -> dict:
    clause, fp = f.clause(TRANSITIONS, "t")
    me = adb.execute(text(f"SELECT MAX(month_end) FROM analytics.{TRANSITIONS} t "
                          f"WHERE t.bank_id = :bank AND t.month_end <= :d"), {"bank": bank_id, "d": as_of}).scalar()
    if me is None:
        return _unavailable("no month-end transitions yet")
    base = {"bank": bank_id, "d": me, **fp}
    matrix = _read_rows(adb, f"""
        SELECT from_state, to_state, SUM(exposure_from) AS exposure, SUM(accounts) AS accounts
        FROM analytics.{TRANSITIONS} t WHERE t.bank_id = :bank AND t.month_end = :d {clause}
        GROUP BY from_state, to_state""", base)
    trajectory = _read_rows(adb, f"""
        SELECT month_end,
               SUM(exposure_from) FILTER (WHERE to_state NOT IN ('RESOLVED', 'NO_READING')
                   AND to_state <> from_state) AS rolled,
               SUM(exposure_from) FILTER (WHERE to_state = from_state) AS held,
               SUM(exposure_from) FILTER (WHERE to_state IN ('CURRENT', 'RESOLVED')
                   AND from_state <> 'CURRENT') AS cured
        FROM analytics.{TRANSITIONS} t
        WHERE t.bank_id = :bank AND t.month_end > (:d - interval '12 months') AND t.month_end <= :d {clause}
        GROUP BY month_end ORDER BY month_end""", base)
    return {"available": True, "reason": None,
           "panels": {"transition_matrix": matrix, "trajectory_12m": trajectory}}


# ── Recovery ─────────────────────────────────────────────────────────────────
def _recovery(adb: Session, bank_id: str, f: KpiFilter) -> dict:
    """Recovery vs Expected, bank-wide, by month — the Agencies tab's own
    ratio (agency_scorecard.compute_metrics's recovery_vs_expected), summed
    over every agency instead of one. The view coalesces a (agency, month)
    cell with no priced placement to 0, never NULL (v2_0013's `pn` CTE,
    `coalesce(sum(expected_recovery_inr), 0)`) -- 0 is this column's own
    "unpriced" sentinel, same job NULL does for `field_cost` in `_cost()`
    below, which genuinely stays NULL at the view (measured directly,
    test_unpriced_visits_read_null_until_a_rate_exists). Bank-wide SUM()
    across many agencies' zero-and-nonzero cells is never itself 0 unless
    EVERY cell that month is unpriced, which is what the `if expected else
    None` below reports as unknown.

    The RATIO's own numerator and denominator must come from the SAME rows
    (coordinator audit, 2026-10-07; agency_scorecard.py:140's own rule for
    its collection_efficiency, not applied here before this fix): a month
    where one agency's placement is unpriced (expected_recovery_inr = 0 on
    that cell) still has that agency's own collections, so plain SUM(...)
    counted them in actual_inr's numerator while the ratio's denominator
    only ever reflected the priced cells -- numerator-from-every-agency
    over denominator-from-only-the-priced-ones, not the same window's
    ratio. `actual_known_expected_inr` restricts the numerator to the cells
    the denominator actually counted (expected_recovery_inr <> 0, not a
    NULL check -- this column is never NULL at the view); `actual_inr`
    itself is left unfiltered because it is a displayed total, not a ratio
    term."""
    clause, fp = f.clause(SCORECARD)
    base = {"bank": bank_id, **fp}
    by_month = _read_rows(adb, f"""
        SELECT month_start,
               SUM(verified_collections) + SUM(bank_direct_collections) AS actual_inr,
               SUM(expected_recovery_inr) AS expected_inr,
               SUM(verified_collections) FILTER (WHERE expected_recovery_inr <> 0)
                 + SUM(bank_direct_collections) FILTER (WHERE expected_recovery_inr <> 0)
                 AS actual_known_expected_inr
        FROM analytics.{SCORECARD} WHERE bank_id = :bank {clause}
        GROUP BY month_start ORDER BY month_start""", base)
    for row in by_month:
        expected = row["expected_inr"]
        actual_known = row.pop("actual_known_expected_inr")
        row["recovery_vs_expected"] = round(actual_known / expected, 4) if expected else None
    return {"available": True, "reason": None, "panels": {"by_month": by_month}}


# ── Cost to collect ──────────────────────────────────────────────────────────
def _cost(db: Session, adb: Session, bank_id: str, f: KpiFilter) -> dict:
    """Channel economics (C04 task #4): commission and field cost, per month
    bank-wide and per agency — the Agencies tab's own cost_per_100_inr
    (agency_scorecard.compute_metrics), broken into its two components
    instead of one blended ratio, and surfaced here as its own tab rather
    than only buried in one scorecard column. field_cost is None (never 0)
    when any visit that period has no FIELD_VISIT cost rate — unknown, not
    free (agency_scorecard.py's own rule, same column)."""
    from app.models.tenancy import Agency

    clause, fp = f.clause(SCORECARD)
    base = {"bank": bank_id, **fp}

    def _rows(group_col: str) -> list[dict]:
        rows = _read_rows(adb, f"""
            SELECT {group_col} AS key, SUM(commission_accrued) AS commission_inr, SUM(field_cost) AS field_cost_inr,
                   SUM(verified_collections) + SUM(bank_direct_collections) AS collected_inr,
                   SUM(verified_collections) FILTER (WHERE field_cost IS NOT NULL)
                     + SUM(bank_direct_collections) FILTER (WHERE field_cost IS NOT NULL)
                     AS collected_known_cost_inr
            FROM analytics.{SCORECARD} WHERE bank_id = :bank {clause}
            GROUP BY {group_col} ORDER BY {group_col}""", base)
        for row in rows:
            # Same-rows pairing (coordinator audit, 2026-10-07; agency_scorecard.py:140's
            # own rule, not applied here before this fix): a group spanning several
            # agencies/months can have SOME rows with no FIELD_VISIT rate (field_cost
            # NULL) and others with one. Plain SUM(field_cost) already drops the
            # unknown rows from the numerator; collected_inr's plain SUM did not drop
            # them from the denominator, understating cost_per_100_inr whenever any
            # row in the group lacked a rate. collected_known_cost_inr restricts the
            # denominator to the rows the numerator actually counted; collected_inr
            # itself is left unfiltered because it is a displayed total, not a ratio
            # term.
            collected_known = row.pop("collected_known_cost_inr")
            row["cost_per_100_inr"] = (
                round((row["commission_inr"] + row["field_cost_inr"]) / collected_known * 100, 2)
                if row["field_cost_inr"] is not None and collected_known else None)
        return rows

    by_month = _rows("month_start")
    by_agency = _rows("agency_id")
    for row in by_agency:
        row["agency_id"] = row.pop("key")
    for row in by_month:
        row["month_start"] = row.pop("key")
    names = {a.id: (a.trade_name or a.legal_name) for a in db.query(Agency).filter(Agency.bank_id == bank_id)}
    for row in by_agency:
        row["agency_name"] = names.get(row["agency_id"], row["agency_id"])
    return {"available": True, "reason": None, "panels": {"by_month": by_month, "by_agency": by_agency}}


# ── Agencies ─────────────────────────────────────────────────────────────────
def _agencies(db: Session, adb: Session, bank_id: str, f: KpiFilter) -> dict:
    """A thin re-shape of agency_scorecard.py (§6.2's own scorecard
    definition) over every agency the filter leaves in scope — no new
    arithmetic, reusing the already-tested Performance Index / shrinkage."""
    from app.models.tenancy import Agency

    # Agency has no region_id of its own (coverage is many-to-many via
    # tenancy.agency_regions); geo narrows the SCORECARD rows inside
    # agency_scorecard(), not which agencies appear as cards.
    q = db.query(Agency).filter(Agency.bank_id == bank_id)
    if f.agency:
        q = q.filter(Agency.id == f.agency)
    agencies = q.order_by(Agency.code).all()
    last = latest_month(adb, bank_id=bank_id)
    cards = [{"agency_id": a.id, "code": a.code, "name": a.trade_name or a.legal_name, "status": a.status,
             **agency_scorecard(adb, bank_id=bank_id, agency_id=a.id, region_id=f.geo, month_start=last)}
            for a in agencies]
    return {"available": True, "reason": None, "panels": {"scorecards": cards}}


# ── Compliance ───────────────────────────────────────────────────────────────
def _compliance(db: Session, adb: Session, bank_id: str, f: KpiFilter) -> dict:
    # mv_field_activity_daily's real columns (v2_0007): activity_date,
    # out_of_hours_attempts, geo_unverified_visits, consent_missing_visits —
    # no fraud column on this view (its grain is visits/calls/PTPs/payments
    # per agent-day). Fraud verdicts live on collections.fraud_reviews, which
    # carries its own bank_id/agency_id directly, so a plain filtered count,
    # no join needed. `clause`/`base` apply to fraud_reviews too (both carry
    # the same agency_id column FIELD_DIMENSIONS gates on) -- they used to be
    # skipped here while the breach columns above honoured them, so a bank
    # filtering to one agency saw that agency's own breaches next to every
    # agency's fraud count (coordinator audit, 2026-10-07).
    clause, fp = f.clause(FIELD)
    base = {"bank": bank_id, **fp}
    breaches_over_time = _read_rows(adb, f"""
        SELECT date_trunc('month', activity_date)::date AS month_start,
               SUM(out_of_hours_attempts) AS out_of_hours, SUM(geo_unverified_visits) AS geofence,
               SUM(consent_missing_visits) AS consent_missing
        FROM analytics.{FIELD} WHERE bank_id = :bank {clause}
        GROUP BY 1 ORDER BY 1""", base)
    fraud_by_month = _read_rows(adb, f"""
        SELECT date_trunc('month', reviewed_at)::date AS month_start, count(*) AS fraud_confirmed
        FROM collections.fraud_reviews WHERE bank_id = :bank AND verdict = 'CONFIRMED' AND reviewed_at IS NOT NULL
        {clause} GROUP BY 1 ORDER BY 1""", base)
    for row in breaches_over_time:
        row["fraud_confirmed"] = next((r["fraud_confirmed"] for r in fraud_by_month
                                       if r["month_start"] == row["month_start"]), 0)
    by_agency = _read_rows(adb, f"""
        SELECT agency_id, SUM(out_of_hours_attempts) AS out_of_hours, SUM(geo_unverified_visits) AS geofence,
               SUM(visits) AS visits
        FROM analytics.{FIELD} WHERE bank_id = :bank {clause} GROUP BY agency_id""", base)
    fraud_by_agency = _read_rows(adb, f"""
        SELECT agency_id, count(*) AS fraud_confirmed FROM collections.fraud_reviews
        WHERE bank_id = :bank AND verdict = 'CONFIRMED' {clause} GROUP BY agency_id""", base)
    for row in by_agency:
        row["fraud_confirmed"] = next((r["fraud_confirmed"] for r in fraud_by_agency
                                       if r["agency_id"] == row["agency_id"]), 0)
    # The view carries agency_id only (collections.fraud_reviews too) — a
    # plain ORM lookup for the name, same as _agencies()'s own cards, rather
    # than showing the bank an agency by its raw id.
    from app.models.tenancy import Agency

    names = {a.id: (a.trade_name or a.legal_name) for a in db.query(Agency).filter(Agency.bank_id == bank_id)}
    for row in by_agency:
        row["agency_name"] = names.get(row["agency_id"], row["agency_id"])
    return {"available": True, "reason": None,
           "panels": {"breaches_over_time": breaches_over_time, "by_agency": by_agency}}


TABS = ("exposure", "migration", "agencies", "recovery", "cost", "compliance")


def compute_tab(db: Session, adb: Session, tab: str, bank_id: str, f: KpiFilter) -> dict:
    """Self-contained like compute_overview: derives its own `as_of` from
    `adb` (the tenant-bound analytics session) rather than taking it from
    the caller, so there is one place that can get the reading date wrong."""
    if tab not in TABS:
        return _unavailable(f"no such tab {tab!r}")
    views = available_views(adb)
    missing = [v for v in TAB_VIEWS[tab] if v not in views]
    if missing:
        return _unavailable(f"analytics view pending ({', '.join(missing)})")
    unsupported = f.unsupported(TAB_VIEWS[tab])
    if unsupported:
        return _unavailable(f"cannot be filtered by {', '.join(sorted(unsupported))} on this tab")
    if tab == "agencies":
        return _agencies(db, adb, bank_id, f)
    if tab == "recovery":
        return _recovery(adb, bank_id, f)
    if tab == "cost":
        return _cost(db, adb, bank_id, f)
    as_of = f.end if f.period == "custom" else latest_reading(adb, bank_id)
    if as_of is None:
        return _unavailable("no portfolio reading for this bank yet")
    if tab == "exposure":
        return _exposure(adb, bank_id, as_of, f)
    if tab == "migration":
        return _migration(adb, bank_id, as_of, f)
    return _compliance(db, adb, bank_id, f)
