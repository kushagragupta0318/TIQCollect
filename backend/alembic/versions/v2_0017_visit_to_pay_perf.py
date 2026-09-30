"""v_visit_to_pay: attribution through the visits index, payments tenant-filtered (perf fix).

d4 (tiqcollect-a2) and coordinator 0c, 2026-09-29. v2_0013's view referenced
its `vis` CTE twice, so Postgres materialised it unindexed, and the
attribution LATERAL scanned that temp result once per payment, across EVERY
bank's verified payments: O(payments x visits). On the B16 book a single
test_pg_bank_overview query was still running at 2m23s with no progress.

Now the LATERAL reads collections.visits directly, through ix_visit_case
(case_id, check_in_time), and the payments are tenant-filtered before it.
The result is the same: a case's visits carry the case's agency (composite
FK), so the latest prior visit of a tenant's payment is a tenant visit. New
index ix_payment_case_date (case_id, payment_date) for the payment side.

Rule 4a: v2_0013 has landed, so this revision re-creates the view with
CREATE OR REPLACE (same columns, same order). The downgrade restores
v2_0013's exact text, frozen below.

Revision ID: v2_0017
Revises: v2_0016
Create Date: 2026-09-29
"""
from alembic import op

revision = "v2_0017"
down_revision = "v2_0016"
branch_labels = None
depends_on = None

VISIT_TO_PAY_DAYS = 7
V_TENANT = ("v.bank_id = tenancy.current_bank_id() AND (tenancy.current_scope() = 'BANK' OR "
            "(tenancy.current_scope() = 'AGENCY' AND v.agency_id = tenancy.current_agency_id()))")
P_TENANT = V_TENANT.replace("v.bank_id", "p.bank_id").replace("v.agency_id", "p.agency_id")

VISIT_TO_PAY = f"""
CREATE OR REPLACE VIEW analytics.v_visit_to_pay WITH (security_invoker = true, security_barrier = true) AS
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
        SELECT x.id, x.check_in_time FROM collections.visits x
        WHERE x.case_id = p.case_id AND x.check_in_time <= p.payment_date
        ORDER BY x.check_in_time DESC LIMIT 1) lv
    WHERE {P_TENANT}
      AND p.status::text = 'VERIFIED'
      AND analytics.business_date(p.payment_date, b.timezone)
          <= analytics.business_date(lv.check_in_time, b.timezone) + {VISIT_TO_PAY_DAYS}
)
SELECT v.id AS visit_id, v.bank_id, v.agency_id, v.agent_id, v.case_id, v.visit_date, v.customer_met,
       coalesce(sum(a.amount), 0) AS paid_amount_7d, count(a.id) > 0 AS paid_within_7d,
       min(a.paid_date) AS first_paid_date
FROM vis v LEFT JOIN attributed a ON a.visit_id = v.id
GROUP BY v.id, v.bank_id, v.agency_id, v.agent_id, v.case_id, v.visit_date, v.customer_met
"""

# v2_0013's view, verbatim, for the downgrade (frozen literal; tests pin it to v2_0013).
VISIT_TO_PAY_V2_0013 = """
CREATE OR REPLACE VIEW analytics.v_visit_to_pay WITH (security_invoker = true, security_barrier = true) AS
WITH vis AS (
    SELECT v.id, v.bank_id, v.agency_id, v.agent_id, v.case_id, v.customer_met, v.check_in_time,
           analytics.business_date(v.check_in_time, b.timezone) AS visit_date
    FROM collections.visits v JOIN tenancy.banks b ON b.id = v.bank_id
    WHERE v.bank_id = tenancy.current_bank_id() AND (tenancy.current_scope() = 'BANK' OR (tenancy.current_scope() = 'AGENCY' AND v.agency_id = tenancy.current_agency_id()))
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
      AND analytics.business_date(p.payment_date, b.timezone) <= lv.visit_date + 7
)
SELECT v.id AS visit_id, v.bank_id, v.agency_id, v.agent_id, v.case_id, v.visit_date, v.customer_met,
       coalesce(sum(a.amount), 0) AS paid_amount_7d, count(a.id) > 0 AS paid_within_7d,
       min(a.paid_date) AS first_paid_date
FROM vis v LEFT JOIN attributed a ON a.visit_id = v.id
GROUP BY v.id, v.bank_id, v.agency_id, v.agent_id, v.case_id, v.visit_date, v.customer_met
"""


def upgrade() -> None:
    op.create_index("ix_payment_case_date", "payments", ["case_id", "payment_date"], schema="collections")
    op.execute(VISIT_TO_PAY)


def downgrade() -> None:
    op.execute(VISIT_TO_PAY_V2_0013)
    op.drop_index("ix_payment_case_date", table_name="payments", schema="collections")
