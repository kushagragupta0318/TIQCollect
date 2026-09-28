# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B02) — the v2 data model (docs/DATA-MODEL-V2.md §2).
#   - Ten domain schemas. Every model is schema-qualified. Raw SQL and the
#     many scripts that write `FROM agents` keep resolving through a
#     search_path covering all ten — set ON THE DATABASE by the v2 baseline
#     migration (v2_0001: `ALTER DATABASE … SET search_path`, public first),
#     not per connection here.
#     A session-level SET at connect is lost or leaks under PgBouncer
#     transaction pooling, and only this engine would get it: the Celery
#     worker, Alembic, psql, pg_restore and every script that builds its own
#     engine would not (coordinator review, 2026-09-24). The same applies to
#     timezone='UTC', which moves to the database with it. Unqualified names
#     are only safe because table names are globally unique across schemas
#     (Appendix A of the design, re-checked by tests/test_schema_v2.py).
#   - A naming convention on the metadata, so every constraint and index has a
#     deterministic name the migrations and tests can refer to.
# ────────────────────────────────────────────────────────────────────────────
from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase
from sqlalchemy.pool import QueuePool
from typing import Generator
from app.core.config import settings

# The ten domain schemas, in search_path order. `public` holds infrastructure
# only: alembic_version, demo_baseline and the shared native enum types.
DOMAIN_SCHEMAS: tuple[str, ...] = (
    "tenancy", "lending", "collections", "workforce", "planning",
    "ml", "ai", "strategy", "audit", "analytics",
)
# `public` first (audit W6); the v2 baseline sets exactly this on the database.
SEARCH_PATH = ", ".join(("public", *DOMAIN_SCHEMAS))

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}

# 2026-09-28 (B14): the pool comes from settings (5 + 5 per process, was
# 20 + 40: six processes could open 360 connections against max_connections
# 100), and every transaction starts with SET LOCAL statement_timeout. Nothing
# is SET at session level any more: under PgBouncer transaction pooling a
# session SET leaks to the next client or silently vanishes. (A connect-time
# `SET timezone='UTC'` was here for v1; v2 carries timezone on the database,
# v2_0001, and this branch refuses a v1 database at boot.)
engine = create_engine(
    settings.DATABASE_URL,
    poolclass=QueuePool,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.DEBUG,
)

# The API's timeout until a Celery worker switches this process to the jobs'.
_statement_timeout_ms = settings.API_STATEMENT_TIMEOUT_MS


def use_job_statement_timeout() -> None:
    """Called once when a Celery worker process starts (celery_app): nightly
    jobs legitimately run for minutes; a request never should."""
    global _statement_timeout_ms
    _statement_timeout_ms = settings.JOB_STATEMENT_TIMEOUT_MS


def statement_timeout_ms() -> int:
    return _statement_timeout_ms


def _on_begin_postgres(conn, *, read_only: bool = False) -> None:
    if conn.dialect.name != "postgresql":
        return                       # the SQLite suite has no such settings
    conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(_statement_timeout_ms)}")
    if read_only:
        conn.exec_driver_sql("SET LOCAL transaction_read_only = on")


@event.listens_for(engine, "begin")
def _begin(conn):
    _on_begin_postgres(conn)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# A13 (DATA-MODEL-V2 §8.1): the request's tenant, set on EVERY transaction of
# its session with set_config(..., is_local => true), so it ends with the
# transaction and a pooled connection never carries it into another request.
TENANT_CONTEXT = "tenant_context"
_SET_TENANT = text(
    "SELECT set_config('app.bank_id', :bank_id, true), set_config('app.agency_id', :agency_id, true),"
    " set_config('app.scope', :scope, true), set_config('app.user_id', :user_id, true)")


def _set_tenant(connection, ctx: dict) -> None:
    connection.execute(_SET_TENANT, {k: str(ctx.get(k) or "") for k in ("bank_id", "agency_id", "scope", "user_id")})


@event.listens_for(Session, "after_begin")
def _tenant_on_begin(session, transaction, connection):
    ctx = session.info.get(TENANT_CONTEXT)
    if ctx and connection.dialect.name == "postgresql":
        _set_tenant(connection, ctx)


def apply_tenant_context(db: Session, *, bank_id, agency_id, scope: str, user_id) -> None:
    """Bind the principal's tenant to this session: now, if a transaction is
    open, and at the start of every later one."""
    ctx = {"bank_id": bank_id, "agency_id": agency_id, "scope": scope, "user_id": user_id}
    db.info[TENANT_CONTEXT] = ctx
    if db.in_transaction() and db.get_bind().dialect.name == "postgresql":
        _set_tenant(db.connection(), ctx)

# The analytics read path (B14): a replica when ANALYTICS_DATABASE_URL is set,
# else the primary; every transaction on it is READ ONLY, so a bank screen can
# never write, whatever its code does. A small pool of its own.
analytics_engine = create_engine(
    settings.ANALYTICS_DATABASE_URL or settings.DATABASE_URL,
    poolclass=QueuePool, pool_size=2, max_overflow=3, pool_pre_ping=True, pool_recycle=3600,
    echo=settings.DEBUG,
)


@event.listens_for(analytics_engine, "begin")
def _begin_analytics(conn):
    _on_begin_postgres(conn, read_only=True)


AnalyticsSession = sessionmaker(autocommit=False, autoflush=False, bind=analytics_engine)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_analytics_db() -> Generator[Session, None, None]:
    """The read-only analytics session (B14). Writes on it fail at Postgres."""
    db = AnalyticsSession()
    try:
        yield db
    finally:
        db.close()


def check_db_connection() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
