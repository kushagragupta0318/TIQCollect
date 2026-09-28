"""The contact-hour rule, measured instead of assumed.

2026-09-11. The compliance tile said "0 of N visits outside 8 AM – 7 PM" and
was a tautology: record_visit refuses an out-of-hours attempt with 403 and
stores nothing, so through the real path the stored flag could only ever be
True. Meanwhile AuditAction.CONTACT_HOUR_VIOLATION_ATTEMPT had been declared
since the first schema and written by nothing. Three things are pinned here:

  1. the refusal now leaves evidence — one CONTACT_HOUR_VIOLATION_ATTEMPT row,
     attributed to the agent's user so the manager's audit scope finds it,
     committed before the 403, and never able to weaken the 403;
  2. GET /manager/compliance reports those rows as blocked attempts for the
     same month and team as every other figure on the page, and derives its
     audit-action coverage from the enum and the table rather than a literal;
  3. the seed and demo scripts derive within_contact_hours from the check-in
     time through the one canonical helper, in IST, instead of writing True.

The rule's own boundaries (08:00 in, 19:00 out) are already covered by
tests/test_geo.py and are not restated.
"""
from __future__ import annotations

import pathlib
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.geo import IST, is_within_contact_hours
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.audit_log import AuditAction, AuditLog
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.user import User, UserRole
from app.models.visit import Visit, VisitOutcome
from app.schemas.agent import RecordVisitRequest
from app.services import visit_service as vs
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

engine = make_engine()
TestingSession = make_session_factory(autocommit=False, autoflush=False, bind=engine)
TODAY = date.today()
BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _uid() -> str:
    return str(uuid.uuid4())


def _user(db, email, role, name):
    u = User(id=_uid(), email=email, phone=email.split("@")[0][:10].ljust(10, "0"),
             full_name=name, hashed_password="x", role=role, is_active=True, is_verified=True)
    db.add(u)
    return u


def _agent(db, user, code, mgr):
    a = Agent(id=_uid(), user_id=user.id, employee_code=code, id_card_number=code + "-ID",
              manager_user_id=mgr.id, gender="M",
              base_latitude=28.63, base_longitude=77.21, territory="Delhi",
              languages_spoken=["HINDI"], status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
              specialization=AgentSpecialization.BOTH, ranking_score=80.0, max_cases_per_day=5)
    db.add(a)
    return a


def _case_for(db, agent, ref):
    c = Customer(id=_uid(), customer_ref=ref, full_name=f"Borrower {ref}",
                 date_of_birth=date(1990, 1, 1), gender="M", pan_masked="ABCDE1234F",
                 aadhaar_masked="123456789012", phone_primary="98" + ref.ljust(8, "0"),
                 address_line1="Delhi", city="Delhi", state="Delhi", pincode="110001",
                 latitude=28.6315, longitude=77.2167, language_preference="HINDI")
    db.add(c)
    db.flush()
    loan = Loan(id=_uid(), customer_id=c.id, loan_account_number="L" + ref,
                loan_type=LoanType.PERSONAL, branch_code="DL01",
                sanctioned_amount=100000.0, disbursed_amount=100000.0,
                outstanding_principal=50000.0, total_outstanding=50000.0,
                overdue_amount=10000.0, emi_amount=5000.0, interest_rate=12.0,
                disbursement_date=date(2022, 1, 1), maturity_date=date(2027, 1, 1),
                dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE)
    db.add(loan)
    db.flush()
    k = Case(id=_uid(), case_number="C-" + ref, customer_id=c.id, loan_id=loan.id,
             agent_id=agent.id, status=CaseStatus.ASSIGNED,
             target_amount=20000.0, collected_amount=0.0, allocation_date=TODAY)
    db.add(k)
    return k


@pytest.fixture(scope="module")
def world():
    create_schema(engine)
    db = TestingSession()
    mgr_a = _user(db, "a@t.io", UserRole.AGENCY_MANAGER, "Manager A")
    mgr_b = _user(db, "b@t.io", UserRole.AGENCY_MANAGER, "Manager B")
    db.flush()
    ag_a = _agent(db, _user(db, "ag_a@t.io", UserRole.FIELD_AGENT, "Agent A"), "EMPA", mgr_a)
    ag_b = _agent(db, _user(db, "ag_b@t.io", UserRole.FIELD_AGENT, "Agent B"), "EMPB", mgr_b)
    db.flush()
    case_a = _case_for(db, ag_a, "A1")
    case_a2 = _case_for(db, ag_a, "A2")
    case_b = _case_for(db, ag_b, "B1")
    db.commit()
    yield {"db": db, "mgr_a": mgr_a, "mgr_b": mgr_b, "ag_a": ag_a, "ag_b": ag_b,
           "case_a": case_a, "case_a2": case_a2, "case_b": case_b}
    db.close()
    drop_schema(engine)


@pytest.fixture(scope="module")
def client(world):
    def override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _h(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value, 'dev')}"}


def _freeze(monkeypatch, ist_hour: int, minute: int = 0):
    """Pin visit_service's clock to TODAY at the given IST hour.

    record_visit reads datetime.now(timezone.utc) through the module's own
    `datetime` name, so a subclass whose now() is fixed is enough — combine,
    timedelta arithmetic and astimezone all still work on it."""
    fixed = datetime.combine(TODAY, datetime.min.time()).replace(
        hour=ist_hour, minute=minute, tzinfo=IST).astimezone(timezone.utc)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(vs, "datetime", _Frozen)
    return fixed


def _req() -> RecordVisitRequest:
    return RecordVisitRequest(check_in_latitude=28.6315, check_in_longitude=77.2167,
                              customer_met=False, outcome=VisitOutcome.NOT_AVAILABLE)


def _violations(db, case_id=None):
    q = db.query(AuditLog).filter(AuditLog.action == AuditAction.CONTACT_HOUR_VIOLATION_ATTEMPT)
    if case_id:
        q = q.filter(AuditLog.entity_id == case_id)
    return q.all()


# ═══════════════════════════════════════════════════════════════════════════
# 1. The refusal leaves evidence
# ═══════════════════════════════════════════════════════════════════════════

def test_out_of_hours_visit_is_refused_and_leaves_exactly_one_audit_row(world, monkeypatch):
    db = TestingSession()
    fixed = _freeze(monkeypatch, 22, 6)          # 22:06 IST — the demo script's own hour
    agent = db.get(Agent, world["ag_a"].id)
    case = world["case_a"]
    visits_before = db.query(Visit).count()

    with pytest.raises(HTTPException) as exc:
        vs.VisitService(db).record_visit(agent, case.id, _req())
    assert exc.value.status_code == 403
    assert "IST" in exc.value.detail            # the 403 is unchanged

    rows = _violations(db, case.id)
    assert len(rows) == 1, "one refused request must leave exactly one row"
    row = rows[0]
    assert row.user_id == agent.user_id          # attributed -> tenant-scoped
    assert row.entity_type == "Case" and row.entity_id == case.id
    assert row.success is False
    assert row.failure_reason == "outside contact hours"
    assert row.details["ist_hour"] == 22
    assert row.details["channel"] == "visit"
    assert row.details["agent_id"] == agent.id
    assert row.details["outcome_attempted"] == "NOT_AVAILABLE"
    assert row.details["window"] == "08:00-19:00 IST"
    # Written and COMMITTED before the 403: a fresh session sees it.
    other = TestingSession()
    assert len(_violations(other, case.id)) == 1
    other.close()
    # And the refused visit was never persisted as a visit.
    assert db.query(Visit).count() == visits_before
    assert row.created_at.replace(tzinfo=timezone.utc) == fixed
    db.close()


def test_in_hours_visit_writes_no_violation_row(world, monkeypatch):
    db = TestingSession()
    _freeze(monkeypatch, 11, 30)
    # Best-effort side effects are not under test and must not need a network.
    monkeypatch.setattr(vs.AIReportService, "generate_visit_note",
                        lambda self, *a, **k: None, raising=False)
    monkeypatch.setattr(vs.NotificationService, "notify_visit",
                        lambda self, *a, **k: None, raising=False)
    agent = db.get(Agent, world["ag_a"].id)
    case = world["case_a2"]
    before = len(_violations(db))
    try:
        vs.VisitService(db).record_visit(agent, case.id, _req())
    except HTTPException as e:           # any refusal here is a test bug, not a pass
        pytest.fail(f"in-hours visit refused: {e.status_code} {e.detail}")
    assert len(_violations(db)) == before
    assert db.query(Visit).filter(Visit.case_id == case.id).count() == 1
    stored = db.query(Visit).filter(Visit.case_id == case.id).one()
    assert stored.within_contact_hours is True
    db.close()


def test_an_audit_write_failure_cannot_weaken_the_refusal(world, monkeypatch):
    """The refusal is the control; the row is evidence of it. If the evidence
    cannot be written the control still fires — logged at ERROR, like a
    swallowed notification failure, never turned into a 500 and never
    into a recorded visit."""
    db = TestingSession()
    _freeze(monkeypatch, 23)
    agent = db.get(Agent, world["ag_a"].id)
    case = world["case_a2"]
    before = len(_violations(db))
    real_commit = db.commit
    calls = {"n": 0}

    def _boom():
        calls["n"] += 1
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(db, "commit", _boom)
    with pytest.raises(HTTPException) as exc:
        vs.VisitService(db).record_visit(agent, case.id, _req())
    assert exc.value.status_code == 403
    assert calls["n"] == 1
    monkeypatch.setattr(db, "commit", real_commit)
    db.rollback()
    assert len(_violations(db)) == before        # nothing half-written
    db.close()


# ═══════════════════════════════════════════════════════════════════════════
# 2. The compliance endpoint reports it, scoped like everything else
# ═══════════════════════════════════════════════════════════════════════════

def _blocked_row(db, agent, case, when, **extra):
    db.add(AuditLog(id=_uid(), created_at=when, user_id=agent.user_id,
                    action=AuditAction.CONTACT_HOUR_VIOLATION_ATTEMPT,
                    entity_type="Case", entity_id=case.id,
                    details={"channel": "visit", **extra}, success=False,
                    failure_reason="outside contact hours"))


def test_compliance_reports_blocked_attempts_for_this_month_and_this_team(client, world):
    db = TestingSession()
    now = datetime.now(timezone.utc)
    # Manager B's agent: one attempt this month. Must not leak into A's figure.
    _blocked_row(db, world["ag_b"], world["case_b"], now - timedelta(hours=1))
    # Manager A's agent: one attempt LAST month. Must not count this month.
    first = now.replace(day=1)
    _blocked_row(db, world["ag_a"], world["case_a"], first - timedelta(days=2))
    db.commit()

    a = client.get("/api/v1/manager/compliance", headers=_h(world["mgr_a"])).json()
    b = client.get("/api/v1/manager/compliance", headers=_h(world["mgr_b"])).json()
    # A: the one live refusal recorded by test 1 (on case_a, this month) — and
    # NOT B's row, and NOT the previous-month row.
    assert a["blocked_contact_attempts"] == 1
    assert b["blocked_contact_attempts"] == 1
    # The tautological fields still exist for callers that read them, and say 0.
    assert a["out_of_hours_visits"] == 0
    db.close()


def test_compliance_audit_action_coverage_is_observed_data_not_implementation(client, world):
    """The figure is 'ever recorded in this database' and nothing else.

    Two things are pinned. Arithmetic: declared comes from the enum, recorded
    from a DISTINCT over the table, and the two partition the enum. Semantics:
    writing one row for an action that NO CODE PATH writes (SOS_TRIGGERED is
    declared and unwired) must move the figure — which proves it is observed
    data, because an implementation-coverage metric could not move that way.
    The wire carries the semantics as a string so a consumer cannot misread it.
    """
    def cov():
        return client.get("/api/v1/manager/compliance", headers=_h(world["mgr_a"])).json()["audit_actions"]

    c = cov()
    assert c["declared"] == len(list(AuditAction))
    assert c["ever_recorded"] + len(c["never_recorded"]) == c["declared"]
    assert c["never_recorded"] == sorted(c["never_recorded"])
    assert "not implementation coverage" in c["semantics"]
    db = TestingSession()
    in_table = {(r[0].name if hasattr(r[0], "name") else str(r[0]))
                for r in db.query(AuditLog.action).distinct().all()}
    assert c["ever_recorded"] == len(in_table & {a.name for a in AuditAction})
    assert "CONTACT_HOUR_VIOLATION_ATTEMPT" not in c["never_recorded"]   # written by test 1
    assert "SOS_TRIGGERED" in c["never_recorded"]

    # An action nobody writes, written by hand: observed data moves, coverage would not.
    db.add(AuditLog(id=_uid(), created_at=datetime.now(timezone.utc), user_id=world["ag_a"].user_id,
                    action=AuditAction.SOS_TRIGGERED, entity_type="Agent",
                    entity_id=world["ag_a"].id, details={"by": "test"}, success=True))
    db.commit(); db.close()
    after = cov()
    assert after["ever_recorded"] == c["ever_recorded"] + 1
    assert "SOS_TRIGGERED" not in after["never_recorded"]


def test_the_page_metric_does_not_read_the_tautological_fields(world):
    """The frontend must render blocked_contact_attempts, not compliance_rate,
    on the contact-hour tile. Checked textually because the page is not
    mounted in tests; a mount-level test would be better and is not here."""
    page = (BACKEND.parent / "frontend" / "src" / "pages" / "manager"
            / "ManagerCompliancePage.tsx").read_text(encoding="utf-8")
    assert "blocked_contact_attempts" in page
    # and the audit-trail line names its semantics, never claims implementation
    assert "ever been recorded in this database" in page
    assert "not implementation coverage" in page
    # The old literal must not survive as a RENDERED detail. (It is still quoted in
    # the page's changelog comment, deliberately — corrected visibly, not deleted.)
    assert 'detail: "8 of 22' not in page and 'detail: "13 of 25' not in page
    assert "pct(data?.compliance_rate)" not in page
    assert "visits outside 8 AM" not in page


# ═══════════════════════════════════════════════════════════════════════════
# 3. Seed and demo data derive the flag through the canonical helper, in IST
# ═══════════════════════════════════════════════════════════════════════════

SCRIPTS = ["seed_data.py", "demo_record_visits.py", "demo_collect_to_target.py"]


def test_no_seed_or_demo_script_hardcodes_within_contact_hours():
    for name in SCRIPTS:
        src = (BACKEND / "scripts" / name).read_text(encoding="utf-8")
        assert "within_contact_hours=True" not in src, name
        assert "within_contact_hours=False" not in src, name
        assert "is_within_contact_hours" in src, f"{name} does not call the canonical rule"
        assert "tzinfo=IST" in src, f"{name} does not build visit times in IST"


def test_seed_factory_derives_the_flag_from_the_ist_check_in_time():
    import importlib
    seed = importlib.import_module("scripts.seed_data")

    def at(hour, minute=0, tz=IST):
        return datetime.combine(TODAY, datetime.min.time()).replace(hour=hour, minute=minute, tzinfo=tz)

    def flag(when):
        v = seed._visit(id=_uid(), case_id="c", agent_id="a", check_in_latitude=0.0,
                        check_in_longitude=0.0, check_in_time=when, customer_met=True,
                        outcome=VisitOutcome.REVISIT, visit_number=1)
        return v.within_contact_hours

    assert flag(at(9, 30)) is True
    assert flag(at(18, 59)) is True
    assert flag(at(8, 0)) is True           # boundary: 08:00 is inside
    assert flag(at(7, 59)) is False
    assert flag(at(19, 0)) is False         # boundary: 19:00 is outside
    assert flag(at(22, 6)) is False
    # Timezone is honoured, not assumed: 14:00 UTC is 19:30 IST, which is out.
    assert flag(at(14, 0, tz=timezone.utc)) is False
    assert flag(at(3, 0, tz=timezone.utc)) is True   # 08:30 IST
    # And it is the same answer the API would give.
    for when in (at(9), at(19), at(14, 0, tz=timezone.utc)):
        assert flag(when) == is_within_contact_hours(when)


def test_seed_visit_times_are_built_in_ist_not_utc():
    """The seed's hours were written as Indian hours and stamped UTC, which is
    how a 17:30 visit came to be 23:00. The helpers that build visit times
    must now stamp IST. Checked on the two named helpers and by counting."""
    src = (BACKEND / "scripts" / "seed_data.py").read_text(encoding="utf-8")
    import re
    today_at = re.search(r"def today_at\(.*?\n(?:.*\n){0,4}", src).group(0)
    past = re.search(r"def _past\(.*?\n(?:.*\n){0,4}", src).group(0)
    assert "tzinfo=IST" in today_at and "timezone.utc" not in today_at
    assert "tzinfo=IST" in past and "timezone.utc" not in past
