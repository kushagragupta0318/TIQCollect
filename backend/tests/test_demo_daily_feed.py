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

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.case import Case
from app.models.customer import Customer
from app.models.loan import Loan, dpd_bucket_for
from app.workers.tasks import demo_daily_feed as feed


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def test_seed_day_creates_a_pool_batch_with_consistent_buckets():
    db = _db()
    # Runs the real thing, rescore included: the point is that the whole
    # morning path executes, not that any one step is mocked out of it.
    created = feed._seed_day(db, date(2026, 9, 18))
    assert feed.NEW_CASES_MIN <= created <= feed.NEW_CASES_MAX
    assert db.query(Customer).filter(Customer.customer_ref.like("DAILY20260918%")).count() == created
    cases = db.query(Case).all()
    assert len(cases) == created and all(c.agent_id is None for c in cases), "the batch lands unassigned"
    for loan in db.query(Loan).all():
        assert loan.dpd_bucket == dpd_bucket_for(loan.dpd)


def test_seed_day_is_idempotent_per_calendar_day():
    db = _db()
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
        a = Agent(user_id=u.id, employee_code=code, id_card_number=code + "-ID", agency_id="AG1", base_latitude=la,
                  base_longitude=lo, tier=AgentTier.TIER_2, specialization=AgentSpecialization.BOTH,
                  status=AgentStatus.ON_DUTY, territory=terr, languages_spoken=["HINDI"], ranking_score=50.0)
        db.add(a); out.append((la, lo, terr))
    db.commit()
    return out


def test_new_cases_are_spread_across_every_agents_territory():
    db = _db()
    bases = _roster(db)
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


def test_with_no_agent_bases_the_feed_falls_back_to_the_old_gurugram_box():
    db = _db()
    created = feed._seed_day(db, date(2026, 9, 21))
    custs = db.query(Customer).filter(Customer.customer_ref.like("DAILY20260921%")).all()
    assert len(custs) == created and {c.city for c in custs} == {"Gurugram"}
    assert all(feed._GGN_LAT[0] <= c.latitude <= feed._GGN_LAT[1] for c in custs)
