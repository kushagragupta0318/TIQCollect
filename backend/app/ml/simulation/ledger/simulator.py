# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-09 — NEW. The daily tick engine. Emits EVENTS ONLY.
#
#   NOTHING IN THIS FILE COMPUTES A MODEL FEATURE. That is the discipline the
#   whole rewrite rests on: this module knows about borrowers, money and time;
#   `panel.py` knows about features. Because the two are separate, the panel can
#   be checked against the production adapter, which is what makes training and
#   serving skew detectable for the first time.
#
#   DAILY, NOT MONTHLY. book_simulator steps a month at a time, so "paid three
#   days after the promise" is not representable and a PTP can only be kept or
#   broken at month granularity. Promise-keeping is the model's third-strongest
#   feature (IV 0.2798); simulating it coarsely is simulating it wrongly.
#
#   THE LATENTS NEVER LEAVE THIS FILE except through `Ledger.ground_truth`,
#   which is written to its own file and joined into nothing. A borrower's
#   willingness drives their payments and is never observable — the model sees
#   only decayed, noisy consequences of it.
# ───────────────────────────────────────────────────────────────────────────
"""Event-sourced generation of a delinquent collections book.

    sim = LedgerSimulator(LedgerConfig())
    ledger = sim.run()

`ledger` carries append-only event tables. Nothing is mutated after the day it
is written, so any quantity can be reconstructed for any past date — which is
exactly what the live database cannot do, and the reason a retrospective
backtest against it turned out to be impossible.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.ml.simulation.ledger import billing
from app.ml.simulation.ledger.config import LedgerConfig
from app.ml.eligibility import _SECURED
from app.models.loan import LoanType, dpd_bucket_for

logger = logging.getLogger(__name__)

# THE PRODUCT'S OWN ENUM, not a list of plausible strings. An earlier draft had
# TWO_WHEELER, which is not a member of `models.loan.LoanType` at all — so those
# loans could never be materialised into the real schema, and the divergence
# would have surfaced as a materialiser crash rather than as what it was: a
# simulator modelling a world the product cannot represent.
LOAN_TYPES = tuple(e.value for e in (LoanType.PERSONAL, LoanType.AUTO,
                                     LoanType.HOME, LoanType.GOLD,
                                     LoanType.BUSINESS, LoanType.EDUCATION))
# Imported, never restated. `ml/eligibility._SECURED` is what the adapter reads
# to set `is_secured`, and the simulator disagreeing with it would put a
# training/serving skew into the one feature that is a pure lookup.
SECURED = {e.value for e in _SECURED}
CITIES = ("Gurugram", "Delhi", "Noida", "Faridabad", "Ghaziabad")
EMPLOYMENT = ("SALARIED", "SELF_EMPLOYED", "BUSINESS_OWNER")
RESIDENCE = ("OWNED", "RENTED", "FAMILY")
CHANNELS = ("BRANCH", "DSA", "DIGITAL", "TELE")

PAY_VERIFIED, PAY_PENDING, PAY_REJECTED, PAY_REVERSED = (
    "VERIFIED", "PENDING_VERIFICATION", "REJECTED", "REVERSED")
PTP_HONORED, PTP_BROKEN, PTP_RESCHEDULED = "HONORED", "BROKEN", "RESCHEDULED"


def _logistic(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


@dataclass
class Ledger:
    """Append-only event tables, plus the quarantined ground truth."""

    borrowers: pd.DataFrame
    loans: pd.DataFrame
    installments: pd.DataFrame
    bureau_pulls: pd.DataFrame
    payments: pd.DataFrame
    visits: pd.DataFrame
    ptps: pd.DataFrame
    lifecycle: pd.DataFrame
    agents: pd.DataFrame
    #: LATENTS. Never joined into a panel; written to its own file so that
    #: joining it is a deliberate act rather than an accident of a merge.
    ground_truth: pd.DataFrame
    config: dict = field(default_factory=dict)

    def tables(self) -> dict[str, pd.DataFrame]:
        return {k: v for k, v in self.__dict__.items()
                if isinstance(v, pd.DataFrame)}


class LedgerSimulator:
    """Generates the book one day at a time."""

    def __init__(self, cfg: LedgerConfig | None = None):
        self.cfg = cfg or LedgerConfig()
        self.rng = np.random.default_rng(self.cfg.seed)

    # ── static draws ────────────────────────────────────────────────────────
    def _make_agents(self) -> pd.DataFrame:
        c, rng = self.cfg, self.rng
        n = c.n_agents
        return pd.DataFrame({
            "agent_id": [f"AG{i:03d}" for i in range(n)],
            # Skill is a real, persistent difference between agents. It affects
            # payments and is NEVER a borrower feature — whose agent you have is
            # the allocator's business, not the borrower's risk.
            "agent_skill": np.clip(rng.normal(0.0, 0.35, n), -0.9, 0.9),
            "joined_day": rng.integers(-720, 1, n),
        })

    def _origination_dpd(self, n: int, mix: dict) -> np.ndarray:
        """Draw an opening DPD from a bucket mix, uniform inside each bucket."""
        keys = list(mix.keys())
        probs = np.array([mix[k] for k in keys], dtype=float)
        probs = probs / probs.sum()
        pick = self.rng.choice(len(keys), size=n, p=probs)
        lo = {"CURRENT": 0, "BUCKET_1": 1, "BUCKET_2": 31, "BUCKET_3": 61, "NPA": 91}
        hi = {"CURRENT": 0, "BUCKET_1": 30, "BUCKET_2": 60, "BUCKET_3": 90, "NPA": 260}
        out = np.zeros(n)
        for i, k in enumerate(keys):
            m = pick == i
            if m.any():
                out[m] = self.rng.integers(lo[k], hi[k] + 1, m.sum())
        return out

    def _new_accounts(self, n: int, day: int, *, seasoned: bool,
                      score_shift: float = 0.0) -> dict:
        """Draw n accounts. `seasoned` opens them with existing delinquency."""
        c, rng = self.cfg, self.rng
        lt = rng.choice(LOAN_TYPES, n, p=[0.34, 0.18, 0.11, 0.11, 0.16, 0.10])
        secured = np.isin(lt, list(SECURED)).astype(int)

        sanction = np.round(np.exp(rng.normal(11.9, 0.75, n)) / 1000) * 1000
        sanction = np.clip(sanction, 25_000, 4_000_000)
        tenure = rng.choice([12, 24, 36, 48, 60], n, p=[.12, .28, .32, .18, .10])
        rate = np.clip(rng.normal(16.5, 4.0, n) - 3.5 * secured, 8.0, 34.0)
        r_m = rate / 1200.0
        emi = np.round(sanction * r_m * (1 + r_m) ** tenure /
                       np.maximum((1 + r_m) ** tenure - 1, 1e-9), 0)

        # Latent disposition. Correlated with the bureau score but NOT
        # determined by it — the residual is the unobserved variance that stops
        # any observable model from reaching the oracle.
        z = rng.normal(0, 1, n)
        w_anchor = np.clip(0.5 + 0.19 * z + rng.normal(0, 0.17, n), 0.02, 0.98)
        cibil_true = 300 + 600 * _logistic(1.35 * z + rng.normal(0, 0.62, n))
        cibil = np.clip(np.round(cibil_true + score_shift), 300, 900)

        income = np.round(np.exp(rng.normal(10.6, 0.55, n)) / 100) * 100
        mob = (rng.integers(3, 40, n) if seasoned
               else rng.integers(0, 3, n))
        mob = np.minimum(mob, tenure - 1)

        _ages = rng.integers(22, 66, n)
        target_dpd = self._origination_dpd(
            n, c.dpd_start_mix if seasoned else c.dpd_origination_mix)
        sched, paid = billing.seed_seasoned_book(
            rng, n, target_dpd, mob, emi, tenure, c.cycle_days)

        return {
            "loan_type": lt, "is_secured": secured, "sanction_amount": sanction,
            "tenure_months": tenure, "interest_rate": np.round(rate, 2),
            "emi_amount": emi, "cibil_score": cibil, "monthly_income": income,
            "city": rng.choice(CITIES, n, p=[.30, .24, .18, .14, .14]),
            "employment_type": rng.choice(EMPLOYMENT, n, p=[.52, .31, .17]),
            "residence_type": rng.choice(RESIDENCE, n, p=[.44, .41, .15]),
            "sourcing_channel": rng.choice(CHANNELS, n, p=[.38, .29, .21, .12]),
            "branch_code": rng.choice([f"BR{i:02d}" for i in range(1, 13)], n),
            "age": _ages,
            "dob_day": day - (_ages * 365 + rng.integers(0, 365, n)),
            "address_vintage_months": rng.integers(1, 200, n),
            "phone_verified": (rng.random(n) < 0.78).astype(int),
            "credit_vintage_months": np.clip(
                rng.integers(2, 220, n), 2, None).astype(int),
            "num_open_loans": rng.integers(1, 7, n),
            "num_enquiries_6m": rng.poisson(1.6, n),
            "other_lender_delinq": (rng.random(n) < 0.24).astype(int),
            "utilization_pct": np.clip(rng.beta(2.4, 2.0, n) * 100, 0, 100),
            "mail_returned_count": rng.poisson(0.35, n),
            # latents
            "_w_anchor": w_anchor,
            "_w": w_anchor.copy(),
            "_capacity": np.clip(rng.beta(2.6, 2.2, n), 0.03, 0.97),
            "_reachability": np.clip(rng.beta(2.4, 2.1, n), 0.03, 0.97),
            "_shock": np.zeros(n, dtype=int),
            # schedule + ledger position
            "first_due_day": sched.first_due_day + day,
            "n_installments": tenure,
            "paid_known": paid,
            "opened_day": np.full(n, day),
            "clean_cycles": np.zeros(n, dtype=int),
            "last_pay_day": np.full(n, -9999),
            "last_met_day": np.full(n, -9999),
            "bureau_day": np.full(n, day - rng.integers(0, 120, n)),
        }

    # ── the run ─────────────────────────────────────────────────────────────
    def run(self, intercept: float = -4.05) -> Ledger:
        # Reseeded per run so a given (config, seed, intercept) always produces
        # the same book. Without this the calibration loop below would consume
        # the stream and every probe would sample a different world, which is
        # both irreproducible and a good way to mistake noise for a trend.
        self.rng = np.random.default_rng(self.cfg.seed)
        c, rng = self.cfg, self.rng
        n = c.n_borrowers
        n_days = (c.months + 1) * c.cycle_days

        agents = self._make_agents()
        skill = agents["agent_skill"].to_numpy()
        agent_of = rng.integers(0, c.n_agents, n)

        st = self._new_accounts(n, 0, seasoned=True)
        loan_uid = np.arange(n)
        next_uid = n
        borrower_uid = np.arange(n)
        next_borrower = n
        active = np.ones(n, dtype=bool)

        # Static rows are appended as accounts are created, so a recycled slot
        # produces a genuinely new loan rather than silently reusing an old id.
        loan_rows: list[dict] = []
        borrower_rows: list[dict] = []
        inst_rows: list[dict] = []

        def _register(idx, day):
            for i in np.atleast_1d(idx):
                loan_rows.append({
                    "loan_id": f"L{loan_uid[i]:07d}",
                    "borrower_id": f"B{borrower_uid[i]:07d}",
                    "loan_type": st["loan_type"][i], "is_secured": int(st["is_secured"][i]),
                    "sanction_amount": float(st["sanction_amount"][i]),
                    "emi_amount": float(st["emi_amount"][i]),
                    "tenure_months": int(st["tenure_months"][i]),
                    "interest_rate": float(st["interest_rate"][i]),
                    "branch_code": st["branch_code"][i],
                    "sourcing_channel": st["sourcing_channel"][i],
                    "first_due_day": int(st["first_due_day"][i]),
                    "opened_day": int(day),
                    # The score AT ORIGINATION. `bureau_pulls` carries every
                    # refresh after that; this is the fallback for an account
                    # that has never been repulled, so `cibil_score` is always
                    # answerable at any as_of without reading today's value.
                    "opening_cibil": float(st["cibil_score"][i]),
                    # MONEY PAID BEFORE THE LEDGER STARTS. A seasoned account
                    # opens mid-life and its earlier instalments were cleared
                    # before this book began observing it, so those payments
                    # are not events here — exactly as a production extract
                    # carries an opening balance plus a ledger from the extract
                    # date forward. Without it `panel.py` recomputes `paid` from
                    # zero and every seasoned loan reads as never having paid:
                    # measured, that put 77.5% of account-months in NPA and DPD
                    # up at 1,055 days.
                    "opening_paid": float(st["paid_known"][i]),
                    # The day the loan was disbursed: one cycle before its first
                    # instalment fell due. Stored because `months_on_book` and
                    # the adapter's `disbursement_date` must both derive from
                    # ONE origination fact rather than each reconstructing it.
                    "origination_day": int(st["first_due_day"][i] - c.cycle_days),
                })
                borrower_rows.append({
                    "borrower_id": f"B{borrower_uid[i]:07d}",
                    "city": st["city"][i], "employment_type": st["employment_type"][i],
                    "residence_type": st["residence_type"][i],
                    "age": int(st["age"][i]),
                    # Age must MOVE. The adapter computes it from date_of_birth
                    # at as_of, so a panel carrying a static age would disagree
                    # with it by a year over a two-year book.
                    "dob_day": int(st["dob_day"][i]),
                    "monthly_income": float(st["monthly_income"][i]),
                    "address_vintage_months": int(st["address_vintage_months"][i]),
                    "phone_verified": int(st["phone_verified"][i]),
                    "credit_vintage_months": int(st["credit_vintage_months"][i]),
                    # Bureau-adjacent attributes. Held on the borrower rather
                    # than on `bureau_pulls` because every one of them is in
                    # FEED_ONLY_FEATURES — a real lender has them, this product
                    # does not store them, so they never reach the model and
                    # their staleness cannot affect a served score.
                    "num_open_loans": int(st["num_open_loans"][i]),
                    "num_enquiries_6m": int(st["num_enquiries_6m"][i]),
                    "other_lender_delinq": int(st["other_lender_delinq"][i]),
                    "utilization_pct": float(st["utilization_pct"][i]),
                    "mail_returned_count": int(st["mail_returned_count"][i]),
                })
                for k in range(int(st["n_installments"][i])):
                    inst_rows.append({
                        "loan_id": f"L{loan_uid[i]:07d}", "installment_no": k,
                        "due_day": int(st["first_due_day"][i] + c.cycle_days * k),
                        "amount": float(st["emi_amount"][i]),
                    })

        _register(np.arange(n), 0)

        pay: list = []
        vis: list = []
        ptp: list = []
        life: list = []
        bureau: list = []
        truth: list = []

        # Open PTP book, as parallel arrays so resolution is vectorised.
        ptp_idx = np.zeros(0, dtype=int)
        ptp_due = np.zeros(0, dtype=int)
        ptp_amt = np.zeros(0, dtype=float)
        ptp_born = np.zeros(0, dtype=int)
        ptp_paid = np.zeros(0, dtype=float)
        ptp_row = np.zeros(0, dtype=int)

        # Pending payment-status transitions: (day, payment_row, new_status).
        sched_status: dict[int, list[tuple[int, str]]] = {}

        def _bucket_flag(d):
            return d > 30

        for t in range(n_days):
            month = t // c.cycle_days
            # ── latents drift ───────────────────────────────────────────────
            eps = rng.normal(0, c.latent_sigma, n)
            st["_w"] = np.clip(
                st["_w_anchor"] + c.latent_rho * (st["_w"] - st["_w_anchor"]) + eps,
                0.01, 0.99)
            if t % c.cycle_days == 0:
                enter = (st["_shock"] == 0) & (rng.random(n) < c.income_shock_in)
                leave = (st["_shock"] == 1) & (rng.random(n) < c.income_shock_out)
                st["_shock"] = np.where(enter, 1, np.where(leave, 0, st["_shock"]))

            eff_capacity = st["_capacity"] * (1 - c.income_shock_depth * st["_shock"])

            # ── derived delinquency ─────────────────────────────────────────
            sched = billing.Schedule(st["first_due_day"], st["emi_amount"],
                                     st["n_installments"], c.cycle_days)
            dpd = billing.dpd_at(t, sched, st["paid_known"], c.grace_days)
            overdue = billing.overdue_at(t, sched, st["paid_known"])

            # ── field activity ──────────────────────────────────────────────
            p_visit = np.where(_bucket_flag(dpd), c.visit_hazard_delinquent,
                               c.visit_hazard_current)
            visited = active & (rng.random(n) < p_visit)
            vi = np.flatnonzero(visited)
            if vi.size:
                p_met = _logistic(0.15 + 3.1 * (st["_reachability"][vi] - 0.5)
                                  + 0.55 * (st["_w"][vi] - 0.5)
                                  - 0.45 * np.clip(dpd[vi] / 150, 0, 2)
                                  + rng.normal(0, 0.22, vi.size))
                met = rng.random(vi.size) < p_met
                st["last_met_day"][vi[met]] = t
                for j, i in enumerate(vi):
                    vis.append((int(loan_uid[i]), t, int(agent_of[i]), bool(met[j])))

                # ── promises, only where the borrower was actually met ──────
                mi = vi[met]
                if mi.size:
                    p_ptp = _logistic(-0.35 + 2.4 * (st["_w"][mi] - 0.5))
                    make = rng.random(mi.size) < p_ptp
                    pi = mi[make]
                    if pi.size:
                        horizon = rng.integers(c.ptp_horizon_lo,
                                               c.ptp_horizon_hi + 1, pi.size)
                        amt = np.minimum(overdue[pi],
                                         st["emi_amount"][pi] *
                                         rng.uniform(0.5, 1.6, pi.size))
                        base = len(ptp)
                        for j, i in enumerate(pi):
                            ptp.append({"loan_uid": int(loan_uid[i]), "created_day": t,
                                        "committed_day": int(t + horizon[j]),
                                        "committed_amount": float(amt[j]),
                                        "resolved_day": -1, "resolved_status": None})
                        ptp_idx = np.concatenate([ptp_idx, pi])
                        ptp_due = np.concatenate([ptp_due, t + horizon])
                        ptp_amt = np.concatenate([ptp_amt, amt])
                        ptp_born = np.concatenate([ptp_born, np.full(pi.size, t)])
                        ptp_paid = np.concatenate([ptp_paid, np.zeros(pi.size)])
                        ptp_row = np.concatenate([ptp_row,
                                                  np.arange(base, base + pi.size)])

            # ── payment hazard ──────────────────────────────────────────────
            recent_contact = (t - st["last_met_day"]) <= 14
            ptp_live = np.zeros(n, dtype=bool)
            if ptp_idx.size:
                live = (ptp_due >= t) & (ptp_born <= t)
                ptp_live[ptp_idx[live]] = True

            season = c.seasonality_amplitude * np.sin(2 * np.pi * month / 12.0)
            shock = c.shock_magnitude if month == c.shock_month_index else 0.0

            # ── CONCEPT DRIFT ───────────────────────────────────────────────
            # The RELATIONSHIP changes, not the population. In the last quarter
            # of the book the borrower's disposition stops predicting payment as
            # well as it did — a policy change, a collections-strategy shift, an
            # external shock to the segment. Every behavioural feature the model
            # reads (paid_ratio_*, contact_rate_6m, ptp_kept_ratio) is downstream
            # of that latent signal, so weakening it degrades them all together.
            #
            # This is the ONLY thing `--stress` turns on, and its purpose is to
            # prove `monitor_model`'s retrain trigger can actually fire. OFF by
            # default so the headline stays comparable with the 1.1.0 baseline.
            # Covariate drift (the origination mix walking) is separate and
            # always on — that one moves PSI, this one moves Gini.
            signal = c.signal_scale
            if c.concept_drift:
                onset = c.months * c.concept_drift_onset_frac
                if month >= onset:
                    ramp = min(1.0, (month - onset) / max(c.months - onset, 1))
                    signal *= (1.0 - c.concept_drift_decay * ramp)

            lin = (intercept
                   + signal * (2.40 * (st["_w"] - 0.5)
                                + 1.55 * (eff_capacity - 0.5)
                                + 0.85 * (st["_reachability"] - 0.5))
                   - 0.0105 * np.clip(dpd, 0, 300)
                   + 0.50 * recent_contact
                   # PAYMENTS CLUSTER ON THE DUE DATE. A bill arrives, a
                   # reminder goes out, salaries land — and the money follows
                   # within days. Drawing payment days from a flat hazard
                   # scatters them uniformly through the cycle instead, which
                   # is what left performing accounts sitting in arrears for
                   # most of every month.
                   + 1.10 * np.exp(-((t - st["first_due_day"]) % c.cycle_days) / 5.0)
                   + 1.20 * ptp_live
                   + 0.65 * skill[agent_of] * recent_contact
                   + season + shock
                   + rng.normal(0, 0.55 * c.observation_noise, n))
            pays = active & (overdue > 0) & (rng.random(n) < _logistic(lin))
            qi = np.flatnonzero(pays)
            if qi.size:
                emi_i = st["emi_amount"][qi]
                # HOW MUCH someone pays depends on HOW FAR BEHIND THEY ARE.
                # An earlier draft drew the amount from a fixed mixture with a
                # flat 8% chance of clearing arrears outright, regardless of
                # whether the borrower was one cycle behind or nine. That made
                # the book a one-way ratchet: partial payments never catch up
                # because a fresh instalment bills every cycle, so cures almost
                # never happened, the book never turned over, and it aged into a
                # sink. Measured on a 500-borrower probe at the prevalence
                # target: NPA 43%, CURRENT 0.5%.
                #
                # Someone one instalment behind frequently clears it; someone
                # six behind almost never does. Conditioning on affordability is
                # a statement about borrowers, not a knob aimed at a metric.
                arrears_cycles = overdue[qi] / np.maximum(emi_i, 1.0)
                p_clear = _logistic(2.1 - 1.55 * arrears_cycles)
                clears = rng.random(qi.size) < p_clear

                kind = rng.choice(3, qi.size, p=[0.42, 0.42, 0.16])
                # Rounded to the nearest 100. Field collection is largely cash
                # and UPI in whole notes — nobody hands over Rs 4,163.72. Only
                # the token draw rounded before, which put the round-number
                # share at 9.6% against a 10-45% band; the shortfall was a
                # missing behaviour, not a missing knob.
                partial = np.round(
                    rng.beta(2, 3, qi.size) *
                    np.minimum(overdue[qi], 2 * emi_i) / 100.0) * 100.0
                token = np.round(rng.uniform(0.15, 0.8, qi.size) *
                                 emi_i / 500.0) * 500.0
                amt = np.where(clears, overdue[qi],
                      np.where(kind == 0, emi_i,
                      np.where(kind == 1, partial, token)))
                amt = np.round(np.clip(amt, 100.0, overdue[qi] + emi_i), 2)

                u = rng.random(qi.size)
                init = np.where(u < c.p_rejected, PAY_REJECTED,
                       np.where(u < c.p_rejected + c.p_pending_then_verified,
                                PAY_PENDING, PAY_VERIFIED))
                for j, i in enumerate(qi):
                    row = len(pay)
                    status = init[j]
                    pay.append({"loan_row": int(i), "loan_uid": int(loan_uid[i]), "payment_day": t,
                                "amount": float(amt[j]),
                                "initial_status": status,
                                "final_status": status,
                                "status_effective_day": t,
                                "reverses_row": -1})
                    if status == PAY_VERIFIED:
                        st["paid_known"][i] += amt[j]
                        st["last_pay_day"][i] = t
                        if rng.random() < c.p_reversed:
                            lag = int(rng.integers(c.reversal_lag_lo,
                                                   c.reversal_lag_hi + 1))
                            sched_status.setdefault(t + lag, []).append(
                                (row, PAY_REVERSED))
                    elif status == PAY_PENDING:
                        lag = int(rng.integers(c.verify_lag_lo, c.verify_lag_hi + 1))
                        sched_status.setdefault(t + lag, []).append(
                            (row, PAY_VERIFIED))
                    if ptp_idx.size:
                        hit = (ptp_idx == i) & (ptp_born <= t) & \
                              (ptp_due + c.ptp_grace_days >= t)
                        ptp_paid[hit] += amt[j]

            # ── status transitions that fall due today ──────────────────────
            for row, new_status in sched_status.pop(t, []):
                rec = pay[row]
                rec["final_status"] = new_status
                rec["status_effective_day"] = t
                i = rec["loan_row"]
                if new_status == PAY_VERIFIED:
                    st["paid_known"][i] += rec["amount"]
                    st["last_pay_day"][i] = t
                elif new_status == PAY_REVERSED:
                    # Money genuinely leaves. DPD can go BACKWARDS in time as a
                    # result — an account that looked cured stops being cured.
                    # That is real, and it is invisible in a schema that stores
                    # only a payment's current status.
                    st["paid_known"][i] -= rec["amount"]

            # ── resolve promises whose grace has expired ────────────────────
            if ptp_idx.size:
                due = ptp_due + c.ptp_grace_days < t
                if due.any():
                    for k in np.flatnonzero(due):
                        rec = ptp[ptp_row[k]]
                        kept = ptp_paid[k] >= 0.5 * ptp_amt[k]
                        if kept:
                            status = PTP_HONORED
                        else:
                            status = (PTP_RESCHEDULED if rng.random() < 0.18
                                      else PTP_BROKEN)
                        rec["resolved_day"] = t
                        rec["resolved_status"] = status
                    keep = ~due
                    ptp_idx, ptp_due, ptp_amt = ptp_idx[keep], ptp_due[keep], ptp_amt[keep]
                    ptp_born, ptp_paid, ptp_row = (ptp_born[keep], ptp_paid[keep],
                                                   ptp_row[keep])

            # ── bureau refresh ──────────────────────────────────────────────
            stale = active & ((t - st["bureau_day"]) >= c.bureau_refresh_days)
            si = np.flatnonzero(stale & (rng.random(n) < 0.06))
            if si.size:
                # The score moves with realised behaviour, so it carries
                # information — but it is refreshed rarely, so what the model
                # reads is usually months old. That staleness is the point.
                drift = -0.06 * np.clip(dpd[si], 0, 200) + rng.normal(0, 12, si.size)
                # ROUNDED. `Customer.cibil_score` is an INTEGER column, so a
                # fractional score cannot survive materialisation and the panel
                # would carry a precision the product can never hold: measured,
                # 156 of 202 loans disagreed with the adapter purely on the
                # decimal part (701.508 against 702).
                st["cibil_score"][si] = np.round(np.clip(
                    st["cibil_score"][si] + drift, 300, 900))
                st["bureau_day"][si] = t
                for j, i in enumerate(si):
                    bureau.append((int(loan_uid[i]), t, float(st["cibil_score"][i])))

            # ── lifecycle ───────────────────────────────────────────────────
            if t % c.cycle_days == 0 and t > 0:
                st["clean_cycles"] = np.where(dpd == 0, st["clean_cycles"] + 1, 0)
                closed = active & (st["clean_cycles"] >= c.cure_cycles_to_close)
                wo = active & (dpd >= c.writeoff_dpd) & \
                    (rng.random(n) < c.writeoff_hazard_monthly)
                se = active & (dpd >= 91) & \
                    (rng.random(n) < c.settlement_hazard_monthly)
                rc = active & (rng.random(n) < c.recall_hazard_monthly)
                dc = active & (rng.random(n) < c.deceased_hazard_monthly)
                matured = active & (sched.billed_count(t) >= st["n_installments"]) \
                    & (dpd == 0)

                for mask, label in ((closed, "CLOSED"), (matured, "CLOSED"),
                                    (wo, "WRITTEN_OFF"), (se, "SETTLED"),
                                    (rc, "RECALLED"), (dc, "DECEASED")):
                    idx = np.flatnonzero(mask & active)
                    for i in idx:
                        life.append((int(loan_uid[i]), t, label))
                    active[idx] = False

                # Recycle the freed slots as new originations, so the book size
                # and its bucket mix stay stationary instead of draining into a
                # deep-delinquency sink.
                free = np.flatnonzero(~active)
                if free.size:
                    shift = c.covariate_drift * (month - c.months / 2) * 2.2
                    fresh = self._new_accounts(free.size, t, seasoned=False,
                                               score_shift=shift)
                    for key, val in fresh.items():
                        st[key][free] = val
                    loan_uid[free] = np.arange(next_uid, next_uid + free.size)
                    borrower_uid[free] = np.arange(next_borrower,
                                                   next_borrower + free.size)
                    next_uid += free.size
                    next_borrower += free.size
                    agent_of[free] = rng.integers(0, c.n_agents, free.size)
                    active[free] = True
                    _register(free, t)
                    for i in free:
                        life.append((int(loan_uid[i]), t, "OPENED"))

            # ── ground truth, monthly, quarantined ──────────────────────────
            if t % c.cycle_days == 0:
                truth.append(pd.DataFrame({
                    "loan_row": np.arange(n), "day": t,
                    "loan_id": [f"L{u:07d}" for u in loan_uid],
                    "willingness": st["_w"], "capacity": eff_capacity,
                    "reachability": st["_reachability"],
                    "shock_state": st["_shock"],
                    "true_pay_logit": lin,
                }))

        return self._assemble(st, loan_uid, loan_rows, borrower_rows, inst_rows,
                              pay, vis, ptp, life, bureau, truth, agents, n_days)

    # ── prevalence calibration ──────────────────────────────────────────────
    def calibrate(self, *, tol: float = 0.008, max_iter: int = 12,
                  probe_borrowers: int = 900, probe_months: int = 12) -> float:
        """Solve the INTERCEPT for the target material-payment rate.

        PREVALENCE ONLY, AND THAT IS THE WHOLE POINT. The intercept shifts every
        borrower's hazard by the same amount on the log-odds scale, so it moves
        the base rate without touching the relative weight of any driver.
        Discrimination is untouched by construction — you cannot bisect your way
        to a Gini with it.

        That distinction is why this is permitted while tuning `signal_scale`
        against a model result would not be: a book's payment rate is an
        observable property one can target, its Gini is the thing under test.
        """
        from dataclasses import replace

        from app.ml.simulation.ledger.panel import build_panel

        probe = replace(self.cfg, n_borrowers=probe_borrowers, months=probe_months)
        target = self.cfg.target_material_rate

        def rate_at(icept: float) -> float:
            sim = LedgerSimulator(probe)
            panel = build_panel(sim.run(intercept=icept), probe)
            return float(1.0 - panel.y.mean())

        lo, hi = -6.5, -1.5
        best, best_gap = -4.05, 9.9
        for _ in range(max_iter):
            mid = 0.5 * (lo + hi)
            got = rate_at(mid)
            gap = abs(got - target)
            if gap < best_gap:
                best, best_gap = mid, gap
            if gap <= tol:
                break
            if got < target:
                lo = mid       # too few payers -> raise the hazard
            else:
                hi = mid
        logger.info("ledger.calibrate intercept=%.4f material_rate_gap=%.4f",
                    best, best_gap)
        return best

    # ── assembly ────────────────────────────────────────────────────────────
    def _assemble(self, st, loan_uid, loan_rows, borrower_rows, inst_rows,
                  pay, vis, ptp, life, bureau, truth, agents, n_days) -> Ledger:
        """Turn the event buffers into tables.

        EVERY EVENT ALREADY CARRIES ITS OWN `loan_uid`, captured at the moment
        it was written. An earlier draft resolved the loan at assembly time from
        the slot's final occupant, which silently reattributed every event on a
        recycled slot to the loan that replaced it — the accounts that close
        early are exactly the well-behaved ones, so the damage would have landed
        on the good end of the book and flattered the model.
        """
        c = self.cfg
        start = c.start_date

        def d(day):
            return start + timedelta(days=int(day))

        def lid(uid):
            return f"L{int(uid):07d}"

        loans = pd.DataFrame(loan_rows).drop_duplicates("loan_id", keep="last")
        loans["disbursement_date"] = [d(x) for x in loans["opened_day"]]

        payments = pd.DataFrame(pay)
        if len(payments):
            payments["loan_id"] = [lid(u) for u in payments.loan_uid]
            payments["payment_date"] = [d(x) for x in payments.payment_day]
            payments["status_effective_date"] = [
                d(x) for x in payments.status_effective_day]
            payments["payment_id"] = [f"P{i:08d}" for i in range(len(payments))]
            payments["receipt_number"] = ["RCP%08d" % i for i in range(len(payments))]

        visits = pd.DataFrame(vis, columns=["loan_uid", "day", "agent_idx", "met"])
        if len(visits):
            visits["loan_id"] = [lid(u) for u in visits.loan_uid]
            visits["visit_date"] = [d(x) for x in visits.day]
            visits["agent_id"] = agents.agent_id.to_numpy()[visits.agent_idx]
            visits["visit_id"] = [f"V{i:08d}" for i in range(len(visits))]

        ptps = pd.DataFrame(ptp)
        if len(ptps):
            ptps["loan_id"] = [lid(u) for u in ptps.loan_uid]
            ptps["created_date"] = [d(x) for x in ptps.created_day]
            ptps["committed_date"] = [d(x) for x in ptps.committed_day]
            ptps["resolved_date"] = [d(x) if x >= 0 else None
                                     for x in ptps.resolved_day]
            ptps["ptp_id"] = [f"T{i:08d}" for i in range(len(ptps))]

        lifecycle = pd.DataFrame(life, columns=["loan_uid", "day", "event"])
        if len(lifecycle):
            lifecycle["loan_id"] = [lid(u) for u in lifecycle.loan_uid]
            lifecycle["event_date"] = [d(x) for x in lifecycle.day]

        pulls = pd.DataFrame(bureau, columns=["loan_uid", "day", "cibil_score"])
        if len(pulls):
            pulls["loan_id"] = [lid(u) for u in pulls.loan_uid]
            pulls["pull_date"] = [d(x) for x in pulls.day]

        installments = pd.DataFrame(inst_rows)
        installments["due_date"] = [d(x) for x in installments.due_day]

        gt = pd.concat(truth, ignore_index=True) if truth else pd.DataFrame()
        if len(gt):
            gt["as_of_date"] = [d(x) for x in gt.day]

        return Ledger(
            borrowers=pd.DataFrame(borrower_rows).drop_duplicates("borrower_id"),
            loans=loans, installments=installments, bureau_pulls=pulls,
            payments=payments, visits=visits, ptps=ptps, lifecycle=lifecycle,
            agents=agents, ground_truth=gt,
            config={**c.to_dict(), "fingerprint": c.fingerprint(),
                    "n_days": n_days},
        )
