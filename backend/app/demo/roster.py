# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-28 (B16, d4) — NEW. The ONE roster file (owner: "all roster names in
#   one place, so a rename is a one-line change"). Every demo name lives here:
#   banks, agencies, their identities, people, domains, contracts, commission
#   slabs, coverage, regions, cities, localities and the name pools the
#   generator draws agents and borrowers from.
#
#   B15's constants moved here VERBATIM from scripts/migrate_v1_to_v2.py
#   (BANK, AGENCY, CONTRACT, COMMISSION, REGIONS, BANK_USERS, V1_STAFF,
#   MASTER_ACCOUNTS, the two domains, new_id, email_for); the transform now
#   imports them. Every B15 id is uuid5(NAMESPACE_TIQ_V2, "<kind>:<key>") on the
#   same keys, so the transformed book is unchanged
#   (tests/test_demo_roster.py pins a sample of ids and every B15 value).
#
#   NOT here, on purpose: each agency's LATENT quality (Appendix C.4). It is
#   generator-only ground truth (app/demo/latent.py) and never reaches a
#   product table.
#
#   Everything below is FICTIONAL. Names were chosen to sound like Indian
#   firms without being any real bank, NBFC or collection agency (DATA-MODEL-V2
#   Appendix C, screened 2026-09-24; web and MCA searches only, formal clearance
#   is legal's step). Every e-mail domain is .test. Phone numbers are invented
#   and outbound SMS / WhatsApp / e-mail is suppressed for demo tenants, so none
#   is ever contacted.
#
#   Aravalli's GSTIN (06AAECA4172K1Z3, from B15) does not carry the standard
#   mod-36 check character (it would be "O"). Kept byte-identical, because
#   B15's rows are the fixture's exact history; every NEW agency's GSTIN is
#   built by gstin() below and does carry a valid one.
# ────────────────────────────────────────────────────────────────────────────
"""The demo roster: the only place a demo tenant name is written."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

#: Namespace for every deterministic demo id (B15's, unchanged).
NAMESPACE_TIQ_V2 = uuid.UUID("5f0c1b8e-2f3a-4d7e-9b61-0a4f7c2e9d11")

#: The day the demo book stands at. The v1 book (and so Aravalli's history)
#: is a snapshot anchored on 2026-09-22; generated books end the same day.
ANCHOR_DATE = date(2026, 9, 22)


class RosterError(ValueError):
    pass


def plus_years(d: date, years: int) -> date:
    """The same calendar day `years` later; 29 February becomes the 28th."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def new_id(kind: str, key: str) -> str:
    """Deterministic id for a row v1 did not have (§9.2): two runs agree."""
    return str(uuid.uuid5(NAMESPACE_TIQ_V2, f"{kind}:{key}"))


def email_for(full_name: str, domain: str) -> str:
    """<first>.<last>@domain (Appendix C.5), lower case ASCII."""
    parts = [p for p in full_name.strip().lower().replace(".", " ").split() if p.isalpha()]
    if not parts:
        raise RosterError(f"cannot derive an email from {full_name!r}")
    local = parts[0] if len(parts) == 1 else f"{parts[0]}.{parts[-1]}"
    return f"{local}@{domain}"


_GST_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin(state_code: str, pan: str, entity: str = "1") -> str:
    """A GSTIN in the real format, with the real mod-36 check character:
    2-digit state code + PAN + entity number + 'Z' + check."""
    body = f"{state_code}{pan}{entity}Z"
    if len(body) != 14:
        raise RosterError(f"bad GSTIN body {body!r}")
    total = 0
    for i, ch in enumerate(body):
        p = _GST_ALPHABET.index(ch) * (1 if i % 2 == 0 else 2)
        total += p // 36 + p % 36
    return body + _GST_ALPHABET[(36 - total % 36) % 36]


# ════════════════════════════════════════════════════════════════════════════
# Girivan Finance Ltd and Aravalli Field Services — B15, moved verbatim
# ════════════════════════════════════════════════════════════════════════════
BANK = dict(id=new_id("bank", "GIRIVAN"), code="GIRIVAN", legal_name="Girivan Finance Ltd",
            display_name="Girivan Finance", timezone="Asia/Kolkata", status="ACTIVE", is_demo=True,
            brand={"upi_payee_name": "Girivan Finance Ltd", "sms_sender_id": "GIRIVN"})
AGENCY = dict(id=new_id("agency", "ARAVALLI"), bank_id=BANK["id"], code="AGY-ARAVALLI",
              legal_name="Aravalli Field Services Pvt. Ltd.", trade_name="Aravalli Field Services",
              entity_type="PVT_LTD", cin="U74999HR2019PTC082417", pan="AAECA4172K", gstin="06AAECA4172K1Z3",
              registered_address={"line1": "Plot 88, Udyog Vihar Phase IV", "city": "Gurugram",
                                  "state": "Haryana", "pincode": "122015"},
              hq_city="Gurugram", website="https://aravallifs.test",
              contacts=[{"role": "Director", "name": "Rajiv Bhandari", "phone": "+919810460211",
                         "email": "rajiv.bhandari@aravallifs.test"},
                        {"role": "Operations Head", "name": "Meera Khanna", "phone": "+919810460212",
                         "email": "meera.khanna@aravallifs.test"},
                        {"role": "Compliance Officer", "name": "Tarun Sethi", "phone": "+919810460213",
                         "email": "tarun.sethi@aravallifs.test"}],
              contact_name="Meera Khanna", contact_email="meera.khanna@aravallifs.test",
              contact_phone="9810460212", status="ACTIVE",
              activated_at=datetime(2025, 11, 3, 4, 30, tzinfo=timezone.utc), is_demo=True)
AGENCY_DOMAIN = "aravallifs.test"
BANK_DOMAIN = "girivanfinance.test"
BANK_USERS = [  # Appendix C.5
    ("ananya.iyer", "Ananya Iyer", "BANK_ADMIN", "9820031101"),
    ("rohan.mehta", "Rohan Mehta", "BANK_ANALYST", "9820031102"),
    ("farah.siddiqui", "Farah Siddiqui", "BANK_TECHOPS", "9820031103"),
    # A second BANK_ADMIN (2b, 2026-09-29): the placement engine's apply step
    # is four-eyes (applied_by must differ from created_by, ADR 0010), so a
    # bank with one admin cannot demo apply end to end. The owner said yes to
    # a 4th master login (2026-09-30), so she is MASTER_ACCOUNTS' last slot.
    ("kavya.reddy", "Kavya Reddy", "BANK_ADMIN", "9820031104"),
]
# v1's non-agent users. Deviation from Appendix C.5, announced to the
# coordinator: manager1 (Vikram Malhotra, 15 agents) stays AGENCY_MANAGER —
# the owner's master login needs an agency MANAGER and his is the showcase
# team — and v1's "System Admin" becomes Aravalli's Operations Head, the
# agency's AGENCY_ADMIN (C.3's pattern), rather than Vikram.
V1_STAFF = {
    "admin@tiqcollect.in": ("meera.khanna", "Meera Khanna", "AGENCY_ADMIN"),
    "manager1@tiqcollect.in": ("vikram.malhotra", "Vikram Malhotra", "AGENCY_MANAGER"),
    "manager2@tiqcollect.in": ("sunita.kapoor", "Sunita Kapoor", "AGENCY_MANAGER"),
}
# Order matters: apply_demo_logins.REQUIRED_ROLE_GROUPS is positional.
MASTER_ACCOUNTS = (f"ananya.iyer@{BANK_DOMAIN}", f"vikram.malhotra@{AGENCY_DOMAIN}",
                   f"piyush.sharma@{AGENCY_DOMAIN}", f"kavya.reddy@{BANK_DOMAIN}",
                   # 5th slot (2026-10-06): Meera Khanna, Aravalli's AGENCY_ADMIN, so the
                   # agency-admin tier (and the reversal agency-approve) is demoable.
                   f"meera.khanna@{AGENCY_DOMAIN}")

# Aravalli's workforce (B16). GENDER IS ROSTER DATA, stated per person by the
# roster's author — never derived from a name, anywhere (coordinator,
# 2026-09-28). v1 recorded no gender at all (ALLOC-G: 926 decisions BLOCKED on
# "needs a female agent"); these are the 18 v1 agents, by their v1 names.
ARAVALLI_V1_AGENT_GENDER = {
    "Rajesh Kumar Yadav": "MALE", "Mohammed Zafar Khan": "MALE", "Karan Rawat Singh": "MALE",
    "Deepak Narayan Joshi": "MALE", "Devraj Anand Kapoor": "MALE", "Pankaj Kumar Sinha": "MALE",
    "Anil Kumar Mishra": "MALE", "Arjun Singh Chauhan": "MALE", "Rohit Anand Saxena": "MALE",
    "Rahul Dev Pandey": "MALE", "Sunil Kumar Sharma": "MALE", "Akash Ratan Verma": "MALE",
    "Mohan Lal Nair": "MALE", "Sanjay Mohan Gupta": "MALE", "Nitesh Gupta Agarwal": "MALE",
    "Suresh Chand Tiwari": "MALE", "Vivek Prasad Dubey": "MALE", "Piyush Sharma": "MALE",
}
# Three women who joined Aravalli in September 2026 (coordinator, option (b)):
# NEW agents with no history, so the v1 book stays exact while the next plan
# has a female agent for the cases that require one. The 926 historical
# BLOCKED decisions stay as recorded.
# (name, gender, joined_on, city, locality index, manager, employee code, id card, phone)
ARAVALLI_NEW_AGENTS = (
    ("Shreya Chaudhary", "FEMALE", date(2026, 9, 8), "GURUGRAM", 0, "Vikram Malhotra", "EMP0101", "TIQID00101",
     "9810460301"),
    ("Pooja Rawat", "FEMALE", date(2026, 9, 14), "DELHI", 1, "Sunita Kapoor", "EMP0102", "TIQID00102",
     "9810460302"),
    ("Kiran Bhatia", "FEMALE", date(2026, 9, 15), "NOIDA", 1, "Vikram Malhotra", "EMP0103", "TIQID00103",
     "9810460303"),
)
CONTRACT = dict(id=new_id("contract", "ARAVALLI-2025"), bank_id=BANK["id"], agency_id=AGENCY["id"],
                contract_no="GFL/AGY/2025/0017", start_date=date(2025, 11, 3), end_date=date(2027, 11, 2),
                status="ACTIVE", max_placed_cases=2500, max_agents=25, sla_first_visit_days=5,
                recall_no_activity_days=75, recall_on_sla_breach=False, recall_at_contract_end=True,
                performance_bonus_pct=Decimal("1.000"), performance_target_pct=Decimal("85.000"),
                security_deposit=Decimal("1500000.00"))
# Appendix C.3's Aravalli slab, by bucket.
COMMISSION = {"CURRENT": "3.500", "BUCKET_1": "3.500", "BUCKET_2": "6.000", "BUCKET_3": "9.000", "NPA": "13.000"}
# Zone North → region NCR → states → cities (design §9.2 step 1).
REGIONS = [
    ("ZONE", "NORTH", "North", None, None, None),
    ("REGION", "NCR", "National Capital Region", "NORTH", 28.61, 77.21),
    ("STATE", "HR", "Haryana", "NCR", 29.06, 76.09),
    ("STATE", "DL", "Delhi", "NCR", 28.70, 77.10),
    ("STATE", "UP", "Uttar Pradesh", "NCR", 26.85, 80.95),
    ("CITY", "GURUGRAM", "Gurugram", "HR", 28.46, 77.03),
    ("CITY", "DELHI", "Delhi", "DL", 28.64, 77.22),
    ("CITY", "NOIDA", "Noida", "UP", 28.54, 77.39),
]


# ════════════════════════════════════════════════════════════════════════════
# The rest of Girivan's geography (B16): four zones, 21 cities
# ════════════════════════════════════════════════════════════════════════════
# Appended after B15's rows, same tuple shape. A second state row for central
# Uttar Pradesh exists because (bank, level, code) is unique and B15 placed
# state UP under region NCR: putting Lucknow there would make "everything
# under NCR" include Lucknow.
GIRIVAN_REGIONS_EXTRA = [
    ("CITY", "GHAZIABAD", "Ghaziabad", "UP", 28.67, 77.45),
    ("REGION", "RAJ", "Rajasthan", "NORTH", 26.92, 75.79),
    ("STATE", "RJ", "Rajasthan", "RAJ", 26.92, 75.79),
    ("CITY", "JAIPUR", "Jaipur", "RJ", 26.91, 75.79),
    ("CITY", "AJMER", "Ajmer", "RJ", 26.45, 74.64),
    ("REGION", "AWADH", "Central Uttar Pradesh", "NORTH", 26.85, 80.95),
    ("STATE", "UP-C", "Uttar Pradesh (Central)", "AWADH", 26.85, 80.95),
    ("CITY", "LUCKNOW", "Lucknow", "UP-C", 26.85, 80.95),
    ("CITY", "KANPUR", "Kanpur", "UP-C", 26.45, 80.33),
    ("ZONE", "WEST", "West", None, None, None),
    ("REGION", "MAHA", "Maharashtra", "WEST", 19.08, 72.88),
    ("STATE", "MH", "Maharashtra", "MAHA", 19.75, 75.71),
    ("CITY", "MUMBAI", "Mumbai", "MH", 19.08, 72.88),
    ("CITY", "THANE", "Thane", "MH", 19.22, 72.98),
    ("CITY", "NAVI_MUMBAI", "Navi Mumbai", "MH", 19.03, 73.03),
    ("CITY", "PUNE", "Pune", "MH", 18.52, 73.86),
    ("REGION", "GUJ", "Gujarat", "WEST", 23.02, 72.57),
    ("STATE", "GJ", "Gujarat", "GUJ", 22.26, 71.19),
    ("CITY", "AHMEDABAD", "Ahmedabad", "GJ", 23.02, 72.57),
    ("CITY", "SURAT", "Surat", "GJ", 21.17, 72.83),
    ("CITY", "VADODARA", "Vadodara", "GJ", 22.31, 73.18),
    ("ZONE", "SOUTH", "South", None, None, None),
    ("REGION", "TGKA", "Telangana and Karnataka", "SOUTH", 15.32, 77.94),
    ("STATE", "TG", "Telangana", "TGKA", 18.11, 79.02),
    ("STATE", "KA", "Karnataka", "TGKA", 15.32, 75.71),
    ("CITY", "HYDERABAD", "Hyderabad", "TG", 17.39, 78.49),
    ("CITY", "BENGALURU", "Bengaluru", "KA", 12.97, 77.59),
    ("REGION", "TNR", "Tamil Nadu", "SOUTH", 11.13, 78.66),
    ("STATE", "TN", "Tamil Nadu", "TNR", 11.13, 78.66),
    ("CITY", "CHENNAI", "Chennai", "TN", 13.08, 80.27),
    ("CITY", "COIMBATORE", "Coimbatore", "TN", 11.02, 76.96),
    ("ZONE", "EAST", "East", None, None, None),
    ("REGION", "BGOD", "Bengal and Odisha", "EAST", 21.50, 86.90),
    ("STATE", "WB", "West Bengal", "BGOD", 22.99, 87.85),
    ("STATE", "OD", "Odisha", "BGOD", 20.95, 85.10),
    ("CITY", "KOLKATA", "Kolkata", "WB", 22.57, 88.36),
    ("CITY", "BHUBANESWAR", "Bhubaneswar", "OD", 20.30, 85.82),
]

#: The language a city's borrowers mostly prefer, and the name pool its
#: people are drawn from.
CITY_CULTURE = {
    "GURUGRAM": "HINDI", "DELHI": "HINDI", "NOIDA": "HINDI", "GHAZIABAD": "HINDI",
    "JAIPUR": "HINDI", "AJMER": "HINDI", "LUCKNOW": "HINDI", "KANPUR": "HINDI",
    "MUMBAI": "MARATHI", "THANE": "MARATHI", "NAVI_MUMBAI": "MARATHI", "PUNE": "MARATHI",
    "AHMEDABAD": "GUJARATI", "SURAT": "GUJARATI", "VADODARA": "GUJARATI",
    "HYDERABAD": "TELUGU", "BENGALURU": "KANNADA",
    "CHENNAI": "TAMIL", "COIMBATORE": "TAMIL",
    "KOLKATA": "BENGALI", "BHUBANESWAR": "ODIA",
}

#: Real localities, fictional everything else: (locality, lat, lon, pincode).
#: Borrower addresses and agent bases are jittered around these.
LOCALITIES = {
    "GURUGRAM": [("DLF Phase 3", 28.494, 77.093, "122002"), ("Sohna Road", 28.414, 77.042, "122018"),
                 ("Palam Vihar", 28.506, 77.032, "122017"), ("Sector 56", 28.424, 77.101, "122011")],
    "DELHI": [("Laxmi Nagar", 28.630, 77.277, "110092"), ("Janakpuri", 28.621, 77.082, "110058"),
              ("Rohini Sector 7", 28.708, 77.114, "110085"), ("Mayur Vihar Phase 1", 28.604, 77.294, "110091"),
              ("Shahdara", 28.673, 77.289, "110032")],
    "NOIDA": [("Sector 18", 28.570, 77.321, "201301"), ("Sector 62", 28.627, 77.372, "201309"),
              ("Sector 50", 28.572, 77.362, "201303"), ("Greater Noida West", 28.604, 77.438, "201318")],
    "GHAZIABAD": [("Indirapuram", 28.641, 77.371, "201014"), ("Raj Nagar Extension", 28.699, 77.425, "201017"),
                  ("Vaishali", 28.645, 77.339, "201010"), ("Kavi Nagar", 28.672, 77.445, "201002")],
    "JAIPUR": [("Malviya Nagar", 26.853, 75.805, "302017"), ("Vaishali Nagar", 26.912, 75.743, "302021"),
               ("Mansarovar", 26.866, 75.761, "302020"), ("Raja Park", 26.894, 75.826, "302004"),
               ("Jhotwara", 26.945, 75.742, "302012")],
    "AJMER": [("Adarsh Nagar", 26.432, 74.656, "305008"), ("Civil Lines", 26.469, 74.644, "305001"),
              ("Madar", 26.482, 74.673, "305007")],
    "LUCKNOW": [("Gomti Nagar", 26.850, 81.000, "226010"), ("Aliganj", 26.890, 80.941, "226024"),
                ("Indira Nagar", 26.883, 80.998, "226016"), ("Alambagh", 26.815, 80.902, "226005")],
    "KANPUR": [("Kidwai Nagar", 26.432, 80.319, "208011"), ("Swaroop Nagar", 26.483, 80.314, "208002"),
               ("Kakadeo", 26.482, 80.293, "208025"), ("Govind Nagar", 26.442, 80.297, "208006")],
    "MUMBAI": [("Andheri East", 19.115, 72.869, "400069"), ("Kurla West", 19.073, 72.880, "400070"),
               ("Borivali West", 19.231, 72.848, "400092"), ("Ghatkopar East", 19.086, 72.908, "400077"),
               ("Malad West", 19.187, 72.848, "400064")],
    "THANE": [("Majiwada", 19.230, 72.982, "400601"), ("Kopri", 19.195, 72.978, "400603"),
              ("Ghodbunder Road", 19.260, 72.967, "400615")],
    "NAVI_MUMBAI": [("Vashi", 19.077, 72.999, "400703"), ("Nerul", 19.033, 73.019, "400706"),
                    ("Kharghar", 19.047, 73.070, "410210"), ("Airoli", 19.157, 72.998, "400708")],
    "PUNE": [("Hadapsar", 18.508, 73.926, "411028"), ("Kothrud", 18.507, 73.807, "411038"),
             ("Pimpri", 18.627, 73.800, "411018"), ("Wakad", 18.599, 73.763, "411057"),
             ("Kondhwa", 18.477, 73.892, "411048")],
    "AHMEDABAD": [("Maninagar", 22.996, 72.600, "380008"), ("Navrangpura", 23.037, 72.560, "380009"),
                  ("Bopal", 23.034, 72.463, "380058"), ("Naroda", 23.068, 72.653, "382330")],
    "SURAT": [("Adajan", 21.196, 72.793, "395009"), ("Varachha", 21.212, 72.867, "395006"),
              ("Udhna", 21.170, 72.843, "394210"), ("Vesu", 21.141, 72.772, "395007")],
    "VADODARA": [("Alkapuri", 22.310, 73.169, "390007"), ("Gotri", 22.316, 73.138, "390021"),
                 ("Manjalpur", 22.270, 73.190, "390011"), ("Karelibaug", 22.322, 73.207, "390018")],
    "HYDERABAD": [("Kukatpally", 17.485, 78.411, "500072"), ("Dilsukhnagar", 17.369, 78.526, "500060"),
                  ("Ameerpet", 17.437, 78.448, "500016"), ("Mehdipatnam", 17.395, 78.431, "500028"),
                  ("LB Nagar", 17.347, 78.552, "500074")],
    "BENGALURU": [("Jayanagar", 12.925, 77.583, "560041"), ("Whitefield", 12.970, 77.750, "560066"),
                  ("Rajajinagar", 12.991, 77.554, "560010"), ("BTM Layout", 12.917, 77.610, "560076"),
                  ("Yelahanka", 13.100, 77.596, "560064")],
    "CHENNAI": [("T. Nagar", 13.042, 80.234, "600017"), ("Velachery", 12.975, 80.221, "600042"),
                ("Anna Nagar", 13.085, 80.210, "600040"), ("Tambaram", 12.925, 80.127, "600045"),
                ("Perambur", 13.117, 80.233, "600011")],
    "COIMBATORE": [("RS Puram", 11.008, 76.951, "641002"), ("Gandhipuram", 11.017, 76.968, "641012"),
                   ("Peelamedu", 11.029, 77.012, "641004"), ("Saibaba Colony", 11.024, 76.941, "641011")],
    "KOLKATA": [("Salt Lake Sector V", 22.580, 88.418, "700091"), ("Behala", 22.498, 88.310, "700034"),
                ("Dum Dum", 22.620, 88.420, "700028"), ("Garia", 22.463, 88.391, "700084")],
    "BHUBANESWAR": [("Saheed Nagar", 20.287, 85.845, "751007"), ("Patia", 20.353, 85.818, "751024"),
                    ("Nayapalli", 20.294, 85.810, "751012"), ("Khandagiri", 20.259, 85.779, "751030")],
}

#: Girivan branches outside NCR (B15 made NCR's from the v1 loans' codes, in
#: v1's GGN044 format; these follow it). code -> (city, name).
GIRIVAN_BRANCHES_EXTRA = {
    # B15's 494 NCR branches all resolve to Gurugram (v1's codes are GGN* /
    # BR*); Delhi and Noida get branches of their own.
    "DEL014": ("DELHI", "Delhi Laxmi Nagar"), "DEL022": ("DELHI", "Delhi Janakpuri"),
    "NOI008": ("NOIDA", "Noida Sector 18"), "NOI015": ("NOIDA", "Noida Sector 62"),
    "GZB021": ("GHAZIABAD", "Ghaziabad Indirapuram"),
    "JPR011": ("JAIPUR", "Jaipur Malviya Nagar"), "JPR017": ("JAIPUR", "Jaipur Vaishali Nagar"),
    "AJM004": ("AJMER", "Ajmer Civil Lines"),
    "LKO007": ("LUCKNOW", "Lucknow Hazratganj"), "LKO012": ("LUCKNOW", "Lucknow Gomti Nagar"),
    "KNP005": ("KANPUR", "Kanpur Mall Road"),
    "MUM021": ("MUMBAI", "Mumbai Andheri East"), "MUM034": ("MUMBAI", "Mumbai Ghatkopar"),
    "THN008": ("THANE", "Thane Majiwada"), "NMB006": ("NAVI_MUMBAI", "Navi Mumbai Vashi"),
    "PUN015": ("PUNE", "Pune Kothrud"), "PUN019": ("PUNE", "Pune Hadapsar"),
    "AMD013": ("AHMEDABAD", "Ahmedabad Navrangpura"), "SRT006": ("SURAT", "Surat Adajan"),
    "VDR004": ("VADODARA", "Vadodara Alkapuri"),
    "HYD018": ("HYDERABAD", "Hyderabad Ameerpet"), "HYD024": ("HYDERABAD", "Hyderabad Kukatpally"),
    "BLR022": ("BENGALURU", "Bengaluru Jayanagar"), "BLR027": ("BENGALURU", "Bengaluru Whitefield"),
    "CHN014": ("CHENNAI", "Chennai T. Nagar"), "CBE005": ("COIMBATORE", "Coimbatore RS Puram"),
    "KOL016": ("KOLKATA", "Kolkata Park Street"), "BBS006": ("BHUBANESWAR", "Bhubaneswar Saheed Nagar"),
}


# ════════════════════════════════════════════════════════════════════════════
# Kumaon Finance Ltd — the second, small tenant (plan §4.7: isolation only)
# ════════════════════════════════════════════════════════════════════════════
KUMAON_DOMAIN = "kumaonfinance.test"
KUMAON_BANK = dict(id=new_id("bank", "KUMAON"), code="KUMAON", legal_name="Kumaon Finance Ltd",
                   display_name="Kumaon Finance", timezone="Asia/Kolkata", status="ACTIVE", is_demo=True,
                   brand={"upi_payee_name": "Kumaon Finance Ltd", "sms_sender_id": "KUMFIN"})
KUMAON_BANK_USERS = [
    ("deepika.rawat", "Deepika Rawat", "BANK_ADMIN", "9822107401"),
    ("sameer.pande", "Sameer Pande", "BANK_ANALYST", "9822107402"),
]
KUMAON_REGIONS = [
    ("ZONE", "WEST", "West", None, None, None),
    ("REGION", "PUNE-MET", "Pune Metropolitan", "WEST", 18.52, 73.86),
    ("STATE", "MH", "Maharashtra", "PUNE-MET", 19.75, 75.71),
    ("CITY", "PUNE", "Pune", "MH", 18.52, 73.86),
]
KUMAON_BRANCHES = {"KFL-PUN01": ("PUNE", "Pune Baner"), "KFL-PUN02": ("PUNE", "Pune Pimpri")}


# ════════════════════════════════════════════════════════════════════════════
# The agencies (Appendix C.2 / C.3)
# ════════════════════════════════════════════════════════════════════════════
LOAN_TYPES = ("HOME", "AUTO", "PERSONAL", "BUSINESS", "GOLD", "CREDIT_CARD", "EDUCATION", "MICROFINANCE")
DPD_BUCKETS = ("CURRENT", "BUCKET_1", "BUCKET_2", "BUCKET_3", "NPA")


@dataclass(frozen=True)
class AgencyRoster:
    """One agency, every invented detail. `row` is its tenancy.agencies row."""
    key: str
    bank_key: str                     # "GIRIVAN" | "KUMAON"
    row: dict
    domain: str
    zone: str
    serves: tuple                     # city codes
    n_agents: int
    onboarded: date | None            # contract start / activation; None while onboarding
    contract: dict | None
    commission: dict                  # bucket -> pct (str, 3 dp)
    products: tuple = LOAN_TYPES
    buckets: tuple = DPD_BUCKETS
    agency_region: str = ""           # the region code its agency_regions row names
    invite_sent: date | None = None   # onboarding: when the admin invite went out
    missing_docs: tuple = ()          # doc types never uploaded (onboarding)
    expiring_docs: dict = field(default_factory=dict)   # doc type -> days until expiry at ANCHOR_DATE
    managers: tuple = ()              # agency managers' names (AGENCY_MANAGER)

    @property
    def id(self) -> str:
        return self.row["id"]

    @property
    def bank_id(self) -> str:
        return self.row["bank_id"]

    @property
    def people(self) -> list[dict]:
        return self.row["contacts"]

    @property
    def ops_head(self) -> dict:
        return next(p for p in self.people if p["role"] == "Operations Head")


def _agency(key: str, bank: dict, *, code: str, legal: str, trade: str, entity: str, cin: str, pan: str,
            state_code: str, address: dict, hq: str, domain: str, people: list[tuple], status: str,
            activated: datetime | None, suspended: tuple | None = None) -> dict:
    contacts = [{"role": role, "name": name, "phone": f"+91{phone}", "email": email_for(name, domain)}
                for role, name, phone in people]
    ops = next(c for c in contacts if c["role"] == "Operations Head")
    row = dict(id=new_id("agency", key), bank_id=bank["id"], code=code, legal_name=legal, trade_name=trade,
               entity_type=entity, cin=cin, pan=pan, gstin=gstin(state_code, pan), registered_address=address,
               hq_city=hq, website=f"https://{domain}", contacts=contacts, contact_name=ops["name"],
               contact_email=ops["email"], contact_phone=ops["phone"][3:], status=status,
               activated_at=activated, is_demo=True)
    if suspended:
        row.update(suspended_at=suspended[0], suspended_reason=suspended[1])
    return row


def _contract(key: str, bank: dict, *, no: str, start: date, end: date, cases: int, seats: int, sla: int,
              recall: int, deposit: str, on_breach: bool = False, status: str = "ACTIVE") -> dict:
    return dict(id=new_id("contract", key), bank_id=bank["id"], agency_id=new_id("agency", key.split("-")[0]),
                contract_no=no, start_date=start, end_date=end, status=status, max_placed_cases=cases,
                max_agents=seats, sla_first_visit_days=sla, recall_no_activity_days=recall,
                recall_on_sla_breach=on_breach, recall_at_contract_end=True,
                performance_bonus_pct=Decimal("1.000"), performance_target_pct=Decimal("85.000"),
                security_deposit=Decimal(deposit))


def _at(d: date, hh: int = 4, mm: int = 30) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone.utc)


def _slab(c, b1, b2, b3, npa) -> dict:
    return {"CURRENT": c, "BUCKET_1": b1, "BUCKET_2": b2, "BUCKET_3": b3, "NPA": npa}


ARAVALLI = AgencyRoster(
    key="ARAVALLI", bank_key="GIRIVAN", row=AGENCY, domain=AGENCY_DOMAIN, zone="NORTH",
    # 18 v1 agents + the 3 who joined in September 2026 (ARAVALLI_NEW_AGENTS).
    serves=("GURUGRAM", "DELHI", "NOIDA"), n_agents=18 + len(ARAVALLI_NEW_AGENTS), onboarded=date(2025, 11, 3),
    contract=CONTRACT,
    commission=COMMISSION, agency_region="NCR", managers=("Vikram Malhotra", "Sunita Kapoor"))

SARTHAK = AgencyRoster(
    key="SARTHAK", bank_key="GIRIVAN", domain="sarthakrecovery.test", zone="NORTH",
    serves=("NOIDA", "GHAZIABAD", "DELHI"), n_agents=22, onboarded=date(2026, 1, 12), agency_region="NCR",
    row=_agency("SARTHAK", BANK, code="AGY-SARTHAK", legal="Sarthak Recovery Services LLP",
                trade="Sarthak Recovery Services", entity="LLP", cin="AAQ-7314", pan="AAYFS5821H",
                state_code="09", hq="Noida", domain="sarthakrecovery.test",
                address={"line1": "4th Floor, Sarthak Towers, Plot A-27, Sector 63", "city": "Noida",
                         "state": "Uttar Pradesh", "pincode": "201301"},
                people=[("Director", "Alok Tyagi", "9811274401"),
                        ("Operations Head", "Nidhi Chaudhary", "9811274402"),
                        ("Compliance Officer", "Harish Bhati", "9811274403")],
                status="ACTIVE", activated=_at(date(2026, 1, 12))),
    contract=_contract("SARTHAK-2026", BANK, no="GFL/AGY/2026/0021", start=date(2026, 1, 12),
                       end=date(2027, 7, 11), cases=3000, seats=28, sla=5, recall=75, deposit="1200000.00"),
    commission=_slab("3.000", "3.250", "5.500", "8.500", "12.500"),
    products=("PERSONAL", "AUTO", "CREDIT_CARD", "BUSINESS", "MICROFINANCE", "GOLD"),
    managers=("Rakesh Nagar", "Shalini Tomar"))

RAJPUTANA = AgencyRoster(
    key="RAJPUTANA", bank_key="GIRIVAN", domain="rajputanacredit.test", zone="NORTH",
    serves=("JAIPUR", "AJMER"), n_agents=16, onboarded=date(2026, 2, 2), agency_region="RAJ",
    row=_agency("RAJPUTANA", BANK, code="AGY-RAJPUTANA", legal="Rajputana Credit Solutions Pvt. Ltd.",
                trade="Rajputana Credit Solutions", entity="PVT_LTD", cin="U74999RJ2021PTC074318",
                pan="AAHCR6620D", state_code="08", hq="Jaipur", domain="rajputanacredit.test",
                address={"line1": "Suite 302, Chitrakoot Business Park, Ajmer Road", "city": "Jaipur",
                         "state": "Rajasthan", "pincode": "302021"},
                people=[("Director", "Mahendra Shekhawat", "9829163301"),
                        ("Operations Head", "Kavita Rathore", "9829163302"),
                        ("Compliance Officer", "Devendra Joshi", "9829163303")],
                status="ACTIVE", activated=_at(date(2026, 2, 2))),
    contract=_contract("RAJPUTANA-2026", BANK, no="GFL/AGY/2026/0024", start=date(2026, 2, 2),
                       end=date(2027, 2, 1), cases=2000, seats=20, sla=6, recall=90, deposit="800000.00"),
    commission=_slab("3.750", "4.000", "6.500", "9.500", "14.000"),
    products=("PERSONAL", "AUTO", "GOLD", "BUSINESS", "MICROFINANCE"),
    managers=("Surendra Choudhary", "Anjali Meena"))

# The suspension figures are MEASURED on the generated book (demo profile,
# seed 20260922; re-measured 2026-09-30 after L6 added calendar seasonality,
# which moves WHEN a loan is visited and so reshuffles the fence-gaming draw:
# Awadh 29.8% of 352 August visits outside the fence; Girivan's other
# agencies ~4% (Aravalli's re-derived flags included, Kumaon's Almora not).
# Was 33.7% of 315 / 4.6% before seasonality (2026-09-28).
# tests/pg/test_pg_demo_fixture.py re-measures them on the committed dump,
# so the text cannot drift from the data.
AWADH = AgencyRoster(
    key="AWADH", bank_key="GIRIVAN", domain="awadhfield.test", zone="NORTH",
    serves=("LUCKNOW", "KANPUR"), n_agents=14, onboarded=date(2026, 2, 20), agency_region="AWADH",
    row=_agency("AWADH", BANK, code="AGY-AWADH", legal="Awadh Field Collections Pvt. Ltd.",
                trade="Awadh Field Collections", entity="PVT_LTD", cin="U74900UP2020PTC131047",
                pan="AAKCA2958Q", state_code="09", hq="Lucknow", domain="awadhfield.test",
                address={"line1": "2nd Floor, Gomti Arcade, Vibhuti Khand, Gomti Nagar", "city": "Lucknow",
                         "state": "Uttar Pradesh", "pincode": "226010"},
                people=[("Director", "Faisal Rizvi", "9839205501"),
                        ("Operations Head", "Anand Srivastava", "9839205502"),
                        ("Compliance Officer", "Pooja Awasthi", "9839205503")],
                status="SUSPENDED", activated=_at(date(2026, 2, 20)),
                suspended=(_at(date(2026, 9, 2), 11, 15),
                           "Geofence-failure spike under review: 30% of August visits recorded outside "
                           "the 100 m fence against 4% across Girivan's other agencies. Placements paused "
                           "pending the evidence audit.")),
    contract=_contract("AWADH-2026", BANK, no="GFL/AGY/2026/0026", start=date(2026, 2, 20),
                       end=date(2027, 2, 19), cases=1800, seats=18, sla=7, recall=60, deposit="500000.00",
                       on_breach=True),
    commission=_slab("3.500", "3.750", "6.250", "9.000", "13.500"),
    products=("PERSONAL", "MICROFINANCE", "GOLD", "AUTO"),
    managers=("Rajesh Tiwari", "Shabnam Ansari"))

SAHYADRI = AgencyRoster(
    key="SAHYADRI", bank_key="GIRIVAN", domain="sahyadrifield.test", zone="WEST",
    serves=("MUMBAI", "THANE", "NAVI_MUMBAI", "PUNE"), n_agents=28, onboarded=date(2025, 12, 8),
    agency_region="MAHA",
    row=_agency("SAHYADRI", BANK, code="AGY-SAHYADRI", legal="Sahyadri Field Recovery Pvt. Ltd.",
                trade="Sahyadri Field Recovery", entity="PVT_LTD", cin="U74999MH2017PTC296512",
                pan="AAWCS8834L", state_code="27", hq="Mumbai", domain="sahyadrifield.test",
                address={"line1": "Unit 1104, Nilgiri Business Bay, LBS Marg, Vikhroli West", "city": "Mumbai",
                         "state": "Maharashtra", "pincode": "400083"},
                people=[("Director", "Sanjay Deshmukh", "9820774101"),
                        ("Operations Head", "Priya Kulkarni", "9820774102"),
                        ("Compliance Officer", "Nitin Gokhale", "9820774103")],
                status="ACTIVE", activated=_at(date(2025, 12, 8))),
    contract=_contract("SAHYADRI-2025", BANK, no="GFL/AGY/2025/0019", start=date(2025, 12, 8),
                       end=date(2027, 12, 7), cases=4000, seats=35, sla=4, recall=75, deposit="2500000.00"),
    commission=_slab("3.250", "3.500", "5.750", "8.750", "13.000"),
    expiring_docs={"INSURANCE": 21},
    managers=("Amol Patil", "Sneha Jadhav", "Vivek Pawar"))

SABARMATI = AgencyRoster(
    key="SABARMATI", bank_key="GIRIVAN", domain="sabarmaticollect.test", zone="WEST",
    serves=("AHMEDABAD", "SURAT", "VADODARA"), n_agents=17, onboarded=date(2026, 3, 16), agency_region="GUJ",
    row=_agency("SABARMATI", BANK, code="AGY-SABARMATI", legal="Sabarmati Collection Services LLP",
                trade="Sabarmati Collection Services", entity="LLP", cin="AAT-2296", pan="ACJFS4107B",
                state_code="24", hq="Ahmedabad", domain="sabarmaticollect.test",
                address={"line1": "B-512, Shivalay Corporate Square, Ashram Road", "city": "Ahmedabad",
                         "state": "Gujarat", "pincode": "380009"},
                people=[("Director", "Hitesh Patel", "9825318801"),
                        ("Operations Head", "Mitali Shah", "9825318802"),
                        ("Compliance Officer", "Jignesh Desai", "9825318803")],
                status="ACTIVE", activated=_at(date(2026, 3, 16))),
    contract=_contract("SABARMATI-2026", BANK, no="GFL/AGY/2026/0031", start=date(2026, 3, 16),
                       end=date(2027, 3, 15), cases=1800, seats=20, sla=3, recall=60, deposit="700000.00"),
    commission=_slab("3.000", "3.000", "5.000", "8.000", "12.000"),
    products=("PERSONAL", "BUSINESS", "CREDIT_CARD", "GOLD", "AUTO"),
    managers=("Bhavesh Modi", "Hetal Trivedi"))

DECCAN = AgencyRoster(
    key="DECCAN", bank_key="GIRIVAN", domain="deccanresolve.test", zone="SOUTH",
    serves=("HYDERABAD", "BENGALURU"), n_agents=24, onboarded=date(2026, 1, 26), agency_region="TGKA",
    row=_agency("DECCAN", BANK, code="AGY-DECCAN", legal="Deccan Resolve Associates Pvt. Ltd.",
                trade="Deccan Resolve Associates", entity="PVT_LTD", cin="U74999TG2018PTC124583",
                pan="AAGCD7351P", state_code="36", hq="Hyderabad", domain="deccanresolve.test",
                address={"line1": "Level 6, Nizam Heights, Road No. 36, Jubilee Hills", "city": "Hyderabad",
                         "state": "Telangana", "pincode": "500033"},
                people=[("Director", "Srinivas Reddy", "9849027601"),
                        ("Operations Head", "Swathi Rao", "9849027602"),
                        ("Compliance Officer", "Mohammed Imran", "9849027603")],
                status="ACTIVE", activated=_at(date(2026, 1, 26))),
    contract=_contract("DECCAN-2026", BANK, no="GFL/AGY/2026/0023", start=date(2026, 1, 26),
                       end=date(2027, 7, 25), cases=3200, seats=30, sla=5, recall=90, deposit="1500000.00"),
    commission=_slab("3.500", "3.500", "6.000", "9.250", "13.500"),
    managers=("Venkatesh Naidu", "Kavya Hegde", "Prakash Gowda"))

COROMANDEL = AgencyRoster(
    key="COROMANDEL", bank_key="GIRIVAN", domain="coromandelrp.test", zone="SOUTH",
    serves=("CHENNAI", "COIMBATORE"), n_agents=15, onboarded=date(2026, 4, 6), agency_region="TNR",
    row=_agency("COROMANDEL", BANK, code="AGY-COROMANDEL", legal="Coromandel Recovery Partners LLP",
                trade="Coromandel Recovery Partners", entity="LLP", cin="AAU-5308", pan="ACMFC9216E",
                state_code="33", hq="Chennai", domain="coromandelrp.test",
                address={"line1": "No. 18, Marina View Chambers, Cathedral Road", "city": "Chennai",
                         "state": "Tamil Nadu", "pincode": "600086"},
                people=[("Director", "Ramesh Subramanian", "9840513901"),
                        ("Operations Head", "Divya Krishnan", "9840513902"),
                        ("Compliance Officer", "Karthik Raman", "9840513903")],
                status="ACTIVE", activated=_at(date(2026, 4, 6))),
    contract=_contract("COROMANDEL-2026", BANK, no="GFL/AGY/2026/0034", start=date(2026, 4, 6),
                       end=date(2027, 4, 5), cases=1600, seats=18, sla=5, recall=75, deposit="600000.00"),
    commission=_slab("3.250", "3.500", "6.000", "9.000", "13.000"),
    products=("PERSONAL", "AUTO", "HOME", "GOLD", "EDUCATION", "CREDIT_CARD"),
    managers=("Senthil Kumar", "Meenakshi Sundaram"))

HOOGHLY = AgencyRoster(
    key="HOOGHLY", bank_key="GIRIVAN", domain="hooghlycredit.test", zone="EAST",
    serves=("KOLKATA", "BHUBANESWAR"), n_agents=0, onboarded=None, agency_region="BGOD",
    row=_agency("HOOGHLY", BANK, code="AGY-HOOGHLY", legal="Hooghly Credit Management Pvt. Ltd.",
                trade="Hooghly Credit Management", entity="PVT_LTD", cin="U74140WB2022PTC254909",
                pan="AAJCH3470N", state_code="19", hq="Kolkata", domain="hooghlycredit.test",
                address={"line1": "7th Floor, Ganga Kutir Chambers, Camac Street", "city": "Kolkata",
                         "state": "West Bengal", "pincode": "700016"},
                people=[("Director", "Arindam Bose", "9830648201"),
                        ("Operations Head", "Sreeja Mukherjee", "9830648202"),
                        ("Compliance Officer", "Debashis Ghosh", "9830648203")],
                status="PENDING", activated=None),
    contract=_contract("HOOGHLY-2026", BANK, no="GFL/AGY/2026/0041", start=date(2026, 10, 1),
                       end=date(2027, 9, 30), cases=2000, seats=22, sla=6, recall=75, deposit="1000000.00",
                       status="DRAFT"),
    commission=_slab("3.500", "3.750", "6.250", "9.250", "13.750"),
    invite_sent=date(2026, 9, 18), missing_docs=("INSURANCE", "DRA_REGISTER"))

KUMAON_AGENCY_DOMAIN = "almorarecovery.test"
ALMORA = AgencyRoster(
    key="ALMORA", bank_key="KUMAON", domain=KUMAON_AGENCY_DOMAIN, zone="WEST",
    serves=("PUNE",), n_agents=8, onboarded=date(2026, 3, 2), agency_region="PUNE-MET",
    row=_agency("ALMORA", KUMAON_BANK, code="AGY-ALMORA", legal="Almora Recovery Desk LLP",
                trade="Almora Recovery Desk", entity="LLP", cin="AAV-1187", pan="ACQFA6093G",
                state_code="27", hq="Pune", domain=KUMAON_AGENCY_DOMAIN,
                address={"line1": "Office 204, Pinecrest Plaza, Baner Road", "city": "Pune",
                         "state": "Maharashtra", "pincode": "411045"},
                people=[("Director", "Harish Pant", "9822651101"),
                        ("Operations Head", "Gunjan Negi", "9822651102"),
                        ("Compliance Officer", "Rahul Kandpal", "9822651103")],
                status="ACTIVE", activated=_at(date(2026, 3, 2))),
    contract=_contract("ALMORA-2026", KUMAON_BANK, no="KFL/AGY/2026/0003", start=date(2026, 3, 2),
                       end=date(2027, 3, 1), cases=2500, seats=10, sla=5, recall=75, deposit="500000.00"),
    commission=_slab("3.000", "3.500", "6.000", "9.000", "12.500"),
    products=("PERSONAL", "GOLD", "MICROFINANCE", "AUTO"),
    managers=("Tanuja Bhandari",))

#: Every agency, Appendix C.2's order, then Kumaon's.
AGENCIES = (ARAVALLI, SARTHAK, RAJPUTANA, AWADH, SAHYADRI, SABARMATI, DECCAN, COROMANDEL, HOOGHLY, ALMORA)
#: The ones the generator books (Aravalli's book is v1's, via the B15 transform).
GENERATED_AGENCIES = tuple(a for a in AGENCIES if a.key != "ARAVALLI")

BANKS = {"GIRIVAN": BANK, "KUMAON": KUMAON_BANK}
BANK_DOMAINS = {"GIRIVAN": BANK_DOMAIN, "KUMAON": KUMAON_DOMAIN}

#: Every e-mail domain a demo account can be on: the guard list for
#: DEMO_MASTER_DISABLE_OTHERS (config.DEMO_EMAIL_DOMAINS derives from it).
DEMO_EMAIL_DOMAINS = (BANK_DOMAIN, KUMAON_DOMAIN) + tuple(a.domain for a in AGENCIES)

#: The agency documents Appendix C.3 lists, as tenancy DOC_TYPES, with a
#: realistic validity (years; None = does not expire) and specimen title.
AGENCY_DOCUMENTS = (
    ("INCORPORATION_CERT", None, "Certificate of Incorporation"),
    ("GST", None, "GST Registration Certificate (Form GST REG-06)"),
    ("PAN", None, "Permanent Account Number card"),
    ("AGREEMENT", None, "Master Service Agreement (signed)"),
    ("INSURANCE", 1, "Professional Indemnity Insurance policy"),
    ("POLICE_VERIFICATION_POLICY", 2, "Police Verification Policy for field staff"),
    ("DRA_REGISTER", 1, "DRA certification register"),
)
SPECIMEN_FOOTER = "Specimen — fictional demo document"


# ════════════════════════════════════════════════════════════════════════════
# Name pools (en_IN, by culture). Agents, managers and borrowers are drawn
# from these, seeded, so the book is identical on every run.
# ════════════════════════════════════════════════════════════════════════════
NAME_POOLS = {
    "HINDI": dict(
        male=("Amit", "Rahul", "Sandeep", "Deepak", "Manoj", "Rajesh", "Sunil", "Vikas", "Ankit", "Pankaj",
              "Gaurav", "Saurabh", "Ravi", "Ajay", "Naveen", "Mukesh", "Arun", "Yogesh", "Rohit", "Sachin",
              "Vinod", "Hemant", "Ashish", "Neeraj"),
        female=("Neha", "Pooja", "Priyanka", "Anjali", "Sonia", "Ritu", "Kavita", "Sunita", "Shalini", "Preeti",
                "Nisha", "Swati", "Rekha", "Mamta", "Divya", "Komal", "Pallavi", "Jyoti", "Meenu", "Sapna"),
        surnames=("Sharma", "Verma", "Gupta", "Singh", "Yadav", "Chauhan", "Mishra", "Pandey", "Tiwari", "Saxena",
                  "Agarwal", "Rawat", "Tyagi", "Bhardwaj", "Jain", "Srivastava", "Rathore", "Meena", "Kumar",
                  "Dubey", "Tomar", "Sisodia", "Bansal", "Malik")),
    "MARATHI": dict(
        male=("Sachin", "Rahul", "Amol", "Nilesh", "Prashant", "Sagar", "Vijay", "Mahesh", "Tushar", "Ganesh",
              "Santosh", "Swapnil", "Omkar", "Akshay", "Rohan", "Kiran", "Sameer", "Yogesh", "Aniket", "Pravin"),
        female=("Sneha", "Pooja", "Snehal", "Priya", "Rutuja", "Aishwarya", "Manisha", "Pradnya", "Shweta",
                "Ashwini", "Madhuri", "Vaishali", "Sayali", "Gauri", "Komal", "Neha"),
        surnames=("Patil", "Deshmukh", "Kulkarni", "Jadhav", "Pawar", "Shinde", "More", "Gaikwad", "Chavan",
                  "Joshi", "Deshpande", "Bhosale", "Kadam", "Salunkhe", "Gokhale", "Sawant", "Mane", "Naik")),
    "GUJARATI": dict(
        male=("Hitesh", "Jignesh", "Bhavesh", "Nirav", "Chirag", "Ketan", "Mehul", "Paresh", "Hardik", "Kunal",
              "Darshan", "Rakesh", "Vipul", "Tejas", "Parth", "Dhaval"),
        female=("Hetal", "Mitali", "Nirali", "Krupa", "Payal", "Bhavna", "Jinal", "Komal", "Riddhi", "Dhara",
                "Foram", "Khushbu", "Pooja", "Heena"),
        surnames=("Patel", "Shah", "Desai", "Mehta", "Trivedi", "Modi", "Parikh", "Joshi", "Chauhan", "Bhatt",
                  "Vyas", "Pandya", "Rana", "Solanki", "Thakkar", "Gohil")),
    "TELUGU": dict(
        male=("Srinivas", "Venkatesh", "Ravi", "Suresh", "Naresh", "Kiran", "Praveen", "Mahesh", "Ramesh",
              "Sai", "Harish", "Chaitanya", "Vamsi", "Krishna", "Anil", "Raju"),
        female=("Swathi", "Lakshmi", "Sravani", "Divya", "Anusha", "Keerthi", "Bhavani", "Madhavi", "Sirisha",
                "Pavani", "Haritha", "Sowmya", "Ramya", "Padma"),
        surnames=("Reddy", "Rao", "Naidu", "Chowdary", "Goud", "Varma", "Murthy", "Prasad", "Yadav", "Raju",
                  "Kumar", "Sharma", "Babu", "Achari")),
    "KANNADA": dict(
        male=("Prakash", "Manjunath", "Raghavendra", "Nagaraj", "Suresh", "Mahesh", "Vinay", "Karthik",
              "Shivakumar", "Girish", "Harsha", "Chetan", "Pradeep", "Santosh"),
        female=("Kavya", "Deepa", "Shruthi", "Pavithra", "Rashmi", "Asha", "Sahana", "Vidya", "Bhavya",
                "Chaitra", "Nandini", "Spoorthi"),
        surnames=("Gowda", "Hegde", "Shetty", "Rao", "Kumar", "Murthy", "Naik", "Patil", "Bhat", "Kamath",
                  "Shenoy", "Prasad")),
    "TAMIL": dict(
        male=("Senthil", "Karthik", "Suresh", "Ramesh", "Murugan", "Balaji", "Vignesh", "Arun", "Prabhu",
              "Saravanan", "Dinesh", "Gopal", "Rajkumar", "Manikandan", "Ashok", "Vijay"),
        female=("Divya", "Meenakshi", "Priya", "Lakshmi", "Kavitha", "Revathi", "Sangeetha", "Deepika",
                "Gayathri", "Anitha", "Nithya", "Saranya", "Malathi", "Janani"),
        surnames=("Kumar", "Raman", "Krishnan", "Subramanian", "Sundaram", "Rajan", "Natarajan", "Srinivasan",
                  "Venkatesan", "Pillai", "Murugesan", "Balasubramanian", "Iyer", "Chandran")),
    "BENGALI": dict(
        male=("Arindam", "Debashis", "Sourav", "Subhajit", "Anirban", "Partha", "Sandip", "Tanmoy", "Abhijit",
              "Rajib", "Sayan", "Indranil"),
        female=("Sreeja", "Moumita", "Payel", "Rituparna", "Sudeshna", "Tanushree", "Ananya", "Priyanka",
                "Suchitra", "Debjani"),
        surnames=("Bose", "Mukherjee", "Ghosh", "Chatterjee", "Banerjee", "Das", "Sen", "Dutta", "Roy",
                  "Saha", "Mondal", "Chakraborty")),
    "ODIA": dict(
        male=("Subrat", "Pradeep", "Sanjay", "Bijay", "Ashok", "Manas", "Debasis", "Soumya"),
        female=("Sasmita", "Lipika", "Pragyan", "Sonali", "Itishree", "Madhusmita"),
        surnames=("Mohanty", "Panda", "Sahoo", "Das", "Mishra", "Nayak", "Pradhan", "Behera", "Patnaik",
                  "Swain")),
}
