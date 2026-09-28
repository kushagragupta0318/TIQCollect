# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B11, coordinator MED 4) — NEW. The v2 model resolves unqualified
#   names ("FROM agents" in raw SQL and scripts) through a DATABASE-level
#   search_path, and dates through a database-level timezone; v2_0001 sets
#   both with ALTER DATABASE. Those settings are properties of the database,
#   not of its contents, so a fixture restored with pg_restore (no -C) never
#   carries them — and the restored alembic_version is already past v2_0001,
#   so the migration never runs again to set them. A restored demo therefore
#   came up with search_path "$user", public and every unqualified query
#   failing. The entrypoint now runs `--apply` after a restore and on every
#   start of the seeding container (idempotent), and `--check` elsewhere.
# ────────────────────────────────────────────────────────────────────────────
"""Apply or check the v2 database-level settings (search_path, timezone).

    python -m scripts.ensure_db_settings --apply   # set both, then verify
    python -m scripts.ensure_db_settings --check   # verify only

Exit 0 when a fresh connection sees both settings as the v2 model needs
them, 1 otherwise. The search_path is app.core.database.SEARCH_PATH — the
one definition — never restated here.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.database import SEARCH_PATH

_UTC = {"UTC", "ETC/UTC"}


def _norm(path: str) -> list[str]:
    return [p.strip().strip('"') for p in path.split(",") if p.strip()]


def apply(engine) -> None:
    with engine.begin() as conn:
        db = conn.execute(text("SELECT current_database()")).scalar_one()
        q = conn.dialect.identifier_preparer.quote(db)
        conn.execute(text(f"ALTER DATABASE {q} SET search_path TO {SEARCH_PATH}"))
        conn.execute(text(f"ALTER DATABASE {q} SET timezone TO 'UTC'"))


def problems(engine) -> list[str]:
    """What a NEW connection sees (database-level settings apply at connect)."""
    engine.dispose()
    with engine.connect() as conn:
        path = conn.execute(text("SHOW search_path")).scalar_one()
        tz = conn.execute(text("SHOW timezone")).scalar_one()
    out = []
    if _norm(path) != _norm(SEARCH_PATH):
        out.append(f"search_path is {path!r}, expected {SEARCH_PATH!r}")
    if str(tz).upper() not in _UTC:
        out.append(f"timezone is {tz!r}, expected 'UTC'")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    # A plain engine: no connect listener may paper over a missing setting.
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    try:
        if args.apply:
            apply(engine)
        found = problems(engine)
    finally:
        engine.dispose()
    for p in found:
        print(f"[db-settings] {p}", file=sys.stderr)
    if not found:
        print("[db-settings] search_path and timezone are as the v2 model needs")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
