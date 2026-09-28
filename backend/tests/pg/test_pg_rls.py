"""A13: the v2_0012 policies, proven AS tiq_app (and tiq_jobs) on Postgres.

One row is seeded in every policied table, owned by bank B1 / agency A1 and
wired to one placed loan, plus a NULL-bank audit row. The matrix then reads
every table under each principal. RLS is enabled, not forced (step 1): these
tests switch role with SET LOCAL ROLE, which is what step 2 makes permanent.
"""
from __future__ import annotations

import importlib.util
import pathlib
import uuid
from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

from app.core import database
from tests.pg.conftest import drop_database, new_database, run_alembic

REV = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions" / "v2_0012_rls.py"
_spec = importlib.util.spec_from_file_location("rev_v2_0012_pg", REV)
RLS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RLS)

B1, B2 = str(uuid.uuid4()), str(uuid.uuid4())
A1, A2 = str(uuid.uuid4()), str(uuid.uuid4())
LOAN, CUST = str(uuid.uuid4()), str(uuid.uuid4())
_LATER = []
for _p in sorted(REV.parent.glob("v2_*.py")):
    if _p.stem > REV.stem:
        _spec2 = importlib.util.spec_from_file_location(f"rev_{_p.stem}_pg", _p)
        _m = importlib.util.module_from_spec(_spec2)
        _spec2.loader.exec_module(_m)
        _LATER.append(_m)
LATER_POLICIES = {t: e for m in _LATER for t, e in getattr(m, "RLS_POLICIES", {}).items()}
LATER_BANK_ONLY = {t for m in _LATER for t in getattr(m, "RLS_BANK_ONLY", ())}
POLICIED = sorted({**RLS._policies(), **LATER_POLICIES})
BANK_ONLY = set(RLS.BANK_ONLY) | LATER_BANK_ONLY


def _value(col: sa.Column):
    t = col.type
    if isinstance(t, sa.Uuid):
        return str(uuid.uuid4())
    if isinstance(t, sa.Enum):
        return t.enums[0]
    if isinstance(t, sa.DateTime):
        return datetime(2031, 1, 15, 12, tzinfo=timezone.utc)
    if isinstance(t, sa.Date):
        return date(2031, 1, 15)
    if isinstance(t, sa.Boolean):
        return False
    if isinstance(t, (sa.Integer, sa.Numeric, sa.Float)):
        return 1
    if isinstance(t, sa.JSON):
        return {}
    n = getattr(t, "length", None) or 20
    return uuid.uuid4().hex[: min(n, 10)]


def _seed_row(table: sa.Table, **given) -> dict:
    out = dict(given)
    for c in table.columns:
        if c.name in out or c.nullable or c.server_default is not None or c.default is not None:
            continue
        out[c.name] = _value(c)
    cols = {c.name for c in table.columns}
    for k, v in (("bank_id", B1), ("agency_id", A1), ("loan_id", LOAN), ("customer_id", CUST)):
        if k in cols and k not in given:
            out[k] = v
    return out


@pytest.fixture(scope="module")
def rls_url():
    from alembic import command
    url = new_database("rls")
    try:
        run_alembic(url, command.upgrade, "head")
        yield url
    finally:
        drop_database(url)


@pytest.fixture(scope="module")
def seeded(rls_url):
    import app.models  # noqa: F401
    tables = database.Base.metadata.tables
    special = {"tenancy.banks": {"id": B1}, "tenancy.agencies": {"id": A1, "bank_id": B1},
               "lending.loans": {"id": LOAN, "customer_id": CUST}, "lending.customers": {"id": CUST},
               "collections.placements": {"loan_id": LOAN, "agency_id": A1}}
    eng = create_engine(rls_url)
    # This database exists for this module only. Its CHECK constraints are not under test and
    # would refuse the generic seed values, so they go; RLS, FKs and grants are untouched.
    with eng.begin() as conn:
        checks = conn.execute(text(
            "SELECT format('ALTER TABLE %I.%I DROP CONSTRAINT %I', n.nspname, c.relname, k.conname) "
            "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE k.contype = 'c' AND NOT k.conislocal = false AND c.relispartition = false")).scalars().all()
        for stmt in checks:
            conn.execute(text(stmt))
    failed = {}
    for name in POLICIED:
        table = tables[name]
        try:
            with eng.begin() as conn:
                conn.execute(text("SET LOCAL session_replication_role = replica"))   # parents not under test
                conn.execute(table.insert().values(**_seed_row(table, **special.get(name, {}))))
        except Exception as exc:  # noqa: BLE001 — reported below, table by table
            failed[name] = str(exc).splitlines()[0]
    with eng.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        audit = tables["audit.audit_logs"]
        conn.execute(audit.insert().values(**_seed_row(audit, bank_id=None, agency_id=None,
                                                       action="LOGIN", success=True)))
    assert failed == {}, failed
    yield eng
    eng.dispose()


def _counts(eng, role: str, ctx: dict | None) -> dict[str, int]:
    with eng.connect() as conn:
        with conn.begin():
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            if ctx is not None:
                database._set_tenant(conn, ctx)
            return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in POLICIED}


def _ctx(bank=None, agency=None, scope=None):
    return {"bank_id": bank, "agency_id": agency, "scope": scope, "user_id": str(uuid.uuid4())}


def test_no_context_sees_nothing(seeded):
    """Fail closed: a transaction with no tenant set reads zero rows everywhere."""
    assert {t: n for t, n in _counts(seeded, "tiq_app", None).items() if n} == {}


def test_a_reset_context_sees_nothing(seeded):
    """After a SET LOCAL transaction ends, a pooled connection reads '' (not NULL): same answer."""
    with seeded.connect() as conn:
        with conn.begin():
            database._set_tenant(conn, _ctx(B1, None, "BANK"))
        with conn.begin():
            conn.execute(text("SET LOCAL ROLE tiq_app"))
            assert conn.execute(text("SELECT current_setting('app.bank_id', true)")).scalar() == ""
            assert conn.execute(text("SELECT count(*) FROM collections.cases")).scalar() == 0


def test_the_bank_sees_its_whole_book(seeded):
    got = _counts(seeded, "tiq_app", _ctx(B1, None, "BANK"))
    assert {t: n for t, n in got.items() if n < 1} == {}


def test_its_agency_sees_its_own_rows_and_placed_loans_but_nothing_bank_only(seeded):
    got = _counts(seeded, "tiq_app", _ctx(B1, A1, "AGENCY"))
    assert {t: n for t, n in got.items() if t not in BANK_ONLY and n < 1} == {}
    assert {t: got[t] for t in BANK_ONLY if got[t]} == {}


def test_another_agency_of_the_same_bank_sees_only_the_bank_row(seeded):
    got = _counts(seeded, "tiq_app", _ctx(B1, A2, "AGENCY"))
    assert {t: n for t, n in got.items() if n} == {"tenancy.banks": 1}


def test_another_bank_sees_nothing(seeded):
    for scope, agency in (("BANK", None), ("AGENCY", A1)):
        got = _counts(seeded, "tiq_app", _ctx(B2, agency, scope))
        assert {t: n for t, n in got.items() if n} == {}, scope


def test_a_missing_scope_never_widens_an_agency_to_its_bank(seeded):
    got = _counts(seeded, "tiq_app", _ctx(B1, A2, None))
    assert {t: n for t, n in got.items() if n} == {"tenancy.banks": 1}


def test_platform_sees_only_the_platform_audit_rows(seeded):
    got = _counts(seeded, "tiq_app", _ctx(None, None, "PLATFORM"))
    assert {t: n for t, n in got.items() if n} == {"audit.audit_logs": 1}


def test_the_jobs_role_bypasses(seeded):
    got = _counts(seeded, "tiq_jobs", None)
    assert {t: n for t, n in got.items() if n < 1} == {}


def test_a_write_into_another_tenant_is_refused(seeded):
    with seeded.connect() as conn:
        with pytest.raises(sa.exc.DBAPIError, match="row-level security"):
            with conn.begin():
                conn.execute(text("SET LOCAL ROLE tiq_app"))
                database._set_tenant(conn, _ctx(B1, A1, "AGENCY"))
                conn.execute(text("UPDATE collections.cases SET agency_id = :a"), {"a": A2})


@pytest.mark.parametrize("sql", [
    "UPDATE audit.audit_logs SET success = true",
    "DELETE FROM audit.audit_logs",
    "SELECT 1 FROM audit.audit_logs_default",           # a partition, read around its parent's policy
    "SELECT 1 FROM analytics.mv_collections_daily",      # a materialized view carries no policy
])
def test_what_the_app_role_may_not_do(seeded, sql):
    with seeded.connect() as conn:
        with pytest.raises(sa.exc.DBAPIError, match="permission denied"):
            with conn.begin():
                conn.execute(text("SET LOCAL ROLE tiq_app"))
                database._set_tenant(conn, _ctx(B1, None, "BANK"))
                conn.execute(text(sql))


def test_context_values_are_bound_never_formatted(seeded):
    hostile = "x', true); DROP TABLE collections.cases; --"
    with seeded.connect() as conn:
        with conn.begin():
            database._set_tenant(conn, _ctx(B1, None, "BANK") | {"user_id": hostile})
            assert conn.execute(text("SELECT current_setting('app.user_id')")).scalar() == hostile
            assert conn.execute(text("SELECT to_regclass('collections.cases')")).scalar() is not None


def test_a_session_carries_its_tenant_into_every_transaction_and_no_further(rls_url):
    eng = create_engine(rls_url)
    try:
        s = database.SessionLocal(bind=eng)
        database.apply_tenant_context(s, bank_id=B1, agency_id=A1, scope="AGENCY", user_id="u-1")
        for _ in range(2):                                     # a commit ends it; the next begin restores it
            assert s.execute(text("SELECT current_setting('app.agency_id', true)")).scalar() == A1
            s.commit()
        s.close()
        other = database.SessionLocal(bind=eng)               # a new request on a pooled connection
        assert other.execute(text("SELECT current_setting('app.agency_id', true)")).scalar() in (None, "")
        other.close()
    finally:
        eng.dispose()
