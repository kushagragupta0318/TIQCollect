"""v2 baseline, step 3 of 8: the partitions of the five partitioned parents
(docs/DATA-MODEL-V2.md §7, task B11).

Monthly RANGE partitions named <table>_pYYYYMM plus a DEFAULT partition each.
The range is fixed here — 2026-03 (the fixture's earliest allocation date)
through 2026-12 (the month this was written plus three) — and from then on the
daily maintenance task (B12, core/partitions.py) pre-creates months ahead. A
row whose key falls outside every month lands in DEFAULT, which that task
reports at ERROR rather than silently.

agent_locations is LIST(is_sos) first: SOS fixes go to one partition that
retention never touches; the trail is RANGE(recorded_at) monthly beneath it.

Bounds on TIMESTAMPTZ keys are written with an explicit +00, so the session
time zone cannot move a month boundary.

Revision ID: v2_0003
Revises: v2_0002
Create Date: 2026-09-24
"""
from alembic import op

revision = "v2_0003"
down_revision = "v2_0002"
branch_labels = None
depends_on = None

FIRST, LAST = (2026, 3), (2026, 12)          # inclusive, (year, month)

# (schema, table, key type): DATE keys take a date literal, TIMESTAMPTZ an instant.
RANGE_PARENTS = (
    ("ml", "model_predictions", "date"),
    ("planning", "allocation_decisions", "date"),
    ("audit", "audit_logs", "timestamptz"),
    ("lending", "loan_dpd_history", "date"),
)


def _months():
    y, m = FIRST
    while (y, m) <= LAST:
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        yield f"{y:04d}{m:02d}", f"{y:04d}-{m:02d}-01", f"{ny:04d}-{nm:02d}-01"
        y, m = ny, nm


def _bound(day: str, kind: str) -> str:
    return f"'{day} 00:00:00+00'" if kind == "timestamptz" else f"'{day}'"


def upgrade() -> None:
    for schema, table, kind in RANGE_PARENTS:
        op.execute(f"CREATE TABLE {schema}.{table}_default PARTITION OF {schema}.{table} DEFAULT")
        for tag, lo, hi in _months():
            op.execute(f"CREATE TABLE {schema}.{table}_p{tag} PARTITION OF {schema}.{table} "
                       f"FOR VALUES FROM ({_bound(lo, kind)}) TO ({_bound(hi, kind)})")

    # agent_locations: SOS kept forever; the trail partitioned by month.
    op.execute("CREATE TABLE workforce.agent_locations_sos PARTITION OF workforce.agent_locations "
               "FOR VALUES IN (true)")
    op.execute("CREATE TABLE workforce.agent_locations_trail PARTITION OF workforce.agent_locations "
               "FOR VALUES IN (false) PARTITION BY RANGE (recorded_at)")
    op.execute("CREATE TABLE workforce.agent_locations_trail_default PARTITION OF "
               "workforce.agent_locations_trail DEFAULT")
    for tag, lo, hi in _months():
        op.execute(f"CREATE TABLE workforce.agent_locations_trail_p{tag} PARTITION OF "
                   f"workforce.agent_locations_trail FOR VALUES FROM ({_bound(lo, 'timestamptz')}) "
                   f"TO ({_bound(hi, 'timestamptz')})")


def downgrade() -> None:
    # DETACH before DROP: a partition of a table that other tables reference
    # (model_predictions has four composite FKs pointing at it) cannot be
    # dropped while attached. Detaching an empty one is always allowed; with
    # data behind the FKs Postgres refuses, which is the right answer for a
    # downgrade that would destroy it.
    for tag, _, _ in reversed(list(_months())):
        op.execute(f"ALTER TABLE workforce.agent_locations_trail DETACH PARTITION "
                   f"workforce.agent_locations_trail_p{tag}")
        op.execute(f"DROP TABLE workforce.agent_locations_trail_p{tag}")
    op.execute("ALTER TABLE workforce.agent_locations_trail DETACH PARTITION workforce.agent_locations_trail_default")
    op.execute("DROP TABLE workforce.agent_locations_trail_default")
    for part in ("agent_locations_trail", "agent_locations_sos"):
        op.execute(f"ALTER TABLE workforce.agent_locations DETACH PARTITION workforce.{part}")
        op.execute(f"DROP TABLE workforce.{part}")
    for schema, table, _ in reversed(RANGE_PARENTS):
        for tag, _, _ in reversed(list(_months())):
            op.execute(f"ALTER TABLE {schema}.{table} DETACH PARTITION {schema}.{table}_p{tag}")
            op.execute(f"DROP TABLE {schema}.{table}_p{tag}")
        op.execute(f"ALTER TABLE {schema}.{table} DETACH PARTITION {schema}.{table}_default")
        op.execute(f"DROP TABLE {schema}.{table}_default")