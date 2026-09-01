"""
Empirical Bayes Segment Shrinkage Estimator for Agent Performance
===================================================================
Prevents small-sample noise and historical assignment bias by shrinking
agent-specific recovery win-rates toward the portfolio segment prior.

Segment: (Loan Type x DPD Bucket)
Shrinkage Formula:
    ŵ_{j,s} = (n_{j,s} / (n_{j,s} + k)) * w̄_{j,s} + (k / (n_{j,s} + k)) * μ_s

Multiplier for Agent j on Segment s:
    α_{j,s} = clamp(ŵ_{j,s} / max(μ_s, 0.05), 0.75, 1.25)
"""
from __future__ import annotations

from typing import Any, Sequence
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.payment import Payment, PaymentStatus
from app.models.case import Case
from app.models.loan import Loan, DPDBucket
from app.models.agent import Agent


class EmpiricalBayesAgentAdjuster:
    """Calculates segment-aware Empirical Bayes performance adjustments for agents."""

    def __init__(self, smoothing_k: float = 10.0, min_sample_threshold: int = 5):
        self.smoothing_k = smoothing_k
        self.min_sample_threshold = min_sample_threshold
        # Segment priors: key=(loan_type, dpd_bucket) -> float (0.0 to 1.0)
        self.segment_priors: dict[tuple[str, str], float] = {}
        # Agent observations: key=(agent_id, loan_type, dpd_bucket) -> dict(n=..., total_target=..., total_recovered=...)
        self.agent_observations: dict[tuple[str, str, str], dict[str, float]] = {}
        self.global_prior: float = 0.45

    def fit_from_db(self, db: Session, lookback_days: int = 180) -> EmpiricalBayesAgentAdjuster:
        """Compute empirical priors and agent stats from historical payments."""
        # Query verified payments joined with case and loan
        records = (
            db.query(
                Payment.agent_id,
                Loan.loan_type,
                Loan.dpd_bucket,
                Case.target_amount,
                func.sum(Payment.amount).label("total_paid"),
                func.count(Payment.id).label("payment_count"),
            )
            .join(Case, Payment.case_id == Case.id)
            .join(Loan, Case.loan_id == Loan.id)
            .filter(Payment.status == PaymentStatus.VERIFIED)
            .group_by(Payment.agent_id, Loan.loan_type, Loan.dpd_bucket, Case.target_amount)
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
            cnt = int(r[5] or 1)

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

        # Empirical Bayes shrinkage formula
        shrunk_win_rate = (n / (n + self.smoothing_k)) * raw_win_rate + (self.smoothing_k / (n + self.smoothing_k)) * mu_s

        # Calculate multiplier relative to segment prior
        raw_multiplier = shrunk_win_rate / max(mu_s, 0.05)
        # Conservative bounding to [0.75, 1.25]
        bounded_multiplier = max(0.75, min(1.25, raw_multiplier))

        return shrunk_win_rate, mu_s, bounded_multiplier
