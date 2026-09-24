# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B02) — the v2 data model (docs/DATA-MODEL-V2.md §2).
#   - Ten domain schemas. Every model is schema-qualified; every Postgres
#     connection also sets a search_path covering all ten, so raw SQL and the
#     many scripts that write `FROM agents` keep resolving. That is only safe
#     because table names are globally unique across schemas (Appendix A of
#     the design, re-checked by tests/test_schema_v2.py).
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
SEARCH_PATH = ", ".join((*DOMAIN_SCHEMAS, "public"))

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}

engine = create_engine(
    settings.DATABASE_URL,
    poolclass=QueuePool,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.DEBUG,
)


@event.listens_for(engine, "connect")
def set_pg_session_defaults(dbapi_conn, _):
    with dbapi_conn.cursor() as cur:
        cur.execute("SET timezone='UTC'")
        cur.execute(f"SET search_path TO {SEARCH_PATH}")


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
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
