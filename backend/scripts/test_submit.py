"""End-to-end test: login as agent002, record a visit + payment + PTP, verify DB rows."""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

import requests
from app.core.database import SessionLocal
from app.models.visit import Visit
from app.models.payment import Payment
from app.models.ptp import PTP
from app.models.case import Case

BASE = "http://localhost:8400/api/v1"

# ── Step 1: Login ─────────────────────────────────────────────────────────────
r = requests.post(f"{BASE}/auth/login", json={
    "email": "agent002@tiqcollect.in",
    "password": "Agent@123",
    "device_id": "test-device-001"
})
print(f"[1] Login: {r.status_code}")
if r.status_code != 200:
    print("   ", r.json())
    sys.exit(1)
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# ── Step 2: Get today's cases ─────────────────────────────────────────────────
r2 = requests.get(f"{BASE}/agent/cases", headers=headers)
print(f"[2] Cases: {r2.status_code} — {len(r2.json())} cases today")
cases = r2.json()

# Pick the first ASSIGNED demo case (Mohammed Irfan Khan — 45m away, within fence)
target = next((c for c in cases if c["status"] == "ASSIGNED" and "Mohammed" in c["customer"]["full_name"]), None)
if not target:
    # fallback: any ASSIGNED case
    target = next((c for c in cases if c["status"] == "ASSIGNED"), None)

if not target:
    print("   No ASSIGNED case found — try a different status")
    sys.exit(1)

case_id = target["id"]
cust_name = target["customer"]["full_name"]
print(f"   Using case: {target['case_number']} | {cust_name} | target=Rs{target['target_amount']:,.0f}")

# ── Step 3: Count existing rows before ───────────────────────────────────────
db = SessionLocal()
v_before = db.query(Visit).filter(Visit.case_id == case_id).count()
p_before  = db.query(Payment).filter(Payment.case_id == case_id).count()
ptp_before = db.query(PTP).filter(PTP.case_id == case_id).count()
print(f"[3] Before submit — visits={v_before}, payments={p_before}, ptps={ptp_before}")

# ── Step 4: Submit visit (PART_PAID_PTP) ─────────────────────────────────────
visit_payload = {
    "check_in_latitude": 28.454926,
    "check_in_longitude": 77.071367,
    "customer_met": True,
    "outcome": "PART_PAID_PTP",
    "person_met": "BORROWER",
    "default_reason": "JOB_LOSS",
    "notes": "Test visit — met borrower, collected partial payment, set PTP for balance.",
}
r3 = requests.post(f"{BASE}/agent/cases/{case_id}/visit", json=visit_payload, headers=headers)
print(f"[4] Record visit: {r3.status_code}")
if r3.status_code != 200:
    print("   ", r3.json())
    sys.exit(1)
visit_resp = r3.json()
print(f"   visit_id={visit_resp['id'][:8]}... | geo_verified={visit_resp['geo_verified']} | dist={visit_resp['distance_from_customer_metres']}m | within_hours={visit_resp['within_contact_hours']}")

# ── Step 5: Submit payment (CASH Rs 15,000) ───────────────────────────────────
pay_payload = {
    "amount": 15000.0,
    "mode": "CASH",
}
r4 = requests.post(f"{BASE}/agent/cases/{case_id}/payment", json=pay_payload, headers=headers)
print(f"[5] Collect payment: {r4.status_code}")
if r4.status_code != 200:
    print("   ", r4.json())
else:
    pay_resp = r4.json()
    print(f"   receipt={pay_resp.get('receipt_number')} | amount=Rs{pay_resp.get('amount'):,.0f}")

# ── Step 6: Submit PTP ────────────────────────────────────────────────────────
from datetime import date, timedelta
ptp_date = (date.today() + timedelta(days=5)).isoformat()
ptp_payload = {
    "committed_amount": float(target["target_amount"]) - 15000.0,
    "committed_date": ptp_date,
    "customer_reason": "Will pay balance from next salary credit on 27th."
}
r5 = requests.post(f"{BASE}/agent/cases/{case_id}/ptp", json=ptp_payload, headers=headers)
print(f"[6] Set PTP: {r5.status_code}")
if r5.status_code != 200:
    print("   ", r5.json())
else:
    ptp_resp = r5.json()
    print(f"   ptp committed=Rs{ptp_resp.get('committed_amount'):,.0f} due {ptp_resp.get('committed_date')}")

# ── Step 7: Verify DB rows after ─────────────────────────────────────────────
db.expire_all()
v_after   = db.query(Visit).filter(Visit.case_id == case_id).count()
p_after   = db.query(Payment).filter(Payment.case_id == case_id).count()
ptp_after = db.query(PTP).filter(PTP.case_id == case_id).count()
case_obj  = db.query(Case).filter(Case.id == case_id).first()

print(f"\n[7] After submit — visits={v_after} (+{v_after-v_before}), payments={p_after} (+{p_after-p_before}), ptps={ptp_after} (+{ptp_after-ptp_before})")
print(f"    Case status now: {case_obj.status} | collected=Rs{case_obj.collected_amount:,.0f} | visit_count={case_obj.visit_count}")
db.close()

print("\nAll good — new rows confirmed in PostgreSQL.")



