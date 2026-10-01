"""v_visit_to_pay: attribute per visit through the payments index, not a cross join.

v2_0017 made the view's indexes right and left its SHAPE wrong. Every tenancy
predicate in it is `current_setting(...)`, whose value the planner cannot know
when it plans, so it estimates one row where there are tens of thousands --
`Seq Scan on payments rows=1, actual 21,278` -- and joins the two halves with a
nested loop and a bare `Join Filter: (x.id = v.id)`. Measured on the committed
demo book (41k visits, 22k payments) over a 12-month window, ANALYZEd:

    Nested Loop Left Join  Join Filter: (x.id = v.id)
    Rows Removed by Join Filter: 144,341,315        <- 18,611 x 7,756
    Execution Time: 18,509 ms

The estimate cannot be fixed -- a STABLE function is still not a value at plan
time, and ANALYZE does not help -- so the join has to go. Each visit now looks
its own payments up through `ix_payment_case_date` (case_id, payment_date),
which v2_0017 built, and the "is this the visit the payment belongs to" test
becomes NOT EXISTS over a later visit instead of ORDER BY ... LIMIT 1 inside a
LATERAL evaluated once per payment. Being index-driven, it costs the same
whatever the planner guesses.

Three things are deliberately kept as v2_0017 had them:

  - The later-visit guard scans `collections.visits` UNSCOPED. A payment whose
    nearest preceding visit belongs to ANOTHER agency is attributed to that
    visit and so appears for nobody in an agency-scoped read. Scoping this scan
    would hand that payment to the wrong agency.
  - Both sides of the 7-day window use the PAYMENT's bank timezone.
  - The column list, security_invoker and security_barrier.

One thing deliberately changes: ties. `ORDER BY check_in_time DESC LIMIT 1`
picked an arbitrary visit among two with the same check_in_time; the guard
breaks the tie on (check_in_time, id), so the later id wins and the same book
always reads the same way.

This depends on nothing in v2_0019 (l8) or v2_0020 (l5) and is written against
v2_0018; renumber it if the chain lands in another order.

Revision ID: v2_0021
Revises: v2_0018
Create Date: 2026-10-01
"""
from alembic import op

revision = "v2_0021"
down_revision = "v2_0018"
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
)
SELECT v.id AS visit_id, v.bank_id, v.agency_id, v.agent_id, v.case_id, v.visit_date, v.customer_met,
       coalesce(sum(a.amount), 0) AS paid_amount_7d, count(a.id) > 0 AS paid_within_7d,
       min(a.paid_date) AS first_paid_date
FROM vis v
LEFT JOIN LATERAL (
    SELECT p.id, p.amount, analytics.business_date(p.payment_date, b.timezone) AS paid_date
    FROM collections.payments p
    JOIN tenancy.banks b ON b.id = p.bank_id
    WHERE p.case_id = v.case_id
      AND p.payment_date >= v.check_in_time
      AND p.status::text = 'VERIFIED'
      AND {P_TENANT}
      AND analytics.business_date(p.payment_date, b.timezone)
          <= analytics.business_date(v.check_in_time, b.timezone) + {VISIT_TO_PAY_DAYS}
      AND NOT EXISTS (
          SELECT 1 FROM collections.visits x
          WHERE x.case_id = v.case_id
            AND (x.check_in_time, x.id) > (v.check_in_time, v.id)
            AND x.check_in_time <= p.payment_date)
) a ON true
GROUP BY v.id, v.bank_id, v.agency_id, v.agent_id, v.case_id, v.visit_date, v.customer_met
"""

# v2_0017's view, verbatim, for the downgrade. Also what the equivalence test
# reads: tests/pg/test_pg_visit_to_pay_equivalence.py builds it under a second
# name and holds the two to the same rows.
VISIT_TO_PAY_V2_0017 = f"""
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


def upgrade() -> None:
    op.execute(VISIT_TO_PAY)


def downgrade() -> None:
    op.execute(VISIT_TO_PAY_V2_0017)
