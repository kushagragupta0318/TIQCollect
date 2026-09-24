"""
PTP lifecycle — a promise is resolved by the calendar, one grace day after
its date, from verified money only, and the trail says the system did it.

Every test builds its own in-memory book so no ordering can leak between
them. Numbers in test names follow the 2026-09-17 brief.
"""
from __future__ import annotations

import re
import pathlib
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.audit_log import AuditAction, AuditLog
from app.models.case import Case, CaseStatus
from app.models.customer import Customer, RiskCategory
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.models.user import User, UserRole
from app.models.visit import PersonMet, Visit, VisitOutcome
from app.services import ptp_lifecycle_service as svc
from app.services.ptp_lifecycle_service import (
    GRACE_DAYS, REASON_GRACE_EXPIRED, SOURCE_BACKFILL, SOURCE_NIGHTLY, process_scope,
)
from app.workers.tasks.ptp_lifecycle import run_for_all_managers
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

EFF = date(2026, 9, 17)                      # the business date every test judges against
D_TODAY, D_YESTERDAY, D_ELIGIBLE = EFF, EFF - timedelta(days=1), EFF - timedelta(days=2)


# ── a tiny book ──────────────────────────────────────────────────────────────

def _at(d: date, hour=12, minute=0):
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc)


def _session():
    engine = make_engine()
    create_schema(bind=engine)
    return make_session_factory(autocommit=False, autoflush=False, bind=engine)


class World:
    def __init__(self):
        self.Session = _session()
        self.db = self.Session()
        db = self.db
        self.mgr = self._user("mgr@t.in", UserRole.AGENCY_MANAGER, "Mgr")
        self.other_mgr = self._user("other@t.in", UserRole.AGENCY_MANAGER, "Other")
        self.agent = self._agent("A001", self._user("a1@t.in", UserRole.FIELD_AGENT, "A1"), self.mgr)
        self.agent2 = self._agent("A002", self._user("a2@t.in", UserRole.FIELD_AGENT, "A2"), self.mgr)
        self.other_agent = self._agent("B001", self._user("b1@t.in", UserRole.FIELD_AGENT, "B1"), self.other_mgr)
        self.cust = Customer(customer_ref="C1", full_name="B", date_of_birth="1990-01-01", gender="M",
                             pan_masked="X", aadhaar_masked="X", phone_primary="9000000001", address_line1="1",
                             city="Delhi", state="DL", pincode="110001", latitude=28.6, longitude=77.2,
                             risk_category=RiskCategory.MEDIUM, cibil_score=650)
        db.add(self.cust); db.flush()
        self.loan = Loan(loan_account_number="L1", customer_id=self.cust.id, loan_type=LoanType.PERSONAL,
                         bank_name="B", branch_code="BR", sanctioned_amount=100000.0, disbursed_amount=100000.0,
                         outstanding_principal=50000.0, total_outstanding=52000.0, overdue_amount=12000.0,
                         emi_amount=4000.0, disbursement_date="2025-01-01", maturity_date="2027-01-01", dpd=45,
                         dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE, interest_rate=12.0, penal_charges=0.0)
        db.add(self.loan); db.flush()
        self._n = 0
        db.commit()

    def _user(self, email, role, name):
        u = User(email=email, phone="9" + str(abs(hash(email)) % 10**9).zfill(9), full_name=name,
                 hashed_password="x", role=role, is_active=True, is_verified=True)
        self.db.add(u); self.db.flush(); return u

    def _agent(self, code, user, mgr):
        a = Agent(user_id=user.id, employee_code=code, id_card_number=code + "-ID", agency_id="AG1",
                  base_latitude=28.6, base_longitude=77.2, tier=AgentTier.TIER_1,
                  specialization=AgentSpecialization.BOTH, status=AgentStatus.ON_DUTY,
                  territory="Delhi", languages_spoken=["HINDI"], ranking_score=80.0, manager_user_id=mgr.id)
        self.db.add(a); self.db.flush(); return a

    def case(self, agent=None, status=CaseStatus.PTP_SET):
        self._n += 1
        c = Case(case_number=f"CASE{self._n:04d}", customer_id=self.cust.id, loan_id=self.loan.id,
                 agent_id=(agent or self.agent).id, status=status, target_amount=20000.0, collected_amount=0.0,
                 allocation_date=EFF.isoformat())
        self.db.add(c); self.db.flush(); return c

    def visit(self, case, when, outcome=VisitOutcome.PTP, agent=None):
        """The doorstep event a promise is taken at. `when` is check-in."""
        self._n += 1
        v = Visit(case_id=case.id, agent_id=(agent.id if agent else case.agent_id),
                  check_in_latitude=28.6, check_in_longitude=77.2, check_in_time=when,
                  distance_from_customer_metres=10.0, geo_verified=True, within_contact_hours=True,
                  customer_met=True, outcome=outcome, person_met=PersonMet.BORROWER, visit_number=self._n)
        self.db.add(v); self.db.commit(); self.db.refresh(v); return v

    def ptp(self, case, due, amount=1000.0, status=PTPStatus.ACTIVE, agent=None, parent=None,
            created=None, visit=None):
        """A promise. Unless told otherwise it was MADE ten days before it is
        due (created_at), with no taking visit — the plainest origin. Pass
        `visit=` to anchor it on a doorstep, `created=` to move the fallback."""
        p = PTP(case_id=case.id, agent_id=agent.id if agent else case.agent_id,
                committed_amount=amount, committed_date=due, status=status,
                visit_id=visit.id if visit else None,
                parent_ptp_id=parent.id if parent else None, reschedule_count=1 if parent else 0)
        p.created_at = created if created is not None else _at(due - timedelta(days=10), 9)
        self.db.add(p); self.db.commit(); self.db.refresh(p); return p

    def pay(self, case, amount, when, status=PaymentStatus.VERIFIED, agent=None):
        self._n += 1
        p = Payment(case_id=case.id, agent_id=(agent.id if agent else case.agent_id), amount=amount, mode=PaymentMode.UPI,
                    status=status, receipt_number=f"TIQ-T-{self._n:06d}", payment_date=when)
        self.db.add(p); self.db.commit(); return p

    def run(self, *, agent_ids=None, effective=EFF, dry_run=False, source=SOURCE_NIGHTLY, processed_at=None):
        ids = agent_ids if agent_ids is not None else [self.agent.id, self.agent2.id]
        return process_scope(self.db, agent_ids=ids, effective_date=effective, source=source,
                             dry_run=dry_run, processed_at=processed_at)

    def status(self, p):
        self.db.refresh(p); return p.status

    def audits(self, p=None):
        q = self.db.query(AuditLog).filter(AuditLog.action == AuditAction.PTP_UPDATED)
        if p is not None:
            q = q.filter(AuditLog.entity_id == p.id)
        return q.all()


@pytest.fixture
def w():
    world = World()
    yield world
    world.db.close()


# ── 1-3: the grace rule, in calendar days ────────────────────────────────────

def test_1_promise_due_today_stays_active(w):
    p = w.ptp(w.case(), D_TODAY)
    s = w.run()
    assert w.status(p) is PTPStatus.ACTIVE and s.eligible == 0 and s.grace_period_skipped == 0


def test_2_promise_due_yesterday_is_inside_the_grace_day_and_stays_active(w):
    p = w.ptp(w.case(), D_YESTERDAY)
    s = w.run()
    assert w.status(p) is PTPStatus.ACTIVE
    assert s.eligible == 0 and s.grace_period_skipped == 1


def test_3_promise_due_two_or_more_days_ago_is_eligible(w):
    p2 = w.ptp(w.case(), D_ELIGIBLE)
    p40 = w.ptp(w.case(), EFF - timedelta(days=40))
    s = w.run()
    assert s.eligible == 2
    assert w.status(p2) is not PTPStatus.ACTIVE and w.status(p40) is not PTPStatus.ACTIVE


def test_exact_rule_is_committed_date_lte_effective_minus_two_days():
    assert GRACE_DAYS == 1
    assert svc.eligibility_cutoff(EFF) == EFF - timedelta(days=2)
    assert svc.is_eligible(EFF - timedelta(days=2), EFF)
    assert not svc.is_eligible(EFF - timedelta(days=1), EFF)
    assert not svc.is_eligible(EFF, EFF)


# ── 4-6: the outcome, from verified money ────────────────────────────────────

def test_4_no_verified_payment_becomes_broken(w):
    p = w.ptp(w.case(), D_ELIGIBLE, amount=5000.0)
    s = w.run()
    assert w.status(p) is PTPStatus.BROKEN and s.broken == 1
    assert p.actual_paid_amount == 0.0


def test_5_partial_verified_payment_becomes_partially_honored(w):
    c = w.case(); p = w.ptp(c, D_ELIGIBLE, amount=5000.0)
    w.pay(c, 2000.0, _at(D_ELIGIBLE))
    s = w.run()
    assert w.status(p) is PTPStatus.PARTIALLY_HONORED and s.partially_honored == 1
    assert p.actual_paid_amount == 2000.0


def test_6_full_verified_payment_becomes_honored(w):
    c = w.case(); p = w.ptp(c, D_ELIGIBLE, amount=5000.0)
    w.pay(c, 3000.0, _at(D_ELIGIBLE - timedelta(days=1)))
    w.pay(c, 2000.0, _at(D_ELIGIBLE))
    s = w.run()
    assert w.status(p) is PTPStatus.HONORED and s.honored == 1
    assert p.actual_paid_amount == 5000.0


def test_6b_a_payment_on_the_grace_day_counts_and_one_after_it_does_not(w):
    c1 = w.case(); on_grace = w.ptp(c1, D_ELIGIBLE, amount=1000.0)
    w.pay(c1, 1000.0, _at(D_ELIGIBLE + timedelta(days=1), 23, 59))       # the grace day, late
    c2 = w.case(); after_grace = w.ptp(c2, D_ELIGIBLE, amount=1000.0)
    w.pay(c2, 1000.0, _at(D_ELIGIBLE + timedelta(days=2), 0, 1))         # the morning after
    w.run()
    assert w.status(on_grace) is PTPStatus.HONORED
    assert w.status(after_grace) is PTPStatus.BROKEN


# ── 7: rescheduled promises ──────────────────────────────────────────────────

def test_7_rescheduled_promise_is_left_alone_and_its_replacement_is_judged_on_its_own_date(w):
    c = w.case()
    old = w.ptp(c, EFF - timedelta(days=10), status=PTPStatus.RESCHEDULED)
    new_future = w.ptp(c, EFF + timedelta(days=5), parent=old)             # replacement, not yet due
    old2 = w.ptp(w.case(), EFF - timedelta(days=10), status=PTPStatus.RESCHEDULED)
    new_overdue = w.ptp(old2.case, D_ELIGIBLE, parent=old2)                # replacement, past its own grace
    s = w.run()
    assert w.status(old) is PTPStatus.RESCHEDULED and w.status(old2) is PTPStatus.RESCHEDULED
    assert w.status(new_future) is PTPStatus.ACTIVE
    assert w.status(new_overdue) is PTPStatus.BROKEN
    assert s.rescheduled_skipped == 2 and s.eligible == 1
    assert w.audits(old) == [] and w.audits(old2) == []


# ── 8: unverified money is not money ─────────────────────────────────────────

@pytest.mark.parametrize("st", [PaymentStatus.PENDING_VERIFICATION, PaymentStatus.REJECTED, PaymentStatus.REVERSED])
def test_8_unverified_payment_does_not_honour_or_partially_honour(w, st):
    c = w.case(); p = w.ptp(c, D_ELIGIBLE, amount=1000.0)
    w.pay(c, 1000.0, _at(D_ELIGIBLE), status=st)
    w.run()
    assert w.status(p) is PTPStatus.BROKEN and p.actual_paid_amount == 0.0


def test_8b_money_by_a_different_agent_on_the_case_is_not_this_promise_s_money(w):
    # The honouring path has always summed the SAME agent's payments; the
    # lifecycle uses the same definition, so it cannot disagree with it.
    c = w.case(); p = w.ptp(c, D_ELIGIBLE, amount=1000.0)
    w.pay(c, 1000.0, _at(D_ELIGIBLE), agent=w.agent2)
    w.run()
    assert w.status(p) is PTPStatus.BROKEN


# ── 9-10: terminal rows and idempotency ──────────────────────────────────────

@pytest.mark.parametrize("st", [PTPStatus.HONORED, PTPStatus.BROKEN, PTPStatus.PARTIALLY_HONORED, PTPStatus.EXPIRED])
def test_9_already_terminal_promise_is_unchanged(w, st):
    p = w.ptp(w.case(), EFF - timedelta(days=30), status=st)
    s = w.run()
    assert w.status(p) is st and s.eligible == 0 and w.audits(p) == []


def test_10_running_twice_changes_nothing_the_second_time(w):
    c = w.case(); a = w.ptp(c, D_ELIGIBLE); b = w.ptp(c, EFF - timedelta(days=9), amount=500.0)
    w.pay(c, 200.0, _at(EFF - timedelta(days=9)))
    first = w.run()
    audits_after_first = len(w.audits())
    statuses = {p.id: w.status(p) for p in (a, b)}
    second = w.run()
    assert first.eligible == 2 and second.eligible == 0
    assert (second.honored, second.partially_honored, second.broken) == (0, 0, 0)
    assert {p.id: w.status(p) for p in (a, b)} == statuses
    assert len(w.audits()) == audits_after_first


# ── 11: the audit trail ──────────────────────────────────────────────────────

def test_11_every_transition_leaves_one_system_audit_row_with_the_facts(w):
    c = w.case(); p = w.ptp(c, D_ELIGIBLE, amount=4000.0)
    w.pay(c, 1500.0, _at(D_ELIGIBLE))
    at = _at(EFF, 0, 5)
    s = w.run(processed_at=at)
    rows = w.audits(p)
    assert len(rows) == 1
    r = rows[0]
    assert r.user_id is None and r.entity_type == "PTP" and r.entity_id == p.id and r.success is True
    assert r.created_at.replace(tzinfo=timezone.utc) == at
    d = r.details
    assert d["actor"] == "SYSTEM" and d["source"] == SOURCE_NIGHTLY
    assert d["reason"] == REASON_GRACE_EXPIRED
    assert (d["from"], d["to"]) == ("ACTIVE", "PARTIALLY_HONORED")
    assert d["committed_date"] == D_ELIGIBLE.isoformat() and d["committed_amount"] == 4000.0
    assert d["verified_paid_through_grace"] == 1500.0 and d["grace_days"] == 1
    assert d["effective_date"] == EFF.isoformat() and d["processed_at"] == at.isoformat()
    assert s.transitions[0].audit_log_id == r.id


def test_11b_a_dry_run_writes_nothing(w):
    c = w.case(); p = w.ptp(c, D_ELIGIBLE)
    s = w.run(dry_run=True)
    assert s.eligible == 1 and s.broken == 1 and s.transitions[0].new_status == "BROKEN"
    assert w.status(p) is PTPStatus.ACTIVE and w.audits() == []
    assert s.transitions[0].audit_log_id is None


# ── 12-13: the backfill ──────────────────────────────────────────────────────

def test_12_backfill_uses_the_lifecycle_function_and_carries_no_rule_of_its_own():
    src = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ptp_lifecycle_backfill.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    assert "run_for_all_managers" in code
    # No status is decided in the script: the only PTPStatus it constructs is
    # the one it restores from the manifest on --undo.
    assert not re.search(r"PTPStatus\.(BROKEN|HONORED|PARTIALLY_HONORED|EXPIRED)", code)
    assert "GRACE_DAYS" not in code and "eligibility_cutoff" not in code
    assert not re.search(r"committed_date\s*[<>]", code), "the script must not restate the grace rule"
    # And it never deletes audit rows.
    assert not re.search(r"query\(AuditLog\)[^\n]*\.delete\(|delete\(AuditLog\)|DELETE FROM audit_logs", code)


def test_12b_the_task_runner_produces_the_same_transitions_as_the_service_it_wraps(w):
    w.ptp(w.case(), D_ELIGIBLE)
    c = w.case(); w.ptp(c, EFF - timedelta(days=5), amount=100.0)
    w.pay(c, 100.0, _at(EFF - timedelta(days=5)))
    dry = run_for_all_managers(w.db, EFF, source=SOURCE_BACKFILL, dry_run=True)
    applied = run_for_all_managers(w.db, EFF, source=SOURCE_BACKFILL, dry_run=False)
    key = lambda r: sorted((t.ptp_id, t.new_status) for pm in r["per_manager"] for t in pm["transitions"])  # noqa: E731
    assert key(dry) == key(applied)
    assert (dry["eligible"], dry["honored"], dry["broken"]) == (2, 1, 1)
    assert {d["source"] for d in (a.details for a in w.audits())} == {SOURCE_BACKFILL}


def test_13_backfill_and_task_never_touch_another_manager_s_promises(w):
    mine = w.ptp(w.case(), D_ELIGIBLE)
    theirs = w.ptp(w.case(agent=w.other_agent), D_ELIGIBLE)
    # A scoped call for one manager leaves the other's row untouched...
    s = w.run(agent_ids=[w.agent.id, w.agent2.id])
    assert s.eligible == 1 and w.status(mine) is PTPStatus.BROKEN and w.status(theirs) is PTPStatus.ACTIVE
    # ...and the per-manager runner attributes each promise to its own manager.
    theirs2 = w.ptp(w.case(agent=w.other_agent), D_ELIGIBLE)
    res = run_for_all_managers(w.db, EFF, source=SOURCE_NIGHTLY)
    by_mgr = {pm["manager_user_id"]: pm for pm in res["per_manager"]}
    assert by_mgr[w.mgr.id]["eligible"] == 0
    assert by_mgr[w.other_mgr.id]["eligible"] == 2
    assert {t.ptp_id for t in by_mgr[w.other_mgr.id]["transitions"]} == {theirs.id, theirs2.id}


def test_13b_the_service_refuses_to_run_unscoped(w):
    p = w.ptp(w.case(), D_ELIGIBLE)
    s = process_scope(w.db, agent_ids=[], effective_date=EFF, source=SOURCE_NIGHTLY)
    assert s.eligible == 0 and w.status(p) is PTPStatus.ACTIVE


def test_13c_an_agent_with_no_manager_is_counted_not_processed(w):
    w.other_agent.manager_user_id = None; w.db.commit()
    orphan = w.ptp(w.case(agent=w.other_agent), D_ELIGIBLE)
    res = run_for_all_managers(w.db, EFF, source=SOURCE_NIGHTLY)
    assert res["orphan_agent_overdue_skipped"] == 1 and w.status(orphan) is PTPStatus.ACTIVE


# ── 14-15: calendar dates, not hours; the midnight boundary ──────────────────

def test_14_grace_is_calendar_days_not_elapsed_hours(w):
    # Due on the 15th; at 00:05 on the 17th only ~24 hours have elapsed since
    # the 15th ended, yet two CALENDAR days have — and it is eligible.
    p = w.ptp(w.case(), EFF - timedelta(days=2))
    s = w.run(processed_at=_at(EFF, 0, 5))
    assert s.eligible == 1 and w.status(p) is PTPStatus.BROKEN
    # Due on the 16th: at 23:59 on the 17th nearly 48 hours have elapsed since
    # the 16th began, but only one calendar day has — still inside the grace.
    q = w.ptp(w.case(), EFF - timedelta(days=1))
    s2 = w.run(processed_at=_at(EFF, 23, 59))
    assert s2.eligible == 0 and w.status(q) is PTPStatus.ACTIVE


def test_15_the_boundary_flips_exactly_when_the_effective_date_turns(w):
    p = w.ptp(w.case(), D_ELIGIBLE)
    s_before = w.run(effective=EFF - timedelta(days=1))          # D+1: the grace day
    assert s_before.eligible == 0 and s_before.grace_period_skipped == 1 and w.status(p) is PTPStatus.ACTIVE
    s_on = w.run(effective=EFF)                                   # D+2 at 00:05
    assert s_on.eligible == 1 and w.status(p) is PTPStatus.BROKEN


# ── 16: the payment path is unchanged ────────────────────────────────────────

def test_16_payment_service_still_honours_a_promise_paid_by_its_date(w):
    from app.services.payment_service import PaymentService
    c = w.case(); p = w.ptp(c, EFF + timedelta(days=3), amount=1000.0)
    w.pay(c, 1000.0, _at(EFF))
    PaymentService(w.db)._honor_active_ptps_paid_by_due_date(c.id, c.agent_id, _at(EFF))
    w.db.commit()
    assert w.status(p) is PTPStatus.HONORED and p.actual_paid_amount == 1000.0
    row = w.audits(p)[0]
    assert row.details["reason"] == "VERIFIED_PAYMENT_BY_COMMITTED_DATE"
    # and a partial payment still leaves it ACTIVE for the lifecycle to judge later
    c2 = w.case(); q = w.ptp(c2, EFF + timedelta(days=3), amount=1000.0)
    w.pay(c2, 400.0, _at(EFF))
    PaymentService(w.db)._honor_active_ptps_paid_by_due_date(c2.id, c2.agent_id, _at(EFF))
    w.db.commit()
    assert w.status(q) is PTPStatus.ACTIVE


def test_16b_one_definition_of_money_against_a_promise():
    src = pathlib.Path("app/services/payment_service.py").read_text(encoding="utf-8")
    assert "verified_paid_against(" in src
    assert "func.sum(Payment.amount)" not in src, "payment_service restated the sum the lifecycle service owns"


# ── 17-19: the consumers see the corrected status ────────────────────────────

def test_17_allocator_ptp_fatigue_gate_now_trips_on_calendar_broken_promises(w):
    from app.services.planner_service import PlannerService
    c = w.case()
    for d in (30, 20, 10):
        w.ptp(c, EFF - timedelta(days=d))
    planner = PlannerService(w.db, manager_user_id=w.mgr.id)
    assert planner._ptp_fatigue_map([c.id]) == {}          # three ACTIVE, overdue: invisible before
    w.run()
    assert planner._ptp_fatigue_map([c.id]) == {c.id: {w.agent.id}}


def test_18_ptp_kept_ratio_reads_the_corrected_statuses(w):
    from app.services.ml_scoring_service import MLScoringService
    c = w.case(); c2 = w.case()          # two cases on the loan; the feature spans both
    created = datetime(2026, 8, 1, tzinfo=timezone.utc)
    a = w.ptp(c, EFF - timedelta(days=5), amount=1000.0, created=created)     # partly paid -> kept
    b = w.ptp(c2, EFF - timedelta(days=4), amount=1000.0, created=created)    # unpaid -> broken
    w.pay(c, 300.0, _at(EFF - timedelta(days=5)))
    svc_ml = MLScoringService(w.db)
    before = svc_ml.build_features(w.loan, as_of=EFF)
    assert before["ptp_set_6m"] == 2.0 and before["ptp_kept_6m"] == 0.0 and before["ptp_kept_ratio"] == 0.0
    assert before["ptp_broken_6m"] == 0.0
    w.run()
    w.db.expire_all()
    after = svc_ml.build_features(w.loan, as_of=EFF)
    assert (w.status(a), w.status(b)) == (PTPStatus.PARTIALLY_HONORED, PTPStatus.BROKEN)
    assert after["ptp_set_6m"] == 2.0 and after["ptp_kept_6m"] == 1.0 and after["ptp_kept_ratio"] == 0.5
    assert after["ptp_broken_6m"] == 1.0


def test_19_reminder_and_compliance_queries_no_longer_see_a_resolved_promise_as_active(w):
    from sqlalchemy import func
    c = w.case(); p = w.ptp(c, D_ELIGIBLE)
    w.run()
    # ptp_reminders: ACTIVE, not yet reminded, due on the run date
    reminders = w.db.query(PTP).filter(PTP.status == PTPStatus.ACTIVE, PTP.reminder_sent == False).all()  # noqa: E712
    assert p.id not in {r.id for r in reminders}
    # the compliance PTP-risk view: broken promises in the last 90 days, per case
    broken = dict(
        w.db.query(PTP.case_id, func.count(PTP.id))
        .filter(PTP.case_id.in_([c.id]), PTP.status == PTPStatus.BROKEN, PTP.committed_date >= EFF - timedelta(days=90))
        .group_by(PTP.case_id).all()
    )
    assert broken == {c.id: 1}


def test_19b_promises_card_kept_rate_counts_the_resolved_promise(w):
    c = w.case()
    w.ptp(c, EFF - timedelta(days=20), status=PTPStatus.HONORED)
    broken_soon = w.ptp(c, D_ELIGIBLE)
    # Relative to TODAY, not EFF: this test runs the lifecycle at date.today()
    # (the API judges "due" against the real calendar), so a promise pinned to
    # EFF + 3 stopped being "upcoming" on 2026-09-21 and the test failed every
    # day after. Found 2026-09-22.
    upcoming = w.ptp(c, max(EFF, date.today()) + timedelta(days=3))

    def override():
        db = w.Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as client:
            h = {"Authorization": f"Bearer {create_access_token(w.mgr.id, w.mgr.role.value, 'test-device')}"}
            before = client.get("/api/v1/manager/dashboard", headers=h).json()["ptp_health"]
            w.run(effective=date.today())     # the API judges "due" against today's calendar
            after = client.get("/api/v1/manager/dashboard", headers=h).json()["ptp_health"]
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert before["kept_rate_pct"] == 100.0 and before["active"] == 2
    assert w.status(broken_soon) is PTPStatus.BROKEN and w.status(upcoming) is PTPStatus.ACTIVE
    assert after["honored"] == 1 and after["broken"] == 1 and after["kept_rate_pct"] == 50.0
    assert after["active"] == 1


# ── the schedule ─────────────────────────────────────────────────────────────

def test_the_task_is_scheduled_at_00_05_daily():
    from app.workers.celery_app import celery_app
    entry = celery_app.conf.beat_schedule["ptp-lifecycle-housekeeping"]
    assert entry["task"] == "app.workers.tasks.ptp_lifecycle.resolve_expired_promises"
    sched = entry["schedule"]
    assert sched.hour == {0} and sched.minute == {5}
    assert sched.day_of_month == set(range(1, 32))


def test_a_failing_row_is_counted_and_does_not_stop_the_batch(w, monkeypatch):
    c = w.case(); bad = w.ptp(c, D_ELIGIBLE); good = w.ptp(w.case(), D_ELIGIBLE)
    real = svc.verified_paid_against

    def boom(db, ptp, through):
        if ptp.id == bad.id:
            raise RuntimeError("simulated")
        return real(db, ptp, through)
    monkeypatch.setattr(svc, "verified_paid_against", boom)
    s = w.run()
    assert s.failed == 1 and s.broken == 1
    assert w.status(bad) is PTPStatus.ACTIVE and w.status(good) is PTPStatus.BROKEN
    assert len(w.audits()) == 1


# ── attribution: money counts only from the promise's origin ─────────────────
# (2026-09-17, Definition B. The origin is the taking visit's check-in, else
# created_at; the upper bound is the grace day, as before.)

def test_attr_a_payment_before_the_promise_was_made_does_not_count(w):
    c = w.case()
    v = w.visit(c, _at(EFF - timedelta(days=8), 16, 29))
    p = w.ptp(c, D_ELIGIBLE, amount=3675.0, visit=v)
    w.pay(c, 31481.0, _at(EFF - timedelta(days=13)))          # five days before the promise
    w.pay(c, 21633.0, _at(EFF - timedelta(days=8), 13, 0))     # same day, three hours BEFORE check-in
    w.run()
    assert w.status(p) is PTPStatus.BROKEN and p.actual_paid_amount == 0.0
    assert svc.verified_paid_against(w.db, p, through=D_ELIGIBLE + timedelta(days=1)) == 0.0


def test_attr_b_a_payment_during_the_taking_visit_counts(w):
    c = w.case()
    v = w.visit(c, _at(EFF - timedelta(days=8), 11, 25), outcome=VisitOutcome.PART_PAID_PTP)
    full = w.ptp(c, D_ELIGIBLE, amount=1000.0, visit=v)
    w.pay(c, 1000.0, _at(EFF - timedelta(days=8), 11, 25))     # at check-in, to the second
    c2 = w.case()
    v2 = w.visit(c2, _at(EFF - timedelta(days=8), 11, 25), outcome=VisitOutcome.PART_PAID_PTP)
    part = w.ptp(c2, D_ELIGIBLE, amount=1000.0, visit=v2)
    w.pay(c2, 400.0, _at(EFF - timedelta(days=8), 11, 35))     # ten minutes into the visit
    w.run()
    assert w.status(full) is PTPStatus.HONORED
    assert w.status(part) is PTPStatus.PARTIALLY_HONORED and part.actual_paid_amount == 400.0


def test_attr_c_a_payment_after_the_visit_and_before_due_plus_grace_counts(w):
    c = w.case()
    v = w.visit(c, _at(EFF - timedelta(days=8)))
    p = w.ptp(c, D_ELIGIBLE, amount=1000.0, visit=v)
    w.pay(c, 600.0, _at(EFF - timedelta(days=5)))                          # between visit and due
    w.pay(c, 400.0, _at(D_ELIGIBLE + timedelta(days=1), 23, 59))           # last minute of the grace day
    w.run()
    assert w.status(p) is PTPStatus.HONORED and p.actual_paid_amount == 1000.0


def test_attr_d_a_payment_after_due_plus_grace_does_not_count(w):
    c = w.case()
    v = w.visit(c, _at(EFF - timedelta(days=8)))
    p = w.ptp(c, D_ELIGIBLE, amount=1000.0, visit=v)
    w.pay(c, 1000.0, _at(D_ELIGIBLE + timedelta(days=2), 0, 1))            # the morning after the grace day
    w.run()
    assert w.status(p) is PTPStatus.BROKEN


def test_attr_e_a_linked_visit_wins_over_created_at(w):
    # The row was written days BEFORE the visit (a replayed book): if
    # created_at were the origin, the payment between the two would count.
    c = w.case()
    v = w.visit(c, _at(EFF - timedelta(days=6), 15, 0))
    p = w.ptp(c, D_ELIGIBLE, amount=1000.0, visit=v, created=_at(EFF - timedelta(days=12)))
    w.pay(c, 1000.0, _at(EFF - timedelta(days=9)))                         # after created_at, before the visit
    assert svc.ptp_origin(p) == v.check_in_time
    w.run()
    assert w.status(p) is PTPStatus.BROKEN
    # and the mirror: written AFTER the visit, paid in between -> counts
    c2 = w.case()
    v2 = w.visit(c2, _at(EFF - timedelta(days=9), 15, 0))
    q = w.ptp(c2, D_ELIGIBLE, amount=1000.0, visit=v2, created=_at(EFF - timedelta(days=6)))
    w.pay(c2, 1000.0, _at(EFF - timedelta(days=8)))
    w.run()
    assert w.status(q) is PTPStatus.HONORED


def test_attr_f_created_at_is_the_origin_only_when_there_is_no_visit(w):
    c = w.case()
    p = w.ptp(c, D_ELIGIBLE, amount=1000.0, created=_at(EFF - timedelta(days=7), 10, 0))
    assert p.visit_id is None and svc.ptp_origin(p) == p.created_at
    w.pay(c, 500.0, _at(EFF - timedelta(days=7), 9, 0))                    # an hour before the row existed
    w.pay(c, 500.0, _at(EFF - timedelta(days=7), 11, 0))                   # an hour after
    w.run()
    assert w.status(p) is PTPStatus.PARTIALLY_HONORED and p.actual_paid_amount == 500.0


def test_attr_g_multiple_payments_inside_the_window_are_summed(w):
    c = w.case()
    v = w.visit(c, _at(EFF - timedelta(days=8)))
    p = w.ptp(c, D_ELIGIBLE, amount=1000.0, visit=v)
    for amt, d in ((250.0, 7), (250.0, 5), (250.0, 3), (250.0, 2)):
        w.pay(c, amt, _at(EFF - timedelta(days=d)))
    w.pay(c, 999.0, _at(EFF - timedelta(days=9)))                          # before the visit: excluded
    w.pay(c, 999.0, _at(EFF - timedelta(days=8)), status=PaymentStatus.REJECTED)   # unverified: excluded
    w.run()
    assert w.status(p) is PTPStatus.HONORED and p.actual_paid_amount == 1000.0


def test_attr_h_two_promises_on_one_case_are_each_judged_from_their_own_origin(w):
    # The case-level sum is unchanged: money after BOTH origins credits both;
    # money between the two origins credits only the earlier promise.
    c = w.case()
    v1 = w.visit(c, _at(EFF - timedelta(days=12)))
    early = w.ptp(c, D_ELIGIBLE, amount=1000.0, visit=v1)
    v2 = w.visit(c, _at(EFF - timedelta(days=6)))
    late = w.ptp(c, D_ELIGIBLE, amount=1000.0, visit=v2)
    w.pay(c, 600.0, _at(EFF - timedelta(days=9)))                          # after v1, before v2
    w.pay(c, 400.0, _at(EFF - timedelta(days=4)))                          # after both
    w.run()
    assert w.status(early) is PTPStatus.HONORED and early.actual_paid_amount == 1000.0
    assert w.status(late) is PTPStatus.PARTIALLY_HONORED and late.actual_paid_amount == 400.0


def test_attr_i_payment_service_flow_visit_then_promise_then_payment_is_unchanged(w):
    from app.services.payment_service import PaymentService
    c = w.case()
    v = w.visit(c, _at(EFF - timedelta(days=1), 10, 0))
    p = w.ptp(c, EFF + timedelta(days=3), amount=1000.0, visit=v)
    w.pay(c, 1000.0, _at(EFF, 11, 0))
    PaymentService(w.db)._honor_active_ptps_paid_by_due_date(c.id, c.agent_id, _at(EFF, 11, 0))
    w.db.commit()
    assert w.status(p) is PTPStatus.HONORED and p.actual_paid_amount == 1000.0
    # money that predates the promise no longer honours it on the payment path either
    c2 = w.case()
    w.pay(c2, 5000.0, _at(EFF - timedelta(days=3)))
    v2 = w.visit(c2, _at(EFF - timedelta(days=1), 10, 0))
    q = w.ptp(c2, EFF + timedelta(days=3), amount=1000.0, visit=v2)
    w.pay(c2, 100.0, _at(EFF, 11, 0))
    PaymentService(w.db)._honor_active_ptps_paid_by_due_date(c2.id, c2.agent_id, _at(EFF, 11, 0))
    w.db.commit()
    assert w.status(q) is PTPStatus.ACTIVE


# ── the rollback guard ───────────────────────────────────────────────────────

def _manifest_for(result):
    from dataclasses import asdict
    return {"dry_run": False, "written_at": "t",
            "rows": [asdict(t) for pm in result["per_manager"] for t in pm["transitions"]]}


def test_undo_restores_a_row_the_backfill_wrote_and_nothing_else_touched(w):
    from scripts.ptp_lifecycle_backfill import undo_manifest
    c = w.case(); p = w.ptp(c, D_ELIGIBLE, amount=1000.0)
    w.pay(c, 300.0, _at(EFF - timedelta(days=5)))
    res = run_for_all_managers(w.db, EFF, source=SOURCE_BACKFILL)
    assert w.status(p) is PTPStatus.PARTIALLY_HONORED and p.actual_paid_amount == 300.0
    restored, left = undo_manifest(w.db, _manifest_for(res))
    assert (restored, left) == (1, [])
    assert w.status(p) is PTPStatus.ACTIVE and p.actual_paid_amount == 0.0
    rows = w.audits(p)
    assert len(rows) == 2 and rows[-1].details["reason"] == "BACKFILL_ROLLBACK"
    assert rows[-1].details["reverses_audit_log_id"] == rows[0].id


def test_undo_leaves_a_row_that_legitimately_moved_on_after_the_backfill(w):
    from scripts.ptp_lifecycle_backfill import undo_manifest
    from app.services.payment_service import PaymentService
    # 1. status changed since (a later payment honoured it through payment_service)
    c1 = w.case(); a = w.ptp(c1, D_ELIGIBLE, amount=1000.0)
    # 2. amount changed since, status the same
    c2 = w.case(); b = w.ptp(c2, D_ELIGIBLE, amount=1000.0)
    w.pay(c2, 300.0, _at(EFF - timedelta(days=5)))
    # 3. a later PTP_UPDATED audit row exists for it
    c3 = w.case(); d = w.ptp(c3, D_ELIGIBLE, amount=1000.0)
    res = run_for_all_managers(w.db, EFF, source=SOURCE_BACKFILL)
    assert (w.status(a), w.status(b), w.status(d)) == (PTPStatus.BROKEN, PTPStatus.PARTIALLY_HONORED, PTPStatus.BROKEN)

    a.status = PTPStatus.HONORED; w.db.commit()                    # 1
    b.actual_paid_amount = 999.0; w.db.commit()                    # 2
    w.db.add(AuditLog(id=str(uuid.uuid4()), created_at=datetime.now(timezone.utc) + timedelta(seconds=5),
                      user_id=None, action=AuditAction.PTP_UPDATED, entity_type="PTP", entity_id=d.id,
                      details={"from": "BROKEN", "to": "BROKEN", "reason": "SOMETHING_ELSE"}, success=True))
    w.db.commit()                                                  # 3

    restored, left = undo_manifest(w.db, _manifest_for(res))
    assert restored == 0
    assert {pid for pid, _ in left} == {a.id, b.id, d.id}
    assert w.status(a) is PTPStatus.HONORED
    assert w.status(b) is PTPStatus.PARTIALLY_HONORED and b.actual_paid_amount == 999.0
    assert w.status(d) is PTPStatus.BROKEN
    assert not any(r.details.get("reason") == "BACKFILL_ROLLBACK" for r in w.audits())
