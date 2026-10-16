"""agent experience_years + strong_loan_type: capture an agent's years of
experience and the loan product they collect best (shown in Add Agent).

Two NULLABLE columns on workforce.agents:
  * experience_years  — a small non-negative integer (years in the field).
  * strong_loan_type  — the agent's strongest product, reusing the existing
                        public.loan_type_enum (create_type=False: the type was
                        created by v2_0002, so this only references it).

Both optional, so every existing agent row and any create path that omits them
is unaffected. No backfill, no default.

Chains off v2_0039. Number lives only in this file; reparse at merge.
Revision ID: v2_0040
Revises: v2_0039
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "v2_0040"
down_revision = "v2_0039"
branch_labels = None
depends_on = None

# The product enum already exists (v2_0002). create_type=False references it
# rather than trying to CREATE TYPE again.
_LOAN_TYPE = postgresql.ENUM(
    "HOME", "AUTO", "PERSONAL", "BUSINESS", "GOLD", "CREDIT_CARD", "EDUCATION", "MICROFINANCE",
    name="loan_type_enum", schema="public", create_type=False,
)


def upgrade() -> None:
    op.add_column("agents", sa.Column("experience_years", sa.SmallInteger(), nullable=True), schema="workforce")
    op.add_column("agents", sa.Column("strong_loan_type", _LOAN_TYPE, nullable=True), schema="workforce")


def downgrade() -> None:
    op.drop_column("agents", "strong_loan_type", schema="workforce")
    op.drop_column("agents", "experience_years", schema="workforce")
