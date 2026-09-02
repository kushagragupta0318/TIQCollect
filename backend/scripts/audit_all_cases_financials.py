import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from app.models.case import Case, CaseStatus
from app.models.payment import Payment, PaymentStatus
from app.models.ptp import PTP, PTPStatus

db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://fieldops:fieldops_dev_pass@localhost:15432/fieldops")
engine = create_engine(db_url)
Session = sessionmaker(bind=engine)
db = Session()

print("=" * 60)
print("AUDITING ALL CASES FINANCIAL INTEGRITY")
print("=" * 60)

cases = db.query(Case).all()
print(f"Total cases in database: {len(cases)}")

errors = []
excess_collections = []
ptp_excess = []
status_mismatches = []
negative_amounts = []

for c in cases:
    # 1. Target vs Collected
    if (c.collected_amount or 0) > (c.target_amount or 0):
        excess_collections.append((c.case_number, c.customer.full_name, c.target_amount, c.collected_amount))

    # 2. Check verified payments sum vs collected_amount
    actual_paid = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.case_id == c.id,
        Payment.status == PaymentStatus.VERIFIED
    ).scalar()

    if abs(float(actual_paid) - float(c.collected_amount or 0)) > 0.05:
        errors.append((c.case_number, float(c.collected_amount or 0), float(actual_paid)))

    # 3. Check PTP committed amount vs target_amount
    ptps = db.query(PTP).filter(PTP.case_id == c.id, PTP.status.in_([PTPStatus.ACTIVE, PTPStatus.HONORED, PTPStatus.PARTIALLY_HONORED])).all()
    for p in ptps:
        if float(p.committed_amount or 0) > float(c.target_amount or 0):
            ptp_excess.append((c.case_number, p.id, float(c.target_amount or 0), float(p.committed_amount or 0)))

    # 4. Status consistency
    if c.status == CaseStatus.PAID and (c.collected_amount or 0) < (c.target_amount or 0):
        status_mismatches.append((c.case_number, "PAID but collected < target", c.target_amount, c.collected_amount))
    elif c.status == CaseStatus.PARTIALLY_PAID and ((c.collected_amount or 0) <= 0 or (c.collected_amount or 0) >= (c.target_amount or 0)):
        status_mismatches.append((c.case_number, "PARTIALLY_PAID but collected <= 0 or >= target", c.target_amount, c.collected_amount))
    elif c.status in [CaseStatus.UNASSIGNED, CaseStatus.ASSIGNED, CaseStatus.IN_PROGRESS] and (c.collected_amount or 0) >= (c.target_amount or 0) and c.target_amount > 0:
        status_mismatches.append((c.case_number, f"{c.status} but collected >= target", c.target_amount, c.collected_amount))

    # 5. Negative amounts
    if (c.target_amount or 0) < 0 or (c.collected_amount or 0) < 0:
        negative_amounts.append((c.case_number, c.target_amount, c.collected_amount))

print(f"\n1. Cases with collected_amount > target_amount: {len(excess_collections)}")
for e in excess_collections[:5]:
    print(f"   - Case {e[0]} ({e[1]}): Target=Rs.{e[2]:,.2f}, Collected=Rs.{e[3]:,.2f}")

print(f"\n2. Cases with collected_amount != SUM(verified payments): {len(errors)}")
for e in errors[:5]:
    print(f"   - Case {e[0]}: Case.collected={e[1]}, Sum(Payments)={e[2]}")

print(f"\n3. PTPs with committed_amount > target_amount: {len(ptp_excess)}")
for e in ptp_excess[:5]:
    print(f"   - Case {e[0]}: Target=Rs.{e[2]:,.2f}, PTP committed=Rs.{e[3]:,.2f}")

print(f"\n4. Status financial mismatches: {len(status_mismatches)}")
for e in status_mismatches[:5]:
    print(f"   - Case {e[0]}: {e[1]} (Target={e[2]}, Collected={e[3]})")

print(f"\n5. Negative financial values: {len(negative_amounts)}")

if not excess_collections and not errors and not ptp_excess and not status_mismatches and not negative_amounts:
    print("\n[SUCCESS] PERFECT AUDIT: All cases, payments, PTPs, and statuses are 100% consistent!")
else:
    print("\n[WARNING] AUDIT FAILED with discrepancies.")

db.close()
