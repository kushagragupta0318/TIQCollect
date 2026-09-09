# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. A standalone, seeded, file-based simulator of an Indian
#   retail collections book, built to support model development.
#
#   WHY A SECOND SIMULATOR, when scripts/generate_synthetic_repayment_history.py
#   already exists and is good. That one simulates a book forward through time
#   *through the real ORM*, calling the production RepaymentService.rescore at
#   each scoring date. That is exactly right for proving the production pipeline
#   is honest, and it must stay. What it cannot do is produce a MODELLING
#   dataset at scale: it needs a live Postgres, and it walks day by day through
#   the ORM, so 5,000 borrowers x 24 months is hours, not minutes.
#
#   This module has one job: emit a point-in-time panel, as files, from a seed,
#   with no infrastructure at all. Same philosophy — latent variables drive the
#   world and are never observable, observables are noisy functions of them, the
#   generator's formula is deliberately not the model's formula — implemented in
#   numpy over a monthly loop.
#
#   THE ACCOUNT LIFECYCLE IS NOT DECORATION. The first version of this file had
#   accounts that could only roll forward: DPD rose by 30 every month a borrower
#   missed, and nothing ever cured, closed, settled or was written off. Measured
#   on 1,500 borrowers x 18 months, the book absorbed into NPA — 19,320 of
#   27,000 rows (71.6%) — and the realised bad rate drifted from the configured
#   0.72 to 0.87. That is the same defect this file exists to avoid: a DPD
#   distribution that collapses to one bucket makes DPD look far more
#   discriminating than it is, and a binner has nothing to bin at the low end.
#   Entry and exit are what keep a book's bucket mix stationary, so they are
#   modelled.
#
#   THE DIFFICULTY IS A DIAL, AND THAT IS THE POINT. `signal_scale` and
#   `observation_noise` set how much of the truth is recoverable. They exist
#   because a synthetic book that yields Gini 0.95 is not a harder version of a
#   real one, it is a different and useless problem.
# ───────────────────────────────────────────────────────────────────────────
"""
Synthetic Indian retail collections book — point-in-time panel generator.

    from app.ml.simulation.book_simulator import BookSimulator, SimConfig
    panel = BookSimulator(SimConfig(n_borrowers=5000, months=24, seed=42)).run()

WHAT COMES OUT
--------------
One row per (loan, month-end observation date). Every feature is computed from
history STRICTLY BEFORE that date; the target is drawn from the month AFTER it.
There is no way to ask this module for a feature "as it is now" — `as_of` is
structural, not a parameter you may forget.

THE HIDDEN WORLD
----------------
Three latent variables drive everything and are never written to the panel:

    willingness   does the borrower intend to pay
    capacity      can they afford to
    agent_skill   per agent, on the logit scale

Observables are noisy views of them: a CIBIL score with a real error term, a DPD
that only loosely tracks willingness, a payment history that is a sequence of
Bernoulli draws. A willing borrower sometimes fails and a difficult one
sometimes pays, so no model — however good — can score much above the ceiling
those noise terms impose. That ceiling is the whole design goal.

TARGET POLARITY, FIXED ONCE
---------------------------
`y` is the RISK event: 1 = did NOT make a material payment in the outcome
window. Higher model score therefore means worse, decile 1 holds the riskiest,
and bad rate must fall monotonically from decile 1 to decile 10. Every
evaluation in ml/pipeline assumes this and nothing re-derives it.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

LOAN_TYPES = ["PERSONAL", "HOME", "AUTO", "GOLD", "BUSINESS",
              "CREDIT_CARD", "EDUCATION", "MICROFINANCE"]
# Which of them are secured. Mirrors ml/eligibility.py's SECURED set rather than
# inventing a second answer to the same question; kept as a literal here only
# because this module must not import the app (it runs without a database).
SECURED_TYPES = {"HOME", "AUTO", "GOLD"}

CITIES = ["Delhi", "Noida", "Gurugram", "Faridabad", "Ghaziabad"]
EMPLOYMENT = ["SALARIED", "SELF_EMPLOYED", "BUSINESS_OWNER", "RETIRED", "HOMEMAKER"]
RESIDENCE = ["OWNED", "RENTED", "FAMILY", "COMPANY_PROVIDED"]
CHANNELS = ["BRANCH", "DSA", "DIGITAL", "TELE"]
BRANCHES = [f"BR{i:03d}" for i in range(1, 19)]

EXIT_ACTIVE, EXIT_CLOSED, EXIT_WRITTEN_OFF, EXIT_SETTLED = 0, 1, 2, 3


@dataclass
class SimConfig:
    """Every knob. Written to the dataset's metadata so a panel is reproducible."""

    n_borrowers: int = 5_000
    months: int = 24
    n_agents: int = 30
    seed: int = 42

    # ── Difficulty dials ────────────────────────────────────────────────────
    # signal_scale multiplies the latent terms in the payment logit: lower means
    # outcomes are driven more by chance and less by who the borrower is, so the
    # achievable Gini falls. observation_noise scales the error on every
    # observable proxy: higher means the model sees a blurrier view of the same
    # truth. They are separate because they degrade a model for different
    # reasons and a real book has both.
    signal_scale: float = 1.0
    observation_noise: float = 1.0

    # Target rate for the RISK class (no material payment in the window). Indian
    # field collections on a delinquent book resolves roughly 25-35% of accounts
    # per cycle, so the risk event is the majority class. The intercept is
    # SOLVED for this rather than set by hand — see _solve_intercept.
    target_bad_rate: float = 0.70

    # ── Book composition at t0 ──────────────────────────────────────────────
    # A real book is NOT all delinquent. The demo seed starts at DPD 35, which
    # both inflates DPD's apparent power and leaves a binner nothing to bin at
    # the low end. Here the book spans current to deep NPA, and the lifecycle
    # below keeps it that way.
    dpd_start_mix: dict = field(default_factory=lambda: {
        "CURRENT": 0.24, "BUCKET_1": 0.21, "BUCKET_2": 0.20,
        "BUCKET_3": 0.17, "NPA": 0.18,
    })
    # New accounts arrive mostly clean, as originations do.
    dpd_origination_mix: dict = field(default_factory=lambda: {
        "CURRENT": 0.62, "BUCKET_1": 0.27, "BUCKET_2": 0.11,
    })

    # ── Account lifecycle ───────────────────────────────────────────────────
    cure_months_to_close: int = 3       # consecutive clean months -> closed
    # A real book does not let accounts sit at 400 DPD forever: past roughly
    # 180-240 days they are settled, written off or sold. Without that the
    # NPA bucket becomes a sink — measured at 51.7% of all rows before these
    # were tightened, against the 25-30% a live delinquent book carries.
    writeoff_dpd: float = 210.0         # eligible for write-off beyond this
    writeoff_hazard: float = 0.30       # monthly probability once eligible
    settlement_hazard: float = 0.07     # monthly, for deep-bucket accounts

    # ── Realistic imperfection ──────────────────────────────────────────────
    missing_bureau_rate: float = 0.11      # MCAR on bureau pulls
    thin_file_missing_boost: float = 0.35  # MNAR: thin files miss more often
    agent_churn_per_year: float = 0.18     # joiners/leavers, for cold-start

    # ── Seasonality and shock ───────────────────────────────────────────────
    # A festival uplift and one adverse month, so PSI/CSI monitoring has
    # something real to detect and out-of-time validation is a genuine test.
    # BORROWERS' CIRCUMSTANCES CHANGE, and that is what stops DPD from being a
    # sufficient statistic. With static latents, accumulated DPD is a near-
    # perfect summary of a borrower's type: measured, DPD ALONE scored Gini
    # 0.496 against a fifty-feature model's 0.543. A job loss, a recovery, a
    # medical event — real ones drift, so a six-month-old delinquency record is
    # a decayed signal and recent behaviour carries information DPD does not.
    # Monthly shock sd, applied with mean reversion toward the borrower's
    # original disposition.
    latent_drift: float = 0.075
    latent_reversion: float = 0.055

    seasonality_amplitude: float = 0.18
    shock_month_index: int = 17
    shock_magnitude: float = -0.55

    start_date: date = date(2024, 10, 1)

    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _logistic(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -35, 35)))


def _month_end(d: date, k: int) -> date:
    """k months after d, snapped to the last day of that month."""
    y, m = divmod((d.year * 12 + d.month - 1) + k, 12)
    m += 1
    nxt_y, nxt_m = (y + 1, 1) if m == 12 else (y, m + 1)
    return date(nxt_y, nxt_m, 1) - timedelta(days=1)


def dpd_bucket_of(dpd) -> str:
    """DPD -> bucket. Mirrors models/loan.dpd_bucket_for.

    Restated here ONLY because this module deliberately imports nothing from
    `app` — it must run with no database and no settings. That is a real
    exception to the one-definition rule, so tests assert the two agree across
    the whole 0-400 range rather than trusting this comment. If that test fails,
    this function is wrong, not the model's.
    """
    if dpd <= 0:
        return "CURRENT"
    if dpd <= 30:
        return "BUCKET_1"
    if dpd <= 60:
        return "BUCKET_2"
    if dpd <= 90:
        return "BUCKET_3"
    return "NPA"


_BUCKET_SPAN = {"CURRENT": (0, 0), "BUCKET_1": (1, 30), "BUCKET_2": (31, 60),
                "BUCKET_3": (61, 90), "NPA": (91, 300)}


# ---------------------------------------------------------------------------
# The simulator
# ---------------------------------------------------------------------------

class BookSimulator:
    """Simulates a collections book forward through time and emits a panel.

    The loop is monthly and strictly forward: state at month m is written to the
    panel, then the month m+1 outcome is drawn, then state advances. Nothing is
    ever reconstructed backwards, which is what makes the panel point-in-time by
    construction rather than by discipline.

    Slots are recycled. The book holds a constant number of live accounts; when
    one exits, a freshly originated account takes its slot and its history is
    zeroed. That keeps the arrays a fixed shape (fast) while giving the book the
    entry/exit churn that keeps its bucket mix stationary.
    """

    def __init__(self, cfg: SimConfig | None = None):
        self.cfg = cfg or SimConfig()
        self.rng = np.random.default_rng(self.cfg.seed)
        self.truth: dict = {}
        self._loan_counter = 0
        self.exit_log: dict[str, int] = {"closed": 0, "written_off": 0, "settled": 0}

    # ── Agents ──────────────────────────────────────────────────────────────
    def _make_agents(self) -> pd.DataFrame:
        rng = self.rng
        n = self.cfg.n_agents
        # Skill on the logit scale, sd 0.55 — the same spread the DB-backed
        # generator plants, so the two experiments stay comparable.
        skill = rng.normal(0.0, 0.55, n)
        joined = rng.integers(-36, self.cfg.months, n)   # some join mid-book
        return pd.DataFrame({
            "agent_id": [f"AG{i:03d}" for i in range(n)],
            "agent_skill": skill,
            "join_month": joined,
            "agent_tier": rng.choice(["TIER_1", "TIER_2", "TIER_3"], n, p=[.3, .45, .25]),
        })

    # ── Account creation (initial book AND replacements) ────────────────────
    def _new_accounts(self, n: int, *, at_origination: bool) -> dict:
        """Static attributes plus opening state for n accounts.

        at_origination=False gives the seasoned mix the book starts with;
        True gives freshly booked accounts, which are mostly clean.
        """
        cfg, rng = self.cfg, self.rng
        noise = cfg.observation_noise

        # ── Latents. The hidden world, never written to the panel. ──────────
        willingness = rng.beta(2.2, 2.4, n)
        capacity = rng.beta(2.0, 2.6, n)
        # Correlated but far from identical: the same life events damage both.
        capacity = np.clip(0.72 * capacity + 0.28 * willingness
                           + rng.normal(0, 0.07, n), 0.01, 0.99)
        # REACHABILITY IS A THIRD, PERSISTENT LATENT — how findable this borrower
        # is at their recorded address. It exists because without it a
        # contactability model has nothing to learn: the first contact_risk run
        # scored Gini 0.168 and selected three features, because being met
        # depended only on willingness (which drifts month to month) and DPD.
        # In a real book "hard to find" is a stable property of a household —
        # wrong address, absconded, works away, gated society — and it is what
        # makes a borrower's own contact HISTORY predictive of their next
        # contact. Unlike willingness it barely drifts, which is the point.
        reachability = np.clip(rng.beta(2.4, 2.0, n), 0.01, 0.99)

        # CIBIL is the classic noisy proxy: real signal, real error. A 55-point
        # sd on a 300-900 scale is roughly what a bureau score's disagreement
        # with true intent looks like.
        cibil = np.clip(480 + 330 * (0.62 * willingness + 0.38 * capacity)
                        + rng.normal(0, 55 * noise, n), 300, 900).round()

        loan_type = rng.choice(LOAN_TYPES, n, p=[.26, .09, .13, .11, .12, .15, .06, .08])
        secured = np.isin(loan_type, list(SECURED_TYPES))

        # Log-normal sanction, so the book has the heavy right tail a real one has.
        sanction = np.exp(rng.normal(np.where(secured, 11.6, 10.4), 0.75, n))
        sanction = np.clip(np.round(sanction / 1000) * 1000, 15_000, 6_000_000)

        tenure = np.where(secured,
                          rng.choice([60, 84, 120, 180, 240], n),
                          rng.choice([12, 18, 24, 36, 48], n)).astype(float)
        rate = np.clip(np.where(secured, rng.normal(9.4, 1.5, n),
                                rng.normal(16.8, 3.2, n)), 7.0, 34.0)
        r = rate / 1200.0
        emi = np.round(sanction * r * (1 + r) ** tenure / ((1 + r) ** tenure - 1), 0)

        if at_origination:
            months_on_book = rng.integers(1, 7, n).astype(float)
            mix = cfg.dpd_origination_mix
        else:
            months_on_book = rng.integers(4, np.maximum(5, tenure - 2)).astype(float)
            mix = cfg.dpd_start_mix

        buckets = list(mix.keys())
        probs = np.array(list(mix.values()), dtype=float)
        probs = probs / probs.sum()
        start_bucket = rng.choice(buckets, n, p=probs)
        dpd = np.zeros(n)
        for b in buckets:
            lo, hi = _BUCKET_SPAN[b]
            m = start_bucket == b
            if m.sum():
                dpd[m] = rng.integers(lo, hi + 1, m.sum()) if hi > lo else lo
        # Delinquency is not random with respect to the latents — that is the
        # signal the model is meant to find. Nudge the unwilling deeper.
        dpd = np.clip(dpd + (0.5 - willingness) * 45 * cfg.signal_scale, 0, 400).round()

        income = np.round(np.clip(np.exp(rng.normal(10.9 + 0.9 * capacity, 0.55, n)),
                                  12_000, 900_000), -2)
        paid_frac = np.clip(months_on_book / np.maximum(tenure, 1), 0, 0.97)
        principal = np.round(sanction * (1 - paid_frac * rng.uniform(0.75, 1.0, n)), 0)
        overdue = np.round(emi * (np.floor(dpd / 30) + 1) * rng.uniform(0.85, 1.15, n), 0)
        penal = np.round(overdue * rng.uniform(0.01, 0.06, n), 0)

        ids = [f"L{self._loan_counter + i:07d}" for i in range(n)]
        bids = [f"C{self._loan_counter + i:07d}" for i in range(n)]
        self._loan_counter += n

        return {
            "loan_id": np.array(ids, dtype=object),
            "borrower_id": np.array(bids, dtype=object),
            "_willingness": willingness,
            "_capacity": capacity,
            "_reachability": reachability,
            "_willingness_anchor": willingness.copy(),
            "_capacity_anchor": capacity.copy(),
            "cibil_score": cibil,
            "loan_type": loan_type,
            "is_secured": secured.astype(int),
            "sanction_amount": sanction,
            "tenure_months": tenure,
            "interest_rate": rate.round(2),
            "emi_amount": emi,
            "months_on_book": months_on_book,
            "dpd": dpd,
            "age": np.clip(rng.normal(38, 10, n), 21, 72).round(),
            "city": rng.choice(CITIES, n, p=[.30, .22, .24, .12, .12]),
            "employment_type": rng.choice(EMPLOYMENT, n, p=[.44, .28, .14, .07, .07]),
            # Residence correlates with reachability without determining it —
            # an owner is easier to find than a tenant, but not reliably so.
            # This gives the model one weak OBSERVABLE hook on a latent it
            # otherwise only sees through contact history.
            "residence_type": np.where(
                rng.random(n) < 0.55,
                np.where(reachability > 0.5, "OWNED", "RENTED"),
                rng.choice(RESIDENCE, n, p=[.38, .42, .16, .04])),
            "sourcing_channel": rng.choice(CHANNELS, n, p=[.34, .31, .22, .13]),
            "branch_code": rng.choice(BRANCHES, n),
            # CONTACTABILITY DATA THE BANK ACTUALLY HOLDS. A real book records how
            # old the KYC address is, whether the phone has been verified, and
            # how many times mail has come back undelivered. They were missing
            # here, which is why the first contact_risk runs had nothing to read
            # but a noisy 6-month contact ratio and scored Gini 0.168-0.185 —
            # below the 0.25-0.40 a right-party-contact model reaches in
            # practice. Each is a noisy view of the reachability latent, not a
            # copy of it.
            "address_vintage_months": np.clip(
                rng.gamma(2.0, 14.0 * (0.4 + reachability), n)
                + rng.normal(0, 6 * noise, n), 0, 400).round(),
            "phone_verified": (rng.random(n) < (0.30 + 0.55 * reachability)).astype(int),
            "mail_returned_count": rng.poisson(
                np.clip(1.6 * (1.0 - reachability), 0.02, 3.0), n).astype(float),
            "num_open_loans": (rng.poisson(1.7, n) + 1).astype(float),
            "num_enquiries_6m": rng.poisson(2.1 + 2.6 * (1 - willingness), n).astype(float),
            "credit_vintage_months": rng.integers(6, 220, n).astype(float),
            "monthly_income": income,
            "utilization_pct": np.clip(rng.beta(2, 2, n) * 100 + (1 - capacity) * 28
                                       + rng.normal(0, 9 * noise, n), 0, 140).round(2),
            "other_lender_delinq": (rng.random(n) < (0.10 + 0.34 * (1 - willingness))).astype(int),
            "outstanding_principal": principal,
            "overdue_amount": overdue,
            "penal_charges": penal,
        }

    # ── Payment probability ─────────────────────────────────────────────────
    def _pay_logit(self, st: dict, month_idx: int, agent_skill: np.ndarray,
                   intercept: float, ptp_hist: np.ndarray,
                   pay_hist: np.ndarray, rng=None,
                   bounce_hist: np.ndarray | float = 0.0) -> np.ndarray:
        """The generator's formula. Deliberately NOT the model's formula.

        It reads latents the model can never see (willingness, capacity, the
        agent's true skill) and history the model sees only as blurred
        aggregates. Recovering the planted relationship from the observables is
        the inference problem being posed.
        """
        cfg = self.cfg
        s = cfg.signal_scale
        n = len(st["dpd"])

        dpd_norm = np.clip(st["dpd"] / 120.0, 0, 2.0)
        arrears = np.clip(st["overdue_amount"] / np.maximum(st["emi_amount"], 1), 0, 12) / 6.0
        dti = np.clip(st["emi_amount"] / np.maximum(st["monthly_income"], 1), 0, 2.0)

        season = cfg.seasonality_amplitude * math.sin(2 * math.pi * (month_idx % 12) / 12.0)
        shock = cfg.shock_magnitude if month_idx == cfg.shock_month_index else 0.0

        # WHY THE DPD COEFFICIENT IS MODEST AND THE OBSERVED-BEHAVIOUR ONES ARE
        # NOT. DPD sits downstream of willingness and of the roll process, so a
        # large direct coefficient makes it a sufficient statistic for
        # everything else. Measured at -1.15: DPD ALONE scored Gini 0.498
        # against the full model's 0.523, i.e. fifty features bought 0.025.
        # Real behaviour scorecards do not look like that — DPD alone runs
        # around 0.35 and the combination reaches 0.50, because the lift comes
        # from many exactly-observed fields, not one.
        #
        # So the terms below that the model can see WITHOUT noise — bounce
        # counts, other-lender delinquency, enquiry velocity, utilisation, DTI —
        # carry real weight. The latents keep the ceiling low; these give a good
        # model something to find beneath it.
        util = np.clip(np.nan_to_num(st["utilization_pct"], nan=55.0) / 100.0, 0, 1.4)
        enq = np.clip(np.nan_to_num(st["num_enquiries_6m"], nan=2.0), 0, 12) / 6.0

        return (intercept
                + s * 1.45 * (st["_willingness"] - 0.5)
                + s * 1.20 * (st["_capacity"] - 0.5)
                - s * 0.70 * dpd_norm
                - s * 0.45 * arrears
                - s * 0.75 * (dti - 0.35)
                + s * 1.05 * (ptp_hist - 0.5)
                + s * 1.10 * (pay_hist - 0.5)
                - s * 0.55 * st["other_lender_delinq"]
                - s * 0.40 * (util - 0.55)
                - s * 0.30 * (enq - 0.35)
                - s * 0.22 * np.clip(bounce_hist, 0, 6)
                + s * 0.45 * agent_skill
                + season + shock
                + (rng or self.rng).normal(0, 0.45, n))   # irreducible event noise

    def _draw_outcome(self, st: dict, month_idx: int, agent_skill: np.ndarray,
                      intercept: float, ptp_hist: np.ndarray, pay_hist: np.ndarray,
                      rng=None, bounce_hist: np.ndarray | float = 0.0
                      ) -> tuple[np.ndarray, np.ndarray]:
        """One month's payment draw. Returns (recovered, amount_paid).

        WHAT A BORROWER PAYS IS BOUNDED BY WHAT THEY CAN AFFORD, not by what
        they owe. The first version drew `amount_paid = share * overdue_amount`,
        so a deep-NPA account with six cycles of arrears paid six times as much
        as a one-cycle account for the same `share` — and since "recovered"
        compares against roughly one cycle's demand, arrears made the target
        EASIER to hit the deeper the account sat. Measured on 2,000 borrowers x
        24 months, that inverted the very gradient the model exists to find:
        bad rate ran 0.867 for CURRENT against 0.790 for BUCKET_2, and the five
        buckets spanned 0.79-0.93 with no order to them.

        Affordability is drawn in EMI units from a Gamma whose scale rises with
        latent capacity, then capped by what is actually outstanding. Whether a
        payment happens at all stays governed by the logit, which is where DPD,
        arrears and the agent enter — so the DPD gradient survives to the
        outcome instead of being cancelled by the amount.
        """
        rng = rng or self.rng
        n = len(st["dpd"])
        p_pay = _logistic(self._pay_logit(st, month_idx, agent_skill, intercept,
                                          ptp_hist, pay_hist, rng=rng,
                                          bounce_hist=bounce_hist))
        pays = rng.random(n) < p_pay
        afford_units = np.clip(rng.gamma(2.0, 0.55 + 0.75 * st["_capacity"], n), 0.05, 6.0)
        afford = st["emi_amount"] * afford_units
        amount_paid = np.where(pays, np.minimum(st["overdue_amount"], afford), 0.0)
        cycle_demand = np.minimum(np.maximum(st["overdue_amount"], 1.0), st["emi_amount"])
        recovered = amount_paid >= 0.8 * cycle_demand
        return recovered, np.round(amount_paid, 0)

    def _solve_intercept(self, st: dict, agent_skill: np.ndarray) -> float:
        """Bisect the intercept so the realised bad rate hits the target.

        THE BISECTION RUNS THE ACTUAL OUTCOME DRAW, not mean(p_pay). Solving on
        the payment probability alone was out by 18 points: recovery needs a
        payment to happen AND to be large enough, so a book calibrated to
        p_pay = 0.30 realised a 0.88 bad rate. Anything that solves for a
        quantity other than the one it reports is guessing.

        A dedicated Generator is used so calibration does not consume the main
        random stream — otherwise the panel would depend on how many bisection
        steps happened to run.
        """
        n = len(st["dpd"])
        neutral = np.full(n, 0.5)
        lo, hi = -10.0, 10.0
        for _ in range(40):
            mid = (lo + hi) / 2.0
            cal = np.random.default_rng(self.cfg.seed + 977)
            recovered, _ = self._draw_outcome(st, 0, agent_skill, mid,
                                              neutral, neutral, rng=cal)
            if float((~recovered).mean()) > self.cfg.target_bad_rate:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    # ── Main loop ───────────────────────────────────────────────────────────
    def run(self, intercept_offset: float = 0.0) -> pd.DataFrame:
        cfg, rng = self.cfg, self.rng
        n = cfg.n_borrowers
        agents = self._make_agents()

        st = self._new_accounts(n, at_origination=False)

        # Rolling history, carried across months and zeroed when a slot recycles.
        H = cfg.months + 1
        hist_paid = np.zeros((n, H))
        hist_due = np.zeros((n, H))
        hist_visit = np.zeros((n, H))
        hist_met = np.zeros((n, H))
        hist_ptp_set = np.zeros((n, H))
        hist_ptp_kept = np.zeros((n, H))
        hist_bounce = np.zeros((n, H))

        days_since_pay = rng.integers(5, 190, n).astype(float)
        days_since_contact = rng.integers(2, 95, n).astype(float)
        consecutive_miss = np.floor(st["dpd"] / 30.0)
        clean_months = np.zeros(n)
        max_dpd_seen = st["dpd"].copy()
        times_30p = (st["dpd"] > 30).astype(float)
        times_60p = (st["dpd"] > 60).astype(float)
        months_since_first_delinq = np.where(st["dpd"] > 0, consecutive_miss, 0.0)
        distinct_agents = np.ones(n)
        agent_idx = rng.integers(0, cfg.n_agents, n)

        intercept = self._solve_intercept(
            st, agents["agent_skill"].to_numpy()[agent_idx]) + intercept_offset
        self.truth = {
            "intercept": float(intercept),
            "intercept_offset": float(intercept_offset),
            "agent_skill": {a: float(s) for a, s in
                            zip(agents["agent_id"], agents["agent_skill"])},
            "config": asdict(cfg),
            "note": ("Latent variables. NEVER join these to the panel — they are "
                     "the answer the model is supposed to infer from observables."),
        }

        rows: list[pd.DataFrame] = []

        for m in range(cfg.months):
            as_of = _month_end(cfg.start_date, m)
            active_agent = agents["join_month"].to_numpy()[agent_idx] <= m

            # ── Observable features, from history STRICTLY BEFORE as_of ─────
            lo3, lo6, lo12 = max(0, m - 3), max(0, m - 6), max(0, m - 12)

            def _ratio(a, b, prior=0.5):
                out = np.full(len(a), prior, dtype=float)
                nz = b > 0
                out[nz] = np.clip(a[nz] / b[nz], 0, 1.5)
                return out

            paid_ratio_3m = _ratio(hist_paid[:, lo3:m].sum(1), hist_due[:, lo3:m].sum(1))
            paid_ratio_6m = _ratio(hist_paid[:, lo6:m].sum(1), hist_due[:, lo6:m].sum(1))
            paid_ratio_12m = _ratio(hist_paid[:, lo12:m].sum(1), hist_due[:, lo12:m].sum(1))
            visits_3m = hist_visit[:, lo3:m].sum(1)
            visits_6m = hist_visit[:, lo6:m].sum(1)
            met_6m = hist_met[:, lo6:m].sum(1)
            contact_rate_6m = _ratio(met_6m, visits_6m, prior=0.45)
            ptp_set_6m = hist_ptp_set[:, lo6:m].sum(1)
            ptp_kept_6m = hist_ptp_kept[:, lo6:m].sum(1)
            ptp_kept_ratio = _ratio(ptp_kept_6m, ptp_set_6m, prior=0.5)
            bounce_6m = hist_bounce[:, lo6:m].sum(1)

            dpd = st["dpd"]
            emi = st["emi_amount"]
            total_out = st["outstanding_principal"] + st["overdue_amount"] + st["penal_charges"]

            frame = pd.DataFrame({
                "loan_id": st["loan_id"],
                "borrower_id": st["borrower_id"],
                "as_of_date": as_of,
                "month_index": m,
                "agent_id": agents["agent_id"].to_numpy()[agent_idx],
                "agent_tier": agents["agent_tier"].to_numpy()[agent_idx],

                # bureau / static
                "cibil_score": st["cibil_score"],
                "credit_vintage_months": st["credit_vintage_months"],
                "num_open_loans": st["num_open_loans"],
                "num_enquiries_6m": st["num_enquiries_6m"],
                "other_lender_delinq": st["other_lender_delinq"],
                "address_vintage_months": st["address_vintage_months"],
                "phone_verified": st["phone_verified"],
                "mail_returned_count": st["mail_returned_count"],
                "utilization_pct": st["utilization_pct"],
                "thin_file": (st["credit_vintage_months"] < 24).astype(int),

                # loan
                "loan_type": st["loan_type"],
                "is_secured": st["is_secured"],
                "sanction_amount": st["sanction_amount"],
                "emi_amount": emi,
                "tenure_months": st["tenure_months"],
                "interest_rate": st["interest_rate"],
                "months_on_book": st["months_on_book"],
                "sourcing_channel": st["sourcing_channel"],
                "branch_code": st["branch_code"],

                # delinquency
                "dpd": dpd,
                "dpd_bucket": [dpd_bucket_of(d) for d in dpd],
                "max_dpd_12m": max_dpd_seen,
                "times_30plus_12m": times_30p,
                "times_60plus_12m": times_60p,
                "months_since_first_delinq": months_since_first_delinq,
                "consecutive_misses": consecutive_miss,
                "outstanding_principal": st["outstanding_principal"],
                "overdue_amount": st["overdue_amount"],
                "penal_charges": st["penal_charges"],
                "total_outstanding": total_out,
                "arrears_ratio": np.round(st["overdue_amount"] / np.maximum(emi, 1), 3),
                "penal_ratio": np.round(st["penal_charges"] / np.maximum(total_out, 1), 4),
                "outstanding_to_sanction": np.round(
                    st["outstanding_principal"] / np.maximum(st["sanction_amount"], 1), 3),

                # payment behaviour
                "paid_ratio_3m": np.round(paid_ratio_3m, 3),
                "paid_ratio_6m": np.round(paid_ratio_6m, 3),
                "paid_ratio_12m": np.round(paid_ratio_12m, 3),
                "days_since_last_payment": days_since_pay,
                "bounce_count_6m": bounce_6m,

                # contact / field
                "visits_3m": visits_3m,
                "visits_6m": visits_6m,
                "contact_rate_6m": np.round(contact_rate_6m, 3),
                "days_since_last_contact": days_since_contact,
                "distinct_agents_6m": distinct_agents,

                # promises
                "ptp_set_6m": ptp_set_6m,
                "ptp_kept_6m": ptp_kept_6m,
                "ptp_kept_ratio": np.round(ptp_kept_ratio, 3),

                # demographic
                "age": st["age"],
                "city": st["city"],
                "employment_type": st["employment_type"],
                "residence_type": st["residence_type"],
                "monthly_income": st["monthly_income"],
                "dti_ratio": np.round(emi / np.maximum(st["monthly_income"], 1), 3),
            })

            # ── Draw the outcome for the window after as_of ─────────────────
            skill = agents["agent_skill"].to_numpy()[agent_idx] * active_agent
            amount_due = st["overdue_amount"]
            recovered, amount_paid = self._draw_outcome(
                st, m, skill, intercept, ptp_kept_ratio, paid_ratio_6m,
                bounce_hist=bounce_6m)
            paid_flag = amount_paid > 0
            frame["y"] = (~recovered).astype(int)
            frame["recovered_amount"] = amount_paid

            # Field activity for the month, driven by contactability.
            visited = rng.random(n) < np.clip(0.25 + 0.55 * (dpd > 30), 0, 1)
            # Reachability carries most of the weight, willingness some, DPD a
            # little: a borrower who is easy to find stays easy to find, which
            # is what makes their own contact history worth reading.
            p_met = _logistic(0.20
                              + 3.2 * (st["_reachability"] - 0.5)
                              + 0.7 * (st["_willingness"] - 0.5)
                              - 0.4 * np.clip(dpd / 150, 0, 2)
                              + rng.normal(0, 0.20, n))
            met = visited & (rng.random(n) < p_met)
            ptp_set = met & (rng.random(n) < 0.42)
            ptp_kept = ptp_set & paid_flag
            frame["visit_made"] = visited.astype(int)
            frame["customer_met"] = met.astype(int)
            frame["ptp_set"] = ptp_set.astype(int)
            frame["ptp_kept"] = ptp_kept.astype(int)

            rows.append(frame)

            # ── Advance the world to month m+1 ──────────────────────────────
            hist_paid[:, m] = amount_paid
            hist_due[:, m] = amount_due
            hist_visit[:, m] = visited
            hist_met[:, m] = met
            hist_ptp_set[:, m] = ptp_set
            hist_ptp_kept[:, m] = ptp_kept
            hist_bounce[:, m] = (~paid_flag) & (rng.random(n) < 0.30)

            days_since_pay = np.where(paid_flag, rng.integers(1, 28, n), days_since_pay + 30)
            days_since_contact = np.where(met, rng.integers(1, 28, n),
                                          days_since_contact + 30)
            consecutive_miss = np.where(paid_flag, 0, consecutive_miss + 1)

            # DPD rolls back by whole cycles actually paid, and forward by one
            # when nothing material arrives. Roll-back is what a real book does
            # and what keeps the bucket distribution from absorbing into NPA.
            cycles_cleared = np.floor(amount_paid / np.maximum(emi, 1))
            st["dpd"] = np.clip(np.where(recovered,
                                         dpd - 30.0 * np.maximum(cycles_cleared, 1),
                                         dpd + 30.0), 0, 900)
            clean_months = np.where(st["dpd"] <= 0, clean_months + 1, 0)

            max_dpd_seen = np.maximum(max_dpd_seen, st["dpd"])
            times_30p = times_30p + (st["dpd"] > 30)
            times_60p = times_60p + (st["dpd"] > 60)
            months_since_first_delinq = np.where(st["dpd"] > 0,
                                                 months_since_first_delinq + 1,
                                                 months_since_first_delinq)

            # EVERY ACTIVE LOAN BILLS A CYCLE, whether or not the last one was
            # paid. This previously billed only when the month was NOT
            # recovered, so an account that cleared its arrears fell to
            # overdue = 0 — and then had nothing to pay next month, which the
            # target scored as "did not recover". Measured: it drove the CURRENT
            # bucket to a 0.840 bad rate, WORSE than BUCKET_1 (0.632) and
            # BUCKET_2 (0.679), inverting the gradient at exactly the end of the
            # book where a scorecard needs it most. A performing account is one
            # that pays its cycle, not one with nothing due.
            st["overdue_amount"] = np.maximum(
                0.0, st["overdue_amount"] - amount_paid) + emi
            st["outstanding_principal"] = np.maximum(
                0, st["outstanding_principal"] - amount_paid * 0.65)
            st["penal_charges"] = st["penal_charges"] + np.where(
                recovered, 0, np.round(emi * 0.012, 0))
            st["months_on_book"] = st["months_on_book"] + 1

            # Latent drift: an Ornstein-Uhlenbeck-ish walk back toward the
            # borrower's own baseline. Applied AFTER the outcome for month m is
            # drawn, so the panel row and the outcome it is labelled with are
            # always the same world-state.
            for key in ("_willingness", "_capacity"):
                st[key] = np.clip(
                    (1.0 - cfg.latent_reversion) * st[key]
                    + cfg.latent_reversion * st[key + "_anchor"]
                    + rng.normal(0, cfg.latent_drift, n), 0.01, 0.99)

            reassign = rng.random(n) < 0.06
            agent_idx = np.where(reassign, rng.integers(0, cfg.n_agents, n), agent_idx)
            distinct_agents = np.minimum(distinct_agents + reassign, 6)

            # ── Exits, then replacement by fresh originations ───────────────
            closed = (clean_months >= cfg.cure_months_to_close) | \
                     (st["outstanding_principal"] <= 0)
            wo = (st["dpd"] >= cfg.writeoff_dpd) & (rng.random(n) < cfg.writeoff_hazard)
            settled = (st["dpd"] >= 120) & (rng.random(n) < cfg.settlement_hazard)
            exiting = closed | wo | settled
            self.exit_log["closed"] += int(closed.sum())
            self.exit_log["written_off"] += int((wo & ~closed).sum())
            self.exit_log["settled"] += int((settled & ~closed & ~wo).sum())

            k = int(exiting.sum())
            if k:
                idx = np.flatnonzero(exiting)
                fresh = self._new_accounts(k, at_origination=True)
                for key, val in fresh.items():
                    st[key][idx] = val
                # A new account has no history. Zeroing the whole row is correct
                # because every window only ever looks backwards.
                for arr in (hist_paid, hist_due, hist_visit, hist_met,
                            hist_ptp_set, hist_ptp_kept, hist_bounce):
                    arr[idx, :] = 0.0
                days_since_pay[idx] = rng.integers(1, 45, k)
                days_since_contact[idx] = rng.integers(1, 45, k)
                consecutive_miss[idx] = 0
                clean_months[idx] = 0
                max_dpd_seen[idx] = st["dpd"][idx]
                times_30p[idx] = 0
                times_60p[idx] = 0
                months_since_first_delinq[idx] = 0
                distinct_agents[idx] = 1
                agent_idx[idx] = rng.integers(0, cfg.n_agents, k)

        panel = pd.concat(rows, ignore_index=True)
        return self._apply_missingness(panel)


    # ── Whole-run calibration ───────────────────────────────────────────────
    def calibrate(self, tol: float = 0.006, max_iter: int = 14) -> float:
        """Bisect an intercept offset so the WHOLE RUN lands on target_bad_rate.

        _solve_intercept fixes the rate at t0. That is not the same thing as the
        rate over 24 months: the book ages, accounts roll into deeper buckets
        where the bad rate is far higher, and the mix at month 20 is not the mix
        at month 0. Measured, a book solved to 0.70 at t0 realised 0.8047 over
        the full panel — 10 points of drift, which would have been reported as
        if it were the configured value.

        A full run is ~0.2s, so calibrating on the quantity actually reported is
        affordable. This is the same principle as _solve_intercept's own fix:
        solve for what you report.
        """
        lo, hi = -3.5, 3.5
        best = 0.0
        for _ in range(max_iter):
            mid = (lo + hi) / 2.0
            sim = BookSimulator(self.cfg)
            rate = float(sim.run(intercept_offset=mid)["y"].mean())
            best = mid
            if abs(rate - self.cfg.target_bad_rate) <= tol:
                break
            if rate > self.cfg.target_bad_rate:
                lo = mid          # too many bads -> pay more -> raise intercept
            else:
                hi = mid
        return best

    # ── Realistic imperfection ──────────────────────────────────────────────
    def _apply_missingness(self, panel: pd.DataFrame) -> pd.DataFrame:
        """MCAR on bureau pulls, plus MNAR concentrated on thin files.

        Without this the 'missing becomes its own WOE bin' machinery is
        theoretical. A book with no missing values does not test a binner.
        """
        cfg, rng = self.cfg, self.rng
        n = len(panel)
        thin = panel["thin_file"].to_numpy()
        for col in ["cibil_score", "utilization_pct", "num_enquiries_6m",
                    "credit_vintage_months", "monthly_income"]:
            p = np.clip(cfg.missing_bureau_rate + thin * cfg.thin_file_missing_boost, 0, 0.95)
            panel.loc[rng.random(n) < p, col] = np.nan
        return panel

    # ── Persistence ─────────────────────────────────────────────────────────
    def save(self, out_dir: Path, panel: pd.DataFrame) -> dict:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        panel.to_parquet(out_dir / "panel.parquet", index=False)
        # The latents go to a sidecar, never into the panel — the same rule the
        # DB-backed generator follows, for the same reason.
        (out_dir / "ground_truth.json").write_text(json.dumps(self.truth, indent=2, default=str))
        meta = {
            "rows": int(len(panel)),
            "distinct_loans": int(panel["loan_id"].nunique()),
            "months": int(panel["month_index"].nunique()),
            "realised_bad_rate": round(float(panel["y"].mean()), 4),
            "target_bad_rate": self.cfg.target_bad_rate,
            "dpd_bucket_mix": {k: round(v, 4) for k, v in
                               panel["dpd_bucket"].value_counts(normalize=True).items()},
            "exits": dict(self.exit_log),
            "config_fingerprint": self.cfg.fingerprint(),
            "config": asdict(self.cfg),
            "SYNTHETIC_WARNING": (
                "Generated by app/ml/simulation/book_simulator.py. These are "
                "SYNTHETIC borrowers. Metrics computed on this panel demonstrate "
                "that the pipeline works end to end; they are NOT evidence of "
                "real-world predictive performance."
            ),
        }
        (out_dir / "dataset_metadata.json").write_text(json.dumps(meta, indent=2, default=str))
        return meta
