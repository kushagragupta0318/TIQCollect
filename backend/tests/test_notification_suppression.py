"""B22 — invented numbers are never contacted.

2026-09-24. The demo book is invented people with real-format Indian mobiles,
and before this change the only gate between a recorded visit and an SMS to a
stranger was whether Twilio credentials happened to be set. These tests swap
in a fake Twilio client that records every message it is asked to send, so
"suppressed" is proved by the transport never being called — not by reading
the code.

2026-09-24 (later, coordinator audit gate 1). The demo-TENANT half was dead
code: `demo_tenant=False` was a default no caller overrode. The senders now
take `db` and a subject and read Bank/Agency.is_demo from the rows; a subject
that resolves to nothing is suppressed (fail closed). The tenant tests below
build real rows on a session with no default tenant.
"""
from __future__ import annotations

import sys
import types

import pytest

from datetime import date

from app.core.config import settings
from app.services.brand import NEUTRAL_NAME, brand_for, tenant_of
from app.services.notification_service import NotificationService, twilio_configured
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

INVENTED = "+919876501234"      # an invented borrower's number
PRESENTER = "8015935790"        # DEMO_CONTACT_PHONE default


@pytest.fixture()
def sent(monkeypatch):
    """A fake `twilio.rest.Client`; returns the list of messages it was asked to send."""
    calls: list[dict] = []

    class _Messages:
        def create(self, **kw):
            calls.append(kw)

    class _Client:
        def __init__(self, *_a, **_k):
            self.messages = _Messages()

    fake_rest = types.ModuleType("twilio.rest")
    fake_rest.Client = _Client
    monkeypatch.setitem(sys.modules, "twilio.rest", fake_rest)
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC_test_sid")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "test_token")
    monkeypatch.setattr(settings, "TWILIO_PHONE_NUMBER", "+15550001111")
    monkeypatch.setattr(settings, "TWILIO_WHATSAPP_FROM", "")
    monkeypatch.setattr(settings, "DEMO_CONTACT_PHONE", PRESENTER)
    monkeypatch.setattr(settings, "DEMO_NOTIFY_ALLOWLIST", "")
    return calls


REAL_BANK = test_id("bank:real")
REAL_AGENCY = test_id("agency:real")


@pytest.fixture()
def db():
    """Two tenants: the default test bank/agency (is_demo=True, from
    tests/_db.py) and a REAL one (is_demo False), each with one case."""
    from app.models import Case, Customer, Loan, LoanType
    from app.models.tenancy import Agency, Bank, Branch
    engine = make_engine()
    create_schema(engine)
    s = make_session_factory(engine, info={})()
    s.add(Bank(id=REAL_BANK, code="NPB", legal_name="Narmada Peoples Bank Ltd.", display_name="Narmada Peoples Bank",
               brand={"upi_payee_name": "NARMADA PEOPLES BANK"}, is_demo=False))
    s.flush()
    s.add_all([Agency(id=REAL_AGENCY, bank_id=REAL_BANK, code="NPB-AG-01", legal_name="Satpura Recoveries Pvt. Ltd.",
                      trade_name="Satpura Recoveries", status="ACTIVE", contacts=[], is_demo=False),
               Branch(bank_id=REAL_BANK, branch_code="BPL01", name="Bhopal MP Nagar")])
    s.flush()
    for tag, bank, agency, branch in (("demo", TEST_BANK_ID, TEST_AGENCY_ID, "GGN044"),
                                      ("real", REAL_BANK, REAL_AGENCY, "BPL01")):
        c = Customer(id=test_id(f"cust:{tag}"), bank_id=bank, customer_ref=f"{tag.upper()}-C-1", full_name="Meera Joshi",
                     date_of_birth=date(1985, 2, 3), gender="FEMALE", pan_masked="XXXXX1111X",
                     aadhaar_masked="XXXXXXXX1111", phone_primary=INVENTED[3:], address_line1="12, Arera Colony",
                     city="Bhopal", state="Madhya Pradesh", pincode="462016", latitude=23.21, longitude=77.43)
        ln = Loan(id=test_id(f"loan:{tag}"), bank_id=bank, loan_account_number=f"{tag.upper()}0000001",
                  customer_id=c.id, loan_type=LoanType.PERSONAL, branch_code=branch, sanctioned_amount=1.0,
                  disbursed_amount=1.0, outstanding_principal=1.0, total_outstanding=1.0, emi_amount=1.0,
                  disbursement_date=date(2024, 1, 1), maturity_date=date(2027, 1, 1), interest_rate=12.0)
        s.add_all([c, ln])
        s.flush()
        s.add(Case(id=test_id(f"case:{tag}"), bank_id=bank, agency_id=agency, case_number=f"{tag.upper()}-CASE-1",
                   customer_id=c.id, loan_id=ln.id, target_amount=1.0))
    s.commit()
    yield s
    s.close()


DEMO_CASE = test_id("case:demo")
REAL_CASE = test_id("case:real")


def test_demo_mode_never_dials_an_invented_number(sent, monkeypatch, db):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    assert NotificationService.send_sms(INVENTED, "Your OTP is 123456", db=db, case_id=REAL_CASE) is False
    assert NotificationService.send_twilio(INVENTED, "receipt", "receipt", db=db, case_id=REAL_CASE) is False
    assert sent == []


def test_demo_mode_still_reaches_the_presenter(sent, monkeypatch, db):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    to = "+" + NotificationService.normalize_phone(PRESENTER)
    assert NotificationService.send_sms(to, "Your OTP is 123456", db=db, case_id=DEMO_CASE) is True
    assert [c["to"] for c in sent] == [to]


def test_the_allowlist_admits_extra_numbers_in_any_format(sent, monkeypatch, db):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    monkeypatch.setattr(settings, "DEMO_NOTIFY_ALLOWLIST", "98100 00001, +91-9810000002")
    assert NotificationService.send_sms("+919810000001", "hi", db=db, case_id=DEMO_CASE) is True
    assert NotificationService.send_sms("+919810000002", "hi", db=db, case_id=DEMO_CASE) is True
    assert NotificationService.send_sms(INVENTED, "hi", db=db, case_id=DEMO_CASE) is False
    assert len(sent) == 2


def test_a_demo_tenant_is_suppressed_even_outside_demo_mode(sent, monkeypatch, db):
    """THE gate-1 defect: before 2026-09-24 this path sent, because nothing
    told the sender the tenant was a demo one."""
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    assert NotificationService.send_sms(INVENTED, "hi", db=db, case_id=DEMO_CASE) is False
    assert NotificationService.send_twilio(INVENTED, "a", "b", db=db, case_id=DEMO_CASE) is False
    assert sent == []


def test_a_demo_agency_of_a_real_bank_is_suppressed(sent, monkeypatch, db):
    from app.models.tenancy import Agency
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    db.get(Agency, REAL_AGENCY).is_demo = True
    db.commit()
    assert NotificationService.send_sms(INVENTED, "hi", db=db, case_id=REAL_CASE) is False
    assert sent == []


@pytest.mark.parametrize("subject", [
    {"case_id": test_id("case:does-not-exist")},     # a lookup that finds nothing
    {},                                              # no subject at all
])
def test_an_unresolved_tenant_fails_closed(sent, monkeypatch, db, subject):
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    assert NotificationService.send_sms(INVENTED, "hi", db=db, **subject) is False
    assert sent == []


def test_the_senders_cannot_be_called_without_a_session():
    """No default: a caller that forgets the tenant does not compile its way
    into sending."""
    with pytest.raises(TypeError):
        NotificationService.send_sms(INVENTED, "hi")          # type: ignore[call-arg]
    with pytest.raises(TypeError):
        NotificationService.send_twilio(INVENTED, "a", "b")   # type: ignore[call-arg]


def test_a_real_tenant_outside_demo_mode_is_sent(sent, monkeypatch, db):
    """Positive control: the gate is not simply closed."""
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    assert NotificationService.send_sms(INVENTED, "hi", db=db, case_id=REAL_CASE) is True
    assert NotificationService.send_twilio(INVENTED, "a", "b", db=db, case_id=REAL_CASE) is True
    assert len(sent) == 2


def test_brand_text_comes_from_the_rows_never_a_literal(db):
    b = brand_for(db, case_id=REAL_CASE)
    assert (b.bank_name, b.agency_name, b.upi_payee_name) == (
        "Narmada Peoples Bank", "Satpura Recoveries", "NARMADA PEOPLES BANK")
    assert brand_for(db, case_id=DEMO_CASE).bank_name == "Meridian Trust Bank"
    assert brand_for(db, case_id=test_id("nope")).bank_name == NEUTRAL_NAME
    assert tenant_of(db, case_id=test_id("nope")) is None


@pytest.mark.parametrize("sid", ["${TWILIO_ACCOUNT_SID}", "", "  ${X}"])
def test_an_uninterpolated_credential_counts_as_unset(sent, monkeypatch, sid):
    # A docker --env-file does not interpolate: `.env.example`'s
    # TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID} arrives as a literal string.
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", sid)
    assert twilio_configured() is False
    assert NotificationService.send_sms(INVENTED, "hi", db=None) is False
    assert sent == []
