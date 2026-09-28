# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-28 (B16, d4) — NEW. Puts the demo agencies' specimen PDFs into
#   object storage after a fixture restore (docker-entrypoint.sh). A pg_dump
#   carries tenancy.agency_documents but not the MinIO objects its
#   storage_keys name, so a restored box would list documents it cannot open.
#   The bytes are regenerated from the roster (app/demo/documents.py, a pure
#   function) and every one is checked against the sha256 on its row BEFORE
#   anything is uploaded: a mismatch means the roster and the fixture have
#   drifted, and the script refuses rather than publish a document the
#   database does not describe.
# ────────────────────────────────────────────────────────────────────────────
"""Upload the demo agencies' specimen documents that object storage lacks.

    python -m scripts.ensure_demo_documents        # exit 0 done / nothing to do, 1 refused or failed
"""
from __future__ import annotations

import sys

import sqlalchemy as sa
from sqlalchemy import create_engine

from app.core.config import settings
from app.demo import documents as D
from app.demo import roster as R


def plan(conn) -> tuple[list[D.Specimen], list[str]]:
    """The specimens the database's document rows describe, and every row
    whose sha256 the roster no longer reproduces."""
    t = sa.table("agency_documents", sa.column("agency_id"), sa.column("storage_key"), sa.column("sha256"),
                 schema="tenancy")
    rows = {r.storage_key: (str(r.agency_id), r.sha256) for r in conn.execute(sa.select(t.c.agency_id,
                                                                                        t.c.storage_key, t.c.sha256))}
    wanted, drift = [], []
    for agency in R.AGENCIES:
        for s in D.specimens(agency):
            got = rows.get(s.storage_key)
            if got is None:
                continue                                  # not in this database (another profile)
            if got != (agency.id, s.sha256):
                drift.append(s.storage_key)
            else:
                wanted.append(s)
    return wanted, drift


def main() -> int:
    engine = create_engine(settings.DATABASE_URL)
    with engine.connect() as conn:
        wanted, drift = plan(conn)
    if drift:
        print(f"[ensure_demo_documents] REFUSED: {len(drift)} document row(s) do not match the roster's "
              f"specimen (first: {drift[0]}); regenerate the fixture", file=sys.stderr)
        return 1
    if not wanted:
        print("[ensure_demo_documents] no demo agency documents in this database; nothing to do")
        return 0
    try:
        up, have = D.upload_missing(wanted)
    except Exception as exc:  # noqa: BLE001 — reported; the caller decides whether it is fatal
        print(f"[ensure_demo_documents] object storage unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"[ensure_demo_documents] {up} uploaded, {have} already present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
