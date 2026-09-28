"""B19: the v2 schema on a real Postgres — the migration chain, the model
diff, the enums, the database-level settings and the permission seed."""
from __future__ import annotations

from sqlalchemy import Enum, create_engine, text

from tests.pg.conftest import drop_database, new_database, run_alembic


def test_the_head_matches_the_models(pg_url):
    """`alembic check`: autogenerate finds nothing to do against the models."""
    from alembic import command
    run_alembic(pg_url, command.check)


def test_the_chain_downgrades_to_base_and_upgrades_again():
    from alembic import command
    url = new_database("roundtrip")
    try:
        run_alembic(url, command.upgrade, "head")
        run_alembic(url, command.downgrade, "base")
        run_alembic(url, command.upgrade, "head")
        run_alembic(url, command.check)
    finally:
        drop_database(url)


def test_every_native_enum_holds_the_models_values_in_order(pg_engine):
    import app.models  # noqa: F401
    from app.core.database import Base
    want = {}
    for t in Base.metadata.sorted_tables:
        for c in t.columns:
            if isinstance(c.type, Enum) and c.type.native_enum and c.type.name:
                want[c.type.name] = list(c.type.enums)
    with pg_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT t.typname, e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid "
            "JOIN pg_namespace n ON n.oid = t.typnamespace WHERE n.nspname = 'public' "
            "ORDER BY t.typname, e.enumsortorder"))
        got = {}
        for name, label in rows:
            got.setdefault(name, []).append(label)
    assert {k: got.get(k) for k in want} == want


def test_the_database_carries_search_path_and_timezone(pg_url):
    from app.core.database import SEARCH_PATH
    eng = create_engine(pg_url)
    try:
        with eng.connect() as conn:
            path = [p.strip() for p in conn.execute(text("SHOW search_path")).scalar().split(",")]
            assert path == [p.strip() for p in SEARCH_PATH.split(",")]
            assert conn.execute(text("SHOW timezone")).scalar().upper() in ("UTC", "ETC/UTC")
    finally:
        eng.dispose()


def test_the_permission_seed_is_loaded(pg_engine):
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM tenancy.permissions")).scalar() == 74
        assert conn.execute(text("SELECT count(*) FROM tenancy.role_permissions")).scalar() == 138
