import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import SessionLocal
from app.models.user import User, UserRole
from app.services.planner_service import PlannerService

def main():
    db = SessionLocal()
    mgr = db.query(User).filter(User.role == UserRole.AGENCY_MANAGER).first()
    p = PlannerService(db, manager_user_id=mgr.id)
    r = p.plan_next_day(strategy="SMART", force_replan=True, simulate=False)
    print(f"Total Beats Planned: {len(r.beats)}")
    for b in r.beats:
        print(f"  {b.agent.user.full_name} ({b.agent.employee_code}): {b.total_cases} stops, ~{b.estimated_distance_km:.1f} km, ₹{b.total_target_amount:,.0f}")

if __name__ == "__main__":
    main()
