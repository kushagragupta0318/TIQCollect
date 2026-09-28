# 0007. Retraining is automatic up to a human; promotion needs four eyes

**Status:** Accepted, 2026-09-09 (lifecycle) and 2026-09-10 (four eyes).
**Switch:** `ML_AUTO_RETRAIN_ENABLED`.

## Decision

```
predictions → matured outcomes → labels → monitoring → retrain_recommended
    → candidate → challenger trained → gates → comparison with the DEPLOYED model
    → PENDING_APPROVAL ...................... [automation stops here]
    → a person approves → a DIFFERENT person promotes → champion.txt moves
```

- **Monitoring waits for 500 matured account-days** (`monitor.readiness`). The 500 is from
  Hanley–McNeil: at n = 500, SE(AUC) ≈ 0.022, which is narrow enough to see a 20% relative Gini
  drop.
  - Below 500 the state is `not_ready`. With one class it is `insufficient_outcome_variation`.
  - Neither triggers anything, and both are logged at INFO.
- **A retrain is triggered by any of these,** each against the artifact's own development
  figure:
  - Gini down more than 20% relative, KS down more than 30%, or Brier up more than 25%
  - calibration gap above 0.10
  - score or feature PSI above 0.25
  - a rank-order break, or a missing champion feature
- **The training data is production data or nothing.** `production_dataset` builds from served
  predictions and attached labels, and there is no synthetic fallback. The floors are 2,000
  rows, 50 minority-class rows and 8 as-of dates.
- **The challenger is compared with the deployed model on the same out-of-time rows,** through
  the serving path. A tie keeps the incumbent.
- **`registry.promote` is the only writer of `champion.txt`.** It refuses when:
  - the artifact is missing;
  - the gate result is not PASS;
  - the pointer no longer reads the incumbent the candidate was compared against;
  - the approver and the promoter are the same person.

## Consequences

- **Promotion is open to every manager today** (known issue 11). F12 restricts it to
  `BANK_TECHOPS` and is kept active for the pilot.
- **The loaded model object is cached per process.** `DecisionEngine.serving_state` reports
  loaded vs pointer vs configured per host and pid. A fleet can split across two champions
  until processes reload.
- **Nothing here says anything yet about real borrowers.** Every lifecycle test uses fixture
  cohorts. The first matured production cohort cannot exist before 2026-10-08.
