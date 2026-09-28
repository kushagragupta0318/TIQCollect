# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-28 (B12, docs/DATA-MODEL-V2.md §7.3) — NEW. The ONE definition of
#   partition policy, and the operations the daily maintenance task runs.
#
#   v2_0003 created monthly partitions 2026-03 .. 2026-12 and a DEFAULT under
#   each partitioned parent. Without this, the first row dated 2027-01 lands in
#   DEFAULT, and a month partition can never again be created for a range
#   DEFAULT already holds rows in. So every day this pre-creates the current
#   month and the next PREMAKE months, and reports any row in a DEFAULT at
#   ERROR (never silently).
#
#   RETENTION is applied only where the policy is DECIDED: the location trail
#   (LOCATION_RETENTION_DAYS, 90). allocation_decisions (24 months) and
#   model_predictions (>= 36 months) are PROPOSED in the design (open question
#   Q8) and are `retention_days=None` until the owner decides — no data is
#   dropped on a proposal. audit_logs retention needs the archive-then-drop
#   path (§7.4), which does not exist, so its partitions are REFUSED here
#   outright, whatever the registry says.
#
#   B11 MED 8: a partition of a table other tables reference (model_predictions:
#   allocation_decisions, placement_decisions, placements, settlement_offers,
#   each by (model_prediction_id, model_prediction_as_of)) cannot be detached
#   while referencing rows exist. detach_and_drop() nulls those references for
#   the partition's key range first. The referencing FKs are read from the
#   model metadata — never a hand-kept list — so a fifth referencer is covered
#   the day it is declared.
#
#   Plain DETACH, not DETACH ... CONCURRENTLY: Postgres refuses CONCURRENTLY on
#   a parent that has a DEFAULT partition, and every parent here has one.
# ────────────────────────────────────────────────────────────────────────────
"""Partition policy (the registry) and the maintenance operations."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

PREMAKE_MONTHS = 3


@dataclass(frozen=True)
class Policy:
    parent: str                 # schema.table of the RANGE-partitioned parent
    key: str                    # the partition key column
    kind: str                   # "date" | "timestamptz"
    retention_days: int | None  # None = never detached (policy undecided, or forever)


def _location_retention_days() -> int:
    from app.core.config import settings
    return settings.LOCATION_RETENTION_DAYS


# The registry. Names and bounds match v2_0003 (<parent>_pYYYYMM, DEFAULT
# <parent>_default). agent_locations is LIST(is_sos) first; only its trail
# (is_sos = false) is range-partitioned, and the SOS partition is never touched.
PARTITIONED: dict[str, Policy] = {
    "workforce.agent_locations_trail": Policy("workforce.agent_locations_trail", "recorded_at", "timestamptz",
                                              retention_days=-1),   # -1: read LOCATION_RETENTION_DAYS at run time
    "planning.allocation_decisions": Policy("planning.allocation_decisions", "plan_date", "date", None),   # Q8: 24 months proposed
    "ml.model_predictions": Policy("ml.model_predictions", "as_of_date", "date", None),                   # Q8: >= 36 months proposed
    "audit.audit_logs": Policy("audit.audit_logs", "created_at", "timestamptz", None),                    # archive path (§7.4) not built
    "lending.loan_dpd_history": Policy("lending.loan_dpd_history", "as_of_date", "date", None),           # month-ends kept indefinitely
}
# Detached before the tables they reference (§7.3 step 3).
RETENTION_ORDER = ("planning.allocation_decisions", "workforce.agent_locations_trail",
                   "ml.model_predictions", "lending.loan_dpd_history", "audit.audit_logs")
# Never detached or dropped by this module, whatever its policy says.
NEVER_DROP = frozenset({"audit.audit_logs"})


class PartitionError(Exception):
    pass


def retention_days(policy: Policy) -> int | None:
    return _location_retention_days() if policy.retention_days == -1 else policy.retention_days


# ── pure planning ───────────────────────────────────────────────────────────
def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def partition_name(parent: str, month: date) -> str:
    return f"{parent}_p{month:%Y%m}"


def months_to_have(today: date, premake: int = PREMAKE_MONTHS) -> list[date]:
    """The current month and the next `premake`."""
    first = month_start(today)
    return [add_months(first, i) for i in range(premake + 1)]


def expired_months(existing: list[date], today: date, days: int | None) -> list[date]:
    """Month partitions whose WHOLE range is older than `days`: a month goes
    when its upper bound is on or before today - days. So effective retention
    is `days` to `days + 31` (design §7.1, Q8)."""
    if days is None:
        return []
    cutoff = today - timedelta(days=days)
    return sorted(m for m in existing if add_months(m, 1) <= cutoff)


def bound(d: date, kind: str) -> str:
    return f"'{d:%Y-%m-%d} 00:00:00+00'" if kind == "timestamptz" else f"'{d:%Y-%m-%d}'"


_MONTH_RE = re.compile(r"_p(\d{4})(\d{2})$")


def existing_months(conn, parent: str) -> list[date]:
    schema, table = parent.split(".")
    rows = conn.execute(text(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "JOIN pg_class p ON p.oid = i.inhparent JOIN pg_namespace n ON n.oid = p.relnamespace "
        "WHERE n.nspname = :s AND p.relname = :t"), {"s": schema, "t": table}).scalars()
    out = []
    for name in rows:
        m = _MONTH_RE.search(name)
        if m:
            out.append(date(int(m.group(1)), int(m.group(2)), 1))
    return sorted(out)


# ── operations (Postgres) ───────────────────────────────────────────────────
def ensure_months(conn, policy: Policy, today: date) -> list[str]:
    """CREATE the missing month partitions for today's month and PREMAKE ahead."""
    have = set(existing_months(conn, policy.parent))
    created = []
    for m in months_to_have(today):
        if m in have:
            continue
        name = partition_name(policy.parent, m)
        conn.execute(text(
            f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {policy.parent} "
            f"FOR VALUES FROM ({bound(m, policy.kind)}) TO ({bound(add_months(m, 1), policy.kind)})"))
        created.append(name)
    return created


def default_rows(conn, policy: Policy) -> int:
    return conn.execute(text(f"SELECT count(*) FROM {policy.parent}_default")).scalar_one()


def referencing_columns(parent: str) -> list[tuple[str, str, str]]:
    """(referencing table, id column, key column) for every composite FK into
    `parent`, read from the model metadata (the one definition)."""
    import app.models  # noqa: F401
    from app.core.database import Base
    schema, table = parent.split(".")
    out = []
    for t in Base.metadata.sorted_tables:
        for fk in t.foreign_key_constraints:
            ref = fk.elements[0].column.table
            if ref.schema == schema and ref.name == table and len(fk.elements) == 2:
                cols = [e.parent.name for e in fk.elements]
                out.append((f"{t.schema}.{t.name}", cols[0], cols[1]))
    return sorted(set(out))


def detach_and_drop(conn, policy: Policy, month: date) -> dict:
    """Null the references into this month's range (MED 8), then DETACH and
    DROP the partition. Refuses the NEVER_DROP parents outright."""
    if policy.parent in NEVER_DROP:
        raise PartitionError(f"{policy.parent}: partitions are never dropped here "
                             "(audit retention needs the archive-then-drop path, design §7.4)")
    lo, hi = month, add_months(month, 1)
    nulled = {}
    for ref_table, id_col, key_col in referencing_columns(policy.parent):
        n = conn.execute(text(
            f"UPDATE {ref_table} SET {id_col} = NULL, {key_col} = NULL "
            f"WHERE {key_col} >= :lo AND {key_col} < :hi"), {"lo": lo, "hi": hi}).rowcount
        if n:
            nulled[ref_table] = n
    name = partition_name(policy.parent, month)
    conn.execute(text(f"ALTER TABLE {policy.parent} DETACH PARTITION {name}"))
    conn.execute(text(f"DROP TABLE {name}"))
    return {"dropped": name, "references_nulled": nulled}


def maintain(engine, today: date | None = None) -> dict:
    """One maintenance pass, every table in the registry. Each table's work
    is its own transaction; any failure raises, so the beat alert fires."""
    today = today or datetime.now(timezone.utc).date()
    result = {"created": [], "dropped": [], "references_nulled": {}, "default_rows": {}}
    for parent in RETENTION_ORDER:
        policy = PARTITIONED[parent]
        with engine.begin() as conn:
            result["created"] += ensure_months(conn, policy, today)
            n = default_rows(conn, policy)
            if n:
                result["default_rows"][parent] = n
        days = retention_days(policy)
        if days is None or parent in NEVER_DROP:
            continue
        with engine.connect() as conn:
            months = expired_months(existing_months(conn, parent), today, days)
        for m in months:
            with engine.begin() as conn:
                r = detach_and_drop(conn, policy, m)
            result["dropped"].append(r["dropped"])
            for k, v in r["references_nulled"].items():
                result["references_nulled"][k] = result["references_nulled"].get(k, 0) + v
    return result
