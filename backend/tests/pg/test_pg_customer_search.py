"""The borrower lookup against real Postgres.

SQLite pins the service's decisions (who may call, the uniform emptiness, the
region limit, the cap). What only Postgres can show is the SQL itself: the
ILIKE escape, the grouping, and — the part worth a gate — that the row-level
policies refuse another bank's borrower EVEN IF the explicit `bank_id` filter
were ever dropped. Belt and braces, tested separately, so neither one quietly
becomes the only thing holding.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core import database
from app.services.bank.customer_search import search_customers
from tests.pg.conftest import drop_database, new_database, run_alembic

B1, B2 = str(uuid.uuid4()), str(uuid.uuid4())
CUST1, CUST2, CUST3 = (str(uuid.uuid4()) for _ in range(3))
L1, L2, L3 = (str(uuid.uuid4()) for _ in range(3))


def _filler(column):
    t = column.type
    if isinstance(t, sa.Boolean):
        return False
    if isinstance(t, (sa.Integer, sa.SmallInteger, sa.BigInteger)):
        return 0
    if isinstance(t, (sa.Numeric, sa.Float)):
        return 0
    if isinstance(t, sa.Date):
        return date(2026, 1, 1)
    if isinstance(t, sa.DateTime):
        return datetime(2026, 1, 1, tzinfo=timezone.utc)
    if isinstance(t, (sa.JSON, sa.ARRAY)) or t.__class__.__name__ in {"JsonDoc", "ARRAY"}:
        return []
    if "UUID" in t.__class__.__name__.upper():
        return str(uuid.uuid4())
    return "x"


def _insert(conn, name: str, **values):
    table = database.Base.metadata.tables[name]
    row = dict(values)
    for c in table.columns:
        if c.name not in row and not c.nullable and c.server_default is None and c.default is None:
            row[c.name] = _filler(c)
    conn.execute(table.insert().values(**row))


@pytest.fixture(scope="module")
def book():
    from alembic import command
    import app.models  # noqa: F401
    url = new_database("customersearch")
    run_alembic(url, command.upgrade, "head")
    eng = create_engine(url)
    try:
        with eng.begin() as conn:
            for stmt in conn.execute(text(
                    "SELECT format('ALTER TABLE %I.%I DROP CONSTRAINT %I', n.nspname, c.relname, k.conname) "
                    "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE k.contype = 'c' AND k.conislocal AND NOT c.relispartition")).scalars().all():
                conn.execute(text(stmt))
        with eng.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            for b, code in ((B1, "GFLSRCH"), (B2, "KFLSRCH")):
                _insert(conn, "tenancy.banks", id=b, code=code, legal_name=f"{code} Finance Ltd",
                        display_name=code, timezone="Asia/Kolkata")
            # A name carrying a literal % — the character a LIKE query would
            # otherwise read as "everything".
            for cust, bank, ref, name in (
                    (CUST1, B1, "C-70001", "Farhan Siddiqui"),
                    (CUST2, B2, "C-90001", "Farhan Siddiqui"),      # same name, other bank
                    (CUST3, B1, "C-70002", "Ritu 100% Enterprises")):
                _insert(conn, "lending.customers", id=cust, bank_id=bank, customer_ref=ref,
                        full_name=name, pan_masked="XXXXX4821K", aadhaar_masked="XXXXXXXX3307")
            for loan, bank, cust, n in ((L1, B1, CUST1, 1), (L2, B1, CUST3, 2), (L3, B2, CUST2, 3)):
                _insert(conn, "lending.loans", id=loan, bank_id=bank, customer_id=cust,
                        loan_account_number=f"LN7{n:07d}", loan_type="PERSONAL", branch_code="NONE",
                        npa_since=None)
        yield eng
    finally:
        eng.dispose()
        drop_database(url)


class _Ctx:
    scope = "BANK"

    def __init__(self, bank_id):
        self.bank_id = bank_id
        self.agency_id = None


def _search(eng, bank_id, query, *, role="tiq_app"):
    """The service, run as the app role with the caller's tenant context — the
    only way the policies are the ones a request would meet."""
    Session = sessionmaker(bind=eng)
    db = Session()
    try:
        conn = db.connection()
        conn.execute(text(f"SET LOCAL ROLE {role}"))
        database._set_tenant(conn, {"user_id": "t", "bank_id": bank_id, "agency_id": None, "scope": "BANK"})
        return search_customers(db, _Ctx(bank_id), query)
    finally:
        db.rollback()
        db.close()


def test_a_bank_finds_its_own_borrower_and_never_the_other_banks_one(book):
    mine = _search(book, B1, "Siddiqui")
    assert [i["customer_id"] for i in mine["items"]] == [CUST1]
    theirs = _search(book, B2, "Siddiqui")
    assert [i["customer_id"] for i in theirs["items"]] == [CUST2]
    assert _search(book, B1, "C-90001")["items"] == []          # the other bank's reference
    assert _search(book, B1, "LN70000003")["items"] == []       # and its account number


def test_the_policies_refuse_the_other_bank_even_without_the_explicit_filter(book):
    """The service names bank_id AND the policies bind the session. This runs
    the same shape with the filter removed, so the two defences are known to
    work separately rather than one hiding the other's absence."""
    from app.models.customer import Customer
    from app.models.loan import Loan

    Session = sessionmaker(bind=book)
    db = Session()
    try:
        conn = db.connection()
        conn.execute(text("SET LOCAL ROLE tiq_app"))
        database._set_tenant(conn, {"user_id": "t", "bank_id": B1, "agency_id": None, "scope": "BANK"})
        rows = (db.query(Customer.id)
                .join(Loan, Loan.customer_id == Customer.id)
                .filter(Customer.full_name.ilike("%Siddiqui%", escape="\\"))
                .all())
        assert [r[0] for r in rows] == [CUST1]
    finally:
        db.rollback()
        db.close()


def test_a_wildcard_is_matched_literally(book):
    """"%" must find the borrower whose NAME contains one, and nobody else."""
    hit = _search(book, B1, "100%")
    assert [i["customer_id"] for i in hit["items"]] == [CUST3]
    assert _search(book, B1, "%")["items"] == []
    assert _search(book, B1, "%%%")["items"] == []
    assert _search(book, B1, "___")["items"] == []


def test_a_hit_counts_only_the_loans_the_caller_can_see(book):
    [hit] = _search(book, B1, "Siddiqui")["items"]
    assert hit["loans"] == 1
    assert hit["first_account"] == "LN70000001"
