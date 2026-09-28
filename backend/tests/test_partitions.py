"""B12: the partition policy registry and its planning (core/partitions.py).

The operations are Postgres DDL; their evidence (a maintenance pass and a
MED 8 detach on the transformed book) is in the B12 commit. These pin the
policy and the arithmetic.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core import partitions as P


def test_the_registry_covers_exactly_the_parents_v2_0003_partitioned():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "v2_0003_partitions.py"
    spec = importlib.util.spec_from_file_location("v2_0003", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    migrated = {f"{s}.{t}" for s, t, _ in mod.RANGE_PARENTS} | {"workforce.agent_locations_trail"}
    assert set(P.PARTITIONED) == migrated
    assert set(P.RETENTION_ORDER) == migrated
    for parent, kind in [(f"{s}.{t}", k) for s, t, k in mod.RANGE_PARENTS]:
        assert P.PARTITIONED[parent].kind == kind


def test_retention_is_applied_only_where_the_policy_is_decided():
    """Q8 is open: decisions and predictions keep everything until the owner
    decides; audit needs the archive path first. Only the trail has a policy."""
    decided = {p for p, pol in P.PARTITIONED.items() if P.retention_days(pol) is not None}
    assert decided == {"workforce.agent_locations_trail"}
    from app.core.config import settings
    assert P.retention_days(P.PARTITIONED["workforce.agent_locations_trail"]) == settings.LOCATION_RETENTION_DAYS


def test_audit_partitions_are_refused_whatever_the_policy_says():
    """§7.4: audit retention is archive-then-drop, and that path is not built.
    The refusal happens before any SQL is sent (no connection needed)."""
    pol = P.Policy("audit.audit_logs", "created_at", "timestamptz", retention_days=1)
    with pytest.raises(P.PartitionError):
        P.detach_and_drop(None, pol, date(2020, 1, 1))


def test_referencers_are_detached_before_what_they_reference():
    order = list(P.RETENTION_ORDER)
    assert order.index("planning.allocation_decisions") < order.index("ml.model_predictions")


def test_the_prediction_referencers_come_from_the_model_metadata():
    """MED 8: every composite FK into model_predictions is nulled before its
    partition is detached. Read from the metadata, so a new referencer is
    covered without editing a list."""
    refs = P.referencing_columns("ml.model_predictions")
    tables = {t for t, _, _ in refs}
    assert {"planning.allocation_decisions", "collections.placements"} <= tables
    for _, id_col, key_col in refs:
        assert id_col == "model_prediction_id" and key_col == "model_prediction_as_of"


@pytest.mark.parametrize("today,want", [
    (date(2026, 9, 28), [date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1)]),
    (date(2026, 11, 3), [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1)]),
])
def test_the_current_month_and_three_ahead_are_kept_ready(today, want):
    assert P.months_to_have(today) == want


def test_a_month_expires_only_when_all_of_it_is_older_than_the_retention():
    months = [date(2026, m, 1) for m in range(3, 10)]
    # 90 days before 2026-09-28 is 2026-06-30: March..May end on or before it; June ends 07-01, after.
    assert P.expired_months(months, date(2026, 9, 28), 90) == [date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1)]
    assert P.expired_months(months, date(2026, 9, 28), None) == []


def test_bounds_and_names_match_the_migration():
    assert P.partition_name("ml.model_predictions", date(2027, 1, 1)) == "ml.model_predictions_p202701"
    assert P.bound(date(2027, 1, 1), "timestamptz") == "'2027-01-01 00:00:00+00'"
    assert P.bound(date(2027, 1, 1), "date") == "'2027-01-01'"
    assert P.add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)
