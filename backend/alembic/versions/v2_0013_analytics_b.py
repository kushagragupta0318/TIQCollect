"""The analytics layer, part B (DATA-MODEL-V2 §6, task B13b).

Creates:
  - strategy.cost_rates, the one cost table (§4.8), bank-only under RLS;
  - analytics.portfolio_state(dpd_bucket, loan_status, npa_since, as_of) and
    dim_portfolio_state: the plan §7.1 8-state space, rendered from
    models/loan.portfolio_state and pinned by tests against it;
  - mv_portfolio_daily (C03), mv_bucket_transitions_monthly (C04) and
    mv_agency_scorecard_monthly (D06), each with the plain-column unique index
    REFRESH ... CONCURRENTLY needs (sentinel for "unknown"/"none" keys);
  - the five *_scoped views the API reads, one per materialized view;
  - v_visit_to_pay (C03 Visit-to-Pay, coordinator 2026-09-28): per visit,
    the VERIFIED payments ATTRIBUTED to it. Each payment goes to the latest
    visit on its case at or before it, if within 7 days: bank-local CALENDAR
    days, since no holiday calendar exists yet. A payment counts once.
    security_invoker AND tenant-filtered, because RLS is not yet enforced.
  - Every view the API reads is security_barrier and shares one TENANT
    predicate: BANK sees its bank, AGENCY its agency; a field agent (scope
    AGENT), PLATFORM, or a missing scope or tenant sees nothing.

The *_scoped views are NOT security_invoker, deliberately (§6 said they were).
A security_invoker view needs the caller to hold SELECT on the materialized
view beneath it, and tiq_app holds none by design. So each wrapper runs as its
owner and carries the tenant predicate in its own WHERE: bank_id =
tenancy.current_bank_id() AND (BANK scope OR agency_id =
tenancy.current_agency_id()). With no tenant set it returns nothing.

Honesty rules the views follow:
  - Nothing is carried forward. A loan without a reading on a day is not in
    that day; a transition pair needs a reading at both month-ends within 7
    days, else it is counted in excluded_stale_pairs / excluded_missing_pairs.
  - Only complete months form transitions (month-end <= the bank's latest reading).
  - collectible_due is NULL for a (bank, month) with no instalment due in it
    (the demo fixture windows instalments), and NULL when any active
    placement lacks its opening reading (counted in collectible_due_unread):
    unknown, never a silent understatement.
  - "Today" is the bank-local business_date(now()), never the server's
    current_date.
  - field_cost is NULL when any visit in the cell has no FIELD_VISIT rate.
  - agent_days_with_visits is not attendance: no attendance table exists.
  - mv_portfolio_daily splits rows by is_backfill (a transformed snapshot,
    not an observation), so a day-on-day delta can be taken like for like.
    contacted_7d_* is a subset of placed_*: a met visit or answered call in
    (as_of - 7, as_of] on a placed loan, so unworked = placed - contacted.
  - Agent-level measures (agents_*, agent_*) sit on the region sentinel row:
    an agent has no region of their own, so summing over regions stays exact.
  - portfolio_state reads npa_since from lending.loans (current), because the
    history row does not carry it: the NPA sub/doubtful split is as-of today's
    npa_since, not point-in-time.

Revision ID: v2_0013
Revises: v2_0012
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from alembic import op

revision = "v2_0013"
down_revision = "v2_0012"
branch_labels = None
depends_on = None

S = "'00000000-0000-0000-0000-000000000000'::uuid"
APP_ROLE, JOBS_ROLE = "tiq_app", "tiq_jobs"

# Frozen from models/loan.py (PORTFOLIO_STATES, portfolio_state); pinned by tests.
STATES = (
    ("CURRENT", "Current", 0, False, False),
    ("SMA_0", "SMA-0 (1-30 DPD)", 1, False, False),
    ("SMA_1", "SMA-1 (31-60 DPD)", 2, False, False),
    ("SMA_2", "SMA-2 (61-90 DPD)", 3, False, False),
    ("NPA_SUB", "NPA sub-standard", 4, True, False),
    ("NPA_DOUBTFUL", "NPA doubtful (12+ months)", 5, True, False),
    ("WRITTEN_OFF", "Written off", 6, False, True),
    ("RESOLVED", "Resolved (closed or settled)", 7, False, True),
)
PTP_KEPT = ("HONORED", "PARTIALLY_HONORED")
MATERIALIZED = ("mv_portfolio_daily", "mv_bucket_transitions_monthly", "mv_agency_scorecard_monthly")
VISIT_TO_PAY_DAYS = 7

# (view, materialized view, columns); agency_id / region_id are un-sentineled to NULL.
SCOPED = {
    "portfolio_daily_scoped": ("mv_portfolio_daily", (
        "as_of_date", "is_month_end", "bank_id", "region_id", "agency_id", "loan_type", "dpd_bucket", "is_backfill",
        "accounts",
        "total_outstanding", "overdue_amount", "outstanding_principal", "delinquent_accounts", "delinquent_exposure",
        "npa_accounts", "npa_exposure", "placed_accounts", "placed_exposure", "contacted_7d_accounts",
        "contacted_7d_exposure")),
    "bucket_transitions_monthly_scoped": ("mv_bucket_transitions_monthly", (
        "month_end", "bank_id", "loan_type", "region_id", "agency_id", "from_state", "to_state", "accounts",
        "exposure_from", "exposure_to", "max_staleness_days", "excluded_stale_pairs", "excluded_missing_pairs")),
    "agency_scorecard_monthly_scoped": ("mv_agency_scorecard_monthly", (
        "month_start", "bank_id", "agency_id", "region_id", "placed_new", "placed_exposure_new",
        "active_placements_eom", "resolved_placements", "recalled_placements", "collectible_due",
        "collectible_due_unread", "verified_collections", "bank_direct_collections", "expected_recovery_inr",
        "ptps_matured", "ptps_honoured", "visits", "met_visits", "first_visits_within_sla",
        "placements_due_first_visit", "agent_days_with_visits", "agents_active", "agents_contracted",
        "agents_exited", "agent_leave_days", "commission_accrued", "field_cost", "breaches_out_of_hours",
        "breaches_geofence", "fraud_confirmed", "consent_missing")),
    "collections_daily_scoped": ("mv_collections_daily", (
        "collection_date", "bank_id", "agency_id", "verified_amount", "verified_count", "cash_amount",
        "digital_amount", "cheque_dd_amount", "bank_direct_amount", "pending_amount", "rejected_amount",
        "reversed_amount", "ptp_due_amount", "ptp_honoured_amount", "settlement_amount")),
    "field_activity_daily_scoped": ("mv_field_activity_daily", (
        "activity_date", "bank_id", "agency_id", "agent_id", "attendance_status", "visits", "met_visits",
        "distinct_cases_visited", "ptps_set", "payments_verified_count", "payments_verified_amount",
        "payments_pending_amount", "calls", "calls_answered", "planned_stops", "visited_stops", "planned_km",
        "out_of_hours_attempts", "geo_unverified_visits", "consent_missing_visits", "first_activity_at",
        "last_activity_at")),
}

# RLS for the one new table (v2_0012's template (c), bank-only); read by tests/test_rls_policy_map.py.
RLS_BANK_ONLY = ("strategy.cost_rates",)
RLS_POLICIES = {"strategy.cost_rates": "(bank_id = tenancy.current_bank_id() AND tenancy.current_scope() = 'BANK')"}


def _lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _if_role(role: str, sql: str) -> str:
    body = sql.replace("'", "''")
    return (f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f"THEN EXECUTE '{body}'; END IF; END $$;")


def _grant_specs() -> list[tuple[str, str, str]]:
    out = [(r, "USAGE", "SCHEMA strategy") for r in (APP_ROLE, JOBS_ROLE)]
    out.append((APP_ROLE, "SELECT, INSERT, UPDATE, DELETE", "strategy.cost_rates"))
    out.append((JOBS_ROLE, "SELECT", "strategy.cost_rates"))
    for r in (APP_ROLE, JOBS_ROLE):
        out.append((r, "SELECT", "analytics.dim_portfolio_state"))
        out.append((r, "SELECT", "analytics.v_visit_to_pay"))
        out += [(r, "SELECT", f"analytics.{v}") for v in SCOPED]
    out += [(JOBS_ROLE, "SELECT", f"analytics.{mv}") for mv in MATERIALIZED]
    return out


PORTFOLIO_STATE_SQL = """
CREATE FUNCTION analytics.portfolio_state(dpd_bucket text, loan_status text, npa_since date, as_of date)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE
    WHEN loan_status = 'WRITTEN_OFF' THEN 'WRITTEN_OFF'
    WHEN loan_status IN ('CLOSED', 'SETTLED') THEN 'RESOLVED'
    WHEN loan_status = 'NPA' OR dpd_bucket = 'NPA' THEN
      CASE WHEN npa_since IS NOT NULL AND (npa_since + interval '12 months')::date <= as_of
           THEN 'NPA_DOUBTFUL' ELSE 'NPA_SUB' END
    WHEN dpd_bucket = 'CURRENT'  THEN 'CURRENT'
    WHEN dpd_bucket = 'BUCKET_1' THEN 'SMA_0'
    WHEN dpd_bucket = 'BUCKET_2' THEN 'SMA_1'
    WHEN dpd_bucket = 'BUCKET_3' THEN 'SMA_2'
    ELSE 'UNKNOWN'
  END
$$
"""

PORTFOLIO_DAILY = f"""
CREATE MATERIALIZED VIEW analytics.mv_portfolio_daily AS
WITH tz AS (SELECT id AS bank_id, timezone FROM tenancy.banks),
contact AS (
    SELECT DISTINCT c.loan_id, analytics.business_date(v.check_in_time, tz.timezone) AS d
    FROM collections.visits v JOIN collections.cases c ON c.id = v.case_id JOIN tz ON tz.bank_id = v.bank_id
    WHERE v.customer_met
    UNION
    SELECT c.loan_id, analytics.business_date(x.called_at, tz.timezone)
    FROM collections.call_logs x JOIN collections.cases c ON c.id = x.case_id JOIN tz ON tz.bank_id = x.bank_id
    WHERE x.outcome = 'ANSWERED'
),
contacted AS (
    SELECT DISTINCT h.loan_id, h.as_of_date
    FROM lending.loan_dpd_history h JOIN contact k ON k.loan_id = h.loan_id
    WHERE k.d > h.as_of_date - 7 AND k.d <= h.as_of_date
)
SELECT h.as_of_date, bool_or(h.is_month_end) AS is_month_end, h.bank_id,
       coalesce(h.region_id, {S}) AS region_id, coalesce(h.agency_id, {S}) AS agency_id,
       h.loan_type::text AS loan_type, h.dpd_bucket::text AS dpd_bucket, h.is_backfill,
       count(*) AS accounts,
       coalesce(sum(h.total_outstanding), 0) AS total_outstanding,
       coalesce(sum(h.overdue_amount), 0) AS overdue_amount,
       coalesce(sum(h.outstanding_principal), 0) AS outstanding_principal,
       count(*) FILTER (WHERE h.dpd > 0) AS delinquent_accounts,
       coalesce(sum(h.total_outstanding) FILTER (WHERE h.dpd > 0), 0) AS delinquent_exposure,
       count(*) FILTER (WHERE h.npa_flag OR h.dpd_bucket::text = 'NPA') AS npa_accounts,
       coalesce(sum(h.total_outstanding) FILTER (WHERE h.npa_flag OR h.dpd_bucket::text = 'NPA'), 0) AS npa_exposure,
       count(*) FILTER (WHERE h.agency_id IS NOT NULL) AS placed_accounts,
       coalesce(sum(h.total_outstanding) FILTER (WHERE h.agency_id IS NOT NULL), 0) AS placed_exposure,
       count(*) FILTER (WHERE k.loan_id IS NOT NULL AND h.agency_id IS NOT NULL) AS contacted_7d_accounts,
       coalesce(sum(h.total_outstanding) FILTER (WHERE k.loan_id IS NOT NULL AND h.agency_id IS NOT NULL), 0)
           AS contacted_7d_exposure
FROM lending.loan_dpd_history h
LEFT JOIN contacted k ON k.loan_id = h.loan_id AND k.as_of_date = h.as_of_date
GROUP BY h.as_of_date, h.bank_id, 4, 5, 6, 7, h.is_backfill
"""

TRANSITIONS = f"""
CREATE MATERIALIZED VIEW analytics.mv_bucket_transitions_monthly AS
WITH latest AS (SELECT bank_id, max(as_of_date) AS latest FROM lending.loan_dpd_history GROUP BY bank_id),
r AS (
    SELECT DISTINCT ON (h.loan_id, date_trunc('month', h.as_of_date))
           h.loan_id, h.bank_id, h.loan_type::text AS loan_type,
           coalesce(h.region_id, {S}) AS region_id, coalesce(h.agency_id, {S}) AS agency_id,
           h.total_outstanding, h.as_of_date,
           (date_trunc('month', h.as_of_date) + interval '1 month - 1 day')::date AS month_end,
           analytics.portfolio_state(h.dpd_bucket::text, h.loan_status::text, l.npa_since, h.as_of_date) AS state
    FROM lending.loan_dpd_history h JOIN lending.loans l ON l.id = h.loan_id
    ORDER BY h.loan_id, date_trunc('month', h.as_of_date), h.as_of_date DESC
),
pairs AS (
    SELECT f.bank_id, f.loan_type, f.region_id, f.agency_id, f.state AS from_state,
           coalesce(t.state, 'NO_READING') AS to_state,
           (f.month_end + interval '1 day' + interval '1 month - 1 day')::date AS month_end,
           f.total_outstanding AS exposure_from, t.total_outstanding AS exposure_to,
           CASE WHEN t.loan_id IS NULL THEN NULL
                ELSE greatest(f.month_end - f.as_of_date, t.month_end - t.as_of_date) END AS staleness
    FROM r f
    JOIN latest s ON s.bank_id = f.bank_id
    LEFT JOIN r t ON t.loan_id = f.loan_id
                 AND t.month_end = (f.month_end + interval '1 day' + interval '1 month - 1 day')::date
    WHERE (f.month_end + interval '1 day' + interval '1 month - 1 day')::date <= s.latest
)
SELECT month_end, bank_id, loan_type, region_id, agency_id, from_state, to_state,
       count(*) FILTER (WHERE staleness <= 7) AS accounts,
       coalesce(sum(exposure_from) FILTER (WHERE staleness <= 7), 0) AS exposure_from,
       coalesce(sum(exposure_to) FILTER (WHERE staleness <= 7), 0) AS exposure_to,
       max(staleness) FILTER (WHERE staleness <= 7) AS max_staleness_days,
       count(*) FILTER (WHERE staleness > 7) AS excluded_stale_pairs,
       count(*) FILTER (WHERE staleness IS NULL) AS excluded_missing_pairs
FROM pairs
GROUP BY month_end, bank_id, loan_type, region_id, agency_id, from_state, to_state
"""


def _scorecard() -> str:
    kept = ", ".join(_lit(s) for s in PTP_KEPT)
    k = "month_start, bank_id, agency_id, region_id"
    return f"""
CREATE MATERIALIZED VIEW analytics.mv_agency_scorecard_monthly AS
WITH tz AS (SELECT id AS bank_id, timezone FROM tenancy.banks),
loan_region AS (
    SELECT l.id AS loan_id, l.loan_type::text AS loan_type, coalesce(b.region_id, {S}) AS region_id
    FROM lending.loans l LEFT JOIN tenancy.branches b ON b.bank_id = l.bank_id AND b.branch_code = l.branch_code
),
case_region AS (
    SELECT c.id AS case_id, c.placement_id, c.loan_id, lr.region_id
    FROM collections.cases c JOIN loan_region lr ON lr.loan_id = c.loan_id
),
pl AS (SELECT p.*, lr.region_id, lr.loan_type FROM collections.placements p JOIN loan_region lr ON lr.loan_id = p.loan_id),
months AS (
    SELECT p.id AS placement_id, gs::date AS month_start, (gs + interval '1 month - 1 day')::date AS month_end
    FROM pl p JOIN tz ON tz.bank_id = p.bank_id
    CROSS JOIN LATERAL generate_series(
        date_trunc('month', p.placed_on),
        date_trunc('month', coalesce(p.ended_on, analytics.business_date(now(), tz.timezone))), interval '1 month') gs
),
m_placed AS (
    SELECT date_trunc('month', placed_on)::date AS month_start, bank_id, agency_id, region_id,
           count(*) AS placed_new, coalesce(sum(exposure_at_placement), 0) AS placed_exposure_new,
           coalesce(sum(expected_recovery_inr), 0) AS expected_recovery_inr
    FROM pl GROUP BY 1, 2, 3, 4
),
m_ended AS (
    SELECT date_trunc('month', ended_on)::date AS month_start, bank_id, agency_id, region_id,
           count(*) FILTER (WHERE status = 'RESOLVED') AS resolved_placements,
           count(*) FILTER (WHERE status = 'RECALLED') AS recalled_placements
    FROM pl WHERE ended_on IS NOT NULL GROUP BY 1, 2, 3, 4
),
active AS (
    SELECT m.month_start, m.month_end, p.id, p.bank_id, p.agency_id, p.region_id, p.loan_id,
           (p.ended_on IS NULL OR p.ended_on > m.month_end) AS open_at_eom
    FROM months m JOIN pl p ON p.id = m.placement_id
),
opening AS (
    SELECT DISTINCT ON (a.id, a.month_start) a.id, a.month_start, h.overdue_amount
    FROM active a JOIN lending.loan_dpd_history h
      ON h.loan_id = a.loan_id AND h.as_of_date < a.month_start AND h.as_of_date >= a.month_start - 8
    ORDER BY a.id, a.month_start, h.as_of_date DESC
),
due AS (
    SELECT a.id, a.month_start, sum(i.amount_due) AS amount_due
    FROM active a JOIN lending.loan_instalments i
      ON i.loan_id = a.loan_id AND i.is_current_schedule AND i.due_date BETWEEN a.month_start AND a.month_end
    GROUP BY a.id, a.month_start
),
covered AS (
    SELECT DISTINCT bank_id, date_trunc('month', due_date)::date AS month_start FROM lending.loan_instalments
),
m_active AS (
    SELECT a.month_start, a.bank_id, a.agency_id, a.region_id,
           count(*) FILTER (WHERE a.open_at_eom) AS active_placements_eom,
           coalesce(sum(o.overdue_amount), 0) + coalesce(sum(d.amount_due), 0) AS collectible_due,
           count(*) FILTER (WHERE o.id IS NULL) AS collectible_due_unread,
           bool_or(cv.bank_id IS NOT NULL) AS due_covered
    FROM active a
    LEFT JOIN opening o ON o.id = a.id AND o.month_start = a.month_start
    LEFT JOIN due d ON d.id = a.id AND d.month_start = a.month_start
    LEFT JOIN covered cv ON cv.bank_id = a.bank_id AND cv.month_start = a.month_start
    GROUP BY 1, 2, 3, 4
),
m_pay AS (
    SELECT date_trunc('month', analytics.business_date(p.payment_date, tz.timezone))::date AS month_start,
           p.bank_id, coalesce(p.agency_id, {S}) AS agency_id, coalesce(lr.region_id, {S}) AS region_id,
           coalesce(sum(p.amount) FILTER (WHERE p.mode::text <> 'BANK_DIRECT'), 0) AS verified_collections,
           coalesce(sum(p.amount) FILTER (WHERE p.mode::text = 'BANK_DIRECT'), 0) AS bank_direct_collections,
           round(coalesce(sum(p.amount * t.commission_pct / 100) FILTER (WHERE p.mode::text <> 'BANK_DIRECT'), 0), 2)
               AS commission_accrued
    FROM collections.payments p
    JOIN tz ON tz.bank_id = p.bank_id
    LEFT JOIN loan_region lr ON lr.loan_id = p.loan_id
    LEFT JOIN collections.cases c ON c.id = p.case_id
    LEFT JOIN collections.placements pp ON pp.id = c.placement_id
    LEFT JOIN tenancy.agency_contract_terms t
      ON t.contract_id = pp.contract_id AND t.loan_type::text = lr.loan_type
     AND t.dpd_bucket::text = pp.dpd_bucket_at_placement::text
    WHERE p.status::text = 'VERIFIED'
    GROUP BY 1, 2, 3, 4
),
m_ptp AS (
    SELECT date_trunc('month', t.committed_date)::date AS month_start, t.bank_id, t.agency_id,
           coalesce(cr.region_id, {S}) AS region_id,
           count(*) AS ptps_matured, count(*) FILTER (WHERE t.status::text IN ({kept})) AS ptps_honoured
    FROM collections.ptps t JOIN tz ON tz.bank_id = t.bank_id LEFT JOIN case_region cr ON cr.case_id = t.case_id
    WHERE t.committed_date <= analytics.business_date(now(), tz.timezone)
    GROUP BY 1, 2, 3, 4
),
vis AS (
    SELECT v.*, analytics.business_date(v.check_in_time, tz.timezone) AS d, coalesce(cr.region_id, {S}) AS region_id,
           cr.placement_id, r.rate_inr
    FROM collections.visits v
    JOIN tz ON tz.bank_id = v.bank_id
    LEFT JOIN case_region cr ON cr.case_id = v.case_id
    LEFT JOIN LATERAL (
        SELECT c.rate_inr FROM strategy.cost_rates c
        WHERE c.bank_id = v.bank_id AND c.channel = 'FIELD_VISIT' AND c.unit = 'PER_ATTEMPT'
          AND c.valid_from <= analytics.business_date(v.check_in_time, tz.timezone)
          AND (c.valid_to IS NULL OR c.valid_to >= analytics.business_date(v.check_in_time, tz.timezone))
        ORDER BY c.valid_from DESC LIMIT 1) r ON true
),
m_visit AS (
    SELECT date_trunc('month', d)::date AS month_start, bank_id, agency_id, region_id,
           count(*) AS visits, count(*) FILTER (WHERE customer_met) AS met_visits,
           count(*) FILTER (WHERE within_contact_hours IS FALSE) AS breaches_out_of_hours,
           count(*) FILTER (WHERE geo_verified IS FALSE) AS breaches_geofence,
           count(*) FILTER (WHERE consent_given IS NOT TRUE) AS consent_missing,
           CASE WHEN count(*) FILTER (WHERE rate_inr IS NULL) > 0 THEN NULL ELSE round(sum(rate_inr), 2) END AS field_cost
    FROM vis GROUP BY 1, 2, 3, 4
),
first_visit AS (SELECT placement_id, min(d) AS d FROM vis WHERE placement_id IS NOT NULL GROUP BY placement_id),
m_sla AS (
    SELECT date_trunc('month', p.sla_first_visit_due)::date AS month_start, p.bank_id, p.agency_id, p.region_id,
           count(*) AS placements_due_first_visit,
           count(*) FILTER (WHERE fv.d <= p.sla_first_visit_due) AS first_visits_within_sla
    FROM pl p LEFT JOIN first_visit fv ON fv.placement_id = p.id
    WHERE p.sla_first_visit_due IS NOT NULL
    GROUP BY 1, 2, 3, 4
),
m_fraud AS (
    SELECT date_trunc('month', analytics.business_date(f.reviewed_at, tz.timezone))::date AS month_start,
           f.bank_id, f.agency_id, coalesce(vv.region_id, {S}) AS region_id, count(*) AS fraud_confirmed
    FROM collections.fraud_reviews f
    JOIN tz ON tz.bank_id = f.bank_id
    LEFT JOIN vis vv ON vv.id = f.visit_id
    WHERE f.verdict::text = 'CONFIRMED' AND f.reviewed_at IS NOT NULL
    GROUP BY 1, 2, 3, 4
),
m_agents AS (
    SELECT date_trunc('month', d)::date AS month_start, bank_id, agency_id, {S} AS region_id,
           count(DISTINCT agent_id) AS agents_active, count(DISTINCT (agent_id, d)) AS agent_days_with_visits
    FROM vis GROUP BY 1, 2, 3
),
m_exited AS (
    SELECT date_trunc('month', exited_on)::date AS month_start, bank_id, agency_id, {S} AS region_id,
           count(*) AS agents_exited
    FROM workforce.agents WHERE exited_on IS NOT NULL GROUP BY 1, 2, 3
),
m_leave AS (
    SELECT gs::date AS month_start, l.bank_id, l.agency_id, {S} AS region_id,
           sum(least(l.to_date, (gs + interval '1 month - 1 day')::date) - greatest(l.from_date, gs::date) + 1)
               AS agent_leave_days
    FROM workforce.leave_requests l,
         generate_series(date_trunc('month', l.from_date), date_trunc('month', l.to_date), interval '1 month') gs
    WHERE l.status::text = 'APPROVED'
    GROUP BY 1, 2, 3
),
m_contracted AS (
    SELECT gs::date AS month_start, c.bank_id, c.agency_id, {S} AS region_id,
           coalesce(sum(c.max_agents), 0) AS agents_contracted
    FROM tenancy.agency_contracts c JOIN tz ON tz.bank_id = c.bank_id
    CROSS JOIN LATERAL generate_series(
        date_trunc('month', c.start_date),
        date_trunc('month', least(c.end_date, analytics.business_date(now(), tz.timezone))), interval '1 month') gs
    WHERE c.status <> 'DRAFT'
    GROUP BY 1, 2, 3
),
keys AS (
    SELECT {k} FROM m_placed UNION SELECT {k} FROM m_ended UNION SELECT {k} FROM m_active
    UNION SELECT {k} FROM m_pay UNION SELECT {k} FROM m_ptp UNION SELECT {k} FROM m_visit
    UNION SELECT {k} FROM m_sla UNION SELECT {k} FROM m_fraud UNION SELECT {k} FROM m_agents
    UNION SELECT {k} FROM m_exited UNION SELECT {k} FROM m_leave UNION SELECT {k} FROM m_contracted
)
SELECT k.month_start, k.bank_id, k.agency_id, k.region_id,
       coalesce(pn.placed_new, 0) AS placed_new, coalesce(pn.placed_exposure_new, 0) AS placed_exposure_new,
       coalesce(ma.active_placements_eom, 0) AS active_placements_eom,
       coalesce(me.resolved_placements, 0) AS resolved_placements,
       coalesce(me.recalled_placements, 0) AS recalled_placements,
       CASE WHEN ma.due_covered AND ma.collectible_due_unread = 0 THEN ma.collectible_due END AS collectible_due,
       coalesce(ma.collectible_due_unread, 0) AS collectible_due_unread,
       coalesce(mp.verified_collections, 0) AS verified_collections,
       coalesce(mp.bank_direct_collections, 0) AS bank_direct_collections,
       coalesce(pn.expected_recovery_inr, 0) AS expected_recovery_inr,
       coalesce(mt.ptps_matured, 0) AS ptps_matured, coalesce(mt.ptps_honoured, 0) AS ptps_honoured,
       coalesce(mv.visits, 0) AS visits, coalesce(mv.met_visits, 0) AS met_visits,
       coalesce(ms.first_visits_within_sla, 0) AS first_visits_within_sla,
       coalesce(ms.placements_due_first_visit, 0) AS placements_due_first_visit,
       coalesce(mg.agent_days_with_visits, 0) AS agent_days_with_visits,
       coalesce(mg.agents_active, 0) AS agents_active,
       coalesce(mc.agents_contracted, 0) AS agents_contracted,
       coalesce(mx.agents_exited, 0) AS agents_exited,
       coalesce(ml.agent_leave_days, 0) AS agent_leave_days,
       coalesce(mp.commission_accrued, 0) AS commission_accrued,
       CASE WHEN mv.visits IS NULL THEN 0 ELSE mv.field_cost END AS field_cost,
       coalesce(mv.breaches_out_of_hours, 0) AS breaches_out_of_hours,
       coalesce(mv.breaches_geofence, 0) AS breaches_geofence,
       coalesce(mf.fraud_confirmed, 0) AS fraud_confirmed,
       coalesce(mv.consent_missing, 0) AS consent_missing
FROM keys k
LEFT JOIN m_placed pn USING ({k}) LEFT JOIN m_ended me USING ({k}) LEFT JOIN m_active ma USING ({k})
LEFT JOIN m_pay mp USING ({k}) LEFT JOIN m_ptp mt USING ({k}) LEFT JOIN m_visit mv USING ({k})
LEFT JOIN m_sla ms USING ({k}) LEFT JOIN m_fraud mf USING ({k}) LEFT JOIN m_agents mg USING ({k})
LEFT JOIN m_exited mx USING ({k}) LEFT JOIN m_leave ml USING ({k}) LEFT JOIN m_contracted mc USING ({k})
"""


# The one tenant predicate of every analytics view the API reads. BANK sees its bank; AGENCY its agency;
# AGENT (a field agent), PLATFORM, a missing scope or a missing tenant see nothing.
TENANT = ("bank_id = tenancy.current_bank_id() AND (tenancy.current_scope() = 'BANK' OR "
          "(tenancy.current_scope() = 'AGENCY' AND agency_id = tenancy.current_agency_id()))")
V_TENANT = ("v.bank_id = tenancy.current_bank_id() AND (tenancy.current_scope() = 'BANK' OR "
            "(tenancy.current_scope() = 'AGENCY' AND v.agency_id = tenancy.current_agency_id()))")

VISIT_TO_PAY = f"""
CREATE VIEW analytics.v_visit_to_pay WITH (security_invoker = true, security_barrier = true) AS
WITH vis AS (
    SELECT v.id, v.bank_id, v.agency_id, v.agent_id, v.case_id, v.customer_met, v.check_in_time,
           analytics.business_date(v.check_in_time, b.timezone) AS visit_date
    FROM collections.visits v JOIN tenancy.banks b ON b.id = v.bank_id
    WHERE {V_TENANT}
),
attributed AS (
    SELECT p.id, p.amount, analytics.business_date(p.payment_date, b.timezone) AS paid_date, lv.id AS visit_id
    FROM collections.payments p
    JOIN tenancy.banks b ON b.id = p.bank_id
    CROSS JOIN LATERAL (
        SELECT x.id, x.visit_date FROM vis x
        WHERE x.case_id = p.case_id AND x.check_in_time <= p.payment_date
        ORDER BY x.check_in_time DESC LIMIT 1) lv
    WHERE p.status::text = 'VERIFIED'
      AND analytics.business_date(p.payment_date, b.timezone) <= lv.visit_date + {VISIT_TO_PAY_DAYS}
)
SELECT v.id AS visit_id, v.bank_id, v.agency_id, v.agent_id, v.case_id, v.visit_date, v.customer_met,
       coalesce(sum(a.amount), 0) AS paid_amount_7d, count(a.id) > 0 AS paid_within_7d,
       min(a.paid_date) AS first_paid_date
FROM vis v LEFT JOIN attributed a ON a.visit_id = v.id
GROUP BY v.id, v.bank_id, v.agency_id, v.agent_id, v.case_id, v.visit_date, v.customer_met
"""


def _scoped_sql(view: str, mv: str, cols: tuple[str, ...]) -> str:
    sel = ", ".join(f"NULLIF({c}, {S}) AS {c}" if c in ("agency_id", "region_id") else c for c in cols)
    return (f"CREATE VIEW analytics.{view} WITH (security_barrier = true) AS SELECT {sel} "
            f"FROM analytics.{mv} WHERE {TENANT}")


UNIQUE = {
    "mv_portfolio_daily": "(as_of_date, bank_id, region_id, agency_id, loan_type, dpd_bucket, is_backfill)",
    "mv_bucket_transitions_monthly": "(month_end, bank_id, loan_type, region_id, agency_id, from_state, to_state)",
    "mv_agency_scorecard_monthly": "(month_start, bank_id, agency_id, region_id)",
}


def upgrade() -> None:
    op.create_table(
        "cost_rates",
        sa.Column("bank_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("unit", sa.String(length=12), nullable=False),
        sa.Column("rate_inr", sa.Numeric(precision=12, scale=4, asdecimal=False), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("channel IN ('FIELD_VISIT', 'CALL', 'SMS', 'WHATSAPP', 'EMAIL', 'IVR', 'LEGAL_NOTICE')",
                           name=op.f("ck_cost_rates_channel")),
        sa.CheckConstraint("unit IN ('PER_ATTEMPT', 'PER_CONTACT', 'PER_KM', 'PER_MESSAGE', 'PER_CASE')",
                           name=op.f("ck_cost_rates_unit")),
        sa.CheckConstraint("rate_inr >= 0", name=op.f("ck_cost_rates_rate_non_negative")),
        sa.CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name=op.f("ck_cost_rates_valid_range")),
        sa.ForeignKeyConstraint(["bank_id"], ["tenancy.banks.id"], name=op.f("fk_cost_rates_bank_id_banks")),
        sa.ForeignKeyConstraint(["created_by"], ["tenancy.users.id"], name=op.f("fk_cost_rates_created_by_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_rates")),
        sa.UniqueConstraint("bank_id", "channel", "unit", "valid_from",
                            name=op.f("uq_cost_rates_bank_id_channel_unit_valid_from")),
        schema="strategy",
    )
    # PG-only: no two rates for one (bank, channel, unit) may overlap in time.
    op.execute("ALTER TABLE strategy.cost_rates ADD CONSTRAINT ex_cost_rates_no_overlap EXCLUDE USING gist "
               "(bank_id WITH =, channel WITH =, unit WITH =, daterange(valid_from, valid_to, '[]') WITH &&)")
    for table, expr in RLS_POLICIES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY p_tenant ON {table} USING {expr} WITH CHECK {expr}")

    op.execute(PORTFOLIO_STATE_SQL)
    rows = ", ".join("(" + ", ".join(_lit(x) for x in r) + ")" for r in STATES)
    op.execute("CREATE VIEW analytics.dim_portfolio_state WITH (security_invoker = true) AS "
               f"SELECT * FROM (VALUES {rows}) AS s(state, label, sort_order, is_npa, is_terminal)")
    op.execute(PORTFOLIO_DAILY)
    op.execute(TRANSITIONS)
    op.execute(_scorecard())
    for mv, cols in UNIQUE.items():
        op.execute(f"CREATE UNIQUE INDEX uq_{mv} ON analytics.{mv} {cols}")
    for view, (mv, cols) in SCOPED.items():
        op.execute(_scoped_sql(view, mv, cols))
    op.execute(VISIT_TO_PAY)
    for r, priv, obj in _grant_specs():
        op.execute(_if_role(r, f"GRANT {priv} ON {obj} TO {r}"))


def downgrade() -> None:
    for r, priv, obj in reversed(_grant_specs()):
        op.execute(_if_role(r, f"REVOKE {priv} ON {obj} FROM {r}"))
    op.execute("DROP VIEW IF EXISTS analytics.v_visit_to_pay")
    for view in reversed(list(SCOPED)):
        op.execute(f"DROP VIEW IF EXISTS analytics.{view}")
    for mv in reversed(MATERIALIZED):
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS analytics.{mv}")
    op.execute("DROP VIEW IF EXISTS analytics.dim_portfolio_state")
    op.execute("DROP FUNCTION IF EXISTS analytics.portfolio_state(text, text, date, date)")
    op.drop_table("cost_rates", schema="strategy")
