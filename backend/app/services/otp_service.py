# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-30 — New file. Borrower OTP verification of field-collection amounts.
#   The anti-fraud control the money path was missing: a Payment used to be
#   written purely on the agent's word (default status PENDING_VERIFICATION,
#   nothing ever promoting it to VERIFIED). OtpService issues a 4-digit OTP to
#   the borrower's REGISTERED phone (Customer.phone_primary — never a number the
#   agent types), the borrower reads it back, and a correct code promotes the
#   Payment to VERIFIED and pushes the e-receipt.
#
#   OTP state is EPHEMERAL with a 5-minute TTL, never persisted to a DB table.
#   OTPs are short-lived secrets; Postgres is the wrong home for them. The store
#   is Redis WHEN IT IS ACTUALLY REACHABLE, otherwise an in-process TTL store
#   (_InProcessOtpStore) so OTP works with zero infrastructure in dev / a single
#   worker — the choice is made once per process (see _make_store). Either way,
#   TTL expiry + a single-use flag + a wrong-attempt counter + a resend throttle
#   are the protections a 4-digit code (10,000 combinations) actually needs. The
#   only things that persist are the legitimate audit trail (audit_logs
#   PAYMENT_VERIFIED) and the payment's own VERIFIED/verified_at columns.
# 2026-07-30 (later) — Added the in-process fallback above after the DB-table
#   design was dropped: a hard Redis dependency 500s when no Redis is running.
#   NOTE: the in-process store is single-process only (not shared across workers,
#   lost on restart) — fine for a 5-min code in dev; run Redis for multi-worker.
#
#   Two flows share one service:
#     • pre-collection (online): generate_and_send(payment_id=None) → verify →
#       PaymentService.collect_payment(verification_id) consumes the verified
#       Redis entry and writes the Payment straight as VERIFIED.
#     • deferred (offline): the Payment was already written PENDING_VERIFICATION
#       (no signal in the field); once the borrower has signal,
#       generate_and_send(payment_id=...) → verify flips that Payment to VERIFIED.
#   Parameters in config.py (4 digits / 5-min TTL / 3 attempts / resend throttle).
#   Full detail: /changelog.md and prototype_to_product/30.07.md
# ───────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from sqlalchemy.orm import joinedload

from app.core.config import settings
from app.core.errors import AppException, ErrorCode
from app.core.geo import is_within_contact_hours
from app.models.audit_log import AuditLog, AuditAction
from app.models.case import Case
from app.models.payment import Payment, PaymentStatus
from app.services.notification_service import NotificationService
import structlog

logger = structlog.get_logger()
from app.services.payment_service import PaymentService


class _InProcessOtpStore:
    """Ephemeral, in-RAM OTP store with TTL — no external service, no DB table.

    Implements just the handful of Redis commands OtpService uses, so it's a
    drop-in when Redis isn't running (dev / single-worker). Single-process only:
    OTP state is not shared across workers and is lost on restart — which is
    exactly right for a 5-minute code. For a multi-worker deployment, run Redis
    and this is bypassed automatically (see _make_store)."""

    def __init__(self):
        self._kv: dict[str, tuple[str, float | None]] = {}     # name -> (value, expires_at)
        self._h: dict[str, tuple[dict, float | None]] = {}     # name -> (fields, expires_at)
        self._lock = threading.Lock()

    @staticmethod
    def _alive(entry):
        if entry is None:
            return None
        _, exp = entry
        if exp is not None and exp < time.time():
            return None
        return entry

    def set(self, name, value, nx=False, ex=None):
        with self._lock:
            if nx and self._alive(self._kv.get(name)) is not None:
                return None
            self._kv[name] = (str(value), time.time() + ex if ex else None)
            return True

    def incr(self, name):
        with self._lock:
            entry = self._alive(self._kv.get(name))
            cur = int(entry[0]) if entry else 0
            exp = entry[1] if entry else None
            cur += 1
            self._kv[name] = (str(cur), exp)
            return cur

    def expire(self, name, ttl):
        with self._lock:
            for store in (self._kv, self._h):
                entry = self._alive(store.get(name))
                if entry is not None:
                    store[name] = (entry[0], time.time() + ttl)
            return True

    def hset(self, name, key=None, value=None, mapping=None):
        with self._lock:
            entry = self._alive(self._h.get(name))
            fields = dict(entry[0]) if entry else {}
            exp = entry[1] if entry else None
            if mapping:
                fields.update({k: str(v) for k, v in mapping.items()})
            if key is not None:
                fields[key] = str(value)
            self._h[name] = (fields, exp)
            return 1

    def hgetall(self, name):
        with self._lock:
            entry = self._alive(self._h.get(name))
            return dict(entry[0]) if entry else {}

    def hincrby(self, name, field, amount=1):
        with self._lock:
            entry = self._alive(self._h.get(name))
            fields = dict(entry[0]) if entry else {}
            exp = entry[1] if entry else None
            fields[field] = str(int(fields.get(field, "0")) + amount)
            self._h[name] = (fields, exp)
            return int(fields[field])

    def delete(self, *names):
        with self._lock:
            n = 0
            for nm in names:
                if self._kv.pop(nm, None) is not None or self._h.pop(nm, None) is not None:
                    n += 1
            return n


# Module-level lazy singleton so every OtpService in a process shares one store.
# Tests monkeypatch this to inject a fake — nothing here talks to a DB table.
_otp_store = None


def _make_store():
    """Prefer Redis if it's actually reachable; otherwise fall back to the
    in-process store so OTP works with zero infrastructure. The choice is made
    once per process at first use."""
    try:
        import redis
        client = redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=0.5)
        client.ping()
        return client
    except Exception:
        return _InProcessOtpStore()


def _default_store():
    global _otp_store
    if _otp_store is None:
        _otp_store = _make_store()
    return _otp_store


class OtpService:
    """Issue + verify borrower OTPs that authorise a collection amount.

    OTP state is EPHEMERAL — Redis when available, else an in-process TTL store.
    Never a DB table. No HTTP knowledge — callable from a router, a Celery task,
    or a WebSocket handler, same discipline as PaymentService/VisitService.
    """

    def __init__(self, db, store=None):
        self.db = db
        self._store = store

    @property
    def store(self):
        if self._store is None:
            self._store = _default_store()
        return self._store

    # -----------------------------------------------------------------
    # Redis key helpers
    # -----------------------------------------------------------------
    @staticmethod
    def _otp_key(otp_id: str) -> str:
        return f"otp:{otp_id}"

    @staticmethod
    def _throttle_key(case_id: str, amount: float) -> str:
        return f"otp:last:{case_id}:{amount}"

    @staticmethod
    def _count_key(case_id: str, amount: float) -> str:
        return f"otp:count:{case_id}:{amount}"

    @staticmethod
    def _mode_str(mode) -> str:
        return mode.value if hasattr(mode, "value") else str(mode)

    # -----------------------------------------------------------------
    # Crypto helpers
    # -----------------------------------------------------------------
    @staticmethod
    def _hash_code(code: str) -> str:
        """HMAC-SHA256(code) keyed by SECRET_KEY. A 4-digit code has only
        10,000 values, so a bare hash would be trivially reversible with a
        rainbow table — keying it with the server secret is what makes the
        stored hash useless to anyone who reads Redis."""
        return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _generate_code() -> str:
        """Uniform OTP_LENGTH-digit code via `secrets` (CSPRNG), zero-padded."""
        upper = 10 ** settings.OTP_LENGTH
        return str(secrets.randbelow(upper)).zfill(settings.OTP_LENGTH)

    @staticmethod
    def _masked(e164: str) -> str:
        return "XXXXXX" + e164[-4:]

    # -----------------------------------------------------------------
    # POST /agent/cases/{case_id}/payment/otp/send
    # -----------------------------------------------------------------
    def generate_and_send(self, agent, case_id: str, amount: float, mode=None, payment_id: str | None = None) -> dict:
        # `mode` is optional: the OTP is issued BEFORE the agent picks a payment
        # channel (UPI/RTGS/cash), so the code binds to the AMOUNT — the field
        # fraud turns on — not the mode. The deferred flow (payment_id set) pins
        # amount+mode from the existing payment below.
        case = (
            self.db.query(Case)
            .options(joinedload(Case.customer), joinedload(Case.loan))
            .filter(Case.id == case_id, Case.agent_id == agent.id)
            .first()
        )
        if not case:
            raise AppException(404, ErrorCode.CASE_NOT_FOUND, "Case not found or not assigned to you")

        customer = case.customer
        if not customer or not customer.phone_primary:
            raise AppException(400, ErrorCode.VALIDATION_ERROR, "Customer has no registered phone to send an OTP to")
        # Same Do-Not-Contact / contact-hours guards the visit path already enforces.
        if customer.do_not_contact:
            raise AppException(403, ErrorCode.FORBIDDEN, "Customer is marked Do Not Contact")

        now = datetime.now(timezone.utc)
        if not is_within_contact_hours(now):
            raise AppException(403, ErrorCode.OUTSIDE_CONTACT_HOURS, "Outside RBI-permitted contact hours")

        # Deferred flow: pin amount/mode to the existing payment row so a
        # bound OTP can only ever authorise that exact payment.
        if payment_id:
            payment = self.db.query(Payment).filter(Payment.id == payment_id, Payment.case_id == case.id).first()
            if not payment:
                raise AppException(404, ErrorCode.NOT_FOUND, "Payment not found for this case")
            if payment.status == PaymentStatus.VERIFIED:
                raise AppException(409, ErrorCode.PAYMENT_ALREADY_PROCESSED, "Payment is already verified")
            amount, mode = payment.amount, payment.mode
        else:
            # Pre-collection: the amount must fit the outstanding balance,
            # same check collect_payment applies before writing the row.
            remaining = round(case.target_amount - case.collected_amount, 2)
            if amount > remaining:
                raise AppException(
                    400, ErrorCode.VALIDATION_ERROR,
                    f"Amount ₹{amount:,.2f} exceeds remaining balance ₹{remaining:,.2f}",
                )

        mode_s = self._mode_str(mode) if mode is not None else ""
        self._enforce_send_limits(case.id, amount)

        code = self._generate_code()
        otp_id = str(uuid.uuid4())
        key = self._otp_key(otp_id)
        self.store.hset(key, mapping={
            "case_id": case.id,
            "agent_id": agent.id,
            "payment_id": payment_id or "",
            "amount": str(amount),
            "mode": mode_s,
            "code_hash": self._hash_code(code),
            "attempts": "0",
            "verified": "0",
        })
        self.store.expire(key, settings.OTP_TTL_SECONDS)

        e164 = "+" + NotificationService.normalize_phone(customer.phone_primary)
        ttl_min = max(1, settings.OTP_TTL_SECONDS // 60)
        sms_body = (
            f"ABC Bank: {code} is your OTP to confirm Rs.{amount:,.0f} collected against your loan. "
            f"Valid {ttl_min} min. Share it ONLY with the visiting agent to confirm YOUR own payment. "
            f"Never share otherwise. - ABC Bank"
        )
        # 2026-09-11 — the result was discarded, so this method answered 200
        # whether or not the borrower could ever receive the code, and the
        # agent's screen said "OTP sent" over a transport that had failed or was
        # never configured. The OTP itself is still issued and stored either
        # way: a borrower who is told the code by another route can still
        # confirm with it, the throttle and cap still apply, and nothing
        # about verification changes. What changes is that the caller is told.
        sms_sent = NotificationService.send_sms(e164, sms_body)
        if not sms_sent:
            # send_sms has already logged a real transport failure at ERROR.
            # This is the OTP-specific consequence, and it fires for the
            # unconfigured case too, where send_sms is silent by design.
            logger.warning("otp.sms_not_delivered", otp_id=otp_id, case_id=case.id,
                           masked_phone=self._masked(e164),
                           transport_configured=bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN))

        ret = {
            "otp_id": otp_id,
            "masked_phone": self._masked(e164),
            "expires_at": (now + timedelta(seconds=settings.OTP_TTL_SECONDS)).isoformat(),
            "resend_available_at": (now + timedelta(seconds=settings.OTP_RESEND_THROTTLE_SECONDS)).isoformat(),
            # Whether the code reached the SMS transport. Additive; every
            # existing field is unchanged. False when Twilio is unconfigured
            # (demo/dev, where demo_otp below carries the code instead).
            "sms_sent": bool(sms_sent),
        }
        if getattr(settings, "DEMO_MODE", False) or getattr(settings, "ENVIRONMENT", "") == "development":
            ret["demo_otp"] = code
        return ret

    def _enforce_send_limits(self, case_id: str, amount: float) -> None:
        """Resend throttle + active-send cap for the same collection. A 4-digit
        code demands both: the throttle blocks SMS-bombing / cost abuse, the cap
        stops an agent minting a fresh code every few seconds to brute the space.
        Both are Redis keys that self-expire, so counts reset on their own."""
        # SET NX EX — succeeds only if no send happened in the last throttle window.
        if not self.store.set(self._throttle_key(case_id, amount), "1", nx=True, ex=settings.OTP_RESEND_THROTTLE_SECONDS):
            raise AppException(429, ErrorCode.RATE_LIMITED, f"Please wait up to {settings.OTP_RESEND_THROTTLE_SECONDS}s before requesting another OTP")
        count_key = self._count_key(case_id, amount)
        count = self.store.incr(count_key)
        if count == 1:
            self.store.expire(count_key, settings.OTP_TTL_SECONDS)
        if count > settings.OTP_MAX_SENDS:
            raise AppException(429, ErrorCode.RATE_LIMITED, "Too many OTPs requested. Wait for the current one to expire.")

    # -----------------------------------------------------------------
    # POST /agent/cases/{case_id}/payment/otp/verify
    # -----------------------------------------------------------------
    def verify(self, agent, case_id: str, otp_id: str, code: str) -> dict:
        key = self._otp_key(otp_id)
        data = self.store.hgetall(key)
        # Missing key = never issued OR already expired (5-min TTL) OR burned.
        if not data or data.get("case_id") != case_id:
            raise AppException(400, ErrorCode.VALIDATION_ERROR, "OTP not found or expired. Please request a new one.")

        # Idempotent: a second verify of an already-verified pre-collection OTP
        # just re-confirms success rather than erroring.
        if data.get("verified") == "1":
            return {"verified": True, "otp_id": otp_id, "payment_id": data.get("payment_id") or None}

        max_attempts = settings.OTP_MAX_ATTEMPTS
        if int(data.get("attempts", "0")) >= max_attempts:
            self.store.delete(key)
            raise AppException(429, ErrorCode.RATE_LIMITED, "Too many wrong attempts. Please request a new OTP.")

        if not hmac.compare_digest(data["code_hash"], self._hash_code(code or "")):
            attempts = self.store.hincrby(key, "attempts", 1)
            left = max(0, max_attempts - attempts)
            if attempts >= max_attempts:
                self.store.delete(key)   # burn the code
            raise AppException(400, ErrorCode.VALIDATION_ERROR, f"Incorrect OTP. {left} attempt(s) left.")

        payment_id = data.get("payment_id") or None
        if payment_id:
            # Deferred flow: promote the already-created pending payment now, and
            # consume the OTP (single-use) — nothing left to collect afterwards.
            payment = self.db.query(Payment).filter(Payment.id == payment_id).first()
            if payment and payment.status != PaymentStatus.VERIFIED:
                payment.status = PaymentStatus.VERIFIED
                payment.verified_at = datetime.now(timezone.utc)
                self._audit_verified(agent, payment, deferred=True)
                self.db.commit()
                # 2026-09-24 — live event (core/events.py; never raises).
                from app.core.events import publish_event
                publish_event("payment.verified", agent=agent, data={
                    "payment_id": payment.id, "case_id": payment.case_id,
                    "amount": payment.amount,
                    "mode": str(getattr(payment.mode, "value", payment.mode)),
                    "status": "VERIFIED", "deferred": True,
                })
                self._send_receipt(agent, payment)
            self.store.delete(key)
        else:
            # Pre-collection flow: mark verified; collect_payment consumes it.
            self.store.hset(key, "verified", "1")

        return {"verified": True, "otp_id": otp_id, "payment_id": payment_id}

    # -----------------------------------------------------------------
    # Called by PaymentService.collect_payment (pre-collection online path)
    # -----------------------------------------------------------------
    def consume_for_payment(self, verification_id: str, case_id: str, amount: float) -> bool:
        """Confirm a verified OTP authorises THIS collection (matched on the
        amount — the OTP is issued before the mode is chosen), then consume it
        (single-use). Returns True on success so the caller writes the Payment
        as VERIFIED; raises otherwise."""
        key = self._otp_key(verification_id)
        data = self.store.hgetall(key)
        if not data or data.get("verified") != "1":
            raise AppException(400, ErrorCode.VALIDATION_ERROR, "Payment is not verified by a borrower OTP (or the OTP expired)")
        if data.get("case_id") != case_id:
            raise AppException(400, ErrorCode.VALIDATION_ERROR, "OTP does not belong to this case")
        if data.get("payment_id"):
            raise AppException(409, ErrorCode.PAYMENT_ALREADY_PROCESSED, "This OTP verification was already used for a payment")
        if abs(float(data.get("amount", "0")) - amount) > 0.01:
            raise AppException(400, ErrorCode.VALIDATION_ERROR, "Verified amount does not match this payment")
        self.store.delete(key)   # single-use
        return True

    # -----------------------------------------------------------------
    # Shared helpers
    # -----------------------------------------------------------------
    def _send_receipt(self, agent, payment: Payment) -> None:
        """Reuse the existing payment-received receipt (SMS + WhatsApp) so the
        borrower always gets the immutable e-receipt on verification."""
        case = (
            self.db.query(Case)
            .options(joinedload(Case.customer), joinedload(Case.loan))
            .filter(Case.id == payment.case_id)
            .first()
        )
        if not case:
            return
        req_like = SimpleNamespace(amount=payment.amount, mode=payment.mode)
        PaymentService(self.db)._notify_payment_received(agent, case, payment, req_like)

    def _audit_verified(self, agent, payment: Payment, deferred: bool) -> None:
        self.db.add(AuditLog(
            id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc),
            user_id=agent.user_id,
            action=AuditAction.PAYMENT_VERIFIED,
            entity_type="Payment",
            entity_id=payment.id,
            details={"amount": payment.amount, "mode": str(payment.mode), "channel": "OTP", "deferred": deferred},
            success=True,
        ))
