# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B11, coordinator decision on the re-audit) — NEW. The entrypoint
#   leaves an initialised v2 database alone and never runs `alembic upgrade
#   head` on it — deliberately: upgrading at boot is a deployment decision,
#   not a side effect of a restart. But it also never SAID anything, so a
#   database whose post-restore upgrade failed, or that simply predates the
#   image, ran newer code against an older schema in silence (the platform's
#   `fieldops` was found 14 migrations behind on 2026-09-21, CLAUDE.md issue
#   5). This compares the database's revision with the code's head and fails
#   the container start, naming both and the exact command to run.
# ────────────────────────────────────────────────────────────────────────────
"""Refuse to run against a database that is not at this code's alembic head.

    python -m scripts.check_migrations     # exit 0 at head, 1 otherwise

Never upgrades anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.core.config import settings

_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def code_heads() -> set[str]:
    cfg = Config(str(_INI))
    cfg.set_main_option("script_location", str(_INI.parent / "alembic"))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def database_heads(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            # The v2 version table lives in public (alembic/env.py).
            return set(MigrationContext.configure(conn, opts={"version_table_schema": "public"}).get_current_heads())
    finally:
        engine.dispose()


def main() -> int:
    want = code_heads()
    have = database_heads(settings.DATABASE_URL)
    if have == want:
        print(f"[migrations] database is at head ({', '.join(sorted(want))})")
        return 0
    print(f"[migrations] ERROR: database revision {sorted(have) or ['<none>']} "
          f"is not this code's head {sorted(want)}.", file=sys.stderr)
    print("[migrations] Nothing is upgraded automatically. After taking a backup, run:\n"
          "[migrations]     docker compose exec api alembic upgrade head\n"
          "[migrations] (or `alembic upgrade head` in backend/ with DATABASE_URL set).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
