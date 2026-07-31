import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import logging
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

from app.core.database import SessionLocal
from app.models.agent import Agent
from app.models.case import Case
from app.models.user import User
from sqlalchemy.orm import joinedload
from datetime import date

def haversine(lat1, lon1, lat2, lon2):
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2-lat1); dl = math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return round(2*R*math.atan2(math.sqrt(a), math.sqrt(1-a)))

db = SessionLocal()
u = db.query(User).filter(User.email == "agent002@tiqcollect.in").first()
agent = db.query(Agent).filter(Agent.user_id == u.id).first()
print(f"Agent : {u.full_name} | {u.email}")
print(f"GPS   : {agent.base_latitude}, {agent.base_longitude}")
print()

today = date.today().isoformat()
cases = (db.query(Case)
    .options(joinedload(Case.customer), joinedload(Case.loan))
    .filter(Case.agent_id == agent.id, Case.allocation_date == today)
    .all())

print(f"Total today's cases: {len(cases)}")
print()

unlocked, blocked = [], []
for c in cases:
    cust = c.customer
    d = haversine(agent.base_latitude, agent.base_longitude, cust.latitude, cust.longitude)
    row = (d, cust.full_name, c.loan.dpd, c.target_amount, str(c.loan.loan_type).replace("LoanType.", ""))
    (unlocked if d <= 100 else blocked).append(row)

unlocked.sort(); blocked.sort()

print(f"UNLOCKED — within 100m ({len(unlocked)} customers):")
for d, n, dpd, amt, lt in unlocked:
    print(f"  {d:>4}m  {n:<28}  DPD={dpd:<4}  Target=Rs{amt:>10,.0f}  {lt}")

print()
print(f"BLOCKED  — beyond 100m ({len(blocked)} customers):")
for d, n, dpd, amt, lt in blocked:
    print(f"  {d:>4}m  {n:<28}  DPD={dpd:<4}  Target=Rs{amt:>10,.0f}  {lt}")

db.close()
