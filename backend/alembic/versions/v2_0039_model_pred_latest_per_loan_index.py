"""ix_model_pred_latest_per_loan: the cash forecast's latest-score-per-loan read (A1).

GET /bank/strategy/cash-forecast was returning 500 for a single user. Its
recovery-risk term read every recovery_risk prediction in a 45-day window
newest-first and kept the first row per loan in Python: 122,674 rows to find
7,844 loans' latest scores, with a 3.6 MB external merge sort. Measured on the
dev book at 15,366 ms against a 15 s statement_timeout, so Postgres cancelled
it and the endpoint 500'd (QueryCanceled in the logs).

cash_forecast.py now aggregates that term in SQL, driven by the bank's active
overdue loans rather than by its prediction history, and this index is what
makes that read cheap -- the two are one fix and neither works alone. WITHOUT
this index the new shape is SLOWER than the one it replaced (5.5 s vs 2.5 s
warm on the dev book), because every per-loan lookup degrades to a Merge
Append across eight partitions with a heap fetch for the filter columns.

Column order is the access path, not a guess: equality on
(bank_id, model_name, loan_id), then as_of_date DESC, scored_at DESC to make
the newest row per loan the first one the index yields, so LIMIT 1 stops
immediately. INCLUDE (probability) carries the only value the read wants, and
the partial predicate carries the two filters, which together make it an INDEX
ONLY SCAN -- measured Heap Fetches: 0, 196-396 ms for the whole term.

ml.model_predictions is RANGE-partitioned by as_of_date (v2_0003). CREATE INDEX
on the partitioned parent creates it on every partition and on every partition
created later, which is what the monthly partitions need. It is NOT
CONCURRENTLY, which Postgres does not allow on a partitioned parent: this takes
an ALTER-level lock on the parent for the build (8-16 s measured on 125k rows),
so it belongs in a deploy window like any other migration here. There is no
data change and no backfill.

Chains off v2_0038. Number lives only in this file; reparse at merge.
Revision ID: v2_0039
Revises: v2_0038
Create Date: 2026-10-07
"""
from alembic import op

revision = "v2_0039"
down_revision = "v2_0038"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_model_pred_latest_per_loan"

# IF NOT EXISTS: the same DDL was created by hand on the dev book to measure
# the fix before this migration existed. Everywhere else it is a plain create.
_CREATE = f"""
    CREATE INDEX IF NOT EXISTS {INDEX_NAME}
        ON ml.model_predictions (bank_id, model_name, loan_id, as_of_date DESC, scored_at DESC)
        INCLUDE (probability)
     WHERE is_modelled AND probability IS NOT NULL
"""


def upgrade() -> None:
    op.execute(_CREATE)


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS ml.{INDEX_NAME}")
