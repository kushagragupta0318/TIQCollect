"""record a direct bank payment as a payment nobody collected

Revision ID: a5f8c31d7e40
Revises: f7d3e91a45c2
Create Date: 2026-09-09

THE DEFECT. `ingest_daily` handles `bank_action = PAID_DIRECT` — the borrower
paid the bank directly — by closing the case as PAID with a resolution note. It
creates no `Payment` row; verified, there is not one `Payment(` construction in
that script. But `ml/pipeline/outcomes.py` derives the model's label exclusively
from VERIFIED `Payment` rows, and `censoring_status` does not censor
`CaseStatus.PAID` (correctly — PAID is the success outcome, not a withdrawal
from the collectable population). So the row was labelled, with `paid = 0`, as
NOT_RECOVERED, y = 1.

**A borrower who paid — the clearest positive outcome there is — read as a
failure.** Not a coverage gap but a biased target: the model would have been
monitored on "did an agent collect it" rather than "did the borrower pay".

Latent rather than manifest on the demo book: 0 of 323 PAID cases lack a
verified payment, because no real bank feed has run against it. It fires the
first time one does.

TWO CHANGES, AND THE OUTCOME DEFINITION IS NOT ONE OF THEM.

  1. `payments.agent_id` becomes NULLABLE. The money belongs in the ledger; the
     agent attribution does not exist, and inventing one would inflate that
     agent's collections, leaderboard and `affinity_score` — which feeds
     `eb_multiplier` and therefore the allocator's `prob_recovery`. NULL means
     "nobody collected this", which is true. Every agent-scoped aggregate in
     `app/` filters `agent_id == x` or `.in_(ids)`, so a NULL row drops out of
     all of them exactly as the absent row used to; `ml/empirical_bayes` groups
     without such a filter and is guarded explicitly in the same change.

  2. `payment_mode_enum` gains `BANK_DIRECT`, so the channel is legible in the
     data rather than inferred from a NULL.

`ml/pipeline/outcomes.py` is UNTOUCHED. The material-payment rule still reads
`paid_in_window >= 0.8 * min(overdue_amount, emi_amount)` over VERIFIED payments
in the window, and it now sees the direct payment because the direct payment is
finally there. Teaching the labeller a second kind of evidence was the other
option and is worse: it would have had no amount to test the threshold against,
so the threshold would have been bypassed for exactly these rows — a silent
change to the target, which is the one thing this fix must not do.

SETTLED is deliberately NOT given a payment row. It is already
`CENSORED_SETTLED`, and a bank-approved reduction is not the borrower repaying.

DOWNGRADE re-tightens the column, which fails if any NULL-agent payment exists.
That is intended: the alternative is deleting real money to satisfy a schema.
"""
import sqlalchemy as sa
from alembic import op

revision = "a5f8c31d7e40"
down_revision = "f7d3e91a45c2"
branch_labels = None
depends_on = None

_ENUM = "payment_mode_enum"


def upgrade() -> None:
    bind = op.get_bind()

    op.alter_column("payments", "agent_id",
                    existing_type=sa.String(length=36),
                    nullable=True)

    if bind.dialect.name == "postgresql":
        # IF NOT EXISTS so a re-run is a no-op; ALTER TYPE ... ADD VALUE cannot
        # run inside a transaction on older servers, hence the autocommit block.
        with op.get_context().autocommit_block():
            op.execute(f"ALTER TYPE {_ENUM} ADD VALUE IF NOT EXISTS 'BANK_DIRECT'")


def downgrade() -> None:
    # Deliberately NOT deleting the rows this made possible. If the tightening
    # fails, there are direct payments recorded and they are real.
    op.alter_column("payments", "agent_id",
                    existing_type=sa.String(length=36),
                    nullable=False)
    # Postgres cannot drop a value from an enum; BANK_DIRECT stays. Harmless:
    # nothing writes it once ingest_daily is rolled back with this migration.
