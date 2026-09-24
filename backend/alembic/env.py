# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B11, docs/DATA-MODEL-V2.md §2.12, §9.1) — schema-aware.
#   - include_schemas=True: the v2 model spans ten domain schemas; without it
#     autogenerate sees only `public` and would propose dropping everything
#     else on the next diff.
#   - version_table_schema="public": `alembic_version` stays where every
#     deployment already has it.
#   - include_name: only our schemas are compared, never pg_catalog/other
#     apps' schemas in a shared database.
#   - search_path is pinned to `public` for the migration connection
#     (coordinator audit item 6): a migration must name every object by
#     schema, and a role-level search_path that happens to list `tenancy`
#     first must not decide where an unqualified object lands.
# ────────────────────────────────────────────────────────────────────────────
import re
from logging.config import fileConfig

import sqlalchemy as sa
from sqlalchemy import engine_from_config, pool, text
from alembic import context
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import settings
from app.core.database import DOMAIN_SCHEMAS, Base
import app.models  # noqa: F401 — registers all models

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
OUR_SCHEMAS = set(DOMAIN_SCHEMAS) | {"public"}


# Partitions are created by v2_0003 and the maintenance task (B12), never by
# the models, so autogenerate must not see them as tables to drop.
_PARTITION = re.compile(r"(_p\d{6}|_default|^agent_locations_(sos|trail.*))$")


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name in OUR_SCHEMAS or name is None
    if type_ == "table":
        return name != "alembic_version" and not _PARTITION.search(name or "")
    return True


# Postgres clones an FK that references a PARTITIONED table once per
# partition. Those clones are not ours to declare; the parent constraint
# (named by our convention) is. Identified exactly — pg_constraint.conparentid
# <> 0 — not by name pattern, so a real DB-only FK with a default PG name is
# still reported (coordinator audit MED 6). Filled per connection below.
_PG_FK_CLONES: set[str] = set()


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "foreign_key_constraint" and reflected and compare_to is None and name in _PG_FK_CLONES:
        return False
    if type_ == "table" and reflected and compare_to is None and _PARTITION.search(name or ""):
        return False
    # The models declare UNIQUE (id, <partition key>) on the partitioned
    # tables because SQLite (the suite) needs a unique target for the
    # composite FKs into them. On Postgres the PRIMARY KEY is exactly those
    # columns (v2_0002), so a second, identical unique index is not created.
    if (type_ == "unique_constraint" and not reflected
            and tuple(c.name for c in obj.columns) in _PARTITIONED_PK.get(obj.table.name, ())):
        return False
    return True


_PARTITIONED_PK = {
    "model_predictions": (("id", "as_of_date"),),
    "allocation_decisions": (("id", "plan_date"),),
    "audit_logs": (("id", "created_at"),),
}


def compare_server_default(context, inspected_column, metadata_column, inspected_default, metadata_default,
                           rendered_metadata_default):
    """The surrogate `id`'s gen_random_uuid() lives only in the migration (the
    models give ids in Python, and SQLite cannot express it). Everything else
    is compared (coordinator audit MED 6)."""
    if metadata_column.name == "id" and inspected_default and "gen_random_uuid" in inspected_default:
        return False
    return None


def compare_type(context, inspected_column, metadata_column, inspected_type, metadata_type):
    """Enum types are created by v2_0001 and changed only by explicit
    ALTER TYPE revisions; the ENUM-vs-Enum rendering difference is not a
    change. Everything else: alembic's default comparison."""
    if isinstance(metadata_type, sa.Enum):
        return False
    return None


_COMMON = dict(
    target_metadata=target_metadata,
    include_schemas=True,
    include_name=include_name,
    include_object=include_object,
    version_table_schema="public",
    compare_type=compare_type,
    compare_server_default=compare_server_default,
)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"}, **_COMMON)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.",
                                     poolclass=pool.NullPool)
    with connectable.connect() as connection:
        connection.execute(text("SET search_path TO public"))
        _PG_FK_CLONES.update(r[0] for r in connection.execute(text(
            "SELECT conname FROM pg_constraint WHERE contype = 'f' AND conparentid <> 0")))
        connection.commit()
        context.configure(connection=connection, **_COMMON)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
