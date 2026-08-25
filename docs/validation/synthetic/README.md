# SYNTHETIC / SIMULATED VALIDATION — NOT PRODUCTION OUTCOMES

> **These results demonstrate that the validation pipeline operates correctly. They
> do not establish real-world scorecard performance.**

The recovery cohort was scored on **2026-08-24**, so its real outcomes do not
mature until:

| Horizon | Real labels due |
|---|---|
| 30 days | 2026-09-23 |
| 60 days | 2026-10-23 |
| 90 days | 2026-11-22 |

Everything in this directory exists so the **validation workflow** can be
demonstrated before those dates. The synthetic dataset exists only to demonstrate
and test the validation framework. When the real outcomes arrive, the same
framework consumes them with **no change to the validation methodology** — the
production command below is untouched and already works.

---

## What is real here, and what is not

The predictions are **real**: 525 loans, the bands as scored (HIGH 162 · MEDIUM
222 · LOW 141), the three rates, the speed index, the factor contributions and the
frozen `total_outstanding`. Nothing was re-scored;
`app/ml/recovery_scorecard.py` is not called to produce a single number here.

Only what happened **after** the prediction is simulated: `recovered_amount_30/60/90`,
the censoring `outcome`, and how far labelling has got. `assumptions.md` documents
exactly how, and was written before any scenario was run.

## Reproduce it

Everything below is deterministic. Same population, same seed, same files.

```bash
cd backend

# One-off, and the only step that touches Postgres. READ-ONLY: the connection
# carries default_transaction_read_only=on, so the server itself refuses a write.
# cohort_population.json is committed, so this step is not needed to reproduce
# the demo — only to rebuild it against a different cohort.
python -m scripts.generate_synthetic_recovery_validation --export-population

# Generate all three scenarios. Pure: no database, no clock, no network.
python -m scripts.generate_synthetic_recovery_validation

# Validate. No database connection is opened in --synthetic mode.
python -m scripts.validate_recovery --synthetic synthetic_baseline --horizon 30
python -m scripts.validate_recovery --synthetic synthetic_baseline --horizon 60
python -m scripts.validate_recovery --synthetic synthetic_baseline --horizon 90

# The contrasting scenarios
python -m scripts.validate_recovery --synthetic synthetic_strong_signal --horizon 90
python -m scripts.validate_recovery --synthetic synthetic_weak_signal   --horizon 90

# Re-render the stakeholder reports in this directory
python -m scripts.report_synthetic_validation --scenario synthetic_baseline
```

> On a Windows host shell, prefix with `PYTHONIOENCODING=utf-8` — the report draws
> box rules and rupee signs that the default `cp1252` console codec cannot encode.

The production command is unchanged and still validates real outcomes when they
land:

```bash
python -m scripts.validate_recovery --cohort 2026-08-24 --horizon 30
```

## Files

| File | What it is |
|---|---|
| `cohort_population.json` | the 525 **real** predictions, exported read-only. No outcomes. |
| `synthetic_baseline.json` / `.csv` | scenario fixture — real predictions + synthetic outcomes |
| `synthetic_strong_signal.json` / `.csv` | the good case |
| `synthetic_weak_signal.json` / `.csv` | the failure case |
| `assumptions.md` | how the synthetic outcomes are generated, and the known limits |
| `validation_30d.md` · `validation_60d.md` · `validation_90d.md` | baseline reports |
| `validation_90d_strong.md` · `validation_90d_weak.md` | the contrasting scenarios |

Loan ids are `sha256(salt + uuid)[:16]`. They are stable and unique, which is all
the validation framework needs, and meaningless to anyone without the database —
a file that lives in git for the life of the repository is the wrong place for
production keys. No customer name, phone, address or any other personal data is
carried across; the factor `summary` and `evidence` blobs, which contain rupee
figures and free text, are dropped at export because the framework never reads them.

## What the three scenarios showed

Run once each, from fixed seeds, and reported as found. The 90-day horizon:

| | baseline | strong_signal | weak_signal |
|---|---|---|---|
| Realised HIGH / MEDIUM / LOW | 62.6% / 23.7% / 3.8% | 83.4% / 28.6% / 1.9% | 26.5% / 15.7% / 12.6% |
| Spearman ρ | 0.740 | 0.866 | **0.184** |
| HIGH/LOW lift | 16.38 | 42.87 | 2.11 |
| LOW above HIGH median | 0.0% | 0.0% | **22.5%** |
| Calibration bias | **−8.6%** | −0.3% | **−22.6%** |
| Bias spread across bands | **11.0%** | **30.8%** | **36.3%** |
| Top-20% capture lift | 3.64× | 3.68× | 2.91× |
| Framework verdict | `SPREAD_WRONG_ORDER_RIGHT` | `BOTH_HOLD` | `ORDER_WRONG` |
| Candidate lever | `WEIGHT_SCALE_OR_BAND_EDGES` | — none — | `FACTOR_WEIGHTS` |

Three different verdicts and three different named levers, from one unmodified
framework. That is the demonstration: the pipeline discriminates rather than
approving whatever it is shown. `weak_signal` fails the ranking floor, and that
failure is reported rather than tuned away.

Two findings are **the same in every scenario** and are worth reading as real:

- **LOW never reaches the 100-loan admissibility bar** (79–86 of 141). It loses 50
  loans to caselessness before any outcome exists. This is a structural property of
  the production cohort, not a simulation artefact, and it will constrain the real
  validation on 2026-11-22 too.
- **115 loans are `UNOBSERVABLE`, not zero.** 35.5% of the LOW band against 16.0%
  of HIGH. See `assumptions.md` §1 for why recording ₹0 there would have
  manufactured the result the validation is testing for.

## What this does not do

- It does not change the scorecard. `RECOVERY_SCORECARD_VERSION` is
  `recovery-scorecard-1.1.0`, `BASE_RATE` is 0.35, the band edges are
  `((0.50, HIGH), (0.25, MEDIUM))`, and no weight moved.
- It does not open the production gate. `RECOVERY_WRITE_LABEL = False`.
- It does not write to any database. Not one synthetic row exists in
  `repayment_score_snapshots`; the fixtures are files, and the validator opens no
  connection in `--synthetic` mode.
- It does not recalibrate anything. Every verdict still ends
  `REPORT ONLY — no scorecard change is authorised by this run`.

---

## SYNTHETIC / SIMULATED VALIDATION — NOT PRODUCTION OUTCOMES

Real production validation requires the actual 30/60/90-day outcomes. Until
2026-09-23 at the earliest, the recovery scorecard is **unvalidated**, and nothing
in this directory changes that.
