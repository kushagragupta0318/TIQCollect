# ─── CHANGELOG (prototype → product) ───
# New file, 2026-08-24. Covers
# alembic/versions/f4b7d9c1e832_add_recovery_potential_to_snapshots.py.
#
# This file exists because of a near-miss. The recovery feature was built on the
# assumption that no migration was needed — the baseline migration is empty and
# the base schema is create_all-managed. That is true of `loans`; it is NOT true
# of repayment_score_snapshots, which e91b4c2d70af created. create_all makes
# missing TABLES, never missing COLUMNS, so an existing database would have got
# none of the recovery columns and every manager endpoint touching
# recovery_rate_90 would have failed with UndefinedColumn.
#
# The parity test below is the guard against that class of mistake recurring: add
# a recovery column to the model and forget the migration, and it fails here
# rather than in production against a database nobody reseeded.
import importlib.util
import pathlib
from datetime import date

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.repayment_snapshot import RepaymentSnapshot
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

# 2026-09-24 (B11): the v1 chain moved to alembic/versions_v1/ (history, off
# the upgrade path) and alembic/versions/ now holds the v2 baseline. This
# file's parity tests are about the v1 revision, so they read it there; the
# single-head test reads the LIVE chain.
MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions_v1"
LIVE_CHAIN = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
REVISION = "f4b7d9c1e832"
PARENT = "e91b4c2d70af"
TABLE = "repayment_score_snapshots"
RECOVERY_INDEX = "ix_repayment_snapshot_recovery_unlabelled"


def _load(revision: str, where: pathlib.Path = MIGRATIONS):
    """Import a migration module by revision id, without alembic's env."""
    path = next(p for p in where.glob("*.py") if p.name.startswith(revision + "_"))
    return _load_path(path)


def _load_path(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"mig_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _load(REVISION)


def _model_recovery_columns():
    return {c.name: c for c in RepaymentSnapshot.__table__.columns
            if c.name.startswith("recover")}


# ── Model / migration parity ────────────────────────────────────────────────
def test_migration_adds_exactly_the_model_recovery_columns(migration):
    """THE POINT OF THIS FILE. If these two sets drift, a database built by
    migration and one built by create_all stop being the same schema — and
    because nothing in this repo runs `alembic upgrade` automatically, the
    divergence surfaces only when a query hits the missing column."""
    in_migration = {c.name for c in migration._recovery_columns()}
    in_model = set(_model_recovery_columns())
    assert in_migration == in_model, (
        f"only in migration: {sorted(in_migration - in_model)}; "
        f"only in model: {sorted(in_model - in_migration)}"
    )


def test_column_count_is_the_documented_thirteen(migration):
    """Pinned so a silent addition or removal is visible in the diff of this
    test, not just in the migration."""
    assert len(migration._recovery_columns()) == 13


def test_nullability_matches_the_model(migration):
    """recovery_labelled_through_days is the only NOT NULL column, and that is
    load-bearing: the partial index predicate `< 90` would silently exclude NULL
    rows, and those rows would never be labelled again."""
    model = _model_recovery_columns()
    for column in migration._recovery_columns():
        assert column.nullable == model[column.name].nullable, column.name

    not_null = [c.name for c in migration._recovery_columns() if not c.nullable]
    assert not_null == ["recovery_labelled_through_days"]


def test_types_match_the_model(migration):
    """2026-09-24 (v2): money moved from DOUBLE to NUMERIC(14,2) in the model
    (design §2.3). This v1 revision is history and still says Float for the
    rupee columns; the v2 baseline creates them as NUMERIC. So: every non-money
    column must still agree, and every money column must be Float here and
    Numeric in the model — a third type on either side is a real drift."""
    import sqlalchemy as sa
    model = _model_recovery_columns()
    for column in migration._recovery_columns():
        mt = model[column.name].type
        if isinstance(mt, sa.Numeric) and not isinstance(mt, sa.Float):
            assert isinstance(column.type, sa.Float), column.name
        else:
            assert type(column.type) is type(mt), column.name


def test_the_not_null_column_has_a_server_default(migration):
    """Without it, adding a NOT NULL column to a populated table fails outright.
    With a CONSTANT default, Postgres 11+ also does it as metadata only — no
    table rewrite and no long lock, whatever the row count."""
    column = next(c for c in migration._recovery_columns()
                  if c.name == "recovery_labelled_through_days")
    assert column.server_default is not None
    assert "0" in str(column.server_default.arg)


# ── Chain integrity ─────────────────────────────────────────────────────────
def test_it_revises_the_snapshot_table_migration(migration):
    assert migration.revision == REVISION
    assert migration.down_revision == PARENT


def test_there_is_exactly_one_head():
    """Two heads make `alembic upgrade head` ambiguous and it refuses to run."""
    revisions, parents = set(), set()
    for path in LIVE_CHAIN.glob("*.py"):
        module = _load_path(path)
        revisions.add(module.revision)
        if module.down_revision:
            parents.add(module.down_revision)
    heads = revisions - parents
    # ONE head, whatever it happens to be. This asserted heads == {REVISION},
    # which held only while this was the newest migration: the next one to land
    # failed a test about ambiguity on a chain that was not ambiguous. Identity
    # of this particular revision is already covered by
    # test_it_revises_the_snapshot_table_migration.
    assert len(heads) == 1, f"expected a single head, found {sorted(heads)}"
    # (2026-09-24: this also asserted REVISION was on the chain. The v1
    # revision is history now; the v2 baseline creates the same columns, and
    # test_migration_adds_exactly_the_model_recovery_columns still holds the
    # v1 revision to the model.)
    assert "v2_0001" in revisions


# ── Round trip against a real database ──────────────────────────────────────
def _table_without_recovery(metadata):
    """The snapshot table as e91b4c2d70af left it — i.e. a database from before
    the recovery feature. Only the columns the migration cares about; the FKs and
    the rest are irrelevant to adding a column and would need three more tables.
    """
    return sa.Table(
        TABLE, metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("loan_id", sa.String(), nullable=False),
        sa.Column("customer_id", sa.String(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("likelihood", sa.Float(), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=True),
    )


@pytest.fixture()
def legacy_db():
    """A pre-feature database with one existing snapshot row in it."""
    engine = make_engine()
    metadata = sa.MetaData()
    table = _table_without_recovery(metadata)
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(table.insert().values(
            id=test_id("snap-1"), loan_id=test_id("loan-1"), customer_id=test_id("cust-1"),
            as_of_date=date(2026, 8, 1), likelihood=61.5, outcome=None,
        ))
    return engine


def _run(engine, fn):
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            fn()


def test_upgrade_adds_the_columns_to_an_existing_table(legacy_db, migration):
    before = {c["name"] for c in sa.inspect(legacy_db).get_columns(TABLE)}
    assert "recovery_rate_90" not in before

    _run(legacy_db, migration.upgrade)

    after = {c["name"] for c in sa.inspect(legacy_db).get_columns(TABLE)}
    assert set(_model_recovery_columns()) <= after


def test_existing_rows_survive_with_null_recovery_fields(legacy_db, migration):
    """The whole risk of an ALTER on a populated table. The row must still be
    there, its own values untouched, and every new field NULL — not 0.0, which
    would read as "we predicted nothing recoverable" about a loan that was never
    scored."""
    _run(legacy_db, migration.upgrade)

    with legacy_db.connect() as conn:
        row = conn.execute(sa.text(
            "SELECT id, loan_id, likelihood, recovery_potential, recovery_rate_30,"
            " recovery_rate_60, recovery_rate_90, recovered_amount_30,"
            " recovery_labelled_through_days FROM repayment_score_snapshots"
        )).mappings().one()

    assert row["id"] == test_id("snap-1")
    assert row["loan_id"] == test_id("loan-1")
    assert row["likelihood"] == 61.5
    for field in ("recovery_potential", "recovery_rate_30", "recovery_rate_60",
                  "recovery_rate_90", "recovered_amount_30"):
        assert row[field] is None, f"{field} should be NULL on a pre-feature row"
    # The one exception, and it is the server_default doing its job.
    assert row["recovery_labelled_through_days"] == 0


def test_upgrade_is_idempotent(legacy_db, migration):
    """A freshly seeded database already has these columns, from create_all, and
    no alembic_version row. The moment anyone stamps and upgrades it, a blind
    ADD COLUMN would raise DuplicateColumn. Running twice here is that scenario."""
    _run(legacy_db, migration.upgrade)
    _run(legacy_db, migration.upgrade)   # must not raise

    columns = [c["name"] for c in sa.inspect(legacy_db).get_columns(TABLE)]
    assert len(columns) == len(set(columns)), "a column was added twice"


def test_downgrade_removes_exactly_the_recovery_columns(legacy_db, migration):
    original = {c["name"] for c in sa.inspect(legacy_db).get_columns(TABLE)}

    _run(legacy_db, migration.upgrade)
    _run(legacy_db, migration.downgrade)

    assert {c["name"] for c in sa.inspect(legacy_db).get_columns(TABLE)} == original


def test_downgrade_keeps_the_data(legacy_db, migration):
    _run(legacy_db, migration.upgrade)
    _run(legacy_db, migration.downgrade)

    with legacy_db.connect() as conn:
        row = conn.execute(sa.text(
            "SELECT id, likelihood FROM repayment_score_snapshots")).mappings().one()
    assert row["id"] == test_id("snap-1") and row["likelihood"] == 61.5


def test_downgrade_is_idempotent(legacy_db, migration):
    """Downgrading a database that never had the columns is a no-op, not an
    error — the mirror of upgrade()'s tolerance."""
    _run(legacy_db, migration.downgrade)   # never upgraded; must not raise
    _run(legacy_db, migration.upgrade)
    _run(legacy_db, migration.downgrade)
    _run(legacy_db, migration.downgrade)   # must not raise
