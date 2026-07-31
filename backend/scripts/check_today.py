"""Check all of agent002's activity created today."""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

from app.core.database import SessionLocal
from app.models.visit import Visit
from app.models.payment import Payment
from app.models.ptp import PTP
from app.models.case import Case, CaseStatus
from app.models.user import User
from app.models.agent import Agent
from datetime import date, datetime, timezone

db = SessionLocal()

u = db.query(User).filter(User.email == "agent002@tiqcollect.in").first()
agent = db.query(Agent).filter(Agent.user_id == u.id).first()
today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)

# Visits today
visits = db.query(Visit).filter(Visit.agent_id == agent.id, Visit.check_in_time >= today_start).all()
print(f"Visits today by agent002: {len(visits)}")
for v in visits:
    case = db.query(Case).filter(Case.id == v.case_id).first()
    t = v.check_in_time.strftime("%H:%M")
    print(f"  {case.case_number} | outcome={v.outcome.value} | geo={v.geo_verified} | @{t}")

# Payments today
payments = db.query(Payment).filter(Payment.agent_id == agent.id, Payment.payment_date >= today_start).all()
print(f"\nPayments today by agent002: {len(payments)}")
for p in payments:
    print(f"  {p.receipt_number} | Rs{p.amount:,.0f} | {p.mode.value}")

# PTPs today
ptps = db.query(PTP).filter(PTP.agent_id == agent.id, PTP.created_at >= today_start).all()
print(f"\nPTPs today by agent002: {len(ptps)}")
for ptp in ptps:
    print(f"  Rs{ptp.committed_amount:,.0f} due {ptp.committed_date} | {ptp.status.value}")

# Cases that changed status today (not ASSIGNED anymore)
changed = db.query(Case).filter(
    Case.agent_id == agent.id,
    Case.status != CaseStatus.ASSIGNED,
    Case.allocation_date == date.today().isoformat()
).all()
print(f"\nCases with changed status today: {len(changed)}")
for c in changed:
    print(f"  {c.case_number} | status={c.status.value} | collected=Rs{c.collected_amount:,.0f}")

db.close()
