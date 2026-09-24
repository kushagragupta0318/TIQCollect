# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B02/B06) — New file. THE one place the tenant-denormalisation
#   rule lives (docs/DATA-MODEL-V2.md §2.5).
#
#   Every agency-owned row carries bank_id + agency_id, deliberately copied
#   from its parent so indexes and row-level security need no join — and
#   composite foreign keys make a copy that disagrees with its parent
#   impossible in Postgres. This listener fills the copy, so the ~250 existing
#   constructor sites (`Visit(case_id=…, agent_id=…)`, `Payment(…)`) did not
#   have to learn about tenancy — AND it refuses a copy that disagrees with
#   ANY declared parent, at flush, before the database has to.
#
#   How: a model declares `__tenant_parents__ = ((fk_attr, "ParentClass"[,
#   mapping]), …)` in priority order. For every NEW object:
#     - FILL: the first parent found supplies whichever of bank_id, agency_id,
#       loan_id and plan_date the child left None (or the explicit mapping
#       {child_attr: parent_attr});
#     - CHECK: every parent found must agree with every value the child now
#       holds — otherwise TenantMismatchError. This is the ORM-level form of
#       the cross-agency guarantee: a Visit whose case belongs to agency A and
#       whose agent belongs to agency B cannot be flushed.
#   A parent pending in the same flush is filled first, so a whole graph added
#   at once resolves.
#
#   Roots — Bank, Agency, Customer, Loan, User, Agent — must be given their
#   tenant explicitly in application code. The ONE exception is tests: a
#   session whose `info["default_tenant"]` is set (tests/_db.py) fills a
#   still-missing root tenant from it. Production sessions never set it, and
#   the NOT NULL constraints then refuse the row.
#
#   2026-09-24 (later) — performance, found by the coordinator measuring it:
#   the first version scanned session.new once per child and issued a SELECT
#   per parent, so flushing 8k new visits took 36.9 s against 1.0 s with ids
#   given explicitly. It now indexes session.new once per flush, reads the
#   identity map, and loads what is left with ONE IN query per parent class.
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.base import Base

INHERITED_ATTRS: tuple[str, ...] = ("bank_id", "agency_id", "loan_id", "plan_date")
_DEFAULT_MAPPING = {a: a for a in INHERITED_ATTRS}

# Roles whose users carry both tenant ids / only the bank (ck_users_role_scope).
_BANK_SCOPED = {"BANK_ADMIN", "BANK_ANALYST", "BANK_TECHOPS", "SERVICE"}
_IN_CHUNK = 5000


class TenantMismatchError(ValueError):
    """A row's tenant (or other inherited value) disagrees with its parent's."""


def _class_named(name: str):
    return Base.registry._class_registry.get(name)  # noqa: SLF001 — the documented lookup


def _specs(obj: Any):
    for spec in getattr(type(obj), "__tenant_parents__", ()):
        mapping = spec[2] if len(spec) > 2 else _DEFAULT_MAPPING
        yield spec[0], spec[1], mapping


class _Resolver:
    """Parent lookup for one flush: pending objects, then the identity map,
    then one batched query per parent class."""

    def __init__(self, session: Session, new_objects: list[Any]):
        self.session = session
        self.pending: dict[tuple[type, Any], Any] = {}
        for o in new_objects:
            pk = getattr(o, "id", None)
            if pk is not None:
                self.pending[(type(o), pk)] = o
        self.loaded: dict[tuple[type, Any], Any] = {}
        need: dict[type, set] = defaultdict(set)
        for o in new_objects:
            for fk_attr, cls_name, _ in _specs(o):
                pk = getattr(o, fk_attr, None)
                cls = _class_named(cls_name)
                if pk is None or cls is None or (cls, pk) in self.pending:
                    continue
                need[cls].add(pk)
        for cls, pks in need.items():
            missing = []
            for pk in pks:
                try:
                    hit = session.identity_map.get(session.identity_key(cls, pk))
                except Exception:  # noqa: BLE001 — malformed id: the INSERT will report it
                    hit = None
                if hit is not None:
                    self.loaded[(cls, pk)] = hit
                else:
                    missing.append(pk)
            for i in range(0, len(missing), _IN_CHUNK):
                chunk = missing[i:i + _IN_CHUNK]
                try:
                    for row in session.query(cls).filter(cls.id.in_(chunk)).all():
                        self.loaded[(cls, row.id)] = row
                except Exception:  # noqa: BLE001 — a malformed id among them
                    for pk in chunk:
                        try:
                            row = session.get(cls, pk)
                        except Exception:  # noqa: BLE001
                            row = None
                        if row is not None:
                            self.loaded[(cls, pk)] = row

    def parent(self, cls, pk):
        return self.pending.get((cls, pk)) or self.loaded.get((cls, pk))


def _fill(r: _Resolver, obj: Any, seen: set[int]) -> None:
    if id(obj) in seen:
        return
    seen.add(id(obj))
    found = []
    for fk_attr, cls_name, mapping in _specs(obj):
        pk = getattr(obj, fk_attr, None)
        cls = _class_named(cls_name)
        if pk is None or cls is None:
            continue
        parent = r.parent(cls, pk)
        if parent is None:
            continue
        if parent in r.session.new:
            _fill(r, parent, seen)
        found.append((fk_attr, parent, mapping))
        for child_attr, parent_attr in mapping.items():
            if hasattr(obj, child_attr) and getattr(obj, child_attr) is None:
                value = getattr(parent, parent_attr, None)
                if value is not None:
                    setattr(obj, child_attr, value)
    # CHECK against every parent found, not just the one that filled.
    for fk_attr, parent, mapping in found:
        for child_attr, parent_attr in mapping.items():
            mine = getattr(obj, child_attr, None)
            theirs = getattr(parent, parent_attr, None)
            if mine is not None and theirs is not None and mine != theirs:
                raise TenantMismatchError(
                    f"{type(obj).__name__}.{child_attr}={mine!r} disagrees with "
                    f"{type(parent).__name__}.{parent_attr}={theirs!r} (via {fk_attr})"
                )


def _apply_test_defaults(obj: Any, defaults: dict) -> None:
    """Tests only (session.info['default_tenant']): fill a root's tenant."""
    role = getattr(obj, "role", None)
    role_name = getattr(role, "value", role)
    is_user = type(obj).__name__ == "User"
    for attr in ("bank_id", "agency_id"):
        if not hasattr(obj, attr) or getattr(obj, attr) is not None or not defaults.get(attr):
            continue
        if is_user:
            if role_name == "PLATFORM_ADMIN":
                continue
            if attr == "agency_id" and role_name in _BANK_SCOPED:
                continue
        col = inspect(type(obj)).columns.get(attr)
        # Only NOT NULL columns, plus User (whose CHECK decides by role).
        if col is not None and (not col.nullable or is_user):
            setattr(obj, attr, defaults[attr])


@event.listens_for(Session, "before_flush")
def fill_tenant_columns(session: Session, flush_context, instances) -> None:  # noqa: ARG001
    new = list(session.new)
    if not new:
        return
    defaults = session.info.get("default_tenant")
    with session.no_autoflush:
        resolver = _Resolver(session, new)
        if defaults:
            # Roots first, so children inherit the defaulted tenant.
            for obj in new:
                if not getattr(type(obj), "__tenant_parents__", None) or type(obj).__name__ in ("Agent", "User"):
                    _apply_test_defaults(obj, defaults)
        seen: set[int] = set()
        for obj in new:
            _fill(resolver, obj, seen)
        if defaults:
            # Children whose parents could not supply a value (e.g. a case with
            # no placement takes the default agency).
            for obj in new:
                _apply_test_defaults(obj, defaults)
