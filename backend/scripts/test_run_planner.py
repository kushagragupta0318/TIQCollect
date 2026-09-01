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
    r_smart = p.plan_next_day(strategy="SMART", simulate=True)
    r_leg = p.plan_next_day(strategy="LEGACY", simulate=True)
    print(f"SMART Recovery:  ₹{r_smart.expected_recovery_total:,.2f} ({r_smart.total_cases_allocated} cases)")
    print(f"LEGACY Recovery: ₹{r_leg.expected_recovery_total:,.2f} ({r_leg.total_cases_allocated} cases)")
    diff_pct = ((r_smart.expected_recovery_total - r_leg.expected_recovery_total) / r_leg.expected_recovery_total) * 100
    print(f"Smart ML Lift:   +{diff_pct:.1f}% higher recovery")

if __name__ == "__main__":
    main()
