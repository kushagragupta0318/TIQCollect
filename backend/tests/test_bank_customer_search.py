"""The bank's borrower lookup (the top-bar search behind C08).

A search box is the easiest place in a product to leak the shape of someone
else's book: it answers "does this exist?" for any string, cheaply and
repeatedly. So what is pinned here is mostly what it REFUSES — another bank's
borrower, a borrower outside the caller's region, a masked identifier as a
search term, a wildcard, and a query short enough to enumerate rather than
identify. In every one of those cases the body must be the same empty one a
genuine no-match returns.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.customer import Customer
from app.models.tenancy import Bank, Branch, Region
from app.models.user import User, UserRole
from app.services.bank.customer_search import MAX_RESULTS, MIN_QUERY
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id
from tests._placement import make_loan, put_branches_in

BANK2 = test_id("bank:kumaon-search")
BASE = "/api/v1/bank"


def _user(db, key, role, *, bank, agency=None, phone):
    u = User(id=test_id(f"usearch:{key}"), email=f"{key}@search.test", phone=phone, full_name=key.title(),
             hashed_password=hash_password("Harbour-Lights-2026"), role=role, bank_id=bank, agency_id=agency)
    db.add(u)
    return u


def _name(db, loan, full_name: str, *, ref: str | None = None):
    """make_loan gives every customer the same name; a search test needs them
    told apart."""
    c = db.get(Customer, loan.customer_id)
    c.full_name = full_name
    if ref:
        c.customer_ref = ref
    db.flush()
    return c


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})
    db = Session()
    db.add(Bank(id=BANK2, code="KFLS", legal_name="Kumaon Finance Ltd", display_name="Kumaon Finance",
                timezone="Asia/Kolkata", brand={}, status="ACTIVE", is_demo=True))
    db.flush()
    db.add(Branch(id=test_id("branch:kfls"), bank_id=BANK2, branch_code="GGN044", name="Kumaon Gurugram",
                  is_active=True))
    put_branches_in(db, "GGN", bank_id=TEST_BANK_ID)
    db.flush()

    mine = make_loan(db, 1)
    other = make_loan(db, 2)
    foreign = make_loan(db, 11, bank_id=BANK2)
    _name(db, mine, "Farhan Siddiqui", ref="C-10001")
    _name(db, other, "Meenakshi Iyer", ref="C-10002")
    _name(db, foreign, "Farhan Siddiqui", ref="C-90001")     # same name, another bank

    users = {
        "ba": _user(db, "ba", UserRole.BANK_ADMIN, bank=TEST_BANK_ID, phone="9820000002"),
        "am": _user(db, "am", UserRole.AGENCY_MANAGER, bank=TEST_BANK_ID, agency=TEST_AGENCY_ID,
                     phone="9820000006"),
        "ba2": _user(db, "ba2", UserRole.BANK_ADMIN, bank=BANK2, phone="9820000004"),
    }
    db.commit()

    def override_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    try:
        yield {"db": db, "c": TestClient(app), "mine": mine, "other": other, "foreign": foreign, **users}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _h(user):
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


def _search(w, user, q):
    return w["c"].get(f"{BASE}/customers/search", params={"q": q}, headers=_h(user))


def _names(response):
    return [i["full_name"] for i in response.json()["items"]]


# ── who may search ───────────────────────────────────────────────────────────

def test_the_lookup_needs_the_bank_read_capability_and_a_bank_user(w):
    assert _search(w, w["ba"], "Farhan").status_code == 200
    assert _search(w, w["am"], "Farhan").status_code == 403       # agency user: not this portal
    assert w["c"].get(f"{BASE}/customers/search", params={"q": "Farhan"}).status_code == 401


# ── what it finds ────────────────────────────────────────────────────────────

def test_finds_a_borrower_by_name_anywhere_in_it_whatever_the_case(w):
    assert _names(_search(w, w["ba"], "siddiqui")) == ["Farhan Siddiqui"]
    assert _names(_search(w, w["ba"], "IYER")) == ["Meenakshi Iyer"]


def test_finds_a_borrower_by_customer_reference_or_loan_account_number(w):
    assert _names(_search(w, w["ba"], "C-10002")) == ["Meenakshi Iyer"]
    assert _names(_search(w, w["ba"], w["mine"].loan_account_number)) == ["Farhan Siddiqui"]


def test_a_hit_carries_enough_to_tell_two_people_apart_and_nothing_a_report_wants(w):
    [hit] = _search(w, w["ba"], "Siddiqui").json()["items"]
    assert set(hit) == {"customer_id", "full_name", "city", "loans", "first_account"}
    assert hit["customer_id"] == w["mine"].customer_id
    assert hit["loans"] == 1


# ── what it refuses ──────────────────────────────────────────────────────────

def test_another_banks_borrower_is_indistinguishable_from_no_borrower(w):
    """Both banks hold a "Farhan Siddiqui". Bank 2's own admin finds theirs;
    bank 1's search returns only its own, and a search for a name nobody has
    returns the identical body — so a miss never reveals whose it was."""
    mine = _search(w, w["ba"], "Siddiqui")
    assert [i["customer_id"] for i in mine.json()["items"]] == [w["mine"].customer_id]
    theirs = _search(w, w["ba2"], "Siddiqui")
    assert [i["customer_id"] for i in theirs.json()["items"]] == [w["foreign"].customer_id]
    nobody = _search(w, w["ba"], "Venkataraghavan")
    only_foreign = _search(w, w["ba"], w["foreign"].loan_account_number)
    assert nobody.json() == only_foreign.json() == {
        "items": [], "query_too_short": False, "min_query_length": MIN_QUERY, "truncated": False}


def test_a_short_query_matches_nothing_and_says_which_kind_of_nothing_it_is(w):
    short = _search(w, w["ba"], "Fa").json()
    assert short["items"] == [] and short["query_too_short"] is True
    assert short["min_query_length"] == MIN_QUERY
    # A real no-match is NOT reported as "too short": the distinction is about
    # the query, which the caller already knows, never about the book.
    assert _search(w, w["ba"], "Venkataraghavan").json()["query_too_short"] is False
    assert _search(w, w["ba"], "   ").json()["query_too_short"] is True


def test_a_wildcard_is_a_literal_character_and_cannot_list_the_book(w):
    for probe in ("%", "%%%", "___", "%a%"):
        assert _search(w, w["ba"], probe).json()["items"] == []


def test_a_masked_identifier_is_neither_searchable_nor_returned(w):
    """pan_masked and aadhaar_masked are identifiers with their middle removed.
    Searchable, they would turn a partial into an oracle."""
    customer = w["db"].get(Customer, w["mine"].customer_id)
    assert _search(w, w["ba"], customer.pan_masked).json()["items"] == []
    assert _search(w, w["ba"], customer.aadhaar_masked).json()["items"] == []
    assert _search(w, w["ba"], "XXXXX").json()["items"] == []
    body = _search(w, w["ba"], "Siddiqui").text
    assert "pan" not in body.lower() and "aadhaar" not in body.lower()


def test_a_borrower_only_outside_the_region_is_not_found_at_all(w):
    db = w["db"]
    north = test_id(f"region:{TEST_BANK_ID}:NORTH")
    hrx = test_id("region:hrx-search")
    db.add(Region(id=hrx, bank_id=TEST_BANK_ID, parent_id=north, level="STATE", code="HRX", name="HRX",
                  path="NORTH.HRX"))
    db.flush()
    db.add(Branch(id=test_id("branch:hrxsearch"), bank_id=TEST_BANK_ID, branch_code="HRX09", name="HRX 09",
                  region_id=hrx, is_active=True))
    db.flush()
    outside = make_loan(db, 9, branch_code="HRX09")
    _name(db, outside, "Padmini Raghunathan", ref="C-10009")
    limited = _user(db, "an-hr", UserRole.BANK_ANALYST, bank=TEST_BANK_ID, phone="9820000009")
    limited.scope_region_id = test_id(f"region:{TEST_BANK_ID}:HR")
    db.commit()

    assert _names(_search(w, w["ba"], "Raghunathan")) == ["Padmini Raghunathan"]   # unlimited finds them
    nothing = _search(w, limited, "Raghunathan")
    assert nothing.json() == _search(w, limited, "Venkataraghavan").json()
    assert nothing.json()["items"] == []
    assert _names(_search(w, limited, "Siddiqui")) == ["Farhan Siddiqui"]          # in-region, still found


def test_a_wide_query_is_capped_and_says_so_rather_than_paginating(w):
    db = w["db"]
    for n in range(20, 20 + MAX_RESULTS + 3):
        loan = make_loan(db, n)
        _name(db, loan, f"Shubhangi Deshpande {n}", ref=f"C-2{n:04d}")
    db.commit()

    body = _search(w, w["ba"], "Deshpande").json()
    assert len(body["items"]) == MAX_RESULTS
    assert body["truncated"] is True
    # No total: the number of matches is the size of the book behind the query.
    assert "total" not in body and "page" not in body
    assert _search(w, w["ba"], "Siddiqui").json()["truncated"] is False
