# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-03 — Three defects fixed so this estimator can be used as a training
#   feature rather than only as a live allocation input.
#
#   1. `lookback_days` was in the signature and NOWHERE in the query. Every
#      verified payment ever recorded was read, whatever the caller asked for.
#      The parameter was a promise the function did not keep, and every comment
#      in this codebase describing a "180-day window" was quoting it.
#
#   2. There was no `as_of`, so the estimate could not be computed as it stood
#      on a past date. Attaching today's EB to a snapshot dated three weeks ago
#      leaks three weeks of outcomes into a feature — the exact failure
#      RepaymentService guards against structurally with its mandatory `as_of`
#      and `_raise_if_leaked`. `as_of` is now REQUIRED and keyword-only: there
#      is no signature you can call by accident that is not point-in-time.
#
#      EB is reconstructible historically in a way the borrower features are
#      NOT. Payment rows are append-only and carry `payment_date`; Loan.dpd and
#      PTP.status are overwritten in place with no history (see
#      models/repayment_snapshot.py). So a backfilled EB value is honest where
#      a backfilled DPD is not.
#
#   3. The aggregation grouped by `Case.target_amount`, a continuous money
#      column, as a stand-in for case identity. Two cases with the same target
#      collapsed into one row: their payments were summed while ONE target was
#      counted. Measured on this book, that inflated the global prior from
#      0.820 to 0.861 and produced 15 of 315 (agent, segment) cells reporting a
#      recovery rate above 100% — impossible, since `collected <= target` is an
#      enforced invariant. The EMI rescale made it worse by rounding targets to
#      the nearest 10, so far more cases now share a target value.
#
#   Also fixed: fit_from_db accumulated into self.agent_observations without
#   clearing, so calling it twice on one instance double-counted everything.
#   Harmless while every caller fitted once; not harmless for a backfill loop.
#
#   The shrinkage formula itself is untouched.
# ───────────────────────────────────────────────────────────────────────────
"""
Empirical Bayes Segment Shrinkage Estimator for Agent Performance
===================================================================
Prevents small-sample noise and historical assignment bias by shrinking
agent-specific recovery win-rates toward the portfolio segment prior.

Segment: (Loan Type x DPD Bucket)
Shrinkage Formula:
    w_hat_{j,s} = (n/(n+k)) * w_bar_{j,s} + (k/(n+k)) * mu_s

Multiplier for Agent j on Segment s:
    alpha_{j,s} = clamp(w_hat_{j,s} / max(mu_s, 0.05), 0.75, 1.25)

This is the AGENT-side signal only: how an agent performs on work of a given
kind. It deliberately knows nothing about the individual borrower — that is the
borrower model's job, and the two are combined by letting a model learn the
weight of this estimate, never by multiplying two probabilities together.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.payment import Payment, PaymentStatus
from app.models.case import Case
from app.models.loan import Loan


#: The multiplier's conservative bounds, and the prior below which a rate is
#: noise rather than a denominator. Shared by every EB consumer (eb_shrink).
MULTIPLIER_BOUNDS = (0.75, 1.25)
PRIOR_FLOOR = 0.05


def eb_shrink(n: float, k: float, own: float, prior: float, *,
              bounds: tuple[float, float] = MULTIPLIER_BOUNDS, prior_floor: float = PRIOR_FLOOR) -> dict:
    """The shrinkage formula, once: w = n/(n+k); shrunk = w*own + (1-w)*prior;
    multiplier = shrunk / max(prior, floor), bounded. Pure. Used by the agent
    adjuster below and by services/bank/agency_effect (coordinator 2026-09-29:
    one definition)."""
    w = n / (n + k) if (n + k) > 0 else 0.0
    shrunk = w * own + (1.0 - w) * prior
    lo, hi = bounds
    return {"shrunk": shrunk, "multiplier": max(lo, min(hi, shrunk / max(prior, prior_floor)))}


class EmpiricalBayesAgentAdjuster:
    """Calculates segment-aware Empirical Bayes performance adjustments for agents."""

    DEFAULT_LOOKBACK_DAYS = 180

    def __init__(self, smoothing_k: float = 10.0, min_sample_threshold: int = 5):
        self.smoothing_k = smoothing_k
        self.min_sample_threshold = min_sample_threshold
        # Segment priors: key=(loan_type, dpd_bucket) -> float (0.0 to 1.0)
        self.segment_priors: dict[tuple[str, str], float] = {}
        # Agent observations: key=(agent_id, loan_type, dpd_bucket)
        #   -> dict(n=..., target=..., recovered=...)
        self.agent_observations: dict[tuple[str, str, str], dict[str, float]] = {}
        self.global_prior: float = 0.45
        # What this instance was fitted on, so a caller (or a snapshot row) can
        # record the window rather than assume it.
        self.fitted_as_of: date | datetime | None = None
        self.fitted_window: tuple[datetime, datetime] | None = None

    # ── Point-in-time window ─────────────────────────────────────────────────
    @staticmethod
    def resolve_window(as_of: date | datetime, lookback_days: int) -> tuple[datetime, datetime]:
        """The half-open payment window [start, cutoff) this fit may read.

        The upper bound follows the convention already settled in
        RepaymentService, which is what keeps features and labels from
        overlapping by a day:

          * a DATE means the whole of that day is visible, matching
            build_features' `payment_date.date() <= as_of`. The exclusive cutoff
            is therefore midnight at the START of the following day. The label
            window in _infer_outcome opens at `as_of_date <` — strictly after —
            so the two never share a payment.
          * a DATETIME is taken literally: strictly before that instant. Used
            when a caller has a real timestamp rather than a scoring day.

        Naive datetimes are treated as whatever tz payment_date carries, matching
        how the rest of this codebase compares them.
        """
        if isinstance(as_of, bool) or not isinstance(as_of, (date, datetime)):
            raise TypeError(f"as_of must be a date or datetime, got {type(as_of).__name__}")
        if lookback_days <= 0:
            raise ValueError(f"lookback_days must be positive, got {lookback_days}")

        if isinstance(as_of, datetime):
            cutoff = as_of
            anchor_day = as_of.date()
        else:
            anchor_day = as_of
            cutoff = datetime.combine(as_of + timedelta(days=1), time.min)

        start = datetime.combine(anchor_day - timedelta(days=lookback_days), time.min)
        if cutoff.tzinfo is not None and start.tzinfo is None:
            start = start.replace(tzinfo=cutoff.tzinfo)
        return start, cutoff

    def fit_from_db(
        self,
        db: Session,
        *,
        as_of: date | datetime,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> "EmpiricalBayesAgentAdjuster":
        """Compute empirical priors and agent stats from historical payments.

        `as_of` is mandatory and keyword-only. Nothing at or after it is read,
        so the result is safe to attach to a historical row as a feature.
        """
        start, cutoff = self.resolve_window(as_of, lookback_days)

        # Refitting must not accumulate onto the previous fit.
        self.segment_priors = {}
        self.agent_observations = {}
        self.global_prior = 0.45

        # ONE ROW PER (agent, case). case_id in the GROUP BY is what makes the
        # grain unique — grouping by target_amount instead let two cases with an
        # identical target merge into one row, summing both their payments
        # against a single target. target_amount stays in the GROUP BY only
        # because Postgres cannot infer its functional dependency on
        # Payment.case_id (the dependency is on Case.id, and we group on the FK).
        #
        # A case worked by two agents yields two rows and its target is counted
        # once for EACH — 42 of 556 paid cases on this book. That is deliberate:
        # each agent is being measured on "of the target you were collecting
        # against, how much did YOU bring in", and splitting the denominator
        # would credit an agent for money a colleague collected. It biases both
        # agents' rates DOWNWARD on shared cases, which is the safe direction
        # for a score that hands out more work.
        records = (
            db.query(
                Payment.agent_id,
                Loan.loan_type,
                Loan.dpd_bucket,
                Case.target_amount,
                func.sum(Payment.amount).label("total_paid"),
            )
            .join(Case, Payment.case_id == Case.id)
            .join(Loan, Case.loan_id == Loan.id)
            .filter(
                Payment.status == PaymentStatus.VERIFIED,
                # 2026-09-09 — REQUIRED, not tidiness. `payments.agent_id`
                # became nullable so a direct bank payment could be recorded
                # against the case without being credited to anyone. This query
                # groups BY agent_id with no agent filter, so a NULL would form
                # its own group AND, worse, add to `segment_totals` — the
                # segment prior every agent's `eb_multiplier` is divided by. A
                # payment nobody collected is not evidence about any agent, and
                # letting it move the prior would move the allocator's
                # `prob_recovery` for every case in the segment.
                Payment.agent_id.isnot(None),
                Payment.payment_date >= start,
                Payment.payment_date < cutoff,
            )
            .group_by(
                Payment.agent_id,
                Payment.case_id,
                Loan.loan_type,
                Loan.dpd_bucket,
                Case.target_amount,
            )
            .all()
        )

        segment_totals: dict[tuple[str, str], dict[str, float]] = {}
        total_recovered_all = 0.0
        total_target_all = 0.0

        for r in records:
            agent_id = str(r[0])
            lt = str(r[1].value if hasattr(r[1], "value") else r[1])
            dpd = str(r[2].value if hasattr(r[2], "value") else r[2])
            target = float(r[3] or 10000.0)
            paid = float(r[4] or 0.0)
            # ONE observation: this agent, this case. NOT the payment count —
            # a case settled in three instalments is one piece of evidence about
            # the agent, not three, and n drives the shrinkage weight directly.
            cnt = 1

            seg_key = (lt, dpd)
            if seg_key not in segment_totals:
                segment_totals[seg_key] = {"target": 0.0, "recovered": 0.0, "n": 0}
            segment_totals[seg_key]["target"] += target
            segment_totals[seg_key]["recovered"] += paid
            segment_totals[seg_key]["n"] += cnt

            total_recovered_all += paid
            total_target_all += target

            ag_key = (agent_id, lt, dpd)
            if ag_key not in self.agent_observations:
                self.agent_observations[ag_key] = {"target": 0.0, "recovered": 0.0, "n": 0}
            self.agent_observations[ag_key]["target"] += target
            self.agent_observations[ag_key]["recovered"] += paid
            self.agent_observations[ag_key]["n"] += cnt

        # Set Global Prior
        if total_target_all > 0:
            self.global_prior = max(0.10, min(0.90, total_recovered_all / total_target_all))

        # Set Segment Priors
        for seg, vals in segment_totals.items():
            if vals["target"] > 0 and vals["n"] >= 3:
                self.segment_priors[seg] = max(0.05, min(0.95, vals["recovered"] / vals["target"]))
            else:
                self.segment_priors[seg] = self.global_prior

        self.fitted_as_of = as_of
        self.fitted_window = (start, cutoff)
        return self

    def get_segment_multiplier(self, agent_id: str, loan_type: str, dpd_bucket: str) -> tuple[float, float, float]:
        """
        Compute the Bayesian shrunk recovery rate and bounded multiplier.

        Returns:
            (shrunk_win_rate, segment_prior, bounded_multiplier)
        """
        seg_key = (loan_type, dpd_bucket)
        mu_s = self.segment_priors.get(seg_key, self.global_prior)

        ag_key = (agent_id, loan_type, dpd_bucket)
        ag_data = self.agent_observations.get(ag_key)

        if not ag_data or ag_data["n"] < self.min_sample_threshold:
            # Under minimum sample size threshold -> return prior rate and neutral multiplier 1.0
            return mu_s, mu_s, 1.0

        n = ag_data["n"]
        raw_win_rate = ag_data["recovered"] / max(1.0, ag_data["target"])
        raw_win_rate = max(0.0, min(1.0, raw_win_rate))

        # Empirical Bayes shrinkage, bounded relative to the segment prior (eb_shrink).
        eb = eb_shrink(n, self.smoothing_k, raw_win_rate, mu_s)
        return eb["shrunk"], mu_s, eb["multiplier"]

    # ── Evidence, for consumers that need to weigh the estimate ──────────────
    def get_segment_evidence(self, agent_id: str, loan_type: str, dpd_bucket: str) -> int:
        """How many (agent, case) observations back this cell. 0 means none.

        Kept separate from get_segment_multiplier so that signature — which the
        allocator unpacks positionally — does not change. A model handed
        shrunk_win without this cannot tell an agent's own measured rate from
        the segment average returned in its place, and on this book the fallback
        is the common path, not the exception.
        """
        ag = self.agent_observations.get((agent_id, loan_type, dpd_bucket))
        return int(ag["n"]) if ag else 0
