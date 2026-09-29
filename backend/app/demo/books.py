# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-28 (B16, d4) — NEW. One agency's collections book, generated as
#   EVENTS by the ledger simulator and written into the product schema with
#   every derived column DERIVED:
#     - loan state at the anchor (dpd, overdue, penal, principal, last
#       payment) from materialise.loan_state_at, the rewind's own arithmetic;
#     - payment and promise status from payment_status_at / ptp_status_at;
#     - promises through ledger.product_rules.product_promises, i.e.
#       models/ptp.promise_is_for_money (a zero promise cannot exist);
#     - within_contact_hours and geo_verified from core.geo, the two calls
#       visit_service makes, on the time and coordinates the event carries.
#       An attempt outside the window is refused as the product refuses it:
#       an audit row, never a visit.
#   The ledger itself is untouched. Latent agency quality enters through a
#   subclass that overrides _make_agents with the same number of draws, and
#   through the ledger's existing visit / call hazards (app/demo/latent.py).
#   Nothing here names a model metric.
#
#   Placement: the bank places a loan with the agency at the first weekly
#   scan on which it is past due (and its product and bucket are authorised
#   by the contract). The agency's activity on a loan is the ledger's events
#   inside [placed_on, end); payments outside that window are the bank's own
#   (they move the loan's state, not the agency's ledger).
# ────────────────────────────────────────────────────────────────────────────
"""Generate and write one agency's book (customers → loans → placements →
cases → visits / calls / promises / payments → offers / disputes)."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd
from sqlalchemy.engine import Connection

from app.core.config import settings
from app.core.geo import is_within_contact_hours, within_geo_fence
from app.demo import roster as R
from app.demo.latent import (AGENCY_LATENT, COMPLAINT_RATE, COMPLAINT_RATE_BREACHING, CONTACT_HOUR,
                             SETTLEMENT_DECLINE_BEFORE_ACCEPT, AgencyLatent)
from app.demo.world import IST, AgencyWorld, insert, jitter
from app.ml.simulation.ledger.config import LedgerConfig
from app.ml.simulation.ledger.materialise import loan_state_at, payment_status_at, ptp_status_at
from app.ml.simulation.ledger.product_rules import product_promises
from app.ml.simulation.ledger.simulator import LedgerSimulator
from app.models.audit_log import AuditAction
from app.models.case import ClosureReason as CR
from app.models.customer import CUSTOMER_TAG_DECEASED
from app.models.loan import dpd_bucket_for

UTC = timezone.utc
#: The ledger simulator's death EVENT name (not the customer tag, not a closure reason).
SIM_EVENT_DECEASED = "DECEASED"


#: The fixture keeps a loan's schedule only for instalments due within six
#: months of the anchor, either side (coordinator, 2026-09-28: the full
#: schedules were the largest table in a dump capped at 60 MB). A missing
#: instalment OUTSIDE this window is not an absent debt; readers must not
#: treat it as one (backend/fixtures/README.md).
INSTALMENT_WINDOW = (date(2026, 3, 22), date(2027, 3, 22))


def history_dates(start: date) -> list[date]:
    """loan_dpd_history dates for a generated book: every calendar month-end
    from the book's start, every day of the anchor's month up to the anchor
    (the analytics MV is daily in the current month), and the anchor itself."""
    out, d = set(), start
    while d <= R.ANCHOR_DATE:
        if (d + timedelta(days=1)).day == 1:
            out.add(d)
        if (d.year, d.month) == (R.ANCHOR_DATE.year, R.ANCHOR_DATE.month):
            out.add(d)
        d += timedelta(days=1)
    return sorted(out)


class DemoLedgerSimulator(LedgerSimulator):
    """The ledger with an agency's latent skill. Same draws, same order, as
    LedgerSimulator._make_agents — only the normal's mean and spread differ —
    so everything downstream of the agent table is the ledger's own logic."""

    def __init__(self, cfg: LedgerConfig, latent: AgencyLatent):
        super().__init__(cfg)
        self.latent = latent

    def _make_agents(self) -> pd.DataFrame:
        c, rng = self.cfg, self.rng
        n = c.n_agents
        return pd.DataFrame({
            "agent_id": [f"AG{i:03d}" for i in range(n)],
            "agent_skill": np.clip(rng.normal(self.latent.skill_mean, self.latent.skill_sd, n), -0.9, 0.9),
            "joined_day": rng.integers(-720, 1, n),
        })


@dataclass
class BookTruth:
    """What the manifest records about one agency's generated book."""
    agency: str
    ledger_fingerprint: str
    seed: int
    agent_skill: dict = field(default_factory=dict)          # employee code -> latent skill
    agent_gender: dict = field(default_factory=dict)
    injected: dict = field(default_factory=lambda: defaultdict(list))   # breach kind -> ids
    counts: Counter = field(default_factory=Counter)
    rates: dict = field(default_factory=dict)


def _hour(rng, kind: str) -> float:
    mu, sd = CONTACT_HOUR[kind]
    lo, hi = settings.CONTACT_HOUR_START + 0.05, settings.CONTACT_HOUR_END - 0.05
    return float(np.clip(rng.normal(mu, sd), lo, hi))


def _moment(start: date, day: int, hour: float) -> datetime:
    d = start + timedelta(days=int(day))
    h = int(hour)
    m = int((hour - h) * 60)
    return datetime.combine(d, time(h, m), tzinfo=IST).astimezone(UTC)


def _offset(lat: float, lon: float, metres: float, bearing: float) -> tuple[float, float]:
    return (lat + metres * math.cos(bearing) / 111_320,
            lon + metres * math.sin(bearing) / (111_320 * math.cos(math.radians(lat))))


def require_rbi_window() -> None:
    """The flags are derived through core.geo, which reads the configured
    window. A demo box running 0-24 would derive "always in hours" and the
    injected refusals would be impossible; refuse to generate there."""
    if (settings.CONTACT_HOUR_START, settings.CONTACT_HOUR_END) != (8, 19):
        raise RuntimeError(f"generate with CONTACT_HOUR_START=8 / CONTACT_HOUR_END=19 (RBI); got "
                           f"{settings.CONTACT_HOUR_START}-{settings.CONTACT_HOUR_END}")


def generate_book(conn: Connection, w: AgencyWorld, *, slots_per_agent: float, seed: int,
                  bank_admin_id: str) -> BookTruth:
    require_rbi_window()
    a, latent = w.roster, AGENCY_LATENT[w.roster.key]
    start = a.onboarded
    anchor_day = (R.ANCHOR_DATE - start).days            # events on day < anchor_day + 1 are in
    end_day = anchor_day + 1
    suspended_day = ((a.row["suspended_at"].astimezone(IST).date() - start).days
                     if a.row.get("suspended_at") else None)
    base = LedgerConfig()
    cfg = replace(base, n_borrowers=max(60, int(round(len(w.agents) * slots_per_agent))),
                  months=anchor_day // base.cycle_days + 1, n_agents=len(w.agents), seed=seed, start_date=start,
                  visit_hazard_current=base.visit_hazard_current * latent.visit_rate,
                  visit_hazard_delinquent=base.visit_hazard_delinquent * latent.visit_rate,
                  call_hazard_current=base.call_hazard_current * latent.call_rate,
                  call_hazard_delinquent=base.call_hazard_delinquent * latent.call_rate,
                  observe_disposition=True, observe_declines=True, observe_call_duration=True,
                  observe_verbal_commitments=True)
    led = DemoLedgerSimulator(cfg, latent).run()
    rng = np.random.default_rng(seed + 1)
    truth = BookTruth(agency=a.key, ledger_fingerprint=cfg.fingerprint(), seed=seed)
    for ag, sk in zip(w.agents, led.agents.agent_skill):
        truth.agent_skill[ag.name] = round(float(sk), 4)
        truth.agent_gender[ag.name] = ag.gender
    n = w.number
    bank_id, agency_id = a.bank_id, a.id
    ten = dict(bank_id=bank_id, agency_id=agency_id)

    def lid(kind: str, key) -> str:
        return R.new_id(kind, f"{a.key}:{key}")

    # ── placement: the first weekly scan on which the loan is past due ──────
    # The ledger runs past the anchor; a loan that originates after it is not
    # in the book yet. `origination_day` is the ledger's ONE origination fact
    # (the materialiser's too); `opened_day` is the slot's last registration,
    # which put 653 loans' disbursement after the anchor in the first build.
    loans = led.loans[led.loans.origination_day < end_day].copy()
    keep = set(loans.loan_id)
    authorised = {lt for lt in a.products}
    placed_on: dict[str, int] = {}
    npa_since: dict[str, int] = {}
    place_state: dict[str, tuple] = {}
    last_scan = min(end_day, suspended_day) if suspended_day is not None else end_day
    for day in range(0, end_day, 7):
        st = loan_state_at(led, cfg, day, keep)
        for lid_, row in zip(st.index, st.itertuples()):
            if row.dpd > 90 and lid_ not in npa_since:
                npa_since[lid_] = day
            if lid_ in placed_on or day >= last_scan or row.dpd <= 0:
                continue
            if dpd_bucket_for(int(row.dpd)).value not in a.buckets:
                continue
            placed_on[lid_] = day
            place_state[lid_] = (int(row.dpd), float(row.total), float(row.overdue))
    ltype = dict(zip(loans.loan_id, loans.loan_type))
    placed_on = {k: v for k, v in placed_on.items() if ltype[k] in authorised}

    # the loan's lifecycle end (first terminal event), and when the agency's window closes
    life = led.lifecycle[(led.lifecycle.event != "OPENED") & (led.lifecycle.day < end_day)]
    first_end = life.sort_values("day").groupby("loan_id").first()
    window_end: dict[str, int] = {}
    for k, d0 in placed_on.items():
        e = end_day
        if k in first_end.index and int(first_end.loc[k, "day"]) >= d0:
            e = min(e, int(first_end.loc[k, "day"]) + 1)
        if suspended_day is not None:
            e = min(e, suspended_day)
        window_end[k] = e

    def in_window(frame: pd.DataFrame, day_col: str) -> pd.DataFrame:
        if not len(frame):
            return frame
        p = frame.loan_id.map(placed_on)
        e = frame.loan_id.map(window_end)
        return frame[p.notna() & (frame[day_col] >= p) & (frame[day_col] < e)]

    visits = in_window(led.visits, "day").sort_values(["day", "visit_id"])
    calls = in_window(led.calls, "day").sort_values(["day", "call_id"])
    ptps = in_window(product_promises(led.ptps), "created_day")
    pays = led.payments[(led.payments.payment_day < end_day)]

    # the loan's agent: who worked it (the ledger keeps one per borrower slot)
    worked = pd.concat([visits[["loan_id", "agent_idx"]], calls[["loan_id", "agent_idx"]]])
    agent_of_loan = worked.groupby("loan_id").agent_idx.agg(lambda s: int(s.mode().iloc[0])).to_dict()
    for k in placed_on:
        if k not in agent_of_loan:
            agent_of_loan[k] = int(rng.integers(len(w.agents)))

    # ── customers ───────────────────────────────────────────────────────────
    borrowers = led.borrowers.set_index("borrower_id")
    loans_of = loans.groupby("borrower_id").loan_id.apply(list).to_dict()
    cust_rows, cust_pos = [], {}
    hostile_by = set()
    if len(led.flags):
        f = led.flags[(led.flags.flag == "HOSTILE") & (led.flags.day < end_day)]
        hostile_by = set(f.loan_id)
    deceased = set(life[life.event == SIM_EVENT_DECEASED].loan_id)
    pulls = led.bureau_pulls[led.bureau_pulls.day < end_day] if len(led.bureau_pulls) else led.bureau_pulls
    latest_cibil = (pulls.sort_values("day").groupby("loan_id").cibil_score.last() if len(pulls)
                    else pd.Series(dtype=float))
    opening_cibil = dict(zip(loans.loan_id, loans.opening_cibil))
    city_names = {c: nm for (lv, c, nm, *_x) in R.REGIONS + R.GIRIVAN_REGIONS_EXTRA + R.KUMAON_REGIONS if lv == "CITY"}
    state_names = {"HR": "Haryana", "DL": "Delhi", "UP": "Uttar Pradesh", "UP-C": "Uttar Pradesh",
                   "RJ": "Rajasthan", "MH": "Maharashtra", "GJ": "Gujarat", "TG": "Telangana",
                   "KA": "Karnataka", "TN": "Tamil Nadu", "WB": "West Bengal", "OD": "Odisha"}
    state_of_city = {c: state_names[p] for (lv, c, _n, p, *_x) in R.REGIONS + R.GIRIVAN_REGIONS_EXTRA if lv == "CITY"}
    state_of_city["PUNE"] = "Maharashtra"
    for seq, (bid, lids) in enumerate(sorted(loans_of.items())):
        b = borrowers.loc[bid]
        placed_lids = [x for x in lids if x in placed_on]
        ag = w.agents[agent_of_loan[placed_lids[0]]] if placed_lids else w.agents[int(rng.integers(len(w.agents)))]
        city = ag.city
        culture = R.CITY_CULTURE[city]
        female = bool(rng.random() < 0.38)
        pool = R.NAME_POOLS[culture]
        firsts = pool["female" if female else "male"]
        name = f"{firsts[int(rng.integers(len(firsts)))]} {pool['surnames'][int(rng.integers(len(pool['surnames'])))]}"
        loc = R.LOCALITIES[city][int(rng.integers(len(R.LOCALITIES[city])))]
        lat, lon = jitter(rng, loc[1], loc[2], 900)
        cust_pos[bid] = (lat, lon)
        cib = latest_cibil.get(lids[-1], opening_cibil[lids[-1]])
        tags = [CUSTOMER_TAG_DECEASED] if any(x in deceased for x in lids) else []
        evening = rng.random() < 0.25
        cust_rows.append(dict(
            id=lid("customer", bid), bank_id=bank_id,
            customer_ref=f"{R.BANKS[a.bank_key]['code'][:3]}{n:02d}{seq:07d}", full_name=name,
            date_of_birth=start + timedelta(days=int(b.dob_day)), gender="FEMALE" if female else "MALE",
            pan_masked=f"XXXXX{int(rng.integers(1000, 9999))}{chr(65 + int(rng.integers(26)))}",
            aadhaar_masked=f"XXXXXXXX{int(rng.integers(1000, 9999))}",
            phone_primary=f"{7 + seq % 3}{n:02d}{seq:07d}",
            address_line1=f"{int(rng.integers(1, 480))}, {loc[0]}", city=city_names[city],
            state=state_of_city[city], pincode=loc[3], latitude=lat, longitude=lon,
            cibil_score=(None if pd.isna(cib) else int(round(float(cib)))),
            preferred_contact_start=(17 if evening else 9), preferred_contact_end=(19 if evening else 18),
            language_preference=culture, customer_segment=str(b.employment_type),
            is_hostile=any(x in hostile_by for x in lids),
            requires_female_agent=bool(female and rng.random() < 0.10),
            do_not_contact=bool(rng.random() < 0.004), fraud_flag=bool(getattr(b, "fraud_flag", 0)),
            complaints_raised=0, tags=tags))
    cust_idx = {r["id"]: i for i, r in enumerate(cust_rows)}

    # ── loans, state at the anchor ──────────────────────────────────────────
    state = loan_state_at(led, cfg, end_day, keep)
    status_now = payment_status_at(pays, end_day)
    verified_pays = pays[status_now == "VERIFIED"]
    last_amt = verified_pays.sort_values("payment_day").groupby("loan_id").amount.last()
    inst = led.installments
    next_due = inst[inst.due_day >= end_day].groupby("loan_id").due_day.min()
    life_status = {}
    for ev in life.sort_values("day").itertuples():
        life_status.setdefault(ev.loan_id, (ev.event, int(ev.day)))
    loan_rows, branch_of = [], {}
    for seq, r in enumerate(loans.itertuples()):
        cust = lid("customer", r.borrower_id)
        city = [c for c in w.branches_by_city if cust_rows[cust_idx[cust]]["city"] == city_names[c]]
        codes = w.branches_by_city[city[0]] if city else []
        if not codes:
            raise R.RosterError(f"{a.key}: no branch in {city}")
        branch = codes[seq % len(codes)]
        branch_of[r.loan_id] = branch
        s = state.loc[r.loan_id]
        ev = life_status.get(r.loan_id)
        dpd = int(s.dpd)
        status = {"WRITTEN_OFF": "WRITTEN_OFF", "SETTLED": "SETTLED", "CLOSED": "CLOSED"}.get(
            ev[0] if ev else "", "NPA" if dpd > 90 else "ACTIVE")
        opened = start + timedelta(days=int(r.origination_day))
        lp = s.last_payment_day
        loan_rows.append(dict(
            id=lid("loan", r.loan_id), bank_id=bank_id, customer_id=cust,
            loan_account_number=f"{branch}{n:02d}{seq:06d}", loan_type=r.loan_type, branch_code=branch,
            sanctioned_amount=round(float(r.sanction_amount), 2), disbursed_amount=round(float(r.sanction_amount), 2),
            outstanding_principal=round(float(s.principal), 2), total_outstanding=round(float(s.total), 2),
            overdue_amount=round(float(s.overdue), 2), emi_amount=round(float(r.emi_amount), 2),
            disbursement_date=opened, maturity_date=opened + timedelta(days=cfg.cycle_days * int(r.tenure_months)),
            last_payment_date=(None if pd.isna(lp) else start + timedelta(days=int(lp))),
            last_payment_amount=(round(float(last_amt[r.loan_id]), 2) if r.loan_id in last_amt.index else 0.0),
            next_due_date=(start + timedelta(days=int(next_due[r.loan_id])) if r.loan_id in next_due.index else None),
            dpd=dpd, dpd_as_of=R.ANCHOR_DATE, dpd_bucket=dpd_bucket_for(dpd).value, status=status,
            interest_rate=round(float(r.interest_rate), 2), penal_charges=round(float(s.penal), 2),
            tenure_months=int(r.tenure_months), npa_flag=dpd > 90,
            npa_since=(start + timedelta(days=npa_since[r.loan_id]) if r.loan_id in npa_since and dpd > 90 else None)))

    # ── placements and cases ────────────────────────────────────────────────
    contract = a.contract
    place_rows, case_rows = [], []
    case_of, agent_for = {}, {}
    for seq, (k, d0) in enumerate(sorted(placed_on.items(), key=lambda kv: (kv[1], kv[0]))):
        dpd0, total0, over0 = place_state[k]
        ag = w.agents[agent_of_loan[k]]
        agent_for[k] = ag
        pid, cid = lid("placement", k), lid("case", k)
        case_of[k] = cid
        placed = start + timedelta(days=d0)
        ev = life_status.get(k)
        ended = ev is not None and ev[1] >= d0
        p_status, reason, c_status, closure, notes = "ACTIVE", None, None, None, None
        if ended:
            p_status, reason, c_status, closure = {
                "WRITTEN_OFF": ("RETURNED", CR.WRITTEN_OFF, "WRITTEN_OFF", CR.WRITTEN_OFF),
                "SETTLED": ("RESOLVED", CR.SETTLED, "CLOSED", CR.SETTLED),
                "CLOSED": ("RESOLVED", CR.PAID, "PAID", CR.PAID),
                "RECALLED": ("RECALLED", CR.RECALLED, "CLOSED", CR.RECALLED),
                SIM_EVENT_DECEASED: ("RETURNED", CR.DECEASED, "CLOSED", CR.DECEASED),
            }[ev[0]]
            reason, closure = reason.value, closure.value
            if ev[0] == "RECALLED":
                # outcomes.censoring_status reads a recall only from this prefix.
                notes = f"RECALLED by bank on {start + timedelta(days=ev[1])}"
        place_rows.append(dict(
            id=pid, **ten, loan_id=lid("loan", k), contract_id=contract["id"], source="FEED", status=p_status,
            placed_on=placed, expected_end_on=placed + timedelta(days=contract["recall_no_activity_days"] or 90),
            ended_on=(start + timedelta(days=ev[1]) if ended else None), end_reason=reason,
            placed_by=bank_admin_id, dpd_at_placement=dpd0, dpd_bucket_at_placement=dpd_bucket_for(dpd0).value,
            exposure_at_placement=round(total0, 2), overdue_at_placement=round(over0, 2),
            sla_first_visit_due=placed + timedelta(days=contract["sla_first_visit_days"]),
            created_at=_moment(start, d0, 9.5)))
        case_rows.append(dict(
            id=cid, **ten, placement_id=pid, case_number=f"C{a.row['code'][4:7]}{seq + 1:07d}",
            customer_id=lid("customer", loans.loc[loans.loan_id == k, "borrower_id"].iloc[0]),
            loan_id=lid("loan", k), agent_id=ag.id, assigned_by_id=ag.manager_user_id,
            status=c_status or "ASSIGNED", priority=("CRITICAL" if dpd0 > 90 else "HIGH" if dpd0 > 60
                                                     else "MEDIUM" if dpd0 > 30 else "LOW"),
            target_amount=round(max(over0, 1.0), 2), collected_amount=0.0, allocation_date=placed,
            visit_count=0, resolved_at=(_moment(start, ev[1], 17.0) if ended else None),
            resolution_notes=notes, closure_reason=closure, created_at=_moment(start, d0, 10.0)))
    case_idx = {r["id"]: i for i, r in enumerate(case_rows)}

    # ── visits (time of day, place, and the product's two flags) ────────────
    visit_rows, audit_rows = [], []
    visits_by_loan_day: dict[tuple, dict] = {}
    spike = (latent.spike_from, latent.spike_to)
    for v in visits.itertuples():
        ag = w.agents[int(v.agent_idx)]
        cust = case_rows[case_idx[case_of[v.loan_id]]]["customer_id"]
        clat, clon = cust_rows[cust_idx[cust]]["latitude"], cust_rows[cust_idx[cust]]["longitude"]
        vday = start + timedelta(days=int(v.day))
        outcome, met = str(v.outcome), bool(v.met)
        gaming_rate = (latent.spike_rate if spike[0] and spike[0] <= vday < spike[1] else latent.fence_gaming_rate)
        roll = float(rng.random())
        kind = "genuine"
        if roll < gaming_rate:
            kind = "fence_gaming"
        elif roll < gaming_rate + latent.fabricated_photo_rate:
            kind = "fabricated_photo"
        if kind == "fence_gaming":
            outcome, met = "ADDRESS_ISSUE", False
            vlat, vlon = _offset(clat, clon, float(rng.uniform(150, 900)), float(rng.uniform(0, 2 * math.pi)))
        elif outcome == "ADDRESS_ISSUE":
            vlat, vlon = _offset(clat, clon, float(rng.uniform(20, 400)), float(rng.uniform(0, 2 * math.pi)))
        else:
            vlat, vlon = _offset(clat, clon, float(rng.uniform(3, 85)), float(rng.uniform(0, 2 * math.pi)))
        # Stored at 6 dp, so the flag and the distance are derived from the
        # stored coordinates, exactly as a reader recomputing them would.
        vlat, vlon = round(vlat, 6), round(vlon, 6)
        check_in = _moment(start, int(v.day), _hour(rng, "visit_met" if met else "visit_missed"))
        if not is_within_contact_hours(check_in):
            raise AssertionError("a generated visit fell outside the contact window")
        distance, geo_ok = within_geo_fence(vlat, vlon, clat, clon)
        if not geo_ok and outcome != "ADDRESS_ISSUE":
            # The product refuses it (visit_service); the agent re-checks-in at the door.
            vlat, vlon = _offset(clat, clon, float(rng.uniform(3, 60)), float(rng.uniform(0, 2 * math.pi)))
            vlat, vlon = round(vlat, 6), round(vlon, 6)
            distance, geo_ok = within_geo_fence(vlat, vlon, clat, clon)
        vid = lid("visit", v.visit_id)
        # an attempt refused for contact hours, before the real visit
        if rng.random() < latent.out_of_hours_rate:
            early = rng.random() < 0.5
            tried = _moment(start, int(v.day), float(rng.uniform(6.8, 7.95) if early else rng.uniform(19.05, 20.9)))
            if is_within_contact_hours(tried):
                raise AssertionError("an injected out-of-hours attempt is inside the window")
            aid = lid("audit", f"ooh:{v.visit_id}")
            audit_rows.append(dict(id=aid, created_at=tried, user_id=ag.user_id, **ten,
                                   action=AuditAction.CONTACT_HOUR_VIOLATION_ATTEMPT.value, entity_type="case",
                                   entity_id=case_of[v.loan_id], success=False,
                                   failure_reason="Outside RBI contact hours",
                                   details={"attempted_at_ist": tried.astimezone(IST).isoformat(),
                                            "window": f"{settings.CONTACT_HOUR_START}-{settings.CONTACT_HOUR_END}"}))
            truth.injected["out_of_hours_attempt"].append(aid)
        photo = None
        if met or kind == "fabricated_photo":
            if kind == "fabricated_photo":
                photo = _offset(vlat, vlon, float(rng.uniform(2000, 9000)), float(rng.uniform(0, 2 * math.pi)))
            else:
                photo = _offset(vlat, vlon, float(rng.uniform(1, 25)), float(rng.uniform(0, 2 * math.pi)))
        disp = getattr(v, "disposition", None)
        reason = getattr(v, "default_reason", None)
        row = dict(
            id=vid, **ten, case_id=case_of[v.loan_id], agent_id=ag.id,
            check_in_latitude=round(vlat, 6), check_in_longitude=round(vlon, 6), check_in_time=check_in,
            check_out_time=check_in + timedelta(minutes=int(rng.integers(6, 26))),
            distance_from_customer_metres=round(distance, 1), geo_verified=geo_ok,
            within_contact_hours=is_within_contact_hours(check_in), customer_met=met, outcome=outcome,
            person_met=("BORROWER" if met else None),
            not_met_reason=(None if met or outcome == "ADDRESS_ISSUE" else
                            ("PREMISES_LOCKED" if rng.random() < 0.55 else "CUSTOMER_AWAY")),
            default_reason=(reason if met and isinstance(reason, str) else None),
            borrower_disposition=(disp if met and isinstance(disp, str) and disp else None),
            consent_given=(True if met else None),   # nobody to ask when the borrower was not met
            agent_photo_lat=(round(photo[0], 6) if photo else None),
            agent_photo_lon=(round(photo[1], 6) if photo else None),
            agent_photo_accuracy=(float(rng.uniform(4, 18)) if photo else None),
            agent_photo_captured_at=(check_in + timedelta(minutes=2) if photo else None))
        visit_rows.append(row)
        visits_by_loan_day.setdefault((v.loan_id, int(v.day)), row)
        c = case_rows[case_idx[case_of[v.loan_id]]]
        c["visit_count"] += 1
        if kind != "genuine":
            truth.injected[kind].append(vid)
    truth.counts["visits"] = len(visit_rows)

    # ── calls ───────────────────────────────────────────────────────────────
    call_rows = []
    contacts_by_loan: dict[str, list[int]] = defaultdict(list)
    for (k, d), vr in visits_by_loan_day.items():
        if vr["customer_met"]:
            contacts_by_loan[k].append(d)
    for c in calls.itertuples():
        ag = w.agents[int(c.agent_idx)]
        answered = bool(c.answered)
        cust = case_rows[case_idx[case_of[c.loan_id]]]["customer_id"]
        when = _moment(start, int(c.day), _hour(rng, "call_answered" if answered else "call_missed"))
        disp = getattr(c, "disposition", None)
        vd = getattr(c, "verbal_due_day", -1)
        call_rows.append(dict(
            id=lid("call", c.call_id), **ten, case_id=case_of[c.loan_id], agent_id=ag.id, customer_id=cust,
            called_at=when, outcome=str(c.outcome),
            duration_seconds=(int(c.duration_seconds) if answered and not pd.isna(c.duration_seconds) else None),
            phone_used=cust_rows[cust_idx[cust]]["phone_primary"],
            payment_intent_signalled=(bool(c.payment_intent) if answered else None),
            borrower_disposition=(disp if answered and isinstance(disp, str) and disp else None),
            verbal_payment_date=(start + timedelta(days=int(vd)) if vd is not None and int(vd) >= 0 else None)))
        if answered:
            contacts_by_loan[c.loan_id].append(int(c.day))
    truth.counts["calls"] = len(call_rows)

    # ── promises ────────────────────────────────────────────────────────────
    ptp_rows = []
    for t, st in zip(ptps.itertuples(), ptp_status_at(ptps, end_day)):
        ag = agent_for[t.loan_id]
        vr = visits_by_loan_day.get((t.loan_id, int(t.created_day)))
        ptp_rows.append(dict(
            id=lid("ptp", t.ptp_id), **ten, case_id=case_of[t.loan_id], agent_id=ag.id,
            visit_id=(vr["id"] if vr else None), committed_amount=round(float(t.committed_amount), 2),
            committed_date=start + timedelta(days=int(t.committed_day)), status=st.value,
            created_at=_moment(start, int(t.created_day), 15.0)))
        if st.value == "ACTIVE":
            case_rows[case_idx[case_of[t.loan_id]]]["status"] = "PTP_SET" \
                if case_rows[case_idx[case_of[t.loan_id]]]["closure_reason"] is None else \
                case_rows[case_idx[case_of[t.loan_id]]]["status"]
    truth.counts["ptps"] = len(ptp_rows)

    # ── payments: collected by the agent, or paid to the bank directly ──────
    pay_rows = []
    p_in = in_window(pays, "payment_day")
    p_status = payment_status_at(p_in, end_day)
    for seq, (p, st) in enumerate(zip(p_in.itertuples(), p_status)):
        k, d = p.loan_id, int(p.payment_day)
        ag = agent_for[k]
        vr = visits_by_loan_day.get((k, d))
        # Attributed to the agent when it follows their contact: always within
        # a week (the ledger's own contact effect lasts call_contact_days=7),
        # usually within three (the agency's payment link, a follow-up call).
        gaps = [d - x for x in contacts_by_loan.get(k, ()) if d - x >= 0]
        recent = bool(gaps) and (min(gaps) <= 7 or (min(gaps) <= 21 and rng.random() < 0.6))
        amount = round(float(p.amount), 2)
        if amount <= 0:
            continue
        if vr is not None and vr["customer_met"]:
            mode = "CASH" if rng.random() < 0.45 else ("UPI" if rng.random() < 0.8 else "CHEQUE")
            agent_id, visit_id = ag.id, vr["id"]
            when = vr["check_in_time"] + timedelta(minutes=5)
        elif recent:
            mode = "UPI" if rng.random() < 0.85 else "NEFT"
            agent_id, visit_id = ag.id, None
            when = _moment(start, d, float(rng.uniform(9, 21)))
        else:
            mode, agent_id, visit_id = "BANK_DIRECT", None, None
            when = _moment(start, d, 12.0)
        pay_rows.append(dict(
            id=lid("payment", p.payment_id), **ten, case_id=case_of[k], loan_id=lid("loan", k), visit_id=visit_id,
            agent_id=agent_id, amount=amount, mode=mode, status=str(st),
            receipt_number=f"R{n:02d}{seq:08d}",
            upi_reference=(f"{int(rng.integers(10**11, 10**12))}" if mode == "UPI" else None),
            cheque_number=(f"{int(rng.integers(100000, 999999))}" if mode == "CHEQUE" else None),
            bank_reference=(f"GFLN{int(rng.integers(10**9, 10**10))}" if mode in ("NEFT", "BANK_DIRECT") else None),
            payment_date=when,
            verified_at=(when + timedelta(days=int(rng.integers(0, 3))) if str(st) == "VERIFIED" else None),
            verified_by_id=(bank_admin_id if str(st) == "VERIFIED" else None)))
        if str(st) == "VERIFIED" and agent_id is not None:
            c = case_rows[case_idx[case_of[k]]]
            c["collected_amount"] = round(c["collected_amount"] + amount, 2)
    truth.counts["payments"] = len(pay_rows)
    for c in case_rows:
        if c["closure_reason"] is None:
            if c["status"] == "ASSIGNED" and c["visit_count"] > 0:
                c["status"] = "IN_PROGRESS"
            if c["collected_amount"] > 0 and c["status"] in ("ASSIGNED", "IN_PROGRESS"):
                c["status"] = "PARTIALLY_PAID"

    # ── settlement offers (before every SETTLED end), disputes, complaints ──
    offer_rows, dispute_rows = [], []
    settled = life[(life.event == "SETTLED")]
    for ev in settled.itertuples():
        if ev.loan_id not in case_of or ev.day < placed_on[ev.loan_id]:
            continue
        ag = agent_for[ev.loan_id]
        s = loan_state_at(led, cfg, int(ev.day), {ev.loan_id}).iloc[0]
        outstanding = round(float(s.total), 2)
        if outstanding <= 0:
            continue
        tries = 2 if rng.random() < SETTLEMENT_DECLINE_BEFORE_ACCEPT else 1
        for t in range(tries):
            last = t == tries - 1
            d = int(ev.day) - (8 if last else 26)
            share = float(rng.uniform(0.55, 0.72)) if last else float(rng.uniform(0.78, 0.9))
            offer_rows.append(dict(
                id=lid("settlement_offer", f"{ev.loan_id}:{t}"), **ten, case_id=case_of[ev.loan_id],
                loan_id=lid("loan", ev.loan_id), placement_id=lid("placement", ev.loan_id),
                outstanding_at_offer=outstanding, offered_amount=round(outstanding * share, 2),
                policy_floor_amount=round(outstanding * 0.5, 2), source="AGENT", proposed_by=ag.user_id,
                approved_by=bank_admin_id, approved_at=_moment(start, d, 12.0),
                status=("FULFILLED" if last else "DECLINED_BY_BORROWER"),
                valid_until=start + timedelta(days=d + 15),
                decided_at=_moment(start, d + (6 if last else 4), 16.0),
                borrower_response_at=_moment(start, d + (6 if last else 4), 15.0)))
    truth.counts["settlement_offers"] = len(offer_rows)
    by_default = {"AMOUNT_DISPUTED": "AMOUNT_DISPUTED", "ALREADY_PAID": "ALREADY_PAID", "FRAUD_CLAIM": "FRAUD_CLAIM"}
    for vr in visit_rows:
        if vr["outcome"] != "DISPUTE":
            continue
        raised = vr["check_in_time"]
        resolved = rng.random() < 0.7
        dispute_rows.append(dict(
            id=R.new_id("dispute", vr["id"]), **ten, case_id=vr["case_id"],
            loan_id=case_rows[case_idx[vr["case_id"]]]["loan_id"], visit_id=vr["id"], kind="DISPUTE",
            raised_via="VISIT", category=by_default.get(vr["default_reason"] or "", "OTHER"),
            description="Borrower disputes the amount due at the doorstep; statement requested.",
            raised_at=raised, sla_due_at=raised + timedelta(days=15),
            status=("RESOLVED_REJECTED" if resolved and rng.random() < 0.6 else
                    "RESOLVED_UPHELD" if resolved else "UNDER_REVIEW"),
            resolved_at=(raised + timedelta(days=int(rng.integers(3, 15))) if resolved else None),
            resolved_by=(bank_admin_id if resolved else None),
            resolution=("Statement shared; the dues stand." if resolved else None)))
    breaching = latent.out_of_hours_rate >= 0.02 or latent.spike_rate > 0
    rate = COMPLAINT_RATE_BREACHING if breaching else COMPLAINT_RATE
    for c in case_rows:
        if rng.random() >= rate:
            continue
        raised = c["created_at"] + timedelta(days=int(rng.integers(5, 60)))
        if raised.date() > R.ANCHOR_DATE:
            continue
        cat = ("AGENT_CONDUCT" if rng.random() < 0.5 else "HARASSMENT" if rng.random() < 0.5 else "PRIVACY")
        resolved = rng.random() < 0.6
        did = R.new_id("dispute", f"complaint:{c['id']}")
        dispute_rows.append(dict(
            id=did, **ten, case_id=c["id"], loan_id=c["loan_id"], kind="COMPLAINT",
            raised_via=("BANK" if rng.random() < 0.5 else "BORROWER"), category=cat,
            description={"AGENT_CONDUCT": "Borrower reports the field agent was rude at the door.",
                         "HARASSMENT": "Borrower reports repeated visits to the workplace.",
                         "PRIVACY": "Borrower reports the agent discussed the loan with a neighbour."}[cat],
            raised_at=raised, sla_due_at=raised + timedelta(days=15),
            status=("RESOLVED_UPHELD" if resolved and rng.random() < 0.4 else
                    "RESOLVED_REJECTED" if resolved else "OPEN"),
            resolved_at=(raised + timedelta(days=int(rng.integers(4, 20))) if resolved else None),
            resolved_by=(bank_admin_id if resolved else None),
            resolution=("Reviewed with the agency; action noted." if resolved else None)))
        cust_rows[cust_idx[c["customer_id"]]]["complaints_raised"] += 1
        truth.injected["complaint"].append(did)
    truth.counts["disputes"] = sum(1 for d in dispute_rows if d["kind"] == "DISPUTE")
    truth.counts["complaints"] = sum(1 for d in dispute_rows if d["kind"] == "COMPLAINT")

    # ── schedules (windowed) and DPD history for EVERY loan ─────────────────
    lo_d, hi_d = INSTALMENT_WINDOW
    inst_rows = [dict(bank_id=bank_id, loan_id=lid("loan", r.loan_id), instalment_no=int(r.installment_no),
                      due_date=r.due_date, amount_due=round(float(r.amount), 2), source="LEDGER",
                      id=R.new_id("instalment", f"{a.key}:{r.loan_id}:{int(r.installment_no)}"))
                 for r in inst.itertuples() if lo_d <= r.due_date <= hi_d]
    region_of_city_name = {city_names[c]: rid for c, rid in w.region_by_city.items()}
    region_of_loan = {r.loan_id: region_of_city_name.get(cust_rows[cust_idx[lid("customer", r.borrower_id)]]["city"])
                      for r in loans.itertuples()}
    opened_day = dict(zip(loans.loan_id, loans.origination_day.astype(int)))
    hist_rows = []
    for d in history_dates(start):
        day = (d - start).days + 1                 # the state at the END of d
        on = [k for k in loans.loan_id if opened_day[k] < day]
        if not on:
            continue
        st = loan_state_at(led, cfg, day, set(on))
        for k in on:
            s = st.loc[k]
            dpd = int(s.dpd)
            ev = life_status.get(k)
            ended = ev is not None and ev[1] < day
            placed = k in placed_on and placed_on[k] < day and not (ended and ev[1] >= placed_on[k])
            status = ({"WRITTEN_OFF": "WRITTEN_OFF", "SETTLED": "SETTLED", "CLOSED": "CLOSED"}.get(ev[0])
                      if ended else None) or ("NPA" if dpd > 90 else "ACTIVE")
            hist_rows.append(dict(
                loan_id=lid("loan", k), as_of_date=d, bank_id=bank_id, dpd=dpd,
                dpd_bucket=dpd_bucket_for(dpd).value, loan_status=status, overdue_amount=round(float(s.overdue), 2),
                total_outstanding=round(float(s.total), 2), outstanding_principal=round(float(s.principal), 2),
                penal_charges=round(float(s.penal), 2), npa_flag=dpd > 90, loan_type=ltype[k],
                region_id=region_of_loan[k], agency_id=(agency_id if placed else None),
                placement_id=(lid("placement", k) if placed else None),
                is_month_end=(d + timedelta(days=1)).day == 1,
                source="LEDGER", is_backfill=False, observed_pit=True))

    # ── write ───────────────────────────────────────────────────────────────
    for table, rows in (("customers", cust_rows), ("loans", loan_rows), ("placements", place_rows),
                        ("cases", case_rows), ("visits", visit_rows), ("call_logs", call_rows),
                        ("ptps", ptp_rows), ("payments", pay_rows), ("settlement_offers", offer_rows),
                        ("disputes", dispute_rows), ("loan_instalments", inst_rows),
                        ("loan_dpd_history", hist_rows), ("audit_logs", audit_rows)):
        truth.counts[table] = insert(conn, table, rows)
    v = pd.DataFrame(visit_rows)
    if len(v):
        v["month"] = [t.astimezone(IST).strftime("%Y-%m") for t in v.check_in_time]
        truth.rates["outside_fence_share_by_month"] = (
            v.groupby("month").geo_verified.apply(lambda s: round(1 - s.mean(), 4)).to_dict())
        truth.rates["outside_fence_share"] = round(1 - float(v.geo_verified.mean()), 4)
        truth.rates["met_rate"] = round(float(v.customer_met.mean()), 4)
    truth.rates["placements"] = len(place_rows)
    return truth
