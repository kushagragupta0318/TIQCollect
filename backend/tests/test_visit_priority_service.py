# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-27 — New file. The DB-aware half of visit priority: the bulk reads,
#   and the allocator ordering that consumes them.
#
#   WHY THE QUERY-BUDGET TEST IS HERE AND NOT A COMMENT. "It reads in bulk" is
#   precisely the claim that stops being true the first time somebody adds a
#   convenience lookup inside the per-case loop, and nothing else in the suite
#   would notice. score_cases must issue a FIXED number of queries whatever the
#   case count; this counts them with a SQLAlchemy event listener.
#
#   AND WHY THE ORDERING TEST DELIBERATELY SCRAMBLES THE FIXTURE. The previous
#   ranking bug was that the sort ran BEFORE the loans were loaded, so the score
#   read total_outstanding=None for every case and the queue degraded to
#   insertion order while still reporting itself as ranked. A fixture inserted in
#   the correct order would have passed that bug.
# ─────────────────────────────────────────────────────────────────────────────
"""score_cases' query budget, and the allocator queue it feeds."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ml.allocator import CaseAllocator
from app.ml.visit_priority import COMPONENT_ORDER
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.base import Base
from app.models.case import Case, CasePriority, CaseStatus
from app.models.customer import Customer, RiskCategory
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.ptp import PTP, PTPStatus
from app.models.repayment_snapshot import TRIGGER_SEED, RepaymentSnapshot
from app.models.user import User, UserRole
from app.services.visit_priority_service import score_cases
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

engine = make_engine()
Session = make_session_factory(bind=engine, autoflush=False)
TODAY = date.today()


def _customer(db, i, *, dnc=False, needs_female=False):
    c = Customer(id=f"cu{i}", customer_ref=f"CUST{i:04d}", full_name=f"Borrower {i}",
                 date_of_birth="1985-06-01", gender="M", pan_masked="XXXXX1234X",
                 aadhaar_masked="XXXXXXXX5678", phone_primary=f"98000000{i:02d}",
                 address_line1="1 Road", city="Gurugram", state="HR",
                 pincode="122001", latitude=28.45, longitude=77.02,
                 risk_category=RiskCategory.MEDIUM, risk_score=50.0,
                 cibil_score=650, do_not_contact=dnc,
                 requires_female_agent=needs_female)
    db.add(c)
    return c


def _loan(db, i, *, dpd, outstanding):
    ln = Loan(id=f"ln{i}", customer_id=f"cu{i}", loan_account_number=f"LN{i:06d}",
              loan_type=LoanType.PERSONAL, branch_code="BR1",
              sanctioned_amount=outstanding * 1.4, disbursed_amount=outstanding * 1.3,
              outstanding_principal=outstanding * 0.9,
              outstanding_interest=outstanding * 0.1, penal_charges=500.0,
              total_outstanding=outstanding, overdue_amount=outstanding * 0.3,
              emi_amount=12_000.0, tenure_months=36, interest_rate=14.0,
              disbursement_date="2024-01-01", maturity_date="2027-01-01",
              dpd=dpd, dpd_bucket=(DPDBucket.NPA if dpd > 90 else DPDBucket.BUCKET_3),
              status=LoanStatus.ACTIVE, npa_flag=dpd > 90)
    db.add(ln)
    return ln


def _snapshot(db, i, *, rate_90, as_of=None):
    db.add(RepaymentSnapshot(
        loan_id=f"ln{i}", customer_id=f"cu{i}", as_of_date=as_of or TODAY,
        trigger=TRIGGER_SEED, source="SCORECARD", model_version="scorecard-1.1.0",
        likelihood=50.0, risk_score=50.0, band="UNCERTAIN",
        risk_category=RiskCategory.MEDIUM, evidence_coverage=0.8,
        features={}, contributions={},
        recovery_potential="MEDIUM", recovery_rate_30=rate_90 / 3,
        recovery_rate_60=rate_90 / 2, recovery_rate_90=rate_90,
        recovery_evidence_coverage=0.8, recovery_source="SCORECARD",
        recovery_model_version="recovery-scorecard-1.1.0",
        recovery_contributions={}))


# (tag, dpd, outstanding, rate_90, visit_count) — INSERTED IN A SCRAMBLED ORDER
# on purpose, so a passing ordering test cannot be an artefact of insertion.
_PLAN = [
    ("worthless_ancient", 300, 40_000.0, 0.05, 4),
    ("big_pre_npa",        85, 5_000_000.0, 0.50, 0),
    ("mid",                70, 400_000.0, 0.40, 1),
    ("big_npa",           200, 4_000_000.0, 0.35, 0),
    ("small_pre_npa",      80, 60_000.0, 0.40, 0),
]


def _build(db, *, n=None, dnc_idx=(), female_idx=(), ptp_idx=()):
    mgr = User(id="u-mgr", email="m@x.io", phone="9111111111", full_name="Mgr",
               hashed_password="x", role=UserRole.AGENCY_MANAGER)
    db.add(mgr)
    for a in range(2):
        u = User(id=f"u-a{a}", email=f"a{a}@x.io", phone=f"90000000{a:02d}",
                 full_name=f"Agent {a}", hashed_password="x",
                 role=UserRole.FIELD_AGENT)
        db.add(u)
        db.add(Agent(id=f"ag{a}", user_id=u.id, employee_code=f"EMP{a:04d}",
                     id_card_number=f"IC{a}", agency_id="AG1", gender="MALE",
                     base_latitude=28.4, base_longitude=77.0, territory="Gurugram",
                     languages_spoken=["HINDI"], max_cases_per_day=50,
                     status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
                     specialization=AgentSpecialization.BOTH,
                     ranking_score=50.0, manager_user_id=mgr.id))

    plan = _PLAN if n is None else [_PLAN[i % len(_PLAN)] for i in range(n)]
    for i, (tag, dpd, outstanding, rate, visits) in enumerate(plan):
        _customer(db, i, dnc=i in dnc_idx, needs_female=i in female_idx)
        _loan(db, i, dpd=dpd, outstanding=outstanding)
        _snapshot(db, i, rate_90=rate)
        db.add(Case(id=f"c{i}", case_number=f"CASE{i:07d}", customer_id=f"cu{i}",
                    loan_id=f"ln{i}", agent_id=None,
                    status=CaseStatus.UNASSIGNED, priority=CasePriority.MEDIUM,
                    target_amount=50_000.0, collected_amount=0.0,
                    visit_count=visits, max_visits_allowed=3,
                    is_escalated=False, allocation_score=0.0,
                    is_ml_allocated=False))
        if i in ptp_idx:
            db.add(PTP(id=f"p{i}", case_id=f"c{i}", agent_id="ag0",
                       committed_amount=10_000.0,
                       committed_date=TODAY + timedelta(days=1),
                       actual_paid_amount=0.0, status=PTPStatus.ACTIVE))
    db.commit()
    return {tag: f"c{i}" for i, (tag, *_rest) in enumerate(plan)}


@pytest.fixture
def db():
    drop_schema(engine)
    create_schema(engine)
    s = Session()
    yield s
    s.close()


# ── The query budget ────────────────────────────────────────────────────────
def _count_queries(session, fn):
    seen = []
    conn = session.connection()

    def before(_conn, _cur, statement, *a, **kw):
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    event.listen(conn.engine, "before_cursor_execute", before)
    try:
        fn()
    finally:
        event.remove(conn.engine, "before_cursor_execute", before)
    return seen


def test_score_cases_issues_a_fixed_number_of_queries(db):
    """THREE SELECTs at most — latest recovery rate, promises due soon, and the
    loans — no matter how many cases. A per-case lookup would be 3 x N round
    trips: on the live book's 545 open cases that is ~1,600 queries for one page
    load.

    Deliberately fetches the cases WITHOUT joinedload, which is how a careless
    caller would do it. An earlier version read case.loan and quietly cost
    1 + N queries; the count is the service's guarantee, not the caller's.
    """
    _build(db, n=40)
    cases = db.query(Case).all()
    assert len(cases) == 40

    two = _count_queries(db, lambda: score_cases(db, cases[:2], today=TODAY))
    forty = _count_queries(db, lambda: score_cases(db, cases, today=TODAY))

    assert len(two) == 3, two
    assert len(forty) == 3, forty
    assert len(forty) == len(two), "query count grew with case count"


def test_passing_preloaded_loans_saves_the_loan_query(db):
    """The allocator already bulk-loads loans in _load_case_context. Handing them
    in must not cost a second fetch of the same rows."""
    _build(db)
    cases = db.query(Case).all()
    loans = {l.id: l for l in db.query(Loan).all()}
    with_loans = _count_queries(db, lambda: score_cases(db, cases, today=TODAY,
                                                       loans=loans))
    assert len(with_loans) == 2, with_loans


def test_no_queries_at_all_for_an_empty_case_list(db):
    """An empty IN clause is both wasteful and, on some dialects, invalid."""
    _build(db)
    assert _count_queries(db, lambda: score_cases(db, [], today=TODAY)) == []


def test_only_the_newest_snapshot_per_loan_is_used(db):
    """A loan scored on several days must contribute exactly one rate — the
    latest. The group-by-max self-join can return two rows per loan if the
    (loan_id, as_of_date) uniqueness is ever violated."""
    _build(db)
    # An older, deliberately different rate for the same loan.
    _snapshot(db, 1, rate_90=0.01, as_of=TODAY - timedelta(days=30))
    db.commit()
    case = db.query(Case).filter(Case.id == "c1").one()
    result = score_cases(db, [case], today=TODAY)[case.id]
    value = next(c for c in result["components"] if c["code"] == "RECOVERABLE_VALUE")
    assert value["evidence"]["rate_90"] == 0.50, value
    assert result["rate_as_of"] == TODAY.isoformat()


def test_an_imminent_ptp_is_scored_with_amount_and_a_distant_one_is_not(db):
    """Only today/tomorrow PTPs receive a follow-up lift, never a penalty waiver."""
    _build(db, ptp_idx={0})
    db.add(PTP(id="pfar", case_id="c2", agent_id="ag0", committed_amount=1.0,
               committed_date=TODAY + timedelta(days=40),
               actual_paid_amount=0.0, status=PTPStatus.ACTIVE))
    db.commit()
    cases = db.query(Case).all()
    scored = score_cases(db, cases, today=TODAY)

    def effort(cid):
        return next(c for c in scored[cid]["components"] if c["code"] == "EFFORT")

    assert effort("c0")["evidence"]["penalty_waived"] is False
    assert effort("c0")["evidence"]["ptp_committed_amount"] == 10_000.0
    assert effort("c0")["evidence"]["ptp_follow_up_points"] > 0
    assert effort("c2")["evidence"]["penalty_waived"] is False
    assert "ptp_follow_up_points" not in effort("c2")["evidence"]


# ── The allocator queue ─────────────────────────────────────────────────────
def test_the_allocator_orders_by_visit_priority(db):
    """The fixture is inserted scrambled, so this cannot pass by accident.

    Guards the ordering bug specifically: the sort must run AFTER
    _load_case_context, or every case scores against total_outstanding=None and
    the queue silently becomes insertion order while still calling itself ranked.
    """
    ids = _build(db)
    res = CaseAllocator(db).run()
    order = [e["case_number"] for e in res["explanations"]]
    scores = [e["score"] for e in res["explanations"]]

    assert scores == sorted(scores, reverse=True), scores
    # The biggest, about-to-tip case leads; the worthless ancient one trails.
    assert order[0] == "CASE0000001"          # big_pre_npa
    assert order[-1] == "CASE0000000"         # worthless_ancient
    assert ids


def test_a_large_npa_case_beats_a_small_pre_npa_case_in_the_real_queue(db):
    """The same guarantee as the unit test, but through the allocator, on real
    rows. This is the failure of the removed DPD policy."""
    _build(db)
    res = CaseAllocator(db).run()
    rank = {e["case_number"]: e["rank"] for e in res["explanations"]}
    assert rank["CASE0000003"] < rank["CASE0000004"]   # big_npa before small_pre_npa


def test_every_explanation_carries_all_three_components(db):
    """The brief's requirement: a manager must be able to point at the numbers.
    A score without its components is not explainable."""
    _build(db)
    res = CaseAllocator(db).run()
    assert res["explanations"]
    for e in res["explanations"]:
        assert [c["code"] for c in e["components"]] == list(COMPONENT_ORDER)
        assert e["reason"]
        assert e["score"] is not None


def test_the_stored_allocation_score_is_the_reported_score(db):
    """Case.allocation_score already carried three incompatible conventions
    across four writers. What this allocator writes must at least equal what it
    says it wrote."""
    _build(db)
    res = CaseAllocator(db).run()
    reported = {e["case_number"]: e["score"] for e in res["explanations"]}
    for case in db.query(Case).filter(Case.agent_id.isnot(None)).all():
        assert case.allocation_score == pytest.approx(reported[case.case_number])


def test_the_allocator_never_marks_a_case_as_model_allocated(db):
    """A hand-weighted scorecard. is_ml_allocated is what the UI reads."""
    _build(db)
    CaseAllocator(db).run()
    assert all(c.is_ml_allocated is False for c in db.query(Case).all())


def test_a_resolved_case_gets_no_score_at_all(db):
    """"Why this case is visited first: 64/100" on a case marked PAID is the
    product contradicting itself on one screen. 198 of 743 cases on the live book
    are resolved, so this is the common path — it shipped visible before this
    test existed.

    Omitted from the result entirely rather than scored-and-hidden, so a caller
    cannot render it by forgetting to check.
    """
    _build(db)
    cases = db.query(Case).all()
    cases[0].status = CaseStatus.PAID
    cases[1].status = CaseStatus.CLOSED
    db.commit()

    scored = score_cases(db, cases, today=TODAY)
    assert cases[0].id not in scored
    assert cases[1].id not in scored
    # Everything still open is scored as before.
    assert len(scored) == len(cases) - 2


def test_an_escalated_case_is_still_scored(db):
    """ESCALATED is open, visitable, and the case a manager most wants ranked.
    Grouping it with PAID would silently drop the hardest work."""
    _build(db)
    cases = db.query(Case).all()
    cases[0].status = CaseStatus.ESCALATED
    db.commit()
    assert cases[0].id in score_cases(db, cases, today=TODAY)


# ── Compliance must be untouched by the reordering ──────────────────────────
def test_a_do_not_contact_case_is_still_never_allocated(db):
    """Only the SEQUENCE changed. If reordering could affect the hard rules, the
    whole change would need re-certifying rather than reasoning about."""
    _build(db, dnc_idx={1})   # the highest-scoring case, so order cannot hide it
    res = CaseAllocator(db).run()
    blocked = db.query(Case).filter(Case.customer_id == "cu1").one()
    assert blocked.status == CaseStatus.UNASSIGNED
    assert blocked.agent_id is None
    assert res["blocked_by_rule"].get("DO_NOT_CONTACT") == 1
    assert "CASE0000001" not in [e["case_number"] for e in res["explanations"]]


def test_a_female_agent_requirement_is_still_honoured(db):
    """Both fixture agents are male, so the case must go unallocated rather than
    to whoever is free — even though it is the top-scoring case."""
    _build(db, female_idx={1})
    res = CaseAllocator(db).run()
    assert db.query(Case).filter(Case.customer_id == "cu1").one().agent_id is None
    assert res["no_eligible_agent"] >= 1


def test_the_method_string_says_rule_not_model(db):
    """Read by logs and by anyone auditing what ran. "rule" is load-bearing."""
    _build(db)
    res = CaseAllocator(db).run()
    assert res["method"].startswith("rule_based")
    assert "visit_priority" in res["method"]
    assert res["ordering"] == "visit_priority"
