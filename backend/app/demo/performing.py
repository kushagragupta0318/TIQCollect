# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-30 (lane L6, realism pack) — NEW. The bank's PERFORMING book.
#   The agency books come from the ledger simulator, a collections world whose
#   borrowers start mostly delinquent: at the anchor only 27-36% of each
#   generated book's live loans were CURRENT (measured by re-running every
#   agency's ledger), so the bank portal showed a lender whose whole book was
#   in collections. A real lender's book is mostly current, and only the
#   delinquent tail is placed with agencies.
#
#   These loans have never been past due: every EMI is paid on its due date,
#   so dpd is 0 on every day, overdue and penal are 0, and the balance is the
#   amortised principal. They have no case, placement, visit or payment row:
#   a bank's own EMI collection is not a collections payment, so every
#   collections view is unchanged. They do carry the same DPD-history dates as
#   the generated books (history_dates), so the portfolio views count them on
#   every day they count anything else.
#
#   Written after the agency books, from its own RNG stream, so no agency's
#   book changes. history rows carry source LEDGER, the generator's source
#   (no reader distinguishes it; fixtures/README.md says what it covers).
# ────────────────────────────────────────────────────────────────────────────
"""Generate a bank's performing (never-delinquent) loans into a v2 database
that already holds the bank's regions, branches and agency books."""
from __future__ import annotations

import calendar
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.demo import roster as R
from app.demo.books import INSTALMENT_WINDOW, borrower_detail, city_geography, history_dates
from app.demo.world import T, insert, jitter, region_key
from app.models.loan import dpd_bucket_for

#: Product terms for a PRIME book, an assumption of this generator and not a
#: restatement of the ledger's (whose borrowers are the collections tail):
#: share, median sanction (INR), lognormal sd, tenures (months), rate band (% p.a.).
#: CREDIT_CARD is left out: a revolving line has no EMI or tenure to amortise.
PRODUCTS = {
    "PERSONAL":     (0.30, 250_000, 0.60, (12, 24, 36, 48, 60), (11.0, 16.5)),
    "AUTO":         (0.18, 600_000, 0.45, (36, 48, 60, 84), (8.8, 11.5)),
    "GOLD":         (0.16, 120_000, 0.70, (6, 12), (9.0, 14.0)),
    "BUSINESS":     (0.12, 800_000, 0.65, (24, 36, 48, 60), (13.0, 18.0)),
    "MICROFINANCE": (0.12, 50_000, 0.30, (12, 18, 24), (20.0, 24.0)),
    "HOME":         (0.07, 2_800_000, 0.50, (120, 180, 240), (8.4, 9.8)),
    "EDUCATION":    (0.05, 700_000, 0.50, (60, 84, 120), (9.5, 12.0)),
}
SEGMENTS = (("SALARIED", 0.58), ("SELF_EMPLOYED", 0.27), ("BUSINESS_OWNER", 0.15))
#: Two-digit "book number" in customer refs, loan numbers and phones. The
#: agency books use their agency's number (1-10), so these never collide.
BOOK_NUMBER = {"GIRIVAN": 50, "KUMAON": 51}


@dataclass
class PerformingTruth:
    bank: str
    loans: int
    by_city: dict
    by_type: dict
    instalments: int
    history_rows: int
    history_from: str
    seed: int


def _add_months(d: date, k: int) -> date:
    m = d.month - 1 + k
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _emi(principal: float, rate_pa: float, n: int) -> float:
    r = rate_pa / 1200.0
    return principal * r * (1 + r) ** n / ((1 + r) ** n - 1)


def balance_after(principal: float, rate_pa: float, emi: float, k: int) -> float:
    """Principal outstanding after k EMIs paid on time (never below zero)."""
    r = rate_pa / 1200.0
    return max(0.0, principal * (1 + r) ** k - emi * ((1 + r) ** k - 1) / r)


def _allocate(total: int, weights: dict) -> dict:
    """Largest-remainder split of `total` by `weights` (deterministic)."""
    s = sum(weights.values())
    raw = {k: total * w / s for k, w in weights.items()}
    out = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: (-(raw[k] - out[k]), k))[:total - sum(out.values())]:
        out[k] += 1
    return out


def _city_codes(bank_key: str) -> list[str]:
    rows = R.KUMAON_REGIONS if bank_key == "KUMAON" else R.REGIONS + R.GIRIVAN_REGIONS_EXTRA
    return [c for (lv, c, *_x) in rows if lv == "CITY" and c in R.LOCALITIES and c in R.CITY_CULTURE]


def generate_performing(conn: Connection, *, bank_key: str, n_loans: int, seed: int,
                        history_from: date) -> PerformingTruth:
    """`n_loans` never-delinquent loans for `bank_key`, spread over the cities
    where the bank already has borrowers in proportion to them (with a per-city
    ratio drawn around 1, so delinquency rates differ by city as they do)."""
    bank = R.BANKS[bank_key]
    bank_id, n = bank["id"], BOOK_NUMBER[bank_key]
    rng = np.random.default_rng(seed)
    city_names, state_of_city = city_geography()
    code_of_name = {nm: c for c, nm in city_names.items()}
    cities = _city_codes(bank_key)

    cu, br = T["customers"], T["branches"]
    seen = Counter()
    for name, k in conn.execute(sa.select(cu.c.city, sa.func.count()).where(cu.c.bank_id == bank_id)
                                .group_by(cu.c.city)).all():
        code = code_of_name.get(name)
        if code in cities:
            seen[code] += int(k)
    if not seen:
        raise R.RosterError(f"{bank_key}: no borrowers in a roster city; generate the agency books first")
    region_city = {R.new_id("region", region_key(bank_key, c)): c for c in cities}
    branches: dict[str, list[str]] = {}
    for code, region_id in conn.execute(sa.select(br.c.branch_code, br.c.region_id)
                                        .where(br.c.bank_id == bank_id)).all():
        c = region_city.get(str(region_id))
        if c:
            branches.setdefault(c, []).append(code)
    ratio = {c: float(np.exp(rng.normal(0.0, 0.3))) for c in sorted(seen)}
    per_city = _allocate(n_loans, {c: seen[c] * ratio[c] for c in sorted(seen) if branches.get(c)})

    types = list(PRODUCTS)
    type_p = np.array([PRODUCTS[t][0] for t in types])
    type_p = type_p / type_p.sum()
    seg_names, seg_p = zip(*SEGMENTS)
    anchor = R.ANCHOR_DATE
    cust_rows, loan_rows, inst_rows, hist_rows = [], [], [], []
    lo_d, hi_d = INSTALMENT_WINDOW
    all_dates = history_dates(history_from)
    seq = 0
    for city in sorted(per_city):
        culture = R.CITY_CULTURE[city]
        pool = R.NAME_POOLS[culture]
        codes = sorted(branches[city])
        region_id = R.new_id("region", region_key(bank_key, city))
        for _ in range(per_city[city]):
            seq += 1
            lt = types[int(rng.choice(len(types), p=type_p))]
            _share, median, sd, tenures, (r_lo, r_hi) = PRODUCTS[lt]
            tenure = int(tenures[int(rng.integers(len(tenures)))])
            sanction = float(np.clip(round(median * float(np.exp(rng.normal(0, sd))) / 1000) * 1000,
                                     median / 6, median * 6))
            rate = round(float(rng.uniform(r_lo, r_hi)), 2)
            emi = round(_emi(sanction, rate, tenure), 2)
            # Months already repaid at the anchor: at least one, and the loan
            # still has at least two EMIs to go, so it is live at the anchor.
            paid_at_anchor = int(rng.integers(1, max(2, min(tenure - 2, 96)) + 1))
            opened = _add_months(anchor, -paid_at_anchor) - timedelta(days=int(rng.integers(0, 28)))
            dues = [_add_months(opened, i) for i in range(1, tenure + 1)]
            k_now = bisect_right(dues, anchor)          # EMIs paid by the end of the anchor, each on its due date
            principal = round(balance_after(sanction, rate, emi, k_now), 2)
            female = bool(rng.random() < 0.30)
            firsts = pool["female" if female else "male"]
            name = f"{firsts[int(rng.integers(len(firsts)))]} {pool['surnames'][int(rng.integers(len(pool['surnames'])))]}"
            loc = R.LOCALITIES[city][int(rng.integers(len(R.LOCALITIES[city])))]
            lat, lon = jitter(rng, loc[1], loc[2], 900)
            segment = (str(rng.choice(["SELF_EMPLOYED", "BUSINESS_OWNER"])) if lt == "BUSINESS"
                       else str(rng.choice(seg_names, p=seg_p)))
            evening = rng.random() < 0.25
            cust_id = R.new_id("customer", f"PERF:{bank_key}:{seq}")
            loan_id = R.new_id("loan", f"PERF:{bank_key}:{seq}")
            cust_rows.append(dict(
                id=cust_id, bank_id=bank_id, customer_ref=f"{bank['code'][:3]}{n:02d}{seq:07d}", full_name=name,
                date_of_birth=anchor - timedelta(days=int(rng.integers(23 * 365, 62 * 365))),
                gender="FEMALE" if female else "MALE",
                pan_masked=f"XXXXX{int(rng.integers(1000, 9999))}{chr(65 + int(rng.integers(26)))}",
                aadhaar_masked=f"XXXXXXXX{int(rng.integers(1000, 9999))}",
                phone_primary=f"{7 + seq % 3}{n:02d}{seq:07d}",
                address_line1=f"{int(rng.integers(1, 480))}, {loc[0]}", city=city_names[city],
                state=state_of_city[city], pincode=loc[3], latitude=lat, longitude=lon,
                cibil_score=(None if rng.random() < 0.04 else int(np.clip(round(rng.normal(768, 34)), 680, 890))),
                preferred_contact_start=(17 if evening else 9), preferred_contact_end=(19 if evening else 18),
                language_preference=culture, customer_segment=segment, is_hostile=False,
                requires_female_agent=False, do_not_contact=bool(rng.random() < 0.003), fraud_flag=False,
                complaints_raised=0, tags=[],
                **borrower_detail(rng, name, f"6{n:02d}{seq:07d}", email_share=0.62)))
            last_paid = dues[k_now - 1]
            loan_rows.append(dict(
                id=loan_id, bank_id=bank_id, customer_id=cust_id,
                loan_account_number=f"{codes[seq % len(codes)]}{n:02d}{seq:06d}", loan_type=lt,
                branch_code=codes[seq % len(codes)], sanctioned_amount=sanction, disbursed_amount=sanction,
                outstanding_principal=principal, total_outstanding=principal, overdue_amount=0.0, emi_amount=emi,
                disbursement_date=opened, maturity_date=dues[-1], last_payment_date=last_paid,
                last_payment_amount=emi, next_due_date=dues[k_now], dpd=0, dpd_as_of=anchor,
                dpd_bucket=dpd_bucket_for(0).value, status="ACTIVE", interest_rate=rate, penal_charges=0.0,
                tenure_months=tenure, npa_flag=False, npa_since=None))
            inst_rows += [dict(id=R.new_id("instalment", f"PERF:{bank_key}:{seq}:{i}"), bank_id=bank_id,
                               loan_id=loan_id, instalment_no=i, due_date=due, amount_due=emi, source="GENERATED")
                          for i, due in enumerate(dues, start=1) if lo_d <= due <= hi_d]
            # Loan-major: this loan's rows together (see books.py on pg_dump).
            for d in all_dates:
                if d < opened:
                    continue
                bal = round(balance_after(sanction, rate, emi, bisect_right(dues, d)), 2)
                hist_rows.append(dict(
                    loan_id=loan_id, as_of_date=d, bank_id=bank_id, dpd=0, dpd_bucket=dpd_bucket_for(0).value,
                    loan_status="ACTIVE", overdue_amount=0.0, total_outstanding=bal, outstanding_principal=bal,
                    penal_charges=0.0, npa_flag=False, loan_type=lt, region_id=region_id, agency_id=None,
                    placement_id=None, is_month_end=(d + timedelta(days=1)).day == 1,
                    source="LEDGER", is_backfill=False, observed_pit=True))
    for table, rows in (("customers", cust_rows), ("loans", loan_rows), ("loan_instalments", inst_rows),
                        ("loan_dpd_history", hist_rows)):
        insert(conn, table, rows)
    return PerformingTruth(
        bank=bank_key, loans=len(loan_rows), by_city=dict(sorted(per_city.items())),
        by_type=dict(sorted(Counter(r["loan_type"] for r in loan_rows).items())),
        instalments=len(inst_rows), history_rows=len(hist_rows), history_from=history_from.isoformat(), seed=seed)

