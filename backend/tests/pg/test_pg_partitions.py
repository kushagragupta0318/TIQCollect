"""B19: partition maintenance (B12) on a real Postgres, including B11 MED 8."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from app.core import partitions as P


def _row(table: sa.Table, **given):
    """A row with a value for every NOT NULL column that has no default."""
    out = dict(given)
    for c in table.columns:
        if c.name in out or c.nullable or c.server_default is not None or c.default is not None:
            continue
        t = c.type
        if isinstance(t, sa.Uuid):
            v = str(uuid.uuid4())
        elif isinstance(t, sa.Enum):
            v = t.enums[0]
        elif isinstance(t, sa.DateTime):
            v = datetime(2031, 1, 15, 12, tzinfo=timezone.utc)
        elif isinstance(t, sa.Date):
            v = date(2031, 1, 15)
        elif isinstance(t, sa.Boolean):
            v = False
        elif isinstance(t, (sa.Integer, sa.Numeric, sa.Float)):
            v = 1
        elif isinstance(t, sa.JSON):
            v = {}
        else:
            v = "x"
        out[c.name] = v
    return out


def _tables():
    import app.models  # noqa: F401
    from app.core.database import Base
    return Base.metadata.tables


def test_months_are_precreated_and_a_second_pass_does_nothing(pg_engine):
    first = P.maintain(pg_engine, today=date(2031, 1, 15))
    assert len(first["created"]) == 4 * len(P.PARTITIONED)        # 2031-01..04, every parent
    again = P.maintain(pg_engine, today=date(2031, 1, 15))
    assert again["created"] == [] and again["dropped"] == []


def test_a_row_in_a_default_partition_is_reported(pg_engine):
    """A key outside every month lands in DEFAULT and blocks that month's
    partition; the task must say so, never silently."""
    audit = _tables()["audit.audit_logs"]
    with pg_engine.begin() as conn:
        conn.execute(audit.insert().values(**_row(
            audit, created_at=datetime(2045, 6, 1, tzinfo=timezone.utc), action="LOGIN", success=True)))
    try:
        result = P.maintain(pg_engine, today=date(2031, 1, 15))
        assert result["default_rows"].get("audit.audit_logs") == 1
    finally:
        with pg_engine.begin() as conn:     # cleanup of this test's row in a throwaway database
            conn.execute(text("TRUNCATE audit.audit_logs_default"))


def test_med_8_a_referenced_prediction_partition_detaches_only_after_its_references_are_nulled(pg_engine):
    tables = _tables()
    preds, decisions = tables["ml.model_predictions"], tables["planning.allocation_decisions"]
    month = date(2031, 2, 1)
    P.maintain(pg_engine, today=date(2031, 1, 15))                  # 2031-02 exists
    pid = str(uuid.uuid4())
    name = P.partition_name("ml.model_predictions", month)
    with pg_engine.connect() as conn:
        with conn.begin():
            # The decision's other parents (run, case, agent, tenant) are not what
            # is under test; replica mode skips their FK triggers for these inserts.
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(preds.insert().values(**_row(preds, id=pid, as_of_date=date(2031, 2, 10))))
            conn.execute(decisions.insert().values(**_row(
                decisions, plan_date=date(2031, 2, 11), model_prediction_id=pid,
                model_prediction_as_of=date(2031, 2, 10))))
        with pytest.raises(sa.exc.IntegrityError):
            with conn.begin():
                conn.execute(text(f"ALTER TABLE ml.model_predictions DETACH PARTITION {name}"))
        with conn.begin():
            out = P.detach_and_drop(conn, P.PARTITIONED["ml.model_predictions"], month)
        assert out["dropped"] == name
        assert out["references_nulled"] == {"planning.allocation_decisions": 1}
        assert conn.execute(text("SELECT to_regclass(:n)"), {"n": name}).scalar() is None
        left = conn.execute(text(
            "SELECT count(*) FROM planning.allocation_decisions WHERE model_prediction_id IS NOT NULL")).scalar()
        conn.rollback()
    assert left == 0


def test_audit_partitions_are_never_detached(pg_engine):
    with pg_engine.connect() as conn:
        with pytest.raises(P.PartitionError):
            P.detach_and_drop(conn, P.PARTITIONED["audit.audit_logs"], date(2026, 3, 1))
        assert conn.execute(text("SELECT to_regclass('audit.audit_logs_p202603')")).scalar() is not None
