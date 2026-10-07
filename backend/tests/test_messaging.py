"""Bank↔agency messaging (service-level, SQLite).

Covers the thread lazy-create, the derived sender_side, two-tenant visibility
(bank and the owning agency see it; another agency/bank reads 404), the pending
view (the side awaiting a reply), and the subject guards. The cross-tenant RLS
write itself is Postgres-only (tests/pg, via the policy map).

Each test gets a fresh in-memory database (post_message commits, so a shared one
would leak threads across tests and break the pending counts).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import insert

from app.models.audit_log import AuditAction, AuditLog
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.message import MessageThread, SenderSide, ThreadStatus, ThreadSubject
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.payment_reversal import PaymentReversalRequest, ReversalStatus
from app.models.tenancy import Agency, Bank
from app.models.user import User, UserRole
from app.services.messaging_service import MessagingService
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

BANK = TEST_BANK_ID
AGENCY = TEST_AGENCY_ID
OTHER_AGENCY = test_id("agency:msg-other")
OTHER_BANK = test_id("bank:msg-other")


@pytest.fixture
def db():
    engine = make_engine()
    create_schema(engine)
    with engine.begin() as conn:
        conn.execute(insert(Bank.__table__), [{
            "id": OTHER_BANK, "code": "OTB", "legal_name": "Other Trust Bank Ltd.",
            "display_name": "Other Trust Bank", "timezone": "Asia/Kolkata", "brand": {},
            "status": "ACTIVE", "is_demo": True}])
        conn.execute(insert(Agency.__table__), [{
            "id": OTHER_AGENCY, "bank_id": BANK, "code": "AGENCY-MSG-OTHER",
            "legal_name": "Other Agency Pvt. Ltd.", "trade_name": "Other Agency",
            "status": "ACTIVE", "contacts": [], "is_demo": True}])
    s = make_session_factory(bind=engine)()
    yield s
    s.close()


def _uid() -> str:
    return str(uuid.uuid4())


class _Ctx:
    def __init__(self, user, scope):
        self.user_id, self.bank_id, self.agency_id, self.scope = user.id, user.bank_id, user.agency_id, scope


_BANK_ROLES = (UserRole.BANK_ADMIN, UserRole.BANK_ANALYST, UserRole.BANK_TECHOPS)


def _user(db, role, *, agency=AGENCY, bank=BANK):
    uid = _uid()
    u = User(id=uid, email=f"{role.value.lower()}-{uid[:6]}@t.io", phone="98" + uid.replace("-", "")[:8],
             full_name=role.value, hashed_password="x", role=role, is_active=True, is_verified=True,
             bank_id=bank, agency_id=(None if role in _BANK_ROLES else agency))
    db.add(u); db.flush(); return u


def _reversal(db, *, bank=BANK, agency=AGENCY) -> PaymentReversalRequest:
    cust = Customer(id=_uid(), customer_ref=_uid()[:8], full_name="B", date_of_birth=date(1990, 1, 1),
                    gender="M", pan_masked="ABCDE1234F", aadhaar_masked="123456789012",
                    phone_primary="9812345678", address_line1="D", city="D", state="D", pincode="110001",
                    latitude=28.6, longitude=77.2, language_preference="HINDI")
    db.add(cust); db.flush()
    loan = Loan(id=_uid(), customer_id=cust.id, loan_account_number="L" + _uid()[:6], loan_type=LoanType.PERSONAL,
                branch_code="DL01", sanctioned_amount=100000.0, disbursed_amount=100000.0,
                outstanding_principal=50000.0, total_outstanding=50000.0, overdue_amount=10000.0,
                emi_amount=5000.0, interest_rate=12.0, disbursement_date=date(2022, 1, 1),
                maturity_date=date(2027, 1, 1), dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE)
    db.add(loan); db.flush()
    case = Case(id=_uid(), case_number="C-" + _uid()[:6], customer_id=cust.id, loan_id=loan.id,
                status=CaseStatus.PAID, target_amount=1000.0, collected_amount=1000.0,
                allocation_date=date.today())
    db.add(case); db.flush()
    pay = Payment(id=_uid(), bank_id=bank, agency_id=agency, case_id=case.id, loan_id=loan.id,
                  amount=1000.0, mode=PaymentMode.CASH, status=PaymentStatus.VERIFIED,
                  receipt_number="R" + _uid()[:8], payment_date=datetime.now(timezone.utc))
    db.add(pay); db.flush()
    rev = PaymentReversalRequest(id=_uid(), bank_id=bank, agency_id=agency, payment_id=pay.id,
                                 case_id=case.id, reason="x", status=ReversalStatus.PENDING_BANK,
                                 agency_requested_by_id=_user(db, UserRole.AGENCY_MANAGER, agency=agency).id,
                                 agency_approved_by_id=_user(db, UserRole.AGENCY_ADMIN, agency=agency).id,
                                 agency_approved_at=datetime.now(timezone.utc))
    db.add(rev); db.flush(); return rev


def test_agency_post_lazy_creates_thread_and_derives_side(db):
    rev = _reversal(db)
    mgr = _user(db, UserRole.AGENCY_MANAGER)
    svc = MessagingService(db)
    out = svc.post_message(_Ctx(mgr, "AGENCY"), ThreadSubject.REVERSAL.value, rev.id, "please reverse, mis-key")
    assert out["thread"]["status"] == ThreadStatus.OPEN.value
    assert len(out["messages"]) == 1
    assert out["messages"][0]["sender_side"] == SenderSide.AGENCY.value   # derived from scope
    t = db.query(MessageThread).one()
    assert t.bank_id == BANK and t.agency_id == AGENCY                    # thread carries the subject's tenant
    assert db.query(AuditLog).filter(AuditLog.action == AuditAction.MESSAGE_SENT).count() == 1


def test_bank_and_agency_share_one_thread_second_post_appends(db):
    rev = _reversal(db)
    mgr = _user(db, UserRole.AGENCY_MANAGER)
    bank = _user(db, UserRole.BANK_ADMIN)
    svc = MessagingService(db)
    svc.post_message(_Ctx(mgr, "AGENCY"), ThreadSubject.REVERSAL.value, rev.id, "agency says hi")
    out = svc.post_message(_Ctx(bank, "BANK"), ThreadSubject.REVERSAL.value, rev.id, "bank replies")
    assert db.query(MessageThread).count() == 1               # no second thread
    assert [m["sender_side"] for m in out["messages"]] == [SenderSide.AGENCY.value, SenderSide.BANK.value]


def test_another_agency_of_the_same_bank_cannot_see_it(db):
    rev = _reversal(db)
    mgr = _user(db, UserRole.AGENCY_MANAGER)
    other = _user(db, UserRole.AGENCY_MANAGER, agency=OTHER_AGENCY)
    svc = MessagingService(db)
    svc.post_message(_Ctx(mgr, "AGENCY"), ThreadSubject.REVERSAL.value, rev.id, "mine")
    with pytest.raises(HTTPException) as e:
        svc.get_thread(_Ctx(other, "AGENCY"), ThreadSubject.REVERSAL.value, rev.id)
    assert e.value.status_code == 404


def test_another_bank_cannot_post(db):
    rev = _reversal(db)
    outsider = _user(db, UserRole.BANK_ADMIN, bank=OTHER_BANK)
    svc = MessagingService(db)
    with pytest.raises(HTTPException) as e:
        svc.post_message(_Ctx(outsider, "BANK"), ThreadSubject.REVERSAL.value, rev.id, "not mine")
    assert e.value.status_code == 404


def test_pending_is_the_side_awaiting_a_reply(db):
    rev = _reversal(db)
    mgr = _user(db, UserRole.AGENCY_MANAGER)
    bank = _user(db, UserRole.BANK_ADMIN)
    svc = MessagingService(db)
    svc.post_message(_Ctx(mgr, "AGENCY"), ThreadSubject.REVERSAL.value, rev.id, "agency asks")
    assert len(svc.list_pending(_Ctx(bank, "BANK"))) == 1    # bank awaiting; agency (last speaker) not
    assert svc.list_pending(_Ctx(mgr, "AGENCY")) == []
    svc.post_message(_Ctx(bank, "BANK"), ThreadSubject.REVERSAL.value, rev.id, "bank answers")
    assert svc.list_pending(_Ctx(bank, "BANK")) == []        # now the agency is awaiting
    assert len(svc.list_pending(_Ctx(mgr, "AGENCY"))) == 1


def test_get_thread_is_empty_before_the_first_message(db):
    rev = _reversal(db)
    mgr = _user(db, UserRole.AGENCY_MANAGER)
    out = MessagingService(db).get_thread(_Ctx(mgr, "AGENCY"), ThreadSubject.REVERSAL.value, rev.id)
    assert out["thread"] is None and out["messages"] == []


def test_empty_body_and_unwired_placement_are_refused(db):
    rev = _reversal(db)
    mgr = _user(db, UserRole.AGENCY_MANAGER)
    svc = MessagingService(db)
    with pytest.raises(HTTPException) as e1:
        svc.post_message(_Ctx(mgr, "AGENCY"), ThreadSubject.REVERSAL.value, rev.id, "   ")
    assert e1.value.status_code == 422
    with pytest.raises(HTTPException) as e2:
        svc.post_message(_Ctx(mgr, "AGENCY"), ThreadSubject.PLACEMENT.value, _uid(), "hi")
    assert e2.value.status_code == 422
