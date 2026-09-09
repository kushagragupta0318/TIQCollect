# Implementation Plan v2 — ML Platform, Data Foundation, Routing & Allocation

**Status:** planning only. No code changed.
**Date:** 2026-09-07 · **Branch:** `TIQCollect-v2-2`
> ## Implementation status — updated 2026-09-08
>
> **Workstream B (the ML factory) and the data foundation of Workstream A are
> built and committed.** `backend/app/ml/pipeline/` holds the full lifecycle,
> `backend/app/ml/simulation/book_simulator.py` generates the panel, and
> `backend/app/ml/artifacts/` carries two trained models with their pickles,
> EDA, evaluation tables and model documents. 600 backend tests pass.
>
> What this plan predicted, and what actually happened:
>
> - **§1.2 was right about the Gini alarm, for the wrong reason.** The plan
>   treated `A_scorecard roc_auc 1.0000` as possible circularity. The deeper
>   problem was that no gate existed to catch it. There is one now, and it is a
>   hard failure at 0.60.
> - **§2's `IV > 0.5 = leak` rule had to be corrected.** It is an
>   application-scorecard rule; on a behaviour model over a delinquent book,
>   eight features legitimately clear it. Split into a review threshold (0.50)
>   and a hard drop (0.80).
> - **The lift gates were bad-rate-blind.** At a 70% bad rate the maximum
>   achievable top-decile lift is 1.43x, so "suspicious above 5x" could never
>   fire. Replaced with a bad-rate-invariant measure.
> - **A constraint the plan missed entirely: serving availability.** The first
>   model selected three features the live schema does not contain. Training
>   and serving are now one contract, enforced by a test.
> - **M3 (contactability) fails its gates**, at Gini 0.190 against a 0.25 floor,
>   and the reason is data rather than modelling — see CLAUDE.md.
>
> Workstreams C (M1/M2/M4/M5), D (routing/OR) and E (maps) are **not started**.

**Supersedes** plan v1 (routing/allocation/maps), which is folded in as Workstreams D and E.

**What v2 adds over v1:** a realistic data foundation calibrated to a believable
Gini, a proper model-development factory (preprocessing → EDA → WOE/IV → VIF →
SFS → monotonicity → advanced testing → KS/Gini/decile evaluation), pickled
decision engines with a registry, and a closed feedback loop.

---

## 1. Baseline — what exists, measured

### 1.1 There is no ML *engineering* layer at all

| Capability | Present? | Evidence |
|---|---|---|
| Serialized model artifact (pickle/joblib) | **No** | `grep -rn "joblib\|pickle" backend/` → **zero hits**. `ml/models/` holds one JSON. |
| WOE / IV | **No** | zero hits |
| VIF / multicollinearity | **No** | zero hits |
| Stepwise / SFS feature selection | **No** | zero hits |
| KS statistic | **No** | zero hits |
| Gini | **No** | `recovery_scorecard.py:30` explicitly states *"no fitting… therefore NO AUC, NO Gini"* |
| PSI / CSI drift | **No** | zero hits |
| Decile bad-rate table | **Partial** | `ml/shadow_evaluator.py:90-105` builds deciles of *expected probability*, not of a fitted score against outcomes |
| Metrics that do exist | ROC-AUC, PR-AUC, Brier | `ml/train_shadow_model.py:73` |
| EDA / plots | **No** | no matplotlib, seaborn, statsmodels, optbinning in `requirements.txt` |

Also: `global_allocator.py:16` imports `scipy.optimize` but **scipy is not in
`requirements.txt`** — it resolves only as a transitive dependency of
scikit-learn. Undeclared direct dependency; pin it.

### 1.2 The one end-to-end result we have reports a *perfect* model

`ml/models/shadow_model_metadata.json`, written 2026-09-07:

```
total_labelled_samples : 200        (train 120 / val 40 / test 40)
positive_rate          : 0.50
A_scorecard  roc_auc   : 1.0000   →  Gini = 1.00
B_eb_only    roc_auc   : 0.4938   →  Gini = -0.01
C_borrower+eb roc_auc  : 0.4825   →  Gini = -0.04
```

Read that as a whole: **the hand-weighted scorecard separates the synthetic
outcome perfectly, and the trained model does worse than a coin.** Both halves
are alarms.

- **Gini 1.00 on a 40-row test set** is the exact failure mode you are asking to
  design out. Either the synthetic labels are (partly) a function of the same
  DPD/arrears terms the scorecard reads — a circularity, not a leak in the
  point-in-time sense — or 40 rows is too few for the number to mean anything.
  **Diagnosing which is item D0, and it happens before anything is built on top.**
- **A trained model at AUC 0.48** on 120 training rows is not a model result, it
  is a sample-size result. 200 labelled rows cannot support a 15-feature model.

Both point at the same root cause: **the data, not the algorithm.** Hence
Workstream A comes first.

### 1.3 The simulator underneath is genuinely good — extend it, don't replace it

`scripts/generate_synthetic_repayment_history.py` already does the hard and
unusual thing correctly:

- simulates **forward through time**, calling the real `RepaymentService.rescore`
  at each scoring date, so features are frozen when they are true — no backfill,
  no leakage;
- drives the world with **latent variables never written to the database**
  (borrower `willingness × capacity`, agent skill per segment at `sd=0.55` on the
  logit scale) and exposes only **noisy observables** (CIBIL with a 55-point
  error);
- the generator's formula is deliberately *not* the model's formula;
- `run_synthetic_experiment.py` runs the **production** scorer, labeller, EB
  estimator and trainer against an isolated `fieldops_synth` database.

That is a better foundation than most teams have. What it lacks is **scale,
realistic difficulty, and feature breadth** — all three are parameters, not
rewrites.

### 1.4 Current data volumes

| | Now | Needed for a defensible scorecard |
|---|---|---|
| Customers | 400 | 5,000 |
| Loans | 500 | ~6,500 |
| Agents | 18 | 25–30 (with joiners/leavers for cold-start) |
| History | 6 months | 24 months (18 dev + 6 out-of-time) |
| Labelled snapshots | 200 | 60,000–80,000 |
| Events (bads) | ~100 | ≥ 15,000 |
| Candidate features | 15 | 45–60, to select 10–15 from |
| DPD coverage | 31+ only (`DPD_CHOICES` starts at 35) | CURRENT and 1–30 included |
| Missing values | effectively none | realistic MCAR + MNAR patterns |

The rule of thumb this is sized against: **≥ 1,000 events per model, and ≥ 50
events per candidate feature entering selection.** At a 25% event rate and 60
candidates, that is ~12,000 rows minimum and 60,000+ for comfortable
out-of-time validation.

---

## 2. Target metrics — what "realistic" means, as gates

Defined as pipeline gates, not aspirations. **Polarity convention, fixed once:
every classification model predicts the RISK event (P(no recovery), P(not
contacted)), so decile 1 = highest score = worst, and bad rate must fall
monotonically from decile 1 to decile 10.**

| Metric | Reject | Target band | **Reject as suspicious** |
|---|---|---|---|
| **Gini** (= 2·AUC − 1) | < 0.25 | **0.35 – 0.55** | **> 0.60 → mandatory leakage investigation** |
| **KS** | < 20 | **28 – 45** | > 60 |
| KS decile location | — | peaks in deciles 3–5 | peaks in decile 1 or 10 |
| **Decile rank-order breaks** | — | **0 breaks in top 5 deciles, ≤ 1 overall** | — |
| **Top-decile lift** | < 1.5× | 2.0 – 3.5× | > 5× |
| Event rate | — | 20 – 30% | 45–55% (the current 0.50 is a synthetic artefact) |
| **PSI** train→OOT | — | < 0.10 stable | > 0.25 → population shift, do not ship |
| Calibration (Brier / HL) | — | reliability curve within ±5% per decile | — |
| Gini train − Gini test | — | < 0.05 | > 0.10 → overfit |
| Gini by segment (DPD, product, city) | — | no segment below 0.25 | — |

**The `Gini > 0.60` gate is the centrepiece of this plan.** It is enforced in
`pipeline/evaluate.py` as a hard failure that must be explicitly overridden with
a written justification recorded in the model metadata — because in collections,
a Gini above 0.60 is nearly always a leak, and the current
`A_scorecard roc_auc: 1.0` is the proof that this codebase can produce one and
report it as a success.

**Feature-level companion gate:** any single feature with **IV > 0.5** is a
leakage suspect and must be justified before it enters selection. The two gates
catch the same disease at different stages.

---

## Workstream A — Data foundation (~3 weeks, blocks everything)

### A0 — Diagnose the Gini 1.00 first *(2 days, do this before anything else)*

Run `run_synthetic_experiment.py` and answer one question: is the scorecard's
perfect separation circularity (the label generator and the scorecard share DPD
and arrears terms) or sample size? Report per-feature IV against the synthetic
label. **The answer determines how much of A1 is a redesign versus a rescale.**

### A1 — Scale and horizon

Extend `generate_synthetic_repayment_history.py` with a **tier** parameter, so
the fast loop survives:

| Tier | Customers | Months | Purpose | Runtime target |
|---|---|---|---|---|
| `demo` | 400 | 6 | unchanged — the showcase book stays exactly as it is | current |
| `dev` | 1,000 | 12 | fast iteration on the pipeline | < 10 min |
| `full` | 5,000 | 24 | model development + out-of-time | < 2 h |

⚠ The forward simulation is a day-by-day Python loop. At `full` tier it is
~730 days × 5,000 loans. **Expect to need chunked commits and vectorised
scoring**; budget time for it rather than discovering it.

### A2 — Realistic difficulty: calibrate the noise, don't target the Gini

You cannot set a Gini directly — it falls out of the signal-to-noise ratio. But
the generator's noise *is* parameterised (CIBIL error 55 points, agent skill
`sd=0.55`, betavariate draws, per-event Bernoulli noise). So:

1. expose `--noise-scale` / observable-error parameters as a config block;
2. sweep it over 5–7 settings at `dev` tier;
3. fit the reference model at each and record Gini / KS;
4. **pick the setting whose Gini lands mid-band (~0.45)** and freeze it in
   `synthetic_config.py` with the sweep table beside it as evidence.

**Honesty constraint, non-negotiable and consistent with the existing
`SYNTHETIC_WARNING` discipline:** a simulator tuned to yield Gini 0.45 proves the
*pipeline reports believable numbers*. It is not evidence about real borrowers.
Every artifact keeps the warning field.

### A3 — Realistic event rate

Drop the 0.50 positive rate to **20–30%**, consistent with
`measure_demo_realism.py`'s own reference points (contact ~45% of visits, PTP kept
50–70%). This changes which metrics are load-bearing: at a 25% event rate,
**PR-AUC and the decile table matter more than ROC-AUC**, and WOE bins need a
minimum-count constraint (≥ 5% of population and ≥ 30 events per bin).

### A4 — Feature breadth: 15 candidates → 45–60

WOE/IV/VIF/SFS only earn their keep if there is something to select *from*.
Sources, cheapest first:

- **Already on the models and unused** (CLAUDE.md known issue 8): `Customer.city`,
  `Loan.loan_type`, `Loan.branch_code`, `Customer.segment`, language.
- **Behavioural aggregates over the payment ledger** — trailing 3/6/12-month
  paid ratio, payment count, max consecutive misses, days-since-last-payment
  buckets, EMI-to-outstanding ratio, arrears ratio, penal-charge share.
- **Promise history** — PTPs set / kept / broken, kept-ratio, days-to-break.
- **Visit/contact history** — visit count, contact rate, adverse outcomes,
  not-met reason mix, distinct agents seen, days since last contact.
- **Bureau-shaped attributes** — CIBIL band, enquiry count, other-lender
  delinquency, credit vintage, utilisation. *(Synthetic, but they are what a real
  bureau pull provides, so the pipeline must handle them.)*
- **Agent-side** — the existing `eb_shrunk_win`, `eb_evidence_n`, tenure, tier.

**Every one must be point-in-time.** `RepaymentService.build_features` already
refuses to run without `as_of`, and `_FORBIDDEN_FEATURE_KEYS` **raises** on a
system output. Extend both lists as features are added — the guard is only as
good as its list.

### A5 — Realistic imperfection

Without these, half the pipeline is untestable:

- **Missingness**: MCAR on bureau fields (~8–15%) *and* MNAR (CIBIL missing more
  often for thin-file borrowers) — so that "missing as its own WOE bin" is
  exercised rather than theorised.
- **Full DPD spectrum**: add CURRENT and 1–30 accounts. Today `DPD_CHOICES` starts
  at 35, which both inflates DPD's apparent power and leaves the binner nothing
  to bin at the low end.
- **Temporal variation**: seasonality plus one shock month (a festival, a policy
  change), so PSI/CSI monitoring has something real to detect and out-of-time
  validation is a genuine test.
- **Agent churn**: joiners and leavers, so EB cold-start and the agent-fit model's
  new-agent behaviour are exercised.
- **Duplicates, typos, out-of-range values** in the ingest CSV, so preprocessing
  is tested against dirt.

### A6 — Routing ground truth *(feeds Workstream D)*

Synthesise realised travel with **hour-of-day congestion multipliers** (a morning
and evening peak), weather/monsoon effects and random incidents, so that the
travel-time model has something to learn that OSRM does not know. Persist to the
`beat_leg_actual` table (D0.3). **This is the substitute for Google's traffic
layer, and it is why not buying Google Maps costs you nothing here.**

---

## Workstream B — The ML factory (~4 weeks, parallel with A after A1)

### B1 — Structure

```
backend/app/ml/
  pipeline/
    config.py       ModelSpec: target, horizon, features, bin rules, gates
    data.py         point-in-time extraction (as_of mandatory, leak guard)
    preprocess.py   missing policy, outlier capping p1/p99, special values,
                    rare-category grouping, type coercion
    eda.py          univariate, bivariate, missing map, correlation matrix,
                    event-rate-by-bin — all plots to PNG
    binning.py      coarse classing, MONOTONIC binning, WOE transform, IV
    selection.py    IV filter → correlation → VIF → SFS → sign/monotonicity
    train.py        logistic scorecard (champion) + GBM (challenger), calibration
    scorecard.py    PDO scaling → integer points, reason codes from WOE
    evaluate.py     KS, Gini, decile table, lift, PSI/CSI, reliability, gates
    testing.py      leakage, adversarial validation, ablation, bootstrap CI,
                    segment stability
    registry.py     joblib save/load, versioning, hashing, sklearn-version lock
    monitor.py      drift + performance tracking, retrain triggers
    report.py       Model Development Document (HTML/PDF) per version
  artifacts/<model>/<version>/
    model.joblib          ONE sklearn Pipeline: preprocess → WOE → estimator
    binning.joblib
    metadata.json         data hash, git sha, features, params, ALL metrics,
                          sklearn version, SYNTHETIC_WARNING
    evaluation/
      decile_table.csv  ks_table.csv  psi.csv  gate_results.json
      plots/*.png
    MDD.html
```

**One pickle, not five.** The artifact is a single fitted `sklearn.Pipeline`
containing preprocessing, WOE transform and estimator, so a saved model cannot
be applied without the exact transformations it was fitted with — the most common
production failure in scorecards.

### B2 — The development sequence (each model runs all of it)

**Preprocessing** — missing → its own WOE bin, never imputed in a scorecard;
outliers capped at p1/p99; special values (-99999 style) as separate bins; rare
categories grouped at < 5%.

**EDA, all plotted** — distributions and missing map; event rate by decile of each
feature; **correlation matrix heatmap**; IV bar chart; bivariate WOE-trend plots;
target rate over time; segment cuts by DPD / product / city. Written to
`evaluation/plots/` and embedded in the MDD.

**Binning & WOE** — coarse classing, then **monotonic fine classing** so WOE moves
in one direction across the bins of an ordinal feature. Constraints: ≥ 5% of
population and ≥ 30 events per bin; non-monotonic features are re-binned or
dropped, not shipped.

**Selection, in this order** (each step logged with what it removed and why):

1. **IV filter** — keep `0.02 ≤ IV ≤ 0.5`. **IV > 0.5 flagged as a leakage
   suspect**, requiring written justification.
2. **Correlation** — drop `|ρ| > 0.7`, keeping the higher-IV member.
3. **VIF** — iteratively drop `VIF > 5`.
4. **SFS** — forward/stepwise on the WOE-transformed logistic, stopping on
   p-value (0.05) and Gini gain (< 0.005).
5. **Sign & monotonicity check** — every surviving coefficient must carry the
   business-expected sign. **A wrong-sign feature is dropped however
   significant**, because a scorecard that says higher DPD is safer cannot be
   defended to a credit committee.
6. Target **10–15 final features**.

**Champion / challenger** — champion is **WOE + logistic regression**
(interpretable, monotonic, produces reason codes); challenger is
**HistGradientBoosting / XGBoost with monotone constraints**. Both are evaluated;
the GBM ships only if it beats the scorecard by a **material** margin (≥ 0.05
Gini) on out-of-time data. Both already in `requirements.txt`.

**Scorecard scaling** — PDO = 20, base 600 at 50:1 odds, WOE bins to integer
points. This is what makes the output legible to a manager, matching the repo's
existing "a manager can explain why one case sits above another" standard.

**Advanced testing** — leakage sniff (per-feature IV vs a time-shuffled sample);
**adversarial validation** (train vs OOT discriminator; AUC > 0.7 means the
populations differ); ablation; permutation importance; SHAP for the GBM;
**bootstrap CI on Gini**; k-fold **plus** out-of-time; **segment stability** —
Gini by DPD bucket, product, city, agent tenure.

**Evaluation output** — the decile table is the headline artifact, **descending by
score**:

| Decile | n | Events | **Bad rate** | Cum bad rate | Lift | Cum % bads | **KS** | WOE |
|---|---|---|---|---|---|---|---|---|
| 1 (worst) | … | … | 48.2% | 48.2% | 2.4× | 24.1% | 14.2 | … |
| 2 | … | … | 36.1% | 42.1% | 1.8× | 42.2% | 24.6 | … |
| … | | | *strictly decreasing* | | | | *peaks 3–5* | |
| 10 (best) | … | … | 4.1% | 20.0% | 0.2× | 100% | 0.0 | … |

Plus KS curve, ROC, reliability/calibration curve, lift chart, PSI table.

### B3 — Decision engines (the pickle layer)

```python
class DecisionEngine:                      # one per model
    def load(cls, name, version="champion")   # from registry, checksum-verified
    def score(self, features, *, as_of) -> ScoreOutcome
```

`ScoreOutcome` carries `score`, `band`, `probability`, `reason_codes` (top
adverse WOE contributions), `model_version`, and **`is_modelled`** — the flag the
repo already uses so a UI cannot render a hand-weighted scorecard behind an "AI"
chip. Engines are loaded once at startup and cached; a
`GET /manager/ml/health` endpoint reports every loaded model, its version and
its last evaluation — mirroring the existing `GET /manager/ai/health` pattern for
the LLM seam.

**⚠ Pickle hygiene, stated because it is a real production hazard:** pickle
executes code on load. Artifacts are loaded **only** from the repo-managed
registry, never from user input; each is SHA-256 checksummed; and the **sklearn
version is recorded at fit time and asserted at load** — a pickle opened under a
different sklearn can silently change behaviour rather than fail.

### B4 — The feedback loop

- **`model_prediction`** (new, append-only): `model_name, model_version,
  entity_type, entity_id, as_of, features JSON, feature_hash, score, band,
  decision_taken, actual_outcome, outcome_attached_at`. This generalises what
  `RepaymentSnapshot` already does for one model — build it in that shape and
  the point-in-time discipline comes with it.
- **Outcome attachment** — reuse the existing labeller and horizon logic.
- **Monthly monitor job** — PSI on every feature, CSI on the score, KS/Gini/decile
  table on matured outcomes, rank-order break count → written to
  `model_performance_snapshot` and surfaced on a manager ML-health page.
- **Retrain triggers** — scheduled quarterly, or fired by `PSI > 0.25` or a
  relative Gini drop > 20%.
- **Promotion is manual.** A retrained model enters as a challenger, runs in
  shadow for one cycle, and is promoted by a person. Never auto-promote — same
  discipline as the `False`-by-default rollout gates in known issue 9.

### B5 — New dependencies

`statsmodels` (VIF, GLM with p-values), `optbinning` (monotonic binning, WOE, IV,
PSI — the standard library for exactly this), `matplotlib` + `seaborn` (EDA),
`joblib` (explicit, not transitive), **and pin `scipy`**, which is imported
directly today but undeclared.

*Alternative worth weighing: implement binning/IV/VIF in-house (~400 lines).
More code to own, but no new dependency and it matches this repo's habit of
explicit, readable rules. **Recommendation: `optbinning` + `statsmodels`** — this
is solved, well-tested territory and hand-rolled binning is where subtle bugs
live.*

---

## Workstream C — The models, each through the full B2 lifecycle

**A necessary distinction, stated plainly: IV, WOE, KS and decile bad-rate tables
are binary-classification tools. Applying them to a travel-time regression would
be theatre.** The regression models get the correct equivalents. Both suites are
built.

| # | Model | Type | Suite | Labels available |
|---|---|---|---|---|
| **M1** | Travel time per leg | Regression | correlation matrix, VIF, residual diagnostics, **quantile (P80) loss**, MAPE by hour/segment, calibration by predicted band | after A6 + D0.4 |
| **M2** | Service time per visit | Regression | same as M1 | **today** (`check_out − check_in`) |
| **M3** | Contactability P(met \| hour, dow) | **Binary** | **full WOE/IV/VIF/SFS/KS/decile** | **today** (`Visit.customer_met`) |
| **M4** | Agent × case success | **Binary** | **full suite**, agent via EB features — **never one-hot** (~18–30 agents, sparse cells) | after A1 |
| **M5** | Uplift: P(success\|A) − P(success\|B) | Binary/uplift | IPW + Qini curve, uplift deciles | after A1 + the ε-greedy slice |
| **M6** | Repayment likelihood (trained) | **Binary** | **full suite**, challenger to the existing hand-weighted scorecard | after A1; real data 2026-09-23 |
| **M7** | Recovery rate 30/60/90 | Regression | monotonicity preserved structurally | after A1; real data 2026-11-22 |

**Build order: M3 → M2 → M1 → M4 → M6 → M5 → M7.** M3 first because it is a real
binary target with real labels available *today* — it proves the entire B2
pipeline end to end on genuine data before the synthetic book is even finished.

**Two constraints that shape M4 more than the algorithm choice does:**

1. **Cardinality.** With ~18 agents and CLAUDE.md's measured 297-of-315 sparse
   (agent, segment) cells, one-hot encoding the agent overfits immediately. Use
   the EB-shrunk features (`eb_shrunk_win`, `eb_evidence_n`) — which
   `backfill_eb_features.py` already builds point-in-time — or a **hierarchical
   logistic with a random intercept per agent**, which is the principled version
   of the shrinkage the codebase already hand-tunes at `smoothing_k = 10.0`.
2. **Confounding.** Historical (agent, case, outcome) rows were produced *by the
   current allocator*: good agents got good cases. A model fitted on them learns
   the allocator. `AllocationDecision.score_breakdown` persists the full scoring
   of every decision ever made — that is a **stored propensity**, and most teams
   have no such thing — so IPW / doubly-robust correction is available
   retrospectively. But only randomisation makes it clean. See Decision 1.

---

## Workstream D — Routing & OR (from plan v1, condensed)

### D0 — Foundations
- **D0.1** Self-host OSRM as a 9th compose service. Today `OSRM_BASE_URL` defaults
  to `router.project-osrm.org`, the public demo server — rate-limited, no SLA,
  production use outside its policy.
- **D0.2** `optimize_route()` returns a `RouteResult` (order, leg seconds/metres,
  **geometry polyline**, `source`). Add `fetch_osrm_route()`. Today only `/table`
  is called, so **no road geometry exists anywhere** and any map draws straight
  lines.
- **D0.3** Migration: `Beat.route_geometry / route_legs / route_source /
  actual_distance_km / actual_duration_minutes`, plus append-only
  **`beat_leg_actual`**. ⚠ Known issue 5: `seed_data.py` does
  `drop_all`/`create_all` and never stamps `alembic_version` — model and
  migration must land together.
- **D0.4** Nightly job reconstructing realised legs from `AgentLocation` +
  `Visit.check_in/check_out`. **This is M1's training set.**
- **D0.5** `tests/test_routing.py` — the module has **none** today.

### D1 — Fix what is already broken
- **D1.1** `planner_service.py:517-527` fetches the OSRM matrix inside
  `optimize_route` and then **throws it away**, recomputing distance as
  Haversine × 1.15. **Every beat ETA is crow-flies even when OSRM answered.**
- **D1.2** The nightly build passes **no time windows** — VRPTW is implemented but
  only the agent's on-demand re-optimise uses it, so RBI contact hours are not a
  constraint on the planned route.
- **D1.3** Replace the per-agent TSP loop with **one multi-vehicle CVRPTW per
  manager**. This also fixes A1-routing: Stage 2 "route feasibility validation"
  is currently a 22 km straight-line threshold that never calls the solver.
- **D1.4** **Prize-collecting disjunctions** with drop penalties proportional to
  expected recoverable rupees — the solver drops the *least valuable* stop when
  the day does not fit, instead of the *furthest*. This is the classical-OR
  answer to feature #5, and needs no ML.
- **D1.5** Collapse the three service-time constants — `15`
  (`AVG_VISIT_DURATION_MINUTES`), `20` and `30` (both inline in
  `planner_service.py`) — into M2's estimator.

### D2 — Wiring, behind flags
M1 enters as the **`matrix_provider`** argument. `routing.py`'s own docstring
already specifies this seam: *"write a function with the same signature and pass
it as matrix_provider — OR-Tools' role is unaffected either way."* **Zero solver
changes.** M4/M5 replace only `affinity_score` in the allocator utility; all six
terms, the objective table and **every hard gate** stay untouched.

Gates, both `False` by default: `ROUTING_USE_LEARNED_TRAVEL_TIME`,
`ALLOCATOR_USE_LEARNED_AFFINITY`.

**Measurement protocol, copied from the `spec_match` fix (known issue 10):** load
the pre-change file with `git show HEAD:`, run both over the same book, report
**% assignments moved**, **expected-recovery delta**, and — the safety property —
**that the BLOCKED set is identical**. Anything that moves the BLOCKED set is a
compliance change, not a scoring change, and stops the release.

**⚠ Determinism:** `PATH_CHEAPEST_ARC` with no metaheuristic is deliberate —
identical inputs give identical plans, which is what makes the audit trail mean
something. If D1.3/D1.4 need `GUIDED_LOCAL_SEARCH`, it ships with a fixed time
limit *and* seed, pinned by a test.

---

## Workstream E — OpenStreetMap / Leaflet (~2 weeks, parallel, needs only D0.2)

**Leaflet 1.9.4 and `@types/leaflet` are already installed**, and
`ManagerLiveMapPage.tsx:147-151` already runs `L.map` + OSM tiles + polyline
trails. The OSM stack is proven in this repo. **No Google paid product is needed
anywhere.**

- **E1** Extract a shared `<MapCanvas>` into `components/map/` — three pages
  already touch Leaflet; one-definition rule.
- **E2** Give `BeatMapPage` a real map. It has **none today** — a decorative
  `<svg viewBox="0 0 300 120">` (line 367) and `window.open('google.com/maps/dir/…')`
  deep-links (lines 58, 74). Numbered stops in beat order, the OSRM polyline from
  D0.2, live agent position, tap-to-case-detail. **Keep the Google deep-link as
  the "Navigate" button** — it is the free URL scheme, needs no key, and correctly
  hands off to whatever nav app is on the phone. Stop coordinates are already on
  the wire, so this is almost pure frontend.
- **E3 ⚠** Tile hosting is a real decision: OSM's public tile server explicitly
  rules out heavy/commercial use. Self-host from the same extract that feeds OSRM
  (one download serves both) or take a free-tier vector provider. Decide before a
  customer demo, not during one.
- **E4** Manager route review: tomorrow's plan per agent, colour-coded, drag-to-
  reorder writing back to `Beat.ordered_case_ids` + `manually_modified = True`.
  **That flag is a free supervised label** — a manager overriding the plan is a
  labelled example of where the optimiser is wrong. Log the before/after order,
  not just the boolean.
- **E5** Nominatim for geocoding. The only real capability gap versus Google is
  live traffic — which is precisely what M1 learns back from your own GPS trails.

---

## Sequencing

| Wk | A · Data | B · Factory | C · Models | D · Routing | E · Maps |
|---|---|---|---|---|---|
| 1 | **A0 diagnose Gini 1.00** | B5 deps | | D0.1–0.2 | |
| 2 | A1 tiers, A3 event rate | B1 structure, preprocess | | D0.3–0.5 | E1 |
| 3 | A2 noise calibration sweep | B2 binning/WOE/IV | **M3 contactability** | D1.1–1.2 | E2 |
| 4 | A4 feature breadth | B2 selection, VIF, SFS | M3 → gates | D1.3 CVRPTW | E2 |
| 5 | A5 imperfection, A6 routing GT | B2 evaluate: KS/Gini/decile | M3 ships shadow | D1.4 prize-collecting | E3–E4 |
| 6 | `full` tier generation run | B2 testing, B3 engines | **M2 service time** | D1.5 | E4 |
| 7 | | B3 registry, B4 loop | **M1 travel time** | D2 flags | |
| 8 | | B4 monitor, MDD report | **M4 agent fit** | D2 shadow diff | |
| 9–10 | | | M4 gates, **M5 uplift (IPW)** | D2 measured promotion | |
| 11–12 | | | **M6 repayment challenger** | | |

**Weeks 1–5 of D and E need no ML and no new data** — they are worth doing on
their own merits and are the precondition for M1. **M3 on week 3 is the first
real proof of the whole B2 pipeline**, on genuine labels rather than synthetic
ones.

---

## Decisions needed

1. **⚠ Blocking — the ε-greedy randomisation slice.** Route ~5% of daily eligible
   assignments to a uniformly-random *eligible* agent (all hard gates still
   applied) and stamp the propensity onto `AllocationDecision`. This is the only
   thing that makes M4/M5 causally credible, it costs a little expected recovery
   now, and **every month it is deferred pushes trustworthy agent-fit modelling
   back by a month.** It is the one item in this plan that changes what agents
   are asked to do, so it needs a person's sign-off, not mine.
2. **Gini band.** Proposed: target 0.35–0.55, hard gate at > 0.60. Confirm, or
   name the band your risk function expects.
3. **`optbinning` + `statsmodels` vs in-house.** Recommendation: use the
   libraries. Hand-rolled binning is where subtle bugs live.
4. **Data tier sizes** — 5,000 customers / 24 months as proposed, or larger?
   Runtime of the forward simulation is the constraint.
5. **Self-hosting OSRM + tiles** — accept the disk/ops cost, or a hosted provider's
   terms.
6. **Check `outreach_optimizer` next door** (`Collections/command-center/backend/models/`)
   before building M3 — it may already answer the contactability question.

## Constraints this plan inherits and keeps

- **No output of the system may be an input to it** — `_FORBIDDEN_FEATURE_KEYS`
  *raises*. Extend it as features are added.
- **A factor with no evidence abstains**; it never scores zero silently, and
  coverage is not renormalised.
- **Nothing hand-weighted may present itself as a model** — `is_modelled` /
  `is_ml_allocated` / `ai_generated` travel on the object.
- **A weight, band edge or factor-definition change is a model change: bump the
  version.**
- **Point-in-time or nothing** — `as_of` mandatory, `is_backfill` marks rows that
  cannot be made honest.
- **Synthetic results stay labelled synthetic**, in the artifact itself.
- **Correct a wrong comment visibly** rather than deleting it.
