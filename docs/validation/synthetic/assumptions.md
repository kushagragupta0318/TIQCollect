# Synthetic recovery outcomes — the assumption model

## SYNTHETIC / SIMULATED VALIDATION — NOT PRODUCTION OUTCOMES

> These results demonstrate that the validation pipeline operates correctly. They
> do not establish real-world scorecard performance.

This file documents exactly how the synthetic outcomes are produced. It was
written **before** any scenario was run, and no parameter has been changed after
seeing a validation result. That discipline is the only thing that makes a PASS in
`validation_*.md` mean anything at all: a dataset tuned until a metric passes
demonstrates nothing except that it was tuned.

---

## What is real and what is simulated

| | Source |
|---|---|
| The 525 loans | **real** — the production cohort scored 2026-08-24 |
| `recovery_potential` (HIGH/MEDIUM/LOW) | **real** — as scored, never recomputed |
| `recovery_rate_30/60/90` | **real** — as scored, never recomputed |
| `recovery_speed_index`, `recovery_evidence_coverage` | **real** |
| Factor contributions and points | **real** |
| `total_outstanding` | **real** — frozen in the snapshot's `features` blob |
| Whether the loan has a case | **real** |
| `recovered_amount_30/60/90` | **SIMULATED** |
| `outcome` (censoring) | **SIMULATED** |
| `recovery_labelled_through_days` | **SIMULATED** (labelling progress) |

`app/ml/recovery_scorecard.py` is never called to score anything. The simulation
layer models only what happened **after** the prediction — the one thing that
cannot be known until 2026-11-22.

```
REAL:        prediction → 30d real outcome → 60d real outcome → 90d real outcome
PROTOTYPE:   prediction → 30d synthetic    → 60d synthetic    → 90d synthetic
                ↑
          identical, untouched
```

---

## The generative model, step by step

For each loan, in canonical order (sorted by hashed loan id, so the seeded draw
stream cannot depend on the order Postgres happened to return rows in):

### 1. Caseless loans are never given an amount

If the loan has no case, all three `recovered_amount_*` stay `NULL` and
`recovery_labelled_through_days` is set to 90. `classify()` reads that as
`UNOBSERVABLE`.

This is not a convenience. `Payment.case_id` is `NOT NULL`, so money reaches the
ledger only through a case — a caseless loan cannot show a recovery however much
the borrower paid. Recording `0.0` would manufacture precisely the correlation
this validation is testing for, because **caselessness also depresses the score**:
`PAYMENT_MOMENTUM` reads `amount_paid_in_window`, which is unreachable without a
case, so it abstains on 100% of caseless loans against 31.7% of cased ones. No
case would become a strong predictor of "recovers nothing" on both sides of the
join at once, and it would bias LOW hardest — 50 of the 115 caseless loans are
LOW, against 26 HIGH.

The generator mirrors `attach_recovery_outcomes` here rather than inventing a rule.

### 2. A large mass at exactly zero

```
p_zero = clip(zero_floor + zero_slope · (p − BASE_RATE), 0.02, 0.95)
recovers_nothing ~ Bernoulli(p_zero)
```

where `p` is the loan's real predicted `recovery_rate_90` and `BASE_RATE = 0.35`
is the scorecard's own base, used only as a centring constant so `zero_floor`
reads as "the probability at a neutral loan".

Delinquent books do not recover a little from everyone; most accounts return
nothing in a quarter. Without this the bands would separate on the mean while
every LOW loan still recovered something, which no real book does.

### 3. The true eventual recovery rate

```
mu   = BASE_RATE + beta · (p − BASE_RATE) + base_shift + security_effect · sec
m    = clip(mu, 0.02, 0.98)
k    = max( m(1−m)/sigma² − 1 ,  1/min(m, 1−m) )
true = 0                          if recovers_nothing
     = Beta(m·k, (1−m)·k)         otherwise
```

**A Beta, not a clipped normal, and the first draft got this wrong.** Drawing
`Normal(mu, sigma)` and clipping to `[0, 1]` puts mass on both boundaries wherever
the mean is near one: 27% of HIGH loans in `synthetic_strong_signal` recovered
their entire balance inside 90 days, which no delinquent book does, and a second
spike of exact zeros landed on top of the Bernoulli in step 2 — double-counting
the thing that step already models. A Beta is supported on the open interval, so
both artefacts disappear.

The concentration `k` comes from the requested spread (`Var = m(1−m)/(k+1)`), with
a floor that keeps **both** shape parameters at or above 1 so the density stays
unimodal. Without that floor a mean pushed to the 0.98 clip produced
`Beta(0.49, 0.01)` — a U-shape piling loans at exactly full recovery, which is the
very artefact the distribution was chosen to avoid. Near the boundary `sigma` is
therefore not honoured exactly; that is the correct trade, because a mean of 0.98
cannot carry a spread of 0.15 and stay inside `[0, 1]`.

This was found by `test_the_result_is_not_artificially_perfect`, which encodes the
realism bars **as tests** for exactly this reason: the corrected model was chosen
against a criterion written down in advance, not against whether a validation
metric passed.

`sec` is +1 secured, −1 unsecured, 0 undetermined — read from the **real**
`SECURITY` factor points, using the same rule the validator uses.

- **`beta`** is how strongly the truth tracks the prediction. At 1.0 the
  scorecard's spread is *assumed exactly right*. This assumption does visible
  work: a strong Section 2 result at `beta = 1.0` is that assumption made
  visible, not a finding. `synthetic_weak_signal` sets it to 0.20 and fails.
- **`base_shift`** is a deliberate level error, so the calibration section has
  something to find. Ranking and calibration fail independently; a demo where
  both are perfect exercises neither.
- **`security_effect`** is an extra collateral effect **on top of** the ±0.16 the
  scorecard already scores — planted so the factor-vs-residual diagnostic has a
  known signal to surface. The reports say it is planted, so no reader is told a
  discovery.
- **`sigma`** is loan-level idiosyncratic variation. This is what creates the
  overlap between bands.

### 4. Timing

```
speed = clip(real recovery_speed_index + Normal(0, 0.12), 0, 1)
f30   = (0.10 + 0.45 · speed) · lag
f60   = (0.35 + 0.45 · speed) · lag

amount_90 = round(true · total_outstanding, 2)
amount_60 = round(amount_90 · f60, 2)
amount_30 = round(amount_90 · f30, 2)
```

The ramp shape is the scorecard's own (`_SHARE_30_SLOW/_FAST`,
`_SHARE_60_SLOW/_FAST`), so no variable is introduced that the scorecard does not
use. `lag < 1` assumes realised money arrives **slower** than the ramp predicts,
so the 30- and 60-day calibration has a real error to find rather than being
correct by construction. `f30 < f60 ≤ 1` for every input, so
`amount_30 ≤ amount_60 ≤ amount_90 ≤ total_outstanding` holds by construction;
the code also clamps at the paisa so rounding cannot break it.

### 5. Censoring

```
p_censor = clip(censor_base + censor_slope · (p − BASE_RATE), 0, 0.5)
outcome  ~ WRITTEN_OFF 0.40 / SETTLED 0.30 / RECALLED 0.20 / DECEASED 0.10
```

Drawn **after** the amounts, and it does not erase them — the production labeller
also measures censored rows and excludes them through `outcome`, not by refusing
to look. The vocabulary is taken from `models/repayment_snapshot.CENSORED_OUTCOMES`,
so `classify()` excludes these through the production rule.

`censor_slope < 0` makes censoring more likely on weaker loans, which is what
makes censoring worth *excluding* rather than ignoring: it is correlated with the
thing being measured.

### 6. Labelling backlog

With probability `backlog`, the 90-day amount is left `NULL` and
`recovery_labelled_through_days` stays at 60, which `classify()` reads as
`IMMATURE` at the 90-day horizon and `ADMISSIBLE` at 30 and 60.

This is a real operational state rather than an invention. Labelling is manual per
the runbook, and between 2026-10-23 and 2026-11-22 the production cohort will look
exactly like this.

---

## Scenarios

Three, each with its own seed and its own fixed parameters. They exist so that
different validation *states* can be demonstrated without ever altering one
dataset after seeing its result.

| Parameter | `synthetic_baseline` | `synthetic_strong_signal` | `synthetic_weak_signal` |
|---|---:|---:|---:|
| seed | 20260824 | 20260825 | 20260826 |
| `beta` | 1.00 | 1.80 | 0.20 |
| `sigma` | 0.22 | 0.15 | 0.30 |
| `base_shift` | −0.06 | −0.02 | −0.10 |
| `security_effect` | 0.05 | 0.05 | 0.05 |
| `zero_floor` | 0.28 | 0.30 | 0.30 |
| `zero_slope` | −0.85 | −1.40 | −0.20 |
| `lag` | 0.80 | 0.90 | 0.70 |
| `censor_base` | 0.05 | 0.05 | 0.05 |
| `censor_slope` | −0.05 | −0.05 | −0.05 |
| `backlog` | 0.06 | 0.06 | 0.06 |

Intent, stated in advance:

- **baseline** — a scorecard with real but ordinary ranking power, and a level
  error. Expected to rank and fail calibration.
- **strong_signal** — the good case. Expected to pass both, and to show the
  planted `SECURITY` residual clearly.
- **weak_signal** — the failure case. Expected to fail the ranking floor. It
  exists so the demo cannot be accused of only being able to say yes.

---

## Known limitations of the model

1. **`sigma` is not honoured near the boundaries.** The Beta's unimodality floor
   overrides it when the mean approaches 0.02 or 0.98, so the strongest and
   weakest loans are less dispersed than the parameter asks for. Unavoidable for
   a bounded quantity, and stated rather than hidden.
2. **No borrower behaviour.** There is no arrival process, no part-payment
   sequence and no interaction with field visits. The horizon split is a single
   draw scaled by two fractions.
3. **`beta` is the whole ranking story.** In a real book the true rate depends on
   things the scorecard does not measure. Here it depends on the prediction plus
   noise, so ranking power is an input to the simulation rather than an emergent
   property of it. This is the single biggest reason a synthetic PASS is not
   evidence about the scorecard.
4. **Censoring and caselessness are the only missing-data mechanisms.** Real
   validation will also meet version mismatch, backfilled rows and loans whose
   `total_outstanding` fell to zero. Those branches of `classify()` are exercised
   by unit tests, not by these fixtures — the fixtures report them as 0 with the
   row still printed, so a zero reads as "did not happen" rather than "cannot".

---

## SYNTHETIC / SIMULATED VALIDATION — NOT PRODUCTION OUTCOMES

Real production validation requires the actual 30/60/90-day outcomes, which mature
on 2026-09-23, 2026-10-23 and 2026-11-22. Until then the scorecard is unvalidated,
and nothing in this directory changes that.
