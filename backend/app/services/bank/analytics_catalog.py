"""The bank portal's Analytics tabs (plan §5.4, task C04). Each tab is a
function over the same scoped views C01/C02 already use, taking the same
`KpiFilter`; a dimension the view cannot carry is refused by
`KpiFilter.unsupported()` upstream in the endpoint, same as the Overview.

Two tabs in the spec are NOT built here, by design, not oversight:
- Field Operations' beat-adherence and planned-vs-actual-km panels: the
  `beats` table is empty for the whole bank (no generator ever writes a
  Beat row, demo or otherwise) — a generator gap, not a page bug. Its three
  other metrics (visits/agent/day, met rate, SLA coverage) ARE built.
- Recovery's target-line chart: there is no bank-set collection target
  anywhere in the schema. Recovery vs Expected (recovery_risk-predicted,
  §6.2) IS built; inventing a target number would be exactly the
  fabrication ADR 0005 exists to prevent. Flagged to the owner as a missing
  product concept, not this page's job to invent one.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

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
    # no join needed.
    clause, fp = f.clause(FIELD)
    base = {"bank": bank_id, **fp}
    breaches_over_time = _read_rows(adb, f"""
        SELECT date_trunc('month', activity_date)::date AS month_start,
               SUM(out_of_hours_attempts) AS out_of_hours, SUM(geo_unverified_visits) AS geofence,
               SUM(consent_missing_visits) AS consent_missing
        FROM analytics.{FIELD} WHERE bank_id = :bank {clause}
        GROUP BY 1 ORDER BY 1""", base)
    fraud_by_month = _read_rows(adb, """
        SELECT date_trunc('month', reviewed_at)::date AS month_start, count(*) AS fraud_confirmed
        FROM collections.fraud_reviews WHERE bank_id = :bank AND verdict = 'CONFIRMED' AND reviewed_at IS NOT NULL
        GROUP BY 1 ORDER BY 1""", {"bank": bank_id})
    for row in breaches_over_time:
        row["fraud_confirmed"] = next((r["fraud_confirmed"] for r in fraud_by_month
                                       if r["month_start"] == row["month_start"]), 0)
    by_agency = _read_rows(adb, f"""
        SELECT agency_id, SUM(out_of_hours_attempts) AS out_of_hours, SUM(geo_unverified_visits) AS geofence,
               SUM(visits) AS visits
        FROM analytics.{FIELD} WHERE bank_id = :bank {clause} GROUP BY agency_id""", base)
    fraud_by_agency = _read_rows(adb, """
        SELECT agency_id, count(*) AS fraud_confirmed FROM collections.fraud_reviews
        WHERE bank_id = :bank AND verdict = 'CONFIRMED' GROUP BY agency_id""", {"bank": bank_id})
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


TABS = ("exposure", "migration", "agencies", "compliance")


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
    as_of = f.end if f.period == "custom" else latest_reading(adb, bank_id)
    if as_of is None:
        return _unavailable("no portfolio reading for this bank yet")
    if tab == "exposure":
        return _exposure(adb, bank_id, as_of, f)
    if tab == "migration":
        return _migration(adb, bank_id, as_of, f)
    return _compliance(db, adb, bank_id, f)
