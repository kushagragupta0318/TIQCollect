"""The Postgres suite (B19): what SQLite cannot show.

Runs only when TIQ_PG_TEST_URL names a Postgres server the tests may create
databases on (CI: a postgres:16 service; locally: e.g.
postgresql+psycopg2://fieldops:fieldops_dev_pass@postgres:5432/postgres from a
throwaway container). Unset, every test here is skipped, so the ordinary
suite is unchanged. Each session creates its own database and drops it after;
it never touches an existing one.
"""
from __future__ import annotations

import os
import pathlib
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

PG_URL = os.environ.get("TIQ_PG_TEST_URL", "")
BACKEND = pathlib.Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(config, items):
    if PG_URL:
        return
    skip = pytest.mark.skip(reason="TIQ_PG_TEST_URL not set: the Postgres suite (tests/pg) is skipped")
    for item in items:
        if "tests/pg/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)


def _admin():
    return create_engine(PG_URL, isolation_level="AUTOCOMMIT")


def new_database(tag: str) -> str:
    name = f"tiq_pgtest_{tag}_{uuid.uuid4().hex[:8]}"
    with _admin().connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    return make_url(PG_URL).set(database=name).render_as_string(hide_password=False)


def drop_database(url: str) -> None:
    name = make_url(url).database
    with _admin().connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def alembic_cfg():
    from alembic.config import Config
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return cfg


def run_alembic(url: str, fn, *args):
    """alembic/env.py reads settings.DATABASE_URL; point it at `url` for one command."""
    from app.core.config import settings
    old = settings.DATABASE_URL
    settings.DATABASE_URL = url
    try:
        return fn(alembic_cfg(), *args)
    finally:
        settings.DATABASE_URL = old


@pytest.fixture(scope="session")
def pg_url():
    """A database at the v2 head, for the whole session."""
    from alembic import command
    url = new_database("head")
    try:
        run_alembic(url, command.upgrade, "head")
        yield url
    finally:
        drop_database(url)


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    eng = create_engine(pg_url)
    yield eng
    eng.dispose()
