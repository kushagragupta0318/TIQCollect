"""Sticky case ownership, and the one sanctioned way to override it.

2026-09-11. Until this change a case could change agent three ways and none of
them asked anybody: the nightly Hungarian re-solve (35 of 214 allocated cases
in one measured night), the 10% exploration swap (20 of those 214), and any
agent who recorded a visit or payment on it. The first two are closed here;
the third is deliberately untouched and remains a product decision.

Two halves. The first exercises GlobalAllocator directly with SimpleNamespace
objects, reusing the fixtures of tests/test_global_allocator.py — every case
in THAT file is unowned, which is why all fourteen of its tests still pass
unchanged and why this file has to exist at all. The second drives
POST /manager/cases/{id}/reassign through the real app against SQLite.
"""
from __future__ import annotations

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
from app.models.allocation_decision import AllocationOutcome
from app.models.audit_log import AuditAction, AuditLog
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.ptp import PTP, PTPStatus
from app.models.user import User, UserRole
from app.services.global_allocator import (
    BAR_OUTSIDE_TERRITORY, BAR_PTP_FATIGUE, BAR_REQUIRES_FEMALE_AGENT,
    GlobalAllocator, case_bar, pair_bar,
)

from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401
from tests.test_global_allocator import (   # noqa: E402  — shared fixtures
    BASE_LAT, BASE_LON, allocate, assigned, make_agent, make_case, outcomes,
)


# ═══════════════════════════════════════════════════════════════════════════
# Part 1 — the allocator
# ═══════════════════════════════════════════════════════════════════════════

def test_existing_owner_survives_a_normal_replan():
    """Capacity pressure is what actually moved owned cases on the real book,
    so that is what this test applies.

    a1 (female, one slot) owns c_owned. c_new arrives, needs a female agent,
    and only a1 can take it. Without the ownership gate the solver fills a1
    with c_new and hands c_owned to a2, whose slot is free — two allocations,
    higher objective, and an owned case quietly moved. With the gate, c_owned
    can go to a1 or to nobody; a2 must end the night empty.

    (A first draft of this test put the case beside a2 and far from a1 and
    expected a2 to win on proximity. It did not: `_work_anchor` scores
    proximity from the centroid of the cases an agent already holds, which on
    a one-case book is the case itself, so the owner always won and the test
    passed with the gate deleted. Mutation-checked; this version fails.)"""
    a1 = make_agent(test_id("a1"), gender="F", cap=1)
    a2 = make_agent(test_id("a2"), gender="M", cap=1)
    c_owned = make_case("c_owned", agent_id=test_id("a1"))
    c_new = make_case("c_new", needs_female=True, target=500_000.0)
    by_agent, decisions, _ = allocate([c_owned, c_new], [a1, a2])
    got = assigned(decisions)
    assert got.get("c_owned") in (test_id("a1"), None)
    assert by_agent[test_id("a2")] == [], "an owned case was moved to fill a free slot"


def test_an_unowned_case_still_goes_to_the_best_agent():
    """Same geometry, no owner: the optimiser is untouched for new work."""
    a1 = make_agent(test_id("a1"), lat=BASE_LAT + 0.072, spec=AgentSpecialization.UNSECURED)
    a2 = make_agent(test_id("a2"), spec=AgentSpecialization.SECURED)
    case = make_case("c1", loan_type=LoanType.AUTO)
    assert assigned(allocate([case], [a1, a2])[1]) == {"c1": test_id("a2")}


def _explore(cases, agents, *, rate):
    return GlobalAllocator(exploration_rate=rate, exploration_seed=7).allocate(
        cases=cases, agents=agents, history_matrix={},
        prio_scores={c.id: 50.0 for c in cases})[1]


def test_exploration_never_moves_an_owned_case():
    """epsilon = 1.0 — every allocated case is a candidate. Two agents, both
    eligible for every case, six owned cases split between them. Before this
    change that configuration swapped pairs every time; now nothing moves and
    nothing is stamped as explored."""
    agents = [make_agent(test_id("a1"), cap=3), make_agent(test_id("a2"), cap=3)]
    cases = [make_case(f"c{i}", agent_id=test_id("a1") if i < 3 else test_id("a2")) for i in range(6)]
    decisions = _explore(cases, agents, rate=1.0)
    got = assigned(decisions)
    assert got == {c.id: c.agent_id for c in cases}
    assert not any(d.score_breakdown.get("exploration") for d in decisions)


def test_exploration_still_randomises_unowned_cases():
    """The same book with no owners: exploration must still fire, or the
    agent-fit experiment has been switched off rather than scoped."""
    agents = [make_agent(test_id("a1"), cap=3), make_agent(test_id("a2"), cap=3)]
    cases = [make_case(f"c{i}") for i in range(6)]
    decisions = _explore(cases, agents, rate=1.0)
    explored = [d for d in decisions if d.score_breakdown.get("exploration")]
    assert explored, "no case was explored on an entirely unowned book"


def test_exploration_cannot_use_an_owned_case_as_the_other_half_of_a_swap():
    """A swap moves TWO cases. Filtering only initiators would still drag an
    owned case across as somebody else's partner — so the partner must be
    unowned too. Three unowned cases on a1, three owned on a2: a1's cases
    have nobody legal to swap with, and stay."""
    agents = [make_agent(test_id("a1"), cap=3), make_agent(test_id("a2"), cap=3)]
    cases = ([make_case(f"u{i}") for i in range(3)]
             + [make_case(f"o{i}", agent_id=test_id("a2")) for i in range(3)])
    decisions = _explore(cases, agents, rate=1.0)
    got = assigned(decisions)
    for i in range(3):
        assert got[f"o{i}"] == test_id("a2")
    assert not any(d.score_breakdown.get("exploration") for d in decisions)


def test_an_ineligible_owner_releases_the_case_to_deferral_not_to_another_agent():
    """a1 owns a case 50 km from their base; a2 sits right on it. The territory
    gate bars a1, and the case is DEFERRED — not handed to a2 — with a reason
    a manager can act on. That is the stated decision: an owner who cannot
    take the case follows the existing deferral path rather than being
    quietly replaced."""
    a1 = make_agent(test_id("a1"), lat=BASE_LAT + 0.45)          # ~50 km north
    a2 = make_agent(test_id("a2"))
    case = make_case("c1", agent_id=test_id("a1"))
    decisions = allocate([case], [a1, a2])[1]
    assert outcomes(decisions) == {"c1": AllocationOutcome.DEFERRED.value}
    assert not assigned(decisions)
    d = decisions[0]
    assert d.score_breakdown["owner_unavailable"] is True
    assert "assigned agent" in d.reason and "Reassign" in d.reason


def test_ptp_fatigue_still_overrides_ownership():
    """Three broken promises to the owner bar THE OWNER. Under stickiness the
    case cannot go elsewhere either, so it defers — and does not go back to
    the agent the borrower has already promised three times."""
    agents = [make_agent(test_id("a1"), cap=1), make_agent(test_id("a2"), cap=1)]
    case = make_case("c1", agent_id=test_id("a1"))
    decisions = allocate([case], agents, ptp_fatigue={"c1": {test_id("a1")}})[1]
    assert not assigned(decisions)
    assert outcomes(decisions) == {"c1": AllocationOutcome.DEFERRED.value}
    assert decisions[0].score_breakdown["owner_unavailable"] is True


def test_ptp_fatigue_on_an_unowned_case_behaves_exactly_as_before():
    """The pre-existing test in test_global_allocator, restated with an
    explicit unowned case so the two behaviours sit side by side."""
    agents = [make_agent(test_id("a1"), cap=1), make_agent(test_id("a2"), cap=1)]
    decisions = allocate([make_case("c1")], agents, ptp_fatigue={"c1": {test_id("a1")}})[1]
    assert assigned(decisions) == {"c1": test_id("a2")}


def test_capacity_is_not_silently_exceeded_by_a_sticky_owner():
    """a1 owns five cases and can work two. a2 is idle and eligible for all
    five. Exactly two are allocated, all to a1; three defer; a2 gets none —
    because giving a2 an owned case merely to fill a slot is the churn this
    change exists to stop."""
    a1, a2 = make_agent(test_id("a1"), cap=2), make_agent(test_id("a2"), cap=5)
    cases = [make_case(f"c{i}", agent_id=test_id("a1")) for i in range(5)]
    by_agent, decisions, _ = allocate(cases, [a1, a2])
    assert len(by_agent[test_id("a1")]) == 2
    assert by_agent[test_id("a2")] == []
    got = outcomes(decisions)
    assert sum(1 for v in got.values() if v == AllocationOutcome.ALLOCATED.value) == 2
    deferred = [d for d in decisions if d.outcome == AllocationOutcome.DEFERRED.value]
    assert len(deferred) == 3
    assert all(d.score_breakdown["owner_unavailable"] for d in deferred)


def test_every_case_still_appears_in_exactly_one_decision_under_ownership():
    a1 = make_agent(test_id("a1"), cap=1)
    cases = [make_case("c1", agent_id=test_id("a1")), make_case("c2", agent_id=test_id("a1")),
             make_case("c3", agent_id=test_id("ghost")), make_case("c4")]
    decisions = allocate(cases, [a1])[1]
    assert sorted(d.case_id for d in decisions) == ["c1", "c2", "c3", "c4"]
    # c3's owner is not on this roster at all: deferred, never reassigned.
    assert outcomes(decisions)["c3"] == AllocationOutcome.DEFERRED.value


def test_the_shared_gate_functions_agree_with_the_matrix_loop():
    """pair_bar/case_bar are what manager reassignment calls. They must say
    exactly what the cost-matrix loop does, or a manager could hand a case
    to an agent the nightly run would refuse. Each gate is checked both ways."""
    female_needed = make_case("c", needs_female=True)
    assert pair_bar(female_needed, female_needed.customer, make_agent("m", gender="M"),
                    dist_km=1.0, territory_radius_km=16.0) == BAR_REQUIRES_FEMALE_AGENT
    assert pair_bar(female_needed, female_needed.customer, make_agent("f", gender="F"),
                    dist_km=1.0, territory_radius_km=16.0) is None
    plain = make_case("c")
    assert pair_bar(plain, plain.customer, make_agent("a"), dist_km=16.1,
                    territory_radius_km=16.0) == BAR_OUTSIDE_TERRITORY
    assert pair_bar(plain, plain.customer, make_agent("a"), dist_km=1.0,
                    territory_radius_km=16.0, fatigued_agent_ids={"a"}) == BAR_PTP_FATIGUE
    assert case_bar(make_case("c", dnc=True).customer) == "DNC"
    assert case_bar(make_case("c", hostile=True).customer) == "HOSTILITY"
    assert case_bar(plain.customer) is None
    # Ownership is a gate for the planner and NOT for the manager.
    owned = make_case("c", agent_id=test_id("other"))
    assert pair_bar(owned, owned.customer, make_agent("a"), dist_km=1.0,
                    territory_radius_km=16.0) == "OWNED_BY_ANOTHER_AGENT"
    assert pair_bar(owned, owned.customer, make_agent("a"), dist_km=1.0,
                    territory_radius_km=16.0, enforce_ownership=False) is None


# ═══════════════════════════════════════════════════════════════════════════
# Part 2 — POST /manager/cases/{id}/reassign
# ═══════════════════════════════════════════════════════════════════════════

engine = make_engine()
TestingSession = make_session_factory(autocommit=False, autoflush=False, bind=engine)
TODAY = date.today()


def _uid() -> str:
    return str(uuid.uuid4())


def _user(db, email, role, name):
    u = User(id=_uid(), email=email, phone=email.split("@")[0][:10].ljust(10, "0"),
             full_name=name, hashed_password="x", role=role, is_active=True, is_verified=True)
    db.add(u)
    return u


def _agent(db, user, code, mgr, *, gender="M", lat=28.63, lon=77.21):
    a = Agent(id=_uid(), user_id=user.id, employee_code=code, id_card_number=code + "-ID",
              manager_user_id=mgr.id, gender=gender,
              base_latitude=lat, base_longitude=lon, territory="Delhi",
              languages_spoken=["HINDI"], status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
              specialization=AgentSpecialization.BOTH, ranking_score=80.0, max_cases_per_day=5)
    db.add(a)
    return a


def _borrower(db, ref, *, lat=28.6315, lon=77.2167, **flags):
    c = Customer(id=_uid(), customer_ref=ref, full_name=f"Borrower {ref}",
                 date_of_birth=date(1990, 1, 1), gender="M", pan_masked="ABCDE1234F",
                 aadhaar_masked="123456789012", phone_primary="98" + ref.ljust(8, "0"),
                 address_line1="Delhi", city="Delhi", state="Delhi", pincode="110001",
                 latitude=lat, longitude=lon, language_preference="HINDI", **flags)
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
    return c, loan


def _case(db, number, cust, loan, agent, status=CaseStatus.ASSIGNED):
    k = Case(id=_uid(), case_number=number, customer_id=cust.id, loan_id=loan.id,
             agent_id=agent.id if agent else None, status=status,
             target_amount=20000.0, collected_amount=0.0, allocation_date=TODAY)
    db.add(k)
    return k


@pytest.fixture(scope="module")
def world():
    create_schema(engine)
    db = TestingSession()
    mgr = _user(db, "mgr@t.io", UserRole.AGENCY_MANAGER, "Manager One")
    other = _user(db, "other@t.io", UserRole.AGENCY_MANAGER, "Manager Two")
    db.flush()
    a_male = _agent(db, _user(db, "a1@t.io", UserRole.FIELD_AGENT, "Arjun"), "EMP001", mgr)
    a_female = _agent(db, _user(db, "a2@t.io", UserRole.FIELD_AGENT, "Priya"), "EMP002", mgr, gender="F")
    a_far = _agent(db, _user(db, "a3@t.io", UserRole.FIELD_AGENT, "Far Away"), "EMP003", mgr,
                   lat=28.63 + 0.45, lon=77.21)                  # ~50 km north
    a_other = _agent(db, _user(db, "a9@t.io", UserRole.FIELD_AGENT, "Not Mine"), "EMP999", other)
    db.flush()

    plain_c, plain_l = _borrower(db, "PLAIN")
    fem_c, fem_l = _borrower(db, "FEM", requires_female_agent=True)
    dnc_c, dnc_l = _borrower(db, "DNC", do_not_contact=True)
    fat_c, fat_l = _borrower(db, "FAT")
    paid_c, paid_l = _borrower(db, "PAID")
    for_c, for_l = _borrower(db, "FOREIGN")
    new_c, new_l = _borrower(db, "NEW")

    plain = _case(db, "C-PLAIN", plain_c, plain_l, a_male)
    fem = _case(db, "C-FEM", fem_c, fem_l, a_female)
    dnc = _case(db, "C-DNC", dnc_c, dnc_l, a_male)
    fat = _case(db, "C-FAT", fat_c, fat_l, a_male)
    paid = _case(db, "C-PAID", paid_c, paid_l, a_male, status=CaseStatus.PAID)
    foreign = _case(db, "C-FOREIGN", for_c, for_l, a_other)
    # A NEW case: no agent, status UNASSIGNED. The nightly allocator's job.
    unowned = _case(db, "C-NEW", new_c, new_l, None, status=CaseStatus.UNASSIGNED)
    db.flush()

    # Three broken promises to the FEMALE agent on C-FAT: she is fatigued there.
    for i in range(3):
        db.add(PTP(id=_uid(), case_id=fat.id, agent_id=a_female.id, committed_amount=1000.0,
                   committed_date=TODAY - timedelta(days=30 - i), status=PTPStatus.BROKEN))
    db.commit()
    yield {"db": db, "mgr": mgr, test_id("other"): other,
           "male": a_male, "female": a_female, "far": a_far, "not_mine": a_other,
           "plain": plain, "fem": fem, "dnc": dnc, "fat": fat, "paid": paid, "foreign": foreign,
           "unowned": unowned}
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


def _post(client, world, case, agent, reason, who=None):
    return client.post(f"/api/v1/manager/cases/{case.id}/reassign",
                       json={"new_agent_id": agent.id, "reason": reason},
                       headers=_h(who or world["mgr"]))


def test_reassignment_without_a_reason_is_rejected(client, world):
    for bad in ("", "   ", "\n\t"):
        r = _post(client, world, world["plain"], world["female"], bad)
        assert r.status_code == 422, (bad, r.text)
        assert "reason" in r.text.lower()
    # And nothing moved.
    db = TestingSession()
    assert db.get(Case, world["plain"].id).agent_id == world["male"].id
    assert db.query(AuditLog).filter(AuditLog.action == AuditAction.CASE_ASSIGNED).count() == 0
    db.close()


def test_reassignment_with_a_reason_succeeds_and_is_audited(client, world):
    r = _post(client, world, world["plain"], world["female"],
              "  Borrower asked for a different agent after a dispute.  ")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_agent_id"] == world["male"].id
    assert body["to_agent_id"] == world["female"].id
    assert body["reason"] == "Borrower asked for a different agent after a dispute."

    db = TestingSession()
    assert db.get(Case, world["plain"].id).agent_id == world["female"].id

    log = (db.query(AuditLog)
           .filter(AuditLog.action == AuditAction.CASE_ASSIGNED,
                   AuditLog.entity_id == world["plain"].id).one())
    assert log.user_id == world["mgr"].id
    assert log.entity_type == "Case"
    assert log.old_values == {"agent_id": world["male"].id}
    assert log.new_values == {"agent_id": world["female"].id}
    assert log.details["reason"] == "Borrower asked for a different agent after a dispute."
    assert log.details["from_agent_name"] == "Arjun"
    assert log.details["to_agent_name"] == "Priya"
    assert log.success is True
    db.close()


def test_the_case_now_surfaces_its_owner_and_last_reason(client, world):
    r = client.get("/api/v1/manager/cases", headers=_h(world["mgr"]))
    assert r.status_code == 200
    row = next(c for c in r.json()["cases"] if c["id"] == world["plain"].id)
    assert row["agent_id"] == world["female"].id
    assert row["agent_name"] == "Priya"
    lr = row["last_reassignment"]
    assert lr["reason"] == "Borrower asked for a different agent after a dispute."
    assert lr["from_agent_name"] == "Arjun" and lr["to_agent_name"] == "Priya"
    assert lr["by"] == "Manager One"
    # A case never moved by hand says so with None, not an empty dict.
    untouched = next(c for c in r.json()["cases"] if c["id"] == world["fem"].id)
    assert untouched["last_reassignment"] is None

    d = client.get(f"/api/v1/manager/cases/{world['plain'].id}", headers=_h(world["mgr"]))
    assert d.status_code == 200
    assert d.json()["last_reassignment"]["reason"].startswith("Borrower asked")


def test_reassigning_to_the_current_owner_is_refused(client, world):
    r = _post(client, world, world["plain"], world["female"], "no-op")
    assert r.status_code == 409


def test_incoming_agent_is_checked_against_the_female_requirement(client, world):
    r = _post(client, world, world["fem"], world["male"], "trying a male agent")
    assert r.status_code == 409
    assert "female" in r.json()["detail"].lower()


def test_incoming_agent_is_checked_against_the_territory_radius(client, world):
    r = _post(client, world, world["fem"], world["far"], "someone far away")
    # a_far is female-eligible-neutral (male) so the female gate fires first
    # on C-FEM; use C-FAT, which has no such requirement, for territory.
    r = _post(client, world, world["fat"], world["far"], "someone far away")
    assert r.status_code == 409
    assert "territory" in r.json()["detail"].lower()


def test_incoming_agent_is_checked_against_ptp_fatigue(client, world):
    r = _post(client, world, world["fat"], world["female"], "give it to Priya")
    assert r.status_code == 409
    assert "promises" in r.json()["detail"].lower()


def test_a_dnc_case_cannot_be_reassigned_to_anyone(client, world):
    r = _post(client, world, world["dnc"], world["female"], "anyone")
    assert r.status_code == 409
    assert "do-not-contact" in r.json()["detail"].lower()


def test_a_resolved_case_cannot_be_reassigned(client, world):
    r = _post(client, world, world["paid"], world["female"], "already paid")
    assert r.status_code == 409


def test_tenancy_is_enforced_in_both_directions(client, world):
    # Another manager's case: 404, not 403 — no enumeration oracle.
    r = _post(client, world, world["foreign"], world["male"], "poaching")
    assert r.status_code == 404
    # Another manager's agent as the destination: also 404.
    r = _post(client, world, world["plain"], world["not_mine"], "outsourcing")
    assert r.status_code == 404
    # And the other manager cannot touch our case.
    r = _post(client, world, world["plain"], world["male"], "theirs", who=world[test_id("other")])
    assert r.status_code == 404


def test_an_unassigned_case_cannot_be_manually_assigned_through_reassign(client, world):
    """PRODUCT INVARIANT: UNASSIGNED cases are NEW cases, and the nightly
    allocator gives them their first agent. Reassignment moves ownership that
    already exists; it must never be a back door to first assignment.

    The first version of the endpoint accepted this call (its lookup was the
    planner's pool filter, which includes unassigned cases because the planner
    is what assigns them). Nothing in the UI could reach it — the case list and
    detail both hide unowned cases — but a hand-made request could, and it left
    a case with an agent and a status of UNASSIGNED. Refused now, with the
    lifecycle spelled out, and every side effect asserted absent.
    """
    db = TestingSession()
    audit_before = db.query(AuditLog).count()
    db.close()

    r = _post(client, world, world["unowned"], world["male"],
              "manager tries to hand a brand-new case straight to an agent")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "This case has no agent yet — tonight's plan will assign it. "
        "Only an assigned case can be reassigned.")

    db = TestingSession()
    c = db.get(Case, world["unowned"].id)
    assert c.agent_id is None, "an unassigned case acquired an owner through reassign"
    assert c.status == CaseStatus.UNASSIGNED, "the endpoint must never advance UNASSIGNED"
    # No audit row for this case, and none anywhere: the refusal is not an event.
    assert db.query(AuditLog).filter(AuditLog.entity_id == c.id).count() == 0
    assert db.query(AuditLog).count() == audit_before
    db.close()
