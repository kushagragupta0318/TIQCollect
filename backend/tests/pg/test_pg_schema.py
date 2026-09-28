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


def test_v2_0010_releases_only_the_bindings_that_have_no_secret():
    """A09b audit HIGH: a pre-A09b binding (no secret hash) is released, so the
    agent's next login binds afresh; a binding with a secret is untouched."""
    import uuid
    from alembic import command
    from app.core.database import Base
    from tests.pg.test_pg_partitions import _row
    url = new_database("v2_0010")
    try:
        run_alembic(url, command.upgrade, "v2_0009")
        devices = Base.metadata.tables["workforce.agent_devices"]
        old, new = str(uuid.uuid4()), str(uuid.uuid4())
        eng = create_engine(url)
        with eng.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))   # parents not under test
            conn.execute(devices.insert().values(**_row(devices, id=old, is_bound=True,
                                                        device_secret_sha256=None)))
            conn.execute(devices.insert().values(**_row(devices, id=new, is_bound=True,
                                                        device_secret_sha256="a" * 64)))
        run_alembic(url, command.upgrade, "v2_0010")
        with eng.connect() as conn:
            rows = {r.id: r for r in conn.execute(text(
                "SELECT id::text, is_bound, unbound_at, unbind_reason FROM workforce.agent_devices"))}
        eng.dispose()
        assert rows[old].is_bound is False and rows[old].unbound_at is not None
        assert rows[old].unbind_reason.startswith("A09b")
        assert rows[new].is_bound is True and rows[new].unbind_reason is None
    finally:
        drop_database(url)
