"""One way to write an audit row that must never take its caller down with it.

2026-09-11. Until today every audit row was written inline —
`db.add(AuditLog(...))` followed by the caller's own commit — and that was
fine for the eight actions that existed, because each sat on a path whose
failure already meant the whole request failed. The actions being wired now
are different in kind: VISIT_RECORDED, PTP_SET and PAYMENT_SUBMITTED are
written AFTER the business write has committed, and a refused request writing
ROLE_VIOLATION_ATTEMPT is already on its way out with a 403. In none of those
may a failure to record the event change what happened to the event. That
contract is the same one the contact-hour violation row established in
visit_service, and it is lifted here so the next writer does not restate it.

Not used to retrofit the existing inline writers. They are correct as they
are, and rewriting working code to match a helper is churn.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditLog

logger = structlog.get_logger()


def write_audit(
    db: Session,
    *,
    action: AuditAction,
    user_id: str | None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict[str, Any] | None = None,
    success: bool = True,
    failure_reason: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    created_at: datetime | None = None,
) -> bool:
    """Add one AuditLog row and commit it on its own. Returns whether it landed.

    Commits itself because `get_db` never does, and a row left pending behind
    a caller that has already committed — or is about to raise — is discarded
    with the session. A failure is rolled back and logged at ERROR with the
    exception type and traceback, exactly as a swallowed notification failure
    is, and the caller carries on: the audit trail records what happened, it
    does not decide whether it happens.
    """
    try:
        db.add(_row(action=action, user_id=user_id, entity_type=entity_type, entity_id=entity_id,
                    details=details, success=success, failure_reason=failure_reason,
                    ip_address=ip_address, user_agent=user_agent, created_at=created_at))
        db.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — the event already happened; the row is evidence
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.error("audit.write_failed", action=str(getattr(action, "value", action)),
                     entity_type=entity_type, entity_id=entity_id,
                     error=str(exc), error_type=type(exc).__name__, exc_info=True)
        return False


def stage_audit(
    db: Session,
    *,
    action: AuditAction,
    user_id: str | None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict[str, Any] | None = None,
    success: bool = True,
    failure_reason: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Add one AuditLog row to the CALLER'S transaction; the caller commits.

    2026-09-28 (P1, d4; coordinator's audit MED). write_audit commits on its
    own, so a row written after a business commit is lost if the process
    dies between the two. Where the business write and its evidence belong
    together — an invite accepted, a password changed, a factor enrolled —
    the row is staged before the one commit and lands exactly when the event
    does. write_audit stays right for refusals, where there is no business
    write and the row must survive the raise that follows."""
    db.add(_row(action=action, user_id=user_id, entity_type=entity_type, entity_id=entity_id,
                details=details, success=success, failure_reason=failure_reason,
                ip_address=ip_address, user_agent=user_agent, created_at=None))


def _row(*, action, user_id, entity_type, entity_id, details, success, failure_reason,
         ip_address, user_agent, created_at) -> AuditLog:
    """The one construction of an AuditLog row, for both writers."""
    return AuditLog(
        id=str(uuid.uuid4()),
        created_at=created_at or datetime.now(timezone.utc),
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details=details,
        success=success,
        failure_reason=failure_reason,
    )
