"""The bank Command Center's KPIs, each defined ONCE (plan §5.3, task C01).

Every KPI here is a label, a unit, a direction, a basis (the text a user sees
under "how is this computed") and one SQL expression over the analytics
layer's `*_scoped` views. Those views are security_invoker and filter on the
caller's bank; the queries ALSO name `bank_id = :bank`, so a missing session
setting can only narrow a result, never widen it. The API never reads an
`mv_*` directly (43: the app role has no grant on them).

A KPI that cannot be computed says so, with the reason (a view not yet
built, a definition still pending, no reading on that date). It never shows
a zero or a guess in its place.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.bank.kpi_filter import DIMENSION_LABELS, SENTINEL, KpiFilter

logger = structlog.get_logger()

PORTFOLIO = "portfolio_daily_scoped"
TRANSITIONS = "bucket_transitions_monthly_scoped"
COLLECTIONS = "collections_daily_scoped"
FIELD = "field_activity_daily_scoped"
SCORECARD = "agency_scorecard_monthly_scoped"
VISIT_TO_PAY = "v_visit_to_pay"
VIEWS = (PORTFOLIO, TRANSITIONS, COLLECTIONS, FIELD, SCORECARD, VISIT_TO_PAY)
#: A visit needs 7 days to have its chance of a payment (v_visit_to_pay's window).
VISIT_MATURITY_DAYS = 7

CR, LAKH = 1e7, 1e5


# ── formatting: the server's KPI strings (frontend bank/theme/format.ts pulseMoney) ──
def money(v: float) -> str:
    return f"₹{v / CR:,.1f} Cr" if abs(v) >= CR else f"₹{v / LAKH:,.1f} L"


def pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def count(v: float) -> str:
    return f"{int(round(v)):,}"


def short_date(d: date) -> str:
    return d.strftime("%d %b %Y")


@dataclass(frozen=True)
class KpiDef:
    id: str
    label: str
    row: str                       # "book" (where the book stands) | "outcome" (what came back)
    unit: str                      # "inr" | "pct" | "score" | "rs" (rupees per ₹100)
    higher_is_better: bool
    basis: str
    drill: str                     # the analytics tab a click opens (plan §5.4)
    kind: str                      # "stock" | "month" | "transition" | "visits" (matured, per visit) | "pending"
    views: tuple = ()
    sql: str = ""                  # returns one row: value, aux
    sub: Callable[[dict], str] | None = None
    pending: str = ""              # why there is no definition yet (kind == "pending")
    alias: str = ""                # the table alias the filter clause qualifies (transitions: "t")


# Stock KPIs read one date of portfolio_daily_scoped. {cohort} narrows the
# comparison to rows read at BOTH dates (like for like), when the view can say so.
_P = f"FROM analytics.{PORTFOLIO} WHERE bank_id = :bank AND as_of_date = :d {{cohort}}"

KPIS: tuple[KpiDef, ...] = (
    # ── Row 1: where the book stands ───────────────────────────────────────
    KpiDef(
        "delinquent_exposure", "Delinquent Exposure", "book", "inr", False,
        "Total outstanding on loans with DPD above 0, at the reading date. Sub-line: that as a share of the "
        "whole book's outstanding.",
        "exposure", "stock", (PORTFOLIO,),
        f"SELECT SUM(delinquent_exposure) AS value, SUM(delinquent_exposure) / NULLIF(SUM(total_outstanding), 0) "
        f"AS aux {_P}",
        lambda r: f"{pct(r['aux'])} of the book" if r.get("aux") is not None else "share of book not available"),
    KpiDef(
        "placed_share", "Placed with Agencies", "book", "pct", True,
        "Delinquent exposure that is placed with an agency, divided by all delinquent exposure, at the "
        "reading date (a placed loan that has since cured is not counted). Sub-line: agencies holding "
        "placed accounts.",
        "agencies", "stock", (PORTFOLIO,),
        f"SELECT SUM(placed_exposure) FILTER (WHERE dpd_bucket <> 'CURRENT') / NULLIF(SUM(delinquent_exposure), 0) "
        f"AS value, "
        f"COUNT(DISTINCT agency_id) FILTER (WHERE placed_accounts > 0 AND agency_id IS NOT NULL "
        f"AND agency_id <> '{SENTINEL}') AS aux {_P}",
        lambda r: f"{count(r['aux'] or 0)} {'agency' if (r['aux'] or 0) == 1 else 'agencies'} holding placements"),
    KpiDef(
        "unworked_exposure", "Unworked Exposure", "book", "inr", False,
        "Placed exposure with no field visit or call in the 7 days to the reading date (placed exposure minus "
        "placed exposure contacted in those 7 days). Sub-line: share of placed exposure.",
        "fieldops", "stock", (PORTFOLIO,),
        f"SELECT SUM(placed_exposure) - SUM(contacted_7d_exposure) AS value, "
        f"(SUM(placed_exposure) - SUM(contacted_7d_exposure)) / NULLIF(SUM(placed_exposure), 0) AS aux {_P}",
        lambda r: f"{pct(r['aux'])} of placed exposure" if r.get("aux") is not None else ""),
    KpiDef(
        "gnpa_pct", "GNPA %", "book", "pct", False,
        "NPA exposure (DPD above 90) divided by the whole book's outstanding, at the reading date. Sub-line: "
        "NPA accounts.",
        "migration", "stock", (PORTFOLIO,),
        f"SELECT SUM(npa_exposure) / NULLIF(SUM(total_outstanding), 0) AS value, SUM(npa_accounts) AS aux {_P}",
        lambda r: f"{count(r['aux'] or 0)} accounts in NPA"),
    KpiDef(
        "roll_forward_rate", "Roll-Forward Rate", "book", "pct", False,
        "Exposure-weighted share of loans that moved to a worse portfolio state between the last two "
        "month-ends (states and their order: analytics.dim_portfolio_state). Only loans read at both "
        "month-ends count; pairs excluded as stale or missing are shown, never carried forward.",
        "migration", "transition", (TRANSITIONS,),
        f"""SELECT SUM(t.exposure_from) FILTER (WHERE s2.sort_order > s1.sort_order
                                                   AND t.to_state NOT IN ('RESOLVED', 'NO_READING'))
                  / NULLIF(SUM(t.exposure_from) FILTER (WHERE t.to_state <> 'NO_READING'), 0) AS value,
                  SUM(t.excluded_stale_pairs) + SUM(t.excluded_missing_pairs) AS aux
           FROM analytics.{TRANSITIONS} t
           JOIN analytics.dim_portfolio_state s1 ON s1.state = t.from_state
           LEFT JOIN analytics.dim_portfolio_state s2 ON s2.state = t.to_state
           WHERE t.bank_id = :bank AND t.month_end = :d AND t.from_state NOT IN ('WRITTEN_OFF', 'RESOLVED')""",
        lambda r: f"{count(r['aux'] or 0)} pairs excluded (stale or missing)", alias="t"),
    KpiDef(
        "cure_rate", "Cure Rate", "book", "pct", True,
        "Exposure-weighted share of delinquent loans (any state after CURRENT, before WRITTEN_OFF) that "
        "returned to CURRENT or were RESOLVED between the last two month-ends. Loans with no reading at the "
        "later month-end are left out, not counted as uncured.",
        "migration", "transition", (TRANSITIONS,),
        f"""SELECT SUM(t.exposure_from) FILTER (WHERE t.to_state IN ('CURRENT', 'RESOLVED'))
                  / NULLIF(SUM(t.exposure_from) FILTER (WHERE t.to_state <> 'NO_READING'), 0) AS value,
                  SUM(t.accounts) FILTER (WHERE t.to_state IN ('CURRENT', 'RESOLVED')) AS aux
           FROM analytics.{TRANSITIONS} t
           WHERE t.bank_id = :bank AND t.month_end = :d
             AND t.from_state NOT IN ('CURRENT', 'WRITTEN_OFF', 'RESOLVED')""",
        lambda r: f"{count(r['aux'] or 0)} accounts cured", alias="t"),
    # ── Row 2: what came back, what it cost, how it was done ───────────────
    # Month to date, from the agency scorecard: its money, placement, visit and
    # breach columns sum across rows (43's grain; agent columns sit apart).
    KpiDef(
        "collection_efficiency", "Collection Efficiency", "outcome", "pct", True,
        "Collections verified through agencies divided by collectible due, month to date. Payments made to "
        "the bank directly are shown beside it, not in it (DATA-MODEL-V2 Q23). An agency or region with no "
        "opening reading that month is left out of both sides, not zeroed; a selection narrowed to only such "
        "an agency or region is itself unavailable, never a guess.",
        "recovery", "month", (SCORECARD,),
        # L6, 2026-09-30: was a single blanket guard — ANY row anywhere in the
        # selection lacking a reading nulled the WHOLE value, so Aravalli's
        # permanent pre-anchor gap (fixtures/README.md) blanked this KPI for
        # every other agency too. FILTER excludes an unread row from BOTH
        # sums consistently: the bank-wide read is real (fewer agencies, same
        # abstain-don't-impute rule, ADR 0005), and a selection narrowed to
        # just an unread agency sums zero rows on both sides -> NULL, exactly
        # like the ordinary "no reading" path.
        f"""SELECT SUM(verified_collections) FILTER (WHERE collectible_due IS NOT NULL)
                  / NULLIF(SUM(collectible_due) FILTER (WHERE collectible_due IS NOT NULL), 0) AS value,
                   SUM(bank_direct_collections) AS aux
            FROM analytics.{SCORECARD} WHERE bank_id = :bank AND month_start BETWEEN :m0 AND :m1""",
        lambda r: f"{money(float(r['aux'] or 0))} more paid to the bank directly"),
    KpiDef(
        "resolution_rate", "Resolution Rate", "outcome", "pct", True,
        "Placements resolved (paid, closed or settled) this month, divided by placements active at any point "
        "in the month (active at month-end + resolved + recalled).",
        "agencies", "month", (SCORECARD,),
        f"""SELECT SUM(resolved_placements)
                   / NULLIF(SUM(active_placements_eom) + SUM(resolved_placements) + SUM(recalled_placements), 0) AS value,
                   SUM(resolved_placements) AS aux
            FROM analytics.{SCORECARD} WHERE bank_id = :bank AND month_start BETWEEN :m0 AND :m1""",
        lambda r: f"{count(r['aux'] or 0)} placements resolved"),
    KpiDef(
        "ptp_keep_rate", "PTP Keep Rate", "outcome", "pct", True,
        "Promises honoured divided by promises that fell due this month (matured promises only).",
        "recovery", "month", (SCORECARD,),
        f"""SELECT SUM(ptps_honoured)::numeric / NULLIF(SUM(ptps_matured), 0) AS value, SUM(ptps_matured) AS aux
            FROM analytics.{SCORECARD} WHERE bank_id = :bank AND month_start BETWEEN :m0 AND :m1""",
        lambda r: f"of {count(r['aux'] or 0)} matured promises"),
    KpiDef(
        "visit_to_pay", "Visit-to-Pay Conversion", "outcome", "pct", True,
        "Met visits followed by a verified payment on the same case within 7 days, divided by met visits, "
        "over the period's visits that are at least 7 days old (a younger visit has not had its chance yet).",
        "fieldops", "visits", (VISIT_TO_PAY,),
        f"""SELECT AVG(CASE WHEN paid_within_7d THEN 1.0 ELSE 0.0 END) AS value, COUNT(*) AS aux
            FROM analytics.{VISIT_TO_PAY} WHERE bank_id = :bank AND customer_met AND visit_date BETWEEN :lo AND :hi""",
        lambda r: f"of {count(r['aux'] or 0)} matured met visits"),
    KpiDef(
        "cost_to_collect", "Cost to Collect", "outcome", "rs", False,
        "Agency commission accrued plus field cost, per ₹100 collected through agencies, month to date. "
        "Field cost counts only where a FIELD_VISIT cost rate exists; the sub-line says when any visited cell has "
        "none (a cell with no visits costs 0 either way).",
        "cost", "month", (SCORECARD,),
        f"""SELECT 100.0 * (SUM(commission_accrued) + COALESCE(SUM(field_cost), 0))
                   / NULLIF(SUM(verified_collections), 0) AS value,
                   COUNT(*) FILTER (WHERE visits > 0 AND field_cost IS NULL) AS aux
            FROM analytics.{SCORECARD} WHERE bank_id = :bank AND month_start BETWEEN :m0 AND :m1""",
        lambda r: "per ₹100 collected (commission + field cost)" if not r.get("aux")
        else "per ₹100 collected — commission only: field cost not costed (no FIELD_VISIT rate yet)"),
    KpiDef(
        "compliance_integrity", "Compliance & Integrity", "outcome", "score", True,
        "100 minus breaches per 100 visits, month to date. Breaches, each weighted 1: attempts refused for "
        "contact hours, visits outside the 100 m fence, confirmed fraud findings, visits without recorded "
        "consent.",
        "compliance", "month", (SCORECARD,),
        f"""SELECT 100 - 100.0 * (SUM(breaches_out_of_hours) + SUM(breaches_geofence) + SUM(fraud_confirmed)
                                  + SUM(consent_missing)) / NULLIF(SUM(visits), 0) AS value, SUM(visits) AS aux
            FROM analytics.{SCORECARD} WHERE bank_id = :bank AND month_start BETWEEN :m0 AND :m1""",
        lambda r: f"over {count(r['aux'] or 0)} visits"),
)

ROWS = (
    {"id": "book", "caption": "Where the book stands", "kpis": [k.id for k in KPIS if k.row == "book"]},
    {"id": "outcome", "caption": "What came back, what it cost, how it was done",
     "kpis": [k.id for k in KPIS if k.row == "outcome"]},
)


# ── reading the views ────────────────────────────────────────────────────────
def available_views(db: Session) -> set[str]:
    """The scoped views that exist here. SQLite (the test database) has none."""
    if db.get_bind().dialect.name != "postgresql":
        return set()
    names = db.execute(text("SELECT table_name FROM information_schema.views "
                            "WHERE table_schema = 'analytics' AND table_name = ANY(:v)"),
                       {"v": list(VIEWS)}).scalars().all()
    return set(names)


def _has_column(db: Session, view: str, column: str) -> bool:
    return bool(db.execute(text("SELECT 1 FROM information_schema.columns WHERE table_schema = 'analytics' "
                                "AND table_name = :t AND column_name = :c"), {"t": view, "c": column}).first())


def latest_reading(db: Session, bank_id: str) -> date | None:
    return db.execute(text(f"SELECT MAX(as_of_date) FROM analytics.{PORTFOLIO} WHERE bank_id = :bank"),
                      {"bank": bank_id}).scalar()


def _month_end_before(d: date) -> date:
    return d.replace(day=1) - timedelta(days=1)


@dataclass
class Reading:
    value: float | None
    aux: object = None


def _read(db: Session, sql: str, params: dict) -> Reading:
    row = db.execute(text(sql), params).mappings().first()
    if row is None or row["value"] is None:
        return Reading(None)
    return Reading(float(row["value"]), row["aux"])


def _fmt(unit: str, v: float) -> str:
    if unit == "rs":
        return f"₹{v:.2f}"
    return money(v) if unit == "inr" else pct(v) if unit == "pct" else f"{v:.1f}"


def _trend(k: KpiDef, now: float, prev: float | None, against: str) -> tuple[str, bool | None, bool | None]:
    if prev is None:
        return f"no comparable reading {against}", None, None
    if k.unit == "pct":
        delta, text_ = (now - prev) * 100, f"{(now - prev) * 100:+.1f} pp {against}"
    elif k.unit == "score":
        delta, text_ = now - prev, f"{now - prev:+.1f} {against}"
    else:
        if prev == 0:
            return f"no base {against}", None, None
        delta = (now - prev) / abs(prev) * 100
        text_ = f"{delta:+.1f}% {against}"
    if abs(delta) < 0.05:
        return text_, None, None
    up = delta > 0
    return text_, up, up == k.higher_is_better


@dataclass
class Overview:
    as_of: date | None
    kpis: list[dict] = field(default_factory=list)
    totals: list[dict] = field(default_factory=list)
    narrative: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def compute_overview(db: Session, bank_id: str, f: KpiFilter | None = None) -> Overview:
    f = f or KpiFilter()
    views = available_views(db)
    as_of = None
    if PORTFOLIO in views:
        as_of = f.end if f.period == "custom" else latest_reading(db, bank_id)
    ov = Overview(as_of=as_of)
    like_for_like = PORTFOLIO in views and _has_column(db, PORTFOLIO, "is_backfill")
    for k in KPIS:
        # One KPI must never take the page down. A KPI that raises (a slow query
        # killed by statement_timeout, a None where a tuple is unpacked, a bad
        # cast) becomes one "unavailable" tile, not a 500 on /bank/overview.
        #
        # Each KPI runs inside a SAVEPOINT: a DB error aborts the transaction, so
        # without one the NEXT KPI's query fails with InFailedSqlTransaction and
        # the whole page cascades. Rolling back the savepoint returns the session
        # to a usable state for the remaining KPIs.
        sp = db.begin_nested()
        try:
            kpi = _one(db, k, bank_id, as_of, views, like_for_like, f)
            sp.commit()
            ov.kpis.append(kpi)
        except Exception:
            sp.rollback()
            logger.exception("kpi.compute_failed", kpi=k.id, bank_id=bank_id)
            ov.kpis.append(_unavailable(k, "could not be computed"))
    if as_of is not None:
        ov.totals = _totals(db, bank_id, as_of, views, f)
        ov.notes = _notes(db, bank_id, as_of, like_for_like, f)
    ov.narrative = narrative(ov.kpis)
    return ov


def _unavailable(k: KpiDef, reason: str) -> dict:
    # The reason goes on the sub-line, which wraps; the trend line is one line and truncates.
    return {"id": k.id, "label": k.label, "value": "—", "sub": reason[:1].upper() + reason[1:], "trend": "Not available",
            "trendUp": None, "good": None, "basis": f"{k.basis} Not available: {reason}.", "drill": k.drill,
            "available": False, "reason": reason}


def _month_span(lo: date, hi: date) -> tuple[date, date, date, date, int]:
    """The months the window touches, and the same number of months before them."""
    m0, m1 = lo.replace(day=1), hi.replace(day=1)
    span = (m1.year - m0.year) * 12 + m1.month - m0.month + 1
    pm1 = _month_end_before(m0).replace(day=1)
    pm0 = pm1
    for _ in range(span - 1):
        pm0 = _month_end_before(pm0).replace(day=1)
    return m0, m1, pm0, pm1, span


def _one(db: Session, k: KpiDef, bank: str, as_of: date | None, views: set[str], like_for_like: bool,
         f: KpiFilter) -> dict:
    if k.kind == "pending":
        return _unavailable(k, f"definition pending — {k.pending}")
    missing = [v for v in k.views if v not in views]
    if missing:
        return _unavailable(k, f"analytics view pending ({', '.join(missing)})")
    unsupported = f.unsupported(k.views)
    if unsupported:
        names = " or ".join(DIMENSION_LABELS[d] for d in sorted(unsupported))
        return _unavailable(k, f"cannot be filtered by {names} (its view has no such dimension)")
    if as_of is None:
        return _unavailable(k, "no portfolio reading for this bank yet")
    clause, fp = f.clause(k.views[0], k.alias)
    base = {"bank": bank, **fp}
    if k.kind == "stock":
        sql = k.sql + " " + clause                    # still carries {cohort}
        prev_d = _month_end_before(as_of)
        now = _read(db, sql.format(cohort=""), {**base, "d": as_of})
        # The delta compares only rows read at both dates, where the view can say which those are.
        cohort = "AND NOT is_backfill" if like_for_like else ""
        base_now = _read(db, sql.format(cohort=cohort), {**base, "d": as_of}) if cohort else now
        prev = _read(db, sql.format(cohort=cohort), {**base, "d": prev_d})
        against = f"vs {prev_d:%d %b}"          # short enough for the card; like-for-like is in the basis
        trend = _trend(k, base_now.value, prev.value, against) if base_now.value is not None else None
    elif k.kind == "month":
        sql = k.sql + " " + clause
        m0, m1, pm0, pm1, span = _month_span(*f.window(as_of))
        now = _read(db, sql, {**base, "m0": m0, "m1": m1})
        prev = _read(db, sql, {**base, "m0": pm0, "m1": pm1})
        label = "vs last month" if span == 1 else f"vs prior {span} months"
        trend = _trend(k, now.value, prev.value, label) if now.value is not None else None
    elif k.kind == "visits":
        sql = k.sql + " " + clause
        lo, hi = f.window(as_of)
        hi = min(hi, as_of - timedelta(days=VISIT_MATURITY_DAYS))
        if hi < lo:
            return _unavailable(k, f"no visit in this period is {VISIT_MATURITY_DAYS} days old yet")
        span = (hi - lo).days + 1
        now = _read(db, sql, {**base, "lo": lo, "hi": hi})
        prev = _read(db, sql, {**base, "lo": lo - timedelta(days=span), "hi": lo - timedelta(days=1)})
        trend = _trend(k, now.value, prev.value, f"vs prior {span} days") if now.value is not None else None
    else:  # transition: the latest month-end on or before the reading, against the one before
        sql = k.sql + " " + clause
        me = db.execute(text(f"SELECT MAX(month_end) FROM analytics.{TRANSITIONS} WHERE bank_id = :bank "
                             f"AND month_end <= :d"), {"bank": bank, "d": as_of}).scalar()
        if me is None:
            return _unavailable(k, "no month-end transitions yet")
        now = _read(db, sql, {**base, "d": me})
        prev = _read(db, sql, {**base, "d": _month_end_before(me)})
        trend = _trend(k, now.value, prev.value, "vs prior month") if now.value is not None else None
    if now.value is None:
        return _unavailable(k, "no reading for this selection")
    # A value with no comparable prior (e.g. a stock KPI whose like-for-like
    # cohort was empty last period) shows the figure without a trend, rather than
    # unpacking None. The value is real; only the comparison is missing.
    trend_text, up, good = trend if trend is not None else ("No prior-period comparison", None, None)
    basis = k.basis
    if k.kind == "stock" and like_for_like:
        basis += " The change is like for like: only loans read on both dates."
    return {"id": k.id, "label": k.label, "value": _fmt(k.unit, now.value),
            "sub": k.sub({"aux": now.aux}) if k.sub else "", "trend": trend_text, "trendUp": up, "good": good,
            "basis": basis, "drill": k.drill, "available": True, "reason": None, "raw": now.value}


_TOTAL_QUERIES = (
    (COLLECTIONS, "SELECT SUM(verified_amount) AS v FROM analytics.{v} WHERE bank_id = :bank "
                  "AND collection_date BETWEEN :lo AND :hi {c}",
     (("Verified collections", "v", "money", "payments verified in the period, bank-direct included"),)),
    (FIELD, "SELECT SUM(visits) AS v, COUNT(DISTINCT agent_id) FILTER (WHERE visits > 0) AS g "
            "FROM analytics.{v} WHERE bank_id = :bank AND activity_date BETWEEN :lo AND :hi {c}",
     (("Field visits", "v", "count", "visits recorded in the period"),
      ("Agents in the field", "g", "count", "agents with at least one visit in the period"))),
)


def _totals(db: Session, bank: str, as_of: date, views: set[str], f: KpiFilter) -> list[dict]:
    out = []
    if PORTFOLIO in views:
        clause, fp = f.clause(PORTFOLIO)
        r = db.execute(text(f"SELECT SUM(accounts) a, SUM(total_outstanding) o, SUM(placed_accounts) p "
                            f"FROM analytics.{PORTFOLIO} WHERE bank_id = :bank AND as_of_date = :d "
                            + clause),
                       {"bank": bank, "d": as_of, **fp}).mappings().first()
        if r and r["a"] is not None:
            out += [{"label": "Accounts", "value": count(r["a"]), "basis": f"loans read on {short_date(as_of)}"},
                    {"label": "Outstanding", "value": money(float(r["o"] or 0)), "basis": "total outstanding"},
                    {"label": "Placed accounts", "value": count(r["p"] or 0),
                     "basis": "loans placed with an agency"}]
    lo, hi = f.window(as_of)
    fmt = {"money": lambda v: money(v), "count": lambda v: count(v)}
    # Collections and field activity carry the agency dimension only.
    for view, sql, cols in _TOTAL_QUERIES:
        if view not in views or f.unsupported((view,)):
            continue
        clause, fp = f.clause(view)
        r = db.execute(text(sql.format(v=view, c=clause)), {"bank": bank, "lo": lo, "hi": hi, **fp}).mappings().first()
        if r and r["v"] is not None:
            out += [{"label": lab, "value": fmt[kind](float(r[col] or 0)), "basis": basis}
                    for lab, col, kind, basis in cols]
    return out


def _notes(db: Session, bank: str, as_of: date, like_for_like: bool, f: KpiFilter) -> list[str]:
    """Where a reading is a backfill rather than a point-in-time observation, say so."""
    if not like_for_like:
        return ["Some agencies' figures on this date may be a transformed snapshot rather than a point-in-time "
                "reading; the analytics view does not yet mark which, so month-on-month changes include them."]
    clause, fp = f.clause(PORTFOLIO)
    r = db.execute(text(f"SELECT SUM(accounts) a, SUM(total_outstanding) o FROM analytics.{PORTFOLIO} "
                        f"WHERE bank_id = :bank AND as_of_date = :d AND is_backfill " + clause),
                   {"bank": bank, "d": as_of, **fp}).mappings().first()
    if r and r["a"]:
        return [f"{count(r['a'])} accounts ({money(float(r['o'] or 0))}) on {short_date(as_of)} are a current "
                f"snapshot with no history before that date; changes are compared like for like without them."]
    return []


# ── the narrative: rules over the numbers above, never a model ───────────────
def narrative(kpis: list[dict]) -> list[str]:
    by = {k["id"]: k for k in kpis if k.get("available")}
    out = []
    if "delinquent_exposure" in by:
        k = by["delinquent_exposure"]
        out.append(f"Delinquent exposure stands at {k['value']} ({k['sub']}), {k['trend']}.")
    if "placed_share" in by and "unworked_exposure" in by:
        out.append(f"{by['placed_share']['value']} of it is placed with agencies; {by['unworked_exposure']['value']} "
                   f"of placed exposure saw no visit or call in the last 7 days.")
    if "gnpa_pct" in by:
        out.append(f"GNPA is {by['gnpa_pct']['value']} ({by['gnpa_pct']['sub']}).")
    if "compliance_integrity" in by:
        out.append(f"Field compliance scores {by['compliance_integrity']['value']} this month "
                   f"({by['compliance_integrity']['sub']}).")
    missing = [k["label"] for k in kpis if not k.get("available")]
    if missing:
        out.append(f"Not yet available: {', '.join(missing)}.")
    return out
