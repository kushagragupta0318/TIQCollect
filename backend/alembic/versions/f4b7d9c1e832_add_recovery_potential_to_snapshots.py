"""add recovery potential columns to repayment_score_snapshots

The recovery scorecard (app/ml/recovery_scorecard.py) writes a second, independent
score onto the same snapshot row as the repayment likelihood: HIGH/MEDIUM/LOW plus
the 30/60/90-day rates behind it, and — one horizon at a time — how much actually
came back. See app/models/repayment_snapshot.py for why these live on the existing
row rather than in a parallel table.

WHY THIS MIGRATION EXISTS AT ALL. The feature was first written on the assumption
that no migration was needed, because the base schema is create_all-managed (the
baseline migration, d7a84c5a710f, is empty). That reasoning does not hold for THIS
table: repayment_score_snapshots was created by e91b4c2d70af, and create_all
creates missing TABLES only — it never ALTERs an existing one to add a column. So
an already-populated database would have got none of these columns, and the
manager endpoints would have failed with UndefinedColumn on recovery_rate_90.

IDEMPOTENT BY INSPECTION, deliberately. This repo has two live schema paths that
disagree about who owns DDL:

  * scripts/seed_data.py:1131 calls Base.metadata.create_all(), and
    docker-entrypoint.sh never runs `alembic upgrade`. A freshly seeded database
    therefore has these columns ALREADY, from the models, and no alembic_version
    row at all.
  * an existing database predating the feature has the table but not the columns.

A migration that blindly ADD COLUMNed would succeed on the second and fail with
DuplicateColumn on the first, the moment anyone stamped and upgraded it. So each
column is added only if absent. That is not defensive padding — it is the only
behaviour that is correct under both paths, and it keeps `alembic upgrade head`
safe to run against any database this project produces.

Revision ID: f4b7d9c1e832
Revises: e91b4c2d70af
Create Date: 2026-08-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f4b7d9c1e832'
down_revision: Union[str, None] = 'e91b4c2d70af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'repayment_score_snapshots'
RECOVERY_INDEX = 'ix_repayment_snapshot_recovery_unlabelled'

# Mirrors models/repayment_snapshot.py exactly. JSON with a JSONB variant rather
# than plain JSONB: the model does the same, because tests/test_otp_service.py
# builds the whole schema on SQLite and a Postgres-only type anywhere under
# app/models breaks fourteen unrelated tests.
_JSON_DOC = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def _recovery_columns() -> list[sa.Column]:
    """Fresh Column objects on every call — a Column instance can only be
    attached to one table, so reusing a module-level list across upgrade() and a
    test would bind the first one and raise on the second."""
    return [
        # ── The score ────────────────────────────────────────────────────────
        # A plain String, not the RecoveryPotential enum, matching `outcome` on
        # this table: adding a value to a Postgres enum needs a migration and can
        # never be removed, which is a poor trade for a vocabulary that is ours.
        sa.Column('recovery_potential', sa.String(length=8), nullable=True),
        sa.Column('recovery_rate_30', sa.Float(), nullable=True),
        sa.Column('recovery_rate_60', sa.Float(), nullable=True),
        sa.Column('recovery_rate_90', sa.Float(), nullable=True),
        # How fast, as distinct from how much. The 30/60 rates are derived from
        # it, so a modeller reading this row does not have to re-derive the ramp.
        sa.Column('recovery_speed_index', sa.Float(), nullable=True),
        sa.Column('recovery_evidence_coverage', sa.Float(), nullable=True),
        sa.Column('recovery_source', sa.String(length=20), nullable=True),
        sa.Column('recovery_model_version', sa.String(length=40), nullable=True),
        sa.Column('recovery_contributions', _JSON_DOC, nullable=True),

        # ── What actually came back. NULL means "that horizon has not matured
        # yet", never "nothing arrived" — the training pull depends on the
        # distinction, so these must stay nullable.
        sa.Column('recovered_amount_30', sa.Float(), nullable=True),
        sa.Column('recovered_amount_60', sa.Float(), nullable=True),
        sa.Column('recovered_amount_90', sa.Float(), nullable=True),

        # How far the labeller has got: 0, 30, 60 or 90.
        #
        # NOT NULL with a server_default, and both halves matter. The default
        # backfills every existing row to 0 in the same statement, so no UPDATE
        # pass is needed and the column is never transiently NULL. And because
        # the default is a constant rather than a volatile expression, Postgres
        # 11+ adds it as metadata only — no table rewrite, no long lock, whatever
        # the row count.
        #
        # NOT NULL is also what makes the partial index below have a total
        # predicate: `recovery_labelled_through_days < 90` would silently exclude
        # NULL rows from the labeller's scan, and those rows would never be
        # labelled again.
        sa.Column('recovery_labelled_through_days', sa.Integer(), nullable=False,
                  server_default=sa.text('0')),
    ]


def _present(bind) -> set[str]:
    return {c['name'] for c in sa.inspect(bind).get_columns(TABLE)}


def _indexes(bind) -> set[str]:
    return {i['name'] for i in sa.inspect(bind).get_indexes(TABLE)}


# ── Offline (--sql) support ──────────────────────────────────────────────────
# `alembic upgrade --sql` runs against a MockConnection, which has no inspection
# system, so the online path above cannot work there. Offline mode is how this
# gets reviewed before it is allowed near a populated database, which makes it
# worth supporting rather than documenting as broken.
#
# The offline path leans on Postgres's own IF NOT EXISTS / IF EXISTS instead of
# inspecting — same idempotency, no round trip. It is Postgres-specific, which is
# acceptable precisely here: generating SQL for a named dialect is what offline
# mode is for. The online path stays dialect-neutral so the SQLite round-trip
# tests keep testing something real.
def _offline() -> bool:
    return op.get_context().as_sql


def _column_ddl(column: sa.Column) -> str:
    from sqlalchemy.dialects import postgresql as pg
    ddl = f"ADD COLUMN IF NOT EXISTS {column.name} " \
          f"{column.type.compile(dialect=pg.dialect())}"
    if column.server_default is not None:
        ddl += f" DEFAULT {column.server_default.arg.text}"
    if not column.nullable:
        ddl += " NOT NULL"
    return ddl


def upgrade() -> None:
    if _offline():
        # One ALTER TABLE, so the whole change is a single statement to review.
        op.execute(
            f"ALTER TABLE {TABLE}\n    "
            + ",\n    ".join(_column_ddl(c) for c in _recovery_columns())
        )
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {RECOVERY_INDEX} ON {TABLE} (as_of_date)"
            f" WHERE recovery_labelled_through_days < 90"
        )
        return

    bind = op.get_bind()
    present = _present(bind)

    for column in _recovery_columns():
        if column.name not in present:
            op.add_column(TABLE, column)

    # The recovery labeller's scan. A SEPARATE partial index rather than a
    # widening of ix_repayment_snapshot_unlabelled: a row can have its repayment
    # `outcome` filled in at day 30 and still owe its 60- and 90-day recovery
    # figures, so "outcome IS NULL" stops describing the work left to do.
    #
    # Partial for the same reason as its sibling — every row it finds, it removes
    # from the index by advancing recovery_labelled_through_days, so it stays
    # small no matter how large the table grows.
    if RECOVERY_INDEX not in _indexes(bind):
        op.create_index(
            RECOVERY_INDEX, TABLE, ['as_of_date'],
            postgresql_where=sa.text('recovery_labelled_through_days < 90'),
        )


def downgrade() -> None:
    """Remove exactly what upgrade() added, and nothing else.

    Every drop is guarded, so downgrading a database that never had these columns
    is a no-op rather than an error — the mirror of upgrade()'s tolerance, and
    necessary for the same two-schema-paths reason.

    Note what is NOT dropped: ix_repayment_snapshot_unlabelled and every other
    index on this table belong to e91b4c2d70af. This migration does not own them,
    so it must not remove them.
    """
    if _offline():
        op.execute(f"DROP INDEX IF EXISTS {RECOVERY_INDEX}")
        op.execute(
            f"ALTER TABLE {TABLE}\n    "
            + ",\n    ".join(f"DROP COLUMN IF EXISTS {c.name}"
                             for c in reversed(_recovery_columns()))
        )
        return

    bind = op.get_bind()

    # The index first: it is built on recovery_labelled_through_days, and
    # dropping that column out from under it would take the index with it
    # implicitly rather than because this migration said so.
    if RECOVERY_INDEX in _indexes(bind):
        op.drop_index(RECOVERY_INDEX, table_name=TABLE)

    present = _present(bind)
    for column in reversed(_recovery_columns()):
        if column.name in present:
            op.drop_column(TABLE, column.name)
