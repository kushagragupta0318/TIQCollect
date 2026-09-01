import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from app.core.database import engine

def main():
    print("Fixing 100% paid cases in PostgreSQL database...")
    with engine.connect() as conn:
        res = conn.execute(text("""
            UPDATE cases 
            SET status = 'PAID', 
                resolved_at = COALESCE(resolved_at, NOW())
            WHERE (collected_amount >= target_amount OR abs(collected_amount - target_amount) < 0.01)
              AND target_amount > 0 
              AND status NOT IN ('PAID', 'CLOSED', 'WRITTEN_OFF');
        """))
        conn.commit()
        print(f"✅ Updated {res.rowcount} cases to PAID status!")

if __name__ == "__main__":
    main()
