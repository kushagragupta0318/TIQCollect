"""Phase 8 acceptance tests: REAL PostgreSQL, real threads, real row locks.

WHY THESE ARE SEPARATE FROM tests/. The main suite runs on in-memory SQLite
(tests/_db), which cannot answer the questions this phase exists to ask:
SQLite ignores `with_for_update()`, serialises everything through one
connection, and has no TIMESTAMPTZ. Any concurrency claim made from it would be
false comfort.

So this package connects to a DEDICATED database (`fieldops_phase8`), never the
dev book, and refuses to run anywhere else — see `pg_engine` below. Every test
creates its own tenant rows with a unique suffix and cleans up after itself, so
runs do not interfere with each other.

RUN:
    docker compose exec -T api sh -lc \
      'cd /app && PHASE8_DB=1 python -m pytest tests/phase8 -q -p no:cacheprovider'

Without PHASE8_DB=1 the whole package is skipped, so a normal `pytest` run of
the project never touches Postgres.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# The isolated database. Deliberately a separate name from the dev book, and
# asserted below, so a misconfigured run cannot inject faults into real data.
PHASE8_DB_NAME = "fieldops_phase8"

pytestmark = pytest.mark.skipif(not os.getenv("PHASE8_DB"),
                                reason="Phase 8 Postgres tests: set PHASE8_DB=1")


def _dsn() -> str:
    from app.core.config import settings
    base = settings.DATABASE_URL.rsplit("/", 1)[0]
    return f"{base}/{PHASE8_DB_NAME}"


@pytest.fixture(scope="session")
def pg_engine():
    if not os.getenv("PHASE8_DB"):
        pytest.skip("PHASE8_DB not set")
    dsn = _dsn()
    # THE GUARD. If this ever points at anything but the dedicated database,
    # stop — these tests kill transactions and inject failures.
    assert dsn.rsplit("/", 1)[-1] == PHASE8_DB_NAME, dsn
    engine = create_engine(dsn, pool_size=10, max_overflow=10, future=True)
    with engine.connect() as c:
        got = c.execute(text("select current_database()")).scalar()
        assert got == PHASE8_DB_NAME, f"refusing to run against {got!r}"
    _seed_lookups(engine)
    yield engine
    engine.dispose()


def _seed_lookups(engine) -> None:
    """The lookup tables several FKs point at (legal_statuses,
    bank_action_types, ...). alembic creates them empty; the canonical rows
    live in models/lookups.LOOKUP_SEEDS, which is what tests/_db uses too —
    so this seeds from the same source rather than copying the dev book.
    Idempotent: a table that already has rows is left alone."""
    from sqlalchemy import func, insert, select

    import app.models  # noqa: F401 — registers every model
    from app.models.lookups import LOOKUP_MODELS, LOOKUP_SEEDS

    with engine.begin() as conn:
        for table, rows in LOOKUP_SEEDS.items():
            model = LOOKUP_MODELS[table]
            if conn.execute(select(func.count()).select_from(model.__table__)).scalar():
                continue
            conn.execute(insert(model.__table__), rows)


@pytest.fixture(scope="session")
def Session(pg_engine):
    return sessionmaker(bind=pg_engine, expire_on_commit=False, future=True)


@pytest.fixture()
def db(Session):
    s = Session()
    yield s
    s.rollback()
    s.close()


@pytest.fixture()
def tenant(db):
    """One bank + agency + branch + users, with a unique code per test.

    Built with real ORM objects so every constraint and tenant listener that
    production has applies here too.
    """
    from app.core.security import hash_password
    from app.models.tenancy import Agency, Bank, Branch
    from app.models.user import User, UserRole

    tag = uuid.uuid4().hex[:8]
    bank_id, agency_id = str(uuid.uuid4()), str(uuid.uuid4())
    bank = Bank(id=bank_id, code=f"P8{tag[:6].upper()}", legal_name=f"Phase8 Bank {tag}",
                display_name=f"Phase8 {tag}", timezone="Asia/Kolkata", brand={},
                status="ACTIVE", is_demo=True)
    db.add(bank)
    db.flush()
    agency = Agency(id=agency_id, bank_id=bank_id, code=f"AGY-{tag[:6].upper()}",
                    legal_name=f"Phase8 Agency {tag}", status="ACTIVE", contacts=[], is_demo=True)
    branch = Branch(id=str(uuid.uuid4()), bank_id=bank_id, branch_code=f"B{tag[:5].upper()}",
                    name=f"Phase8 Branch {tag}", is_active=True)
    admin = User(id=str(uuid.uuid4()), email=f"p8admin.{tag}@example.test",
                 phone=f"9{tag[:9].translate(str.maketrans('abcdef', '123456'))}"[:10],
                 full_name="Phase8 Admin", hashed_password=hash_password("Harbour-Lights-2026"),
                 role=UserRole.BANK_ADMIN, bank_id=bank_id, agency_id=None, is_active=True)
    db.add_all([agency, branch, admin])
    db.commit()

    yield {"tag": tag, "bank_id": bank_id, "agency_id": agency_id,
           "bank_code": bank.code, "agency_code": agency.code,
           "branch_code": branch.branch_code, "admin_id": admin.id}

    # Teardown: only this test's own rows, by tenant id. Never a TRUNCATE.
    _cleanup(db, bank_id)


def _cleanup(db, bank_id: str) -> None:
    """Delete this tenant's rows, children first. Scoped by bank_id so nothing
    outside the test's own tenant is touched."""
    db.rollback()
    stmts = [
        "delete from planning.allocation_runs where bank_id = :b",
        "delete from collections.cases where bank_id = :b",
        "delete from collections.placements where bank_id = :b",
        "delete from lending.bank_feed_rows where bank_id = :b",
        "delete from lending.bank_feed_batches where bank_id = :b",
        "delete from ml.repayment_score_snapshots where bank_id = :b",
        "delete from lending.loans where bank_id = :b",
        "delete from lending.customers where bank_id = :b",
        "delete from tenancy.agency_regions where bank_id = :b",
        "delete from tenancy.agency_contract_terms where bank_id = :b",
        "delete from tenancy.agency_contracts where bank_id = :b",
        # branches BEFORE regions: fk_branches_region_id_bank_id_regions.
        "delete from tenancy.branches where bank_id = :b",
        "delete from tenancy.regions where bank_id = :b",
        "delete from tenancy.agencies where bank_id = :b",
        "delete from audit.audit_logs where bank_id = :b",
        "delete from tenancy.users where bank_id = :b",
        "delete from tenancy.banks where id = :b",
    ]
    for sql in stmts:
        try:
            db.execute(text(sql), {"b": bank_id})
            db.commit()
        except Exception:
            db.rollback()


@pytest.fixture()
def make_batch(db, tenant):
    """A staged batch, as the upload API would leave it."""
    from app.models.lending import BankFeedBatch

    def _make(*, status="RECEIVED", on=None, sha=None, storage_key="phase8/feed.csv",
              rows_total=1, loaded_at=None):
        b = BankFeedBatch(
            id=str(uuid.uuid4()), bank_id=tenant["bank_id"], feed_type="DAILY_BOOK",
            business_date=on or date.today(), file_name="phase8.csv",
            file_sha256=(sha or uuid.uuid4().hex * 2)[:64],
            storage_key=storage_key, received_via="UPLOAD", status=status,
            rows_total=rows_total, dq_report={}, received_at=datetime.now(timezone.utc),
            loaded_at=loaded_at,
        )
        db.add(b)
        db.commit()
        return b
    return _make


@pytest.fixture()
def placeable(db, tenant):
    """A loan whose placement would succeed, plus a quarantined feed row that
    points at it — the Phase 6 releasable shape."""
    from datetime import date as _date

    from app.models.customer import Customer
    from app.models.lending import BankFeedBatch, BankFeedRow
    from app.models.loan import Loan, LoanStatus, LoanType
    from app.models.tenancy import AgencyContract, AgencyRegion
    # The suite's own region helpers: regions carry a NOT NULL `path` and a
    # parent chain, so building one by hand drifts from the model.
    from tests._placement import region_tree

    tag = tenant["tag"]
    regions = region_tree(db, bank_id=tenant["bank_id"])
    contract = AgencyContract(id=str(uuid.uuid4()), bank_id=tenant["bank_id"],
                              agency_id=tenant["agency_id"], contract_no=f"C/{tag}",
                              status="ACTIVE", start_date=_date(2026, 4, 1),
                              end_date=_date(2027, 3, 31), max_placed_cases=None,
                              sla_first_visit_days=5)
    db.add(contract)
    db.flush()
    db.add(AgencyRegion(id=str(uuid.uuid4()), bank_id=tenant["bank_id"],
                        agency_id=tenant["agency_id"], contract_id=contract.id,
                        region_id=regions["NORTH"]))
    # This tenant's branch must sit inside the covered subtree.
    db.execute(text("update tenancy.branches set region_id=:r where bank_id=:b"),
               {"r": regions["GGN"], "b": tenant["bank_id"]})

    # Postgres enforces NOT NULL on pan_masked / aadhaar_masked, which the
    # SQLite suite schema does not. make_loan fills every required column —
    # and the values are MASKED synthetic tokens, never real identifiers.
    cust = Customer(id=str(uuid.uuid4()), bank_id=tenant["bank_id"], customer_ref=f"P8C-{tag}",
                    full_name="Phase8 Borrower", date_of_birth=_date(1988, 6, 14), gender="MALE",
                    pan_masked="XXXXX0000X", aadhaar_masked="XXXXXXXX0000",
                    phone_primary="9899000101", address_line1="Test", city="Gurugram",
                    state="Haryana", pincode="122018",
                    latitude=28.412, longitude=77.064)
    db.add(cust)
    db.flush()
    loan = Loan(id=str(uuid.uuid4()), bank_id=tenant["bank_id"],
                loan_account_number=f"P8LN-{tag}", customer_id=cust.id,
                loan_type=LoanType.PERSONAL, branch_code=tenant["branch_code"],
                sanctioned_amount=400000.0, disbursed_amount=400000.0,
                outstanding_principal=260000.0, total_outstanding=281000.0,
                overdue_amount=24600.0, emi_amount=12300.0, dpd=47,
                status=LoanStatus.ACTIVE, disbursement_date=_date(2024, 2, 1),
                maturity_date=_date(2027, 2, 1), interest_rate=14.25)
    db.add(loan)
    db.flush()

    batch = BankFeedBatch(id=str(uuid.uuid4()), bank_id=tenant["bank_id"],
                          feed_type="DAILY_BOOK", business_date=_date.today(),
                          file_name="p8.csv", file_sha256=uuid.uuid4().hex * 2,
                          received_via="UPLOAD", status="PARTIAL", rows_total=1,
                          rows_accepted=0, rows_quarantined=1, dq_report={},
                          received_at=datetime.now(timezone.utc))
    db.add(batch)
    db.flush()
    row = BankFeedRow(id=str(uuid.uuid4()), bank_id=tenant["bank_id"], batch_id=batch.id,
                      row_no=1, loan_account_number=loan.loan_account_number,
                      customer_ref=cust.customer_ref, case_number=f"P8CASE-{tag}",
                      raw={"loan_account_number": loan.loan_account_number,
                           "customer_ref": cust.customer_ref,
                           "agency_code": tenant["agency_code"]},
                      status="QUARANTINED",
                      dq_errors=[{"reason": "NO_CONTRACT_IN_FORCE", "detail": "held"}],
                      loan_id=loan.id, processed_at=datetime.now(timezone.utc))
    db.add(row)
    db.commit()
    return {"batch_id": batch.id, "row_id": row.id, "loan_id": loan.id}


@pytest.fixture()
def ctx_admin(tenant):
    """The authenticated context for this tenant's bank admin, for calling
    services directly. The bank is never taken from a client value."""
    from app.core.permissions import role_capabilities
    from app.core.request_context import RequestContext
    from app.models.user import UserRole
    return RequestContext(user_id=tenant["admin_id"], role=UserRole.BANK_ADMIN,
                          bank_id=tenant["bank_id"], agency_id=None, sid=None,
                          perms=role_capabilities(UserRole.BANK_ADMIN))
