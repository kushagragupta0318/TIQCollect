import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import SessionLocal
from app.models.user import User, UserRole
from app.services.planner_service import PlannerService

def main():
    db = SessionLocal()
    mgr = db.query(User).filter(User.role == UserRole.AGENCY_MANAGER).first()
    print("Found manager:", mgr.id, mgr.email)
    p = PlannerService(db, manager_user_id=mgr.id)
    run = p.plan_next_day()
    print("Run result:", run.id, "Allocated:", run.total_cases_allocated, "Recovery:", run.expected_recovery_total)

if __name__ == "__main__":
    main()
