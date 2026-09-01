import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import SessionLocal
from app.models.case import Case, CaseStatus

def main():
    db = SessionLocal()
    cases = db.query(Case).filter(Case.collected_amount >= Case.target_amount, Case.target_amount > 0).all()
    print(f"Total cases where collected >= target: {len(cases)}")
    status_counts = {}
    for c in cases:
        status_counts[c.status] = status_counts.get(c.status, 0) + 1
        if c.status == CaseStatus.PARTIALLY_PAID:
            print(f"  {c.case_number}: Target={c.target_amount}, Collected={c.collected_amount}, Status={c.status}")
    print("Status Breakdown:", status_counts)

if __name__ == "__main__":
    main()
