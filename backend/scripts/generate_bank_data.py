"""
TIQCollect — Bank Portfolio Data Generator
-------------------------------------------
Generates a realistic dummy CSV in the exact format a bank sends to a
collection agency. Uses standard RBI / industry column names.

Output: data/incoming/bank_portfolio_YYYYMMDD.csv

Usage:
  python -m scripts.generate_bank_data                    # 200 rows, today's date
  python -m scripts.generate_bank_data --rows 500         # 500 rows
  python -m scripts.generate_bank_data --out custom.csv   # custom output path

After generating, ingest into TIQCollect:
  python -m scripts.ingest_daily --file data/incoming/bank_portfolio_YYYYMMDD.csv
"""

import sys
import os
import csv
import math
import random
import argparse
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from faker import Faker

fake = Faker("en_IN")
random.seed(None)  # fresh random each run

# ── Output columns (exact bank field names) ───────────────────────────────────
BANK_COLUMNS = [
    # ── Block 1: Customer Identity (sent from bank's CRM / KYC system)
    "CUSTOMER_ID",
    "CUSTOMER_NAME",
    "PHONE_NUMBER",
    "EMAIL",
    "ADDRESS",
    "CITY",
    "STATE",
    "PINCODE",
    "LATITUDE",
    "LONGITUDE",
    "CUSTOMER_SEGMENT",      # SALARIED / SELF_EMPLOYED / BUSINESS_OWNER / RETIRED / HOMEMAKER / STUDENT
    "BUREAU_SCORE",          # CIBIL score 300–900
    "FRAUD_FLAG",            # Y / N

    # ── Block 2: Loan / Account Details (from bank's CBS / LMS)
    "LOAN_ACCOUNT_NO",
    "PRODUCT_TYPE",          # PERSONAL / HOME / AUTO / BUSINESS / GOLD / CREDIT_CARD / EDUCATION / MICROFINANCE
    "LOAN_AMOUNT",           # Original sanctioned amount
    "DISBURSEMENT_DATE",
    "OUTSTANDING_PRINCIPAL",
    "OUTSTANDING_INTEREST",
    "PENAL_CHARGES",
    "TOTAL_OUTSTANDING_AMOUNT",
    "EMI_AMOUNT",
    "TENURE",                # Original tenure in months
    "INTEREST_RATE",         # Annual % rate
    "DPD",                   # Days Past Due (as of today)
    "BUCKET",                # CURRENT / BUCKET_1 / BUCKET_2 / BUCKET_3 / NPA
    "NPA_FLAG",              # Y / N
    "LAST_PAYMENT_DATE",
    "LAST_PAYMENT_AMOUNT",
    "LEGAL_STATUS",          # NONE / NOTICE_SENT / SARFAESI / SUIT_FILED / DRT / ARBITRATION
    "SETTLEMENT_STATUS",     # NONE / OFFERED / NEGOTIATING / ACCEPTED / REJECTED
    "RISK_SCORE",            # Bank's own probability-of-default score (0–100)

    # ── Block 3: Collection Assignment (from bank's collections system)
    "CASE_NUMBER",           # Bank's internal reference for this collection case
    "COLLECTION_STAGE",      # SOFT_CALL / FIELD / PRE_LEGAL / LEGAL / NPA_RECOVERY / WRITTEN_OFF_RECOVERY
    "PTP_DATE",              # Last PTP set in bank's system (if any)
    "PTP_AMOUNT",
    "PTP_STATUS",            # ACTIVE / HONORED / BROKEN / EXPIRED
    "AGENT_REMARKS",         # Notes from bank's own collection team or previous agency

    # ── Block 4: Disposition / Action for this feed (tells TIQCollect what to do)
    "BANK_ACTION",           # ACTIVE / PAID_DIRECT / RECALL / SETTLED / WRITTEN_OFF / DECEASED
    "RECALL_REASON",         # CUSTOMER_COMPLAINT / LEGAL_PROCEEDINGS / TRANSFERRED / COURT_ORDER / DECEASED
    "SETTLEMENT_AMOUNT",     # Only when BANK_ACTION = SETTLED (reduced agreed amount)
    "BANK_REMARK",           # Free-text note from bank to agency
]

# ── Reference data ────────────────────────────────────────────────────────────
CITIES = [
    ("Mumbai", "Maharashtra", 19.0760, 72.8777),
    ("Delhi", "Delhi", 28.6139, 77.2090),
    ("Bangalore", "Karnataka", 12.9716, 77.5946),
    ("Hyderabad", "Telangana", 17.3850, 78.4867),
    ("Chennai", "Tamil Nadu", 13.0827, 80.2707),
    ("Pune", "Maharashtra", 18.5204, 73.8567),
    ("Ahmedabad", "Gujarat", 23.0225, 72.5714),
    ("Jaipur", "Rajasthan", 26.9124, 75.7873),
    ("Lucknow", "Uttar Pradesh", 26.8467, 80.9462),
    ("Kolkata", "West Bengal", 22.5726, 88.3639),
    ("Surat", "Gujarat", 21.1702, 72.8311),
    ("Bhopal", "Madhya Pradesh", 23.2599, 77.4126),
    ("Nagpur", "Maharashtra", 21.1458, 79.0882),
    ("Patna", "Bihar", 25.5941, 85.1376),
    ("Coimbatore", "Tamil Nadu", 11.0168, 76.9558),
]

BANKS = [
    "State Bank of India", "HDFC Bank", "ICICI Bank", "Axis Bank",
    "Kotak Mahindra Bank", "IndusInd Bank", "Punjab National Bank",
    "Bank of Baroda", "Canara Bank", "Indian Bank", "Yes Bank",
    "IDFC First Bank", "Federal Bank", "RBL Bank",
]

PRODUCT_TYPES = ["PERSONAL", "HOME", "AUTO", "BUSINESS", "GOLD",
                 "CREDIT_CARD", "EDUCATION", "MICROFINANCE"]

# DPD distribution — NPA recovery pool only (31+ DPD)
# Three buckets: 30-60 (BUCKET_2), 61-90 (BUCKET_3), 90+ (NPA)
DPD_CHOICES = [35, 42, 50, 58, 65, 72, 80, 88, 95, 105, 120, 150, 180, 210, 270]
DPD_WEIGHTS  = [10, 10,  8,  7,  9,  9,  8,  7,  6,   6,   5,   5,   4,   4,   2]

CUSTOMER_SEGMENTS = [
    "SALARIED", "SALARIED", "SALARIED",         # ~45% salaried
    "SELF_EMPLOYED", "SELF_EMPLOYED",             # ~25% self-employed
    "BUSINESS_OWNER", "BUSINESS_OWNER",           # ~20% business
    "RETIRED",                                    # ~5% retired
    "HOMEMAKER",                                  # ~3% homemaker
    "STUDENT",                                    # ~2% student
]

PRODUCT_WEIGHTS = [30, 15, 20, 15, 5, 8, 4, 3]  # PERSONAL heaviest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _jitter(lat: float, lon: float, km: float = 12) -> tuple[float, float]:
    dx = random.uniform(-km, km) / 111
    dy = random.uniform(-km, km) / (111 * math.cos(math.radians(lat)))
    return round(lat + dx, 6), round(lon + dy, 6)


def _dpd_to_bucket(dpd: int) -> str:
    if dpd == 0:     return "CURRENT"
    if dpd <= 30:    return "BUCKET_1"
    if dpd <= 60:    return "BUCKET_2"
    if dpd <= 90:    return "BUCKET_3"
    return "NPA"


def _interest_rate_for_product(product: str) -> float:
    rates = {
        "HOME": (8.5, 11.0), "AUTO": (9.0, 13.0), "PERSONAL": (12.0, 24.0),
        "BUSINESS": (13.0, 22.0), "GOLD": (7.5, 12.0), "CREDIT_CARD": (30.0, 42.0),
        "EDUCATION": (8.0, 10.5), "MICROFINANCE": (18.0, 26.0),
    }
    lo, hi = rates.get(product, (12.0, 18.0))
    return round(random.uniform(lo, hi), 2)


def _sanction_for_product(product: str) -> float:
    ranges = {
        "HOME": (1000000, 8000000), "AUTO": (300000, 2000000),
        "PERSONAL": (50000, 500000), "BUSINESS": (200000, 5000000),
        "GOLD": (30000, 300000), "CREDIT_CARD": (50000, 500000),
        "EDUCATION": (100000, 2000000), "MICROFINANCE": (5000, 50000),
    }
    lo, hi = ranges.get(product, (100000, 500000))
    raw = random.uniform(lo, hi)
    return round(raw / 1000) * 1000  # round to nearest 1000


def _tenure_for_product(product: str) -> int:
    tenures = {
        "HOME": [120, 180, 240, 300], "AUTO": [36, 48, 60, 84],
        "PERSONAL": [12, 24, 36, 48, 60], "BUSINESS": [24, 36, 60, 84, 120],
        "GOLD": [6, 12, 24], "CREDIT_CARD": [12, 24, 36],
        "EDUCATION": [60, 84, 120, 180], "MICROFINANCE": [12, 18, 24],
    }
    return random.choice(tenures.get(product, [24, 36, 60]))


def _collection_stage(dpd: int, legal: str) -> str:
    if dpd <= 30:   return "SOFT_CALL"
    if dpd <= 60:   return "FIELD"
    if dpd <= 90:   return "FIELD"
    if legal != "NONE": return "LEGAL"
    if dpd <= 150:  return "PRE_LEGAL"
    return "NPA_RECOVERY"


def _legal_status(dpd: int) -> str:
    if dpd < 90:
        return "NONE"
    weights = {
        "NONE": 60, "NOTICE_SENT": 25, "SARFAESI": 8,
        "SUIT_FILED": 4, "DRT": 2, "ARBITRATION": 1,
    }
    return random.choices(list(weights.keys()), list(weights.values()))[0]


def _settlement_status(dpd: int) -> str:
    if dpd < 90:
        return "NONE"
    weights = {
        "NONE": 70, "OFFERED": 15, "NEGOTIATING": 8,
        "ACCEPTED": 4, "REJECTED": 3,
    }
    return random.choices(list(weights.keys()), list(weights.values()))[0]


def _bank_action_and_remark(dpd: int, settlement: str) -> tuple[str, str, str, str]:
    """
    Returns (BANK_ACTION, RECALL_REASON, SETTLEMENT_AMOUNT, BANK_REMARK).
    Distribution: ~88% ACTIVE, ~5% PAID_DIRECT, ~3% RECALL, ~2% SETTLED,
                  ~1% WRITTEN_OFF, ~1% DECEASED
    """
    roll = random.random()
    if roll < 0.88:
        return "ACTIVE", "", "", ""
    if roll < 0.93:
        return "PAID_DIRECT", "", "", f"Customer cleared overdue via {random.choice(['NEFT', 'UPI', 'IMPS', 'branch payment'])}."
    if roll < 0.96:
        reason = random.choice(["CUSTOMER_COMPLAINT", "LEGAL_PROCEEDINGS", "TRANSFERRED", "COURT_ORDER"])
        remark = {
            "CUSTOMER_COMPLAINT": "RBI ombudsman complaint filed. Cease all contact immediately.",
            "LEGAL_PROCEEDINGS":  "Matter sub-judice. Do not contact until court order lifted.",
            "TRANSFERRED":        "Account transferred to another agency. Stop all activity.",
            "COURT_ORDER":        "Court injunction received. No field visits permitted.",
        }[reason]
        return "RECALL", reason, "", remark
    if roll < 0.98:
        base = _sanction_for_product("PERSONAL") * random.uniform(0.3, 0.6)
        amt = round(base / 1000) * 1000
        return "SETTLED", "", str(amt), f"OTS approved at ₹{amt:,.0f}. Collect and close."
    if roll < 0.99:
        return "WRITTEN_OFF", "", "", "Loan written off. Attempt NPA recovery — any collection is a bonus."
    return "DECEASED", "", "", f"Death certificate submitted by family on {fake.date_between('-60d', 'today').strftime('%d-%b-%Y')}. Stop all visits."


# ── Row generator ─────────────────────────────────────────────────────────────

def generate_row(cust_seq: int, loan_seq: int) -> dict:
    today = date.today()

    # Customer
    city, state, city_lat, city_lon = random.choice(CITIES)
    lat, lon = _jitter(city_lat, city_lon)
    gender = random.choice(["MALE", "MALE", "FEMALE"])
    cibil = random.randint(300, 800)
    segment = random.choice(CUSTOMER_SEGMENTS)
    fraud = "Y" if random.random() < 0.02 else "N"

    # Loan
    product = random.choices(PRODUCT_TYPES, PRODUCT_WEIGHTS)[0]
    rate = _interest_rate_for_product(product)
    tenure = _tenure_for_product(product)
    sanctioned = _sanction_for_product(product)
    disbursed = sanctioned * random.uniform(0.92, 1.0)
    disbursement_dt = today - timedelta(days=random.randint(90, int(tenure * 30 * 0.8)))
    maturity_dt = disbursement_dt + timedelta(days=tenure * 30)

    # Monthly rate → EMI (reducing balance)
    r = rate / 1200
    emi = round((disbursed * r * (1 + r) ** tenure) / ((1 + r) ** tenure - 1), 2) if r > 0 else disbursed / tenure

    # DPD (higher for this portfolio since it's a collection file)
    dpd = random.choices(DPD_CHOICES, DPD_WEIGHTS)[0]
    bucket = _dpd_to_bucket(dpd)
    npa = "Y" if dpd >= 90 else "N"

    # Outstanding amounts
    months_elapsed = (today - disbursement_dt).days / 30
    repayment_pct = max(0.1, min(0.9, months_elapsed / tenure))
    outstanding_principal = round(disbursed * (1 - repayment_pct * random.uniform(0.6, 1.0)), 2)
    outstanding_interest = round(outstanding_principal * (rate / 100) * random.uniform(0.04, 0.25), 2)
    penal = round(random.uniform(0, dpd * 100), 2) if dpd > 0 else 0.0
    total_outstanding = round(outstanding_principal + outstanding_interest + penal, 2)
    overdue = round(emi * (dpd // 30 + 1) * random.uniform(0.8, 1.2), 2) if dpd > 0 else 0.0

    # Payment history
    last_pay_dt = (today - timedelta(days=dpd + random.randint(0, 15))).strftime("%Y-%m-%d") if dpd > 0 else (today - timedelta(days=random.randint(1, 28))).strftime("%Y-%m-%d")
    last_pay_amt = round(emi * random.uniform(0.5, 1.1), 2)

    # Legal / settlement
    legal = _legal_status(dpd)
    settlement = _settlement_status(dpd)

    # Bank risk score (NPA probability 0–100)
    bank_risk = round(min(100, dpd / 90 * 50 + (800 - cibil) / 500 * 50 + random.uniform(-5, 5)), 1)
    bank_risk = max(0, bank_risk)

    # Collection stage
    stage = _collection_stage(dpd, legal)

    # Prior PTP (25% of overdue accounts)
    has_ptp = (dpd > 0) and (random.random() < 0.25)
    ptp_date = (today - timedelta(days=random.randint(5, 45))).strftime("%Y-%m-%d") if has_ptp else ""
    ptp_amount = round(emi * random.uniform(1, 3), 2) if has_ptp else ""
    ptp_status = random.choice(["ACTIVE", "BROKEN", "HONORED", "EXPIRED"]) if has_ptp else ""

    # Agent remarks (30% of accounts)
    remarks = ""
    if random.random() < 0.30:
        remarks_pool = [
            "Customer requested callback after 10 AM.",
            "Neighbour informed — customer out of station.",
            "Promises to pay after salary credit.",
            "Premises locked on last 2 visits.",
            "Customer hostile — requested escalation to manager.",
            "Agreed to partial payment of ₹5,000 this month.",
            "Business closed temporarily — reopen next month.",
            "Family member (spouse) met — agrees to pay.",
            "Customer disputing interest charges.",
            f"Last contact on {fake.date_between('-30d', '-5d').strftime('%d %b')} — cooperative.",
        ]
        remarks = random.choice(remarks_pool)

    # Bank action
    bank_action, recall_reason, settlement_amount, bank_remark = _bank_action_and_remark(dpd, settlement)

    return {
        "CUSTOMER_ID":             f"CUST{cust_seq:06d}",
        "CUSTOMER_NAME":           fake.name(),
        "PHONE_NUMBER":            f"9{random.randint(100000000, 999999999):09d}",
        "EMAIL":                   fake.email() if random.random() > 0.45 else "",
        "ADDRESS":                 fake.street_address(),
        "CITY":                    city,
        "STATE":                   state,
        "PINCODE":                 str(random.randint(100000, 799999)),
        "LATITUDE":                lat,
        "LONGITUDE":               lon,
        "CUSTOMER_SEGMENT":        segment,
        "BUREAU_SCORE":            cibil,
        "FRAUD_FLAG":              fraud,

        "LOAN_ACCOUNT_NO":         f"LN{loan_seq:010d}",
        "PRODUCT_TYPE":            product,
        "LOAN_AMOUNT":             round(sanctioned, 2),
        "DISBURSEMENT_DATE":       disbursement_dt.strftime("%Y-%m-%d"),
        "OUTSTANDING_PRINCIPAL":   outstanding_principal,
        "OUTSTANDING_INTEREST":    outstanding_interest,
        "PENAL_CHARGES":           penal,
        "TOTAL_OUTSTANDING_AMOUNT": total_outstanding,
        "EMI_AMOUNT":              round(emi, 2),
        "TENURE":                  tenure,
        "INTEREST_RATE":           rate,
        "DPD":                     dpd,
        "BUCKET":                  bucket,
        "NPA_FLAG":                npa,
        "LAST_PAYMENT_DATE":       last_pay_dt,
        "LAST_PAYMENT_AMOUNT":     last_pay_amt,
        "LEGAL_STATUS":            legal,
        "SETTLEMENT_STATUS":       settlement,
        "RISK_SCORE":              bank_risk,

        "CASE_NUMBER":             f"BNK{loan_seq:010d}",
        "COLLECTION_STAGE":        stage,
        "PTP_DATE":                ptp_date,
        "PTP_AMOUNT":              ptp_amount,
        "PTP_STATUS":              ptp_status,
        "AGENT_REMARKS":           remarks,

        "BANK_ACTION":             bank_action,
        "RECALL_REASON":           recall_reason,
        "SETTLEMENT_AMOUNT":       settlement_amount,
        "BANK_REMARK":             bank_remark,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def generate(n_rows: int, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Start customer/loan sequences from high numbers so they don't clash with seed_data
    cust_base = random.randint(90001, 95000)
    loan_base = random.randint(2000000000, 2100000000)

    rows = []
    for i in range(n_rows):
        rows.append(generate_row(cust_base + i, loan_base + i))

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=BANK_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    # Summary stats
    actions = {}
    buckets = {}
    stages = {}
    for r in rows:
        actions[r["BANK_ACTION"]] = actions.get(r["BANK_ACTION"], 0) + 1
        buckets[r["BUCKET"]] = buckets.get(r["BUCKET"], 0) + 1
        stages[r["COLLECTION_STAGE"]] = stages.get(r["COLLECTION_STAGE"], 0) + 1

    print(f"\nGenerated {n_rows} rows  →  {output_path}")
    print(f"\n{len(BANK_COLUMNS)} columns across 4 blocks:")
    print("  Block 1 – Customer Identity   : CUSTOMER_ID … FRAUD_FLAG          (13 cols)")
    print("  Block 2 – Loan / Account      : LOAN_ACCOUNT_NO … RISK_SCORE       (15 cols)")
    print("  Block 3 – Collection History  : CASE_NUMBER … AGENT_REMARKS        (6 cols)")
    print("  Block 4 – Disposition         : BANK_ACTION … BANK_REMARK          (4 cols)")
    print("\nDPD Bucket distribution:")
    for k, v in sorted(buckets.items()):
        bar = "█" * (v * 30 // n_rows)
        print(f"  {k:<12} {v:>4} rows  {bar}")
    print("\nBank Action distribution:")
    for k, v in sorted(actions.items(), key=lambda x: -x[1]):
        print(f"  {k:<16} {v:>4} rows  ({v/n_rows*100:.1f}%)")
    print("\nCollection Stage distribution:")
    for k, v in sorted(stages.items(), key=lambda x: -x[1]):
        print(f"  {k:<24} {v:>4} rows")
    print(f"\nTo ingest into TIQCollect:")
    print(f"  python -m scripts.ingest_daily --file {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate dummy bank portfolio CSV")
    parser.add_argument("--rows", type=int, default=2, help="Number of loan accounts to generate (default: 2 for daily test; use --rows 150 for full daily batch)")
    parser.add_argument("--out", type=Path, default=None, help="Output CSV path (default: data/incoming/bank_portfolio_YYYYMMDD.csv)")
    args = parser.parse_args()

    if args.out:
        output_path = args.out
    else:
        today = date.today().strftime("%Y%m%d")
        output_path = Path(__file__).parent.parent / "data" / "incoming" / f"bank_portfolio_{today}.csv"

    generate(args.rows, output_path)


if __name__ == "__main__":
    main()
