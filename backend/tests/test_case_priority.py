"""
cases.priority — one rule, re-derived nightly. 2026-09-21.
"""
from __future__ import annotations

import pathlib
import re

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.case import Case, CasePriority, CaseStatus, priority_for
from app.models.customer import Customer, RiskCategory
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType, dpd_bucket_for
from app.services.case_priority_service import restamp
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401


@pytest.mark.parametrize("dpd,want", [
    (None, CasePriority.LOW), (0, CasePriority.LOW), (30, CasePriority.LOW),
    (31, CasePriority.MEDIUM), (60, CasePriority.MEDIUM),
    (61, CasePriority.HIGH), (90, CasePriority.HIGH),
    (91, CasePriority.CRITICAL), (400, CasePriority.CRITICAL),
])
def test_the_ladder(dpd, want):
    assert priority_for(dpd) is want


def test_the_ladder_shares_the_dpd_bucket_edges():
    # Same bands as dpd_bucket_for, so the two labels of one loan never disagree.
    for dpd in range(0, 200):
        b = dpd_bucket_for(dpd); p = priority_for(dpd)
        if b is DPDBucket.NPA:
            assert p is CasePriority.CRITICAL
        elif b is DPDBucket.BUCKET_3:
            assert p is CasePriority.HIGH
        elif b is DPDBucket.BUCKET_2:
            assert p is CasePriority.MEDIUM
        else:
            assert p is CasePriority.LOW


def test_nobody_restates_the_ladder():
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for folder in ("app", "scripts"):
        for f in (root / folder).rglob("*.py"):
            if f.name == "case.py" and f.parent.name == "models":
                continue
            if f.name == "analyse_priority_shift.py":
                continue   # read-only historical comparison of the OLD score-based rule; documents it, does not write it
            code = "\n".join(line.split("#", 1)[0] for line in f.read_text(encoding="utf-8").splitlines())
            if re.search(r"CasePriority\.(CRITICAL|HIGH|MEDIUM)\s+if\s", code) or "_priority_from_score" in code:
                offenders.append(str(f.relative_to(root)))
    assert offenders == [], offenders


def _book():
    engine = make_engine()
    create_schema(bind=engine)
    db = make_session_factory(autocommit=False, autoflush=False, bind=engine)()
    cust = Customer(customer_ref="CP1", full_name="B", date_of_birth="1990-01-01", gender="M", pan_masked="X",
                    aadhaar_masked="X", phone_primary="9000000001", address_line1="1", city="Delhi", state="DL",
                    pincode="110001", latitude=28.6, longitude=77.2, risk_category=RiskCategory.MEDIUM)
    db.add(cust); db.flush()

    def loan(dpd):
        l = Loan(loan_account_number=f"CPL{dpd}-{db.query(Loan).count()}", customer_id=cust.id, loan_type=LoanType.PERSONAL, bank_name="B",
                 branch_code="BR", sanctioned_amount=1.0, disbursed_amount=1.0, outstanding_principal=1.0, total_outstanding=1.0,
                 overdue_amount=1.0, emi_amount=1.0, disbursement_date="2025-01-01", maturity_date="2027-01-01", dpd=dpd,
                 dpd_bucket=dpd_bucket_for(dpd), status=LoanStatus.ACTIVE, interest_rate=1.0, penal_charges=0.0)
        db.add(l); db.flush(); return l

    def case(dpd, stored, status=CaseStatus.ASSIGNED):
        c = Case(case_number=f"CP-{db.query(Case).count()}", customer_id=cust.id, loan_id=loan(dpd).id, agent_id=None,
                 status=status, priority=stored, target_amount=1.0, collected_amount=0.0, allocation_date=None)
        db.add(c); db.flush(); return c
    return db, case


def test_restamp_fixes_drift_and_leaves_closed_cases_alone():
    db, case = _book()
    drifted = case(120, CasePriority.MEDIUM)            # aged past NPA since creation
    cured = case(40, CasePriority.CRITICAL)             # paid down, DPD fell
    fine = case(75, CasePriority.HIGH)
    closed = case(150, CasePriority.LOW, status=CaseStatus.PAID)   # history, never rewritten
    db.commit()
    dry = restamp(db, dry_run=True)
    assert dry["changed"] == 2 and db.get(Case, drifted.id).priority is CasePriority.MEDIUM
    out = restamp(db)
    assert out["open_cases"] == 3 and out["changed"] == 2
    assert db.get(Case, drifted.id).priority is CasePriority.CRITICAL
    assert db.get(Case, cured.id).priority is CasePriority.MEDIUM
    assert db.get(Case, fine.id).priority is CasePriority.HIGH
    assert db.get(Case, closed.id).priority is CasePriority.LOW
    assert restamp(db)["changed"] == 0     # idempotent
    assert {(b, a) for _, b, a in out["changes"]} == {("MEDIUM", "CRITICAL"), ("CRITICAL", "MEDIUM")}


def test_the_nightly_task_calls_the_restamp():
    src = (pathlib.Path(__file__).resolve().parents[1] / "app/workers/tasks/repayment_scoring.py").read_text(encoding="utf-8")
    assert "case_priority_service import restamp" in src and "restamp(db)" in src
