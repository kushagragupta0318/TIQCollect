"""/api/v1/bank/placements (P3 D08) through the real app: capability gates,
bank scoping from the user row, uniform 404 for another bank's ids, and the
agency view of placements received.

Two banks, each with an agency, a contract covering its branches and loans.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.audit_log import AuditAction, AuditLog
from app.models.placement import Placement
from app.models.tenancy import Agency, AgencyContract, Bank, Branch
from app.models.user import User, UserRole
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id
from tests._placement import cover, make_loan

BANK2 = test_id("bank:girivan")
AGENCY2 = test_id("agency:almora-recovery")
AGENCY1B = test_id("agency:sahyadri")                 # a second agency of bank 1
MISSING = test_id("nothing:here")
BASE = "/api/v1/bank/placements"


def _user(db, key, role, *, bank, agency=None, phone):
    u = User(id=test_id(f"u:{key}"), email=f"{key}@example.test", phone=phone, full_name=key.title(),
             hashed_password=hash_password("Harbour-Lights-2026"), role=role, bank_id=bank, agency_id=agency)
    db.add(u)
    return u


def _contract(db, agency_id, bank_id, cap=None):
    c = AgencyContract(bank_id=bank_id, agency_id=agency_id, contract_no=f"C/{agency_id[:8]}", status="ACTIVE",
                       start_date=date(2000, 1, 1), end_date=date(2099, 12, 31), max_placed_cases=cap,
                       sla_first_visit_days=5)
    db.add(c)
    db.flush()
    cover(db, c)
    return c


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})
    db = Session()
    db.add(Bank(id=BANK2, code="GFL", legal_name="Girivan Finance Ltd.", display_name="Girivan Finance",
                timezone="Asia/Kolkata", brand={}, status="ACTIVE", is_demo=True))
    db.flush()
    db.add_all([
        Agency(id=AGENCY2, bank_id=BANK2, code="AGY-ALM", legal_name="Almora Recovery Desk LLP", status="ACTIVE",
               contacts=[], is_demo=True),
        Agency(id=AGENCY1B, bank_id=TEST_BANK_ID, code="AGY-SAH", legal_name="Sahyadri Field Recovery Pvt. Ltd.",
               status="ACTIVE", contacts=[], is_demo=True),
        Branch(id=test_id("branch:gfl"), bank_id=BANK2, branch_code="GGN044", name="Girivan Gurugram",
               is_active=True),
    ])
    db.flush()
    _contract(db, TEST_AGENCY_ID, TEST_BANK_ID, cap=2)
    _contract(db, AGENCY1B, TEST_BANK_ID)
    _contract(db, AGENCY2, BANK2)
    loans_a = [make_loan(db, n) for n in (1, 2, 3)]
    loans_b = [make_loan(db, n, bank_id=BANK2) for n in (11, 12)]
    users = {
        "ba": _user(db, "ba", UserRole.BANK_ADMIN, bank=TEST_BANK_ID, phone="9800000002"),
        "an": _user(db, "an", UserRole.BANK_ANALYST, bank=TEST_BANK_ID, phone="9800000003"),
        "ba2": _user(db, "ba2", UserRole.BANK_ADMIN, bank=BANK2, phone="9800000004"),
        "am": _user(db, "am", UserRole.AGENCY_MANAGER, bank=TEST_BANK_ID, agency=TEST_AGENCY_ID, phone="9800000006"),
        "am1b": _user(db, "am1b", UserRole.AGENCY_MANAGER, bank=TEST_BANK_ID, agency=AGENCY1B, phone="9800000007"),
    }
    db.commit()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        yield {"db": db, "c": TestClient(app), "a": loans_a, "b": loans_b, **users}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _h(user):
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


def _place(w, user, loans, agency=TEST_AGENCY_ID):
    return w["c"].post(BASE, headers=_h(user), json={"agency_id": agency, "loan_ids": [l.id for l in loans]})


# ── the happy path ───────────────────────────────────────────────────────────

def test_bank_admin_picks_previews_places_lists_and_recalls(w):
    c, ba = w["c"], w["ba"]
    loans = c.get(f"{BASE}/loans", headers=_h(ba)).json()
    assert loans["total"] == 3 and {r["loan_id"] for r in loans["items"]} == {l.id for l in w["a"]}
    assert all(r["region_path"] == "NORTH.HR.GGN" and r["placement_id"] is None for r in loans["items"])

    agencies = {a["agency_id"]: a for a in c.get(f"{BASE}/agencies", headers=_h(ba)).json()["items"]}
    assert set(agencies) == {TEST_AGENCY_ID, AGENCY1B}                      # never bank 2's
    assert (agencies[TEST_AGENCY_ID]["headroom"], agencies[TEST_AGENCY_ID]["coverage"][0]["path"]) == (2, "NORTH")

    body = {"agency_id": TEST_AGENCY_ID, "loan_ids": [l.id for l in w["a"]]}
    pre = c.post(f"{BASE}/preview", headers=_h(ba), json=body).json()
    assert pre["counts"] == {"PLACED": 2, "KEPT": 0, "BLOCKED": 1} and pre["run_id"] is None

    out = c.post(BASE, headers=_h(ba), json=body)
    assert out.status_code == 200, out.text
    assert out.json()["counts"] == {"PLACED": 2, "KEPT": 0, "BLOCKED": 1}
    placed = [v for v in out.json()["verdicts"] if v["outcome"] == "PLACED"]

    unplaced = c.get(f"{BASE}/loans", headers=_h(ba)).json()
    assert unplaced["total"] == 1
    both = c.get(f"{BASE}/loans", params={"placed": "any"}, headers=_h(ba)).json()
    assert sum(1 for r in both["items"] if r["placed_with_agency_id"] == TEST_AGENCY_ID) == 2

    listed = c.get(BASE, headers=_h(ba)).json()
    assert listed["total"] == 2 and {r["source"] for r in listed["items"]} == {"MANUAL"}

    r = c.post(f"{BASE}/{placed[0]['placement_id']}/recall", headers=_h(ba), json={"reason": "Borrower moved"})
    assert r.status_code == 200 and r.json()["status"] == "RECALLED" and len(r.json()["cases_closed"]) == 1
    assert c.get(BASE, params={"status": "ACTIVE"}, headers=_h(ba)).json()["total"] == 1


def test_loan_filters_narrow_the_pick_list(w):
    c, ba = w["c"], w["ba"]
    region_hr = test_id(f"region:{TEST_BANK_ID}:HR")
    get = lambda **p: c.get(f"{BASE}/loans", params=p, headers=_h(ba)).json()["total"]  # noqa: E731
    assert get(region_id=region_hr) == 3                               # HR covers GGN below it
    assert get(region_id=MISSING) == 0
    assert get(dpd_bucket="BUCKET_2") == 3 and get(dpd_bucket="NPA") == 0   # fixture loans are 47 DPD
    assert get(dpd_min=48) == 0 and get(dpd_max=47) == 3
    assert get(search="LN0000000") == 3 and get(search="LN00000001") == 1
    assert get(search="%") == 0                                         # a LIKE wildcard is literal
    assert get(loan_type="HOME") == 0


def test_a_region_filter_never_matches_a_sibling_that_only_shares_its_prefix(w):
    """NORTH.HR must not pick up NORTH.HRX (segment match, not string prefix)."""
    from app.models.tenancy import Region
    db, c, ba = w["db"], w["c"], w["ba"]
    north = test_id(f"region:{TEST_BANK_ID}:NORTH")
    hrx = test_id("region:hrx")
    db.add(Region(id=hrx, bank_id=TEST_BANK_ID, parent_id=north, level="STATE", code="HRX", name="HRX",
                  path="NORTH.HRX"))
    db.flush()                                   # no ORM relationship orders region before branch
    db.add(Branch(id=test_id("branch:hrx01"), bank_id=TEST_BANK_ID, branch_code="HRX01", name="HRX 01",
                  region_id=hrx, is_active=True))
    db.flush()
    make_loan(db, 9, branch_code="HRX01")
    db.commit()
    get = lambda rid: c.get(f"{BASE}/loans", params={"region_id": rid}, headers=_h(ba)).json()["total"]  # noqa: E731
    assert get(test_id(f"region:{TEST_BANK_ID}:HR")) == 3
    assert get(hrx) == 1
    assert get(north) == 4


# ── capability gates ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("who", ["an", "am"])
def test_place_preview_pick_and_recall_need_the_bank_admin_capabilities(w, who):
    c, u = w["c"], w[who]
    body = {"agency_id": TEST_AGENCY_ID, "loan_ids": [w["a"][0].id]}
    assert c.get(f"{BASE}/loans", headers=_h(u)).status_code == 403
    assert c.get(f"{BASE}/agencies", headers=_h(u)).status_code == 403
    assert c.post(f"{BASE}/preview", headers=_h(u), json=body).status_code == 403
    assert c.post(BASE, headers=_h(u), json=body).status_code == 403
    assert c.post(f"{BASE}/{MISSING}/recall", headers=_h(u), json={"reason": "x"}).status_code == 403
    assert w["db"].query(Placement).count() == 0


def test_unauthenticated_is_refused(w):
    assert w["c"].get(f"{BASE}/loans").status_code in (401, 403)
    assert w["c"].get(BASE).status_code in (401, 403)


# ── cross-tenant: another bank's ids are "not found", exactly ───────────────

def test_another_banks_loans_agency_and_placement_are_not_found(w):
    c, ba, ba2 = w["c"], w["ba"], w["ba2"]
    assert _place(w, ba, w["a"][:1]).status_code == 200
    p_a = w["db"].query(Placement).one().id

    # bank 2 cannot see bank 1's loans, agencies or placements
    assert c.get(f"{BASE}/loans", params={"placed": "any"}, headers=_h(ba2)).json()["total"] == 2
    ids2 = {a["agency_id"] for a in c.get(f"{BASE}/agencies", headers=_h(ba2)).json()["items"]}
    assert ids2 == {AGENCY2}
    assert c.get(BASE, headers=_h(ba2)).json()["total"] == 0
    assert c.get(BASE, params={"agency_id": TEST_AGENCY_ID}, headers=_h(ba2)).json()["total"] == 0

    # every write naming a bank-1 id answers exactly as a nonexistent id does
    def pair(fn):
        foreign, missing = fn(True), fn(False)
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()
    pair(lambda f: c.post(f"{BASE}/preview", headers=_h(ba2),
                          json={"agency_id": AGENCY2, "loan_ids": [w["a"][1].id if f else MISSING]}))
    pair(lambda f: c.post(BASE, headers=_h(ba2),
                          json={"agency_id": TEST_AGENCY_ID if f else MISSING, "loan_ids": [w["b"][0].id]}))
    pair(lambda f: c.post(f"{BASE}/{p_a if f else MISSING}/recall", headers=_h(ba2), json={"reason": "x"}))
    malformed = c.post(f"{BASE}/not-a-uuid/recall", headers=_h(ba2), json={"reason": "x"})
    assert malformed.status_code == 404
    assert c.post(f"{BASE}/{MISSING}/recall", headers=_h(ba2), json={"reason": "x"}).json() == malformed.json()

    db = w["db"]
    db.expire_all()
    assert db.query(Placement).count() == 1 and db.get(Placement, p_a).status == "ACTIVE"


def test_a_malformed_body_id_is_422_not_500(w):
    r = w["c"].post(BASE, headers=_h(w["ba"]), json={"agency_id": "x", "loan_ids": ["y"]})
    assert r.status_code == 422


def test_the_batch_size_is_capped_at_the_boundary(w):
    ids = [test_id(f"l{i}") for i in range(501)]
    r = w["c"].post(f"{BASE}/preview", headers=_h(w["ba"]), json={"agency_id": TEST_AGENCY_ID, "loan_ids": ids})
    assert r.status_code == 422


# ── the agency's view: placements it received, nobody else's ────────────────

def test_an_agency_manager_sees_only_placements_their_agency_received(w):
    ba, c = w["ba"], w["c"]
    _place(w, ba, w["a"][:1])
    _place(w, ba, w["a"][1:2], agency=AGENCY1B)
    mine = c.get(BASE, headers=_h(w["am"])).json()
    assert mine["total"] == 1 and mine["items"][0]["agency_id"] == TEST_AGENCY_ID
    # a filter naming another agency cannot widen it
    assert c.get(BASE, params={"agency_id": AGENCY1B}, headers=_h(w["am"])).json()["total"] == 0
    assert c.get(BASE, headers=_h(w["am1b"])).json()["items"][0]["agency_id"] == AGENCY1B
    assert c.get(BASE, headers=_h(w["an"])).json()["total"] == 2        # analyst: placement.read, bank-wide


def test_recall_writes_an_audit_row_naming_the_actor(w):
    _place(w, w["ba"], w["a"][:1])
    pid = w["db"].query(Placement).one().id
    w["c"].post(f"{BASE}/{pid}/recall", headers=_h(w["ba"]), json={"reason": "SLA breached twice"})
    w["db"].expire_all()
    row = w["db"].query(AuditLog).filter(AuditLog.action == AuditAction.PLACEMENT_RECALLED).one()
    assert (row.user_id, row.details["reason"]) == (w["ba"].id, "SLA breached twice")
    assert w["c"].post(f"{BASE}/{pid}/recall", headers=_h(w["ba"]), json={"reason": "again"}).status_code == 409
    assert w["c"].post(f"{BASE}/{pid}/recall", headers=_h(w["ba"]), json={"reason": ""}).status_code == 422


def test_an_analysts_region_limit_narrows_the_placements_they_see(w):
    """users.scope_region_id (BANK_ANALYST's region limit), matched by path
    segment. Unset = bank-wide; an id outside their bank = nothing."""
    from app.models.tenancy import Region
    db, c, ba = w["db"], w["c"], w["ba"]
    north = test_id(f"region:{TEST_BANK_ID}:NORTH")
    hrx = test_id("region:hrx")
    db.add(Region(id=hrx, bank_id=TEST_BANK_ID, parent_id=north, level="STATE", code="HRX", name="HRX",
                  path="NORTH.HRX"))
    db.flush()
    db.add(Branch(id=test_id("branch:hrx01"), bank_id=TEST_BANK_ID, branch_code="HRX01", name="HRX 01",
                  region_id=hrx, is_active=True))
    db.flush()
    in_hrx = make_loan(db, 9, branch_code="HRX01")
    db.commit()
    assert _place(w, ba, [w["a"][0], in_hrx], agency=AGENCY1B).status_code == 200

    def seen(region_id):
        u = db.get(User, w["an"].id)
        u.scope_region_id = region_id
        db.commit()
        body = c.get(BASE, headers=_h(u)).json()
        return sorted(r["loan_account_number"] for r in body["items"])

    assert seen(None) == ["LN00000001", "LN00000009"]                      # bank-wide
    assert seen(test_id(f"region:{TEST_BANK_ID}:HR")) == ["LN00000001"]     # HR, not its HRX sibling
    assert seen(hrx) == ["LN00000009"]
    assert seen(north) == ["LN00000001", "LN00000009"]

    # users (scope_region_id, bank_id) -> regions is a composite FK, so a foreign
    # region cannot be stored; the helper still fails closed if one ever resolves to nothing.
    from types import SimpleNamespace
    from app.services.placement_read_service import PlacementReadService
    from app.services.scope import REGION_LIMIT_UNRESOLVED, region_limit_path
    foreign = SimpleNamespace(scope_region_id=test_id(f"region:{BANK2}:NORTH"), bank_id=TEST_BANK_ID)
    assert region_limit_path(db, foreign) is REGION_LIMIT_UNRESOLVED
    out = PlacementReadService(db).placements(bank_id=TEST_BANK_ID, agency_id=None,
                                              region_limit=REGION_LIMIT_UNRESOLVED)
    assert out["total"] == 0


def test_the_placement_list_carries_the_models_synthetic_warning(w):
    """expected_recovery_prob comes from a model trained on synthetic
    borrowers; the list says so, from the artifact's own metadata."""
    from app.ml.pipeline.config import RECOVERY_RISK
    from app.models.model_prediction import ModelPrediction
    db = w["db"]
    assert w["c"].get(BASE, headers=_h(w["ba"])).json()["synthetic_warning"] is None      # nothing modelled
    loan = w["a"][0]
    db.add(ModelPrediction(bank_id=TEST_BANK_ID, model_name=RECOVERY_RISK.name, model_version="2.2.0",
                           entity_type="loan", entity_id=loan.id, loan_id=loan.id, as_of_date=date(2026, 1, 1),
                           probability=0.6, is_modelled=True))
    db.commit()
    _place(w, w["ba"], [loan])
    body = w["c"].get(BASE, headers=_h(w["ba"])).json()
    assert body["items"][0]["expected_recovery_prob"] == pytest.approx(0.4)
    assert "synthetic" in body["synthetic_warning"].lower()


def test_a_region_limited_admin_can_neither_see_nor_place_nor_recall_outside_the_region(w):
    """Auditor MED (2026-09-29): the region limit applies to the write routes
    too, with the same 404 as another bank's ids."""
    from app.models.tenancy import Region
    db, c = w["db"], w["c"]
    north = test_id(f"region:{TEST_BANK_ID}:NORTH")
    hrx = test_id("region:hrx")
    db.add(Region(id=hrx, bank_id=TEST_BANK_ID, parent_id=north, level="STATE", code="HRX", name="HRX",
                  path="NORTH.HRX"))
    db.flush()
    db.add(Branch(id=test_id("branch:hrx01"), bank_id=TEST_BANK_ID, branch_code="HRX01", name="HRX 01",
                  region_id=hrx, is_active=True))
    db.flush()
    outside = make_loan(db, 9, branch_code="HRX01")
    db.commit()
    assert _place(w, w["ba"], [outside], agency=AGENCY1B).status_code == 200      # unlimited admin
    pid = db.query(Placement).one().id

    limited = _user(db, "ba-hr", UserRole.BANK_ADMIN, bank=TEST_BANK_ID, phone="9800000009")
    limited.scope_region_id = test_id(f"region:{TEST_BANK_ID}:HR")
    db.commit()
    h = _h(limited)
    seen = c.get(f"{BASE}/loans", params={"placed": "any"}, headers=h).json()
    assert {r["loan_account_number"] for r in seen["items"]} == {"LN00000001", "LN00000002", "LN00000003"}
    missing = c.post(f"{BASE}/preview", headers=h, json={"agency_id": TEST_AGENCY_ID, "loan_ids": [MISSING]})
    for r in (c.post(f"{BASE}/preview", headers=h, json={"agency_id": TEST_AGENCY_ID, "loan_ids": [outside.id]}),
              c.post(BASE, headers=h, json={"agency_id": TEST_AGENCY_ID, "loan_ids": [outside.id]}),
              c.post(f"{BASE}/{pid}/recall", headers=h, json={"reason": "moved"})):
        assert r.status_code == 404 and r.json() == missing.json()
    assert c.post(f"{BASE}/preview", headers=h,
                  json={"agency_id": TEST_AGENCY_ID, "loan_ids": [w["a"][0].id]}).status_code == 200
    db.expire_all()
    assert db.get(Placement, pid).status == "ACTIVE"
