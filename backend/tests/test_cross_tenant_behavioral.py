# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# New file, 2026-09-28 (A12). "Cross-tenant behavioural test: 2 banks x 2
# agencies, every GET as every principal, no foreign row."
#
# What this covers, and how, rather than by hand-listing every one of the
# ~24 GET routes under /agent and /manager: every GET route on the LIVE app
# under those two prefixes is discovered from `app.routes` itself (so a
# route added after this file is written is tested by construction, not
# missed until someone remembers to add a case for it — the same reason
# test_manager_endpoints.py:494 walks the router rather than listing routes
# by name). Every /agent/* GET is AgentOnly and every /manager/* GET is
# ManagerOnly (verified by grep before writing this — both groups are
# homogeneous), so the principal for a route is decided by its path prefix,
# not by inspecting each handler's own dependency.
#
# Two tenants, two BANKS (not just two agencies under one bank, which A03's
# test_agent_case_scope.py already covers for the agent case-detail route) —
# bank_a/agency_a and bank_b/agency_b, one manager, one agent and one case
# with a visit each. For every discovered route:
#   - zero path params: called as tenant B's own principal (a normal,
#     legitimate call) and the raw response body is scanned for tenant A's
#     case id, agent id, customer name, phone and loan account number as
#     substrings. This does not need to know each response's shape, so it
#     also covers routes this file's fixture cannot make return interesting
#     data (ai/health, ml/health, audit-log, fraud-alerts, ...) — for those
#     the check degenerates to "tenant A's ids are not in an empty or
#     unrelated body", which is still a real (if weaker) assertion.
#   - a `case_id` / `agent_id` / `visit_id` path param: called with tenant
#     A's real id, AS TENANT B's principal, and must be EXACTLY 404 (not
#     merely non-200 — a 422 from FastAPI's own validation would otherwise
#     pass as "refused" without the route's own access check ever running).
#     Cross-checked against a genuinely nonexistent id: the two bodies must
#     be identical, so "belongs to someone else" and "does not exist" stay
#     indistinguishable, the same "uniform 404" shape scope.py and A03
#     already established for the agent case-detail route — now checked on
#     every route that takes one of these three ids, not only the one
#     someone thought to write a test for. (coordinator audit, 2026-09-28)
#   - a QUERY param named `*_id` (found the same way — introspecting
#     `route.dependant.query_params` rather than hand-listing them):
#     tenant A's real id, as tenant B's principal. These are typically
#     FILTERS, not lookups, so a bare 200 is not automatically wrong — an
#     agent_id filter that matches nobody in tenant B's own scope can
#     legitimately return an empty, filtered list. The check is therefore
#     EITHER 404 OR no tenant A marker in the body, same as the
#     parameterless case below. `run_id` (allocation/export-decisions) is
#     skipped: no AllocationRun in this fixture, the same shape of gap as
#     `candidate_id` below, not a hole in the method.
#   - any other PATH param (only `candidate_id`, `/manager/ml/candidates/
#     {candidate_id}`) is skipped, named explicitly below, because this
#     fixture has no ModelCandidate row to seed and manufacturing one is a
#     separate fixture, not a gap in the METHOD.
#
# A separate test (test_aggregate_counts_do_not_move_when_another_tenant_
# gains_cases_and_payments) covers what the marker scan structurally cannot:
# a COUNT or SUM leak. Tenant B's own /manager/dashboard is captured, tenant
# A is given three more cases and two more verified payments, and tenant
# B's dashboard is re-fetched and must be byte-for-byte the response it was
# before — a marker scan only catches a foreign ROW appearing; it cannot see
# a foreign row's amount silently added into someone else's total.
#
# What this does NOT cover: POST/PUT/DELETE routes (a separate, larger
# sweep — the GET side is what "no foreign row" is about); BANK_*, SERVICE
# and PLATFORM_ADMIN principals (MED 1 on A01 found service.manager_api.read
# is declared but wired into no manager.py route yet, so every BANK_*/SERVICE
# call is uniformly 403 today with nothing to leak — worth its own test once
# that capability is wired, not before); and /api/field-ops/*, which is
# Command-Centre-key authenticated, not a User principal, and is being
# deleted by lead-structure D4 (0c32082) regardless.
from __future__ import annotations

import re
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import Loan, LoanType
from app.models.tenancy import Agency, Bank, Branch
from app.models.user import User, UserRole
from app.models.visit import Visit, VisitOutcome
from tests._db import create_schema, make_engine, make_session_factory, test_id

# Path or query params this fixture cannot supply a real foreign id for.
# Named here, not silently skipped by the walker.
_SKIPPED_PARAM_ROUTES = {"candidate_id", "run_id"}

_TENANT_ID_FIELDS = ("case_id", "agent_id", "visit_id")


def _bank_tenant(db, tag: str, phone_prefix: str):
    bank_id = test_id(f"bank:{tag}")
    agency_id = test_id(f"agency:{tag}")
    # Bank flushed on its own: SQLAlchemy's flush-ordering sorts by declared
    # ORM relationships, and Bank/Agency here are plain FK columns with none
    # — in one flush it inserted Agency before Bank and SQLite's enforced FK
    # (tests/_db.py) refused it.
    db.add(Bank(id=bank_id, code=tag.upper()[:10], legal_name=f"{tag.title()} Bank Ltd.",
               display_name=f"{tag.title()} Bank", timezone="Asia/Kolkata", brand={},
               status="ACTIVE", is_demo=True))
    db.flush()
    db.add(Agency(id=agency_id, bank_id=bank_id, code=f"AG-{tag.upper()[:8]}",
                  legal_name=f"{tag.title()} Field Recovery Pvt. Ltd.",
                  trade_name=f"{tag.title()} Field Recovery", status="ACTIVE", contacts=[], is_demo=True))
    db.add(Branch(id=test_id(f"branch:{tag}"), bank_id=bank_id, branch_code="BR1",
                  name=f"{tag.title()} HQ", is_active=True))
    db.flush()

    mgr = User(id=test_id(f"u:mgr:{tag}"), email=f"mgr.{tag}@crosstenant.test", phone=f"{phone_prefix}001",
              full_name=f"Manager {tag.title()}", hashed_password="x", role=UserRole.AGENCY_MANAGER,
              bank_id=bank_id, agency_id=agency_id)
    agent_user = User(id=test_id(f"u:agent:{tag}"), email=f"agent.{tag}@crosstenant.test", phone=f"{phone_prefix}002",
                      full_name=f"Agent {tag.title()}", hashed_password="x", role=UserRole.FIELD_AGENT,
                      bank_id=bank_id, agency_id=agency_id)
    db.add_all([mgr, agent_user])
    db.flush()

    agent = Agent(id=test_id(f"agent:{tag}"), user_id=agent_user.id, employee_code=f"{tag.upper()[:3]}001",
                  id_card_number=f"{tag.upper()}-ID-001", manager_user_id=mgr.id,
                  base_latitude=28.4, base_longitude=77.0, territory=f"{tag.title()} Territory",
                  bank_id=bank_id, agency_id=agency_id)
    db.add(agent)
    db.flush()

    cust = Customer(id=test_id(f"cust:{tag}"), bank_id=bank_id, customer_ref=f"{tag.upper()}-C1",
                    full_name=f"Borrower {tag.title()}", date_of_birth=date(1985, 1, 1), gender="M",
                    pan_masked="XXXXX1111X", aadhaar_masked="XXXXXXXX1111", phone_primary=f"{phone_prefix}003",
                    address_line1="1 Test Road", city="Testville", state="Haryana", pincode="122001",
                    latitude=28.4, longitude=77.0)
    loan = Loan(id=test_id(f"loan:{tag}"), bank_id=bank_id, loan_account_number=f"{tag.upper()}-LN1",
               customer_id=cust.id, loan_type=LoanType.PERSONAL, branch_code="BR1",
               sanctioned_amount=100000.0, disbursed_amount=100000.0, outstanding_principal=80000.0,
               total_outstanding=80000.0, emi_amount=5000.0, interest_rate=12.0,
               disbursement_date=date(2024, 1, 1), maturity_date=date(2027, 1, 1))
    db.add_all([cust, loan])
    db.flush()
    case = Case(id=test_id(f"case:{tag}"), case_number=f"{tag.upper()}-CASE1", customer_id=cust.id, loan_id=loan.id,
               agent_id=agent.id, status=CaseStatus.ASSIGNED, target_amount=80000.0, collected_amount=0.0,
               bank_id=bank_id, agency_id=agency_id)
    db.add(case)
    db.flush()
    visit = Visit(id=test_id(f"visit:{tag}"), case_id=case.id, agent_id=agent.id,
                  check_in_latitude=28.4, check_in_longitude=77.0, check_in_time=datetime.now(timezone.utc),
                  distance_from_customer_metres=10.0, geo_verified=True, within_contact_hours=True,
                  customer_met=True, outcome=VisitOutcome.PTP, bank_id=bank_id, agency_id=agency_id)
    db.add(visit)
    db.commit()

    return {
        "bank_id": bank_id, "agency_id": agency_id, "manager": mgr, "agent_user": agent_user,
        "agent": agent, "customer": cust, "loan": loan, "case": case, "visit": visit,
    }


@pytest.fixture()
def tenants():
    engine = make_engine()

    # analytics/ptp-outcomes and agents/performance GROUP BY to_char(date,
    # 'YYYY-MM') — a Postgres builtin SQLite has no equivalent for. Same
    # shim test_ptp_outcomes.py already registers for the same reason;
    # not a workaround this file invented.
    @event.listens_for(engine, "connect")
    def _sqlite_helpers(dbapi_conn, _record):
        dbapi_conn.create_function("to_char", 2, lambda v, f: str(v)[:7] if v else None)

    create_schema(engine, seed_tenant=False)   # both tenants are explicit here
    Session = make_session_factory(engine)
    db = Session()
    a = _bank_tenant(db, "meridian", "98100010")
    b = _bank_tenant(db, "nilgiri", "98100020")

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        yield {"db": db, "a": a, "b": b}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _headers(user: User, device: str = "dev-1") -> dict:
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, device)}


def _get_routes() -> list[tuple[str, list[str], list[str]]]:
    """(path template, path param names, *_id query param names) for every
    /agent or /manager GET route. Query params come from `route.dependant`
    — the same object FastAPI itself resolves a request against — not from
    reading source text, so a query param renamed or removed is picked up
    automatically."""
    out = []
    for r in app.routes:
        methods = getattr(r, "methods", None)
        path = getattr(r, "path", "")
        if not methods or "GET" not in methods:
            continue
        if not (path.startswith("/api/v1/agent") or path.startswith("/api/v1/manager")):
            continue
        path_params = re.findall(r"\{(\w+)\}", path)
        dependant = getattr(r, "dependant", None)
        query_params = [p.name for p in (dependant.query_params if dependant else [])
                        if p.name.endswith("_id") or p.name == "id"]
        out.append((path, path_params, query_params))
    return out


def _fill(path: str, values: dict) -> str:
    for name, val in values.items():
        path = path.replace("{" + name + "}", str(val))
    return path


ROUTES = _get_routes()
assert ROUTES, "route discovery found nothing — app.routes shape changed, fix the walker before trusting this file"


def _tenant_id_value(field: str, a: dict) -> str:
    return {"case_id": a["case"].id, "agent_id": a["agent"].id, "visit_id": a["visit"].id}[field]


def _foreign_markers(a: dict) -> dict:
    return {
        "case_id": a["case"].id, "agent_id": a["agent"].id, "customer_name": a["customer"].full_name,
        "customer_phone": a["customer"].phone_primary, "loan_account_number": a["loan"].loan_account_number,
    }


@pytest.mark.parametrize("path,path_params,query_params", ROUTES,
                         ids=[p for p, _, _ in ROUTES])
def test_no_foreign_row_on_any_agent_or_manager_get(
    tenants, path: str, path_params: list[str], query_params: list[str],
):
    a, b = tenants["a"], tenants["b"]
    role_principal = b["manager"] if path.startswith("/api/v1/manager") else b["agent_user"]
    client = TestClient(app)
    headers = _headers(role_principal)

    unknown_path = [p for p in path_params if p not in _TENANT_ID_FIELDS and p not in _SKIPPED_PARAM_ROUTES]
    if unknown_path:
        pytest.skip(f"path param(s) {unknown_path} not covered by this fixture — see file header")
    skipped_path = [p for p in path_params if p in _SKIPPED_PARAM_ROUTES]
    if skipped_path:
        pytest.skip(f"path param(s) {skipped_path} explicitly deferred — see file header")
    skipped_query = [p for p in query_params if p in _SKIPPED_PARAM_ROUTES]
    if skipped_query and not path_params:
        # A skipped query param on an otherwise parameterless route doesn't
        # block the marker-scan check below — only the id-in-query check.
        query_params = [p for p in query_params if p not in _SKIPPED_PARAM_ROUTES]

    if path_params:
        # Tenant A's real id, requested as tenant B's principal: must be
        # EXACTLY 404, and the same 404 a genuinely nonexistent id gets —
        # not merely non-200, and not a route-specific "found but hidden"
        # body that would let a caller tell the two cases apart.
        values = {p: _tenant_id_value(p, a) for p in path_params}
        foreign = client.get(_fill(path, values), headers=headers)
        missing_values = {p: test_id(f"nonexistent:{p}") for p in path_params}
        missing = client.get(_fill(path, missing_values), headers=headers)
        assert foreign.status_code == 404, (
            f"{path} returned {foreign.status_code} (not 404) for tenant A's "
            f"{path_params} to a tenant B principal — body: {foreign.text[:300]}")
        assert foreign.status_code == missing.status_code and foreign.json() == missing.json(), (
            f"{path} answers a foreign id differently from a nonexistent one — "
            f"foreign: {foreign.text[:300]!r} missing: {missing.text[:300]!r}")
        return

    # No path params. If the route takes a *_id query filter, try tenant A's
    # real id through it as tenant B's principal: a filter is allowed to
    # answer 200 with an empty/filtered result (it names criteria, not a
    # single resource), so the bar is 404 OR no tenant A marker — never a
    # tenant A row surfacing because the filter matched across the tenant
    # wall instead of narrowing within it.
    markers = _foreign_markers(a)
    for qp in query_params:
        # Unrecognised here on purpose: a *_id query param this file doesn't
        # know how to fill must fail loudly (KeyError) rather than guess a
        # value, the same reason an unknown PATH param is a skip, not a
        # silent no-op.
        r = client.get(path, params={qp: _tenant_id_value(qp, a)}, headers=headers)
        if r.status_code == 404:
            continue
        for label, marker in markers.items():
            assert marker not in r.text, (
                f"{path}?{qp}=<tenant A id> (status {r.status_code}) leaked tenant A's "
                f"{label} ({marker!r})")

    # The plain call, no filter: a normal call as tenant B's own principal
    # must never surface tenant A's identifiers anywhere in the body.
    r = client.get(path, headers=headers)
    for label, marker in markers.items():
        assert marker not in r.text, f"{path} (status {r.status_code}) leaked tenant A's {label} ({marker!r})"


def test_aggregate_counts_do_not_move_when_another_tenant_gains_cases_and_payments(tenants):
    """A marker scan can only see a foreign ROW arrive; it is structurally
    blind to a foreign row's amount being folded into someone ELSE's count
    or sum — the query would still be `Case.agent_id.in_(my_agent_ids)` /
    `Payment.agent_id.in_(my_agent_ids)` correctly scoped and still never
    mention tenant A by name, while still, say, joining across the tenant
    wall with no agency predicate and quietly inflating tenant B's number.
    /manager/dashboard is the sharpest place to see it: every count and sum
    on it is a live aggregate over "this manager's agents" (verified by
    reading it — every query filters `agent_id.in_(my_agent_ids)`), so it's
    the one response most likely to move if any query anywhere lost its
    tenant scope. (coordinator audit, 2026-09-28)"""
    from app.models.payment import Payment, PaymentMode, PaymentStatus

    a, b = tenants["a"], tenants["b"]
    db = tenants["db"]
    client = TestClient(app)
    headers = _headers(b["manager"])

    before = client.get("/api/v1/manager/dashboard", headers=headers)
    assert before.status_code == 200, before.text

    for i in range(3):
        db.add(Case(id=test_id(f"case:extra-a-{i}"), case_number=f"MERIDIAN-EXTRA-{i}",
                    customer_id=a["customer"].id, loan_id=a["loan"].id, agent_id=a["agent"].id,
                    status=CaseStatus.ASSIGNED, target_amount=50000.0, collected_amount=0.0,
                    bank_id=a["bank_id"], agency_id=a["agency_id"]))
    for i in range(2):
        db.add(Payment(id=test_id(f"payment:extra-a-{i}"), case_id=a["case"].id, loan_id=a["loan"].id,
                       agent_id=a["agent"].id, amount=25000.0, mode=PaymentMode.UPI,
                       status=PaymentStatus.VERIFIED, receipt_number=f"MERIDIAN-RCPT-{i}",
                       payment_date=datetime.now(timezone.utc),
                       bank_id=a["bank_id"], agency_id=a["agency_id"]))
    db.commit()

    after = client.get("/api/v1/manager/dashboard", headers=headers)
    assert after.status_code == 200, after.text
    assert before.json() == after.json(), (
        "tenant B's /manager/dashboard changed after tenant A gained cases and "
        "verified payments — a count or sum crossed the tenant wall\n"
        f"before: {before.json()}\nafter:  {after.json()}")
