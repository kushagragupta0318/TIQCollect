# ─── CHANGELOG (prototype → product) ───
# New file, 2026-07-21. Foundation for final_changes.md §8's "typed error
# codes, not free text" convention. Deliberately additive, not a breaking
# change: AppException still sets HTTPException.detail to the plain message
# string, so every existing frontend call site that reads
# err.response.data.detail keeps working unmodified. `code` rides alongside
# as a new field for callers (and the future Bank Command Centre
# integration) that want to branch on a stable identifier instead of
# parsing message text. See changelog.md for the full rationale.
from enum import Enum

from fastapi import HTTPException


class ErrorCode(str, Enum):
    GEO_FENCE_VIOLATION = "GEO_FENCE_VIOLATION"
    OUTSIDE_CONTACT_HOURS = "OUTSIDE_CONTACT_HOURS"
    CASE_ALREADY_ASSIGNED = "CASE_ALREADY_ASSIGNED"
    CASE_NOT_FOUND = "CASE_NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    PAYMENT_ALREADY_PROCESSED = "PAYMENT_ALREADY_PROCESSED"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    # 2026-09-24 — a dependency (Redis, for the live event stream) is down and
    # the client has a documented fallback. Distinct from INTERNAL_ERROR so the
    # frontend can switch to polling instead of showing a failure.
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    # P1 A06-A08 (d4) — accounts. A token that is unknown, expired, used or
    # revoked is one code, so the answer reveals nothing about which.
    INVITE_INVALID = "INVITE_INVALID"
    INVITE_NOT_ALLOWED = "INVITE_NOT_ALLOWED"
    CHANNEL_UNAVAILABLE = "CHANNEL_UNAVAILABLE"
    PASSWORD_POLICY = "PASSWORD_POLICY"
    PASSWORD_INCORRECT = "PASSWORD_INCORRECT"
    RESET_INVALID = "RESET_INVALID"
    MFA_REQUIRED = "MFA_REQUIRED"
    MFA_INVALID = "MFA_INVALID"
    MFA_NOT_ALLOWED = "MFA_NOT_ALLOWED"
    MFA_NOT_CONFIGURED = "MFA_NOT_CONFIGURED"
    MFA_ENROLLMENT_REQUIRED = "MFA_ENROLLMENT_REQUIRED"
    SIGN_IN_REQUIRED = "SIGN_IN_REQUIRED"      # quick-login / refresh refused: use /auth/login
    # 2026-09-24 (hotfix PAY-1) — a UPI collection without its transaction
    # reference (UTR). The only evidence a UPI payment happened is that
    # reference; the demo QR used to waive it after a 10-second timer.
    UPI_REFERENCE_REQUIRED = "UPI_REFERENCE_REQUIRED"
    # NEFT / RTGS / DD without a bank reference, CHEQUE without its number.
    PAYMENT_REFERENCE_REQUIRED = "PAYMENT_REFERENCE_REQUIRED"
    # ML-1: a borrower's stance sent on a contact that did not reach the
    # borrower (services/borrower_stance.py).
    DISPOSITION_WITHOUT_BORROWER = "DISPOSITION_WITHOUT_BORROWER"
    # I02 offline outbox: a replayed item's capture time is refused
    # (services/capture_time.py, ADR 0011 §4). Permanent: the client stops retrying.
    CAPTURE_IN_FUTURE = "CAPTURE_IN_FUTURE"
    CAPTURE_TOO_OLD = "CAPTURE_TOO_OLD"
    CAPTURE_DEVICE_MISMATCH = "CAPTURE_DEVICE_MISMATCH"
    CAPTURE_OUT_OF_ORDER = "CAPTURE_OUT_OF_ORDER"


class AppException(HTTPException):
    """Raise instead of HTTPException when a stable error code should
    accompany the message. Response body stays {"detail": <message>} for
    backward compatibility; `code` is added alongside by the exception
    handler in main.py."""

    def __init__(self, status_code: int, code: ErrorCode, message: str):
        self.code = code
        super().__init__(status_code=status_code, detail=message)
