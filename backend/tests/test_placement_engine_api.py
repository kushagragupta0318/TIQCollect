"""/api/v1/bank/placements/runs (P3 D09, ADR 0010) through the real app:
plan / simulate, four-eyes apply on the plan date, gates re-judged at apply,
recalls then re-placements, and bank scoping.

The analytics session is overridden to None (SQLite has no scorecard view),
so every agency scores neutral; agency_effect has its own tests.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.dependencies import get_tenant_analytics_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.audit_log import AuditAction, AuditLog
from app.models.case import Case
from app.models.placement import Placement
from app.models.planning import PlacementDecision, PlacementRun
from app.models.tenancy import Agency, AgencyContract, AgencyRegion, Bank
from app.models.user import User, UserRole
from app.services.scope import access_day
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id
from tests._placement import cover, make_loan

BASE = "/api/v1/bank/placements"
BANK2 = test_id("bank:girivan")
AG_B = test_id("agency:kaveri")


def _user(db, key, role, *, bank=TEST_BANK_ID, agency=None, phone):
    u = User(id=test_id(f"u:{key}"), email=f"{key}@example.test", phone=phone, full_name=key.title(),
             hashed_password=hash_password("Harbour-Lights-2026"), role=role, bank_id=bank, agency_id=agency)
    db.add(u)
    return u


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})
    db = Session()
    db.add(Bank(id=BANK2, code="GFL", legal_name="Girivan Finance Ltd.", display_name="Girivan Finance",
                timezone="Asia/Kolkata", brand={}, status="ACTIVE", is_demo=True))
    db.add(Agency(id=AG_B, bank_id=TEST_BANK_ID, code="AGY-KAV", legal_name="Kaveri Resolve Partners LLP",
                  status="ACTIVE", contacts=[], is_demo=True))
    db.flush()
    for aid in (TEST_AGENCY_ID, AG_B):
        c = AgencyContract(bank_id=TEST_BANK_ID, agency_id=aid, contract_no=f"C/{aid[:6]}", status="ACTIVE",
                           start_date=date(2000, 1, 1), end_date=date(2099, 12, 31), sla_first_visit_days=5)
        db.add(c)
        db.flush()
        cover(db, c)
    loans = [make_loan(db, n) for n in (1, 2, 3)]
    users = {
        "ba": _user(db, "ba", UserRole.BANK_ADMIN, phone="9800000002"),
        "ba2": _user(db, "ba2", UserRole.BANK_ADMIN, phone="9800000003"),
        "an": _user(db, "an", UserRole.BANK_ANALYST, phone="9800000004"),
        "other": _user(db, "other", UserRole.BANK_ADMIN, bank=BANK2, phone="9800000005"),
        "am": _user(db, "am", UserRole.AGENCY_MANAGER, agency=TEST_AGENCY_ID, phone="9800000006"),
    }
    db.commit()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    app.dependency_overrides[get_tenant_analytics_db] = lambda: None
    try:
        yield {"db": db, "c": TestClient(app), "loans": loans, **users}
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_tenant_analytics_db, None)
        db.close()


def _h(u):
    return {"Authorization": "Bearer " + create_access_token(u.id, u.role.value, "dev-1")}


def _plan(w, user, mode="plan", **kw):
    return w["c"].post(f"{BASE}/runs", headers=_h(user), json={"mode": mode, **kw})


def test_one_admin_plans_another_applies_and_the_book_is_placed(w):
    c, db = w["c"], w["db"]
    r = _plan(w, w["ba"])
    assert r.status_code == 200, r.text
    run = r.json()
    assert (run["status"], run["totals"]["placed"], run["exploration_rate"]) == ("PLANNED", 3, 0.0)
    assert "effect_note" in run["summary"] and "limitations" in run["summary"]
    assert db.query(Placement).count() == 0                                 # planning writes no placement

    dec = c.get(f"{BASE}/runs/{run['run_id']}/decisions", headers=_h(w["an"])).json()   # an analyst may read
    assert dec["total"] == 3 and {d["outcome"] for d in dec["items"]} == {"PLACED"}

    assert c.post(f"{BASE}/runs/{run['run_id']}/apply", headers=_h(w["ba"])).status_code == 403   # four eyes
    applied = c.post(f"{BASE}/runs/{run['run_id']}/apply", headers=_h(w["ba2"]))
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "APPLIED" and applied.json()["summary"]["apply"]["placed"] == 3
    db.expire_all()
    ps = db.query(Placement).all()
    assert len(ps) == 3 and {(p.source, p.placement_run_id, p.placed_by) for p in ps} == {
        ("ENGINE", run["run_id"], w["ba2"].id)}
    assert db.query(Case).count() == 3
    assert db.query(AuditLog).filter(AuditLog.action == AuditAction.PLACEMENT_CREATED).count() == 3
    assert c.post(f"{BASE}/runs/{run['run_id']}/apply", headers=_h(w["ba2"])).status_code == 409   # once only


def test_a_simulation_can_never_be_applied(w):
    run = _plan(w, w["ba"], mode="simulate").json()
    assert run["status"] == "SIMULATED"
    assert w["c"].post(f"{BASE}/runs/{run['run_id']}/apply", headers=_h(w["ba2"])).status_code == 409
    assert w["db"].query(Placement).count() == 0


def test_a_stale_run_is_refused(w):
    run = _plan(w, w["ba"]).json()
    db = w["db"]
    r = db.get(PlacementRun, run["run_id"])
    r.plan_date = date(2020, 1, 1)
    db.commit()
    resp = w["c"].post(f"{BASE}/runs/{run['run_id']}/apply", headers=_h(w["ba2"]))
    assert resp.status_code == 409 and "plan again" in resp.json()["detail"]


def test_apply_re_judges_the_gates_and_skips_what_no_longer_passes(w):
    run = _plan(w, w["ba"]).json()
    db = w["db"]
    db.query(AgencyRegion).delete()                                          # territory withdrawn after planning
    db.commit()
    out = w["c"].post(f"{BASE}/runs/{run['run_id']}/apply", headers=_h(w["ba2"])).json()
    assert out["summary"]["apply"]["placed"] == 0 and out["summary"]["apply"]["skipped_total"] == 3
    assert {s["why"].split(":")[0] for s in out["summary"]["apply"]["skipped"]} == {"NOT_COVERED"}
    db.expire_all()
    assert db.query(Placement).count() == 0


def test_an_engine_recall_ends_the_old_placement_and_re_places_with_another_agency(w):
    from app.services.placement_service import PlacementService
    db = w["db"]
    loan = w["loans"][0]
    old = PlacementService(db).place_new_loan(loan, agency_id=TEST_AGENCY_ID, on=date(2020, 1, 1), source="FEED")
    contract = db.get(AgencyContract, old.contract_id)
    contract.recall_on_sla_breach = True                                    # never visited: SLA long breached
    db.commit()
    run = _plan(w, w["ba"]).json()
    assert run["totals"]["recalled"] == 1
    w["c"].post(f"{BASE}/runs/{run['run_id']}/apply", headers=_h(w["ba2"]))
    db.expire_all()
    assert db.get(Placement, old.id).status == "RECALLED" and db.get(Placement, old.id).end_reason == "SLA_BREACH"
    new = db.query(Placement).filter_by(loan_id=loan.id, status="ACTIVE").one()
    assert (new.agency_id, new.source) == (AG_B, "RE_PLACEMENT")
    d = db.query(PlacementDecision).filter_by(run_id=run["run_id"], loan_id=loan.id).one()
    assert (d.outcome, d.previous_agency_id, d.chosen_agency_id) == ("RECALLED", TEST_AGENCY_ID, AG_B)
    assert db.query(AuditLog).filter(AuditLog.action == AuditAction.PLACEMENT_RECALLED).count() == 1


def test_exploration_is_capped_and_recorded(w):
    assert _plan(w, w["ba"], exploration_rate=0.21).status_code == 422
    run = _plan(w, w["ba"], exploration_rate=0.2).json()
    assert run["exploration_rate"] == 0.2


@pytest.mark.parametrize("who,status", [("an", 403), ("am", 403)])
def test_only_a_bank_admin_can_plan_or_apply(w, who, status):
    assert _plan(w, w[who]).status_code == status
    run = _plan(w, w["ba"]).json()
    assert w["c"].post(f"{BASE}/runs/{run['run_id']}/apply", headers=_h(w[who])).status_code == status


def test_an_agency_role_cannot_read_runs(w):
    _plan(w, w["ba"])
    assert w["c"].get(f"{BASE}/runs", headers=_h(w["am"])).status_code == 403


def test_another_bank_sees_no_run_and_cannot_apply_one(w):
    run = _plan(w, w["ba"]).json()
    c, other = w["c"], w["other"]
    assert c.get(f"{BASE}/runs", headers=_h(other)).json()["items"] == []
    missing = test_id("no:run")
    for path in (f"{BASE}/runs/{run['run_id']}/decisions", f"{BASE}/runs/{missing}/decisions"):
        assert c.get(path, headers=_h(other)).status_code == 404
    a = c.post(f"{BASE}/runs/{run['run_id']}/apply", headers=_h(other))
    b = c.post(f"{BASE}/runs/{missing}/apply", headers=_h(other))
    assert a.status_code == b.status_code == 404 and a.json() == b.json()


def test_a_region_limited_admin_may_not_run_the_engine(w):
    db = w["db"]
    u = db.get(User, w["ba"].id)
    u.scope_region_id = test_id(f"region:{TEST_BANK_ID}:HR")
    db.commit()
    assert _plan(w, u).status_code == 403


def test_today_is_the_ist_access_day(w):
    assert _plan(w, w["ba"]).json()["plan_date"] == access_day().isoformat()
