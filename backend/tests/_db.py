"""The one way tests build a database (standalone plan B02).

2026-09-24. 34 test files each built their own `create_engine("sqlite://")`.
The v2 model is schema-qualified (tenancy.users, collections.cases, …) and
SQLite has no schemas, so every one of them would have failed at
`create_all`. They now share this factory:

- `make_engine()` — in-memory SQLite (StaticPool, one connection, so every
  session sees the same database) with `schema_translate_map` mapping all
  eleven schemas to None. This only works because table and index names are
  globally unique across schemas (tests/test_schema_v2.py checks it).
- `make_session_factory(engine)` — a sessionmaker whose sessions carry
  `info["default_tenant"]`, so fixtures written before tenancy existed still
  produce rows with a bank and an agency (models/tenancy_listener.py).
  Production sessions never set it.
- `create_schema(engine)` — create_all plus the lookup rows, plus the default
  test bank + agency rows themselves, so FK-shaped reads (`loan.bank`) work.
- `test_id(name)` — a deterministic UUID for a readable name. Ids are native
  UUIDs in v2; a literal like "c1" is accepted by SQLite on insert, stored
  mangled and raises on read (measured, design §2.2).
"""
from __future__ import annotations

import uuid

from sqlalchemy import create_engine, insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import DOMAIN_SCHEMAS, Base
import app.models  # noqa: F401 — registers every table and the tenant listener
from app.models.lookups import LOOKUP_MODELS, LOOKUP_SEEDS

SCHEMA_MAP = {s: None for s in (*DOMAIN_SCHEMAS, "public")}

_NS = uuid.UUID("7c3b1a52-2c1e-4b8e-9d6f-5a0f3e2b9c11")


def test_id(name: str) -> str:
    """Deterministic UUID for a human-readable test name."""
    return str(uuid.uuid5(_NS, name))


test_id.__test__ = False  # not a pytest test, despite the name

TEST_BANK_ID = test_id("bank:meridian-trust")
TEST_AGENCY_ID = test_id("agency:aravalli-field-services")
DEFAULT_TENANT = {"bank_id": TEST_BANK_ID, "agency_id": TEST_AGENCY_ID}


def make_engine(url: str = "sqlite://"):
    engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return engine.execution_options(schema_translate_map=SCHEMA_MAP)


def make_session_factory(engine=None, *, bind=None, **kw):
    """A sessionmaker with the test default tenant. Accepts `bind=` as well as
    a positional engine, so `sessionmaker(bind=engine, …)` call sites migrate
    by name only. `autocommit` is dropped (SQLAlchemy 2 has no such option;
    the old call sites passed False, which was already the only behaviour)."""
    kw.pop("autocommit", None)
    kw.setdefault("autoflush", False)
    kw.setdefault("info", {"default_tenant": dict(DEFAULT_TENANT)})
    return sessionmaker(bind=engine if engine is not None else bind, **kw)


def create_schema(engine=None, *, bind=None, seed_tenant: bool = True) -> None:
    """create_all + lookup rows + the default test bank and agency. Idempotent:
    several suites call it once per test without dropping in between."""
    from sqlalchemy import select, func
    from app.models.tenancy import Agency, Bank

    engine = engine if engine is not None else bind
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for table, rows in LOOKUP_SEEDS.items():
            model = LOOKUP_MODELS[table]
            if conn.execute(select(func.count()).select_from(model.__table__)).scalar():
                continue
            conn.execute(insert(model.__table__), rows)
        already = conn.execute(select(func.count()).select_from(Bank.__table__)
                               .where(Bank.__table__.c.id == TEST_BANK_ID)).scalar()
        if seed_tenant and not already:
            conn.execute(insert(Bank.__table__), [{
                "id": TEST_BANK_ID, "code": "MTB", "legal_name": "Meridian Trust Bank Ltd.",
                "display_name": "Meridian Trust Bank", "timezone": "Asia/Kolkata", "brand": {},
                "status": "ACTIVE", "is_demo": True,
            }])
            conn.execute(insert(Agency.__table__), [{
                "id": TEST_AGENCY_ID, "bank_id": TEST_BANK_ID, "code": "AGENCY-TIQ-001",
                "legal_name": "Aravalli Field Services Pvt. Ltd.", "trade_name": "Aravalli Field Services",
                "status": "ACTIVE", "contacts": [], "is_demo": True,
            }])


def drop_schema(engine=None, *, bind=None) -> None:
    Base.metadata.drop_all(engine if engine is not None else bind)
