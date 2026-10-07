"""E06 cash forecast — real-Postgres tenant isolation for the bank-wide money
aggregate (coordinator/12 review, 2026-10-07): a dropped `WHERE bank_id` on
any of the four raw-SQL reads in app/strategy/cash_forecast.py would read as
a wrong number, not as leaked data, and the fake-session unit tests in
test_cash_forecast_reads.py cannot catch it — they feed the rows directly, so
the SQL text itself never runs there (schema-qualified `text()` SQL does not
go through SQLite's schema_translate_map; the same reason history.py and
transitions.py are fake-session-only).

This seeds two real banks on a throwaway Postgres database, with bank B's
numbers deliberately much larger, and proves bank A's figures — through
every one of the module's reads, and through build_cash_forecast end to
end — are exactly what bank A's own book produces, unmoved by bank B's rows.

Needs TIQ_PG_TEST_URL, same as the rest of tests/pg; skipped otherwise
(conftest.py).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import insert
from sqlalchemy.orm import sessionmaker

from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.lookups import LOOKUP_MODELS, LOOKUP_SEEDS
from app.models.model_prediction import ModelPrediction
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.models.tenancy import Agency, Bank, Branch
from app.models.user import User, UserRole
from app.strategy import cash_forecast as CF

pytestmark = pytest.mark.filterwarnings("ignore")

AS_OF = date(2026, 10, 7)
N_WEEKS = CF.MIN_WEEKS_HISTORY + 2   # enough to forecast without abstaining


def _uid() -> str:
    return str(uuid.uuid4())


def _phone() -> str:
    return "98" + _uid().replace("-", "")[:8]


def _bank(db, *, code: str) -> tuple[str, str, str]:
    bank_id = _uid()
    db.execute(insert(Bank.__table__), [{
        "id": bank_id, "code": code, "legal_name": f"{code} Bank Ltd.",
        "display_name": f"{code} Bank", "timezone": "Asia/Kolkata", "brand": {},
        "status": "ACTIVE", "is_demo": True,
    }])
    agency_id = _uid()
    db.execute(insert(Agency.__table__), [{
        "id": agency_id, "bank_id": bank_id, "code": f"{code}-AGY", "legal_name": f"{code} Agency",
        "trade_name": f"{code} Agency", "status": "ACTIVE", "contacts": [], "is_demo": True,
    }])
    branch_code = f"{code}01"
    db.execute(insert(Branch.__table__), [{
        "id": _uid(), "bank_id": bank_id, "branch_code": branch_code, "name": f"{code} Branch",
        "is_active": True,
    }])
    db.commit()
    return bank_id, agency_id, branch_code


def _loan(db, bank_id, branch_code, customer, *, overdue: float):
    loan = Loan(id=_uid(), bank_id=bank_id, customer_id=customer.id, loan_account_number="L" + _uid()[:6],
               loan_type=LoanType.PERSONAL, branch_code=branch_code, sanctioned_amount=100000.0,
               disbursed_amount=100000.0, outstanding_principal=50000.0, total_outstanding=50000.0,
               overdue_amount=overdue, emi_amount=5000.0, interest_rate=12.0,
               disbursement_date=date(2022, 1, 1), maturity_date=date(2027, 1, 1),
               dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE)
    db.add(loan); db.flush()
    return loan


def _book(db, bank_id: str, agency_id: str, branch_code: str, *, scale: float) -> None:
    """One bank's whole book for this test: `scale` makes bank B's numbers
    unmistakably different from bank A's (never a coincidental match)."""
    # Real Postgres session: no tests/_db.py `default_tenant` listener to fill
    # bank_id/agency_id in from context, so every tenant column is explicit.
    cust = Customer(id=_uid(), bank_id=bank_id, customer_ref=_uid()[:8], full_name="Borrower",
                    date_of_birth=date(1985, 1, 1), gender="M", pan_masked="ABCDE1234F",
                    aadhaar_masked="123456789012", phone_primary=_phone(), address_line1="D", city="D",
                    state="D", pincode="110001", latitude=28.6, longitude=77.2, language_preference="HINDI")
    db.add(cust); db.flush()

    mgr = User(id=_uid(), email=f"mgr-{_uid()[:6]}@t.io", phone=_phone(), full_name="Manager",
              hashed_password="x", role=UserRole.AGENCY_MANAGER, bank_id=bank_id, agency_id=agency_id)
    db.add(mgr); db.flush()
    # ck_users_role_scope (Postgres-enforced): FIELD_AGENT needs BOTH bank_id
    # and agency_id, unlike BANK_ADMIN's bank-only scope.
    agent_user = User(id=_uid(), email=f"fa-{_uid()[:6]}@t.io", phone=_phone(), full_name="Field Agent",
                      hashed_password="x", role=UserRole.FIELD_AGENT, bank_id=bank_id, agency_id=agency_id)
    db.add(agent_user); db.flush()
    agent = Agent(id=_uid(), agency_id=agency_id, user_id=agent_user.id, employee_code="E" + _uid()[:5],
                 id_card_number="E-ID" + _uid()[:4], manager_user_id=mgr.id, gender="M",
                 base_latitude=28.6, base_longitude=77.2, territory="Delhi",
                 languages_spoken=["HINDI"], status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
                 specialization=AgentSpecialization.BOTH, ranking_score=80.0, max_cases_per_day=5,
                 current_month_collections=5000.0)
    db.add(agent); db.flush()

    loan = _loan(db, bank_id, branch_code, cust, overdue=9000.0 * scale)
    case = Case(id=_uid(), bank_id=bank_id, agency_id=agency_id, case_number="C-" + _uid()[:6],
               customer_id=cust.id, loan_id=loan.id, agent_id=agent.id, status=CaseStatus.ASSIGNED,
               target_amount=10000.0, collected_amount=0.0, allocation_date=AS_OF)
    db.add(case); db.flush()

    base = datetime.combine(AS_OF, datetime.min.time(), tzinfo=timezone.utc)
    for w in range(1, N_WEEKS + 1):
        db.add(Payment(id=_uid(), bank_id=bank_id, agency_id=agency_id, case_id=case.id, loan_id=loan.id,
                      amount=1000.0 * scale, mode=PaymentMode.CASH, status=PaymentStatus.VERIFIED,
                      receipt_number="R" + _uid()[:10], payment_date=base - timedelta(weeks=w) + timedelta(days=2)))
    # one ACTIVE PTP in the horizon, one HONORED PTP in the lookback (gives a
    # real, bank-specific honor rate rather than the "no history" None case).
    db.add(PTP(id=_uid(), bank_id=bank_id, agency_id=agency_id, case_id=case.id, agent_id=agent.id,
              committed_amount=2000.0 * scale, committed_date=AS_OF + timedelta(days=3),
              status=PTPStatus.ACTIVE))
    db.add(PTP(id=_uid(), bank_id=bank_id, agency_id=agency_id, case_id=case.id, agent_id=agent.id,
              committed_amount=1000.0 * scale, committed_date=AS_OF - timedelta(weeks=1),
              status=PTPStatus.HONORED, actual_paid_amount=1000.0 * scale))

    # a second loan, no PTP, scored by recovery_risk — the term the leaked-row
    # would also inflate if the exclusion-of-PTP'd-loans join lost its bank_id.
    loan2 = _loan(db, bank_id, branch_code, cust, overdue=7000.0 * scale)
    case2 = Case(id=_uid(), bank_id=bank_id, agency_id=agency_id, case_number="C-" + _uid()[:6],
                customer_id=cust.id, loan_id=loan2.id, agent_id=agent.id, status=CaseStatus.ASSIGNED,
                target_amount=7000.0, collected_amount=0.0, allocation_date=AS_OF)
    db.add(case2); db.flush()
    db.add(ModelPrediction(id=_uid(), bank_id=bank_id, model_name="recovery_risk", model_version="2.2.0",
                           entity_type="loan", entity_id=loan2.id, loan_id=loan2.id, as_of_date=AS_OF,
                           probability=0.3, is_modelled=True))
    db.commit()


def _seed_lookups(db) -> None:
    """alembic builds the schema, not the reference rows (loans.legal_status
    etc. are tiny lookup tables normally filled by scripts/seed_data.py) —
    without this, Loan's legal_status/settlement_status FKs have nothing to
    point at on a bare `upgrade head` database."""
    for table, rows in LOOKUP_SEEDS.items():
        db.execute(insert(LOOKUP_MODELS[table].__table__), rows)
    db.commit()


@pytest.fixture(scope="module")
def two_banks(pg_engine):
    Session = sessionmaker(bind=pg_engine)
    db = Session()
    _seed_lookups(db)
    bank_a, agency_a, branch_a = _bank(db, code="BANKA")
    bank_b, agency_b, branch_b = _bank(db, code="BANKB")
    _book(db, bank_a, agency_a, branch_a, scale=1.0)
    _book(db, bank_b, agency_b, branch_b, scale=50.0)   # unmistakably larger
    try:
        yield db, bank_a, bank_b
    finally:
        db.close()


def test_every_read_is_isolated_from_a_second_banks_rows(two_banks):
    db, bank_a, bank_b = two_banks

    history_a = CF.read_weekly_payments(db, bank_a, as_of=AS_OF)
    assert history_a.amounts.sum() == pytest.approx(1000.0 * N_WEEKS)

    ptp_a = CF.read_ptp_schedule(db, bank_a, as_of=AS_OF)
    assert ptp_a.sum() == pytest.approx(2000.0)

    rate_a, n_a = CF.read_ptp_honor_rate(db, bank_a, as_of=AS_OF)
    assert n_a == 1 and rate_a == pytest.approx(1.0)

    recovered_a, loans_a = CF.read_recovery_risk_next_cycle(db, bank_a, as_of=AS_OF)
    assert loans_a == 1
    assert recovered_a == pytest.approx(0.7 * 7000.0)

    run_a = CF.build_cash_forecast(db, bank_a, as_of=AS_OF)
    assert run_a.history_weeks == N_WEEKS
    assert run_a.ptp_honor_rate == pytest.approx(1.0)
    assert run_a.recovery_informed_loans == 1
    assert run_a.recovery_informed_total == pytest.approx(0.7 * 7000.0)

    # Read independently for bank B too: proves isolation runs both ways —
    # bank A isn't just the one that happens to be small.
    history_b = CF.read_weekly_payments(db, bank_b, as_of=AS_OF)
    assert history_b.amounts.sum() == pytest.approx(50000.0 * N_WEEKS)
    recovered_b, loans_b = CF.read_recovery_risk_next_cycle(db, bank_b, as_of=AS_OF)
    assert loans_b == 1
    assert recovered_b == pytest.approx(0.7 * 7000.0 * 50.0)

    # Re-reading bank A after bank B's reads changes nothing — the isolation
    # is per call, not an artefact of insert order.
    history_a_again = CF.read_weekly_payments(db, bank_a, as_of=AS_OF)
    assert history_a_again.amounts.tolist() == history_a.amounts.tolist()
