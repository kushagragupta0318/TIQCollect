"""The performing book (app/demo/performing.py, lane L6): never-delinquent
loans written after the agency books. Run on the SQLite harness; the real
dump's counts are checked by tests/pg/test_pg_demo_fixture.py.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import pytest
import sqlalchemy as sa

from app.demo import performing as P
from app.demo import roster as R
from app.demo.books import INSTALMENT_WINDOW, borrower_detail, history_dates
from app.demo.world import T, _region_rows, insert, region_key
from tests._db import create_schema, drop_schema, make_engine

engine = make_engine()
HISTORY_FROM = date(2026, 3, 2)        # Almora's onboarding, Kumaon's first book


def _seed_kumaon(conn, borrowers: int = 6) -> None:
    """Kumaon's bank, its Pune geography and two branches, and a few agency
    borrowers in Pune: what build_world and the agency book leave behind."""
    insert(conn, "banks", [R.KUMAON_BANK])
    insert(conn, "regions", _region_rows("KUMAON", R.KUMAON_BANK["id"], R.KUMAON_REGIONS, []))
    insert(conn, "branches", [
        dict(id=R.new_id("branch", region_key("KUMAON", code)), bank_id=R.KUMAON_BANK["id"],
             region_id=R.new_id("region", region_key("KUMAON", city)), branch_code=code, name=name,
             address="Baner, Pune 411045", latitude=18.56, longitude=73.78, is_active=True)
        for code, (city, name) in R.KUMAON_BRANCHES.items()])
    insert(conn, "customers", [
        dict(id=R.new_id("customer", f"TEST:{i}"), bank_id=R.KUMAON_BANK["id"], customer_ref=f"KUM10{i:07d}",
             full_name="Amol Sawant", date_of_birth=date(1985, 1, 1), gender="MALE", pan_masked="XXXXX1234K",
             aadhaar_masked="XXXXXXXX1234", phone_primary=f"710{i:07d}", address_line1="12, Baner",
             city="Pune", state="Maharashtra", pincode="411045", latitude=18.56, longitude=73.78)
        for i in range(borrowers)])


@pytest.fixture
def book():
    create_schema(engine)
    with engine.begin() as conn:
        _seed_kumaon(conn)
        truth = P.generate_performing(conn, bank_key="KUMAON", n_loans=120, seed=11, history_from=HISTORY_FROM)
    yield truth
    drop_schema(engine)


def _rows(table: str, **where):
    t = T[table]
    q = sa.select(t)
    for k, v in where.items():
        q = q.where(t.c[k] == v)
    with engine.connect() as c:
        return c.execute(q).mappings().all()


def test_every_performing_loan_is_current_live_and_amortised(book):
    loans = [r for r in _rows("loans", bank_id=R.KUMAON_BANK["id"])]
    assert len(loans) == book.loans == 120
    for ln in loans:
        assert ln["dpd"] == 0 and ln["dpd_bucket"] == "CURRENT" and ln["status"] == "ACTIVE"
        assert float(ln["overdue_amount"]) == 0 and float(ln["penal_charges"]) == 0 and not ln["npa_flag"]
        assert float(ln["total_outstanding"]) == float(ln["outstanding_principal"])
        assert 0 < float(ln["outstanding_principal"]) < float(ln["sanctioned_amount"])
        assert ln["last_payment_date"] <= R.ANCHOR_DATE < ln["next_due_date"] <= ln["maturity_date"]
        assert ln["disbursement_date"] < ln["last_payment_date"]
        assert ln["loan_type"] in P.PRODUCTS                      # never CREDIT_CARD: nothing to amortise
    assert len({ln["loan_account_number"] for ln in loans}) == len(loans)


def test_performing_loans_have_no_collections_footprint(book):
    """Bank EMI collection is not a collections payment: no case, placement or payment."""
    for table in ("cases", "placements", "payments", "visits", "call_logs"):
        assert _rows(table) == [], table


def test_history_is_dpd_zero_on_every_date_and_matches_the_books_dates(book):
    loans = {r["id"]: r for r in _rows("loans", bank_id=R.KUMAON_BANK["id"])}
    hist = _rows("loan_dpd_history", bank_id=R.KUMAON_BANK["id"])
    assert len(hist) == book.history_rows
    assert all(h["dpd"] == 0 and h["dpd_bucket"] == "CURRENT" and h["agency_id"] is None for h in hist)
    by_loan: dict[str, list] = {}
    for h in hist:
        by_loan.setdefault(h["loan_id"], []).append(h["as_of_date"])
    dates = history_dates(HISTORY_FROM)
    for lid, got in by_loan.items():
        opened = loans[lid]["disbursement_date"]
        assert sorted(got) == [d for d in dates if d >= opened]     # the agency books' dates, from opening
    at_anchor = {h["loan_id"]: float(h["total_outstanding"]) for h in hist if h["as_of_date"] == R.ANCHOR_DATE}
    assert at_anchor == {k: float(v["total_outstanding"]) for k, v in loans.items()}


def test_history_is_written_loan_major(book):
    """Each loan's rows are contiguous in insert order (pg_dump compresses the
    current month's partition ~4x better that way). SQLite keeps rowid order."""
    with engine.connect() as c:
        order = [r[0] for r in c.execute(sa.text("SELECT loan_id FROM loan_dpd_history ORDER BY rowid"))]
    runs = [k for i, k in enumerate(order) if i == 0 or order[i - 1] != k]
    assert len(runs) == len(set(order))


def test_balances_never_rise_while_a_loan_is_paid_on_time(book):
    hist = sorted(_rows("loan_dpd_history", bank_id=R.KUMAON_BANK["id"]), key=lambda h: (h["loan_id"], h["as_of_date"]))
    prev = {}
    for h in hist:
        if h["loan_id"] in prev:
            assert float(h["total_outstanding"]) <= prev[h["loan_id"]] + 0.01
        prev[h["loan_id"]] = float(h["total_outstanding"])


def test_schedules_are_windowed_and_ids_unique(book):
    inst = _rows("loan_instalments", bank_id=R.KUMAON_BANK["id"])
    assert len(inst) == book.instalments > 0
    assert all(INSTALMENT_WINDOW[0] <= r["due_date"] <= INSTALMENT_WINDOW[1] for r in inst)
    assert all(r["source"] == "GENERATED" for r in inst)
    assert len({(r["loan_id"], r["instalment_no"]) for r in inst}) == len(inst)


def test_borrower_contacts_are_fictional_and_never_collide_with_an_agency_book(book):
    cust = [r for r in _rows("customers", bank_id=R.KUMAON_BANK["id"]) if r["customer_ref"].startswith("KUM51")]
    assert len(cust) == 120
    assert len({c["customer_ref"] for c in cust}) == len({c["phone_primary"] for c in cust}) == 120
    # agency books use their agency number (1-10) in the same position
    assert all(c["phone_primary"][1:3] == "51" for c in cust)
    emails = [c["email"] for c in cust if c["email"]]
    assert emails and all(e.rsplit("@", 1)[1].endswith(".test") for e in emails)
    assert all(c["address_line2"] for c in cust)
    assert all(680 <= c["cibil_score"] <= 890 for c in cust if c["cibil_score"] is not None)   # a prime book


def test_the_same_seed_writes_the_same_book(book):
    first = sorted((r["loan_account_number"], float(r["total_outstanding"])) for r in _rows("loans"))
    drop_schema(engine)
    create_schema(engine)
    with engine.begin() as conn:
        _seed_kumaon(conn)
        P.generate_performing(conn, bank_key="KUMAON", n_loans=120, seed=11, history_from=HISTORY_FROM)
    assert sorted((r["loan_account_number"], float(r["total_outstanding"])) for r in _rows("loans")) == first


def test_a_bank_with_no_borrowers_in_a_roster_city_is_refused():
    create_schema(engine)
    try:
        with engine.begin() as conn:
            _seed_kumaon(conn, borrowers=0)
            with pytest.raises(R.RosterError, match="agency books first"):
                P.generate_performing(conn, bank_key="KUMAON", n_loans=10, seed=1, history_from=HISTORY_FROM)
    finally:
        drop_schema(engine)


# ── the pure parts ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("principal,rate,n", [(250_000, 14.0, 36), (2_800_000, 8.9, 240), (50_000, 22.0, 12)])
def test_amortisation_starts_at_the_principal_and_ends_at_zero(principal, rate, n):
    emi = P._emi(principal, rate, n)
    assert P.balance_after(principal, rate, emi, 0) == pytest.approx(principal)
    assert P.balance_after(principal, rate, emi, n) == pytest.approx(0, abs=0.01)
    series = [P.balance_after(principal, rate, emi, k) for k in range(n + 1)]
    assert series == sorted(series, reverse=True)


def test_allocate_is_exact_and_deterministic():
    w = {"PUNE": 3.0, "MUMBAI": 1.0, "THANE": 0.5}
    out = P._allocate(1001, w)
    assert sum(out.values()) == 1001 and out == P._allocate(1001, w)
    assert out["PUNE"] > out["MUMBAI"] > out["THANE"]
    assert P._allocate(0, w) == {"PUNE": 0, "MUMBAI": 0, "THANE": 0}


def test_borrower_detail_emails_use_a_reserved_domain_and_strip_punctuation():
    import numpy as np
    rng = np.random.default_rng(3)
    got = [borrower_detail(rng, "Mohd. Irfan Qureshi", "6010000001", email_share=1.0, alt_share=1.0) for _ in range(20)]
    assert all(g["email"].split("@")[0].startswith("mohd.qureshi") for g in got)
    assert all(g["email"].endswith(".test") and g["phone_alternate"] == "6010000001" for g in got)
    none = borrower_detail(rng, "Asha Rani", "6010000002", email_share=0.0, alt_share=0.0)
    assert none["email"] is None and none["phone_alternate"] is None and none["address_line2"]


def test_the_performing_mix_is_a_distribution_over_real_loan_types():
    from app.models.loan import LoanType
    assert set(P.PRODUCTS) <= {t.value for t in LoanType}
    assert sum(v[0] for v in P.PRODUCTS.values()) == pytest.approx(1.0)
    assert "CREDIT_CARD" not in P.PRODUCTS
    assert Counter(P.BOOK_NUMBER.values()).most_common(1)[0][1] == 1      # one number per bank
    assert min(P.BOOK_NUMBER.values()) > len(R.AGENCIES)                    # never an agency's number


def test_v1_borrower_emails_move_to_reserved_domains_and_nothing_else_changes():
    """v1's Faker emails can name a real provider; generate_demo_v2 moves them."""
    from scripts.generate_demo_v2 import rehome_borrower_emails
    create_schema(engine)
    try:
        with engine.begin() as conn:
            _seed_kumaon(conn, borrowers=4)
            cu = T["customers"]
            ids = [r[0] for r in conn.execute(sa.select(cu.c.id).order_by(cu.c.customer_ref))]
            for cid, email in zip(ids, ["rajesh.k23@gmail.com", "asha.rani@inboxmail.test", None, "x@yahoo.co.in"]):
                conn.execute(cu.update().where(cu.c.id == cid).values(email=email))
            assert rehome_borrower_emails(conn) == {"moved_to_test_domains": 2}
            assert rehome_borrower_emails(conn) == {"moved_to_test_domains": 0}          # idempotent
            got = dict(conn.execute(sa.select(cu.c.id, cu.c.email)).all())
        assert got[ids[0]].startswith("rajesh.k23@") and got[ids[0]].endswith(".test")
        assert got[ids[1]] == "asha.rani@inboxmail.test" and got[ids[2]] is None
        assert got[ids[3]].startswith("x@") and got[ids[3]].endswith(".test")
    finally:
        drop_schema(engine)
