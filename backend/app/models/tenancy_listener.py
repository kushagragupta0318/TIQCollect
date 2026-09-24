# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B02/B06) — New file. THE one place the tenant-denormalisation
#   rule lives (docs/DATA-MODEL-V2.md §2.5).
#
#   Every agency-owned row carries bank_id + agency_id, deliberately copied
#   from its parent so indexes and row-level security need no join — and
#   composite foreign keys make a copy that disagrees with its parent
#   impossible in Postgres. This listener is what fills the copy, so the ~250
#   existing constructor sites (`Visit(case_id=…, agent_id=…)`, `Payment(…)`)
#   did not have to learn about tenancy.
#
#   How: a model declares `__tenant_parents__ = ((fk_attr, "ParentClass"[,
#   mapping]), …)` in priority order. Before each flush, for every NEW object,
#   the first parent that can be found supplies whichever of bank_id,
#   agency_id, loan_id and plan_date the child has and left None (or the
#   explicit `mapping` {child_attr: parent_attr}). A parent still pending in
#   the same flush is filled first, so a whole graph added at once resolves.
#
#   Roots — Bank, Agency, Customer, Loan, User, Agent — must be given their
#   tenant explicitly in application code. The ONE exception is tests: a
#   session whose `info["default_tenant"]` is set (tests/_db.py does this)
#   fills a still-missing root tenant from it, so the ~100 test fixtures that
#   predate tenancy keep working. Production sessions never set it, and the
#   NOT NULL constraints then refuse the row.
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.base import Base

INHERITED_ATTRS: tuple[str, ...] = ("bank_id", "agency_id", "loan_id", "plan_date")

# Roles whose users carry both tenant ids / only the bank (ck_users_role_scope).
_AGENCY_SCOPED = {"FIELD_AGENT", "AGENCY_MANAGER", "AGENCY_ADMIN"}
_BANK_SCOPED = {"BANK_ADMIN", "BANK_ANALYST", "BANK_TECHOPS", "SERVICE"}


def _class_named(name: str):
    return Base.registry._class_registry.get(name)  # noqa: SLF001 — the documented lookup


def _pending_parent(session: Session, cls, pk: Any):
    for obj in session.new:
        if isinstance(obj, cls) and getattr(obj, "id", None) == pk:
            return obj
    return None


def _find_parent(session: Session, cls, pk: Any):
    parent = _pending_parent(session, cls, pk)
    if parent is not None:
        return parent
    try:
        return session.get(cls, pk)
    except Exception:  # noqa: BLE001 — a malformed id is the INSERT's problem to report
        return None


def _fill(session: Session, obj: Any, seen: set[int]) -> None:
    if id(obj) in seen:
        return
    seen.add(id(obj))
    for spec in getattr(type(obj), "__tenant_parents__", ()):
        fk_attr, cls_name = spec[0], spec[1]
        mapping = spec[2] if len(spec) > 2 else {a: a for a in INHERITED_ATTRS}
        missing = {c: p for c, p in mapping.items()
                   if hasattr(obj, c) and getattr(obj, c) is None}
        if not missing:
            continue
        pk = getattr(obj, fk_attr, None)
        cls = _class_named(cls_name)
        if pk is None or cls is None:
            continue
        parent = _find_parent(session, cls, pk)
        if parent is None:
            continue
        if parent in session.new:
            _fill(session, parent, seen)
        for child_attr, parent_attr in missing.items():
            value = getattr(parent, parent_attr, None)
            if value is not None:
                setattr(obj, child_attr, value)


def _apply_test_defaults(obj: Any, defaults: dict) -> None:
    """Tests only (session.info['default_tenant']): fill a root's tenant."""
    role = getattr(obj, "role", None)
    role_name = getattr(role, "value", role)
    for attr in ("bank_id", "agency_id"):
        if not hasattr(obj, attr) or getattr(obj, attr) is not None or not defaults.get(attr):
            continue
        if role_name is not None and type(obj).__name__ == "User":
            if role_name == "PLATFORM_ADMIN":
                continue
            if attr == "agency_id" and role_name in _BANK_SCOPED:
                continue
        col = inspect(type(obj)).columns.get(attr)
        # Only NOT NULL columns, plus User (whose CHECK decides by role).
        if col is not None and (not col.nullable or type(obj).__name__ == "User"):
            setattr(obj, attr, defaults[attr])


@event.listens_for(Session, "before_flush")
def fill_tenant_columns(session: Session, flush_context, instances) -> None:  # noqa: ARG001
    if not session.new:
        return
    defaults = session.info.get("default_tenant")
    seen: set[int] = set()
    with session.no_autoflush:
        for obj in list(session.new):
            _fill(session, obj, seen)
        if defaults:
            for obj in list(session.new):
                _apply_test_defaults(obj, defaults)
                # A child whose parent was only just defaulted.
                seen.discard(id(obj))
                _fill(session, obj, seen)
