"""The analytics layer, part A (design §6, task B13).

Creates in schema `analytics`:
  - business_date(ts, tz): THE business day of an instant for a bank (§2.3);
  - dimensions (plain views): dim_date, dim_region, dim_agency, dim_agent,
    dim_product, dim_bucket;
  - materialized views: mv_collections_daily, mv_field_activity_daily, each
    with the plain-column unique index REFRESH ... CONCURRENTLY needs;
  - live views: v_case_360, v_today_field_activity;
  - the mv_refresh_log table (models/analytics.py).

NOT here (part B, with its prerequisites): mv_portfolio_daily and
mv_bucket_transitions_monthly (need lending.loan_dpd_history filled),
dim_portfolio_state / portfolio_state() (need its one Python definition),
mv_agency_scorecard_monthly (needs cost_rates and loan_instalments), and the
*_scoped views (need §8's tenancy.current_bank_id(), task A13).

Design rules followed: views carry ADDITIVE measures only (ratios belong to the
KPI catalog); every MV dimension key is NOT NULL (sentinel
00000000-0000-0000-0000-000000000000 where "unknown"), so the unique index has
no NULLs and a CONCURRENTLY refresh does not churn; every plain view is
security_invoker, so base-table RLS applies to the caller (A13).

FROZEN FACTS, rendered from Python when this was written and pinned by
tests/test_analytics_views.py against the live definitions:
  dim_product  ← ml/eligibility._SECURED / _UNSECURED (BUSINESS is in neither: MIXED)
  dim_bucket   ← models/loan.dpd_bucket_for probed over 0..400
  PTP kept     ← services/ml_scoring_service.PTP_KEPT (HONORED, PARTIALLY_HONORED)
A change to any of them is a new revision, not an edit here.

Not reproduced by B13a, recorded: attendance and beat_stops do not exist yet
(B23), so field activity takes planned / visited stops from planning.beats and
the day's attendance from approved leave; `dropped_stops` is absent until then.

Revision ID: v2_0007
Revises: v2_0006
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from alembic import op

revision = "v2_0007"
down_revision = "v2_0006"
branch_labels = None
depends_on = None

SENTINEL = "00000000-0000-0000-0000-000000000000"

# (loan_type, label, security_class) — frozen from ml/eligibility.
PRODUCTS = (
    ("HOME", "Home loan", "SECURED"),
    ("AUTO", "Auto loan", "SECURED"),
    ("GOLD", "Gold loan", "SECURED"),
    ("PERSONAL", "Personal loan", "UNSECURED"),
    ("CREDIT_CARD", "Credit card", "UNSECURED"),
    ("EDUCATION", "Education loan", "UNSECURED"),
    ("MICROFINANCE", "Microfinance", "UNSECURED"),
    ("BUSINESS", "Business loan", "MIXED"),
)
# (dpd_bucket, label, min_dpd, max_dpd, sort_order, is_npa) — frozen from
# dpd_bucket_for over 0..400; NPA is open-ended (max NULL).
BUCKETS = (
    ("CURRENT", "Current (0)", 0, 0, 0, False),
    ("BUCKET_1", "1-30 DPD", 1, 30, 1, False),
    ("BUCKET_2", "31-60 DPD", 31, 60, 2, False),
    ("BUCKET_3", "61-90 DPD", 61, 90, 3, False),
    ("NPA", "90+ DPD (NPA)", 91, None, 4, True),
)
PTP_KEPT = ("HONORED", "PARTIALLY_HONORED")
MATERIALIZED = ("mv_collections_daily", "mv_field_activity_daily")


def _lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _values(rows):
    return ", ".join("(" + ", ".join(_lit(x) for x in r) + ")" for r in rows)


def upgrade() -> None:
    op.create_table(
        "mv_refresh_log",
        sa.Column("view_name", sa.String(length=80), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint("status IN ('OK', 'FAILED', 'SKIPPED')", name=op.f("ck_mv_refresh_log_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mv_refresh_log")),
        schema="analytics",
    )
    op.create_index(op.f("ix_mv_refresh_log_view_name_started_at"), "mv_refresh_log",
                    ["view_name", "started_at"], unique=False, schema="analytics")

    op.execute("""
        CREATE FUNCTION analytics.business_date(ts timestamptz, tz text) RETURNS date
        LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT (ts AT TIME ZONE tz)::date $$
    """)

    # ── dimensions ──────────────────────────────────────────────────────────
    op.execute("""
        CREATE VIEW analytics.dim_date WITH (security_invoker = true) AS
        SELECT d::date                                            AS date,
               extract(year FROM d)::int                          AS year,
               extract(quarter FROM d)::int                       AS quarter,
               date_trunc('month', d)::date                       AS month_start,
               date_trunc('week', d)::date                        AS iso_week_start,
               extract(isodow FROM d)::int                        AS day_of_week,
               (d::date = (date_trunc('month', d) + interval '1 month - 1 day')::date) AS is_month_end,
               (CASE WHEN extract(month FROM d) >= 4 THEN extract(year FROM d) ELSE extract(year FROM d) - 1 END)::int AS fy,
               ((extract(month FROM d)::int + 8) % 12) / 3 + 1    AS fy_quarter,
               (d::date - make_date((CASE WHEN extract(month FROM d) >= 4 THEN extract(year FROM d)
                                          ELSE extract(year FROM d) - 1 END)::int, 4, 1)) + 1 AS fytd_day
        FROM generate_series(date '2020-01-01', current_date + 400, interval '1 day') AS d
    """)
    op.execute(f"""
        CREATE VIEW analytics.dim_region WITH (security_invoker = true) AS
        WITH RECURSIVE anc AS (
            SELECT r.id AS region_id, r.id AS anc_id, r.level AS anc_level, r.name AS anc_name, r.parent_id
            FROM tenancy.regions r
            UNION ALL
            SELECT a.region_id, p.id, p.level, p.name, p.parent_id
            FROM anc a JOIN tenancy.regions p ON p.id = a.parent_id
        )
        SELECT r.id AS region_id, r.bank_id, r.level, r.name,
               (array_agg(a.anc_id) FILTER (WHERE a.anc_level = 'CITY'))[1]     AS city_id,
               (array_agg(a.anc_name) FILTER (WHERE a.anc_level = 'CITY'))[1]   AS city_name,
               (array_agg(a.anc_id) FILTER (WHERE a.anc_level = 'STATE'))[1]    AS state_id,
               (array_agg(a.anc_name) FILTER (WHERE a.anc_level = 'STATE'))[1]  AS state_name,
               (array_agg(a.anc_id) FILTER (WHERE a.anc_level = 'REGION'))[1]   AS region_l_id,
               (array_agg(a.anc_name) FILTER (WHERE a.anc_level = 'REGION'))[1] AS region_l_name,
               (array_agg(a.anc_id) FILTER (WHERE a.anc_level = 'ZONE'))[1]     AS zone_id,
               (array_agg(a.anc_name) FILTER (WHERE a.anc_level = 'ZONE'))[1]   AS zone_name
        FROM tenancy.regions r JOIN anc a ON a.region_id = r.id
        GROUP BY r.id, r.bank_id, r.level, r.name
        UNION ALL
        SELECT '{SENTINEL}'::uuid, NULL, 'UNKNOWN', 'Unknown', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
    """)
    op.execute(f"""
        CREATE VIEW analytics.dim_agency WITH (security_invoker = true) AS
        SELECT a.id AS agency_id, a.bank_id, a.code, coalesce(a.trade_name, a.legal_name) AS name, a.status,
               c.id AS active_contract_id, c.end_date AS contract_end, c.max_agents, c.max_placed_cases
        FROM tenancy.agencies a
        LEFT JOIN LATERAL (
            SELECT id, end_date, max_agents, max_placed_cases FROM tenancy.agency_contracts k
            WHERE k.agency_id = a.id AND k.status = 'ACTIVE' ORDER BY k.start_date DESC LIMIT 1
        ) c ON true
        UNION ALL
        SELECT '{SENTINEL}'::uuid, NULL, 'UNPLACED', 'Unplaced', NULL, NULL, NULL, NULL, NULL
    """)
    op.execute("""
        CREATE VIEW analytics.dim_agent WITH (security_invoker = true) AS
        SELECT g.id AS agent_id, g.bank_id, g.agency_id, g.manager_user_id, g.employee_code, u.full_name,
               g.status, g.tier, g.territory_region_id, g.joined_on, g.exited_on
        FROM workforce.agents g JOIN tenancy.users u ON u.id = g.user_id
    """)
    op.execute(f"""
        CREATE VIEW analytics.dim_product WITH (security_invoker = true) AS
        SELECT * FROM (VALUES {_values(PRODUCTS)}) AS t(loan_type, label, security_class)
    """)
    op.execute(f"""
        CREATE VIEW analytics.dim_bucket WITH (security_invoker = true) AS
        SELECT * FROM (VALUES {_values(BUCKETS)}) AS t(dpd_bucket, label, min_dpd, max_dpd, sort_order, is_npa)
    """)

    # ── materialized views ─────────────────────────────────────────────────
    kept = ", ".join(_lit(s) for s in PTP_KEPT)
    op.execute(f"""
        CREATE MATERIALIZED VIEW analytics.mv_collections_daily AS
        WITH parts AS (
            SELECT analytics.business_date(p.payment_date, b.timezone) AS collection_date, p.bank_id, p.agency_id,
                   CASE WHEN p.status = 'VERIFIED' THEN p.amount ELSE 0 END                                   AS verified_amount,
                   CASE WHEN p.status = 'VERIFIED' THEN 1 ELSE 0 END                                          AS verified_count,
                   CASE WHEN p.status = 'VERIFIED' AND p.mode = 'CASH' THEN p.amount ELSE 0 END               AS cash_amount,
                   CASE WHEN p.status = 'VERIFIED' AND p.mode IN ('UPI', 'NEFT', 'RTGS') THEN p.amount ELSE 0 END AS digital_amount,
                   CASE WHEN p.status = 'VERIFIED' AND p.mode IN ('CHEQUE', 'DD') THEN p.amount ELSE 0 END    AS cheque_dd_amount,
                   CASE WHEN p.status = 'VERIFIED' AND p.mode = 'BANK_DIRECT' THEN p.amount ELSE 0 END        AS bank_direct_amount,
                   CASE WHEN p.status = 'PENDING_VERIFICATION' THEN p.amount ELSE 0 END                       AS pending_amount,
                   CASE WHEN p.status = 'REJECTED' THEN p.amount ELSE 0 END                                   AS rejected_amount,
                   CASE WHEN p.status = 'REVERSED' THEN p.amount ELSE 0 END                                   AS reversed_amount,
                   0::numeric AS ptp_due_amount, 0::numeric AS ptp_honoured_amount,
                   CASE WHEN p.status = 'VERIFIED' AND p.settlement_offer_id IS NOT NULL THEN p.amount ELSE 0 END AS settlement_amount
            FROM collections.payments p JOIN tenancy.banks b ON b.id = p.bank_id
            UNION ALL
            SELECT t.committed_date, t.bank_id, t.agency_id, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                   t.committed_amount,
                   CASE WHEN t.status IN ({kept}) THEN t.committed_amount ELSE 0 END,
                   0
            FROM collections.ptps t
        )
        SELECT collection_date, bank_id, agency_id,
               sum(verified_amount) AS verified_amount, sum(verified_count)::int AS verified_count,
               sum(cash_amount) AS cash_amount, sum(digital_amount) AS digital_amount,
               sum(cheque_dd_amount) AS cheque_dd_amount, sum(bank_direct_amount) AS bank_direct_amount,
               sum(pending_amount) AS pending_amount, sum(rejected_amount) AS rejected_amount,
               sum(reversed_amount) AS reversed_amount, sum(ptp_due_amount) AS ptp_due_amount,
               sum(ptp_honoured_amount) AS ptp_honoured_amount, sum(settlement_amount) AS settlement_amount
        FROM parts GROUP BY collection_date, bank_id, agency_id
    """)
    op.execute("CREATE UNIQUE INDEX uq_mv_collections_daily ON analytics.mv_collections_daily "
               "(collection_date, bank_id, agency_id)")

    op.execute("""
        CREATE MATERIALIZED VIEW analytics.mv_field_activity_daily AS
        WITH tz AS (SELECT id AS bank_id, timezone FROM tenancy.banks),
        v AS (
            SELECT analytics.business_date(x.check_in_time, tz.timezone) AS d, x.bank_id, x.agency_id, x.agent_id,
                   count(*) AS visits, count(*) FILTER (WHERE x.customer_met) AS met_visits,
                   count(DISTINCT x.case_id) AS distinct_cases_visited,
                   count(*) FILTER (WHERE x.geo_verified IS NOT TRUE) AS geo_unverified_visits,
                   count(*) FILTER (WHERE x.consent_given IS NOT TRUE) AS consent_missing_visits,
                   min(x.check_in_time) AS first_at, max(coalesce(x.check_out_time, x.check_in_time)) AS last_at
            FROM collections.visits x JOIN tz ON tz.bank_id = x.bank_id GROUP BY 1, 2, 3, 4
        ),
        t AS (
            SELECT analytics.business_date(x.created_at, tz.timezone) AS d, x.bank_id, x.agency_id, x.agent_id,
                   count(*) AS ptps_set
            FROM collections.ptps x JOIN tz ON tz.bank_id = x.bank_id GROUP BY 1, 2, 3, 4
        ),
        p AS (
            SELECT analytics.business_date(x.payment_date, tz.timezone) AS d, x.bank_id, x.agency_id, x.agent_id,
                   count(*) FILTER (WHERE x.status = 'VERIFIED') AS payments_verified_count,
                   coalesce(sum(x.amount) FILTER (WHERE x.status = 'VERIFIED'), 0) AS payments_verified_amount,
                   coalesce(sum(x.amount) FILTER (WHERE x.status = 'PENDING_VERIFICATION'), 0) AS payments_pending_amount
            FROM collections.payments x JOIN tz ON tz.bank_id = x.bank_id
            WHERE x.agent_id IS NOT NULL GROUP BY 1, 2, 3, 4
        ),
        c AS (
            SELECT analytics.business_date(x.called_at, tz.timezone) AS d, x.bank_id, x.agency_id, x.agent_id,
                   count(*) AS calls, count(*) FILTER (WHERE x.outcome = 'ANSWERED') AS calls_answered,
                   min(x.called_at) AS first_at, max(x.called_at) AS last_at
            FROM collections.call_logs x JOIN tz ON tz.bank_id = x.bank_id GROUP BY 1, 2, 3, 4
        ),
        bt AS (
            SELECT x.beat_date AS d, x.bank_id, x.agency_id, x.agent_id,
                   sum(x.total_cases) AS planned_stops, sum(coalesce(x.cases_completed, 0)) AS visited_stops,
                   sum(coalesce(x.estimated_distance_km, 0)) AS planned_km, sum(x.actual_distance_km) AS actual_km
            FROM planning.beats x WHERE x.is_leave_day IS NOT TRUE GROUP BY 1, 2, 3, 4
        ),
        oh AS (
            SELECT analytics.business_date(l.created_at, tz.timezone) AS d, g.bank_id, g.agency_id, g.id AS agent_id,
                   count(*) AS out_of_hours_attempts
            FROM audit.audit_logs l JOIN workforce.agents g ON g.user_id = l.user_id JOIN tz ON tz.bank_id = g.bank_id
            WHERE l.action = 'CONTACT_HOUR_VIOLATION_ATTEMPT' GROUP BY 1, 2, 3, 4
        ),
        keys AS (
            SELECT d, bank_id, agency_id, agent_id FROM v UNION SELECT d, bank_id, agency_id, agent_id FROM t
            UNION SELECT d, bank_id, agency_id, agent_id FROM p UNION SELECT d, bank_id, agency_id, agent_id FROM c
            UNION SELECT d, bank_id, agency_id, agent_id FROM bt UNION SELECT d, bank_id, agency_id, agent_id FROM oh
        )
        SELECT k.d AS activity_date, k.bank_id, k.agency_id, k.agent_id,
               CASE WHEN EXISTS (SELECT 1 FROM workforce.leave_requests lr WHERE lr.agent_id = k.agent_id
                                 AND lr.status = 'APPROVED' AND k.d BETWEEN lr.from_date AND lr.to_date)
                    THEN 'ON_LEAVE' ELSE 'PRESENT' END AS attendance_status,
               coalesce(v.visits, 0) AS visits, coalesce(v.met_visits, 0) AS met_visits,
               coalesce(v.distinct_cases_visited, 0) AS distinct_cases_visited,
               coalesce(t.ptps_set, 0) AS ptps_set,
               coalesce(p.payments_verified_count, 0) AS payments_verified_count,
               coalesce(p.payments_verified_amount, 0) AS payments_verified_amount,
               coalesce(p.payments_pending_amount, 0) AS payments_pending_amount,
               coalesce(c.calls, 0) AS calls, coalesce(c.calls_answered, 0) AS calls_answered,
               coalesce(bt.planned_stops, 0) AS planned_stops, coalesce(bt.visited_stops, 0) AS visited_stops,
               coalesce(bt.planned_km, 0) AS planned_km, bt.actual_km,
               coalesce(oh.out_of_hours_attempts, 0) AS out_of_hours_attempts,
               coalesce(v.geo_unverified_visits, 0) AS geo_unverified_visits,
               coalesce(v.consent_missing_visits, 0) AS consent_missing_visits,
               least(v.first_at, c.first_at) AS first_activity_at,
               greatest(v.last_at, c.last_at) AS last_activity_at
        FROM keys k
        LEFT JOIN v  ON (v.d, v.agent_id)   = (k.d, k.agent_id)
        LEFT JOIN t  ON (t.d, t.agent_id)   = (k.d, k.agent_id)
        LEFT JOIN p  ON (p.d, p.agent_id)   = (k.d, k.agent_id)
        LEFT JOIN c  ON (c.d, c.agent_id)   = (k.d, k.agent_id)
        LEFT JOIN bt ON (bt.d, bt.agent_id) = (k.d, k.agent_id)
        LEFT JOIN oh ON (oh.d, oh.agent_id) = (k.d, k.agent_id)
    """)
    op.execute("CREATE UNIQUE INDEX uq_mv_field_activity_daily ON analytics.mv_field_activity_daily "
               "(activity_date, agency_id, agent_id)")

    # ── live views ─────────────────────────────────────────────────────────
    op.execute("""
        CREATE VIEW analytics.v_case_360 WITH (security_invoker = true) AS
        SELECT c.id AS case_id, c.case_number, c.status, c.bank_id, c.agency_id,
               coalesce(ag.trade_name, ag.legal_name) AS agency_name, pl.placed_on,
               c.agent_id, au.full_name AS agent_name,
               c.customer_id, cu.full_name AS customer_name, cu.phone_primary, cu.address_line1, cu.city,
               cu.pan_masked, cu.aadhaar_masked,
               c.loan_id, l.loan_type, l.dpd, l.dpd_bucket, l.total_outstanding, l.overdue_amount,
               c.target_amount, coalesce(pay.collected_verified, 0) AS collected_verified,
               lv.check_in_time AS last_visit_at, lv.outcome AS last_visit_outcome,
               lc.called_at AS last_call_at, lc.outcome AS last_call_outcome,
               greatest(lv.check_in_time, lc.called_at) AS last_contact_at,
               ap.committed_date AS active_ptp_date, ap.committed_amount AS active_ptp_amount,
               (od.id IS NOT NULL) AS has_open_dispute, c.is_escalated,
               mp.probability AS latest_probability, mp.band AS latest_band, mp.model_version AS latest_model_version,
               CASE WHEN lc.called_at IS NULL OR (lv.check_in_time IS NOT NULL AND lv.check_in_time >= lc.called_at)
                    THEN lv.borrower_disposition ELSE lc.borrower_disposition END AS latest_disposition
        FROM collections.cases c
        JOIN lending.customers cu ON cu.id = c.customer_id
        JOIN lending.loans l ON l.id = c.loan_id
        JOIN tenancy.agencies ag ON ag.id = c.agency_id
        LEFT JOIN collections.placements pl ON pl.id = c.placement_id
        LEFT JOIN workforce.agents g ON g.id = c.agent_id
        LEFT JOIN tenancy.users au ON au.id = g.user_id
        LEFT JOIN LATERAL (SELECT sum(amount) AS collected_verified FROM collections.payments
                           WHERE case_id = c.id AND status = 'VERIFIED') pay ON true
        LEFT JOIN LATERAL (SELECT check_in_time, outcome, borrower_disposition FROM collections.visits
                           WHERE case_id = c.id ORDER BY check_in_time DESC LIMIT 1) lv ON true
        LEFT JOIN LATERAL (SELECT called_at, outcome, borrower_disposition FROM collections.call_logs
                           WHERE case_id = c.id ORDER BY called_at DESC LIMIT 1) lc ON true
        LEFT JOIN LATERAL (SELECT committed_date, committed_amount FROM collections.ptps
                           WHERE case_id = c.id AND status = 'ACTIVE' ORDER BY committed_date LIMIT 1) ap ON true
        LEFT JOIN LATERAL (SELECT id FROM collections.disputes
                           WHERE case_id = c.id AND status IN ('OPEN', 'UNDER_REVIEW', 'ESCALATED_TO_BANK') LIMIT 1) od ON true
        LEFT JOIN LATERAL (SELECT probability, band, model_version FROM ml.model_predictions
                           WHERE case_id = c.id ORDER BY as_of_date DESC, scored_at DESC LIMIT 1) mp ON true
    """)
    op.execute("""
        CREATE VIEW analytics.v_today_field_activity WITH (security_invoker = true) AS
        SELECT g.id AS agent_id, g.bank_id, g.agency_id, u.full_name AS agent_name, bd.today AS activity_date,
               CASE WHEN EXISTS (SELECT 1 FROM workforce.leave_requests lr WHERE lr.agent_id = g.id
                                 AND lr.status = 'APPROVED' AND bd.today BETWEEN lr.from_date AND lr.to_date)
                    THEN 'ON_LEAVE' ELSE g.status::text END AS attendance_status,
               bt.status AS beat_status, coalesce(bt.total_cases, 0) AS stops_planned,
               coalesce(bt.cases_completed, 0) AS stops_visited,
               coalesce(vs.visits, 0) AS visits, coalesce(vs.met, 0) AS met_visits,
               coalesce(ps.verified, 0) AS payments_verified_amount, coalesce(ps.pending, 0) AS payments_pending_amount,
               coalesce(ts.ptps_set, 0) AS ptps_set,
               g.last_known_latitude AS last_latitude, g.last_known_longitude AS last_longitude,
               g.last_location_update AS last_location_at,
               (extract(epoch FROM (now() - g.last_location_update)) / 60)::int AS minutes_since_ping,
               g.sos_active
        FROM workforce.agents g
        JOIN tenancy.users u ON u.id = g.user_id
        JOIN tenancy.banks b ON b.id = g.bank_id
        CROSS JOIN LATERAL (SELECT analytics.business_date(now(), b.timezone) AS today) bd
        LEFT JOIN planning.beats bt ON bt.agent_id = g.id AND bt.beat_date = bd.today
        LEFT JOIN LATERAL (SELECT count(*) AS visits, count(*) FILTER (WHERE customer_met) AS met
                           FROM collections.visits v WHERE v.agent_id = g.id
                           AND analytics.business_date(v.check_in_time, b.timezone) = bd.today) vs ON true
        LEFT JOIN LATERAL (SELECT sum(amount) FILTER (WHERE status = 'VERIFIED') AS verified,
                                  sum(amount) FILTER (WHERE status = 'PENDING_VERIFICATION') AS pending
                           FROM collections.payments p WHERE p.agent_id = g.id
                           AND analytics.business_date(p.payment_date, b.timezone) = bd.today) ps ON true
        LEFT JOIN LATERAL (SELECT count(*) AS ptps_set FROM collections.ptps t WHERE t.agent_id = g.id
                           AND analytics.business_date(t.created_at, b.timezone) = bd.today) ts ON true
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS analytics.v_today_field_activity")
    op.execute("DROP VIEW IF EXISTS analytics.v_case_360")
    for mv in reversed(MATERIALIZED):
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS analytics.{mv}")
    for v in ("dim_bucket", "dim_product", "dim_agent", "dim_agency", "dim_region", "dim_date"):
        op.execute(f"DROP VIEW IF EXISTS analytics.{v}")
    op.execute("DROP FUNCTION IF EXISTS analytics.business_date(timestamptz, text)")
    op.drop_index(op.f("ix_mv_refresh_log_view_name_started_at"), table_name="mv_refresh_log", schema="analytics")
    op.drop_table("mv_refresh_log", schema="analytics")
