# SYNTHETIC / SIMULATED VALIDATION — NOT PRODUCTION OUTCOMES

> **These results demonstrate that the validation pipeline operates correctly. They do not establish real-world scorecard performance.**
>
> The synthetic dataset exists only to demonstrate and test the validation
> framework before the real cohort matures. When the real 30/60/90-day
> outcomes arrive, the same framework consumes them with no change to the
> validation methodology.

## Recovery scorecard validation — 90-day horizon (SIMULATED)

| | |
|---|---|
| Cohort (predictions) | 2026-08-24 — **real**, not re-scored |
| Outcomes | **synthetic**, scenario `synthetic_strong_signal` |
| Scenario intent | A scorecard that separates well — the good case |
| Random seed | `20260825` (fixed — same input, same dataset) |
| Simulation version | `recovery-simulation-1.0.0` |
| Scorecard version | `recovery-scorecard-1.1.0` (unchanged by this run) |
| Loans in cohort | 525 |
| Bands as scored | HIGH 162 · MEDIUM 222 · LOW 141 |
| Real labels due | 2026-11-22 |

## Data quality — exclusions before findings

Every scored loan is accounted for. A loan excluded here contributes to no
number below it.

| Reason | Loans | Meaning |
|---|---:|---|
| `ADMISSIBLE` | 355 | enters every metric below |
| `IMMATURE` | 34 | the labelling checkpoint has not reached this horizon yet |
| `UNOBSERVABLE` | 115 | no case for the whole window — the ledger cannot see a payment |
| `CENSORED` | 21 | bank action (write-off / settlement / recall / death), not borrower conduct |
| `VERSION_MISMATCH` | 0 | scored by a different scorecard version — pooling would average two scorecards |
| `NO_DENOMINATOR` | 0 | total_outstanding <= 0, so no rate is definable |
| `BACKFILL` | 0 | features leak the future; never admissible |
| **Admissible** | **355** | 67.6% of 525 |

`UNOBSERVABLE` is the load-bearing one. A caseless loan is **not** recorded as
₹0 recovered: `Payment.case_id` is `NOT NULL`, so money reaches the ledger only
through a case. Recording zero would manufacture the very correlation this
validation tests for — caselessness also depresses the *score*, because
`PAYMENT_MOMENTUM` abstains with no payment path — and it would bias LOW hardest
(50 of the 115 caseless loans are LOW, against 26 HIGH).

## Section 1 — Outcome coverage

| Horizon | Synthetic observations available | Real labels due |
|---|---:|---|
| 30 days | 385 | 2026-09-23 |
| 60 days | 385 | 2026-10-23 |
| 90 days | 355 ← this report | 2026-11-22 |

⚠ **Insufficient sample:** LOW is below the 100-loan bar (LOW=80). Its band mean is
reported but must not be used to calibrate a level. Ordering evidence is
still usable. This is a **real** structural finding, not a simulation
artefact: the LOW band loses 50 of its 141 loans to caselessness before
any outcome exists.

## Section 2 — Ranking: does the order hold?

Ranking is invariant to any monotone rescaling of the predictions. It asks
only whether loans rated higher actually recovered more — which is what the
HIGH/MEDIUM/LOW **label** needs to be right, because the label orders field work.

| Band | n | Realised recovery rate | 95% CI |
|---|---:|---:|---|
| HIGH | 118 | 83.4% | [79.6%, 87.3%] |
| MEDIUM | 157 | 28.6% | [24.9%, 32.4%] |
| LOW ⚠ thin | 80 | 1.9% | [1.0%, 2.8%] |

- Monotonic HIGH ≥ MEDIUM ≥ LOW: **True**
- HIGH/LOW lift: **42.87** (≥ 2.0 is meaningful separation)
- HIGH and LOW confidence intervals disjoint: **True**
- LOW loans above the HIGH median: **0.0%** (< 15% is healthy)
- Spearman ρ: **0.866** (≥ 0.3 is a usable ordering)

## Section 3 — Calibration: are the levels right?

**A different question from Section 2, and it fails independently.** Ranking
asks whether the *order* is right; calibration asks whether *38.8% means 38.8%*.
A scorecard can rank perfectly and be uniformly miscalibrated (the lever is the
intercept, `BASE_RATE`), or be calibrated on average and rank poorly (the lever
is the weights). The label needs ranking; the rupee figure needs calibration.

| Segment | n | Predicted | Realised | Difference |
|---|---:|---:|---:|---:|
| **Overall** | 355 | 41.1% | 40.8% | -0.3% |
| HIGH | 118 | 64.7% | 83.4% | 18.7% |
| MEDIUM | 157 | 37.2% | 28.6% | -8.5% |
| LOW | 80 | 14.0% | 1.9% | -12.1% |

- Calibration-in-the-large (overall bias): **-0.3%** (tolerance ±5%)
- Calibration slope: **1.61** (1.00 perfect; < 1 = predictions over-spread)
- Bias spread across bands: **30.8%** — a roughly *constant*
  bias is an intercept problem; a band-dependent one is a spread problem.

## Section 4 — Operational lift

The metric that maps to actually reallocating agents. Random targeting captures
20% of the money in 20% of the loans; anything near that means the label does
not change where the team should go, whatever the correlations say.

| | |
|---|---:|
| Top 20% by predicted money holds | **73.7%** of realised recovery |
| Random 20% baseline | 20.0% |
| Capture lift | **3.68×** (< 1.75 barely changes the work) |
| Loans in the top 20% | 71 of 355 |

## Section 5 — Collateral segmentation

`SECURITY` is the largest single weight (±0.16) and the only binary factor, so
it dominates the score by construction. The question this section answers is
whether the label still orders loans **within** a collateral class — if the
ordering vanishes inside each group, the label is an expensive proxy for
`loan_type` and the honest thing is to say so.

| Group | n | HIGH/LOW lift | Spearman ρ |
|---|---:|---:|---:|
| SECURED | 127 | 51.23 | 0.827 |
| UNSECURED | 184 | 44.46 | 0.632 |
| UNDETERMINED | 44 | 38.01 | 0.859 |

## Section 6 — Factor diagnostics

**Diagnostics only.** A factor whose contribution tracks the scorecard's own
error is a candidate for investigation — with eight factors, some correlation
is expected by chance, and correlation is not causation. Nothing here licenses
a re-weighting.

| Factor | n | Correlation with error | Mean points |
|---|---:|---:|---:|
| `SECURITY` | 311 | 0.588 | -0.0293 |
| `AGEING` | 355 | 0.182 | -0.0624 |
| `PAYMENT_RECENCY` | 355 | 0.114 | 0.0711 |
| `ARREARS_SHARE` | 355 | 0.107 | 0.0562 |
| `LEGAL_POSTURE` | 56 | 0.062 | 0.0124 |
| `PENAL_DRAG` | 355 | 0.050 | -0.0097 |
| `PAYMENT_MOMENTUM` | 245 | -0.019 | 0.0651 |
| `NPA_STATUS` | 115 | — | -0.0600 |

On synthetic data the strongest of these is **planted**: this scenario adds a
`security_effect` of 0.05 to the true recovery rate on
top of the ±0.16 the scorecard already scores, precisely so this diagnostic has
a known signal to surface. That `SECURITY` shows up is evidence the **diagnostic
works**, not a discovery about the book.

## Section 7 — Verdict

| Metric | Threshold | Actual | Result |
|---|---|---:|---|
| Monotonic HIGH ≥ MEDIUM ≥ LOW | True | True | **PASS** |
| HIGH/LOW lift | ≥ 2.0 | 42.87 | **PASS** |
| Spearman ρ | ≥ 0.3 | 0.866 | **PASS** |
| HIGH/LOW CIs disjoint | True | True | **PASS** |
| LOW above HIGH median | < 15% | 0.0% | **PASS** |
| Calibration bias | within ±5% | -0.3% | **PASS** |
| Bias spread across bands | ≤ 5% | 30.8% | **FAIL** |
| Top-20% capture lift | ≥ 1.75 | 3.68 | **PASS** |
| Admissible per band | ≥ 100 | HIGH=118 · MEDIUM=157 · LOW=80 | **FAIL** |

- Framework verdict: **`BOTH_HOLD`**
- Candidate lever: **— none —**
- Action: **REPORT ONLY — no scorecard change is authorised by this run**

### SYNTHETIC / SIMULATED VALIDATION — NOT PRODUCTION OUTCOMES

Synthetic validation demonstrates that the validation pipeline can distinguish
recovery ranking and calibration behaviour. **This is not evidence of production
scorecard performance.** The scorecard is not validated; the *validator* is.

## Section 8 — Limitations

1. **The real outcomes have not matured.** The cohort was scored 2026-08-24; real labels for this horizon are due 2026-11-22.
2. **The outcomes above are generated from assumptions**, not observed. The
   assumption model is documented in `assumptions.md` and fixed by seed
   `20260825`. Change an assumption and these numbers change.
3. **A synthetic run cannot establish production accuracy.** It establishes that
   the pipeline computes, excludes, ranks, calibrates and concludes correctly.
4. **The `beta` assumption is doing visible work.** This scenario sets it to 1.8 — the degree to which the true recovery rate tracks the
   scorecard's own prediction. At 1.0 the scorecard's spread is *assumed* correct,
   so a strong Section 2 result is that assumption made visible rather than a
   finding. `synthetic_weak_signal` is the contrasting case and it fails.
5. **Spread is squeezed at the extremes.** Non-zero rates are drawn from a Beta,
   whose unimodality floor overrides `sigma` as the mean approaches 0 or 1 — so
   the strongest and weakest loans are less dispersed than the parameter asks.
6. **The production gate remains closed.** `RECOVERY_WRITE_LABEL = False`.
7. **The production scorecard is unchanged** — no weight, band edge, `BASE_RATE`
   or version moved. `recovery-scorecard-1.1.0` throughout.
8. **No synthetic row was written to any database.** The fixture is a file; this
   report opened no database connection.

