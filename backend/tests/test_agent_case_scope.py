"""A03 — one definition of "a case this agent may act on" (services/scope.py).

2026-09-24. Four copies of the rule granted an agent any unassigned case in
ANY tenant, any case on any beat they ever had, and any teammate's case; two
of them then re-assigned the case to the caller. A refusal was a 403 beside a
missing case's 404, so every endpoint answered "does this case exist?".
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.errors import AppException
from app.core.security import create_access_token
from app.main import app
from app.models import Agent, Case, Customer, Loan, LoanType, User, UserRole
from app.models.beat import Beat
from app.models.tenancy import Agency
from app.services.scope import agent_case_or_404
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

from app.services.leave_service import leave_today  # noqa: E402

TODAY = leave_today()   # the IST calendar date access is judged on
OTHER_AGENCY = test_id("agency:kaveri")
PHONES = {"mgr": "9810002001", "mgrb": "9810002002", "asha": "9810002003", "bala": "9810002004", "chitra": "9810002005"}


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine)
    db = Session()
    db.add(Agency(id=OTHER_AGENCY, bank_id=TEST_BANK_ID, code="AGENCY-TIQ-002", legal_name="Kaveri Resolve LLP",
                  trade_name="Kaveri Resolve", status="ACTIVE", contacts=[], is_demo=True))
    db.flush()

    def person(tag, role, agency=TEST_AGENCY_ID):
        return User(id=test_id(f"u:{tag}"), email=f"{tag}@scope.test", phone=PHONES[tag],
                    full_name=tag.title(), hashed_password="x", role=role, bank_id=TEST_BANK_ID, agency_id=agency)

    mgr, mgr_b = person("mgr", UserRole.AGENCY_MANAGER), person("mgrb", UserRole.AGENCY_MANAGER, OTHER_AGENCY)
    ua, ub, uc = person("asha", UserRole.FIELD_AGENT), person("bala", UserRole.FIELD_AGENT), \
        person("chitra", UserRole.FIELD_AGENT, OTHER_AGENCY)
    db.add_all([mgr, mgr_b, ua, ub, uc])
    db.flush()

    def agent(tag, user, manager, agency=TEST_AGENCY_ID):
        return Agent(id=test_id(f"agent:{tag}"), user_id=user.id, employee_code=tag.upper(), id_card_number=f"{tag}-ID",
                     base_latitude=28.45, base_longitude=77.07, territory="Gurugram", manager_user_id=manager.id,
                     bank_id=TEST_BANK_ID, agency_id=agency)

    a, b, c = agent("asha", ua, mgr), agent("bala", ub, mgr), agent("chitra", uc, mgr_b, OTHER_AGENCY)
    db.add_all([a, b, c])
    db.flush()
    cust = Customer(id=test_id("cust"), bank_id=TEST_BANK_ID, customer_ref="S-C-1", full_name="Imran Sheikh",
                    date_of_birth=date(1982, 1, 1), gender="MALE", pan_masked="XXXXX3333X",
                    aadhaar_masked="XXXXXXXX3333", phone_primary="9899003333", address_line1="7, DLF Phase 2",
                    city="Gurugram", state="Haryana", pincode="122002", latitude=28.49, longitude=77.09)
    loan = Loan(id=test_id("loan"), bank_id=TEST_BANK_ID, loan_account_number="S0000001", customer_id=cust.id,
                loan_type=LoanType.PERSONAL, branch_code="GGN044", sanctioned_amount=1.0, disbursed_amount=1.0,
                outstanding_principal=1.0, total_outstanding=1.0, emi_amount=1.0,
                disbursement_date=date(2024, 1, 1), maturity_date=date(2027, 1, 1), interest_rate=12.0)
    db.add_all([cust, loan])
    db.flush()

    def case(tag, agent_id, agency=TEST_AGENCY_ID):
        return Case(id=test_id(f"case:{tag}"), case_number=f"S-{tag}"[:20], customer_id=cust.id, loan_id=loan.id,
                    agent_id=agent_id, target_amount=1000.0, bank_id=TEST_BANK_ID, agency_id=agency)

    cases = {"mine": case("mine", a.id), "peer": case("peer", b.id), "pool": case("pool", None),
             "other_agency_pool": case("other-pool", None, OTHER_AGENCY),
             "handover": case("handover", b.id), "old_beat": case("old-beat", b.id)}
    db.add_all(cases.values())
    db.add_all([Beat(agent_id=a.id, beat_date=TODAY, beat_number=1, ordered_case_ids=[cases["mine"].id, cases["handover"].id]),
                Beat(agent_id=a.id, beat_date=TODAY - timedelta(days=3), beat_number=2, ordered_case_ids=[cases["old_beat"].id])])
    db.commit()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        yield {"db": db, "agent": a, "user": ua, "cases": {k: v.id for k, v in cases.items()}}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _refused(w, key_or_id):
    cid = w["cases"].get(key_or_id, key_or_id)
    with pytest.raises(AppException) as exc:
        agent_case_or_404(w["db"], w["agent"], cid)
    assert exc.value.status_code == 404 and exc.value.detail == "Not found"


def test_own_case_is_granted(w):
    assert agent_case_or_404(w["db"], w["agent"], w["cases"]["mine"]).id == w["cases"]["mine"]


def test_a_case_on_todays_beat_is_granted_and_synced_only_when_asked(w):
    db, cid = w["db"], w["cases"]["handover"]
    assert agent_case_or_404(db, w["agent"], cid).agent_id != w["agent"].id      # read: not synced
    assert agent_case_or_404(db, w["agent"], cid, sync_assignee=True).agent_id == w["agent"].id


@pytest.mark.parametrize("key", ["peer", "pool", "other_agency_pool", "old_beat"])
def test_every_old_grant_is_now_the_same_404(w, key):
    """(c) a teammate's case, (a) an unassigned case — here AND in another
    agency — and (b) a case on a beat from three days ago."""
    _refused(w, key)


def test_yesterdays_beat_grants_nothing_today_even_when_today_has_no_beat(w, monkeypatch):
    """The day boundary (coordinator, CLAUDE.md issue 13). With NO beat today
    the screens fall back to 'the latest day a plan existed' for display; if
    access used that, yesterday's handover cases would open today. Access is
    judged on the IST calendar date, so they must not."""
    from app.services import leave_service
    db = w["db"]
    db.query(Beat).filter(Beat.beat_date == TODAY).delete()
    db.add(Beat(agent_id=w["agent"].id, beat_date=TODAY - timedelta(days=1), beat_number=3,
                ordered_case_ids=[w["cases"]["handover"]]))
    db.commit()
    _refused(w, "handover")
    # ... and the same beat DOES grant on the day it is for.
    monkeypatch.setattr(leave_service, "leave_today", lambda: TODAY - timedelta(days=1))
    assert agent_case_or_404(db, w["agent"], w["cases"]["handover"]).id == w["cases"]["handover"]

@pytest.mark.parametrize("bad", ["not-a-uuid", "", test_id("case:does-not-exist")])
def test_missing_and_malformed_ids_get_the_same_404(w, bad):
    _refused(w, bad)


def test_reading_a_refused_case_never_reassigns_it(w):
    _refused(w, "pool")
    w["db"].expire_all()
    assert w["db"].get(Case, w["cases"]["pool"]).agent_id is None


def _h(w):
    return {"Authorization": "Bearer " + create_access_token(w["user"].id, "FIELD_AGENT", "dev-1")}


def test_the_case_detail_endpoint_hides_existence(w):
    client = TestClient(app)
    foreign = client.get(f"/api/v1/agent/cases/{w['cases']['other_agency_pool']}", headers=_h(w))
    missing = client.get(f"/api/v1/agent/cases/{test_id('nope')}", headers=_h(w))
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert client.get(f"/api/v1/agent/cases/{w['cases']['mine']}", headers=_h(w)).status_code == 200


def test_the_payment_link_route_checks_access_before_anything(w):
    """It checked none: any agent could mint a UPI QR for any case id."""
    r = TestClient(app).post(f"/api/v1/agent/cases/{w['cases']['peer']}/payment-link", json={"amount": 500},
                             headers=_h(w))
    assert r.status_code == 404


# ── set_ptp upper bound (coordinator audit, found on d4's H14 review) ────────

def _ptp(w, amount):
    from types import SimpleNamespace as NS
    from app.services.payment_service import PaymentService
    req = NS(committed_amount=amount, committed_date=TODAY + timedelta(days=5), customer_reason=None,
             agent_notes=None, follow_up_date=None)
    return PaymentService(w["db"]).set_ptp(w["agent"], w["cases"]["mine"], req)


def test_a_promise_is_capped_at_what_the_case_still_needs(w):
    assert _ptp(w, 800.0)["committed_amount"] == 800.0
    assert _ptp(w, 5_000.0)["committed_amount"] == 1000.0     # target 1,000, nothing collected


def test_a_met_target_caps_at_the_loan_not_at_nothing(w):
    """It used to skip the cap entirely here: `if remaining > 0`."""
    case = w["db"].get(Case, w["cases"]["mine"])
    case.collected_amount = case.target_amount
    w["db"].commit()
    assert _ptp(w, 99_900_000.0)["committed_amount"] == 1.0    # the loan's total_outstanding


def test_with_nothing_owed_there_is_nothing_to_promise(w):
    case = w["db"].get(Case, w["cases"]["mine"])
    case.collected_amount = case.target_amount
    w["db"].get(Loan, case.loan_id).total_outstanding = 0.0
    w["db"].commit()
    with pytest.raises(AppException) as exc:
        _ptp(w, 500.0)
    assert exc.value.status_code == 400
