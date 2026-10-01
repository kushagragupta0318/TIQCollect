"""v2_0021's v_visit_to_pay returns exactly what v2_0017's did, and stops
joining the book to itself.

A performance rewrite of an attribution rule is only worth having if not one
payment lands on a different visit, so the gate here is row-for-row equality
against v2_0017's definition -- built alongside under a second name, from the
literal the migration keeps for its own downgrade -- under both scopes a
reader can have. The second test is the defect itself: the old shape removed
144 million rows in a join filter on a 41k-visit book, and nothing in the
planner's estimates was ever going to stop it.
"""
from __future__ import annotations

import re

import pytest
from sqlalchemy import text

from tests.pg.test_pg_demo_fixture import db, sql_text  # noqa: F401 -- the restored fixture

pytestmark = pytest.mark.filterwarnings("ignore")

COLUMNS = ("visit_id, bank_id, agency_id, agent_id, case_id, visit_date, customer_met, "
           "paid_amount_7d, paid_within_7d, first_paid_date")
WIDE = ("SELECT count(*) AS visits, count(*) FILTER (WHERE paid_within_7d) AS paid, "
        "coalesce(sum(paid_amount_7d), 0) AS amount FROM analytics.{view}")


def _old_view_sql() -> str:
    """v2_0017's definition, under a second name, from the migration itself."""
    import importlib.util

    from tests.pg.conftest import BACKEND
    path = BACKEND / "alembic" / "versions" / "v2_0021_visit_to_pay_lateral.py"
    spec = importlib.util.spec_from_file_location("v2_0021", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.VISIT_TO_PAY_V2_0017.replace("analytics.v_visit_to_pay ", "analytics.v_visit_to_pay_old ")


@pytest.fixture(scope="module")
def both(db):  # noqa: F811
    with db.connect() as c:
        c.execute(text(_old_view_sql()))
        c.commit()
    return db


def _scopes(db) -> list[tuple[str, dict]]:
    """Every scope a reader can hold on this book: the bank, then its agencies."""
    with db.connect() as c:
        bank = c.execute(text("SELECT id FROM tenancy.banks ORDER BY created_at LIMIT 1")).scalar()
        agencies = [r[0] for r in c.execute(text(
            "SELECT DISTINCT agency_id FROM collections.visits "
            "WHERE bank_id = :b AND agency_id IS NOT NULL ORDER BY 1"), {"b": bank})]
    out = [("BANK", {"bank_id": str(bank), "scope": "BANK", "agency_id": ""})]
    out += [(f"AGENCY {a}", {"bank_id": str(bank), "scope": "AGENCY", "agency_id": str(a)})
            for a in agencies]
    return out


def _as(c, ctx: dict) -> None:
    c.execute(text("SELECT set_config('app.bank_id', :bank_id, false),"
                   " set_config('app.scope', :scope, false),"
                   " set_config('app.agency_id', :agency_id, false)"), ctx)


def test_the_rewrite_returns_the_same_rows_as_v2_0017_under_every_scope(both):
    scopes = _scopes(both)
    assert len(scopes) > 2, "the fixture should carry a bank and several agencies"
    seen_rows = 0
    for label, ctx in scopes:
        with both.connect() as c:
            _as(c, ctx)
            new_only = c.execute(text(
                f"SELECT {COLUMNS} FROM analytics.v_visit_to_pay "
                f"EXCEPT SELECT {COLUMNS} FROM analytics.v_visit_to_pay_old")).fetchall()
            old_only = c.execute(text(
                f"SELECT {COLUMNS} FROM analytics.v_visit_to_pay_old "
                f"EXCEPT SELECT {COLUMNS} FROM analytics.v_visit_to_pay")).fetchall()
            n = c.execute(text("SELECT count(*) FROM analytics.v_visit_to_pay")).scalar()
        assert new_only == [] and old_only == [], (
            f"{label}: {len(new_only)} rows only in the rewrite, {len(old_only)} only in v2_0017's view; "
            f"first differences {new_only[:2]} / {old_only[:2]}")
        seen_rows += n
    assert seen_rows > 0, "every scope read empty: the comparison proved nothing"


def test_the_bank_wide_read_no_longer_joins_the_book_to_itself(both):
    """The defect, in the one number that names it. v2_0017's shape removed
    144,341,315 rows in a join filter here; a book ten times this size would
    never finish. One million is far above anything the rewrite does and far
    below a quadratic join over this book."""
    with both.connect() as c:
        _as(c, _scopes(both)[0][1])
        plan = "\n".join(r[0] for r in c.execute(text(
            "EXPLAIN (ANALYZE, BUFFERS) " + WIDE.format(view="v_visit_to_pay"))))
        old = "\n".join(r[0] for r in c.execute(text(
            "EXPLAIN (ANALYZE, BUFFERS) " + WIDE.format(view="v_visit_to_pay_old"))))
    removed = sum(int(n.replace(",", "")) for n in re.findall(r"Rows Removed by Join Filter: ([\d,]+)", plan))
    removed_old = sum(int(n.replace(",", "")) for n in re.findall(r"Rows Removed by Join Filter: ([\d,]+)", old))
    assert removed < 1_000_000, f"the rewrite discards {removed:,} rows in a join filter:\n{plan}"
    assert removed_old > removed, (
        "v2_0017's view no longer shows the quadratic join on this fixture, so this guard has "
        f"stopped measuring anything (old {removed_old:,}, new {removed:,})")
