# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-14 — Added visits.notes/consent_given/signature_key to
#   new_visit_columns. Full detail + why: /changelog.md
# ───────────────────────────────────────────────────────────────────────────
"""
One-time migration — adds any missing columns/tables to existing DB without wiping data.
Run from backend/ directory:
    python -m scripts.migrate_add_tables
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text
from app.core.database import engine
import app.models  # noqa: F401 — registers all models with Base.metadata

def main():
    insp = inspect(engine)

    # Add new visit columns if they don't exist yet (safe on existing data)
    new_visit_columns = [
        ("agent_photo_accuracy",        "FLOAT"),
        ("agent_photo_altitude",        "FLOAT"),
        ("agent_photo_captured_at",     "TIMESTAMP WITH TIME ZONE"),
        ("agent_photo_sha256",          "VARCHAR(64)"),
        ("borrower_photo_accuracy",     "FLOAT"),
        ("borrower_photo_altitude",     "FLOAT"),
        ("borrower_photo_captured_at",  "TIMESTAMP WITH TIME ZONE"),
        ("borrower_photo_sha256",       "VARCHAR(64)"),
        ("object_photo_accuracy",       "FLOAT"),
        ("object_photo_altitude",       "FLOAT"),
        ("object_photo_captured_at",    "TIMESTAMP WITH TIME ZONE"),
        ("object_photo_sha256",         "VARCHAR(64)"),
        ("device_id",                   "VARCHAR(200)"),
        # 2026-07-14 — RecordVisitRequest.notes/consent_given/signature_key were
        # accepted by the API but had no column, so they were silently dropped.
        # See changelog.md 2026-07-14 "visit_service.py API fix" entry.
        ("notes",                       "TEXT"),
        ("consent_given",               "BOOLEAN"),
        ("signature_key",               "VARCHAR(500)"),
    ]

    existing_visit_cols = {col["name"] for col in insp.get_columns("visits")}
    with engine.begin() as conn:
        for col_name, col_type in new_visit_columns:
            if col_name not in existing_visit_cols:
                conn.execute(text(f'ALTER TABLE visits ADD COLUMN "{col_name}" {col_type}'))
                print(f"  Added visits.{col_name}")
            else:
                print(f"  visits.{col_name} already exists — skipped")

    print("Done. All existing data is untouched.")

if __name__ == "__main__":
    main()
