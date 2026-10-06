"""services/placement_service.py — a case is opened only on a placement.

2026-09-24 (coordinator audit item 2). The session in most of these tests has
NO default tenant (`info={}`), exactly like production: every bank and agency
id must come from real rows, so a path that only worked because tests/_db.py
filled a missing tenant would fail here.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.models.case import CaseStatus
from app.models.customer import Customer
from app.models.lending import BankFeedBatch
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.placement import Placement
from app.models.tenancy import Agency, AgencyContract, AgencyContractTerm, AgencyRegion, Branch
from app.services.placement_service import (
    AGENCY_NOT_ACTIVE, CONTRACT_FULL, GATES, NO_AGENCY, NO_CONTRACT_IN_FORCE, NOT_AUTHORISED, NOT_COVERED,
    PLACED_ELSEWHERE, PlacementRefused, PlacementService, path_covers,
)
from tests._placement import cover, put_branches_in, region_tree
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

DAY = date(2026, 9, 24)


@pytest.fixture()
def db():
    engine = make_engine()
    create_schema(engine)
    s = make_session_factory(engine, info={})()      # production-shaped: no default tenant
    yield s
    s.close()


def _loan(db, n=1, *, dpd=47, loan_type=LoanType.PERSONAL):
    cust = Customer(id=test_id(f"cust:{n}"), bank_id=TEST_BANK_ID, customer_ref=f"MTB-C-{n:04d}",
                    full_name="Farhan Siddiqui", date_of_birth=date(1988, 6, 14), gender="MALE",
                    pan_masked="XXXXX4821K", aadhaar_masked="XXXXXXXX3307", phone_primary="9899000101",
                    address_line1="C-214, Sector 49", city="Gurugram", state="Haryana", pincode="122018",
                    latitude=28.412, longitude=77.064)
    loan = Loan(id=test_id(f"loan:{n}"), bank_id=TEST_BANK_ID, loan_account_number=f"MTB{n:07d}",
                customer_id=cust.id, loan_type=loan_type, branch_code="GGN044",
                sanctioned_amount=400000.0, disbursed_amount=400000.0, outstanding_principal=260000.0,
                total_outstanding=281000.0, overdue_amount=24600.0, emi_amount=12300.0, dpd=dpd,
                disbursement_date=date(2024, 2, 1), maturity_date=date(2027, 2, 1), interest_rate=14.25)
    db.add_all([cust, loan])
    db.flush()
    return loan


def _contract(db, *, agency_id=TEST_AGENCY_ID, no="MTB/FCA/2026-27/014", start=date(2026, 4, 1),
              end=date(2027, 3, 31), status="ACTIVE", cap=None, covered=True):
    c = AgencyContract(bank_id=TEST_BANK_ID, agency_id=agency_id, contract_no=no, start_date=start, end_date=end,
                       status=status, max_placed_cases=cap, sla_first_visit_days=5)
    db.add(c)
    db.flush()
    if covered:
        cover(db, c)
    return c


def _second_agency(db, status="ACTIVE"):
    a = Agency(id=test_id("agency:kaveri"), bank_id=TEST_BANK_ID, code="AGENCY-TIQ-002",
               legal_name="Kaveri Resolve Partners LLP", trade_name="Kaveri Resolve", status=status,
               contacts=[], is_demo=True)
    db.add(a)
    db.flush()
    return a


def test_places_and_opens_a_case_with_every_tenant_id_from_real_rows(db):
    contract = _contract(db)
    loan = _loan(db)
    svc = PlacementService(db)
    p = svc.place_new_loan(loan, agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    case = svc.open_case(p, loan, case_number="MTB-CASE-0001", target_amount=12300.0)
    db.commit()
    assert (p.bank_id, p.agency_id, p.contract_id) == (TEST_BANK_ID, TEST_AGENCY_ID, contract.id)
    assert (p.dpd_at_placement, p.dpd_bucket_at_placement) == (47, DPDBucket.BUCKET_2)
    assert (p.exposure_at_placement, p.overdue_at_placement) == (281000.0, 24600.0)
    assert p.sla_first_visit_due == date(2026, 9, 29)
    assert p.expected_end_on == date(2027, 3, 31)          # recall_at_contract_end defaults True
    assert (case.bank_id, case.agency_id, case.placement_id) == (TEST_BANK_ID, TEST_AGENCY_ID, p.id)
    assert (case.customer_id, case.status, case.agent_id) == (loan.customer_id, CaseStatus.UNASSIGNED, None)


def test_placing_twice_with_the_same_agency_is_idempotent(db):
    _contract(db)
    loan = _loan(db)
    svc = PlacementService(db)
    first = svc.place_new_loan(loan, agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    assert svc.place_new_loan(loan, agency_id=TEST_AGENCY_ID, on=DAY, source="FEED").id == first.id
    assert db.query(Placement).count() == 1


def test_a_loan_placed_with_one_agency_is_refused_to_another(db):
    _contract(db)
    other = _second_agency(db)
    _contract(db, agency_id=other.id, no="MTB/FCA/2026-27/015")
    loan = _loan(db)
    svc = PlacementService(db)
    svc.place_new_loan(loan, agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    with pytest.raises(PlacementRefused) as r:
        svc.place_new_loan(loan, agency_id=other.id, on=DAY, source="FEED")
    assert r.value.reason == PLACED_ELSEWHERE


@pytest.mark.parametrize("start,end,status", [
    (date(2025, 4, 1), date(2026, 3, 31), "ACTIVE"),     # ended before the day
    (date(2026, 10, 1), date(2027, 9, 30), "ACTIVE"),    # starts after it
    (date(2026, 4, 1), date(2027, 3, 31), "DRAFT"),      # never signed
    (date(2026, 4, 1), date(2027, 3, 31), "TERMINATED"),
])
def test_no_contract_in_force_is_refused(db, start, end, status):
    _contract(db, start=start, end=end, status=status)
    with pytest.raises(PlacementRefused) as r:
        PlacementService(db).place_new_loan(_loan(db), agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    assert r.value.reason == NO_CONTRACT_IN_FORCE


def test_once_terms_exist_only_an_authorised_product_and_bucket_places(db):
    c = _contract(db)
    db.add(AgencyContractTerm(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, contract_id=c.id,
                              loan_type=LoanType.PERSONAL, dpd_bucket=DPDBucket.BUCKET_2, commission_pct=9.5))
    db.flush()
    svc = PlacementService(db)
    assert svc.place_new_loan(_loan(db, 1, dpd=47), agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    with pytest.raises(PlacementRefused) as r:
        svc.place_new_loan(_loan(db, 2, dpd=120), agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")  # NPA
    assert r.value.reason == NOT_AUTHORISED
    with pytest.raises(PlacementRefused) as r:
        svc.place_new_loan(_loan(db, 3, dpd=47, loan_type=LoanType.HOME), agency_id=TEST_AGENCY_ID, on=DAY,
                           source="FEED")
    assert r.value.reason == NOT_AUTHORISED


def test_a_full_contract_refuses_the_next_placement(db):
    _contract(db, cap=1)
    svc = PlacementService(db)
    svc.place_new_loan(_loan(db, 1), agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    with pytest.raises(PlacementRefused) as r:
        svc.place_new_loan(_loan(db, 2), agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    assert r.value.reason == CONTRACT_FULL


def test_a_suspended_agency_and_a_missing_agency_are_refused(db):
    suspended = _second_agency(db, status="SUSPENDED")
    _contract(db, agency_id=suspended.id, no="MTB/FCA/2026-27/015")
    svc = PlacementService(db)
    with pytest.raises(PlacementRefused) as r:
        svc.place_new_loan(_loan(db, 1), agency_id=suspended.id, on=DAY, source="FEED")
    assert r.value.reason == AGENCY_NOT_ACTIVE
    with pytest.raises(PlacementRefused) as r:
        svc.place_new_loan(_loan(db, 2), agency_id=None, on=DAY, source="FEED")
    assert r.value.reason == NO_AGENCY


def test_open_case_refuses_a_caller_supplied_tenant(db):
    _contract(db)
    loan = _loan(db)
    svc = PlacementService(db)
    p = svc.place_new_loan(loan, agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    with pytest.raises(TypeError, match="agency_id"):
        svc.open_case(p, loan, case_number="MTB-CASE-0002", target_amount=1.0, agency_id=test_id("elsewhere"))


def test_quarantine_holds_the_row_and_appends_reasons(db):
    import hashlib
    batch = BankFeedBatch(bank_id=TEST_BANK_ID, feed_type="DAILY_BOOK", business_date=DAY,
                          file_sha256=hashlib.sha256(b"x").hexdigest(), received_via="UPLOAD")
    db.add(batch)
    db.flush()
    svc = PlacementService(db)
    raw = {"loan_account_number": "MTB0000009", "customer_ref": "MTB-C-0009", "case_number": "MTB-CASE-9"}
    row = svc.quarantine(batch, row_no=9, raw=raw, reason=NO_CONTRACT_IN_FORCE, detail="no contract")
    again = svc.quarantine(batch, row_no=9, raw=raw, reason=NOT_AUTHORISED, detail="HOME not authorised")
    db.commit()
    assert again.id == row.id and row.status == "QUARANTINED"
    assert [e["reason"] for e in row.dq_errors] == [NO_CONTRACT_IN_FORCE, NOT_AUTHORISED]
    assert (row.loan_account_number, row.case_number) == ("MTB0000009", "MTB-CASE-9")


# ── P3 D08: coverage, the gate evaluator, capacity within a batch ────────────

def test_coverage_admits_the_covered_region_and_its_subtree(db):
    c = _contract(db, covered=False)
    cover(db, c, "HR", branch_region="GGN")            # HR covers its city GGN
    p = PlacementService(db).place_new_loan(_loan(db), agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    assert p.contract_id == c.id


@pytest.mark.parametrize("covered_code,branch_region", [
    ("GGN", "HR"),        # a city does not cover its parent state
    ("GGN", "NORTH"),
])
def test_coverage_refuses_a_region_outside_the_contract(db, covered_code, branch_region):
    c = _contract(db, covered=False)
    cover(db, c, covered_code, branch_region=branch_region)
    with pytest.raises(PlacementRefused) as r:
        PlacementService(db).place_new_loan(_loan(db), agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    assert r.value.reason == NOT_COVERED
    assert "does not cover region" in str(r.value)


def test_coverage_fails_closed_without_coverage_rows_or_a_branch_region(db):
    _contract(db, covered=False)                        # no agency_regions rows at all
    put_branches_in(db, "GGN")
    with pytest.raises(PlacementRefused) as r:
        PlacementService(db).place_new_loan(_loan(db, 1), agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    assert r.value.reason == NOT_COVERED

    c2 = _contract(db, no="MTB/FCA/2026-27/016", start=date(2026, 5, 1), covered=False)   # the newer contract wins
    region = region_tree(db)["NORTH"]
    db.add(AgencyRegion(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, contract_id=c2.id, region_id=region))
    for b in db.query(Branch).filter(Branch.bank_id == TEST_BANK_ID):
        b.region_id = None                              # covered contract, but the branch has no region
    db.flush()
    with pytest.raises(PlacementRefused) as r:
        PlacementService(db).place_new_loan(_loan(db, 2), agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    assert r.value.reason == NOT_COVERED
    assert "has no region" in str(r.value)


def test_a_region_code_that_only_shares_a_prefix_is_not_covered():
    assert path_covers("NORTH.HR", "NORTH.HR.GGN")
    assert path_covers("/NORTH/HR/", "NORTH.HR")          # both path spellings compare by segment
    assert not path_covers("NORTH.HR", "NORTH.HRX")
    assert not path_covers("NORTH.HR.GGN", "NORTH.HR")
    assert not path_covers("", "NORTH")
    assert not path_covers(None, "NORTH")


def test_evaluate_reports_every_failing_gate_without_raising(db):
    c = _contract(db, covered=False)
    db.add(AgencyContractTerm(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, contract_id=c.id,
                              loan_type=LoanType.HOME, dpd_bucket=DPDBucket.BUCKET_2, commission_pct=9.5))
    put_branches_in(db, "GGN")
    res = PlacementService(db).evaluate(_loan(db), TEST_AGENCY_ID, DAY)
    assert not res.ok
    assert {g: c.reason for g, c in res.checks.items() if c.passed is False} == {
        "authorisation": NOT_AUTHORISED, "coverage": NOT_COVERED}
    assert res.first_failure.reason == NOT_AUTHORISED          # the order place_new_loan raises in
    assert set(res.as_json()) == set(GATES)
    assert db.query(Placement).count() == 0


def test_evaluate_marks_gates_it_cannot_judge(db):
    res = PlacementService(db).evaluate(_loan(db), test_id("agency:nowhere"), DAY)
    assert res.checks["agency"].reason == NO_AGENCY
    assert [res.checks[g].passed for g in ("contract", "authorisation", "coverage", "capacity")] == [None] * 4
    assert res.first_failure.gate == "agency"


def test_evaluate_counts_placements_planned_in_the_same_batch(db):
    _contract(db, cap=3)
    svc = PlacementService(db)
    svc.place_new_loan(_loan(db, 1), agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    loan = _loan(db, 2)
    assert svc.evaluate(loan, TEST_AGENCY_ID, DAY, planned=1).ok            # 1 placed + 1 planned < 3
    full = svc.evaluate(loan, TEST_AGENCY_ID, DAY, planned=2)                # 1 + 2 = cap
    assert full.checks["capacity"].reason == CONTRACT_FULL
    assert svc.headroom(full.contract) == 2


def test_a_loan_already_held_by_the_agency_passes_capacity_at_the_cap(db):
    _contract(db, cap=1)
    svc = PlacementService(db)
    loan = _loan(db)
    svc.place_new_loan(loan, agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    res = svc.evaluate(loan, TEST_AGENCY_ID, DAY)
    assert res.ok and res.existing is not None


def test_place_new_loan_records_its_run(db):
    from app.models.planning import PlacementRun
    _contract(db)
    run = PlacementRun(bank_id=TEST_BANK_ID, plan_date=DAY, strategy="MANUAL_BATCH", status="APPLIED")
    db.add(run)
    db.flush()
    p = PlacementService(db).place_new_loan(_loan(db), agency_id=TEST_AGENCY_ID, on=DAY, source="MANUAL",
                                            placement_run_id=run.id)
    assert p.placement_run_id == run.id


def test_batch_facts_judge_exactly_as_evaluate_does(db):
    """The engine loads facts for a whole bank at once (agency_facts_many,
    loan_facts); the verdicts must be evaluate()'s, gate by gate."""
    from app.services.placement_service import judge
    c = _contract(db, cap=2)
    db.add(AgencyContractTerm(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, contract_id=c.id,
                              loan_type=LoanType.PERSONAL, dpd_bucket=DPDBucket.BUCKET_2, commission_pct=9.5))
    other = _second_agency(db)
    _contract(db, agency_id=other.id, no="MTB/FCA/2026-27/015", covered=False)       # covers nothing
    suspended = Agency(id=test_id("agency:susp"), bank_id=TEST_BANK_ID, code="AGY-S", legal_name="Suspended",
                       status="SUSPENDED", contacts=[], is_demo=True)
    db.add(suspended)
    db.flush()
    svc = PlacementService(db)
    svc.place_new_loan(_loan(db, 1), agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    loans = [_loan(db, 2), _loan(db, 3, dpd=120), _loan(db, 4, loan_type=LoanType.HOME), db.get(Loan, test_id("loan:1"))]
    many = svc.agency_facts_many(TEST_BANK_ID, DAY)
    assert set(many) == {TEST_AGENCY_ID, other.id, suspended.id}
    facts = svc.loan_facts(loans)
    for aid in many:
        for lf in facts:
            for planned in (0, 1):
                batch = judge(lf, many[aid], planned=planned).as_json()
                single = svc.evaluate(lf.loan, aid, DAY, planned=planned).as_json()
                assert batch == single, (aid, lf.loan.loan_account_number, planned)


# ── one-shot reconcile of placements on closed loans (coordinator, 2026-09-29) ──

@pytest.mark.parametrize("loan_status,end_status", [
    (LoanStatus.CLOSED, "RESOLVED"), (LoanStatus.SETTLED, "RESOLVED"), (LoanStatus.WRITTEN_OFF, "RETURNED"),
])
def test_reconcile_ends_a_placement_whose_loan_the_bank_closed(db, loan_status, end_status):
    from app.models.audit_log import AuditAction, AuditLog
    _contract(db)
    svc = PlacementService(db)
    closed, open_ = _loan(db, 1), _loan(db, 2)
    p_closed = svc.place_new_loan(closed, agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    p_open = svc.place_new_loan(open_, agency_id=TEST_AGENCY_ID, on=DAY, source="FEED")
    closed.status = loan_status
    db.commit()

    dry = svc.reconcile_orphans(TEST_BANK_ID, on=DAY, dry_run=True)
    assert (dry["ended"], dry["by_status"]) == (1, {end_status: 1}) and p_closed.status == "ACTIVE"

    out = svc.reconcile_orphans(TEST_BANK_ID, on=DAY)
    db.commit()
    assert out["ended"] == 1
    assert (p_closed.status, p_closed.end_reason, p_closed.ended_on, p_closed.ended_by) == (
        end_status, "FEED_RECONCILE", DAY, None)
    assert p_open.status == "ACTIVE"
    audit = db.query(AuditLog).filter(AuditLog.action == AuditAction.PLACEMENT_ENDED).one()
    assert (audit.user_id, audit.entity_id, audit.details["source"]) == (None, p_closed.id, "RECONCILE")
    assert (audit.bank_id, audit.agency_id) == (p_closed.bank_id, p_closed.agency_id)
    assert svc.reconcile_orphans(TEST_BANK_ID, on=DAY)["ended"] == 0                   # idempotent
    assert svc.reconcile_orphans(test_id("bank:other"), on=DAY)["ended"] == 0         # another bank: nothing
