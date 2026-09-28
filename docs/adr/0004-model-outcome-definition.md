# 0004. The recovery_risk outcome label

**Status:** Accepted, 2026-09-08. **Version string:** `OUTCOME_DEFINITION_VERSION =
"recovery-outcome-1.0.0"`, stamped on every labelled row.

## Decision

`app/ml/pipeline/outcomes.py` is the **only** writer of `ModelPrediction.actual_outcome`:

```
recovered  ==  paid_in_window >= 0.8 * min(overdue_amount, emi_amount)
window     ==  ( end of as_of_date , end of as_of_date + 30 days ]
```

- **`overdue_amount` and `emi_amount` are frozen at prediction time** in
  `ModelPrediction.outcome_baseline`. Both are overwritten in place on `Loan`.
- **Only VERIFIED payments count.** A reversal is a status change, not a negative row.
- **Written off, settled, recalled or deceased is CENSORED** (label NULL). A bank decision is
  not borrower behaviour. When two censors apply, the order is write-off first.
- **Payments on the observation day are excluded.** Day 30 counts; day 31 does not, and is
  recorded as `paid_after_window`.
- **A bank-reported direct payment (`PAID_DIRECT`) is written as a VERIFIED `Payment`** with
  `agent_id` NULL and mode `BANK_DIRECT`, so the label reads it and no agent is credited with it.
- **Labelling runs at 19:15**, before the 19:30 bank ingest. Otherwise an action that landed
  after a window closed would censor a prediction.

## Why not the repayment labeller's rule

- **The repayment labeller binarises to "any payment > 0".** Its 90% ratio only separates two
  labels that are the same class.
- **It counts REJECTED and REVERSED receipts,** and censors on different evidence.
- **Relabelling the model with it would silently change the target** every metric in
  `ml/artifacts` was computed against.
- `ml/pipeline/label_comparison.py` records what the other rule would have said, for
  diagnosis only.

## Consequences

- A second writer is forbidden. `test_only_one_module_writes_the_outcome_label` is an AST
  tripwire, and `monitor.attach_outcomes`, a dead second writer, was removed on 2026-09-09.
- A future change to the rule bumps `OUTCOME_DEFINITION_VERSION`, so rows stay filterable
  rather than being rewritten retroactively.
- **The first 30-day labels mature from 2026-10-08.** Until then monitoring reports `not_ready`
  (ADR 0007).
