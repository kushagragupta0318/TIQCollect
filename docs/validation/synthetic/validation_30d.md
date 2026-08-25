# SYNTHETIC / SIMULATED VALIDATION — NOT PRODUCTION OUTCOMES

> **These results demonstrate that the validation pipeline operates correctly. They do not establish real-world scorecard performance.**
>
> The synthetic dataset exists only to demonstrate and test the validation
> framework before the real cohort matures. When the real 30/60/90-day
> outcomes arrive, the same framework consumes them with no change to the
> validation methodology.

## Recovery scorecard validation — 30-day horizon (SIMULATED)

| | |
|---|---|
| Cohort (predictions) | 2026-08-24 — **real**, not re-scored |
| Outcomes | **synthetic**, scenario `synthetic_baseline` |
| Scenario intent | A scorecard with real but ordinary ranking power |
| Random seed | `20260824` (fixed — same input, same dataset) |
| Simulation version | `recovery-simulation-1.0.0` |
| Scorecard version | `recovery-scorecard-1.1.0` (unchanged by this run) |
| Loans in cohort | 525 |
| Bands as scored | HIGH 162 · MEDIUM 222 · LOW 141 |
| Real labels due | 2026-09-23 |

## Data quality — exclusions before findings

Every scored loan is accounted for. A loan excluded here contributes to no
number below it.

| Reason | Loans | Meaning |
|---|---:|---|
| `ADMISSIBLE` | 394 | enters every metric below |
| `IMMATURE` | 0 | the labelling checkpoint has not reached this horizon yet |
| `UNOBSERVABLE` | 115 | no case for the whole window — the ledger cannot see a payment |
| `CENSORED` | 16 | bank action (write-off / settlement / recall / death), not borrower conduct |
| `VERSION_MISMATCH` | 0 | scored by a different scorecard version — pooling would average two scorecards |
| `NO_DENOMINATOR` | 0 | total_outstanding <= 0, so no rate is definable |
| `BACKFILL` | 0 | features leak the future; never admissible |
| **Admissible** | **394** | 75.0% of 525 |

`UNOBSERVABLE` is the load-bearing one. A caseless loan is **not** recorded as
₹0 recovered: `Payment.case_id` is `NOT NULL`, so money reaches the ledger only
through a case. Recording zero would manufacture the very correlation this
validation tests for — caselessness also depresses the *score*, because
`PAYMENT_MOMENTUM` abstains with no payment path — and it would bias LOW hardest
(50 of the 115 caseless loans are LOW, against 26 HIGH).

## Section 1 — Outcome coverage

| Horizon | Synthetic observations available | Real labels due |
|---|---:|---|
| 30 days | 394 ← this report | 2026-09-23 |
| 60 days | 394 | 2026-10-23 |
| 90 days | 362 | 2026-11-22 |

⚠ **Insufficient sample:** LOW is below the 100-loan bar (LOW=86). Its band mean is
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
| HIGH | 135 | 17.4% | [15.9%, 18.8%] |
| MEDIUM | 173 | 7.5% | [6.2%, 8.7%] |
| LOW ⚠ thin | 86 | 0.9% | [0.6%, 1.3%] |

- Monotonic HIGH ≥ MEDIUM ≥ LOW: **True**
- HIGH/LOW lift: **18.95** (≥ 2.0 is meaningful separation)
- HIGH and LOW confidence intervals disjoint: **True**
- LOW loans above the HIGH median: **0.0%** (< 15% is healthy)
- Spearman ρ: **0.702** (≥ 0.3 is a usable ordering)

## Section 3 — Calibration: are the levels right?

**A different question from Section 2, and it fails independently.** Ranking
asks whether the *order* is right; calibration asks whether *38.8% means 38.8%*.
A scorecard can rank perfectly and be uniformly miscalibrated (the lever is the
intercept, `BASE_RATE`), or be calibrated on average and rank poorly (the lever
is the weights). The label needs ranking; the rupee figure needs calibration.

| Segment | n | Predicted | Realised | Difference |
|---|---:|---:|---:|---:|
| **Overall** | 394 | 15.2% | 9.4% | -5.7% |
| HIGH | 135 | 22.9% | 17.4% | -5.5% |
| MEDIUM | 173 | 14.5% | 7.5% | -7.0% |
| LOW | 86 | 4.4% | 0.9% | -3.5% |

- Calibration-in-the-large (overall bias): **-5.7%** (tolerance ±5%)
- Calibration slope: **0.85** (1.00 perfect; < 1 = predictions over-spread)
- Bias spread across bands: **3.6%** — a roughly *constant*
  bias is an intercept problem; a band-dependent one is a spread problem.

## Section 4 — Operational lift

The metric that maps to actually reallocating agents. Random targeting captures
20% of the money in 20% of the loans; anything near that means the label does
not change where the team should go, whatever the correlations say.

| | |
|---|---:|
| Top 20% by predicted money holds | **73.4%** of realised recovery |
| Random 20% baseline | 20.0% |
| Capture lift | **3.67×** (< 1.75 barely changes the work) |
| Loans in the top 20% | 79 of 394 |

## Section 5 — Collateral segmentation

`SECURITY` is the largest single weight (±0.16) and the only binary factor, so
it dominates the score by construction. The question this section answers is
whether the label still orders loans **within** a collateral class — if the
ordering vanishes inside each group, the label is an expensive proxy for
`loan_type` and the honest thing is to say so.

| Group | n | HIGH/LOW lift | Spearman ρ |
|---|---:|---:|---:|
| SECURED | 148 | 15.03 | 0.741 |
| UNSECURED | 200 | 8.84 | 0.484 |
| UNDETERMINED | 46 | — | 0.635 |

## Section 6 — Factor diagnostics

**Diagnostics only.** A factor whose contribution tracks the scorecard's own
error is a candidate for investigation — with eight factors, some correlation
is expected by chance, and correlation is not causation. Nothing here licenses
a re-weighting.

| Factor | n | Correlation with error | Mean points |
|---|---:|---:|---:|
| `PAYMENT_RECENCY` | 394 | -0.246 | 0.0685 |
| `SECURITY` | 348 | 0.133 | -0.0239 |
| `AGEING` | 394 | -0.107 | -0.0616 |
| `ARREARS_SHARE` | 394 | -0.096 | 0.0565 |
| `PAYMENT_MOMENTUM` | 268 | -0.079 | 0.0662 |
| `LEGAL_POSTURE` | 62 | 0.016 | 0.0134 |
| `PENAL_DRAG` | 394 | 0.006 | -0.0105 |
| `NPA_STATUS` | 124 | — | -0.0600 |

On synthetic data the strongest of these is **planted**: this scenario adds a
`security_effect` of 0.05 to the true recovery rate on
top of the ±0.16 the scorecard already scores, precisely so this diagnostic has
a known signal to surface. That `SECURITY` shows up is evidence the **diagnostic
works**, not a discovery about the book.

## Section 7 — Verdict

| Metric | Threshold | Actual | Result |
|---|---|---:|---|
| Monotonic HIGH ≥ MEDIUM ≥ LOW | True | True | **PASS** |
| HIGH/LOW lift | ≥ 2.0 | 18.95 | **PASS** |
| Spearman ρ | ≥ 0.3 | 0.702 | **PASS** |
| HIGH/LOW CIs disjoint | True | True | **PASS** |
| LOW above HIGH median | < 15% | 0.0% | **PASS** |
| Calibration bias | within ±5% | -5.7% | **FAIL** |
| Bias spread across bands | ≤ 5% | 3.6% | **PASS** |
| Top-20% capture lift | ≥ 1.75 | 3.67 | **PASS** |
| Admissible per band | ≥ 100 | HIGH=135 · MEDIUM=173 · LOW=86 | **FAIL** |

- Framework verdict: **`LEVEL_WRONG_ORDER_RIGHT`**
- Candidate lever: **BASE_RATE**
- Action: **REPORT ONLY — no scorecard change is authorised by this run**

### SYNTHETIC / SIMULATED VALIDATION — NOT PRODUCTION OUTCOMES

Synthetic validation demonstrates that the validation pipeline can distinguish
recovery ranking and calibration behaviour. **This is not evidence of production
scorecard performance.** The scorecard is not validated; the *validator* is.

## Section 8 — Limitations

1. **The real outcomes have not matured.** The cohort was scored 2026-08-24; real labels for this horizon are due 2026-09-23.
2. **The outcomes above are generated from assumptions**, not observed. The
   assumption model is documented in `assumptions.md` and fixed by seed
   `20260824`. Change an assumption and these numbers change.
3. **A synthetic run cannot establish production accuracy.** It establishes that
   the pipeline computes, excludes, ranks, calibrates and concludes correctly.
4. **The `beta` assumption is doing visible work.** This scenario sets it to 1.0 — the degree to which the true recovery rate tracks the
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

