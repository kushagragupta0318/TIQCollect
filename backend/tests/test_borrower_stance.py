"""ML-1 option A, 2026-09-24: the borrower's stance (BorrowerDisposition).

recovery_risk 2.2.0 reads `latest_disposition` — its strongest behavioural
feature — from `Visit.borrower_disposition` and `CallLog.borrower_disposition`.
Nothing in the product wrote either: 0 of 2,404 visits and 0 of 1,089 call
logs on the live book, so every prediction read NONE. These tests hold the new
write path: a stance is stored only from a contact that reached the borrower,
never defaulted, refused (not dropped) otherwise, and it reaches the model's
own feature adapter unchanged.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.errors import AppException, ErrorCode
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.call_log import BorrowerDisposition, CallLog, CallOutcome
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.user import User, UserRole
from app.models.visit import PersonMet, Visit, VisitOutcome
from app.schemas.agent import RecordVisitRequest
from app.services import visit_service as vs
from app.services.ml_scoring_service import MLScoringService

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _uid():
    return str(uuid.uuid4())


@pytest.fixture(scope="module")
def world():
    Base.metadata.create_all(engine)
    db = Session()
    mgr = User(id=_uid(), email="rekha.suri@aravallifs.in", phone="9000000301", full_name="Rekha Suri",
               hashed_password="x", role=UserRole.AGENCY_MANAGER, is_active=True, is_verified=True)
    ua = User(id=_uid(), email="manoj.bisht@aravallifs.in", phone="9000000302", full_name="Manoj Bisht",
              hashed_password="x", role=UserRole.FIELD_AGENT, is_active=True, is_verified=True)
    db.add_all([mgr, ua])
    db.flush()
    ag = Agent(id=_uid(), user_id=ua.id, employee_code="EMP301", id_card_number="EMP301-ID", agency_id="AG1",
               manager_user_id=mgr.id, gender="M", base_latitude=28.45, base_longitude=77.07, territory="Gurugram",
               languages_spoken=["HINDI"], status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
               specialization=AgentSpecialization.BOTH, ranking_score=80.0, max_cases_per_day=5)
    db.add(ag)
    db.commit()
    yield {"ua": ua, "ag_id": ag.id}
    db.close()
    Base.metadata.drop_all(engine)


def _case(world) -> dict:
    db = Session()
    ref = "S" + uuid.uuid4().hex[:8].upper()
    c = Customer(id=_uid(), customer_ref=ref, full_name="Pradeep Rawat", date_of_birth="1984-02-11", gender="M",
                 pan_masked="ABCDE1234F", aadhaar_masked="123456789012", phone_primary="9812300301",
                 address_line1="Sector 21", city="Gurugram", state="Haryana", pincode="122016",
                 latitude=28.45, longitude=77.07, language_preference="HINDI")
    db.add(c)
    db.flush()
    loan = Loan(id=_uid(), customer_id=c.id, loan_account_number="L" + ref, loan_type=LoanType.PERSONAL,
                bank_name="HDFC", branch_code="GGN021", sanctioned_amount=100000.0, disbursed_amount=100000.0,
                outstanding_principal=50000.0, total_outstanding=50000.0, overdue_amount=10000.0,
                emi_amount=5000.0, interest_rate=12.0, disbursement_date="2022-01-01",
                maturity_date="2027-01-01", dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE)
    db.add(loan)
    db.flush()
    k = Case(id=_uid(), case_number="C-" + ref, customer_id=c.id, loan_id=loan.id, agent_id=world["ag_id"],
             status=CaseStatus.ASSIGNED, target_amount=20000.0, collected_amount=0.0,
             allocation_date=date.today().isoformat())
    db.add(k)
    db.commit()
    out = {"case_id": k.id, "loan_id": loan.id}
    db.close()
    return out


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(vs, "is_within_contact_hours", lambda *a, **k: True)
    monkeypatch.setattr(vs.AIReportService, "generate_visit_report", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(vs.NotificationService, "send_twilio", staticmethod(lambda *a, **k: False))


def _record(world, case_id, **kw):
    db = Session()
    agent = db.get(Agent, world["ag_id"])
    base = dict(check_in_latitude=28.45, check_in_longitude=77.07, customer_met=True,
                person_met=PersonMet.BORROWER, outcome=VisitOutcome.REVISIT)
    base.update(kw)
    try:
        return vs.VisitService(db).record_visit(agent, case_id, RecordVisitRequest(**base))
    finally:
        db.close()


def _visits(case_id):
    db = Session()
    out = db.query(Visit).filter(Visit.case_id == case_id).all()
    db.close()
    return out


# ── visits ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("stance", list(BorrowerDisposition))
def test_a_met_borrowers_stance_is_stored(world, quiet, stance):
    c = _case(world)
    _record(world, c["case_id"], borrower_disposition=stance)
    [v] = _visits(c["case_id"])
    assert v.borrower_disposition == stance


def test_no_stance_stays_null_never_a_default(world, quiet):
    c = _case(world)
    _record(world, c["case_id"])
    [v] = _visits(c["case_id"])
    assert v.borrower_disposition is None


@pytest.mark.parametrize("kw", [
    {"customer_met": False, "person_met": None, "outcome": VisitOutcome.NOT_AVAILABLE},
    {"customer_met": True, "person_met": PersonMet.SPOUSE},
    {"customer_met": True, "person_met": PersonMet.NEIGHBOR},
])
def test_a_stance_from_a_visit_that_did_not_meet_the_borrower_is_refused(world, quiet, kw):
    c = _case(world)
    with pytest.raises(AppException) as e:
        _record(world, c["case_id"], borrower_disposition=BorrowerDisposition.REFUSES, **kw)
    assert e.value.code == ErrorCode.DISPOSITION_WITHOUT_BORROWER and e.value.status_code == 422
    assert _visits(c["case_id"]) == []               # refused before anything was written


def test_the_older_api_shape_without_person_met_still_counts_as_the_borrower(world, quiet):
    c = _case(world)
    _record(world, c["case_id"], person_met=None, borrower_disposition=BorrowerDisposition.MAY_PAY)
    assert _visits(c["case_id"])[0].borrower_disposition == BorrowerDisposition.MAY_PAY


# ── calls, through the route ─────────────────────────────────────────────────
@pytest.fixture
def client(world):
    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _hdr(world):
    return {"Authorization": f"Bearer {create_access_token(world['ua'].id, 'FIELD_AGENT', 'dev')}"}


def _calls(case_id):
    db = Session()
    out = db.query(CallLog).filter(CallLog.case_id == case_id).all()
    db.close()
    return out


def test_an_answered_calls_stance_is_stored(client, world):
    c = _case(world)
    r = client.post(f"/api/v1/agent/cases/{c['case_id']}/call-log", headers=_hdr(world),
                    json={"outcome": "ANSWERED", "borrower_disposition": "HARDSHIP"})
    assert r.status_code == 201, r.text
    [log] = _calls(c["case_id"])
    assert log.borrower_disposition == BorrowerDisposition.HARDSHIP


def test_a_call_without_a_stance_stays_null(client, world):
    c = _case(world)
    assert client.post(f"/api/v1/agent/cases/{c['case_id']}/call-log", headers=_hdr(world),
                       json={"outcome": "ANSWERED"}).status_code == 201
    assert _calls(c["case_id"])[0].borrower_disposition is None


@pytest.mark.parametrize("outcome", [o.value for o in CallOutcome if o != CallOutcome.ANSWERED])
def test_a_stance_on_an_unanswered_call_is_refused(client, world, outcome):
    c = _case(world)
    r = client.post(f"/api/v1/agent/cases/{c['case_id']}/call-log", headers=_hdr(world),
                    json={"outcome": outcome, "borrower_disposition": "WILL_PAY"})
    assert r.status_code == 422 and r.json()["code"] == "DISPOSITION_WITHOUT_BORROWER"
    assert _calls(c["case_id"]) == []


def test_an_unknown_stance_is_a_validation_error(client, world):
    c = _case(world)
    r = client.post(f"/api/v1/agent/cases/{c['case_id']}/call-log", headers=_hdr(world),
                    json={"outcome": "ANSWERED", "borrower_disposition": "COOPERATIVE"})    # the old tone
    assert r.status_code == 422
    assert _calls(c["case_id"]) == []


# ── the model sees it ────────────────────────────────────────────────────────
def test_the_recorded_stance_reaches_the_models_feature(world, quiet):
    """End to end: a stance recorded today is `latest_disposition` for
    tomorrow's score, through the production adapter, with no code in between."""
    c = _case(world)
    db = Session()
    loan = db.get(Loan, c["loan_id"])
    before = MLScoringService(db).build_features(loan, as_of=date.today() + timedelta(days=1))
    db.close()
    assert before["latest_disposition"] == "NONE"

    _record(world, c["case_id"], borrower_disposition=BorrowerDisposition.WILL_PAY)
    db = Session()
    loan = db.get(Loan, c["loan_id"])
    after = MLScoringService(db).build_features(loan, as_of=date.today() + timedelta(days=1))
    db.close()
    assert after["latest_disposition"] == "WILL_PAY"
    assert after["disposition_recency_class"] == "WILL_PAY_FRESH"


def test_a_later_answered_call_is_the_newer_reading(world, quiet, client):
    c = _case(world)
    _record(world, c["case_id"], borrower_disposition=BorrowerDisposition.MAY_PAY)
    assert client.post(f"/api/v1/agent/cases/{c['case_id']}/call-log", headers=_hdr(world),
                       json={"outcome": "ANSWERED", "borrower_disposition": "REFUSES"}).status_code == 201
    db = Session()
    feats = MLScoringService(db).build_features(db.get(Loan, c["loan_id"]), as_of=date.today() + timedelta(days=1))
    db.close()
    # Same day: the panel's tiebreak takes the CALL as the later reading.
    assert feats["latest_disposition"] == "REFUSES"
