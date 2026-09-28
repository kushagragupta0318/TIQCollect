"""B19: the analytics layer (B13a) and the DB config (B14) on a real Postgres."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, text

from app.core import database


def test_business_date_is_the_banks_calendar_day(pg_engine):
    with pg_engine.connect() as conn:
        d = conn.execute(text(
            "SELECT analytics.business_date(timestamptz '2026-09-28 20:00:00+00', 'Asia/Kolkata')")).scalar()
    assert str(d) == "2026-09-29"                   # 01:30 IST the next day


def test_the_materialized_views_refresh_concurrently(pg_engine):
    from app.workers.tasks.analytics_refresh import MATERIALIZED_VIEWS, refresh_all
    out = refresh_all(pg_engine, wait_seconds=0)
    assert out["refreshed"] == list(MATERIALIZED_VIEWS) and out["failed"] == []
    with pg_engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM analytics.mv_refresh_log WHERE status = 'OK'")).scalar()
    assert n >= len(MATERIALIZED_VIEWS)


def test_the_live_and_dimension_views_query(pg_engine):
    with pg_engine.connect() as conn:
        for v in ("dim_date", "dim_region", "dim_agency", "dim_agent", "dim_product", "dim_bucket",
                  "v_case_360", "v_today_field_activity"):
            conn.execute(text(f"SELECT * FROM analytics.{v} LIMIT 1")).all()
        assert conn.execute(text("SELECT count(*) FROM analytics.dim_bucket")).scalar() == 5


@pytest.fixture()
def b14_engines(pg_url):
    api, ro = create_engine(pg_url), create_engine(pg_url)
    event.listen(api, "begin", lambda c: database._on_begin_postgres(c))
    event.listen(ro, "begin", lambda c: database._on_begin_postgres(c, read_only=True))
    yield api, ro
    api.dispose()
    ro.dispose()


def test_every_transaction_gets_the_timeout_by_set_local(b14_engines, monkeypatch):
    api, _ = b14_engines
    monkeypatch.setattr(database, "_statement_timeout_ms", 250)
    with api.connect() as conn:
        assert conn.execute(text("SHOW statement_timeout")).scalar() == "250ms"
        with pytest.raises(Exception, match="statement timeout|canceling statement"):
            conn.execute(text("SELECT pg_sleep(1)"))


def test_the_analytics_session_cannot_write(b14_engines):
    _, ro = b14_engines
    with ro.connect() as conn:
        assert conn.execute(text("SHOW transaction_read_only")).scalar() == "on"
        with pytest.raises(Exception, match="read-only transaction"):
            conn.execute(text("CREATE TEMP TABLE b19_probe (x int)"))
