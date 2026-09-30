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
#
# 2026-09-15 — The data-generating process was widened for recovery_risk 2.0.0.
#   Two things were measured on the previous world and both were properties of
#   THIS FILE rather than of borrowers:
#
#   * EVERY STATIC ATTRIBUTE HAD IV ~ 0 — employment_type 0.0001, city 0.0005,
#     is_secured 0.0002, loan_type 0.0014 on the 1.1.0 IV table — because the
#     three latents were drawn independently of all of them. A salaried
#     borrower on a secured loan behaved exactly like a business owner on an
#     unsecured one. The latents now carry small, stated shifts from employment,
#     collateral, rate, debt-to-income, city, residence, phone verification and
#     address vintage. Small on purpose: statics are weak in a behaviour
#     scorecard, and the point is that they are not NOTHING.
#   * THE PRODUCT'S BEHAVIOURAL CHANNELS WERE NOT SIMULATED AT ALL. The visit
#     table carried only `met`; there was no call table; a promise's size did
#     not depend on the borrower; the payment-size mixture was one fixed draw
#     for everybody. So `Visit.outcome`, `CallLog`, `PTP.committed_amount` and
#     the payment ledger's shape — all of which the live schema stores and the
#     adapter can read point-in-time — could not be features, because the panel
#     had nothing to derive them from and the Phase 3 equality test had nothing
#     to compare. They are events now: visit outcomes (RTP / DISPUTE / ...),
#     one call attempt per event with its outcome, a hostility-flag event, a
#     bank fraud flag, capacity-dependent promise and payment sizes, and a
#     due-date payment spike whose sharpness follows willingness.
#
#   NOTHING HERE TARGETS A GINI. `signal_scale` and `observation_noise` are
#   untouched at 1.0. Every new channel is an OBSERVATION of the same three
#   latents through its own noise, so what it adds to achievable discrimination
#   is a consequence to be measured in the v2 training script, not a knob.
#   Every coefficient below is an ASSUMPTION with no external source, like the
#   realism bands in config.py, and is written out rather than tuned.
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

# Visit outcomes the ledger emits. These are `models.visit.VisitOutcome` values
# — the materialiser maps them by name and would fail loudly on a stranger.
# PTP / REVISIT when met, RTP / DISPUTE when met and refusing or contesting,
# NOT_AVAILABLE / ADDRESS_ISSUE when not met. Money outcomes (PAID_FULL,
# PART_PAID) are not written here because the payment is its own event and the
# panel reads the payment ledger, never the visit label, for anything monetary.
VISIT_PTP, VISIT_REVISIT, VISIT_RTP, VISIT_DISPUTE = "PTP", "REVISIT", "RTP", "DISPUTE"
VISIT_NOT_AVAILABLE, VISIT_ADDRESS_ISSUE = "NOT_AVAILABLE", "ADDRESS_ISSUE"
ADVERSE_VISIT_OUTCOMES = (VISIT_RTP, VISIT_DISPUTE)

# Call outcomes: `models.call_log.CallOutcome` values, same rule.
CALL_ANSWERED = "ANSWERED"
CALL_DECLINED = "DECLINED"
CALL_NOT_ANSWERED = ("NO_ANSWER", "SWITCHED_OFF", "BUSY", "DECLINED")
CALL_NOT_ANSWERED_P = (0.55, 0.20, 0.15, 0.10)

FLAG_HOSTILE = "HOSTILE"

# `models.visit.DefaultReason` values an agent records when a met borrower
# explains why they are behind. The four HARDSHIP reasons are observations of a
# CAPACITY SHOCK — the income-loss state the simulator otherwise keeps hidden.
HARDSHIP_REASONS = ("JOB_LOSS", "SALARY_CUT", "BUSINESS_FAILURE", "MEDICAL")
# Structured disposition vocabulary, 2026-09-15 (later still). REFUSES is the
# phone/door equivalent of the RTP visit outcome; DISPUTE and HARDSHIP mirror
# the existing outcome and default-reason vocabularies.
DISP_WILL, DISP_MAY, DISP_NONE, DISP_REFUSES = "WILL_PAY", "MAY_PAY", "NO_COMMITMENT", "REFUSES"
DISP_HARDSHIP, DISP_DISPUTE = "HARDSHIP", "DISPUTE"
DISPOSITIONS = (DISP_WILL, DISP_MAY, DISP_NONE, DISP_REFUSES, DISP_HARDSHIP, DISP_DISPUTE)


def _disposition(c, rng, w, shocked, fraud, n):
    """One recorded disposition per contact: an ordinal read of current
    willingness through `disposition_read_noise`, with hardship and dispute
    overrides. Returns an object array of length n."""
    r = w + rng.normal(0, c.disposition_read_noise, n)
    d = np.where(r >= c.disposition_cut_will, DISP_WILL,
        np.where(r >= c.disposition_cut_may, DISP_MAY,
        np.where(r >= c.disposition_cut_nocommit, DISP_NONE, DISP_REFUSES))).astype(object)
    p_hard = np.where(shocked, c.p_hardship_reported_when_shocked,
                      c.p_hardship_reported_when_not)
    says_hard = rng.random(n) < p_hard
    says_disp = rng.random(n) < (0.03 + 0.12 * fraud)
    d = np.where(says_disp, DISP_DISPUTE, np.where(says_hard, DISP_HARDSHIP, d))
    return d
HARDSHIP_BY_EMPLOYMENT = {"SALARIED": ("JOB_LOSS", "SALARY_CUT", "MEDICAL"),
                          "SELF_EMPLOYED": ("BUSINESS_FAILURE", "MEDICAL", "JOB_LOSS"),
                          "BUSINESS_OWNER": ("BUSINESS_FAILURE", "MEDICAL")}
OTHER_REASONS = ("OVER_LEVERAGED", "OTHER", "MARITAL_DISPUTE")

# ── Static-attribute effects on the latents (ASSUMPTION, 2026-09-15) ────────
# Additive shifts on the [0, 1] latent scale, where the anchor's own spread is
# ~0.25. Deliberately small: a salaried borrower is a little more willing than
# a business owner, not a different species. The sign is the defensible part;
# the magnitude is ours.
EMPLOYMENT_WILLINGNESS = {"SALARIED": 0.04, "SELF_EMPLOYED": -0.02,
                          "BUSINESS_OWNER": -0.03}
EMPLOYMENT_CAPACITY = {"SALARIED": 0.02, "SELF_EMPLOYED": -0.01,
                       "BUSINESS_OWNER": 0.0}
RESIDENCE_REACHABILITY = {"OWNED": 0.05, "RENTED": -0.05, "FAMILY": 0.0}
METRO_CITIES = ("Gurugram", "Delhi")


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
    #: 2026-09-15. One row per call attempt — the ledger's `call_logs`.
    calls: pd.DataFrame = field(default_factory=pd.DataFrame)
    #: 2026-09-15. Flag events: (loan, day, flag). A hostility flag is raised
    #: on a day, so `is_hostile` at any as_of is "was it raised before then".
    flags: pd.DataFrame = field(default_factory=pd.DataFrame)
    config: dict = field(default_factory=dict)

    def tables(self) -> dict[str, pd.DataFrame]:
        return {k: v for k, v in self.__dict__.items()
                if isinstance(v, pd.DataFrame)}


class LedgerSimulator:
    """Generates the book one day at a time."""

    def __init__(self, cfg: LedgerConfig | None = None):
        self.cfg = cfg or LedgerConfig()
        self.rng = np.random.default_rng(self.cfg.seed)

    # ── the calendar ────────────────────────────────────────────────────────
    def _season(self, t: int, month: int) -> float:
        """Additive shift on day t's payment log-odds. A hook so the demo
        generator (app/demo/books.DemoLedgerSimulator) can tie it to the
        calendar; this default is the ledger's own sine, byte-identical to the
        inline term it replaced (tests/test_demo_generator.py)."""
        return self.cfg.seasonality_amplitude * np.sin(2 * np.pi * month / 12.0)

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

        # ── static attributes, drawn first because the latents read them ────
        city = rng.choice(CITIES, n, p=[.30, .24, .18, .14, .14])
        employment = rng.choice(EMPLOYMENT, n, p=[.52, .31, .17])
        residence = rng.choice(RESIDENCE, n, p=[.44, .41, .15])
        phone_verified = (rng.random(n) < 0.78).astype(int)
        address_vintage = rng.integers(1, 200, n)
        income = np.round(np.exp(rng.normal(10.6, 0.55, n)) / 100) * 100
        # Bank-reported fraud flag, known from day 0 like `Customer.fraud_flag`.
        fraud = (rng.random(n) < c.fraud_rate).astype(int)
        # Debt-to-income drives CAPACITY, not willingness: a borrower whose EMI
        # is half their income cannot pay however much they want to.
        dti = np.clip(emi / np.maximum(income, 1.0), 0.0, 1.5)

        # Latent disposition. Correlated with the bureau score but NOT
        # determined by it — the residual is the unobserved variance that stops
        # any observable model from reaching the oracle.
        #
        # 2026-09-15: the statics shift the anchors. The bureau score is still
        # built from `z` alone, so it does not "know" the employment or the
        # collateral — a bureau does not — and those shifts reach the model only
        # through the static columns themselves. That is what makes them
        # features rather than noise.
        z = rng.normal(0, 1, n)
        w_static = (np.vectorize(EMPLOYMENT_WILLINGNESS.get)(employment)
                    + 0.05 * secured                       # collateral at stake
                    - 0.004 * (rate - 16.5)                # priced-for-risk
                    - 0.30 * fraud)                        # fraud does not pay
        # 0.475 rather than 0.5: the static shifts average about +0.025 over
        # the book, and the anchor's mean is held where it was so the
        # prevalence calibration is comparing like with like.
        w_anchor = np.clip(0.475 + 0.19 * z + w_static + rng.normal(0, 0.17, n),
                           0.02, 0.98)
        cibil_true = 300 + 600 * _logistic(1.35 * z + rng.normal(0, 0.62, n))
        cibil = np.clip(np.round(cibil_true + score_shift), 300, 900)

        # Centred on the book's mean DTI (0.234, measured) and sized so the
        # mean and spread match the beta(2.6, 2.2) draw this replaces
        # (0.54 / 0.21): the composition of the book is unchanged, only WHO in
        # it has capacity now depends on something observable.
        capacity = np.clip(
            0.54 + 0.15 * rng.normal(0, 1, n)
            - 0.55 * (dti - 0.234)
            + np.vectorize(EMPLOYMENT_CAPACITY.get)(employment),
            0.03, 0.97)
        # Reachability reads contactability facts a real lender holds and this
        # product does NOT store (residence, phone verification, address
        # vintage are all FEED_ONLY). They stay unobserved variance for the
        # model; only `city` reaches it.
        # Same discipline: mean 0.53 / spread 0.21, as the beta(2.4, 2.1) it
        # replaces, so the RPC band is not moved by the rewrite.
        reachability = np.clip(
            0.41 + 0.20 * rng.normal(0, 1, n)
            + 0.06 * np.isin(city, METRO_CITIES)
            + 0.07 * phone_verified
            + np.vectorize(RESIDENCE_REACHABILITY.get)(residence)
            + 0.0004 * np.minimum(address_vintage, 120),
            0.03, 0.97)

        mob = (rng.integers(3, 40, n) if seasoned
               else rng.integers(0, 3, n))
        mob = np.minimum(mob, tenure - 1)

        # A REFUSAL TRAIT, latent. Refusing to pay is mostly low willingness
        # but not only: some borrowers refuse on principle, some pay quietly
        # however unwilling. The per-borrower residual is unobserved variance
        # that keeps RTP from being willingness in disguise. Centred so a
        # median borrower refuses ~8% of the time they are met, one at the
        # bottom of the willingness scale ~35%.
        rtp_logit = -2.4 - 4.0 * (w_anchor - 0.5) + rng.normal(0, 0.6, n)

        # THE WORLD MUST NOT START CLEAN. A seasoned account opens mid-life:
        # some of its refusals, and some of its income shocks, happened before
        # the ledger began observing it. Starting every flag at 0 and every
        # shock at 0 made the book's bad rate drift upward for a year as both
        # ramped to their stationary shares — a calendar trend the leakage
        # probe then read through the balance features (shuffled-label Gini
        # 0.077 on the 900-borrower panel against a 0.05 ceiling). That is
        # the Phase 4 burn-in defect again, one layer down. So a seasoned
        # account carries its history: a hostility flag with the probability
        # its own refusal trait would have produced over roughly its months in
        # collections, and the shock state at the stationary share
        # in / (in + out). Fresh originations start clean, as they should.
        if seasoned:
            met_visits_so_far = np.round(rng.uniform(0.3, 1.0, n) * mob)
            p_flag = 1.0 - (1.0 - _logistic(rtp_logit) * c.p_hostile_flag_on_rtp
                            ) ** met_visits_so_far
            hostile0 = (rng.random(n) < p_flag).astype(int)
            p_shock = c.income_shock_in / (c.income_shock_in + c.income_shock_out)
            shock0 = (rng.random(n) < p_shock).astype(int)
        else:
            hostile0 = np.zeros(n, dtype=int)
            shock0 = np.zeros(n, dtype=int)

        _ages = rng.integers(22, 66, n)
        target_dpd = self._origination_dpd(
            n, c.dpd_start_mix if seasoned else c.dpd_origination_mix)
        sched, paid = billing.seed_seasoned_book(
            rng, n, target_dpd, mob, emi, tenure, c.cycle_days)

        return {
            "loan_type": lt, "is_secured": secured, "sanction_amount": sanction,
            "tenure_months": tenure, "interest_rate": np.round(rate, 2),
            "emi_amount": emi, "cibil_score": cibil, "monthly_income": income,
            "city": city,
            "employment_type": employment,
            "residence_type": residence,
            "sourcing_channel": rng.choice(CHANNELS, n, p=[.38, .29, .21, .12]),
            "branch_code": rng.choice([f"BR{i:02d}" for i in range(1, 13)], n),
            "age": _ages,
            "dob_day": day - (_ages * 365 + rng.integers(0, 365, n)),
            "address_vintage_months": address_vintage,
            "phone_verified": phone_verified,
            "credit_vintage_months": np.clip(
                rng.integers(2, 220, n), 2, None).astype(int),
            "num_open_loans": rng.integers(1, 7, n),
            "num_enquiries_6m": rng.poisson(1.6, n),
            "other_lender_delinq": (rng.random(n) < 0.24).astype(int),
            "utilization_pct": np.clip(rng.beta(2.4, 2.0, n) * 100, 0, 100),
            "mail_returned_count": rng.poisson(0.35, n),
            "fraud_flag": fraud,
            # latents
            "_w_anchor": w_anchor,
            "_w": w_anchor.copy(),
            "_capacity": capacity,
            "_reachability": reachability,
            "_shock": shock0,
            # A REFUSAL TRAIT, latent. Refusing to pay is mostly low
            # willingness but not only: some borrowers refuse on principle,
            # some pay quietly however unwilling. The per-borrower residual is
            # unobserved variance that keeps RTP from being willingness in
            # disguise. Centred so a median borrower refuses ~8% of the time
            # they are met, one at the bottom of the willingness scale ~35%.
            "_rtp_logit": rtp_logit,
            # observable flag state, raised by an event during the run — or
            # already raised before the ledger began, for a seasoned account
            "_hostile": hostile0,
            # schedule + ledger position
            "first_due_day": sched.first_due_day + day,
            "n_installments": tenure,
            "paid_known": paid,
            "opened_day": np.full(n, day),
            "clean_cycles": np.zeros(n, dtype=int),
            "last_pay_day": np.full(n, -9999),
            "last_met_day": np.full(n, -9999),
            "last_answered_day": np.full(n, -9999),
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

        pay: list = []
        vis: list = []
        ptp: list = []
        life: list = []
        bureau: list = []
        truth: list = []
        calls: list = []
        flags: list = []

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
                    # Static and observable from day 0 — the bank's flag, not
                    # something an agent discovers. `Customer.fraud_flag`.
                    "fraud_flag": int(st["fraud_flag"][i]),
                })
                for k in range(int(st["n_installments"][i])):
                    inst_rows.append({
                        "loan_id": f"L{loan_uid[i]:07d}", "installment_no": k,
                        "due_day": int(st["first_due_day"][i] + c.cycle_days * k),
                        "amount": float(st["emi_amount"][i]),
                    })

        _register(np.arange(n), 0)
        for i in np.flatnonzero(st["_hostile"]):
            # Raised before observation began: day -1, so "before every as_of".
            flags.append((int(loan_uid[i]), -1, FLAG_HOSTILE))


        # Open PTP book, as parallel arrays so resolution is vectorised.
        ptp_idx = np.zeros(0, dtype=int)
        ptp_due = np.zeros(0, dtype=int)
        ptp_amt = np.zeros(0, dtype=float)
        ptp_born = np.zeros(0, dtype=int)
        ptp_paid = np.zeros(0, dtype=float)
        ptp_row = np.zeros(0, dtype=int)

        # Pending payment-status transitions: (day, payment_row, new_status).
        sched_status: dict[int, list[tuple[int, str]]] = {}

        # Which sweep cycle each account was last REACHED in (answered or
        # declined), for `pre_scoring_until_reached`. -1 = never.
        sweep_reached_cycle = np.full(n, -1, dtype=int)

        # Open verbal commitments, parallel arrays like the PTP book. Only
        # ever appended to when `observe_verbal_commitments` is on.
        vc_idx = np.zeros(0, dtype=int)
        vc_due = np.zeros(0, dtype=int)
        vc_born = np.zeros(0, dtype=int)
        vc_paid = np.zeros(0, dtype=float)
        vc_need = np.zeros(0, dtype=float)

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
                                  + rng.normal(0, 0.22 * c.channel_noise_scale, vi.size))
                met = rng.random(vi.size) < p_met
                st["last_met_day"][vi[met]] = t

                # ── the visit's OUTCOME, 2026-09-15 ─────────────────────────
                # A met borrower may refuse (RTP) or contest the debt
                # (DISPUTE). Refusal is an observation of LOW WILLINGNESS
                # through its own noise — the agent hears "no" from someone who
                # was never going to pay, and occasionally from someone who
                # will. Disputes are rare and mostly flat, with fraud-flagged
                # borrowers contesting far more often. Not met: the address is
                # wrong more often for the unreachable.
                p_rtp = _logistic(st["_rtp_logit"][vi]
                                  + 0.35 * np.clip(dpd[vi] / 150, 0, 2)
                                  + 0.9 * st["fraud_flag"][vi]
                                  + rng.normal(0, 0.30 * c.channel_noise_scale, vi.size))
                p_dispute = 0.035 + 0.12 * st["fraud_flag"][vi]
                p_addr = _logistic(-2.6 - 2.0 * (st["_reachability"][vi] - 0.5))
                u = rng.random(vi.size)
                outcome = np.where(
                    met,
                    np.where(u < p_rtp, VISIT_RTP,
                             np.where(u < p_rtp + p_dispute, VISIT_DISPUTE,
                                      VISIT_REVISIT)),
                    np.where(u < p_addr, VISIT_ADDRESS_ISSUE, VISIT_NOT_AVAILABLE))

                # ── promises, only where met and not refusing ───────────────
                eligible = met & (outcome == VISIT_REVISIT)
                mi = vi[eligible]
                promised = np.zeros(vi.size, dtype=bool)
                if mi.size:
                    p_ptp = _logistic(-0.35 + 2.4 * (st["_w"][mi] - 0.5))
                    make = rng.random(mi.size) < p_ptp
                    promised[eligible] = make
                    pi = mi[make]
                    if pi.size:
                        horizon = rng.integers(c.ptp_horizon_lo,
                                               c.ptp_horizon_hi + 1, pi.size)
                        # PROMISE SIZE FOLLOWS CAPACITY. Someone who can pay
                        # promises an instalment or more; someone who cannot
                        # promises what they think they can find. Same mean as
                        # the old U(0.5, 1.6) draw, so the kept-rate band is
                        # not moved by this; only the SPREAD now carries
                        # information.
                        amt = np.minimum(
                            overdue[pi],
                            st["emi_amount"][pi] *
                            np.clip(0.35 + 1.3 * eff_capacity[pi]
                                    + rng.uniform(-0.25, 0.25, pi.size),
                                    0.3, 1.8))
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

                # A met borrower who neither refused nor promised is recorded
                # as REVISIT; one who promised as PTP. The visit row is written
                # AFTER the promise draw so the label and the PTP row agree.
                outcome = np.where(promised, VISIT_PTP, outcome)

                # ── the REASON the borrower gives, when met ─────────────────
                # `Visit.default_reason`. A borrower in an income shock says so
                # more often than not; one who is not occasionally claims
                # hardship anyway. This is the one channel through which the
                # capacity-shock state — otherwise entirely hidden — reaches
                # the observable record, and it reaches it the way it does in
                # the field: through what people tell the agent.
                reason = np.array([None] * vi.size, dtype=object)
                shocked = st["_shock"][vi] == 1
                p_hard = np.where(shocked, c.p_hardship_reported_when_shocked,
                                  c.p_hardship_reported_when_not)
                says_hard = met & (rng.random(vi.size) < p_hard)
                for j in np.flatnonzero(says_hard):
                    opts = HARDSHIP_BY_EMPLOYMENT[st["employment_type"][vi[j]]]
                    reason[j] = opts[int(rng.integers(0, len(opts)))]
                says_other = met & ~says_hard & (rng.random(vi.size) < 0.30)
                for j in np.flatnonzero(says_other):
                    reason[j] = OTHER_REASONS[int(rng.integers(0, len(OTHER_REASONS)))]

                vdisp = np.array([None] * vi.size, dtype=object)
                if c.observe_disposition and met.any():
                    mi_ = np.flatnonzero(met)
                    vdisp[mi_] = _disposition(c, rng, st["_w"][vi[mi_]],
                                              st["_shock"][vi[mi_]] == 1,
                                              st["fraud_flag"][vi[mi_]], mi_.size)
                for j, i in enumerate(vi):
                    vis.append((int(loan_uid[i]), t, int(agent_of[i]),
                                bool(met[j]), str(outcome[j]), reason[j], vdisp[j]))

                # ── hostility flag: a product fact raised by the agent ──────
                # Raised on SOME refusals, once, and never lowered — which is
                # how `Customer.is_hostile` behaves (case_service sets it; no
                # path clears it automatically). It is an EVENT with a day, so
                # the panel and the materialiser can both answer "was it set
                # before as_of".
                rtp = vi[(outcome == VISIT_RTP) & (st["_hostile"][vi] == 0)]
                if rtp.size:
                    raise_flag = rtp[rng.random(rtp.size) < c.p_hostile_flag_on_rtp]
                    st["_hostile"][raise_flag] = 1
                    for i in raise_flag:
                        flags.append((int(loan_uid[i]), t, FLAG_HOSTILE))

            # ── telephony, 2026-09-15 ───────────────────────────────────────
            # One attempt per event; answered or not is an observation of
            # REACHABILITY (mostly) and willingness (a little), degraded by
            # deep delinquency and by hostility. The outcome vocabulary is the
            # product's `CallOutcome` enum.
            p_call = np.where(_bucket_flag(dpd), c.call_hazard_delinquent,
                              c.call_hazard_current)
            called = active & (rng.random(n) < p_call)
            if c.pre_scoring_call_days > 0:
                # The sweep: day (next snapshot - 1 - uid % k) for the live
                # delinquent pool. Deterministic day, no draw of its own.
                to_next = c.cycle_days - (t % c.cycle_days)      # 1..cycle_days
                if to_next <= c.pre_scoring_call_days:
                    k = c.pre_scoring_call_days
                    sweep = (((to_next - 1) - (loan_uid % k)) % k) < c.pre_scoring_call_attempts
                    if c.pre_scoring_until_reached:
                        sweep = sweep & (sweep_reached_cycle != (t // c.cycle_days))
                    called = called | (active & (overdue > 0) & sweep)
            ci = np.flatnonzero(called)
            if ci.size:
                p_ans = _logistic(0.05 + 2.7 * (st["_reachability"][ci] - 0.5)
                                  + 0.45 * (st["_w"][ci] - 0.5)
                                  - 0.30 * np.clip(dpd[ci] / 150, 0, 2)
                                  - 0.60 * st["_hostile"][ci]
                                  + rng.normal(0, 0.25 * c.channel_noise_scale, ci.size))
                answered = rng.random(ci.size) < p_ans
                # ── picked up, then cut short — 2026-09-15 (later) ─────────
                # `CallOutcome.DECLINED`. Before this the outcome was one of
                # the not-answered draws at a flat 10%, saying nothing about
                # anyone. A borrower who does not want the conversation ends
                # it: an observation of LOW willingness through its own noise,
                # the phone's version of a refusal at the door. Gated so the
                # random stream is untouched when off.
                declined = np.zeros(ci.size, dtype=bool)
                if c.observe_declines:
                    p_dec = _logistic(-2.0 - 3.5 * (st["_w"][ci] - 0.5)
                                      - 0.8 * st["_hostile"][ci]
                                      + rng.normal(0, 0.40 * c.channel_noise_scale, ci.size))
                    declined = answered & (rng.random(ci.size) < p_dec)
                    answered = answered & ~declined
                st["last_answered_day"][ci[answered]] = t
                miss = rng.choice(CALL_NOT_ANSWERED, ci.size, p=CALL_NOT_ANSWERED_P)
                if c.observe_declines:
                    # The flat draw no longer owns DECLINED: the vocabulary
                    # has one meaning, and the willingness-driven draw above
                    # is it. Its share folds into NO_ANSWER.
                    miss = np.where(miss == CALL_DECLINED, "NO_ANSWER", miss)
                # WHAT THE BORROWER SAID. `CallLog.payment_intent_signalled`:
                # on an answered call, did they signal they mean to pay? An
                # observation of CURRENT willingness and capacity — the part
                # of the latent that has moved since the arrears history was
                # laid down — through the borrower's own words, with the noise
                # of people saying whatever ends the call.
                p_intent = _logistic(-0.40 + 3.2 * (st["_w"][ci] - 0.5)
                                     + 0.8 * (eff_capacity[ci] - 0.5)
                                     + rng.normal(0, 0.45 * c.channel_noise_scale, ci.size))
                intent = answered & (rng.random(ci.size) < p_intent)
                # ── how long they talked — 2026-09-15 (later) ──────────────
                # `CallLog.duration_seconds`. Log-normal; a willing borrower
                # talks, a hostile one does not, and stating intent takes
                # time. A CONTINUOUS reading with wide noise (sd 0.55 on the
                # log, so a factor of ~1.7 either way) on every answered call,
                # where the intent flag is one bit.
                dur = np.full(ci.size, np.nan)
                if c.observe_call_duration:
                    ln = (4.2 + 0.9 * (st["_w"][ci] - 0.5) + 0.30 * intent
                          - 0.5 * st["_hostile"][ci]
                          + rng.normal(0, 0.55 * c.channel_noise_scale, ci.size))
                    dur = np.where(answered, np.round(np.exp(ln)), np.nan)
                # ── a date named on the phone — 2026-09-15 (later) ────────
                # `CallLog.verbal_payment_date`. A borrower who means to pay
                # names a day; whether money then arrives by it is read from
                # the payment ledger by whoever asks (panel and adapter alike,
                # `commitment_kept_ratio` of an instalment by due + grace).
                # NOTHING in the payment hazard reads it: it is a promise the
                # record keeps, not a lever on the outcome — unlike a doorstep
                # PTP, which the hazard does honour. So its keeping is a read
                # of the same willingness that drives the payment, through
                # the borrower's own follow-through.
                vdue = np.full(ci.size, -1, dtype=int)
                if c.observe_verbal_commitments:
                    p_commit = _logistic(-0.7 + 2.6 * (st["_w"][ci] - 0.5)
                                         + 0.4 * (eff_capacity[ci] - 0.5)
                                         + rng.normal(0, 0.45 * c.channel_noise_scale, ci.size))
                    commit = answered & (rng.random(ci.size) < p_commit)
                    horizon = rng.integers(c.commitment_horizon_lo,
                                           c.commitment_horizon_hi + 1, ci.size)
                    vdue = np.where(commit, t + horizon, -1)
                    ki = ci[commit]
                    if ki.size:
                        vc_idx = np.concatenate([vc_idx, ki])
                        vc_due = np.concatenate([vc_due, vdue[commit]])
                        vc_born = np.concatenate([vc_born, np.full(ki.size, t)])
                        vc_paid = np.concatenate([vc_paid, np.zeros(ki.size)])
                        vc_need = np.concatenate(
                            [vc_need, c.commitment_kept_ratio * st["emi_amount"][ki]])
                cdisp = np.array([None] * ci.size, dtype=object)
                if c.observe_disposition and answered.any():
                    ai_ = np.flatnonzero(answered)
                    cdisp[ai_] = _disposition(c, rng, st["_w"][ci[ai_]],
                                              st["_shock"][ci[ai_]] == 1,
                                              st["fraud_flag"][ci[ai_]], ai_.size)
                if c.pre_scoring_until_reached and                         (c.cycle_days - (t % c.cycle_days)) <= c.pre_scoring_call_days:
                    # Only a contact made INSIDE the sweep window retires the
                    # account from this sweep; an ordinary call earlier in the
                    # cycle does not (the first build let it, and the sweep
                    # reached 44% instead of ~75%).
                    reached_now = ci[answered | declined]
                    sweep_reached_cycle[reached_now] = t // c.cycle_days
                for j, i in enumerate(ci):
                    calls.append((int(loan_uid[i]), t, int(agent_of[i]),
                                  bool(answered[j]),
                                  CALL_ANSWERED if answered[j]
                                  else (CALL_DECLINED if declined[j] else str(miss[j])),
                                  bool(intent[j]) if answered[j] else None,
                                  None if np.isnan(dur[j]) else float(dur[j]),
                                  int(vdue[j]), cdisp[j]))

            # ── payment hazard ──────────────────────────────────────────────
            recent_contact = (t - st["last_met_day"]) <= 14
            recent_call = (t - st["last_answered_day"]) <= c.call_contact_days
            ptp_live = np.zeros(n, dtype=bool)
            if ptp_idx.size:
                live = (ptp_due >= t) & (ptp_born <= t)
                ptp_live[ptp_idx[live]] = True
            # A live verbal commitment: named, within due + grace, not yet
            # met. See LedgerConfig.commitment_hazard_effect.
            vc_live = np.zeros(n, dtype=bool)
            if vc_idx.size:
                live_vc = ((vc_due + c.commitment_grace_days >= t) & (vc_born <= t)
                           & (vc_paid < vc_need))
                vc_live[vc_idx[live_vc]] = True

            season = self._season(t, month)
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
                   # An answered call prompts payment too — weaker than a
                   # doorstep visit, and for fewer days. 2026-09-15.
                   + c.call_contact_effect * recent_call
                   # PAYMENTS CLUSTER ON THE DUE DATE. A bill arrives, a
                   # reminder goes out, salaries land — and the money follows
                   # within days. Drawing payment days from a flat hazard
                   # scatters them uniformly through the cycle instead, which
                   # is what left performing accounts sitting in arrears for
                   # most of every month.
                   #
                   # 2026-09-15: the SHARPNESS of that spike follows
                   # willingness — a disciplined payer pays on the due date, an
                   # erratic one pays when cornered. The mean coefficient at
                   # w = 0.5 is the old 1.10, so prevalence is untouched; what
                   # changes is that the REGULARITY of a borrower's payment
                   # gaps now says something about them.
                   + (0.55 + 1.10 * st["_w"]) *
                   np.exp(-((t - st["first_due_day"]) % c.cycle_days) / 5.0)
                   + 1.20 * ptp_live
                   + c.commitment_hazard_effect * vc_live
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

                # WHAT KIND of payment follows CAPACITY. 2026-09-15. The old
                # draw was one fixed mixture — 42% a whole EMI, 42% a partial,
                # 16% a token — for every borrower, so the SHAPE of someone's
                # payment history said nothing about them. A borrower who can
                # afford it pays the instalment; one who cannot pays what they
                # have. The mixture means at the median capacity (0.54) are
                # the old ones: 0.42 / 0.42 / 0.16.
                cap_q = eff_capacity[qi]
                p_emi = np.clip(0.14 + 0.52 * cap_q, 0.05, 0.85)
                p_tok = np.clip(0.32 - 0.30 * cap_q, 0.03, 0.45)
                uk = rng.random(qi.size)
                kind = np.where(uk < p_emi, 0,
                                np.where(uk < 1.0 - p_tok, 1, 2))
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
                    if vc_idx.size and status == PAY_VERIFIED:
                        hit = (vc_idx == i) & (vc_born <= t) & \
                              (vc_due + c.commitment_grace_days >= t)
                        vc_paid[hit] += amt[j]

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

            # Verbal commitments leave the live book once met or expired;
            # their status is never stored — the panel and the adapter derive
            # it from the payment ledger.
            if vc_idx.size:
                keep_vc = (vc_due + c.commitment_grace_days >= t) & (vc_paid < vc_need)
                if not keep_vc.all():
                    vc_idx, vc_due, vc_born = vc_idx[keep_vc], vc_due[keep_vc], vc_born[keep_vc]
                    vc_paid, vc_need = vc_paid[keep_vc], vc_need[keep_vc]

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
                              pay, vis, ptp, life, bureau, truth, agents, n_days,
                              calls, flags)

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
                  pay, vis, ptp, life, bureau, truth, agents, n_days,
                  calls=None, flags=None) -> Ledger:
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

        visits = pd.DataFrame(vis, columns=["loan_uid", "day", "agent_idx", "met",
                                            "outcome", "default_reason", "disposition"])
        if len(visits):
            visits["loan_id"] = [lid(u) for u in visits.loan_uid]
            visits["visit_date"] = [d(x) for x in visits.day]
            visits["agent_id"] = agents.agent_id.to_numpy()[visits.agent_idx]
            visits["visit_id"] = [f"V{i:08d}" for i in range(len(visits))]

        call_df = pd.DataFrame(calls or [], columns=["loan_uid", "day", "agent_idx",
                                                     "answered", "outcome",
                                                     "payment_intent",
                                                     "duration_seconds",
                                                     "verbal_due_day", "disposition"])
        if len(call_df):
            call_df["loan_id"] = [lid(u) for u in call_df.loan_uid]
            call_df["call_date"] = [d(x) for x in call_df.day]
            call_df["verbal_payment_date"] = [d(x) if x >= 0 else None
                                              for x in call_df.verbal_due_day]
            call_df["agent_id"] = agents.agent_id.to_numpy()[call_df.agent_idx]
            call_df["call_id"] = [f"K{i:08d}" for i in range(len(call_df))]

        flag_df = pd.DataFrame(flags or [], columns=["loan_uid", "day", "flag"])
        if len(flag_df):
            flag_df["loan_id"] = [lid(u) for u in flag_df.loan_uid]
            flag_df["flag_date"] = [d(x) for x in flag_df.day]

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
            agents=agents, ground_truth=gt, calls=call_df, flags=flag_df,
            config={**c.to_dict(), "fingerprint": c.fingerprint(),
                    "n_days": n_days},
        )
