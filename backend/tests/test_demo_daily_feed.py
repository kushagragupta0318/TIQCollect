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
