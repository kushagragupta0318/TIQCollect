"""
Database migration script:
Ensures allocation_runs, allocation_decisions tables exist and beats.allocation_run_id column exists in PostgreSQL.
"""
import sys
import os
from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import engine, Base
import app.models  # load all models


def run_migration():
    print("Running database schema update for case allocation...")
    # 1. Create any missing tables (allocation_runs, allocation_decisions)
    Base.metadata.create_all(bind=engine)
    
    # 2. Add column to beats if missing
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE beats ADD COLUMN IF NOT EXISTS allocation_run_id VARCHAR(36) REFERENCES allocation_runs(id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_beats_allocation_run_id ON beats(allocation_run_id);"))
        conn.commit()
    print("✅ Database schema migration completed successfully!")


if __name__ == "__main__":
    run_migration()
