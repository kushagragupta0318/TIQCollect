# ─── CHANGELOG (prototype → product) ───
# New file, 2026-07-30. Covers OtpService (borrower payment-verification OTP).
# OTP state is EPHEMERAL — held in Redis, never in a DB table — so these tests
# inject a tiny in-process fake Redis (via the monkeypatched _default_redis
# singleton) and a real in-memory SQLite session for the Case/Payment rows the
# deferred/collect paths touch. Also carries the DB-backed collect_payment
# integration (VERIFIED vs offline PENDING paths) here rather than in
# test_payment_service.py, whose header pins it to no-DB stand-ins.
# See prototype_to_product/30.07.md and /changelog.md.
import hmac
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — registers every model on Base.metadata
from app.core.config import settings
from app.core.database import Base
from app.core.errors import AppException
from app.models.customer import Customer
from app.models.case import Case, CaseStatus
from app.models.payment import Payment, PaymentMode, PaymentStatus
import app.services.otp_service as otp_module
from app.services.otp_service import OtpService
from app.services.payment_service import PaymentService


# ── Minimal in-process fake Redis (only the commands OtpService uses) ─────────
class FakeRedis:
    def __init__(self):
        self.kv = {}    # string keys
        self.h = {}     # hash keys -> dict[str, str]

    def set(self, name, value, nx=False, ex=None):
        if nx and name in self.kv:
            return None
        self.kv[name] = str(value)
        return True

    def incr(self, name):
        self.kv[name] = str(int(self.kv.get(name, "0")) + 1)
        return int(self.kv[name])

    def expire(self, name, ttl):
        return True

    def hset(self, name, key=None, value=None, mapping=None):
        d = self.h.setdefault(name, {})
        if mapping:
            d.update({k: str(v) for k, v in mapping.items()})
        if key is not None:
            d[key] = str(value)
        return 1

    def hgetall(self, name):
        return dict(self.h.get(name, {}))

    def hincrby(self, name, field, amount=1):
        d = self.h.setdefault(name, {})
        d[field] = str(int(d.get(field, "0")) + amount)
        return int(d[field])

    def delete(self, *names):
        n = 0
        for nm in names:
            if self.h.pop(nm, None) is not None or self.kv.pop(nm, None) is not None:
                n += 1
        return n


# ── Pure helpers (no Redis, no DB) ────────────────────────────────────────────

def test_hash_code_is_deterministic_and_keyed():
    h1 = OtpService._hash_code("1234")
    assert h1 == OtpService._hash_code("1234")
    assert h1 != OtpService._hash_code("1235")
    expected = hmac.new(settings.SECRET_KEY.encode(), b"1234", __import__("hashlib").sha256).hexdigest()
    assert h1 == expected


def test_generate_code_length_and_range():
    for _ in range(200):
        code = OtpService._generate_code()
        assert len(code) == settings.OTP_LENGTH and code.isdigit()
        assert 0 <= int(code) < 10 ** settings.OTP_LENGTH


def test_masked_phone_shows_last_four():
    assert OtpService._masked("+919876543210") == "XXXXXX3210"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def fake_redis(monkeypatch):
    fake = FakeRedis()
    # Every OtpService (including ones collect_payment creates internally) shares this.
    monkeypatch.setattr(otp_module, "_default_store", lambda: fake)
    monkeypatch.setattr(otp_module, "_otp_store", None, raising=False)
    return fake


@pytest.fixture(autouse=True)
def _force_contact_hours(monkeypatch):
    monkeypatch.setattr("app.services.otp_service.is_within_contact_hours", lambda now=None: True)


def _agent():
    return SimpleNamespace(
        id="agent-1", user_id="user-1", current_month_collections=0.0,
        user=SimpleNamespace(full_name="Test Agent"),
    )


def _seed_case(db, *, target=10000.0, collected=0.0, do_not_contact=False):
    db.add_all([
        Customer(
            id="cust-1", customer_ref="CUST-1", full_name="Ravi Kumar",
            date_of_birth="1990-01-01", gender="M", pan_masked="XXXXX1234X",
            aadhaar_masked="XXXXXXXX5678", phone_primary="9876543210",
            address_line1="1 MG Road", city="Pune", state="MH", pincode="411001",
            latitude=18.5, longitude=73.8, do_not_contact=do_not_contact,
        ),
        Case(
            id="case-1", case_number="CASE-1", customer_id="cust-1", loan_id="loan-1",
            agent_id="agent-1", target_amount=target, collected_amount=collected,
            status=CaseStatus.ASSIGNED,
        ),
    ])
    db.commit()


def _put_otp(fake, otp_id, *, code="1234", amount=5000.0, mode=PaymentMode.CASH,
             case_id="case-1", payment_id="", attempts=0, verified=False):
    fake.hset(OtpService._otp_key(otp_id), mapping={
        "case_id": case_id, "agent_id": "agent-1", "payment_id": payment_id,
        "amount": str(amount), "mode": OtpService._mode_str(mode),
        "code_hash": OtpService._hash_code(code), "attempts": str(attempts),
        "verified": "1" if verified else "0",
    })


# ── generate_and_send ─────────────────────────────────────────────────────────

def test_generate_and_send_happy(db, fake_redis, monkeypatch):
    _seed_case(db)
    monkeypatch.setattr(OtpService, "_generate_code", staticmethod(lambda: "4321"))
    # No mode passed — OTP is issued before the payment channel is chosen.
    res = OtpService(db).generate_and_send(_agent(), "case-1", 5000.0)
    assert res["masked_phone"].endswith("3210")
    stored = fake_redis.hgetall(OtpService._otp_key(res["otp_id"]))
    assert stored["code_hash"] == OtpService._hash_code("4321")
    assert stored["amount"] == "5000.0" and stored["mode"] == ""
    assert stored["verified"] == "0" and stored["attempts"] == "0"


def test_generate_and_send_blocks_do_not_contact(db, fake_redis):
    _seed_case(db, do_not_contact=True)
    with pytest.raises(AppException) as e:
        OtpService(db).generate_and_send(_agent(), "case-1", 5000.0, PaymentMode.CASH)
    assert e.value.status_code == 403


def test_generate_and_send_rejects_amount_over_balance(db, fake_redis):
    _seed_case(db, target=10000.0, collected=8000.0)   # 2000 remaining
    with pytest.raises(AppException) as e:
        OtpService(db).generate_and_send(_agent(), "case-1", 5000.0, PaymentMode.CASH)
    assert e.value.status_code == 400


def test_generate_and_send_resend_throttle(db, fake_redis):
    _seed_case(db)
    svc = OtpService(db)
    svc.generate_and_send(_agent(), "case-1", 5000.0, PaymentMode.CASH)
    with pytest.raises(AppException) as e:
        svc.generate_and_send(_agent(), "case-1", 5000.0, PaymentMode.CASH)
    assert e.value.status_code == 429


# ── verify ────────────────────────────────────────────────────────────────────

def test_verify_wrong_code_increments_then_burns(db, fake_redis):
    _seed_case(db)
    _put_otp(fake_redis, "otp-1", code="1234")
    svc = OtpService(db)
    for expected_left in (2, 1, 0):
        with pytest.raises(AppException) as e:
            svc.verify(_agent(), "case-1", "otp-1", "0000")
        assert e.value.status_code == 400 and f"{expected_left} attempt" in e.value.detail
    # code burned after max attempts — key gone, now looks expired
    with pytest.raises(AppException) as e:
        svc.verify(_agent(), "case-1", "otp-1", "1234")
    assert e.value.status_code == 400 and "expired" in e.value.detail.lower()


def test_verify_expired_or_missing(db, fake_redis):
    _seed_case(db)
    with pytest.raises(AppException) as e:
        OtpService(db).verify(_agent(), "case-1", "nope", "1234")
    assert e.value.status_code == 400 and "expired" in e.value.detail.lower()


def test_verify_already_verified_is_idempotent(db, fake_redis):
    _seed_case(db)
    _put_otp(fake_redis, "otp-1", code="1234", verified=True)
    res = OtpService(db).verify(_agent(), "case-1", "otp-1", "1234")
    assert res["verified"] is True


def test_verify_correct_precollection_marks_verified(db, fake_redis):
    _seed_case(db)
    _put_otp(fake_redis, "otp-1", code="1234")
    res = OtpService(db).verify(_agent(), "case-1", "otp-1", "1234")
    assert res == {"verified": True, "otp_id": "otp-1", "payment_id": None}
    assert fake_redis.hgetall(OtpService._otp_key("otp-1"))["verified"] == "1"


def test_verify_correct_deferred_promotes_payment(db, fake_redis):
    _seed_case(db)
    db.add(Payment(
        id="pay-1", case_id="case-1", agent_id="agent-1", amount=5000.0,
        mode=PaymentMode.CASH, receipt_number="TIQ-2026-DEADBEEF",
        payment_date=datetime.now(timezone.utc), status=PaymentStatus.PENDING_VERIFICATION,
    ))
    db.commit()
    _put_otp(fake_redis, "otp-1", code="1234", payment_id="pay-1")
    res = OtpService(db).verify(_agent(), "case-1", "otp-1", "1234")
    assert res["payment_id"] == "pay-1"
    promoted = db.query(Payment).filter_by(id="pay-1").one()
    assert promoted.status == PaymentStatus.VERIFIED and promoted.verified_at is not None
    # single-use: OTP consumed
    assert fake_redis.hgetall(OtpService._otp_key("otp-1")) == {}


# ── consume_for_payment ───────────────────────────────────────────────────────

def test_consume_requires_verified_otp(db, fake_redis):
    _put_otp(fake_redis, "otp-1", verified=False)
    with pytest.raises(AppException) as e:
        OtpService(db).consume_for_payment("otp-1", "case-1", 5000.0)
    assert e.value.status_code == 400


def test_consume_rejects_amount_mismatch(db, fake_redis):
    _put_otp(fake_redis, "otp-1", amount=5000.0, verified=True)
    with pytest.raises(AppException):
        OtpService(db).consume_for_payment("otp-1", "case-1", 9999.0)


def test_consume_ok_regardless_of_mode_then_single_use(db, fake_redis):
    # OTP issued with no mode ("") still authorises a payment of any mode.
    _put_otp(fake_redis, "otp-1", amount=5000.0, mode="", verified=True)
    assert OtpService(db).consume_for_payment("otp-1", "case-1", 5000.0) is True
    # consumed — second attempt fails
    with pytest.raises(AppException):
        OtpService(db).consume_for_payment("otp-1", "case-1", 5000.0)


# ── collect_payment integration (VERIFIED vs offline PENDING) ────────────────

def _collect_req(**over):
    base = dict(amount=5000.0, mode=PaymentMode.CASH, visit_id=None, upi_reference=None,
                cheque_number=None, bank_reference=None, receipt_photo_key=None,
                verification_id=None)
    base.update(over)
    return SimpleNamespace(**base)


def test_collect_with_verification_writes_verified(db, fake_redis):
    _seed_case(db)
    _put_otp(fake_redis, "otp-1", amount=5000.0, mode=PaymentMode.CASH, verified=True)
    resp = PaymentService(db).collect_payment(_agent(), "case-1", _collect_req(verification_id="otp-1"))
    assert resp["status"] == PaymentStatus.VERIFIED
    assert db.query(Payment).filter_by(id=resp["id"]).one().verified_at is not None
    # OTP consumed (single-use)
    assert fake_redis.hgetall(OtpService._otp_key("otp-1")) == {}


def test_collect_without_verification_stays_pending(db, fake_redis):
    _seed_case(db)
    resp = PaymentService(db).collect_payment(_agent(), "case-1", _collect_req(verification_id=None))
    assert resp["status"] == PaymentStatus.PENDING_VERIFICATION
    assert db.query(Payment).filter_by(id=resp["id"]).one().verified_at is None
