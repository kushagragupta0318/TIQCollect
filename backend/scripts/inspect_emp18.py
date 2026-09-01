import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import SessionLocal
from app.models.agent import Agent
from app.models.beat import Beat

def main():
    db = SessionLocal()
    b = db.query(Beat).join(Agent).filter(Agent.employee_code == "EMP0018").order_by(Beat.created_at.desc()).first()
    if b:
        print(f"Beat: {b.beat_number}, Total cases: {len(b.ordered_case_ids)}, Distance: {b.estimated_distance_km}")
        from app.models.case import Case
        cases = db.query(Case).filter(Case.id.in_(b.ordered_case_ids)).all()
        for c in cases:
            print(f"  Case: {c.case_number}, Lat: {c.customer.latitude}, Lon: {c.customer.longitude}, City: {c.customer.city}")

if __name__ == "__main__":
    main()
