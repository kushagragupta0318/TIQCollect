# 0008. recovery_risk 2.2.0 is champion

**Status:** Accepted, promoted and committed in `7707139` on 2026-09-16; it has driven every
nightly allocation since 2026-09-17. **Pointer:** `backend/app/ml/artifacts/recovery_risk/champion.txt`.

## What it is

- **A 15-feature exactly additive GAM,** built from boosted single-feature shape functions.
  It is monotone where a sign is declared, and has one declared interaction
  (`latest_disposition × arrears_ratio`).
- **Trained on the event-sourced ledger simulator's synthetic book.**
- **A Platt calibrator fitted on validation.**
- **Reason codes from the exact per-tree decomposition:** intercept + contributions = logit,
  to 9e-15.
- **Its predecessor was 1.1.0,** a 4-feature WOE + logistic scorecard (`dpd`, `cibil_score`,
  `ptp_kept_ratio`, `overdue_amount`; OOT Gini 0.5136 / KS 39.72). It is kept for rollback.

## Evidence (from the artifact, out-of-time, 23,780 rows)

| | Gini | KS | AUC | Brier |
|---|---|---|---|---|
| 2.2.0 as served (calibrated) | 0.5122 | 38.66 | 0.7561 | 0.18021 |
| 2.2.0 **live-equivalent**: borrower stance at NONE, as production records it (ML-1) | **0.4796** | **35.74** | — | — |

- It passes 12/12 spec gates: score PSI 0.010, max CSI 0.070, max VIF 2.14, 0 rank-order
  breaks.
- **KS ≥ 39 was not reached.** The search established about KS 38.8 / Gini 0.51 as the ceiling
  of the frozen world for a stable, interpretable model.
- **The live-equivalent row was measured on 2026-09-24** (tiqcollect-ce, "ML-1 C"). The same
  artifact was rescored with `latest_disposition` and `disposition_recency_class` at NONE. Mean
  P(pay) was 4.1% lower, skewed to early DPD. The sanity gate reproduced the artifact's own
  figures exactly.

## Known limits

1. **ML-1: the strongest behavioural feature is constant in production.** The borrower-stance
   feature (IV 0.31) has no product surface: 0 of 2,404 visits and 0 of 1,089 calls record it.
   Until stance capture ships and its coverage is measured, **quote the live-equivalent figures,
   not the artifact's.**
2. **Synthetic training data.** It demonstrates the pipeline, not real-borrower performance.
3. **The allocator uplift (ADR 0002) was measured under 1.1.0.** It has not been re-run under
   2.2.0.
4. **There is no `MODEL_DEVELOPMENT.html` for 2.2.0.** The closure report is
   `PRODUCTION_READINESS_CLOSURE.md` in the artifact folder.

## Consequences

- Rollback: point `champion.txt` back at `1.1.0` through the promotion path (ADR 0007), or set
  `ML_SCORING_ENABLED=False` to return the allocator to its non-ML term in one act.
- Promoting a successor is a lifecycle decision, never an edit to the pointer.
