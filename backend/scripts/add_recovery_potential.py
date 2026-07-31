"""
One-shot migration: add recovery_potential to loans table only.
Touches nothing else. Safe to re-run (idempotent).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from app.core.database import engine

SQL = """
-- 1. Create enum type if it doesn't already exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'recovery_potential_enum') THEN
        CREATE TYPE recovery_potential_enum AS ENUM ('HIGH', 'MEDIUM', 'LOW');
    END IF;
END$$;

-- 2. Add column if it doesn't already exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'loans' AND column_name = 'recovery_potential'
    ) THEN
        ALTER TABLE loans ADD COLUMN recovery_potential recovery_potential_enum;
    END IF;
END$$;

-- 3. Populate using a single random() draw per row so weights are exact.
--    Signal: DPD is the primary driver; loan_type provides a secondary bump.
--    Weights:
--      SMA-1 (dpd <= 60): HIGH 50%, MEDIUM 30%, LOW 20%
--      SMA-2 (61-90):     HIGH 20%, MEDIUM 45%, LOW 35%
--      NPA   (90+):       HIGH  8%, MEDIUM 22%, LOW 70%
--    Secured loans (HOME / AUTO / GOLD) shift +8% toward HIGH.
WITH rv AS (
    SELECT
        id,
        random()                                     AS r,
        dpd,
        loan_type IN ('HOME', 'AUTO', 'GOLD')        AS is_secured
    FROM loans
    WHERE recovery_potential IS NULL
)
UPDATE loans l
SET recovery_potential = (
    CASE
        -- SMA-1
        WHEN rv.dpd <= 60 AND     rv.is_secured AND rv.r < 0.58 THEN 'HIGH'
        WHEN rv.dpd <= 60 AND     rv.is_secured AND rv.r < 0.86 THEN 'MEDIUM'
        WHEN rv.dpd <= 60 AND     rv.is_secured                 THEN 'LOW'
        WHEN rv.dpd <= 60 AND NOT rv.is_secured AND rv.r < 0.50 THEN 'HIGH'
        WHEN rv.dpd <= 60 AND NOT rv.is_secured AND rv.r < 0.80 THEN 'MEDIUM'
        WHEN rv.dpd <= 60 AND NOT rv.is_secured                 THEN 'LOW'
        -- SMA-2
        WHEN rv.dpd <= 90 AND     rv.is_secured AND rv.r < 0.28 THEN 'HIGH'
        WHEN rv.dpd <= 90 AND     rv.is_secured AND rv.r < 0.73 THEN 'MEDIUM'
        WHEN rv.dpd <= 90 AND     rv.is_secured                 THEN 'LOW'
        WHEN rv.dpd <= 90 AND NOT rv.is_secured AND rv.r < 0.20 THEN 'HIGH'
        WHEN rv.dpd <= 90 AND NOT rv.is_secured AND rv.r < 0.65 THEN 'MEDIUM'
        WHEN rv.dpd <= 90 AND NOT rv.is_secured                 THEN 'LOW'
        -- NPA
        WHEN rv.is_secured AND rv.r < 0.16 THEN 'HIGH'
        WHEN rv.is_secured AND rv.r < 0.46 THEN 'MEDIUM'
        WHEN rv.is_secured                 THEN 'LOW'
        WHEN rv.r < 0.08                   THEN 'HIGH'
        WHEN rv.r < 0.30                   THEN 'MEDIUM'
        ELSE                                    'LOW'
    END
)::recovery_potential_enum
FROM rv
WHERE l.id = rv.id;
"""

with engine.begin() as conn:
    conn.execute(text(SQL))

print("Done — recovery_potential added and populated on loans table.")

# Quick distribution check
with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT recovery_potential, COUNT(*) FROM loans GROUP BY 1 ORDER BY 1"
    )).fetchall()
    total = sum(r[1] for r in rows)
    print(f"\nDistribution across {total} loans:")
    for tag, cnt in rows:
        print(f"  {tag or 'NULL':8s}  {cnt:4d}  ({cnt/total*100:.1f}%)")
