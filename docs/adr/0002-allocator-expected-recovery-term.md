# 0002. The allocator's expected-recovery term

**Status:** Accepted, 2026-09-08 (promoted to live). **Owner of the switch:**
`ML_SCORING_ENABLED` in `core/config.py`.

## Context

The nightly SMART allocation (`services/global_allocator.py`) scores each case × agent pair.
Its `expected_recovery` term was
`value(EV) × prob_recovery`, where:
- `prob_recovery = clamp(shrunk_win × tier, 0.20, 0.85)`
- the value transform was `log1p(EV / 25,000) / log1p(12)`

The transform was calibrated against *target* amounts but fed *expected* values. Once a
calibrated probability (mean 0.27, correlated −0.41 with balance) was used, 100% of expected
values fell below the 25,000 knee. The term used 3.9% of its range, and proximity outvoted it
8:1 on realised spread.

## Decision

These two changes ship as **one** change and roll back as one:
1. `prob_recovery = clamp(P(pay) × eb_multiplier × tier, 0.02, 0.85)`, with `P(pay)` from the
   `recovery_risk` champion. `eb_multiplier` is a bounded agent-side ratio in [0.75, 1.25],
   centred on 1.0.
2. The value transform becomes `log1p(EV / 1,000) / log1p(20)`. The constants are fixed rupee
   figures, never derived from the pool.

`PlannerService._ml_recovery_probabilities` returns nothing when scoring is off or fails, and
the transform falls back to the old one in the same expression. The pairing is enforced in
code; two settings never have to agree.

## Evidence

Measured with 1.1.0's probabilities on synthetic 1,200-case, 40-agent books, 8 seeds the model
had not seen (`scripts/value_transform_study.py`, `value_transform_sensitivity.py`):

| | before | after |
|---|---|---|
| realised recovery | baseline | **+28.0%**, 8 of 8 seeds (+18.4% to +39.6%) |
| forecast bias vs realised | +94% | +9.5% |
| Brier | 0.2172 | 0.1435 |
| value / proximity influence | 0.120 | 0.994 |
| BLOCKED set | — | identical, every transform, every seed |

- **The calibrated probability with the OLD transform loses 10.1%** in 8 of 8 seeds. That is
  why the pairing is enforced.
- The 1,000 / 20,000 constants sit mid-grid. Across an 8× span of knee and a 4× span of
  reference, every point beat the baseline in 3 of 3 seeds (+21.2% to +38.3%).

## Consequences

- **The +28% is a 1.1.0-era, synthetic figure.** It has not been re-run under 2.2.0
  (ADR 0008) or on a real book. The first thing to do against a live book is re-run
  `scripts/value_transform_study.py` there.
- Regression tests pin the behaviour:
  - `test_old_transform_starves_the_value_term`
  - `test_promoted_transform_gives_value_influence_comparable_to_proximity`
  - `test_rescaling_does_not_hand_the_plan_to_the_largest_balances`
  - `test_blocked_set_survives_every_transform`
  - `test_current_transform_is_still_available_as_the_baseline`
- The objective weights sum to 1.05, not 1.0: BALANCED is 0.45/0.40/0.05/0.05/0.05/0.05.
  `argmin` is invariant under scaling, but read the weights as ratios.
