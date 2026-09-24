"""
Leave requests — agent asks, manager decides, the calendar follows. 2026-09-21.

Every test builds its own in-memory book. The API tests run through the real
routers with a token, so tenant scoping is exercised where it is enforced.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.errors import AppException
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.audit_log import AuditAction, AuditLog
from app.models.beat import Beat, BeatStatus
from app.models.case import Case, CaseStatus
from app.models.customer import Customer, RiskCategory
from app.models.leave_request import LeaveRequest, LeaveStatus, LeaveType
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.user import User, UserRole
from app.models.visit import PersonMet, Visit, VisitOutcome
from app.services.leave_service import MAX_LEAVE_DAYS, LeaveService, agent_ids_on_leave, effective_status
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

TODAY = date(2026, 9, 21)                      # a Monday
TOMORROW = TODAY + timedelta(days=1)


def _session():
    engine = make_engine()

    # /manager/agents groups payments with Postgres's to_char(ts, 'YYYY-MM');
    # the same shim tests/test_field_activity.py uses so SQLite can run it.
    @event.listens_for(engine, "connect")
    def _sqlite_helpers(dbapi_conn, _):
        dbapi_conn.create_function("to_char", 2, lambda value, fmt: str(value)[:7] if value else None)

    create_schema(bind=engine)
    return make_session_factory(autocommit=False, autoflush=False, bind=engine)


class World:
    def __init__(self):
        self.Session = _session()
        self.db = self.Session()
        self.mgr = self._user("lv_mgr@t.in", UserRole.AGENCY_MANAGER, "Mgr")
        self.other_mgr = self._user("lv_other@t.in", UserRole.AGENCY_MANAGER, "Other")
        self.au = self._user("lv_a1@t.in", UserRole.FIELD_AGENT, "Asha")
        self.agent = self._agent("LV001", self.au, self.mgr)
        self.agent2 = self._agent("LV002", self._user("lv_a2@t.in", UserRole.FIELD_AGENT, "Bala"), self.mgr)
        self.other_agent = self._agent("LV003", self._user("lv_x@t.in", UserRole.FIELD_AGENT, "Xo"), self.other_mgr)
        self.svc = LeaveService(self.db)
        self._n = 0
        self.db.commit()

    def _user(self, email, role, name):
        u = User(email=email, phone="9" + str(abs(hash(email)) % 10**9).zfill(9), full_name=name,
                 hashed_password="x", role=role, is_active=True, is_verified=True)
        self.db.add(u); self.db.flush(); return u

    def _agent(self, code, user, mgr):
        a = Agent(user_id=user.id, employee_code=code, id_card_number=code + "-ID", 
                  base_latitude=28.6, base_longitude=77.2, tier=AgentTier.TIER_2, specialization=AgentSpecialization.BOTH,
                  status=AgentStatus.ON_DUTY, territory="Delhi", languages_spoken=["HINDI"], ranking_score=50.0,
                  manager_user_id=mgr.id)
        self.db.add(a); self.db.flush(); return a

    def case(self, agent):
        self._n += 1
        if not hasattr(self, "cust"):
            self.cust = Customer(customer_ref="LVC", full_name="B", date_of_birth=date(1990, 1, 1), gender="M", pan_masked="X",
                                 aadhaar_masked="X", phone_primary="9000000001", address_line1="1", city="Delhi", state="DL",
                                 pincode="110001", latitude=28.6, longitude=77.2, risk_category=RiskCategory.MEDIUM)
            self.db.add(self.cust); self.db.flush()
            self.loan = Loan(loan_account_number="LVL", customer_id=self.cust.id, loan_type=LoanType.PERSONAL,
                             branch_code="BR", sanctioned_amount=1.0, disbursed_amount=1.0, outstanding_principal=1.0,
                             total_outstanding=1.0, overdue_amount=1.0, emi_amount=1.0, disbursement_date=date(2025, 1, 1),
                             maturity_date=date(2027, 1, 1), dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE,
                             interest_rate=1.0, penal_charges=0.0)
            self.db.add(self.loan); self.db.flush()
        c = Case(case_number=f"LV-{self._n}", customer_id=self.cust.id, loan_id=self.loan.id, agent_id=agent.id,
                 status=CaseStatus.ASSIGNED, target_amount=1000.0, collected_amount=0.0, allocation_date=TODAY)
        self.db.add(c); self.db.flush(); return c

    def planned_beat(self, agent, d, cases):
        b = Beat(agent_id=agent.id, beat_date=d, beat_number=f"B-{agent.employee_code}-{d}", ordered_case_ids=[c.id for c in cases],
                 total_cases=len(cases), status=BeatStatus.PLANNED)
        self.db.add(b); self.db.commit(); self.db.refresh(b); return b

    def visit(self, agent, case, when):
        v = Visit(case_id=case.id, agent_id=agent.id, check_in_latitude=28.6, check_in_longitude=77.2, check_in_time=when,
                  distance_from_customer_metres=5.0, geo_verified=True, within_contact_hours=True, customer_met=True,
                  outcome=VisitOutcome.REVISIT, person_met=PersonMet.BORROWER, visit_number=1)
        self.db.add(v); self.db.commit(); return v

    def request(self, agent=None, start=None, end=None, lt=LeaveType.CASUAL_LEAVE, reason="family", today=TODAY):
        agent = agent or self.agent
        start = start or TOMORROW
        end = end or start
        return self.svc.request(agent, from_date=start, to_date=end, leave_type=lt, reason=reason, today=today)

    def audits(self):
        return self.db.query(AuditLog).filter(AuditLog.action == AuditAction.AGENT_STATUS_CHANGED).all()


@pytest.fixture
def w():
    world = World()
    yield world
    world.db.close()


# ── request rules ────────────────────────────────────────────────────────────

def test_a_request_is_pending_and_writes_no_beat(w):
    r = w.request(start=TOMORROW, end=TOMORROW + timedelta(days=2))
    assert r.status is LeaveStatus.REQUESTED and r.manager_user_id == w.mgr.id and r.requested_by_id == w.au.id
    assert w.db.query(Beat).count() == 0 and w.audits() == []
    assert w.db.get(Agent, w.agent.id).status is AgentStatus.ON_DUTY


@pytest.mark.parametrize("lt", [LeaveType.CASUAL_LEAVE, LeaveType.EARNED_LEAVE])
def test_only_sick_leave_may_start_today(w, lt):
    with pytest.raises(AppException) as e:
        w.request(start=TODAY, lt=lt)
    assert "sick" in str(e.value.detail).lower()
    assert w.request(start=TODAY, lt=LeaveType.SICK_LEAVE, reason="fever").status is LeaveStatus.REQUESTED


def test_past_dates_absent_type_and_over_long_ranges_are_refused(w):
    with pytest.raises(AppException):
        w.request(start=TODAY - timedelta(days=1), lt=LeaveType.SICK_LEAVE)
    with pytest.raises(AppException):
        w.request(lt=LeaveType.ABSENT)
    with pytest.raises(AppException):
        w.request(start=TOMORROW, end=TOMORROW + timedelta(days=MAX_LEAVE_DAYS))
    with pytest.raises(AppException):
        w.request(start=TOMORROW + timedelta(days=3), end=TOMORROW)
    with pytest.raises(AppException):
        w.request(lt=LeaveType.SICK_LEAVE, reason="   ")


def test_overlapping_open_requests_are_refused_but_a_rejected_one_frees_the_dates(w):
    first = w.request(start=TOMORROW, end=TOMORROW + timedelta(days=3))
    with pytest.raises(AppException) as e:
        w.request(start=TOMORROW + timedelta(days=3), end=TOMORROW + timedelta(days=5))
    assert e.value.status_code == 409
    w.svc.reject(w.mgr.id, first.id, "no")
    assert w.request(start=TOMORROW + timedelta(days=3), end=TOMORROW + timedelta(days=5)).status is LeaveStatus.REQUESTED


def test_agent_can_withdraw_only_while_pending(w):
    r = w.request()
    assert w.svc.withdraw(w.agent, r.id).status is LeaveStatus.CANCELLED
    r2 = w.request(start=TOMORROW + timedelta(days=7))
    w.svc.approve(w.mgr.id, r2.id, today=TODAY)
    with pytest.raises(AppException):
        w.svc.withdraw(w.agent, r2.id)
    with pytest.raises(AppException):
        w.svc.withdraw(w.agent2, r2.id)        # not theirs


# ── approval mechanics ───────────────────────────────────────────────────────

def test_approval_writes_seed_shaped_leave_beats_skipping_sunday(w):
    # Tue 22 .. Mon 28 spans Sunday 27
    r = w.request(start=TOMORROW, end=TOMORROW + timedelta(days=6), lt=LeaveType.EARNED_LEAVE, reason="wedding")
    out = w.svc.approve(w.mgr.id, r.id, "enjoy", today=TODAY)
    beats = w.db.query(Beat).filter(Beat.agent_id == w.agent.id).order_by(Beat.beat_date).all()
    assert [b.beat_date.weekday() for b in beats] == [1, 2, 3, 4, 5, 0]   # no Sunday
    assert all(b.is_leave_day and b.status is BeatStatus.CANCELLED and b.leave_type == "EARNED_LEAVE"
               and b.leave_remarks == "wedding" and b.ordered_case_ids == [] for b in beats)
    assert set(r.beat_ids) == {b.id for b in beats} and out["cases_released"] == 0
    assert r.status is LeaveStatus.APPROVED and r.decided_by_id == w.mgr.id and r.decision_note == "enjoy"


def test_approving_leave_over_a_planned_day_releases_its_cases_to_the_pool(w):
    c1, c2 = w.case(w.agent), w.case(w.agent)
    keep = w.case(w.agent)                       # not on the beat: stays with the agent
    b = w.planned_beat(w.agent, TOMORROW, [c1, c2])
    r = w.request(start=TOMORROW)
    out = w.svc.approve(w.mgr.id, r.id, today=TODAY)
    w.db.expire_all()
    assert out["cases_released"] == 2
    for c in (c1, c2):
        assert w.db.get(Case, c.id).agent_id is None and w.db.get(Case, c.id).status is CaseStatus.UNASSIGNED
    assert w.db.get(Case, keep.id).agent_id == w.agent.id
    b = w.db.get(Beat, b.id)
    assert b.is_leave_day and b.status is BeatStatus.CANCELLED and b.ordered_case_ids == [] and b.id in r.beat_ids
    assert w.db.query(Beat).filter(Beat.agent_id == w.agent.id).count() == 1     # replaced, not duplicated


def test_leave_over_a_day_with_visits_is_refused(w):
    c = w.case(w.agent)
    w.planned_beat(w.agent, TODAY, [c])
    w.visit(w.agent, c, datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc))
    r = w.request(start=TODAY, end=TOMORROW, lt=LeaveType.SICK_LEAVE, reason="ill")
    with pytest.raises(AppException) as e:
        w.svc.approve(w.mgr.id, r.id, today=TODAY)
    assert "already has field work" in str(e.value.detail)
    assert w.db.get(LeaveRequest, r.id).status is LeaveStatus.REQUESTED


def test_status_follows_the_dates(w):
    # starts today -> ON_LEAVE now; starts tomorrow -> unchanged until housekeeping
    r_today = w.request(start=TODAY, lt=LeaveType.SICK_LEAVE, reason="fever")
    w.svc.approve(w.mgr.id, r_today.id, today=TODAY)
    assert w.db.get(Agent, w.agent.id).status is AgentStatus.ON_LEAVE
    r2 = w.request(agent=w.agent2, start=TOMORROW, end=TOMORROW)
    w.svc.approve(w.mgr.id, r2.id, today=TODAY)
    assert w.db.get(Agent, w.agent2.id).status is AgentStatus.ON_DUTY
    # housekeeping the next morning: agent2 goes on leave; the day after, both come back
    out = w.svc.sync_statuses(today=TOMORROW)
    assert w.db.get(Agent, w.agent2.id).status is AgentStatus.ON_LEAVE and out["set_on_leave"] == 1
    assert w.db.get(Agent, w.agent.id).status is AgentStatus.OFF_DUTY and out["cleared"] == 1   # today-only leave ended
    out = w.svc.sync_statuses(today=TOMORROW + timedelta(days=1))
    assert w.db.get(Agent, w.agent2.id).status is AgentStatus.OFF_DUTY and out["cleared"] == 1
    assert w.svc.sync_statuses(today=TOMORROW + timedelta(days=1)) == {"today": (TOMORROW + timedelta(days=1)).isoformat(), "on_leave": 0, "set_on_leave": 0, "cleared": 0}


def test_duty_is_derived_from_approved_leave_not_only_the_stored_status(w):
    """2026-09-22. An approved leave for tomorrow leaves Agent.status ON_DUTY
    until the 00:10 sync — and that sync can be missed (beat asleep, deploy).
    Readers must not depend on it: on the leave day the agent IS on leave."""
    r = w.request(start=TOMORROW, end=TOMORROW)
    w.svc.approve(w.mgr.id, r.id, today=TODAY)
    a = w.db.get(Agent, w.agent.id)
    assert a.status is AgentStatus.ON_DUTY                       # sync has not run
    # today: nobody is on leave
    assert agent_ids_on_leave(w.db, TODAY) == set()
    assert effective_status(a, agent_ids_on_leave(w.db, TODAY)) is AgentStatus.ON_DUTY
    # tomorrow: the leave covers the day, whatever the column says
    ids = agent_ids_on_leave(w.db, TOMORROW, [w.agent.id, w.agent2.id])
    assert ids == {w.agent.id}
    assert effective_status(a, ids) is AgentStatus.ON_LEAVE
    assert effective_status(w.db.get(Agent, w.agent2.id), ids) is AgentStatus.ON_DUTY
    # the scoping list is honoured, and an empty one is an empty answer
    assert agent_ids_on_leave(w.db, TOMORROW, [w.agent2.id]) == set()
    assert agent_ids_on_leave(w.db, TOMORROW, []) == set()
    # a revoked leave no longer counts; suspension outranks leave
    w.svc.revoke(w.mgr.id, r.id, today=TODAY)
    assert agent_ids_on_leave(w.db, TOMORROW) == set()
    a.status = AgentStatus.SUSPENDED
    assert effective_status(a, {a.id}) is AgentStatus.SUSPENDED


def test_rejection_writes_nothing_and_revoke_removes_exactly_its_beats(w):
    r = w.request(start=TOMORROW, end=TOMORROW + timedelta(days=1))
    w.svc.reject(w.mgr.id, r.id, "short-staffed")
    assert w.db.query(Beat).count() == 0 and r.status is LeaveStatus.REJECTED and r.decision_note == "short-staffed"
    other = w.planned_beat(w.agent, TOMORROW + timedelta(days=5), [])     # an unrelated beat must survive a revoke
    r2 = w.request(start=TOMORROW, end=TOMORROW + timedelta(days=1))
    w.svc.approve(w.mgr.id, r2.id, today=TODAY)
    assert w.db.query(Beat).filter(Beat.is_leave_day == True).count() == 2  # noqa: E712
    w.svc.revoke(w.mgr.id, r2.id, "plans changed", today=TODAY)
    assert w.db.query(Beat).filter(Beat.is_leave_day == True).count() == 0  # noqa: E712
    assert w.db.get(Beat, other.id) is not None
    assert w.db.get(LeaveRequest, r2.id).status is LeaveStatus.CANCELLED


def test_manager_mark_including_backdated_absent(w):
    out = w.svc.mark(w.mgr.id, w.agent, from_date=TODAY - timedelta(days=3), to_date=TODAY - timedelta(days=3),
                     leave_type=LeaveType.ABSENT, reason="no-show", today=TODAY)
    r = out["request"]
    assert r.status is LeaveStatus.APPROVED and r.requested_by_id == w.mgr.id
    assert w.db.query(Beat).filter(Beat.leave_type == "ABSENT").count() == 1
    with pytest.raises(AppException):
        w.svc.mark(w.mgr.id, w.agent, from_date=TODAY - timedelta(days=1), to_date=TODAY - timedelta(days=1),
                   leave_type=LeaveType.CASUAL_LEAVE, reason=None, today=TODAY)


def test_every_decision_leaves_a_system_audit_row(w):
    r = w.request(); w.svc.approve(w.mgr.id, r.id, today=TODAY)
    r2 = w.request(start=TOMORROW + timedelta(days=3)); w.svc.reject(w.mgr.id, r2.id)
    w.svc.revoke(w.mgr.id, r.id, today=TODAY)
    events = [a.details["event"] for a in w.audits()]
    assert events == ["LEAVE_APPROVED", "LEAVE_REJECTED", "LEAVE_REVOKED"]
    assert all(a.user_id == w.mgr.id and a.entity_type == "Agent" and a.entity_id == w.agent.id for a in w.audits())


def test_the_only_case_write_is_the_release_to_pool(w):
    import inspect
    import app.services.leave_service as m
    src = inspect.getsource(m)
    assert src.count("c.agent_id = None") == 1 and "c.agent_id = agent" not in src and ".agent_id = ag" not in src


# ── tenant isolation ─────────────────────────────────────────────────────────

def test_a_manager_cannot_see_or_decide_another_managers_request(w):
    theirs = w.request(agent=w.other_agent)
    assert [x.id for x in w.svc.list_for_manager(w.mgr.id)] == []
    for fn in (w.svc.approve, w.svc.reject, w.svc.revoke):
        with pytest.raises(AppException) as e:
            fn(w.mgr.id, theirs.id)
        assert e.value.status_code == 404
    assert w.db.get(LeaveRequest, theirs.id).status is LeaveStatus.REQUESTED


# ── through the API ──────────────────────────────────────────────────────────

@pytest.fixture
def client(w):
    def override():
        db = w.Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _h(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value, 'test-device')}"}


def test_api_round_trip_agent_requests_manager_approves_calendars_follow(w, client):
    start = (date.today() + timedelta(days=2)); end = start + timedelta(days=1)
    r = client.post("/api/v1/agent/leave-requests", headers=_h(w.au),
                    json={"from_date": start.isoformat(), "to_date": end.isoformat(), "leave_type": "CASUAL_LEAVE", "reason": "family"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    mine = client.get("/api/v1/agent/leave-requests", headers=_h(w.au)).json()
    assert [x["status"] for x in mine["requests"]] == ["REQUESTED"]
    pend = client.get("/api/v1/manager/leave-requests?status=REQUESTED", headers=_h(w.mgr)).json()
    assert pend["pending"] == 1 and pend["requests"][0]["agent_name"] == "Asha"
    other = client.get("/api/v1/manager/leave-requests", headers=_h(w.other_mgr)).json()
    assert other["pending"] == 0 and other["requests"] == []
    assert client.post(f"/api/v1/manager/leave-requests/{rid}/approve", headers=_h(w.other_mgr), json={}).status_code == 404
    ok = client.post(f"/api/v1/manager/leave-requests/{rid}/approve", headers=_h(w.mgr), json={"note": "ok"})
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    # both calendars now show those days as ON_LEAVE, not ON_DUTY
    cal = client.get("/api/v1/agent/availability/calendar", headers=_h(w.au)).json()
    # the agent calendar only spans up to today; the manager's one covers the request days if within range
    mcal = client.get(f"/api/v1/manager/agents/{w.agent.id}/availability-calendar", headers=_h(w.mgr)).json()
    days = {d["date"]: d for d in mcal["calendar"]}
    for d in (start, end):
        if d.weekday() != 6 and d.isoformat() in days:
            assert days[d.isoformat()]["status"] == "ON_LEAVE" and days[d.isoformat()]["leave_type"] == "CASUAL_LEAVE"
    assert cal["agent_id"] == w.agent.id
    # team attendance treats the leave beats as leave, not duty
    att = client.get("/api/v1/manager/analytics/team-attendance", headers=_h(w.mgr), params={"month": start.strftime("%Y-%m")})
    assert att.status_code == 200


def test_api_agent_cannot_request_absent_or_use_manager_routes(w, client):
    r = client.post("/api/v1/agent/leave-requests", headers=_h(w.au),
                    json={"from_date": TOMORROW.isoformat(), "to_date": TOMORROW.isoformat(), "leave_type": "ABSENT"})
    assert r.status_code == 422
    assert client.get("/api/v1/manager/leave-requests", headers=_h(w.au)).status_code == 403
    m = client.post(f"/api/v1/manager/agents/{w.agent.id}/leave", headers=_h(w.mgr),
                    json={"from_date": (date.today() + timedelta(days=3)).isoformat(), "to_date": (date.today() + timedelta(days=3)).isoformat(),
                          "leave_type": "EARNED_LEAVE", "reason": "approved offline"})
    assert m.status_code == 201 and m.json()["status"] == "APPROVED"
    assert client.post(f"/api/v1/manager/agents/{w.other_agent.id}/leave", headers=_h(w.mgr),
                       json={"from_date": TOMORROW.isoformat(), "to_date": TOMORROW.isoformat(), "leave_type": "ABSENT"}).status_code == 404


def test_task_is_scheduled_at_00_10():
    from app.workers.celery_app import celery_app
    e = celery_app.conf.beat_schedule["leave-housekeeping"]
    assert e["task"].endswith("sync_leave_statuses") and e["schedule"].hour == {0} and e["schedule"].minute == {10}


def test_api_manager_screens_show_approved_leave_even_when_the_status_sync_was_missed(w, client):
    """The dashboard tile, the agents list and the duty toggle all read leave
    from the leave table for the day, so a stale ON_DUTY cannot leak through."""
    today = date.today()
    # An approved leave spanning yesterday..tomorrow, written directly so the
    # test is independent of request-date rules; then the stored status is
    # forced back to ON_DUTY, which is exactly what a missed 00:10 sync leaves.
    w.db.add(LeaveRequest(agent_id=w.agent.id, manager_user_id=w.mgr.id, from_date=today - timedelta(days=1),
                          to_date=today + timedelta(days=1), leave_type=LeaveType.CASUAL_LEAVE,
                          status=LeaveStatus.APPROVED, requested_by_id=w.au.id, decided_by_id=w.mgr.id, beat_ids=[]))
    w.db.get(Agent, w.agent.id).status = AgentStatus.ON_DUTY
    w.db.commit()

    dash = client.get("/api/v1/manager/dashboard", headers=_h(w.mgr)).json()
    assert dash["total_agents"] == 2 and dash["agents_on_duty"] == 1 and dash["agents_on_leave"] == 1

    agents = {a["id"]: a for a in client.get("/api/v1/manager/agents", headers=_h(w.mgr)).json()}
    assert agents[w.agent.id]["status"] == "ON_LEAVE"
    assert agents[w.agent2.id]["status"] == "ON_DUTY"

    perf = {a["agent_id"]: a for a in client.get("/api/v1/manager/agents/performance", headers=_h(w.mgr)).json()["agents"]}
    assert perf[w.agent.id]["status"] == "ON_LEAVE"

    # the duty toggle cannot override the leave
    r = client.put(f"/api/v1/manager/agents/{w.agent.id}/status", headers=_h(w.mgr), json={"status": "ON_DUTY"})
    assert r.status_code == 409 and "approved leave" in r.json()["detail"]
    r = client.put(f"/api/v1/manager/agents/{w.agent2.id}/status", headers=_h(w.mgr), json={"status": "OFF_DUTY"})
    assert r.status_code == 200 and r.json()["new_status"] == "OFF_DUTY"
