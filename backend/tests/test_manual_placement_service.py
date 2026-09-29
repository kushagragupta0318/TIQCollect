"""services/manual_placement_service.py — D08: a bank places loans by hand,
and recalls one placement by hand.

The session carries NO default tenant (production-shaped), so every bank and
agency id comes from real rows.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core.errors import AppException
from app.core.security import hash_password
from app.ml.pipeline.config import RECOVERY_RISK
from app.ml.pipeline.outcomes import RECALL_NOTE_PREFIX
from app.models.audit_log import AuditAction, AuditLog
from app.models.case import Case, CaseStatus
from app.models.loan import LoanStatus
from app.models.model_prediction import ModelPrediction
from app.models.placement import Placement
from app.models.planning import PlacementDecision, PlacementRun
from app.models.tenancy import Agency, AgencyContract, Bank, Branch
from app.models.user import User, UserRole
from app.services.manual_placement_service import MAX_BATCH, ManualPlacementService
from app.services.placement_service import PlacementService
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id
from tests._placement import cover, make_loan

DAY = date(2026, 9, 29)
BANK2 = test_id("bank:girivan")
AGENCY2 = test_id("agency:almora-recovery")          # works for bank 2


@pytest.fixture()
def db():
    engine = make_engine()
    create_schema(engine)
    s = make_session_factory(engine, info={})()
    s.add(Bank(id=BANK2, code="GFL", legal_name="Girivan Finance Ltd.", display_name="Girivan Finance",
               timezone="Asia/Kolkata", brand={}, status="ACTIVE", is_demo=True))
    s.flush()
    s.add_all([
        Agency(id=AGENCY2, bank_id=BANK2, code="AGY-ALM", legal_name="Almora Recovery Desk LLP",
               status="ACTIVE", contacts=[], is_demo=True),
        Branch(id=test_id("branch:gfl"), bank_id=BANK2, branch_code="GFL001", name="Girivan Pune", is_active=True),
        User(id=test_id("u:ba"), email="ba@example.test", phone="9800000002", full_name="Nandini Rao",
             hashed_password=hash_password("Harbour-Lights-2026"), role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID),
    ])
    s.commit()
    yield s
    s.close()


ACTOR = test_id("u:ba")


def _contract(db, *, agency_id=TEST_AGENCY_ID, bank_id=TEST_BANK_ID, cap=None):
    c = AgencyContract(bank_id=bank_id, agency_id=agency_id, contract_no=f"C/{agency_id[:6]}", status="ACTIVE",
                       start_date=date(2026, 4, 1), end_date=date(2027, 3, 31), max_placed_cases=cap,
                       sla_first_visit_days=5)
    db.add(c)
    db.flush()
    cover(db, c)
    db.commit()
    return c


def _ids(loans):
    return [l.id for l in loans]


# ── preview ──────────────────────────────────────────────────────────────────

def test_preview_judges_every_loan_counts_capacity_across_the_batch_and_writes_nothing(db):
    _contract(db, cap=2)
    loans = [make_loan(db, n) for n in (1, 2, 3)]
    closed = make_loan(db, 4, status=LoanStatus.CLOSED)
    db.commit()
    out = ManualPlacementService(db).preview(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID,
                                            loan_ids=_ids(loans + [closed]), on=DAY)
    assert [v.outcome for v in out.verdicts] == ["PLACED", "PLACED", "BLOCKED", "BLOCKED"]
    assert out.verdicts[2].reason.startswith("CONTRACT_FULL")
    assert out.verdicts[3].reason.startswith("LOAN_NOT_OPEN")
    assert out.verdicts[0].gates["coverage"]["passed"] is True
    assert out.headroom_before == 2
    assert db.query(Placement).count() == db.query(PlacementRun).count() == 0


# ── apply ────────────────────────────────────────────────────────────────────

def test_apply_places_what_passes_and_records_every_verdict_on_one_run(db):
    _contract(db, cap=2)
    loans = [make_loan(db, n) for n in (1, 2, 3)]
    db.commit()
    out = ManualPlacementService(db).apply(bank_id=TEST_BANK_ID, actor_id=ACTOR, agency_id=TEST_AGENCY_ID,
                                          loan_ids=_ids(loans), on=DAY)
    assert [v.outcome for v in out.verdicts] == ["PLACED", "PLACED", "BLOCKED"]

    run = db.get(PlacementRun, out.run_id)
    assert (run.strategy, run.status, run.created_by, run.applied_by) == ("MANUAL_BATCH", "APPLIED", ACTOR, ACTOR)
    assert (run.total_loans_evaluated, run.total_placed, run.total_blocked) == (3, 2, 0 + 1)

    decisions = db.query(PlacementDecision).filter_by(run_id=run.id).all()
    assert sorted(d.outcome for d in decisions) == ["BLOCKED", "PLACED", "PLACED"]
    blocked = next(d for d in decisions if d.outcome == "BLOCKED")
    assert blocked.chosen_agency_id is None and blocked.gate_results["capacity"]["reason"] == "CONTRACT_FULL"

    placements = db.query(Placement).all()
    assert {(p.source, p.placement_run_id, p.placed_by) for p in placements} == {("MANUAL", run.id, ACTOR)}
    cases = db.query(Case).all()
    assert len(cases) == 2
    assert all(c.status == CaseStatus.UNASSIGNED and c.target_amount == 24600.0 for c in cases)
    assert {c.case_number for c in cases} == {v.case_number for v in out.verdicts if v.case_number}

    audits = db.query(AuditLog).filter(AuditLog.action == AuditAction.PLACEMENT_CREATED).all()
    assert len(audits) == 2 and {a.user_id for a in audits} == {ACTOR}
    assert {a.entity_id for a in audits} == {p.id for p in placements}


def test_a_second_apply_keeps_what_is_already_placed(db):
    _contract(db)
    loan = make_loan(db)
    db.commit()
    svc = ManualPlacementService(db)
    first = svc.apply(bank_id=TEST_BANK_ID, actor_id=ACTOR, agency_id=TEST_AGENCY_ID, loan_ids=[loan.id], on=DAY)
    again = svc.apply(bank_id=TEST_BANK_ID, actor_id=ACTOR, agency_id=TEST_AGENCY_ID, loan_ids=[loan.id], on=DAY)
    assert again.verdicts[0].outcome == "KEPT"
    assert again.verdicts[0].placement_id == first.verdicts[0].placement_id
    assert db.query(Placement).count() == db.query(Case).count() == 1


def test_a_target_falls_back_to_outstanding_when_nothing_is_overdue(db):
    _contract(db)
    loan = make_loan(db, overdue=0.0)
    db.commit()
    ManualPlacementService(db).apply(bank_id=TEST_BANK_ID, actor_id=ACTOR, agency_id=TEST_AGENCY_ID,
                                     loan_ids=[loan.id], on=DAY)
    assert db.query(Case).one().target_amount == 281000.0


@pytest.mark.parametrize("who", ["loan", "agency"])
def test_another_banks_loan_or_agency_is_not_found(db, who):
    _contract(db)
    _contract(db, agency_id=AGENCY2, bank_id=BANK2)
    mine = make_loan(db, 1)
    theirs = make_loan(db, 2, bank_id=BANK2, branch_code="GFL001")
    db.commit()
    loan_ids, agency = ([mine.id, theirs.id], TEST_AGENCY_ID) if who == "loan" else ([mine.id], AGENCY2)
    svc = ManualPlacementService(db)
    for call in (svc.preview, svc.apply):
        kw = {"actor_id": ACTOR} if call == svc.apply else {}
        with pytest.raises(AppException) as e:
            call(bank_id=TEST_BANK_ID, agency_id=agency, loan_ids=loan_ids, on=DAY, **kw)
        assert e.value.status_code == 404
    assert db.query(Placement).count() == db.query(PlacementRun).count() == 0


def test_batch_size_is_bounded_and_must_not_be_empty(db):
    _contract(db)
    svc = ManualPlacementService(db)
    for ids in ([], [test_id(f"x{i}") for i in range(MAX_BATCH + 1)]):
        with pytest.raises(AppException) as e:
            svc.preview(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, loan_ids=ids, on=DAY)
        assert e.value.status_code == 422


# ── expected recovery: from a recorded prediction, or abstain ───────────────

def _prediction(db, loan, *, as_of, p_bad, modelled=True, name=RECOVERY_RISK.name):
    row = ModelPrediction(bank_id=loan.bank_id, model_name=name, model_version="2.2.0", entity_type="loan",
                          entity_id=loan.id, loan_id=loan.id, as_of_date=as_of,
                          probability=p_bad if modelled else None, is_modelled=modelled,
                          fallback_reason=None if modelled else "coverage")
    db.add(row)
    db.flush()
    return row


def test_expected_recovery_is_the_complement_of_the_newest_prior_prediction(db):
    _contract(db)
    loan = make_loan(db)
    _prediction(db, loan, as_of=date(2026, 9, 1), p_bad=0.70)
    newest = _prediction(db, loan, as_of=date(2026, 9, 20), p_bad=0.62)
    _prediction(db, loan, as_of=date(2026, 9, 30), p_bad=0.10)                 # after the day: unseen
    _prediction(db, loan, as_of=date(2026, 9, 25), p_bad=0.0, modelled=False)  # declined: unseen
    _prediction(db, loan, as_of=date(2026, 9, 26), p_bad=0.05, name="repayment_scorecard")
    db.commit()
    ManualPlacementService(db).apply(bank_id=TEST_BANK_ID, actor_id=ACTOR, agency_id=TEST_AGENCY_ID,
                                     loan_ids=[loan.id], on=DAY)
    p = db.query(Placement).one()
    assert p.expected_recovery_prob == pytest.approx(0.38)
    assert (p.model_prediction_id, p.model_prediction_as_of) == (newest.id, date(2026, 9, 20))
    assert p.expected_recovery_inr is None                     # D06 defines the amount


def test_expected_recovery_abstains_without_a_prediction(db):
    _contract(db)
    loan = make_loan(db)
    db.commit()
    ManualPlacementService(db).apply(bank_id=TEST_BANK_ID, actor_id=ACTOR, agency_id=TEST_AGENCY_ID,
                                     loan_ids=[loan.id], on=DAY)
    p = db.query(Placement).one()
    assert p.expected_recovery_prob is None and p.model_prediction_id is None


# ── case numbers ─────────────────────────────────────────────────────────────

def test_case_numbers_fit_the_column_carry_the_day_and_are_distinct(db):
    _contract(db)
    loans = [make_loan(db, n) for n in range(1, 41)]
    db.commit()
    ManualPlacementService(db).apply(bank_id=TEST_BANK_ID, actor_id=ACTOR, agency_id=TEST_AGENCY_ID,
                                     loan_ids=_ids(loans), on=DAY)
    numbers = [c.case_number for c in db.query(Case)]
    assert len(set(numbers)) == 40
    assert all(n.startswith("PL260929") and len(n) == 16 for n in numbers)
    p = db.query(Placement).first()
    assert PlacementService.case_number_for(p) == db.query(Case).filter_by(placement_id=p.id).one().case_number


# ── manual recall ────────────────────────────────────────────────────────────

def _placed(db, n=1):
    loan = make_loan(db, n)
    db.commit()
    out = ManualPlacementService(db).apply(bank_id=TEST_BANK_ID, actor_id=ACTOR, agency_id=TEST_AGENCY_ID,
                                          loan_ids=[loan.id], on=DAY)
    return loan, db.get(Placement, out.verdicts[0].placement_id)


def test_recall_ends_the_placement_closes_its_open_cases_and_names_the_actor(db):
    _contract(db)
    loan, p = _placed(db)
    paid = PlacementService(db).open_case(p, loan, case_number="OLD-PAID-1", target_amount=1.0,
                                          status=CaseStatus.PAID)
    db.commit()
    placement, closed = ManualPlacementService(db).recall(
        bank_id=TEST_BANK_ID, actor_id=ACTOR, placement_id=p.id, note="  Borrower moved   to Pune ", on=DAY)
    assert (placement.status, placement.ended_on, placement.ended_by, placement.end_reason) == (
        "RECALLED", DAY, ACTOR, "MANUAL_RECALL")
    assert len(closed) == 1
    case = closed[0]
    assert (case.status, case.closure_reason) == (CaseStatus.CLOSED, "RECALLED")
    assert case.resolution_notes.startswith(RECALL_NOTE_PREFIX)            # the labeller censors on it
    assert "Borrower moved to Pune" in case.resolution_notes
    assert db.get(Case, paid.id).status == CaseStatus.PAID                  # a resolved case is left alone
    audit = db.query(AuditLog).filter(AuditLog.action == AuditAction.PLACEMENT_RECALLED).one()
    assert (audit.user_id, audit.entity_id, audit.details["reason"]) == (ACTOR, p.id, "Borrower moved to Pune")


def test_a_recalled_loan_can_be_placed_again_as_a_new_placement_and_case(db):
    _contract(db)
    loan, p = _placed(db)
    svc = ManualPlacementService(db)
    svc.recall(bank_id=TEST_BANK_ID, actor_id=ACTOR, placement_id=p.id, note="SLA missed", on=DAY)
    again = svc.apply(bank_id=TEST_BANK_ID, actor_id=ACTOR, agency_id=TEST_AGENCY_ID, loan_ids=[loan.id], on=DAY)
    assert again.verdicts[0].outcome == "PLACED"
    assert again.verdicts[0].placement_id != p.id
    assert db.query(Placement).count() == 2 and db.query(Case).count() == 2


def test_recall_refuses_no_reason_another_bank_and_a_second_recall(db):
    _contract(db)
    _, p = _placed(db)
    svc = ManualPlacementService(db)
    for bank, note, status in ((TEST_BANK_ID, "   ", 422), (TEST_BANK_ID, "x" * 501, 422), (BANK2, "moved", 404)):
        with pytest.raises(AppException) as e:
            svc.recall(bank_id=bank, actor_id=ACTOR, placement_id=p.id, note=note, on=DAY)
        assert e.value.status_code == status
    svc.recall(bank_id=TEST_BANK_ID, actor_id=ACTOR, placement_id=p.id, note="moved", on=DAY)
    with pytest.raises(AppException) as e:
        svc.recall(bank_id=TEST_BANK_ID, actor_id=ACTOR, placement_id=p.id, note="moved", on=DAY)
    assert e.value.status_code == 409
