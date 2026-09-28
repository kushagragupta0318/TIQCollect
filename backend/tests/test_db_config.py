"""B14: connection budget, statement timeouts, the read-only analytics path.

The SET LOCALs are Postgres-only (the SQLite suite has neither setting);
their live evidence is in the B14 commit. These pin the wiring."""
from __future__ import annotations

import pytest

from app.core import database
from app.core.config import settings


def test_the_pool_is_the_small_budget_per_process():
    assert settings.DB_POOL_SIZE == 5 and settings.DB_MAX_OVERFLOW == 5
    assert database.engine.pool.size() == settings.DB_POOL_SIZE


def test_the_api_timeout_is_short_and_a_worker_switches_to_the_jobs(monkeypatch):
    monkeypatch.setattr(database, "_statement_timeout_ms", settings.API_STATEMENT_TIMEOUT_MS)
    assert database.statement_timeout_ms() == settings.API_STATEMENT_TIMEOUT_MS < settings.JOB_STATEMENT_TIMEOUT_MS
    database.use_job_statement_timeout()
    assert database.statement_timeout_ms() == settings.JOB_STATEMENT_TIMEOUT_MS


def test_a_celery_worker_process_takes_the_job_timeout_on_start(monkeypatch):
    from celery.signals import worker_process_init
    import app.workers.celery_app  # noqa: F401 — connects the handler
    monkeypatch.setattr(database, "_statement_timeout_ms", settings.API_STATEMENT_TIMEOUT_MS)
    worker_process_init.send(sender=None)
    assert database.statement_timeout_ms() == settings.JOB_STATEMENT_TIMEOUT_MS


class _Conn:
    def __init__(self, dialect):
        self.dialect = type("D", (), {"name": dialect})()
        self.sql = []

    def exec_driver_sql(self, s):
        self.sql.append(s)


@pytest.mark.parametrize("read_only", [False, True])
def test_every_postgres_transaction_starts_with_set_local_only(monkeypatch, read_only):
    """SET LOCAL, never SET: a session-level SET leaks to the next client (or
    vanishes) under PgBouncer transaction pooling."""
    monkeypatch.setattr(database, "_statement_timeout_ms", 15000)
    c = _Conn("postgresql")
    database._on_begin_postgres(c, read_only=read_only)
    assert c.sql[0] == "SET LOCAL statement_timeout = 15000"
    assert ("SET LOCAL transaction_read_only = on" in c.sql) is read_only
    assert all(s.startswith("SET LOCAL ") for s in c.sql)


def test_sqlite_is_left_alone():
    c = _Conn("sqlite")
    database._on_begin_postgres(c, read_only=True)
    assert c.sql == []
