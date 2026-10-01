"""The bank's borrower page (C08): scoping, and what it refuses to invent.

`analytics.v_case_360` exists only in Postgres, so the case rows come from a
stub analytics session here and the SQL itself is pinned in tests/pg. What is
pinned HERE is every decision that is not SQL: who may read, the uniform 404 for
missing / another tenant's / outside-the-region, a region-limited read saying it
is partial, a loan with no case shown as itself, masked identifiers not reached
past, and nothing summed across cases.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.dependencies import get_tenant_analytics_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.case import Case, CaseStatus
from app.models.tenancy import Agency, Bank, Branch, Region
from app.models.user import User, UserRole
from app.services.bank import customer_360 as svc
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id
from tests._placement import make_loan, put_branches_in

BANK2 = test_id("bank:kumaon-360")
AGENCY2 = test_id("agency:kumaon-360")
MISSING = test_id("nothing:here")
BASE = "/api/v1/bank"

#: What the view would return for a case, reduced to the columns the page reads.
CASE_ROW = {
    "case_id": test_id("case:1"), "case_number": "C-0001", "status": "ASSIGNED",
    "agency_id": TEST_AGENCY_ID, "agency_name": "Aravalli Field Services", "placed_on": date(2026, 9, 1),
    "agent_id": None, "agent_name": None, "loan_id": None, "loan_type": "PERSONAL", "dpd": 47,
    "dpd_bucket": "BUCKET_2", "total_outstanding": 180000.0, "overdue_amount": 24600.0,
    "target_amount": 24600.0, "collected_verified": 5000.0,
    "last_visit_at": datetime(2026, 9, 20, 11, tzinfo=timezone.utc), "last_visit_outcome": "PTP",
    "last_call_at": None, "last_call_outcome": None,
    "last_contact_at": datetime(2026, 9, 20, 11, tzinfo=timezone.utc),
    "active_ptp_date": date(2026, 10, 5), "active_ptp_amount": 10000.0,
    "has_open_dispute": False, "is_escalated": False,
    "latest_probability": 0.71, "latest_band": "C", "latest_model_version": "2.2.0",
    "latest_disposition": "WILL_PAY",
}


class _StubRows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _StubAnalytics:
    """Stands in for the tenant-bound analytics session: the view is Postgres-only."""

    def __init__(self):
        self.rows: list[dict] = []
        self.calls: list[dict] = []

    def execute(self, _statement, params=None):
        self.calls.append(params or {})
        return _StubRows(self.rows)


def _user(db, key, role, *, bank, agency=None, phone):
    u = User(id=test_id(f"u360:{key}"), email=f"{key}@example.test", phone=phone, full_name=key.title(),
             hashed_password=hash_password("Harbour-Lights-2026"), role=role, bank_id=bank, agency_id=agency)
    db.add(u)
    return u


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})
    db = Session()
    db.add(Bank(id=BANK2, code="KFL360", legal_name="Kumaon Finance Ltd", display_name="Kumaon Finance",
                timezone="Asia/Kolkata", brand={}, status="ACTIVE", is_demo=True))
    db.flush()
    db.add_all([
        Agency(id=AGENCY2, bank_id=BANK2, code="AGY-K360", legal_name="Almora Recovery Desk LLP",
               status="ACTIVE", contacts=[], is_demo=True),
        Branch(id=test_id("branch:kfl360"), bank_id=BANK2, branch_code="GGN044", name="Kumaon Gurugram",
               is_active=True),
    ])
    put_branches_in(db, "GGN", bank_id=TEST_BANK_ID)
    db.flush()
    mine = make_loan(db, 1)                       # this bank, in-region
    second = make_loan(db, 2)                     # same customer? no: its own customer
    foreign = make_loan(db, 11, bank_id=BANK2)
    users = {
        "ba": _user(db, "ba", UserRole.BANK_ADMIN, bank=TEST_BANK_ID, phone="9810000002"),
        "an": _user(db, "an", UserRole.BANK_ANALYST, bank=TEST_BANK_ID, phone="9810000003"),
        "am": _user(db, "am", UserRole.AGENCY_MANAGER, bank=TEST_BANK_ID, agency=TEST_AGENCY_ID, phone="9810000006"),
        "ba2": _user(db, "ba2", UserRole.BANK_ADMIN, bank=BANK2, phone="9810000004"),
    }
    db.commit()

    stub = _StubAnalytics()

    def override_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_tenant_analytics_db] = lambda: stub
    try:
        yield {"db": db, "c": TestClient(app), "mine": mine, "second": second, "foreign": foreign,
               "stub": stub, **users}
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_tenant_analytics_db, None)
        db.close()


def _h(user):
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


def _get(w, user, customer_id):
    return w["c"].get(f"{BASE}/customers/{customer_id}/360", headers=_h(user))


def _agent_for(db, loan):
    """call_logs.agent_id is NOT NULL: a call is always somebody's."""
    from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
    aid = test_id(f"agent360:{loan.bank_id}")
    existing = db.get(Agent, aid)
    if existing is not None:
        return existing
    u = _user(db, f"ag360:{loan.bank_id[:6]}", UserRole.FIELD_AGENT, bank=loan.bank_id,
              agency=TEST_AGENCY_ID, phone="9811000001")
    db.flush()
    a = Agent(id=aid, bank_id=loan.bank_id, agency_id=TEST_AGENCY_ID, user_id=u.id,
              employee_code="EMP360", id_card_number="EMP360-ID", gender="F",
              base_latitude=28.46, base_longitude=77.03, territory="Gurugram", languages_spoken=["HINDI"],
              status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1, specialization=AgentSpecialization.BOTH,
              ranking_score=80.0, max_cases_per_day=5)
    db.add(a)
    db.flush()
    return a


def _case_for(db, loan, *, number="C-0001"):
    c = Case(id=test_id(f"case:{loan.id}"), bank_id=loan.bank_id, agency_id=TEST_AGENCY_ID,
             case_number=number, customer_id=loan.customer_id, loan_id=loan.id, status=CaseStatus.ASSIGNED,
             target_amount=24600.0, collected_amount=0.0, allocation_date=date(2026, 9, 1))
    db.add(c)
    db.flush()
    return c


# ── who may read ─────────────────────────────────────────────────────────────

def test_the_page_needs_the_bank_read_capability_and_a_bank_user(w):
    cid = w["mine"].customer_id
    assert _get(w, w["ba"], cid).status_code == 200
    assert _get(w, w["an"], cid).status_code == 200
    assert _get(w, w["am"], cid).status_code == 403          # agency user: not this portal
    assert w["c"].get(f"{BASE}/customers/{cid}/360").status_code == 401


def test_another_banks_customer_and_a_missing_one_answer_identically(w):
    missing = _get(w, w["ba"], MISSING)
    foreign = _get(w, w["ba"], w["foreign"].customer_id)
    assert missing.status_code == foreign.status_code == 404
    assert missing.json() == foreign.json()
    assert _get(w, w["ba2"], w["foreign"].customer_id).status_code == 200


def test_a_malformed_customer_id_is_the_same_404(w):
    assert _get(w, w["ba"], "not-a-uuid").status_code == 404


# ── what it shows, and what it refuses to invent ─────────────────────────────

def test_a_borrower_with_no_case_shows_the_loan_rather_than_an_empty_page(w):
    body = _get(w, w["ba"], w["mine"].customer_id).json()
    assert body["cases"] == []
    assert [l["loan_id"] for l in body["loans_without_cases"]] == [w["mine"].id]
    assert body["loans_without_cases"][0]["loan_account_number"] == w["mine"].loan_account_number


def test_case_rows_come_through_per_case_with_nothing_summed_across_them(w):
    loan = w["mine"]
    w["stub"].rows = [
        {**CASE_ROW, "loan_id": loan.id},
        {**CASE_ROW, "case_id": test_id("case:2"), "case_number": "C-0002", "loan_id": loan.id,
         "target_amount": 50000.0, "collected_verified": 1000.0, "latest_probability": 0.22, "latest_band": "A"},
    ]
    body = _get(w, w["ba"], loan.customer_id).json()
    assert [c["case_number"] for c in body["cases"]] == ["C-0001", "C-0002"]
    assert [c["latest_band"] for c in body["cases"]] == ["C", "A"]
    # No borrower-level total, score or band anywhere in the payload.
    assert set(body) == {"customer", "cases", "loans_without_cases", "region_limited", "loans_truncated"}
    assert not any(k.startswith(("total_", "overall_", "customer_")) for k in body["customer"]
                   if k not in {"customer_id"})


def test_the_header_carries_the_masked_identifiers_and_does_not_unmask_them(w):
    body = _get(w, w["ba"], w["mine"].customer_id).json()
    header = body["customer"]
    assert header["pan_masked"] == "XXXXX4821K" and header["aadhaar_masked"] == "XXXXXXXX3307"
    assert not any("unmask" in k or k in {"pan", "aadhaar"} for k in header)


def test_the_view_is_asked_only_for_loans_the_caller_may_see(w):
    _get(w, w["ba"], w["mine"].customer_id)
    params = w["stub"].calls[-1]
    assert params["cid"] == w["mine"].customer_id and params["loan_ids"] == [w["mine"].id]


# ── region limit ─────────────────────────────────────────────────────────────

def _region_limited_user(w):
    db = w["db"]
    north = test_id(f"region:{TEST_BANK_ID}:NORTH")
    hrx = test_id("region:hrx-360")
    db.add(Region(id=hrx, bank_id=TEST_BANK_ID, parent_id=north, level="STATE", code="HRX", name="HRX",
                  path="NORTH.HRX"))
    db.flush()
    db.add(Branch(id=test_id("branch:hrx360"), bank_id=TEST_BANK_ID, branch_code="HRX09", name="HRX 09",
                  region_id=hrx, is_active=True))
    db.flush()
    outside = make_loan(db, 9, branch_code="HRX09")
    limited = _user(db, "an-hr", UserRole.BANK_ANALYST, bank=TEST_BANK_ID, phone="9810000009")
    limited.scope_region_id = test_id(f"region:{TEST_BANK_ID}:HR")
    db.commit()
    return limited, outside


def test_a_borrower_only_outside_the_region_is_the_same_404(w):
    limited, outside = _region_limited_user(w)
    assert _get(w, limited, outside.customer_id).status_code == 404
    assert _get(w, w["ba"], outside.customer_id).status_code == 200      # unlimited sees them


def test_a_region_limited_read_says_it_is_partial(w):
    limited, _ = _region_limited_user(w)
    limited_body = _get(w, limited, w["mine"].customer_id).json()
    full_body = _get(w, w["ba"], w["mine"].customer_id).json()
    assert limited_body["region_limited"] is True
    assert full_body["region_limited"] is False


# ── timeline ─────────────────────────────────────────────────────────────────

def test_the_timeline_is_one_column_newest_first_with_an_actor_on_every_entry(w):
    from app.models.call_log import CallLog, CallOutcome
    from app.models.payment import Payment, PaymentMode, PaymentStatus
    db, loan = w["db"], w["mine"]
    case = _case_for(db, loan)
    db.add(CallLog(id=test_id("call:1"), bank_id=loan.bank_id, agency_id=TEST_AGENCY_ID, case_id=case.id,
                   agent_id=_agent_for(db, loan).id, customer_id=loan.customer_id,
                   called_at=datetime(2026, 9, 18, 9, tzinfo=timezone.utc),
                   outcome=CallOutcome.NO_ANSWER))
    db.add(Payment(id=test_id("pay:1"), bank_id=loan.bank_id, agency_id=TEST_AGENCY_ID, case_id=case.id,
                   loan_id=loan.id, amount=5000.0, mode=PaymentMode.CASH, status=PaymentStatus.VERIFIED,
                   receipt_number="R-1", payment_date=datetime(2026, 9, 21, 12, tzinfo=timezone.utc)))
    db.commit()
    body = w["c"].get(f"{BASE}/cases/{case.id}/timeline", headers=_h(w["ba"])).json()
    kinds = [e["kind"] for e in body["entries"]]
    assert kinds == ["PAYMENT", "CALL"]                       # newest first, one column
    assert all(e["actor_type"] and e["at"] for e in body["entries"])
    assert body["truncated"] is False and body["limit"] == svc.MAX_TIMELINE


def test_the_timeline_never_links_evidence_it_only_says_there_is_some(w):
    """A link is a view, and a view is audited: the timeline hands out no URL."""
    db, loan = w["db"], w["mine"]
    case = _case_for(db, loan, number="C-0009")
    db.commit()
    body = w["c"].get(f"{BASE}/cases/{case.id}/timeline", headers=_h(w["ba"])).json()
    assert "url" not in str(body) and "_key" not in str(body)


def test_another_banks_case_timeline_and_a_missing_one_answer_identically(w):
    db = w["db"]
    foreign_case = Case(id=test_id("case:foreign"), bank_id=BANK2, agency_id=AGENCY2,
                        case_number="C-F", customer_id=w["foreign"].customer_id, loan_id=w["foreign"].id,
                        status=CaseStatus.ASSIGNED, target_amount=1.0, collected_amount=0.0,
                        allocation_date=date(2026, 9, 1))
    db.add(foreign_case)
    db.commit()
    missing = w["c"].get(f"{BASE}/cases/{MISSING}/timeline", headers=_h(w["ba"]))
    foreign = w["c"].get(f"{BASE}/cases/{foreign_case.id}/timeline", headers=_h(w["ba"]))
    assert missing.status_code == foreign.status_code == 404
    assert missing.json() == foreign.json()


def test_a_region_limited_user_cannot_read_a_timeline_outside_the_region(w):
    limited, outside = _region_limited_user(w)
    case = _case_for(w["db"], outside, number="C-OUT")
    w["db"].commit()
    assert w["c"].get(f"{BASE}/cases/{case.id}/timeline", headers=_h(limited)).status_code == 404
    assert w["c"].get(f"{BASE}/cases/{case.id}/timeline", headers=_h(w["ba"])).status_code == 200


def test_the_timeline_is_bounded(w, monkeypatch):
    from app.models.call_log import CallLog, CallOutcome
    monkeypatch.setattr(svc, "MAX_TIMELINE", 2)
    db, loan = w["db"], w["mine"]
    case = _case_for(db, loan, number="C-0010")
    for i in range(4):
        db.add(CallLog(id=test_id(f"call:b{i}"), bank_id=loan.bank_id, agency_id=TEST_AGENCY_ID,
                       case_id=case.id, agent_id=_agent_for(db, loan).id, customer_id=loan.customer_id,
                       called_at=datetime(2026, 9, 10 + i, 9, tzinfo=timezone.utc), outcome=CallOutcome.BUSY))
    db.commit()
    body = w["c"].get(f"{BASE}/cases/{case.id}/timeline", headers=_h(w["ba"])).json()
    assert len(body["entries"]) == 2 and body["truncated"] is True


# ── the entry point from Placements (which lists loans, not customers) ───────

def test_the_loan_entry_resolves_to_its_borrower_and_refuses_the_same_way(w):
    c, loan = w["c"], w["mine"]
    ok = c.get(f"{BASE}/loans/{loan.id}/customer-360", headers=_h(w["ba"]))
    assert ok.status_code == 200
    assert ok.json()["customer"]["customer_id"] == loan.customer_id
    missing = c.get(f"{BASE}/loans/{MISSING}/customer-360", headers=_h(w["ba"]))
    foreign = c.get(f"{BASE}/loans/{w['foreign'].id}/customer-360", headers=_h(w["ba"]))
    assert missing.status_code == foreign.status_code == 404
    assert missing.json() == foreign.json()


def test_the_loan_entry_respects_the_region_limit(w):
    limited, outside = _region_limited_user(w)
    c = w["c"]
    assert c.get(f"{BASE}/loans/{outside.id}/customer-360", headers=_h(limited)).status_code == 404
    assert c.get(f"{BASE}/loans/{outside.id}/customer-360", headers=_h(w["ba"])).status_code == 200
