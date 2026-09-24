"""
The demo daily feed must be able to seed a day end to end.

2026-09-18. From 2026-09-09 to 2026-09-18 the 05:30 feed raised NameError on
its first loan every morning (`dpd_bucket_for` imported in `_core()`, used in
`_seed_day()`), and nothing but a worker log line said so. These tests run
`_seed_day` against an in-memory database, so a name that is imported in one
function and used in another fails here.
"""
from __future__ import annotations

from datetime import date

from app.models.case import Case
from app.models.customer import Customer
from app.models.lending import BankFeedBatch, BankFeedRow
from app.models.loan import Loan, dpd_bucket_for
from app.models.placement import Placement
from app.workers.tasks import demo_daily_feed as feed
from tests._db import (  # noqa: F401
    TEST_AGENCY_ID, TEST_BANK_ID, create_schema, drop_schema, make_engine, make_session_factory, test_id,
)


def _db():
    engine = make_engine()
    create_schema(bind=engine)
    return make_session_factory(autocommit=False, autoflush=False, bind=engine)()


def _contract(db, *, start=date(2026, 4, 1), end=date(2027, 3, 31), status="ACTIVE", cap=None):
    """The default test agency's contract with the default test bank."""
    from app.models.tenancy import AgencyContract
    c = AgencyContract(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, contract_no="MTB/FCA/2026-27/014",
                       start_date=start, end_date=end, status=status, max_placed_cases=cap,
                       sla_first_visit_days=5)
    db.add(c)
    db.commit()
    return c


def _ready(db):
    """A placeable world: agents with bases, and a contract in force."""
    bases = _roster(db)
    _contract(db)
    return bases


def test_seed_day_creates_a_pool_batch_with_consistent_buckets():
    db = _db()
    _ready(db)
    # Runs the real thing, rescore included: the point is that the whole
    # morning path executes, not that any one step is mocked out of it.
    created = feed._seed_day(db, date(2026, 9, 18))
    assert feed.NEW_CASES_MIN <= created <= feed.NEW_CASES_MAX
    assert db.query(Customer).filter(Customer.customer_ref.like("DAILY20260918%")).count() == created
    cases = db.query(Case).all()
    assert len(cases) == created and all(c.agent_id is None for c in cases), "the batch lands unassigned"
    for loan in db.query(Loan).all():
        assert loan.dpd_bucket == dpd_bucket_for(loan.dpd)


def test_every_new_case_stands_on_a_placement_of_its_own_agency():
    """2026-09-24 (v2): a case is an agency's work item on a placement the
    bank made — services/placement_service.py opens it, never the feed."""
    db = _db()
    _ready(db)
    created = feed._seed_day(db, date(2026, 9, 18))
    cases = db.query(Case).all()
    assert len(cases) == created > 0
    placements = {p.id: p for p in db.query(Placement).all()}
    for c in cases:
        p = placements[c.placement_id]
        assert (p.agency_id, p.bank_id, p.loan_id) == (c.agency_id, c.bank_id, c.loan_id)
        assert p.source == "FEED" and p.status == "ACTIVE"
        assert p.sla_first_visit_due == date(2026, 9, 23)          # placed_on + the contract's 5 days
        assert p.dpd_at_placement == db.get(Loan, c.loan_id).dpd


def test_without_a_contract_in_force_every_row_is_quarantined_and_no_case_is_opened():
    """Never an unowned case: the loan (a bank fact) lands, the case does not,
    and the row waits in quarantine with the reason."""
    db = _db()
    _roster(db)
    _contract(db, start=date(2025, 4, 1), end=date(2026, 3, 31))     # expired before the feed day
    created = feed._seed_day(db, date(2026, 9, 18))
    assert created == 0
    assert db.query(Case).count() == 0
    loans = db.query(Loan).count()
    held = db.query(BankFeedRow).all()
    assert loans > 0 and len(held) == loans
    assert {r.status for r in held} == {"QUARANTINED"}
    assert {e["reason"] for r in held for e in r.dq_errors} == {"NO_CONTRACT_IN_FORCE"}
    batch = db.query(BankFeedBatch).one()
    assert (batch.status, batch.rows_quarantined, batch.received_via) == ("PARTIAL", loans, "DEMO")


def test_seed_day_is_idempotent_per_calendar_day():
    db = _db()
    _ready(db)
    first = feed._seed_day(db, date(2026, 9, 18))
    assert first > 0
    assert feed._seed_day(db, date(2026, 9, 18)) == 0
    assert db.query(Case).count() == first

def test_core_hands_seed_day_every_name_it_uses():
    """Structural: every name `_seed_day` unpacks from `_core()` is returned by
    it, in order — the exact shape of the 2026-09-09 failure."""
    import ast, inspect
    src = inspect.getsource(feed)
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    ret = next(n for n in ast.walk(fns["_core"]) if isinstance(n, ast.Return))
    returned = [e.id for e in ret.value.elts]
    unpack = next(n for n in ast.walk(fns["_seed_day"])
                  if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                  and getattr(n.value.func, "id", None) == "_core")
    names = [e.id for e in unpack.targets[0].elts]
    assert len(names) == len(returned)
    assert "dpd_bucket_for" in names and "dpd_bucket_for" in returned


# ── 2026-09-21: new cases follow the roster, not one city ───────────────────

def _haversine_km(lat1, lon1, lat2, lon2):
    import math
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12742 * math.asin(math.sqrt(a))


def _roster(db):
    from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
    from app.models.user import User, UserRole
    bases = [("G1", "Sector 44, Gurugram", 28.455, 77.072), ("N1", "Sector 18, Noida", 28.568, 77.329),
             ("GN", "Sector 12, Greater Noida", 28.474, 77.504), ("D1", "Rohini Sector 11, New Delhi", 28.701, 77.116)]
    out = []
    for code, terr, la, lo in bases:
        u = User(email=f"{code}@t.in", phone="9" + code.ljust(9, "0"), full_name=code, hashed_password="x",
                 role=UserRole.FIELD_AGENT, is_active=True, is_verified=True)
        db.add(u); db.flush()
        a = Agent(user_id=u.id, employee_code=code, id_card_number=code + "-ID", base_latitude=la,
                  base_longitude=lo, tier=AgentTier.TIER_2, specialization=AgentSpecialization.BOTH,
                  status=AgentStatus.ON_DUTY, territory=terr, languages_spoken=["HINDI"], ranking_score=50.0)
        db.add(a); out.append((la, lo, terr))
    db.commit()
    return out


def test_new_cases_are_spread_across_every_agents_territory():
    db = _db()
    bases = _ready(db)
    created = feed._seed_day(db, date(2026, 9, 21))
    custs = db.query(Customer).filter(Customer.customer_ref.like("DAILY20260921%")).all()
    assert len(custs) == created
    # every borrower sits 0.5-7 km from SOME base, and every base gets a share
    per_base = [0] * len(bases)
    for c in custs:
        d = [_haversine_km(c.latitude, c.longitude, la, lo) for la, lo, _ in bases]
        i = min(range(len(d)), key=d.__getitem__)
        assert 0.4 <= d[i] <= 7.2, (c.customer_ref, d[i])
        per_base[i] += 1
    for n in per_base:
        assert n >= created * 0.10, per_base   # uniform over 4 bases -> ~25% each; 10% is a loose floor
    # city labels follow the base's territory, so the Analytics city split stays on the seed's three values
    assert {c.city for c in custs} == {"Gurugram", "Noida", "Delhi"}
    noida = [c for c in custs if c.city == "Noida"]
    assert all(c.state == "Uttar Pradesh" for c in noida) and {c.pincode for c in noida} <= {"201301", "201310"}


def test_with_no_agent_bases_the_feed_creates_nothing():
    """2026-09-24 — this test used to be `..._falls_back_to_the_old_gurugram_box`
    and asserted every borrower landed in a fixed Gurugram box when no agent
    had a base. In v2 a new case belongs to the agency whose territory it
    lands in, and an agency is known here only through its agents: with none,
    the box would have produced cases nobody owns. The feed now logs
    `demo_daily_feed.skip_no_agents` and creates nothing."""
    db = _db()
    _contract(db)
    assert feed._seed_day(db, date(2026, 9, 21)) == 0
    assert db.query(Customer).count() == 0 and db.query(Case).count() == 0