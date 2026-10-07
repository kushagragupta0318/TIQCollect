# TIQCollect metric definitions

Reference only. The grounded definition of every metric lives in code
(`app/services/bank/kpi_catalog.py`, `app/models/loan.py`, `app/services/bank/expected_recovery.py`);
this file restates those definitions in plain language for an LLM reading
reference material, and nothing here overrides them. A number used in a
narrative must still come from the figures passed in the prompt, never from
this file.

## DPD buckets (days past due)

- **CURRENT** — 0 DPD.
- **BUCKET_1** — 1 to 30 DPD.
- **BUCKET_2** — 31 to 60 DPD.
- **BUCKET_3** — 61 to 90 DPD.
- **NPA** — 90+ DPD (Non-Performing Asset).

## Collection Efficiency

Collections verified through agencies, divided by collectible due, month to
date. Payments a borrower made to the bank directly are shown beside this
figure, never folded into it. An agency or region with no opening reading
that month is left out of both sides of the ratio, not zeroed.

## Resolution Rate

Placements resolved this month (paid, closed or settled), divided by
placements active at any point in the month (active at month end, plus
resolved, plus recalled).

## PTP Keep Rate

Promises honoured, divided by promises that fell due this month (matured
promises only — a promise not yet due is not counted either way).

## Visit-to-Pay Conversion

Met visits followed by a verified payment on the same case within 7 days,
divided by met visits, counted only over visits that are themselves at least
7 days old (a younger visit has not had its chance to convert yet).

## Cost to Collect

Agency commission accrued plus field cost, per ₹100 collected through
agencies, month to date. Field cost counts only where a field-visit cost
rate exists for that cell; a cell with no visits costs 0 either way.

## Compliance & Integrity

100 minus breaches per 100 visits, month to date. Each of the following
counts as one breach: an attempt refused for being outside permitted contact
hours, a visit recorded outside the 100-metre geofence of the borrower's
address, a confirmed fraud finding, a visit with no recorded consent.

## Recovery vs Expected

The denominator, "expected recovery," is fixed at the moment a case is
placed with an agency: the recovery-risk model's probability of payment at
that moment, multiplied by the amount actually overdue (arrears), capped at
the loan's outstanding exposure. It is never recomputed later and never
imputed when no prediction existed at placement time — a placement with no
score at the time it was made has no expected-recovery figure, by design,
rather than a guessed one. The numerator is the agency's actual verified
recovery on that cohort.

## Roll-Forward Rate and Cure Rate

Both are exposure-weighted shares of loans moving between month-ends on the
portfolio-state ladder (CURRENT → BUCKET_1 → BUCKET_2 → BUCKET_3 → NPA, plus
RESOLVED and WRITTEN_OFF as the two exits). Roll-forward counts loans that
moved to a worse state; cure counts delinquent loans that returned to
CURRENT or were resolved. A loan with no reading at the later month-end is
excluded from both, never assumed to have stayed put or cured.
