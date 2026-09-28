"""record the borrower's stated disposition on calls and visits

Revision ID: c9a3d5e7f102
Revises: b6c14e83af27
Create Date: 2026-09-16

WHY. The production-readiness audit of `recovery_risk`'s 15-feature GAM
(app/ml/artifacts/recovery_risk/2.1.0/observability/PRODUCTION_READINESS_AUDIT.md)
found that two of its inputs — `latest_disposition`, the strongest behavioural
feature the development book produced (IV 0.31), and `disposition_recency_class`
— had no source column in the product at all. The ledger world records a
six-value stance on every contact that reaches the borrower; the schema
recorded none, so the adapter could not produce either feature and the model
could not be served.

WHAT. One Postgres enum, `borrower_disposition_enum`, and one NULLABLE column
on each contact channel:

    call_logs.borrower_disposition   set on an ANSWERED call, when captured
    visits.borrower_disposition      set on a MET visit, when captured

The same type on both, because the feature pools the two channels and takes
the newest reading (`ml_scoring_service._disposition_features`).

WHAT IT DOES NOT DO. No backfill and no default. A reading nobody took is
NULL, and the adapter turns "no reading before as_of" into the model's own
never-read level ("NONE") — which is the training definition, not a stand-in.
Until the field process captures the stance, every borrower reads as
never-read; that is a documented limitation of the served model, watched by
the missingness monitor, not something this migration papers over.

DOWNGRADE drops the columns and the type; any recorded stances are lost, which
is what dropping a column means.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9a3d5e7f102"
down_revision = "b6c14e83af27"
branch_labels = None
depends_on = None

_ENUM = "borrower_disposition_enum"
_VALUES = ("WILL_PAY", "MAY_PAY", "NO_COMMITMENT", "HARDSHIP", "DISPUTE", "REFUSES")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(*_VALUES, name=_ENUM).create(bind, checkfirst=True)
        col_type = postgresql.ENUM(*_VALUES, name=_ENUM, create_type=False)
    else:
        col_type = sa.Enum(*_VALUES, name=_ENUM)
    op.add_column("call_logs", sa.Column("borrower_disposition", col_type, nullable=True))
    op.add_column("visits", sa.Column("borrower_disposition", col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_column("visits", "borrower_disposition")
    op.drop_column("call_logs", "borrower_disposition")
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(name=_ENUM).drop(bind, checkfirst=True)
