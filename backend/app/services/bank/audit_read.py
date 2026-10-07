"""The bank's audit trail: its own actions and its agencies', read once.

The manager side has had a real audit endpoint since 2026-09-06
(manager.py's /audit-log), and it scopes by ACTOR — the user ids of a
manager's team. A bank cannot use that shape: it wants everything that
happened inside its tenancy, including rows written by agency staff it does
not employ. So the scope here is the row's own `bank_id`, which
models/tenancy_listener fills from the actor on every row that has one, and
which v2_0026 backfilled for the rows written before that listener existed.

ONE definition of the scope, shared by the list and the CSV, for the reason
manager.py records: /manager/allocation/export-decisions was once exportable
across tenants because the endpoint named the scope and the service it called
quietly did not.

WHAT THIS CANNOT SEE, stated rather than discovered: a row whose `bank_id` is
NULL. core/audit.py writes one whenever the action has no actor and the caller
did not pass the entity's tenant — known issue 6. Those rows are real events
and they belong to somebody; they simply cannot be proven to belong to THIS
bank, so including them would be a tenancy leak and dropping them silently
would make a thin log look like a quiet week. `pending_attribution` counts
them instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditLog
from app.models.user import User

WINDOW_DAYS = 7
PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

#: The actions a bank's compliance reader comes to this page FOR. Not a
#: filter — everything in the bank's tenancy is listed — but the UI leads with
#: these, and `coverage.sensitive_actions` tells it which without hardcoding
#: the list in the page.
#:
#: Payment reversal is deliberately ABSENT: PAYMENT_REVERSAL_REQUESTED and
#: PAYMENT_REVERSED do not exist on AuditAction yet, they arrive with the
#: reversal lane's v2_0028, and naming a member that is not there would be an
#: AttributeError at import. Add them when that lands.
SENSITIVE_ACTIONS: tuple[str, ...] = (
    "PLACEMENT_CREATED", "PLACEMENT_RECALLED", "PLACEMENT_ENDED",
    "MODEL_CANDIDATE_APPROVED", "MODEL_CANDIDATE_REJECTED", "MODEL_PROMOTED",
    "LOGIN_FAILED", "ROLE_VIOLATION_ATTEMPT", "DEVICE_MISMATCH", "SESSION_REVOKED",
    "DATA_EXPORT",
    "AGENCY_ONBOARDED", "AGENCY_ACTIVATED", "AGENCY_SUSPENDED", "AGENCY_OFFBOARDED",
    "CONTRACT_CHANGED", "USER_DEACTIVATED", "PASSWORD_RESET", "MFA_DISABLED",
)


@dataclass(frozen=True)
class Filters:
    action: str | None = None
    actor_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None

    def window_start(self) -> datetime:
        return self.since or (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS))


def scoped_query(db: Session, bank_id: str, f: Filters):
    """Every row of this bank's tenancy in the window, newest first.

    `bank_id` covers the bank's own rows AND its agencies' — an agency row
    carries both ids, so one predicate serves both and no agency list is
    needed. A row of another bank cannot match, and a row of no bank cannot
    either; see the module docstring on the second.
    """
    q = db.query(AuditLog).filter(AuditLog.bank_id == bank_id,
                                  AuditLog.created_at >= f.window_start())
    if f.until is not None:
        q = q.filter(AuditLog.created_at <= f.until)
    if f.action:
        q = q.filter(AuditLog.action == f.action)
    if f.actor_id:
        q = q.filter(AuditLog.user_id == f.actor_id)
    return q.order_by(AuditLog.created_at.desc())


def pending_attribution(db: Session, f: Filters) -> int:
    """Rows in the window that belong to NO bank, so no bank can read them.

    Counted, never listed: the count is the honest statement that the trail is
    incomplete, and listing them would hand one bank another's events.
    """
    q = db.query(func.count(AuditLog.id)).filter(AuditLog.bank_id.is_(None),
                                                 AuditLog.created_at >= f.window_start())
    if f.until is not None:
        q = q.filter(AuditLog.created_at <= f.until)
    return int(q.scalar() or 0)


def actor_names(db: Session, rows) -> dict[str, str]:
    """One query, not one per row."""
    ids = {r.user_id for r in rows if r.user_id}
    if not ids:
        return {}
    return {uid: name for uid, name in db.query(User.id, User.full_name).filter(User.id.in_(ids))}


def counts_by_action(db: Session, bank_id: str, f: Filters) -> dict[str, int]:
    """What actually appears in the window, so the page can tell "quiet" from
    "not instrumented" without hardcoding either."""
    rows = (db.query(AuditLog.action, func.count(AuditLog.id))
            .filter(AuditLog.bank_id == bank_id, AuditLog.created_at >= f.window_start())
            .group_by(AuditLog.action).all())
    return {(a.value if hasattr(a, "value") else str(a)): n for a, n in rows}


def row_out(r: AuditLog, names: dict[str, str]) -> dict:
    return {
        "id": r.id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "action": r.action.value if hasattr(r.action, "value") else str(r.action),
        "actor_name": names.get(r.user_id) if r.user_id else None,
        "actor_id": r.user_id,
        "agency_id": r.agency_id,
        "entity_type": r.entity_type,
        "entity_id": r.entity_id,
        "success": r.success,
        "failure_reason": r.failure_reason,
        "ip_address": r.ip_address,
    }


def coverage(db: Session, f: Filters) -> dict:
    """The honesty block. Without it a short log reads as a quiet week."""
    unattributed = pending_attribution(db, f)
    return {
        "declared_action_types": len(AuditAction),
        "sensitive_actions": list(SENSITIVE_ACTIONS),
        "pending_attribution": unattributed,
        "note": (
            "Rows written with no actor and no entity tenant carry no bank and cannot be "
            "attributed to one, so they are counted here rather than listed "
            f"({unattributed} in this window). Immutability is enforced by convention only: "
            "there is no database trigger and no revoked UPDATE/DELETE grant."
        ),
    }
