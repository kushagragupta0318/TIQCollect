# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-09 — NEW. Every knob, and — for every realism band — WHERE THE NUMBER
#   CAME FROM.
#
#   WHY PROVENANCE IS A FIRST-CLASS FIELD. A synthetic book is only as
#   defensible as the numbers it was built to reproduce, and the failure mode is
#   an assumed range hardening into a quoted fact after two rewrites. This file
#   makes that impossible to do accidentally: every band carries a Provenance,
#   and the realism report prints it beside the result.
#
#   THE HONEST POSITION, stated once here rather than implied: almost every
#   collections band below is a PROJECT ASSUMPTION. No external source was
#   consulted while writing this file, so nothing is labelled REGULATORY unless
#   it is a rule this codebase already enforces elsewhere and can be checked by
#   reading it. Plausible is not sourced. These bands are placeholders to be
#   replaced with the client's own book statistics the moment those exist.
# ───────────────────────────────────────────────────────────────────────────
"""Configuration and realism bands for the event-sourced simulator.

TWO KINDS OF NUMBER LIVE HERE AND THEY MUST NOT BE CONFUSED.

`LedgerConfig` holds GENERATIVE parameters — they define the world. `BANDS`
holds ACCEPTANCE ranges — they define what we will accept as realistic. Tuning a
generative parameter until an acceptance band passes is legitimate for BOOK
COMPOSITION (the mix of a portfolio is an observable property one can target).
It is NOT legitimate for model performance, and this module deliberately holds
no Gini target at all: see the note on `signal_scale`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum


class Provenance(str, Enum):
    """Where a realism band came from. Printed beside every check result."""

    #: A rule with legal or regulatory force, already enforced elsewhere in this
    #: codebase and checkable by reading that code. The only category that may
    #: be stated as fact.
    REGULATORY = "REGULATORY"

    #: A property of THIS repo's own definitions — e.g. the model's acceptance
    #: gates. Internally sourced and verifiable, but not a claim about the world.
    INTERNAL = "INTERNAL"

    #: A plausible figure chosen by us, with NO external source consulted. Most
    #: of this file. Must never be quoted as an industry fact.
    ASSUMPTION = "ASSUMPTION"

    #: A value the generator is deliberately steered toward, because it is an
    #: observable book property we want to hold fixed while varying other
    #: things. Steering to these is legitimate; steering to a Gini is not.
    CALIBRATION_TARGET = "CALIBRATION_TARGET"


@dataclass(frozen=True)
class Band:
    """One acceptance range, with its provenance and the reason for it."""

    key: str
    low: float
    high: float
    provenance: Provenance
    note: str

    def contains(self, x: float | None) -> bool:
        return x is not None and self.low <= x <= self.high

    def to_dict(self) -> dict:
        return {"key": self.key, "low": self.low, "high": self.high,
                "provenance": self.provenance.value, "note": self.note}


#: Phase 1 acceptance bands (realism checks 1-6). Checks 7-10 are model-facing
#: and belong to Phase 2; they are deliberately absent here so that Phase 1
#: cannot be tuned against a model result.
BANDS: dict[str, Band] = {
    # ── 1. Roll rates ───────────────────────────────────────────────────────
    "forward_flow_rate": Band(
        "forward_flow_rate", 0.15, 0.55, Provenance.ASSUMPTION,
        "Share of delinquent accounts worsening a bucket in a month. NOT "
        "sourced — replace with the client's roll-rate matrix. "
        "*(Ceiling raised from 0.45 to 0.55 on 2026-09-09, AFTER measuring "
        "0.4645 and working out why. This band is not free: an account that "
        "does not pay at least one instalment rolls forward MECHANICALLY, "
        "because a fresh instalment bills every cycle. So forward flow is "
        "bounded below by roughly (1 - material_payment_rate) times the chance "
        "of crossing a 30-day boundary, and at the 0.20-0.40 material rate this "
        "file also asserts, a ceiling of 0.45 was arithmetically inconsistent "
        "with it. The original band was set without doing that coupling "
        "arithmetic. Recorded rather than quietly widened, because moving a "
        "goalpost after seeing the result is exactly the move that makes a "
        "validation suite worthless — the justification has to survive reading, "
        "and if it does not, the generator was right and the band was wrong.)*"),
    "cure_rate": Band(
        "cure_rate", 0.05, 0.25, Provenance.ASSUMPTION,
        "Share improving a bucket in a month. Wide on purpose: cure is the "
        "quantity we are least able to guess without a real book."),

    # ── 2. Book composition ─────────────────────────────────────────────────
    "npa_share": Band(
        "npa_share", 0.15, 0.35, Provenance.CALIBRATION_TARGET,
        "Share of account-months at 90+ DPD. A CALIBRATION TARGET, not a "
        "finding: the previous simulator let NPA become a sink at a measured "
        "51.7% and the lifecycle hazards exist to stop that. The 90-day "
        "threshold itself is regulatory; this share is our target."),
    "performing_share": Band(
        "performing_share", 0.15, 0.45, Provenance.CALIBRATION_TARGET,
        "Share of account-months at 0-30 DPD. The book must have mass at the "
        "GOOD end or the binner has nothing to bin there and DPD's apparent "
        "power is inflated — the defect the demo seed has by starting every "
        "loan at DPD 35."),
    # *(This band used to be `current_share`, 0.10-0.40, on DPD == 0 exactly.
    # It was measuring the wrong construct and no generator could have passed
    # it. After clearing its arrears an account is at DPD 0 only until the next
    # instalment runs past grace — 5 days of every 30-day cycle — so a monthly
    # snapshot catches a perfectly performing borrower as CURRENT about 17% of
    # the time. Measured: CURRENT 3.0% while the whole 0-30 band held 21%. The
    # mass was right; the definition was wrong. Corrected rather than deleted,
    # and NOT by moving the goalposts to whatever the generator produced — the
    # replacement is on the construct the check was always about.)*
    "current_share": Band(
        "current_share", 0.0, 1.0, Provenance.ASSUMPTION,
        "REPORTED, NOT GATED. Kept visible because a collapse to zero would "
        "mean cures had stopped happening, but its level is an artefact of "
        "grace-vs-cycle arithmetic rather than a property of the book."),

    # ── 3. Vintage ──────────────────────────────────────────────────────────
    "vintage_monotone_frac": Band(
        "vintage_monotone_frac", 0.80, 1.00, Provenance.ASSUMPTION,
        "Fraction of origination cohorts whose cumulative-ever-delinquent curve "
        "is non-decreasing in months-on-book. Near-1 by arithmetic — a genuine "
        "sanity check on the generator, not on the world."),

    # ── 4. Payment shape ────────────────────────────────────────────────────
    "exact_emi_share": Band(
        "exact_emi_share", 0.20, 0.50, Provenance.ASSUMPTION,
        "Payments within +/-2% of one EMI. Real ledgers spike hard at the "
        "contractual amount; the size of the spike is our guess."),
    "round_number_share": Band(
        "round_number_share", 0.10, 0.45, Provenance.ASSUMPTION,
        "Payments that are a multiple of 500. Cash collection clusters on round "
        "notes. Direction is real, magnitude is assumed."),
    "partial_share": Band(
        "partial_share", 0.20, 0.60, Provenance.ASSUMPTION,
        "Payments under 90% of one EMI. A collections book is mostly partials; "
        "the band is ours."),

    # ── 5. Promises ─────────────────────────────────────────────────────────
    "ptp_kept_rate": Band(
        "ptp_kept_rate", 0.35, 0.65, Provenance.ASSUMPTION,
        "Share of resolved PTPs honoured. Wide because it varies enormously by "
        "book and by how aggressively agents record promises. NOT sourced."),

    # ── 6. Contact ──────────────────────────────────────────────────────────
    "rpc_rate": Band(
        "rpc_rate", 0.20, 0.50, Provenance.ASSUMPTION,
        "Right-party contact: visits where the borrower was met. Field visits "
        "beat telephony on this, which is why the band sits above typical "
        "call-centre figures. Still an assumption."),

    # ── Reported, not gated ─────────────────────────────────────────────────
    "material_payment_rate": Band(
        "material_payment_rate", 0.20, 0.40, Provenance.CALIBRATION_TARGET,
        "Share of account-months with a material payment (the model's y=0). "
        "The intercept is SOLVED to this. Calibrating PREVALENCE is legitimate "
        "— it is an observable book property. Calibrating DISCRIMINATION would "
        "not be, and nothing here does."),
}

#: Facts this codebase already enforces, restated so the simulator agrees with
#: the product rather than inventing its own rules.
REGULATORY_NOTES = {
    "npa_threshold_days": (90, Provenance.REGULATORY,
                           "90+ DPD is NPA. Encoded in models/loan.dpd_bucket_for, "
                           "which this simulator IMPORTS rather than restating."),
    "contact_hours": ("08:00-19:00", Provenance.REGULATORY,
                      "RBI contact hours, enforced in the compliance rules and in "
                      "planner_service._contact_windows."),
}


@dataclass
class LedgerConfig:
    """Every generative knob. Serialised into the dataset metadata."""

    n_borrowers: int = 5_000
    months: int = 24
    n_agents: int = 30
    seed: int = 42
    start_date: date = date(2024, 10, 1)

    # ── Billing ─────────────────────────────────────────────────────────────
    #: A FIXED 30-DAY CYCLE rather than calendar months. A simplification, and a
    #: deliberate one: it makes DPD an exact function of the day index, so the
    #: derivation is vectorised and has no calendar edge cases. Real EMIs fall
    #: on calendar dates; nothing downstream depends on which it is.
    cycle_days: int = 30
    grace_days: int = 5      # ASSUMPTION: contractual grace before past-due
    penal_rate_monthly: float = 0.02      # ASSUMPTION: on overdue, past 30 DPD

    # ── Difficulty ──────────────────────────────────────────────────────────
    # NO GINI TARGET EXISTS IN THIS FILE, deliberately. These two scale the
    # latent signal and the observation noise; whatever discrimination results
    # is a CONSEQUENCE to be measured in Phase 2, never an input to be hit.
    # Tuning them until a Gini lands where we would like it is the exact thing
    # that would make every number produced downstream worthless.
    signal_scale: float = 1.0
    observation_noise: float = 1.0

    #: Prevalence target for the material-payment rate. Solved by bisection on
    #: the intercept ALONE, which shifts the base rate without touching the
    #: relative weight of any driver. See BANDS["material_payment_rate"].
    target_material_rate: float = 0.30

    # ── Book composition at t0 ──────────────────────────────────────────────
    dpd_start_mix: dict = field(default_factory=lambda: {
        "CURRENT": 0.26, "BUCKET_1": 0.21, "BUCKET_2": 0.19,
        "BUCKET_3": 0.16, "NPA": 0.18,
    })
    dpd_origination_mix: dict = field(default_factory=lambda: {
        "CURRENT": 0.64, "BUCKET_1": 0.26, "BUCKET_2": 0.10,
    })

    # ── Latents (AR(1), daily) ──────────────────────────────────────────────
    #: Daily persistence. 0.985^30 ~= 0.64 monthly, so a borrower's disposition
    #: is recognisably theirs month to month but a six-month-old delinquency
    #: record is a decayed signal. That decay is what stops DPD being a
    #: sufficient statistic for the borrower.
    latent_rho: float = 0.985
    latent_sigma: float = 0.030
    #: Two-state income shock (job loss / recovery), monthly probabilities.
    income_shock_in: float = 0.010
    income_shock_out: float = 0.075
    income_shock_depth: float = 0.45

    # ── Field activity ──────────────────────────────────────────────────────
    visit_hazard_current: float = 0.010   # per day
    visit_hazard_delinquent: float = 0.055
    ptp_grace_days: int = 3
    ptp_horizon_lo: int = 3
    ptp_horizon_hi: int = 15

    # ── Payment status lifecycle ────────────────────────────────────────────
    # These exist so the ledger carries a real status HISTORY. The live demo
    # database has none — every PTP status and payment status is the current
    # value only — which is precisely why a retrospective backtest is
    # impossible there.
    p_pending_then_verified: float = 0.040
    verify_lag_lo: int = 1
    verify_lag_hi: int = 3
    p_rejected: float = 0.012
    p_reversed: float = 0.008
    reversal_lag_lo: int = 5
    reversal_lag_hi: int = 20

    # ── Lifecycle ───────────────────────────────────────────────────────────
    cure_cycles_to_close: int = 3
    writeoff_dpd: float = 180.0
    writeoff_hazard_monthly: float = 0.50
    settlement_hazard_monthly: float = 0.15
    recall_hazard_monthly: float = 0.004
    deceased_hazard_monthly: float = 0.0012

    # ── Imperfection ────────────────────────────────────────────────────────
    missing_bureau_rate: float = 0.11
    thin_file_missing_boost: float = 0.35
    bureau_refresh_days: int = 90         # staleness is realistic and matters
    agent_churn_per_year: float = 0.18

    # ── Drift ───────────────────────────────────────────────────────────────
    seasonality_amplitude: float = 0.18
    shock_month_index: int = 17
    shock_magnitude: float = -0.55
    #: Covariate drift: origination score mix walks over time. Gives PSI/CSI
    #: something genuine to find.
    covariate_drift: float = 0.55
    #: CONCEPT drift — the relationship itself changes. OFF BY DEFAULT so the
    #: headline stays comparable with the book_simulator baseline (OOT Gini
    #: 0.5136). Enabled only by the --stress profile, where its purpose is to
    #: prove monitor_model's retrain trigger can actually fire.
    #: DELIBERATELY SEVERE. `--stress` exists to prove `monitor_model`'s retrain
    #: trigger can fire at all, so the deterioration has to be unambiguous
    #: rather than marginal — a scenario tuned until the monitor *just* fires
    #: would be measuring the threshold, not the monitor.
    #:
    #: 2026-09-09: the first settings (onset at 0.75 of the book, decay 0.30)
    #: produced no detectable deterioration and the monitor correctly reported
    #: healthy. Measured on 30 months with cohorts scored across months 13-27:
    #: five of eight cohorts sat entirely before the onset, the worst-affected
    #: slice reached only x0.84 of the original signal, and the per-slice Gini
    #: showed no downward trend at all (0.4441, 0.4565, 0.4410, 0.4539). That
    #: was a stress profile that was not stressing, not a monitor that missed
    #: something. Onset now covers most of the scored window and the signal
    #: falls to 45% of its original strength by the end.
    concept_drift: bool = False
    concept_drift_onset_frac: float = 0.35
    #: What decays. The LATENT SIGNAL, not one named column: every behavioural
    #: feature the model reads is downstream of it, so naming a single feature
    #: would misdescribe what actually happens.
    concept_drift_target: str = "latent_signal"
    concept_drift_decay: float = 0.55

    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return json.loads(json.dumps(asdict(self), sort_keys=True, default=str))
