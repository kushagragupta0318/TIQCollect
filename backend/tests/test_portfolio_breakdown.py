"""The book broken down by branch, city and product, not only by DPD bucket
(known issue 8).

The bucket dimension is the query that used to be inline in manager.py, so the
first test here pins its output shape: the agency page already reads it and a
refactor that changed a figure would be a silent regression, not a failure.

SQLite, no network: the service is ordinary SQLAlchemy over the transactional
tables, which is the whole reason branch costs nothing here (the bank's
materialized views are not involved).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.case import Case, CaseStatus
from app.models.customer import Customer, RiskCategory
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.user import User, UserRole
from app.services import portfolio_breakdown as pb

from tests._db import create_schema, drop_schema, make_engine, make_session_factory  # noqa: F401

# Branch codes must be ones tests/_db seeds: loans carry a composite FK
# (bank_id, branch_code) -> tenancy.branches, enforced on SQLite too.

engine = make_engine()
TestingSession = make_session_factory(autocommit=False, autoflush=False, bind=engine)
TODAY = date.today()


def _customer(db, ref: str, city: str) -> Customer:
    c = Customer(customer_ref=ref, full_name=f"Borrower {ref}", date_of_birth=date(1990, 1, 1),
                 gender="F", pan_masked="XXXXX1234X", aadhaar_masked="XXXXXXXX5678",
                 phone_primary="99999" + ref[-5:].rjust(5, "0"), address_line1="1 Road",
                 city=city, state="DL", pincode="110001", latitude=28.6, longitude=77.2,
                 risk_category=RiskCategory.MEDIUM)
    db.add(c)
    db.flush()
    return c


def _loan(db, customer, *, number: str, product: LoanType, branch: str,
          bucket: DPDBucket = DPDBucket.BUCKET_2) -> Loan:
    loan = Loan(loan_account_number=number, customer_id=customer.id, loan_type=product,
                branch_code=branch, sanctioned_amount=100000.0, disbursed_amount=100000.0,
                outstanding_principal=80000.0, total_outstanding=90000.0, overdue_amount=5000.0,
                emi_amount=4000.0, disbursement_date=date(2025, 1, 1),
                maturity_date=date(2027, 1, 1), dpd=45, dpd_bucket=bucket,
                status=LoanStatus.ACTIVE, interest_rate=14.0, penal_charges=200.0)
    db.add(loan)
    db.flush()
    return loan


def _case(db, customer, loan, agent_id, *, number: str, target: float, collected: float) -> Case:
    c = Case(case_number=number, customer_id=customer.id, loan_id=loan.id, agent_id=agent_id,
             status=CaseStatus.IN_PROGRESS, target_amount=target, collected_amount=collected,
             allocation_date=TODAY)
    db.add(c)
    db.flush()
    return c


@pytest.fixture
def world():
    create_schema(engine)
    db = TestingSession()
    mine = User(email="m1@t.io", phone="9000000001", full_name="Manager One",
                hashed_password="x", role=UserRole.AGENCY_MANAGER)
    theirs = User(email="m2@t.io", phone="9000000002", full_name="Manager Two",
                  hashed_password="x", role=UserRole.AGENCY_MANAGER)
    au1 = User(email="a1@t.io", phone="9000000003", full_name="Agent One",
               hashed_password="x", role=UserRole.FIELD_AGENT)
    au2 = User(email="a2@t.io", phone="9000000004", full_name="Agent Two",
               hashed_password="x", role=UserRole.FIELD_AGENT)
    db.add_all([mine, theirs, au1, au2])
    db.flush()

    def agent(code, user_id, manager_id):
        a = Agent(user_id=user_id, employee_code=code, id_card_number=code + "-ID",
                  base_latitude=28.63, base_longitude=77.21, territory="Delhi",
                  languages_spoken=["HINDI"], status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
                  specialization=AgentSpecialization.BOTH, ranking_score=80.0)
        a.manager_user_id = manager_id
        db.add(a)
        return a

    ag_mine = agent("EMP001", au1.id, mine.id)
    ag_theirs = agent("EMP002", au2.id, theirs.id)
    db.flush()

    delhi = _customer(db, "CUST1", "Delhi")
    pune = _customer(db, "CUST2", "Pune")
    # Two products, two branches, two cities, so every dimension has more than
    # one group and a wrong GROUP BY collapses visibly rather than subtly.
    l_personal = _loan(db, delhi, number="LN1", product=LoanType.PERSONAL, branch="BR1")
    l_gold = _loan(db, pune, number="LN2", product=LoanType.GOLD, branch="BR01",
                   bucket=DPDBucket.NPA)
    case_personal = _case(db, delhi, l_personal, ag_mine.id, number="C1",
                          target=10000.0, collected=3000.0)
    case_gold = _case(db, pune, l_gold, ag_mine.id, number="C2", target=5000.0, collected=1000.0)
    # Another manager's case, on a third branch and city: never in my rows.
    kochi = _customer(db, "CUST3", "Kochi")
    l_other = _loan(db, kochi, number="LN3", product=LoanType.AUTO, branch="GG01")
    _case(db, kochi, l_other, ag_theirs.id, number="C3", target=7000.0, collected=7000.0)
    db.commit()
    yield {"db": db, "mine": [ag_mine.id], "theirs": [ag_theirs.id], "manager": mine,
           "cases": {"personal": case_personal, "gold": case_gold},
           "customers": {"delhi": delhi, "pune": pune}, "loans": {"personal": l_personal}}
    db.close()
    drop_schema(engine)


def _by_key(rows):
    return {r.key: r for r in rows}


def test_the_bucket_breakdown_keeps_the_numbers_the_inline_version_produced(world):
    """The agency page reads this today; a refactor must not move a figure."""
    rows = pb.breakdown(world["db"], dimension="bucket", agent_ids=world["mine"])
    by = _by_key(rows)
    assert set(by) == {"BUCKET_2", "NPA"}
    assert by["BUCKET_2"].case_count == 1
    assert by["BUCKET_2"].target_lakhs == 0.1          # 10,000 / 100,000
    assert by["BUCKET_2"].collected_lakhs == 0.03      # 3,000 / 100,000
    assert by["BUCKET_2"].collection_rate_pct == 30.0
    assert by["NPA"].collection_rate_pct == 20.0       # 1,000 of 5,000


def test_buckets_stay_in_severity_order_and_everything_else_leads_with_the_money(world):
    buckets = [r.key for r in pb.breakdown(world["db"], dimension="bucket", agent_ids=world["mine"])]
    assert buckets == ["BUCKET_2", "NPA"]              # severity, not alphabetical
    products = pb.breakdown(world["db"], dimension="product", agent_ids=world["mine"])
    assert [r.key for r in products] == ["PERSONAL", "GOLD"]   # 3,000 collected before 1,000


@pytest.mark.parametrize("dimension,expected", [
    ("product", {"PERSONAL", "GOLD"}),
    ("branch", {"BR1", "BR01"}),
    ("city", {"Delhi", "Pune"}),
])
def test_every_dimension_groups_by_its_own_column(world, dimension, expected):
    rows = pb.breakdown(world["db"], dimension=dimension, agent_ids=world["mine"])
    assert set(_by_key(rows)) == expected
    assert sum(r.case_count for r in rows) == 2


def test_another_managers_cases_are_never_in_the_rows(world):
    """The breakdown is an aggregate, which is exactly where a missing scope
    hides: a wrong total looks like a number, not like someone else's data."""
    for dimension, theirs in (("branch", "GG01"), ("city", "Kochi"), ("product", "AUTO")):
        rows = pb.breakdown(world["db"], dimension=dimension, agent_ids=world["mine"])
        assert theirs not in _by_key(rows), dimension
        assert sum(r.case_count for r in rows) == 2, dimension


def test_a_bank_sees_every_agency_s_cases(world):
    """The same service, the other scope: a bank's book is all of it."""
    bank_id = world["loans"]["personal"].bank_id
    rows = pb.breakdown(world["db"], dimension="branch", bank_id=bank_id)
    assert set(_by_key(rows)) >= {"BR1", "BR01", "GG01"}


def test_exactly_one_scope_is_required(world):
    """Neither argument would aggregate every tenant into one row."""
    with pytest.raises(ValueError, match="exactly one"):
        pb.breakdown(world["db"], dimension="branch")
    with pytest.raises(ValueError, match="exactly one"):
        pb.breakdown(world["db"], dimension="branch", agent_ids=[], bank_id="x")


def test_an_unknown_dimension_is_refused_rather_than_guessed(world):
    with pytest.raises(ValueError, match="unknown dimension"):
        pb.breakdown(world["db"], dimension="agent", agent_ids=world["mine"])


def test_a_missing_value_is_a_named_row_not_a_dropped_one(world):
    """A loan with no city recorded still holds real money; dropping it would
    make the breakdown disagree with the page's own header."""
    db = world["db"]
    db.query(Customer).filter(Customer.id == world["customers"]["pune"].id).update({"city": ""})
    db.commit()
    rows = pb.breakdown(db, dimension="city", agent_ids=world["mine"])
    assert pb.UNKNOWN in _by_key(rows)
    assert sum(r.case_count for r in rows) == 2


def test_the_month_mode_counts_only_that_month_s_verified_payments(world):
    db, case = world["db"], world["cases"]["personal"]
    def pay(amount, when, status=PaymentStatus.VERIFIED):
        db.add(Payment(case_id=case.id, loan_id=case.loan_id,
                       agent_id=world["mine"][0], amount=amount, mode=PaymentMode.CASH,
                       status=status, payment_date=when, receipt_number=f"R{amount}"))
    pay(2000.0, datetime(2026, 3, 10, tzinfo=timezone.utc))
    pay(500.0, datetime(2026, 4, 2, tzinfo=timezone.utc))          # another month
    pay(900.0, datetime(2026, 3, 20, tzinfo=timezone.utc), PaymentStatus.REJECTED)
    db.commit()
    rows = _by_key(pb.breakdown(db, dimension="branch", agent_ids=world["mine"], month="2026-03"))
    assert set(rows) == {"BR1"}                        # only the branch that was paid
    assert rows["BR1"].collected_lakhs == 0.02         # 2,000: not April's, not the rejected one


def test_a_malformed_month_is_refused(world):
    with pytest.raises(ValueError, match="YYYY-MM"):
        pb.breakdown(world["db"], dimension="branch", agent_ids=world["mine"], month="March")


@pytest.fixture
def client(world):
    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _headers(user) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value, 'test-device')}"}


@pytest.mark.parametrize("dimension,theirs", [("branch", "GG01"), ("city", "Kochi"), ("product", "AUTO")])
def test_the_route_returns_only_this_managers_team(client, world, dimension, theirs):
    """The behavioural half of the tenancy check for these routes.

    test_every_manager_route_that_reads_tenant_data_is_scoped is textual and
    recognises `_team_breakdown` by name; as that test's own comment insists,
    recognising a helper is not proof that the helper scopes. This is the proof.
    """
    r = client.get(f"/api/v1/manager/analytics/breakdown?dimension={dimension}",
                   headers=_headers(world["manager"]))
    assert r.status_code == 200, r.text
    rows = r.json()
    assert theirs not in {row["key"] for row in rows}
    assert sum(row["case_count"] for row in rows) == 2


def test_the_route_refuses_a_dimension_it_does_not_serve(client, world):
    r = client.get("/api/v1/manager/analytics/breakdown?dimension=agent",
                   headers=_headers(world["manager"]))
    assert r.status_code == 422


def test_the_dpd_breakdown_route_still_answers_as_before(client, world):
    """The agency page's existing call, unchanged by the refactor."""
    r = client.get("/api/v1/manager/analytics/dpd-breakdown", headers=_headers(world["manager"]))
    assert r.status_code == 200, r.text
    assert [row["key"] for row in r.json()] == ["BUCKET_2", "NPA"]
