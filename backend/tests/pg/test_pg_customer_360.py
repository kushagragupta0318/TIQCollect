"""C08's borrower page against real Postgres: the tenant isolation SQLite cannot show.

`analytics.v_case_360` is `security_invoker`, so the RLS policies on its base
tables decide what the caller sees — and that only exists in Postgres. The
SQLite suite pins the service's decisions (who may call, the uniform 404, the
region limit, nothing summed across cases); what is pinned HERE is that the view
itself hands a bank only its own borrowers, and that the exact statement the
service issues cannot be widened by the loan ids it is given.

A 360-degree view of one borrower is the most damaging thing to leak across
tenants, so this is the gate C08 merges on.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

from app.core import database
from tests.pg.conftest import drop_database, new_database, run_alembic

B1, B2, A1, A2, A3 = (str(uuid.uuid4()) for _ in range(5))   # A1, A3: bank 1; A2: bank 2
CUST1, CUST2 = str(uuid.uuid4()), str(uuid.uuid4())
L1, L2, L3 = (str(uuid.uuid4()) for _ in range(3))          # L1, L2: bank 1; L3: bank 2
P1, P2, P3 = (str(uuid.uuid4()) for _ in range(3))          # an agency reaches a borrower only through a placement
CASE1, CASE2, CASE3 = (str(uuid.uuid4()) for _ in range(3))

#: Exactly the statement services/bank/customer_360.py issues.
SERVICE_SQL = ("SELECT case_id, case_number, loan_id, agency_id, customer_name "
               "FROM analytics.v_case_360 "
               "WHERE customer_id = CAST(:cid AS uuid) AND loan_id = ANY(CAST(:loan_ids AS uuid[])) "
               "ORDER BY placed_on DESC NULLS LAST, case_number")


def _filler(column):
    """A value of the right type for a NOT NULL column the test does not care about."""
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
    # A uuid column cannot take "x": Postgres rejects it outright (SQLite does not).
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
    url = new_database("customer360")
    run_alembic(url, command.upgrade, "head")
    eng = create_engine(url)
    try:
        with eng.begin() as conn:
            # Drop CHECK constraints the filler values cannot satisfy; the view
            # and its policies, which are what this file tests, are untouched.
            for stmt in conn.execute(text(
                    "SELECT format('ALTER TABLE %I.%I DROP CONSTRAINT %I', n.nspname, c.relname, k.conname) "
                    "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE k.contype = 'c' AND k.conislocal AND NOT c.relispartition")).scalars().all():
                conn.execute(text(stmt))
        with eng.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            # Unique codes and account numbers: the filler gives every text
            # column "x", which collides on uq_banks_code and its kin.
            for b, code in ((B1, "GFL360"), (B2, "KFL360")):
                _insert(conn, "tenancy.banks", id=b, code=code, legal_name=f"{code} Finance Ltd",
                        display_name=code, timezone="Asia/Kolkata")
            _insert(conn, "tenancy.agencies", id=A1, bank_id=B1, code="AGY-ARV360",
                    legal_name="Aravalli Field Services Pvt. Ltd.", trade_name="Aravalli Field Services")
            _insert(conn, "tenancy.agencies", id=A2, bank_id=B2, code="AGY-ALM360",
                    legal_name="Almora Recovery Desk LLP", trade_name="Almora Recovery Desk")
            # A3 is bank 1's too, but holds no placement on this borrower.
            _insert(conn, "tenancy.agencies", id=A3, bank_id=B1, code="AGY-SAR360",
                    legal_name="Sarthak Recovery Services LLP", trade_name="Sarthak Recovery Services")
            _insert(conn, "lending.customers", id=CUST1, bank_id=B1, customer_ref="C-360-1",
                    full_name="Farhan Siddiqui", pan_masked="XXXXX4821K", aadhaar_masked="XXXXXXXX3307")
            _insert(conn, "lending.customers", id=CUST2, bank_id=B2, customer_ref="C-360-2",
                    full_name="Nandita Rao", pan_masked="XXXXX9999Z", aadhaar_masked="XXXXXXXX9999")
            for n, (loan, bank, cust) in enumerate(((L1, B1, CUST1), (L2, B1, CUST1), (L3, B2, CUST2)), start=1):
                _insert(conn, "lending.loans", id=loan, bank_id=bank, customer_id=cust,
                        loan_account_number=f"LN360{n:05d}", loan_type="PERSONAL", branch_code="NONE",
                        npa_since=None)
            # The placements are what an AGENCY-scoped caller reaches a borrower
            # through (the RLS policy on lending.customers requires one), so a
            # fixture without them proves nothing about agency isolation.
            for pl, bank, agency, loan in ((P1, B1, A1, L1), (P2, B1, A1, L2), (P3, B2, A2, L3)):
                _insert(conn, "collections.placements", id=pl, bank_id=bank, agency_id=agency, loan_id=loan,
                        status="ACTIVE", placed_on=date(2026, 9, 1), ended_on=None,
                        dpd_bucket_at_placement="BUCKET_1", source="MANUAL")
            # Two cases for one borrower of bank 1 — the whole point of C08 — and
            # one case for a different borrower at another bank.
            for case, bank, agency, loan, cust, number, pl in (
                    (CASE1, B1, A1, L1, CUST1, "C-0001", P1),
                    (CASE2, B1, A1, L2, CUST1, "C-0002", P2),
                    (CASE3, B2, A2, L3, CUST2, "C-9999", P3)):
                _insert(conn, "collections.cases", id=case, bank_id=bank, agency_id=agency, loan_id=loan,
                        customer_id=cust, case_number=number, placement_id=pl, agent_id=None)
        yield eng
    finally:
        eng.dispose()
        drop_database(url)


def _as(eng, sql, ctx=None, role="tiq_app", params=None):
    with eng.connect() as conn:
        with conn.begin():
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            if ctx:
                database._set_tenant(conn, {"user_id": "t", **ctx})
            return conn.execute(text(sql), params or {}).all()


BANK1 = {"bank_id": B1, "agency_id": None, "scope": "BANK"}
BANK2 = {"bank_id": B2, "agency_id": None, "scope": "BANK"}
AGENCY1 = {"bank_id": B1, "agency_id": A1, "scope": "AGENCY"}


def test_the_case_view_is_tenant_bound(book):
    """No context reads nothing; each bank reads only its own."""
    count = "SELECT count(*) FROM analytics.v_case_360"
    assert _as(book, count, None)[0][0] == 0
    assert _as(book, count, BANK1)[0][0] == 2
    assert _as(book, count, BANK2)[0][0] == 1
    numbers = {r[0] for r in _as(book, "SELECT case_number FROM analytics.v_case_360", BANK1)}
    assert numbers == {"C-0001", "C-0002"}


def test_a_bank_never_sees_another_banks_borrower_through_the_view(book):
    rows = _as(book, "SELECT case_number FROM analytics.v_case_360 WHERE customer_id = :c",
               BANK1, params={"c": CUST2})
    assert rows == []


def test_the_statement_the_service_issues_cannot_be_widened_by_the_loan_ids_it_is_given(book):
    """The service passes loan ids it has already scoped. Even handed another
    bank's loan id, the view's own policies return nothing for it."""
    mine = _as(book, SERVICE_SQL, BANK1, params={"cid": CUST1, "loan_ids": [L1, L2]})
    assert {r[1] for r in mine} == {"C-0001", "C-0002"}
    smuggled = _as(book, SERVICE_SQL, BANK1, params={"cid": CUST2, "loan_ids": [L3]})
    assert smuggled == []
    mixed = _as(book, SERVICE_SQL, BANK1, params={"cid": CUST1, "loan_ids": [L1, L3]})
    assert {r[1] for r in mixed} == {"C-0001"}          # the foreign loan contributes nothing


def test_an_agency_reads_only_the_cases_it_holds_a_placement_for(book):
    """C08 itself is bank-only, but the view is shared: an agency must reach a
    borrower only through its own placement. A3 is the same bank as A1 and holds
    none, so it must read nothing of this borrower."""
    rows = _as(book, SERVICE_SQL, AGENCY1, params={"cid": CUST1, "loan_ids": [L1, L2]})
    assert {r[1] for r in rows} == {"C-0001", "C-0002"}
    same_bank_other_agency = {"bank_id": B1, "agency_id": A3, "scope": "AGENCY"}
    assert _as(book, SERVICE_SQL, same_bank_other_agency, params={"cid": CUST1, "loan_ids": [L1, L2]}) == []
    other_bank_agency = {"bank_id": B1, "agency_id": A2, "scope": "AGENCY"}
    assert _as(book, SERVICE_SQL, other_bank_agency, params={"cid": CUST1, "loan_ids": [L1, L2]}) == []


def test_the_view_carries_the_masked_identifiers_and_no_unmasked_column(book):
    cols = {r[0] for r in _as(
        book, "SELECT column_name FROM information_schema.columns "
              "WHERE table_schema = 'analytics' AND table_name = 'v_case_360'", BANK1)}
    assert "pan_masked" in cols and "aadhaar_masked" in cols
    assert not (cols & {"pan", "aadhaar", "pan_number", "aadhaar_number"})
