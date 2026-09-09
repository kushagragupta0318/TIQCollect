# TIQCollect — Field Collections Platform

Feet-on-street debt recovery: field agents visit delinquent borrowers, log visits
with GPS/photo/signature evidence, collect payments verified by a borrower OTP, and
record PTPs. A nightly engine allocates tomorrow's cases and routes each agent's
day. Managers get analytics, compliance, a live agent map, visit-evidence anomaly
detection and an LLM performance narrative over their own team.

FastAPI + Postgres + Redis + MinIO + Celery on the backend; React 19 + Vite +
TypeScript + Tailwind on the frontend. Python >= 3.12.

**Measured 2026-09-08**, not estimated: 28,187 lines under `backend/app`,
12,061 under `backend/scripts`, 17,863 under `frontend/src`, and **902 backend
tests, all passing** — up from 574 at the start of 2026-09-08, across
Workstreams D and E, segment calibration, the allocator promotion,
epsilon-greedy, the live-integration fixes, the model outcome labeller and the
dual-label comparison.

> **The working tree is dirty and nothing is committed.** 34 entries are modified,
> new or deleted against `86eb6d0`. If you are reading this in a fresh session, run
> `git status` before assuming the tree matches the last commit.

## Provenance — read this first

This repo was extracted on **2026-08-17** from the `Collections` platform monorepo
(`field-ops-stub/`) via `git subtree split`, so its 20 inherited commits are that
subdirectory's history with paths rebased to root. Everything after `7c30095` was
written here. 86 commits in total.

**Three copies of this codebase exist.** This one is the only one that should be
edited:

| Location | Status |
|---|---|
| **this repo** | source of truth — work here |
| `Desktop/Collections/field-ops-stub/` | frozen. Still built by the platform's `docker-compose.yml` and routed by Caddy at `fieldops.transorg.ai`. Do not edit. |
| `Desktop/TIQCollect/` | the original standalone repo (June, GitHub remotes at `transorg-engineering/TIQCollect`). Stale since 2026-08-03. Shares **zero commits** with this history — the monorepo copy was pasted, not subtree-added. |

That third copy is stale but not worthless: `.github/workflows/ci.yml` and
`backend/scripts/add_recovery_potential.py` came from it. Anything else needed from
it must be copied by hand — the histories cannot be merged.

**Remote: one, personal.** `origin` is
`github.com/sanyasirao-col/TIQCollect-product` — the owner's own account. Do
**not** push to `transorg-engineering`; that was ruled out on 2026-08-17 and
still is. Pushing to the personal remote is fine on explicit instruction.

*(This block used to read "Remotes: none, deliberately — this repo is
local-only." That stopped being true some time before 2026-08-21 and misled
anyone reading it; corrected rather than deleted so the change is visible.)*

**Branch state, verified 2026-09-07.** `origin/main` is still `8d27a14`
(2026-08-17) — `main` has not moved in three weeks. Work lives on the
`TIQCollect-v*` branches; the current branch `TIQCollect-v2-2` is at `86eb6d0`
and **is** pushed (`origin/TIQCollect-v2-2` matches). Nine remote branches exist.

Command Center (in the platform monorepo) consumes this service's
`/api/v1/manager/*` endpoints through a per-agency service login. Its
`FieldAnalytics.jsx` / `FieldCases.jsx` are **hand-maintained ports** of
`ManagerAnalyticsPage.tsx` / `ManagerCasesPage.tsx` — changes here silently drift
from those. Merge procedure and its traps: [docs/MERGING-INTO-PLATFORM.md](docs/MERGING-INTO-PLATFORM.md).

## Running it

```bash
docker compose up -d
```

`backend/.env` is required and gitignored. It was written for the platform and
refers to shared values as `${VAR}`, which no longer resolve — either substitute
real values or delete those lines and rely on the `${VAR:-default}` fallbacks in
`docker-compose.yml`.

Eight services by default: `postgres` · `redis` · `minio` · `api` · `web` ·
`celery_worker` · `celery_beat` · `seed`, plus an opt-in ninth, `osrm`, behind
the `routing` profile (`docker compose --profile routing up -d`) — it needs a
map extract that is not and cannot be in this repo; the compose block carries
the four commands that build one. The worker and beat share the API image. Every host port
is overridable by env var; the defaults are:

| Service | Host port |
|---|---|
| API | `8400` → 8000 |
| Web | `5473` |
| Postgres · Redis · MinIO | `15432` · `16379` · `19000` |

Seeding is **destructive** (`scripts/seed_data.py` drops every table and every
public-schema enum with CASCADE). `docker-entrypoint.sh` gates it on whether
`public.agents` exists, so it runs only on an empty database, and only in the API
container (`RUN_SEED=true`). Straight after a successful seed the entrypoint also
takes the demo baseline snapshot (`scripts/demo_reset --save`) — the only moment
the showcase case is provably clean.

Verify a change with all four, because each catches what the others miss:

```bash
cd backend  && python -m pytest          # 902 tests, ~186s, no DB or network
cd backend  && python -m compileall app
cd frontend && npm run build             # tsc -b + vite — the real typecheck
cd frontend && npm run lint              # 18 errors left (was 53) — see issue 1
```

**Use `npm run build`, never `npx tsc --noEmit`.** The root `tsconfig.json` is a
solution file — project references and no files of its own — so `tsc --noEmit`
resolves nothing and passes unconditionally. It reported success for weeks while
the production build was failing on a type error in `RecordVisitPage.tsx`. Both
were fixed on 2026-09-06 and CI now runs `tsc -b`.

**CI (`.github/workflows/ci.yml`) fails on the frontend lint step, and only that
step.** The backend job (3.12, `compileall`, `pytest -q`) and the frontend
typecheck both pass. The workflow triggers on pushes to `main` and on PRs.

## Layout

```
backend/app/
  api/v1/endpoints/   agent.py (33 routes, 1.1k lines) · manager.py (33, 4.0k)
                      auth.py (5) · field_ops.py (4, Command Centre contract)
                      health.py (2)
  services/           case · visit · payment · otp · auth · agent · media · notification
                      ai_report · demo · fraud · location · repayment · visit_priority
                      planner · global_allocator · ml_scoring   (17 files)
  core/               config · security · database · dependencies · errors · geo
                      routing (OSRM + OR-Tools VRPTW) · llm (provider seam)
                      transcription (Whisper seam) · storage (MinIO)
  models/             21 files → 19 mapped tables (two are dead — see below)
  ml/                 repayment_scorecard · recovery_scorecard · visit_priority
                      empirical_bayes · eligibility · allocator · repayment (tier seam)
                      recovery_validation · shadow_evaluator · train_shadow_model
  ml/pipeline/        the model factory (2026-09-08) — config · preprocess · eda
                      binning (WOE/IV) · selection (IV→corr→VIF→SFS→sign)
                      train · scorecard · evaluate (KS/Gini/decile/PSI) · testing
                      registry · engine (serving) · monitor · report
  ml/simulation/      book_simulator — a seeded, file-based synthetic collections
                      book. No database, ~1s for 120k rows. THE BASELINE:
                      recovery_risk 1.1.0 was trained on it, unchanged
  ml/simulation/ledger/  event-sourced rewrite (Phase 1, 2026-09-09) — config
                      (bands + provenance) · billing (DPD/overdue as pure
                      functions) · simulator (daily tick, events only) · panel
                      (derives the frame from the ledger) · realism (checks 1-6)
  ml/artifacts/       COMMITTED model bundles: model.joblib · metadata.json
                      scorecard.csv · eda/ · evaluation/ · MODEL_DEVELOPMENT.html
  workers/tasks/      allocation · repayment_scoring · beat_generation · ptp_reminders
                      performance_snapshot · location_retention · transcription
                      demo_daily_feed · beat_reconciliation (plan vs GPS actuals)
  scripts/            seed_data · ingest_daily · synthetic generation + validation
                      demo tooling · one-off repairs
frontend/src/
  pages/agent/        AgentHome · AgentCases · AgentCaseDetail (1.3k)
                      RecordVisit (2.3k) · BeatMap · AgentProfile
  pages/manager/      Overview (1.4k) · Cases (1.2k) · Agents (1.3k)
                      Analytics (1.6k) · Compliance · LiveMap
  pages/auth/         Login · QuickLogin · ManagerBridge
  components/map/     MapCanvas (shared Leaflet bootstrap) · BeatRouteMap
                      polyline (encoded-polyline decoder) · constants
  api · components · contexts · hooks · lib · store · types
docs/                 PLAN.md · MERGING-INTO-PLATFORM.md · case-allocation.{html,pdf}
                      recovery-calibration.html · rollback/ · validation/
ML-PLATFORM-PLAN.md   root, 570 lines, dated 2026-09-07. Planning only — its own
                      header says no code changed. Not a description of what
                      exists; read it as intent, not inventory. Its §1.2 quotes
                      `ml/models/shadow_model_metadata.json`, which is GITIGNORED
                      and regenerated per run, so those numbers cannot be checked
                      from a fresh clone.
```

Domain chain: `Customer → Loan → Case → Visit → {Payment, PTP}`, plus `Beat` (one
agent's routed day), `AllocationRun`/`AllocationDecision` (why every case landed
where it did), `RepaymentSnapshot` (the ML spine), `AgentLocation`, `FraudReview`,
`CallLog`, `AuditLog`, `AllocationSetting`, `AgentPerformance`,
`QuickLoginToken`.

`models/document.py` and `models/case_photo.py` are **dead** — absent from
`models/__init__.py` and imported nowhere, so their tables are never created.
`Document` declares `back_populates="documents"` against `Visit`, which has **no
such relationship** (verified: zero occurrences of "documents" in `visit.py`), so
importing it would break the SQLAlchemy mapper for the whole app. Delete them or
wire them up; do not import them casually. (`base.py` is the declarative base, not
a table — that is why 21 files give 19 tables.)

## The scoring layers

Five distinct scores, deliberately not one. This is the part of the codebase most
worth understanding before changing anything in `ml/`.

| Layer | Module | Question | Grain |
|---|---|---|---|
| Repayment likelihood | `ml/repayment_scorecard.py` | will this borrower pay? | loan |
| Recovery potential | `ml/recovery_scorecard.py` | what share comes back, how fast? | loan |
| Visit priority | `ml/visit_priority.py` | which case first? | case |
| Agent competency | `ml/empirical_bayes.py` | who is good at work like this? | (agent, loan type, DPD) |
| Case ↔ agent match | `services/global_allocator.py` | who gets it tomorrow? | pair |

**None of these five is a trained model.** Four are hand-weighted scorecards; the
fifth (`empirical_bayes`) is a closed-form shrinkage estimator; the allocator is a
Hungarian assignment.

*(This block used to end "the only trained artifact in the repo is
`ml/train_shadow_model.py`, which is shadow-only". That stopped being true on
2026-09-08 — corrected rather than deleted so the change is visible.)* There is
now a **sixth layer, and it IS trained**: `ml/artifacts/recovery_risk/` holds a
fitted WOE + logistic scorecard with a committed pickle, and
`services/ml_scoring_service.py` serves it. It does not replace any of the five
— it feeds one term of the sixth, `prob_recovery` in the allocator — and
**`ML_SCORING_ENABLED` is `True`**, so the nightly run makes real decisions with
it. See **The trained models** and **The allocator's expected-recovery term**
below.

*(This sentence used to read "`ML_SCORING_ENABLED` is `False`, so the running
system behaves exactly as it did." It was written before the 2026-09-08
promotion and was never updated — verified wrong on 2026-09-09 against
`core/config.py:335`. Corrected rather than deleted, because "the model is not
live" is the single most misleading thing this file could have told a reader
about a system that has been allocating with it for a day.)*

Three rules hold the set together, and all three are enforced in code rather than
by convention:

- **No output of this system may be an input to it.** `_FORBIDDEN_FEATURE_KEYS` in
  `services/repayment_service.py` **raises** if a feature dict contains one. The
  ban runs both ways: the recovery scorecard never sees the repayment likelihood
  either. Recovery is deliberately *not* `P(pay) × haircut` — kept independent so
  it can say "unlikely to pay, HIGH to recover" about a hostile borrower on a
  secured loan. That disagreement is the most useful thing the pair produces.
- **A factor with no evidence abstains; it never scores zero silently.** Its weight
  is withheld from the coverage denominator, and coverage is *not* renormalised —
  a thin borrower must read as thin, not as confidently average.
- **Nothing hand-weighted may present itself as a model.** `is_modelled` travels on
  the object (`ScoreOutcome`, `RepaymentScore`, and `LLMResult.ai_generated` for
  the LLM seam), so a UI cannot render a scorecard behind an "AI" chip by
  forgetting to check. *(This bullet used to end "There is **no AUC, Gini or
  accuracy figure anywhere** — verified". That was true until 2026-09-08 and is
  now wrong: `ml/pipeline/evaluate.py` computes Gini, KS, decile bad rate, lift,
  PSI and calibration, and every artifact under `ml/artifacts/` records them.
  Corrected rather than deleted so the change is visible.)* The rule the old
  sentence protected is unchanged and now enforced harder: the four scorecards
  still have no such figures **because they are not models**, and
  `ScoreResult.is_modelled` is `False` on every path where the engine declines.

Both scorecards carry a version string stamped onto every snapshot row —
`scorecard-1.1.0` and `recovery-scorecard-1.1.0`. **A weight, band edge or
factor-definition change is a model change: bump the version.** Rows written under
two versions answer different questions, and a model trained across both without
filtering learns two scorecards at once.

Monotonicity in the recovery scorecard is *structural*: score the eventual 90-day
rate once, then multiply by a bounded speed fraction, so `rate_30 ≤ rate_60 ≤
rate_90` holds for any input and survives any future reweighting.

`RepaymentSnapshot` is what could eventually make a trained model possible: one
frozen, point-in-time row per (loan, day) — features as they stood, the score, and
later the outcome the labeller fills in. Point-in-time correctness is the whole
value: `Loan.dpd`, `PTP.status` and `Loan.last_payment_date` are all overwritten in
place with no history, so a feature read "as it is now" leaks the future. There is
no way to call `build_features` without an `as_of` date, and `is_backfill` marks
rows that cannot be made honest.

`train_shadow_model.py` is **shadow only** — nothing writes a score from it and no
allocation path reads it. The metadata in `ml/models/` is gitignored and currently
reports a *synthetic* run, saying so in its own `SYNTHETIC_WARNING` field. Real
labels cannot mature before 2026-09-23 (30d) / 2026-11-22 (90d).

**Before training anything here, look next door.** The Collections platform
already ships trained models at `Desktop/Collections/command-center/backend/models/`
— `risk_scoring`, `recovery_forecast`, `cure_rate_engine`, `bounce_predictor`,
`transition_probability`, `borrower_segmentation`, `outreach_optimizer`, with
seven fitted `.joblib` artifacts and `model_metrics.json` beside them. The
inbound contract to reach them already exists and this repo does not use it:
`GET /api/account-context/{loan_id}` on Command Center `:8000`, §4 of
`Desktop/Collections/FIELD_OPS_INTEGRATION.md`. A calibrated recovery probability
may be a matter of *calling* those rather than fitting new ones — which is also
the only route to a genuinely calibrated number before the 90-day labels land.

## The trained models

Added **2026-09-08**. Everything above this line describes hand-weighted
scorecards; this section describes the one part of the repo that is fitted from
data, and it is deliberately **off by default**.

```
scripts/build_modelling_dataset.py --tier full   # 120k-row synthetic panel, ~2s
scripts/train_models.py                          # trains, gates, writes artifacts
```

| | `recovery_risk` | `contact_risk` |
|---|---|---|
| Question | will this account make a material payment next cycle? | will an attempted visit meet the borrower? |
| Form | WOE + logistic scorecard, GBM challenger | same |
| Out-of-time Gini | **0.5149** (95% CI 0.5035–0.5264) | 0.1904 |
| KS | **39.62** | 14.25 |
| Rank-order breaks | **0** | 0 |
| Gates | **PASS** — champion | **FAIL** — not promoted |

`recovery_risk` selects 4 of
33 candidates: `dpd`, `cibil_score`, `ptp_kept_ratio`, `overdue_amount`. Train Gini 0.4866 against
out-of-time 0.5149 — the holdout scores *higher*, which is what a model
that has not been overfitted looks like.

**Read `ml/artifacts/<model>/<version>/MODEL_DEVELOPMENT.html`** before changing
anything here. It is generated from the artifact, not from the training run, and
carries the full IV table, every selection step with what it removed and why, the
readable scorecard, the decile tables, KS/ROC/calibration/PSI plots and the
limitations.

**The gates are two-sided and they fail, they do not warn.** A model is rejected
for being too *strong* as readily as too weak — `gini_suspicious = 0.60`, because
on a book like this a Gini above that is nearly always a leak. That gate exists
because this repo has already produced and reported a perfect model: the shadow
metadata written 2026-09-07 records `A_scorecard` at ROC-AUC **1.0000** on a
40-row test set, beside a trained model at 0.4825. Nothing in the codebase could
say which of those two numbers was the alarming one.

Five things worth knowing before touching it:

- **The synthetic book is calibrated, not lucky.** `ml/simulation/book_simulator.py`
  drives a monthly panel from latent willingness / capacity / reachability that
  are never written to the panel, and its difficulty is a dial. Run
  `scripts/build_modelling_dataset.py --sweep` for the table mapping noise
  settings to achievable Gini (1.00/1.00 → 0.531; 0.60/1.90 → 0.322). A target
  Gini is not something you set, it is something a data-generating process
  produces — the sweep is the evidence.
- **Features are restricted to what the product can actually serve.** The first
  fitted model selected `utilization_pct`, `other_lender_delinq` and
  `bounce_count_6m`; none exists in the live schema, so the engine would have
  had 3 of 6 inputs and declined every request. `ml/pipeline/config.py` lists
  what was excluded and why — `FEED_ONLY_FEATURES` (a real lender has them, this
  product does not store them) and `NO_HISTORY_FEATURES` (`Loan.dpd` is
  overwritten in place, so delinquency history cannot be reconstructed honestly
  at all).
- **`contact_risk` fails, and that is the finding.** Restricted to servable
  features it reaches Gini 0.190 against a 0.25 floor. Right-party-contact models
  reach 0.25–0.40 in practice; this one cannot, because the panel is monthly (no
  time-of-day, the dominant real driver) and the product stores no address or
  phone quality. Fixing it is a data change, not a model change. Its artifact is
  written so the failure can be read; its champion pointer is absent.
- **Two numbers, two directions.** `probability` is P(bad), higher = worse, and
  every gate and decile table consumes it. `points` is the scorecard number,
  higher = better, the way a bureau score reads. They are monotonic inverses.
  Bands are fitted from the development distribution, never hardcoded — a fixed
  A-grade at 640 once put the entire book, from P(bad) 0.26 to 0.99, in band E.
- **The loop is closed but idle.** `model_predictions` (new table, migration
  `b3e7d1f90c25`) records every served score with its features; `monitor.py`
  compares matured outcomes against the artifact's development figures and
  recommends a retrain on PSI > 0.25 or a relative Gini drop > 20%. It never
  retrains and never promotes — promotion is manual, like every other rollout
  gate here. **Wired into the 19:15 task on 2026-09-09, behind a maturity
  gate** — see below.
- **`monitor.attach_outcomes` was removed on 2026-09-09.** It was a SECOND
  writer of `ModelPrediction.actual_outcome`, taking an `outcome_fn` callback —
  dead (nothing imported it) but dangerous, because it set `actual_outcome`,
  `outcome_attached_at` and `outcome_horizon_days` while **never setting
  `outcome_definition_version` or `outcome_status`**. Anyone reaching for it
  would have written labels with no record of which rule produced them, into
  the one column whose versioning exists to prevent exactly that. The same
  shape as the two `risk_score` formulas and the seven DPD-bucket spellings.
  `ml/pipeline/outcomes.py` is the only writer;
  `test_only_one_module_writes_the_outcome_label` is an AST tripwire against a
  third copy — labelled a tripwire, not proof, because a structural check
  cannot see behaviour.

**Rollout gates**, all in `core/config.py`, verified against the file on
2026-09-09: **`ML_SCORING_ENABLED=True`** (the allocator decides with it),
`ML_MODEL_VERSION="champion"`, `ML_LOG_PREDICTIONS=True`.
`GET /manager/ml/health` reports what is loaded, its gate result and its
out-of-time metrics whether or not scoring is enabled.

*(This paragraph used to say `ML_SCORING_ENABLED=False` "(nothing reads a model
score today)" and cite it as matching `REPAYMENT_WRITE_RISK_SCORE`'s
discipline. Stale since the 2026-09-08 promotion. The other two flags it was
grouped with — `REPAYMENT_WRITE_RISK_SCORE` and `RECOVERY_WRITE_LABEL` — **are**
still `False`; this one is not, and reading them as a set is how the error
survived two rewrites of this file.)*

**`ML_MODEL_VERSION` did not gate anything until 2026-09-09.** Every
`DecisionEngine.get()` call site omitted the version argument, the parameter
defaulted to the literal `"champion"`, and the setting was read in exactly one
place: the health endpoint, which reported it back as `configured_version`.
Pinning it to `1.0.0` to roll back would have changed nothing except the health
endpoint's claim about itself — it would have *confirmed* the rollback while
1.1.0 went on serving every score. `get()` now resolves `version=None` through
the setting, in one place, and an explicit `version=` still wins for the
training scripts. Pinned by `test_the_configured_version_is_what_actually_gets_served`
and `test_health_reports_the_version_it_is_actually_serving`, both of which were
confirmed to fail against the old default before the fix landed.

**Everything here is trained on SYNTHETIC borrowers** and every artifact says so
in its own `SYNTHETIC_WARNING` field. The metrics demonstrate that the pipeline
works end to end and reports believable figures. They are not evidence about real
borrowers.


## The event-sourced simulator — Phase 1 complete, 2026-09-09

```
python -m scripts.build_ledger_dataset --tier full     # ledger + panel + realism
```

**`book_simulator.py` is untouched and remains the baseline.** Nothing here is
promoted; `recovery_risk` 1.1.0 (OOT Gini 0.5136) is still champion.

**Why a second simulator.** `book_simulator` carries state in a dict and emits
aggregated features directly — `dpd` and `overdue_amount` are state variables it
updates, `ptp_kept_ratio` is computed inside its own loop. It is therefore a
second feature-engineering implementation, and nothing it produces can test the
one that serves. This package generates **events** and derives everything:

```
dpd(t)     = t − due_day(oldest instalment not covered by t) − grace
overdue(t) = billed_by(t) − paid_known_by(t)
```

Both are pure functions of (schedule, ledger, as_of), so point-in-time
correctness is a property of the arithmetic rather than a discipline. And
`paid_known_by(t)` is the sum of payments **whose VERIFIED status was in force
at t** — a payment made on day 10 and reversed on day 25 counts on day 20 and
not on day 30. The live database cannot express that distinction at all.

**Phase 1 realism: 11 of 11 gated checks PASS** (94,159 account-months, 17,884
loans, 24 months, 68,402 payments, 134,256 visits):

```
forward_flow 0.506 · cure 0.055 · NPA 0.317 · performing(0-30d) 0.312
exact-EMI 0.483 · round-number 0.145 · partial 0.347
PTP-kept 0.456 · RPC 0.483 · material-payment 0.306 · vintage-monotone 1.000
```

**Every band prints its provenance, and almost all of them are
`ASSUMPTION` — plausible figures with no external source consulted.** Only the
90-day NPA threshold is `REGULATORY`, and only because
`models/loan.dpd_bucket_for` already encodes it and this simulator *imports* it
rather than restating it. Do not quote any of these ranges as an industry fact.

**No Gini target exists anywhere in the package.** `signal_scale` and
`observation_noise` are fixed a priori; whatever discrimination results is a
Phase 2 measurement. The only thing calibrated is the intercept, by bisection to
the material-payment rate — that shifts every borrower's hazard by the same
amount on the log-odds scale, so it moves prevalence without touching any
driver's relative weight. You cannot bisect your way to a Gini with it.

**Three defects found by measuring, each fixed generatively rather than by a knob:**

- **The book was a one-way ratchet.** Arrears could only be cleared by a flat 8%
  draw regardless of size, so cures almost never happened and the book aged into
  a sink — NPA 43%, CURRENT 0.5%. People clear *small* arrears and not large
  ones, so the clear probability now depends on how many cycles behind they are.
- **The opening balance was not in the ledger.** Seasoned accounts open mid-life,
  and `panel.py` recomputed `paid` from zero, so every one read as never having
  paid: 77.5% of account-months in NPA, DPD up at 1,055 days. `loans.opening_paid`
  carries it, exactly as a production extract carries an opening balance plus a
  ledger from the extract date forward.
- **Payments scattered uniformly through the cycle.** People pay when billed, so
  the hazard now spikes on the due date; without that, and without a contractual
  grace, a perfectly performing account read as delinquent for most of every month.

**And one band was wrong, corrected visibly.** `current_share` gated DPD == 0
exactly, at 0.10–0.40. No generator could have passed it: after clearing, an
account is at DPD 0 only until the next instalment runs past grace — 5 days in
30 — so a monthly snapshot catches a perfect borrower as CURRENT ~17% of the
time. Measured CURRENT 3.0% while the whole 0–30 band held 21%: the mass was
right and the construct was wrong. Replaced by `performing_share` on 0–30 DPD.
`forward_flow_rate`'s ceiling was likewise raised 0.45 → 0.55 *after* measuring
0.4645, because an account that does not pay one instalment rolls forward
mechanically — the old ceiling was arithmetically inconsistent with the
material-payment band sitting three lines below it. Both corrections are
recorded in `config.BANDS` with their reasoning, because moving a goalpost after
seeing the result is precisely what makes a validation suite worthless.

23 executable tests in `tests/test_ledger_simulator.py`.

### Phase 2 — trained, and the gate FAILS on one check

```
python -m scripts.phase2_ledger_validation --tier full
```

`ModelSpec.RECOVERY_RISK` used **verbatim** — same features, target, split and
gates; only the version string replaced via `dataclasses.replace`, which copies.
`make_champion=False`, so **`champion.txt` still reads 1.1.0** and the live
engine is unaffected.

```
                     1.1.0 (book_simulator)      1.2.0-ledger
selected    dpd, cibil, ptp_kept_ratio,   arrears_ratio, cibil, paid_ratio_3m,
            overdue_amount                contact_rate_6m, visits_3m
OOT Gini    0.5136 [0.502, 0.525]         0.4454 [0.433, 0.458]
OOT KS      39.72                         33.20
OOT Brier   0.16985                       0.18143
calib gap   -0.0097                       -0.0056
train->OOT  +0.0279                       +0.0107   (OOT higher: no overfit)
all 13 spec gates                         PASS
```

**Check 10 was mis-specified, and its failure is preserved.** As originally
written it compared the model against **latents at `as_of` alone** and required a
gap >= 0.15. It **FAILED at 0.0036**. Diagnosed before anything was changed:

```
latents only              0.4603     observables only          0.4567
true_pay_logit alone      0.4183     BOTH TOGETHER             0.5516
```

Latents-at-as_of is **not an upper bound on what is knowable**. The two sets are
complementary — observables aggregate months of arrears and contact history that
an instantaneous latent snapshot does not contain, while the latents carry
disposition the observables cannot see. A "ceiling" that one of its own
candidates beats is not a ceiling.

**Redefined 2026-09-09.** The oracle is now the JOINT information set —
hidden latents, their three-month history, the true payment propensity, *and*
the observable features — so it is a genuine superset of what the model
receives:

```
10a  oracle gap, absolute     0.0949   >= 0.05   PASS
10b  fraction captured        0.8279   <= 0.90   PASS
--   superseded latents-only  0.0036   >= 0.15   (FAILED, kept for audit)

ORACLE (joint)  0.5516    observable  0.4567    hidden only  0.4898
```

The thresholds are grounded in the model's own sampling error, not in the
observed gap: `sd(Gini_oot) = 0.0069`, so a 95% half-width is ~0.0135 and the
0.05 floor is nearly four of them. **The simulator was not touched** — no
`signal_scale`, `observation_noise` or latent coefficient changed. The original
criterion, its value and the reason it moved are all carried in the report
output and pinned by `test_the_superseded_criterion_is_preserved_not_deleted`.

**Check 8 diagnostic, 0.8895 — and it is not what it looks like.**
`corr(dpd, arrears_ratio) = 0.947`, because both derive from the same FIFO
position: `arrears_ratio = overdue/emi` and `dpd = days since the oldest unpaid
instalment`. The selection dropped `dpd` and kept `arrears_ratio`, so the ratio
measures *the same variable twice*, not DPD dominating the model. A structural
property of the billing spine, pinned by
`test_dpd_and_arrears_ratio_are_near_duplicates_by_construction`.

**Leakage: clean, and the gate is provably load-bearing.**

```
shuffled labels        -0.0037   (structure reaches the model only via features)
point-in-time honest    0.4567
month m+1 features      0.9252   -> +0.4685 uplift
```

That last line is the important one: using future features nearly doubles Gini,
so the `as_of` boundary is doing enormous work rather than being decorative.

**PHASE 2 GATE: PASS.** 14 tests in `tests/test_ledger_phase2.py`.

### Phase 3 — the adapter, run against a rewound database

`ml/simulation/ledger/materialise.py` loads the ledger into the REAL schema and
rewinds `Loan.dpd`, `overdue_amount`, `penal_charges`, `outstanding_principal`,
`total_outstanding`, `last_payment_date`, `Customer.cibil_score`,
`Payment.status` and `PTP.status` to their value at any past day — the columns
the live schema overwrites in place. `MLScoringService.build_features` then runs
against it unchanged.

**1,180 matched (loan, as_of) pairs** over 494 loans and 5 snapshot days.
**33 features compared, all agreeing.** Adapter coverage of the spec: mean
0.9801, min 0.9394; all four champion features present in every vector.

**FOUR GENUINE DEFECTS, found only because two independent implementations were
finally comparable:**

- **A production training/serving skew in `paid_ratio_3m/6m/12m`.** The adapter
  divided by `sum(Case.target_amount)` — a FIXED figure that does not scale with
  the window — so all three ratios shared one denominator and were 3x, 6x and
  12x too large, with `_6m` and `_12m` pinned at the 1.5 clip. The model was
  trained on a denominator summed over the window's months. **119 of 202 loans
  disagreed on `_3m` and 138 of 202 on `_6m`/`_12m`, by exactly the window
  factor.** Fixed in `ml_scoring_service`; safe to correct now because none of
  `paid_ratio_*` is among `recovery_risk` 1.1.0's four selected features, so no
  served score changes today. Left alone it would have corrupted the first model
  that did select one.
- **`build_features` silently discards the time of day of `as_of`** —
  `datetime.combine(as_of, datetime.min.time())` floors it to midnight, so its
  history filters mean "before day t began", not "by the end of day t". Not a
  bug, but undocumented and easy to get wrong: a first attempt to align the
  panel to an assumed end-of-day boundary broke five features that had been
  agreeing.
- **The panel used two boundaries for one `as_of`** — `_paid_known` at `<= t`
  and `_before` at `< t` — so a payment on a snapshot day moved the BALANCE but
  not the payment HISTORY. Unified on strictly-before.
- **The simulator emitted `TWO_WHEELER`, which is not a `LoanType` member**, and
  restated the secured set instead of importing `ml/eligibility._SECURED`. Both
  now come from the product's own definitions.

Plus two smaller ones: bureau scores were fractional where the column is
`INTEGER` (156/202 disagreed on the decimal alone), and the materialiser wrote
events at midday, which made `(midnight(t) - midday(t-21)).days` floor to 20
against the panel's 21 (130/202).

**PIT tests.** Score at day 90, advance the database to 270, rewind to 90 — the
vector must be byte-identical, and is. A promise resolved after `as_of` still
reads `ACTIVE`; a payment reversed after `as_of` still reads `VERIFIED`; a
payment after `as_of` cannot appear in `last_payment_date`.

Phases 1 and 2 were rerun on the corrected panel and both still PASS — Phase 1
11/11, Phase 2 OOT Gini 0.4423, oracle gap 0.0988, fraction captured 0.8194.
47 tests in `tests/test_ledger_phase3_adapter_equality.py`.

### Phase 4 — the whole lifecycle, replayed

```
python -m scripts.phase4_ledger_lifecycle            # healthy
python -m scripts.phase4_ledger_lifecycle --stress   # concept drift on
```

`predict at as_of -> advance 30 days -> label -> compare -> monitor`, eight
cohorts, every stage running the PRODUCTION code. Scored with `1.2.0-ledger`
through `settings.ML_MODEL_VERSION` — the rollout gate that only started working
on 2026-09-09 — and `champion.txt` still reads 1.1.0.

```
                            HEALTHY              STRESS (concept drift)
predictions                  11,200                11,200
labelled                      7,873                 8,017
censored                      1,047                 1,079
comparable / disagree    5,904 / 660 (11.2%)   6,026 / 654 (10.9%)
live Gini vs dev        0.3885 / 0.4423       0.3515 / 0.4423
relative drop                 12.2%                 20.5%
KS                            29.37                 26.85
Brier                        0.19519               0.20011
calibration gap             -0.0068               -0.0034
score PSI / max feat PSI  0.0047 / 0.0111      0.0045 / 0.0124
VERDICT                      healthy          retrain_recommended
```

**Both directions hold on the SAME thresholds** — nothing in `monitor.py` was
relaxed. Healthy stays quiet; drift fires with *"Gini has fallen 21% below its
development value (0.352 against 0.442)"*.

**PSI stays flat in both runs, and that is the design working.** Concept drift
moves the RELATIONSHIP, not the population, so it is invisible to PSI and only
the Gini path can catch it. Covariate drift is the other half and moves PSI
instead. The two are separately switchable for exactly this reason.

**Three defects found getting the healthy run to pass honestly**, each diagnosed
before anything was changed and none by weakening a threshold:

- **Burn-in contaminated PSI.** Snapshots from day 120 gave
  `days_since_last_contact` PSI 0.76 and fired a retrain on a healthy book — its
  *maximum* is 60 at day 60 and 120 at day 120, so the reference third could not
  express a long no-contact gap. The world was starting, not drifting. Scoring
  now begins at day 400, past the longest 365-day lookback.
- **The harness scored loans that did not exist yet.** 13,882 of 39,800
  predictions (34.9%) hit `NO_BASELINE` — accounts with nothing billed. Only the
  live pool is scored now, as production does.
- **`compare_all` re-derived the model's label instead of reading the one
  `attach_outcomes` committed**, so state that moved on after maturity could
  reclassify a settled label as censored. That dropped the comparable bad rate
  from 0.70 to 0.51 and fired the trigger. It now honours the attached label.

**And the first stress profile did not stress.** Onset at 0.75 of the book left
five of eight cohorts undrifted and the worst-affected slice at x0.84 signal;
per-slice Gini showed no trend (0.4441, 0.4565, 0.4410, 0.4539) and the monitor
correctly said healthy. Onset moved to 0.35 and signal now falls to 45% —
severe by construction, not tuned until the trigger fired.

**The margin is thin and worth knowing: a 55% cut in the latent signal produces
only a 20.5% relative Gini drop, against a 20% trigger.** The monitor fires, but
this scenario sits close to its detection boundary — real decay milder than this
would pass unremarked until it compounded.

17 tests in `tests/test_ledger_phase4_lifecycle.py`. **Phase 5 not started.**

## The allocator's expected-recovery term — PROMOTED 2026-09-08

The nightly SMART allocation now scores its pool with `recovery_risk` and uses
the calibrated probability in `prob_recovery`, together with a rescaled value
transform. **The two ship as one change and cannot be rolled back separately** —
see the pairing below. `ML_SCORING_ENABLED=True` is the switch; setting it False
returns the allocator to its pre-2026-09-08 behaviour in a single act.

```
python -m scripts.shadow_allocation_ml         # before/after on one book
python -m scripts.value_transform_study        # all five transforms, multi-seed
python -u -m scripts.value_transform_sensitivity   # the constants' robustness
```

### What changed, and the measurement

| | before | after |
|---|---|---|
| `prob_recovery` | `clamp(shrunk_win x tier, 0.20, 0.85)` | `clamp(P(pay) x eb_multiplier x tier, 0.02, 0.85)` |
| value transform | `log1p(EV/25_000)/log1p(12)` | `log1p(EV/1_000)/log1p(20)` |
| realised recovery | baseline | **+28.0%, 8 of 8 seeds** (+18.4% to +39.6%) |
| realised recovery rate | 0.2203 | **0.2473** |
| forecast bias vs realised | +94% | **+9.5%** |
| Brier | 0.2172 | **0.1435** (+34%, 5 of 5 seeds) |
| value / proximity influence | 0.120 | **0.994** |
| BLOCKED set | — | **identical, every transform, every seed** |

Measured on 1,200-case, 40-agent books at eight seeds the model was not trained
on. Nominal objective weights are UNTOUCHED — 0.45 expected_recovery, 0.40
proximity. That was the point: the term was not underweighted, it was unable to
use the weight it had.

### Why it is not `affinity_score` that the model replaces

`affinity_score` is AGENT-side — what share of target *this agent* recovers on
work of this kind. `recovery_risk` is BORROWER-side and cannot see the agent, so
substituting it would score every agent identically on a case. The model's place
is `prob_recovery`, which previously carried no borrower-side input at all.
`eb_multiplier` is a ratio centred on 1.0 (`shrunk_win / segment_prior`, bounded
[0.75, 1.25]), so this is a calibrated base rate modulated by a bounded relative
agent effect — not the 2026-09-03 defect of multiplying two estimates of the
same quantity.

### Why the value transform had to move at the same time

`_value_score` was calibrated against TARGET amounts (its own 2026-09-02 note
quotes Rs 7,031 / 25,901 / 211,701 / 500,000) and is fed EXPECTED values. While
`prob_recovery` was effectively a constant 0.43 that was a scale factor and
nothing worse. With a calibrated probability — mean 0.27, correlated **-0.41**
with balance — it becomes structural: **100% of expected values fall below the
25,000 knee**, so the curve runs in its near-linear region and then divides by
`log1p(12) = 2.565`. The term used 3.9% of its available range, and proximity
outvoted it 8:1 on realised spread.

That is why `calibrated probability + log_current` **loses 10.1% of realised
recovery in 8 of 8 seeds** while raising the case-level recovery rate: a correct
probability is anti-correlated with balance, the old scale compresses the
result, and the plan drifts onto nearby small cases.

**The pairing is enforced in code, not by two settings agreeing.**
`PlannerService._ml_recovery_probabilities` returns nothing when scoring is off
or fails, and the transform falls back to `log_current` in the same expression.
The two other corners of that square were never measured, and one of them is
actively bad.

### The constants, and why they are not fitted

`VALUE_EV_KNEE_INR = 1_000`, `VALUE_EV_REFERENCE_INR = 20_000`. Fixed rupee
figures, never pool-derived — a pool-relative scale would make an identical case
score differently depending on what else was planned that night. A sweep over an
8x span of knee and a 4x span of reference beat the baseline in 3 of 3 seeds at
**every** grid point, +21.2% to +38.3%, BLOCKED identical throughout:

```
 knee     ref   INR% mean  wins  val/prox   Q5 share      (baseline Q5 = 0.261)
  500   10000       +38.3   3/3     1.032      0.294
 1000   20000       +31.3   3/3     0.910      0.273   <- chosen
 4000   40000       +21.2   3/3     0.601      0.248
```

So the result is a property of the RESCALING, not of two numbers. 1,000/20,000
is the middle of the grid rather than its best corner (500/10,000, +38.3%):
taking the maximum would be fitting the constants to the measurement, and the
mid choice leaves the value term's influence just *below* proximity's.

### Balance did not take over

The 2026-09-02 failure was an unbounded linear term letting a Rs 500,000 case
score 12.0 against a proximity maximum of 1.0. Every candidate here is bounded to
[0,1], so the real test is behavioural — the plan's mix by balance quintile:

```
plan              Q1      Q2      Q3      Q4      Q5
baseline       0.166   0.178   0.188   0.207   0.262
log_current    0.189   0.197   0.197   0.205   0.212   <- drifts to SMALL cases
log_rescaled   0.122   0.174   0.197   0.228   0.279   <- +1.7pp on Q5
```

`sqrt` (+21.8%) and `power_035` (+22.7%) also beat the baseline 8/8 and are
retained as alternatives; `linear_capped` is weakest (+9.4%, 7/8).
**`log_current` is retained permanently** — every before/after figure above is
expressed against it, and `test_current_transform_is_still_available_as_the_baseline`
stops it being deleted.

### Pinned by regression tests

`test_old_transform_starves_the_value_term` (ratio must stay under 0.40),
`test_promoted_transform_gives_value_influence_comparable_to_proximity`,
`test_rescaling_does_not_hand_the_plan_to_the_largest_balances`,
`test_blocked_set_survives_every_transform`, and
`test_promotion_settings_are_paired`.

### What this rests on, and what it does not

Synthetic books throughout — no database is reachable in this environment. The
figures describe the ALLOCATOR's arithmetic under a calibrated probability,
which is a property of the two formulas and reproducible from the seeds. They
are not evidence about real borrowers, and the first thing to do against a live
book is re-run `scripts/value_transform_study` there before trusting the +28%.

Model out-of-time, from the artifact rather than recomputed: `recovery_risk`
1.1.0, Gini 0.5136, KS 39.72, n 30,000, gates PASS. Full records in
`app/ml/artifacts/recovery_risk/1.1.0/shadow/`.

## Epsilon-greedy exploration — live at 10% from 2026-09-08

`ALLOCATOR_EXPLORATION_RATE = 0.10`. Each night, 10% of assignments go to a
**random eligible agent** instead of the best-scoring one. This is the only
setting in the repo that changes what agents are asked to do, so it is worth
being precise about what it buys and what it costs.

### Why

Every historical (agent, case, outcome) row was produced by this allocator, so
good agents systematically received good cases. An agent-fit model fitted on
that history learns the allocator, not the agents, and no propensity weighting
fully removes a confound that was never broken. A randomised slice is the only
unconfounded evidence available.

### It is sized for ONE question, and not the obvious one

At the measured volume — ~233 allocations/day over 30 agents — the power
arithmetic is unforgiving:

```
                                    eps=5%      eps=10%     eps=20%
per-agent, detect 25% rel. effect   5.8 years   2.9 years   1.4 years
per-agent, detect 40% rel. effect   2.3 years   1.2 years   0.6 years

variance component, 30/agent        2.5 months  1.3 months
variance component, 50/agent        4.2 months  2.1 months
```

Ranking individual agents needs ~820 randomised visits EACH (24,595 total) and
is out of reach at any tolerable rate — it is a volume problem, not a rate one.
What IS reachable is the variance component: **does agent identity matter at
all?** If it turns out small, the whole M4/M5 agent-fit workstream is answered
in weeks instead of years. **Do not read this data as a leaderboard.**

### What it costs — measured, not assumed

```
 eps   explored   mean km   vs eps=0   expected INR   BLOCKED   caseloads
  0%       0.0%     3.366     +0.00%        866,708      same       exact
  5%       5.0%     3.571     +6.10%        866,864      same       exact
 10%       9.5%     3.847    +14.28%        866,992      same       exact
 20%      19.1%     4.059    +20.59%        866,981      same       exact
```

**Travel is the real price: +14.3% mean base-to-case distance at 10%.** A
randomised case can land anywhere inside the 16 km territory rather than the
~3 km a proximity-weighted assignment picks, so a modest share of randomised
cases moves the mean a lot. If that is too expensive, restricting candidates to
the k nearest eligible agents would cut it sharply at the cost of a narrower
randomisation — the propensity stays computable either way.

The expected-recovery forecast barely moves (+0.03%) and that is **not** evidence
the experiment is free. It is evidence the allocator cannot currently see agent
effects at all: `eb_multiplier` is 1.0 wherever an agent has fewer than five
observations in a segment, which is 297 of 315 cells, so only the tier uplift
distinguishes agents in the forecast. The true cost is whatever real agent effect
exists — which is precisely the unknown the experiment is run to measure.

### Safety properties, all pinned by tests

- **It cannot reach the hard gates.** Candidates come only from `eligible_agents`,
  built as the cost matrix is constructed — the single place DNC, hostility, the
  female-agent requirement, the 16 km territory and PTP fatigue are evaluated.
  `test_exploration_never_sends_a_case_to_an_ineligible_agent` re-checks each
  gate independently on every explored case.
- **Capacity is exact**, because exploration SWAPS rather than moves. A move
  would overfill an agent already at `max_cases_per_day`.
- **BLOCKED is untouched** — blocked cases were never assigned, so exploration
  never sees them.
- **Reproducible.** Seeded from the plan date, so a re-plan for the same date
  explores identically and the audit trail holds; a different date draws
  differently. Same reasoning as the route solver's refusal of a wall-clock limit.
- **Self-identifying.** Every explored decision records `exploration`,
  `exploration_propensity`, `exploration_from_agent`, `exploration_n_eligible`
  and `exploration_seed`, so the slice can be found and reweighted later.
  Unexplored decisions carry `exploration: False` explicitly rather than an
  absent key.

### Two defects found while building it, both worth remembering

- **Epsilon meant twice what it said.** Exploration proceeds by swaps and a swap
  randomises BOTH cases, so selecting `N x epsilon` initiators randomised
  `2 x epsilon` of the book — a configured 10% produced a measured 19.1%, and
  20% produced 35.4%. The count is halved now; `epsilon` is the share of
  assignments that end up randomised, which is what the power calculation and
  the operational conversation are both about.
- **The forecast did not describe the plan.** `expected_recovery_sum` is
  accumulated in Stage 2, before exploration runs, so it reported an identical
  figure at every epsilon — a plan quietly disagreeing with its own forecast,
  the exact defect `_prob_recovery` was merged to prevent on 2026-09-03.
  `_explore` now returns the delta and each swapped case is re-priced against
  the agent it actually went to.

Turn it off by setting `ALLOCATOR_EXPLORATION_RATE = 0.0`.

## The live integration — four silent failures, 2026-09-08

`recovery_risk` was promoted, reported healthy on `GET /manager/ml/health`, and
fed the allocator **nothing** for a full cycle. Every fault below returned a
plausible answer and raised nothing. They are recorded because the pattern
matters more than any one of them: **the graceful degradation that makes this
system safe is the same thing that makes its failures invisible.**

| # | Fault | Why it was invisible | Caught by |
|---|---|---|---|
| 1 | `Loan.disbursement_date` is `String(10)`, not a Date. `build_features` raised `TypeError: unsupported operand ... 'date' and 'str'` on the first loan | `_ml_recovery_probabilities` caught it, logged a **warning**, returned `{}`; the planner fell back to `log_current` exactly as designed | `test_build_features_survives_string_dates` |
| 2 | `Loan.cases` is `lazy="noload"` — always empty unless eager-loaded. Read **twice**: once for the case list, once for the `paid_ratio_*` denominator | Every history feature vanished; `ptp_kept_ratio` (IV 0.2798, the champion's 3rd input) was never supplied, yet coverage stayed 0.75 — above the 0.60 floor, so it still scored | `test_history_features_are_populated_from_a_query`, `test_no_code_path_reads_the_noload_relationship` |
| 3 | Scoring de-duplicated by loan and kept only the **first** case on it | 119 of 933 pool cases — **57 of 214 allocated (27%)** — were ML-less while the run reported itself ML-driven | `test_every_case_on_a_shared_loan_is_scored` |
| 4 | Exploration collected eligibility per capacity **slot**, not per agent | An agent with 15 slots appeared 15 times: the draw became weighted by capacity, and the propensity recorded as `1/k` described a uniform draw that never happened. A live run logged *"among 93 eligible agents"* for a manager with **15** | `test_eligible_agent_count_cannot_exceed_the_agent_roster` |

Fault 4 is the one that would have done lasting damage: propensity is what IPW
rests on, so a wrong one silently poisons the very analysis the epsilon-greedy
slice exists to enable.

`_ml_recovery_probabilities` now logs at **error** with the exception type and
traceback, not warning — a swallowed exception that degrades correctly still has
to be loud.

### Concurrent planning collided, and it looked like a broken plan button

Two planning runs for the same manager and date both delete the PLANNED beats
and then both insert, colliding on the UNIQUE `(agent_id, beat_date)` index. It
surfaced as `duplicate key value violates unique constraint "ix_beat_agent_date"`
-> HTTP 500 -> a "Failed to generate plan — please try again" toast, with the run
rolled back and nothing to show for it. **A double-click on "Re-Plan & Sequence"
is enough**, and so is the 20:00 nightly task overlapping a manual re-plan.

`PlannerService._acquire_plan_lock` now takes a Postgres **transaction-level**
advisory lock keyed on `(crc32(manager_user_id), yyyymmdd)` — transaction-level
because a session-level lock leaks on a crashed worker and would block every
later plan until the connection is reaped. Non-Postgres dialects return True and
rely on the `IntegrityError` fallback, which is correct because SQLite (the test
DB) has no concurrent writers. `simulate=True` never takes the lock: it writes no
beats and must not block a real plan.

The endpoint answers **409 Conflict**, not 500 — the request was valid, the
timing was not — with an `IntegrityError` catch behind it as the belt to that
brace. Verified live: four simultaneous requests give exactly one 200 and three
409s, zero 500s.

**The lock was not the hard part.** The first fix did not work: the patch added
`PlanInProgressError` to the imports of `get_latest_allocation_plan` while the
`except` clause sat in `create_or_simulate_allocation_plan`, so the handler
raised `NameError: name 'PlanInProgressError' is not defined` and the 500 came
back unchanged. The patch's own `assert "..." in source` passed, because the
string existed — in the wrong function. A structural assertion that a symbol
appears somewhere in a file proves nothing about scope;
`test_plan_in_progress_returns_409_not_500` executes the handler instead.

### The model's outcome definition — separate, versioned, and scheduled

`app/ml/pipeline/outcomes.py` is the ONLY place the `recovery_risk` label is
defined:

```
recovered  ==  paid_in_window >= 0.8 * min(overdue_amount, emi_amount)
window     ==  ( end of as_of_date , end of as_of_date + 30 days ]
```

**Deliberately NOT the repayment labeller's rule.** That one grades at
`REPAYMENT_FULL_RATIO = 0.9` against what was still owed on `as_of_date`.
Relabelling the model with it would silently change the target `recovery_risk`
was validated on, and every metric in `ml/artifacts` would then describe a
question nobody is asking. The two stay apart, and
`OUTCOME_DEFINITION_VERSION = "recovery-outcome-1.0.0"` is stamped on every row
so a future change is filterable rather than retroactive.

`overdue_amount` and `emi_amount` are **frozen at prediction time** in
`ModelPrediction.outcome_baseline` — a column of its own, not folded into
`features`, because features are the model's INPUTS (what PSI is computed on)
while the baseline is what the OUTCOME is measured against. `emi_amount` is not
a model feature at all, so it has nowhere else to live. Both are overwritten in
place on `Loan`, so reading them at labelling time would compare a payment
window against a balance those very payments already reduced.

**Every edge case is a decision, not an inherited default:**

| Case | Rule |
|---|---|
| Payment on the observation day | **Excluded** — it happened before the prediction |
| Payment on day 30 / day 31 | Day 30 counts; day 31 does not, and is recorded as `paid_after_window` so the horizon can be argued with evidence |
| PENDING / REJECTED / REVERSED | Excluded. Reversal is a status change, not a negative row, so no netting |
| Duplicate receipts | `receipt_number` is UNIQUE in the schema — the DB prevents them. The labeller dedupes anyway, as a guard for data arriving outside the ORM |
| WRITTEN_OFF, SETTLED, RECALLED, DECEASED | **Censored** — `actual_outcome` stays NULL. Counting a bank write-off as "did not pay" teaches the model that bank decisions are borrower behaviour |
| Two censors at once | Fixed order, write-off first: report the reason the money stopped being collectable |
| Missing / non-positive baseline | `NO_BASELINE`, left unlabelled. Never scored against a guess |
| Not yet 30 days old | `NOT_MATURED`, untouched |

RECALL is detectable **only as free text** (`resolution_notes` starting
`"RECALLED by bank"`) because the schema records the bank action nowhere else.
Matched explicitly so the fragility is visible; a `Case.closure_reason` enum
would fix it properly.

**Scheduled at 19:15**, ahead of the 19:30 ingest — `ingest_daily` applies bank
actions to cases, and running after it would censor a prediction on an action
that landed *after* its own window closed.

33 executable tests in `tests/test_model_outcomes.py`, including
`test_scoring_rule_matches_the_rule_the_model_was_trained_on`, which asserts
this labeller and `book_simulator`'s training rule agree across five
overdue/EMI/paid combinations. None of them inspects source text.

### The two labellers, measured side by side

`ml/pipeline/label_comparison.py` records what the *other* labeller would have
said about each matured prediction, over the same `as_of_date` and horizon, into
`ModelPrediction.label_comparison`. It writes nothing else — `actual_outcome` is
untouched, no allocator path reads it, no metric in `ml/artifacts` is computed
from it. It runs inside the 19:15 task, after labelling, and its failure is
logged and swallowed so a diagnostic cannot report a correct labelling run as
failed.

**Three things the code says that the names do not.** Each was read out of the
source and each is pinned by an executable test:

- **The 90% ratio does not gate the positive class.** `_infer_outcome` returns
  `PARTIAL` whenever `received > 0`, and `POSITIVE_OUTCOMES = {REPAID, PARTIAL}`
  maps **both** to 1. `REPAYMENT_FULL_RATIO` only splits two labels that are the
  same class. So binarised for training the repayment bar is `any payment > 0` —
  it *sounds* stricter than 0.8 × a cycle and is far looser.
  `test_the_repayment_rule_binarises_to_any_payment` runs it on ₹1 against
  ₹24,000 owed and lands in `POSITIVE_OUTCOMES`.
- **The two sides count different money.** `attach_outcomes` builds
  `payments_by_case` with **no status filter at all**, and `_infer_outcome` does
  not filter either, so a REJECTED or REVERSED receipt counts as money received.
  `_RECOVERED_PAYMENT_STATUSES` guards `_received_within` — the *recovery* pass —
  and nothing else. The model rule requires VERIFIED. A latent defect in the
  repayment labeller, recorded rather than fixed here because the brief was to
  compare the two as they stand.
- **The two sides censor on different evidence.** `_infer_outcome` censors only
  when nothing was received *and* every case is CLOSED / WRITTEN_OFF /
  ESCALATED; `outcomes.censoring_status` censors on a write-off, settlement,
  recall note or DECEASED tag regardless of what was paid. Neither is a superset
  of the other, so disagreement runs in both directions here.

Disagreements are attributed by elimination over measured sums —
`payment_status` → `payment_scope` → `threshold` — in the order the differences
bite, with the sums stored beside the verdict. Because the model's countable
money is a subset of the repayment rule's and its bar is higher, *model positive
⇒ repayment positive*; `test_model_positive_implies_repayment_positive` asserts
that over a 42-cell grid and `Cause.DIRECTION_ANOMALY` flags it at runtime if it
ever stops holding.

`shared_horizon()` refuses a horizon override that differs from
`settings.REPAYMENT_OUTCOME_HORIZON_DAYS`, because `_infer_outcome` reads that
setting directly and takes no argument — an override would have moved only the
model's window and produced a 30-vs-60-day comparison silently.

**The report always emits all seven causes, zero-filled** (`ALL_CAUSES`). A
`Counter` omits what never happened, which would make "no `payment_status`
disagreements" and "the comparison never ran" the same output — and the second
is what a stale or half-wired report looks like. `disagreement_pct` is over
**comparable** rows (both sides labelled), never over matured ones; a row only
one side labelled cannot agree or disagree, and folding it in would dilute the
rate with rows that were never in the question. `censoring_pct` is a **union**
over matured rows, because a row censored by both sides is one censored row and
produces no cause at all — neither side is the odd one out.

**First live dry run (2026-09-08, `as_of` forced to the 2026-10-08 maturity
date, read-only):** 916 matured, 906 comparable, **0 disagreements**, 10
`censoring_repayment_only`. **That 0% is not evidence of agreement and must not
be quoted as any.** The demo book holds **zero payments of any status after
2026-09-08**, so both rules bottom out at "not recovered" for trivial reasons
and only the censoring asymmetry is visible at all. A real disagreement rate
needs real matured outcomes, and given the first finding above it should be
dominated by `threshold`, not by the denominator. 70 executable tests in
`tests/test_label_comparison.py`.

**Held deliberately, pending real matured outcomes:** no retrain, no
standardising on one label, no allocator change, and the payment-status
behaviour stays a recorded defect rather than a silent fix — changing it now
would move the repayment labeller under the comparison that exists to measure
it.

### Monitoring, gated on maturity — 2026-09-09

`monitor_model()` now runs inside the same 19:15 task, after labelling and the
comparison, but **only when it can say something**. `monitor.readiness()` is a
three-`COUNT` gate that builds no DataFrame, so the nightly cost of "not yet" is
negligible:

```
model_outcomes.monitor_not_ready  model=recovery_risk model_version=1.1.0
  n_matured=0 required=500 outcome_definition_version=recovery-outcome-1.0.0
```

**`not_ready` is INFO, not a warning.** Nothing is wrong when outcomes have not
matured; it is the expected state of a young model. Thirty nightly warnings
before the first real signal would train everyone to scroll past the one that
matters.

**500 is derived, not chosen.** At the development bad rate (0.718) and OOT AUC
(0.757), Hanley–McNeil puts SE(AUC) ≈ 0.022 at n = 500, so the 95% interval on
Gini spans ≈ ±0.085 — wide, but narrow enough to see the 20% relative drop that
triggers a retrain. Below it the interval swallows the trigger and the verdict
would be noise wearing a number.

**Two versions gate the population, and they gate it differently.**
`model_version` filters every row — scores from 1.0.0 and 1.1.0 are different
numbers. `outcome_definition_version` filters only the *label*: an unmatured
prediction has no outcome definition yet, so excluding the row would shrink the
stability sample for a reason that has nothing to do with stability. Exclusions
are counted into `report.excluded` rather than left to be inferred from a row
count that looks low.

*Two defects fixed while wiring it:* `monitor_model(version=None)` used to mean
**"pool every version"** and then report `rep.version` as the *modal* one — a
mixed population labelled with one version string and read as if it described
that model. `None` now means the serving version; pooling must be asked for with
`all_versions=True` and is labelled `POOLED` in the reasons. And
`outcome_definition_version` was not read here at all, so two labelling rules
would have been pooled silently.

The report now carries what a review actually needs: **n, AUC, Gini, KS, Brier,
calibration gap and table, bad/recovery rate, score PSI, per-feature PSI for the
four champion features, and the development benchmarks beside each live figure**
(the retrain rule is a *relative* drop, so a live Gini with nothing to compare
against cannot trigger anything). Score PSI moved out of the feature branch: a
model whose features were all missing used to report no drift at all rather than
the drift **plus** the fault. A champion feature absent from the served vectors
is now a `retrain_recommended` finding in its own right — that is exactly how
`ptp_kept_ratio` vanished for a full cycle while coverage stayed above its floor,
and a shorter PSI table is otherwise indistinguishable from a healthy one.

**Both diagnostics are contained.** The comparison and the monitor each run
*after* `actual_outcome` is committed, so neither can influence a label, and a
raise in either is logged and recorded in the task's return value rather than
failing the task. 18 executable tests in `tests/test_model_monitoring.py`.

### The feedback loop is closed

The planner used `score_many()`, which returns probabilities and records
nothing, so `model_predictions` sat at **0 rows** while the model drove
allocation. It now calls `score_cases_and_log()`, which writes one row per case
— model name, version, artifact SHA-256, loan and case id, probability, the
model's own features, coverage, `as_of_date`, `scored_at` — and
`plan_next_day` stamps the **allocated agent** onto each row after the solve,
because the decision does not exist until then and a prediction with no decision
attached cannot be evaluated per agent when the outcome matures.

### Verified on a real run

```
run 6a4ca988 — 2026-09-09, SMART, manager1 (15 agents)
  allocated 214 | ml_used_for_decision 214/214 | transform log_rescaled
  explored 20 | max eligible-agent count 9 (roster 15) | blocked 22
  model_predictions 916 rows | 214 linked to an agent | v1.1.0 | min coverage 1.000
```

Every allocated decision carries both probabilities side by side —
`prob_recovery` (agent-side, old) and `prob_recovery_ml` (borrower-side,
calibrated) — plus `ml_borrower_p_recover`, `value_transform`,
`ml_used_for_decision`, every utility term and its weighted contribution. An
explored decision names the agent it was moved from, the eligible count and
epsilon in plain text.

**Still unproven: real-world recovery impact.** Every performance figure for
this model comes from synthetic books. What is now demonstrated is that the
pipeline runs end to end on live rows and records what it did — not that it
recovers more money.

## The nightly pipeline

Schedule verified against `workers/celery_app.py`:

```
19:30  scripts/ingest_daily.py     bank CSV → DPD/amounts + bank_action
                                   (PAID_DIRECT · SETTLED · WRITTEN_OFF · RECALL ·
                                   DECEASED) → applies consequence AND labels snapshots
19:45  repayment_scoring           score every loan → roll up to the customer's WORST
                                   loan → snapshot only on change or anchor → attach
                                   matured outcomes → prune unlabelled rows
20:00  allocation                  per manager: pool filter → hard gates → bipartite
                                   solve → OSRM/OR-Tools route → PLANNED beats
05:30  demo_daily_feed             DEMO_MODE only
06:00  morning beat push  ·  09:00 PTP reminders  ·  03:00 location retention sweep
00:00 on the 1st  monthly performance snapshot
```

Fifteen minutes is the entire margin between ingest and allocation, which is why
`RepaymentService._load` bulk-loads instead of querying per loan.

The allocator (`SMART` strategy) is two-stage: a Hungarian solve
(`scipy.linear_sum_assignment`) over a case × capacity-slot cost matrix, then
route-feasibility validation that defers unroutable outliers. Hard gates run
*before* scoring — DNC, hostility, female-agent requirement, 16 km territory, and
**PTP fatigue** (three broken promises to one agent bars *that agent*, never the
case, so the solve picks the next best by itself). Every decision is persisted with
its score breakdown; `LEGACY` is a round-robin kept for comparison.

**The three objective tables each sum to 1.05, not 1.0** (verified: BALANCED is
0.45/0.40/0.05/0.05/0.05/0.05). `argmin` is invariant under positive scaling so no
decision is affected, but a weight documented as 0.45 is really 0.45/1.05 = 42.9%
of the decision. Read the tables as ratios, not percentages.

Full specification: [docs/PLAN.md](docs/PLAN.md).

## Conventions

- Files carry `# ─── CHANGELOG (prototype → product) ───` headers documenting what
  changed and **why**. Follow this when making non-obvious changes — it is the most
  valuable documentation in the codebase, and most of it records defects found by
  *measuring the live book*, not by reading code. Quote the number.
- Those headers cite `changelog.md` and `final_changes.md`. **Neither file exists
  anywhere** — 32 files reference them. Don't hunt for them; the header text is the
  whole record.
- Correct a wrong comment **visibly** rather than deleting it, saying what it used
  to claim and why that misled. See `models/loan.py:recovery_potential` and the
  provenance block above.
- One definition, one place. Most of this repo's worst bugs were two copies of one
  rule drifting apart: two `risk_score` formulas, two `recovery_potential` writers
  (both random), two sets of allocator weights, two `RESOLVED_STATUSES` sets, and
  most recently **seven** copies of the DPD→bucket rule. When you find yourself
  restating a rule, import it instead — and consider a test that asserts nobody
  restates it, because that is what found the seventh copy after reading found six.
- Services raise `AppException` with a typed `ErrorCode`, not free-text detail.
  `main.py` maps it to `{detail, code}`. On the frontend, `lib/apiError.ts` is the
  one reader of that contract.
- Tenant scoping is by `Agent.manager_user_id == current_user.id`, still
  hand-repeated at most call sites. `_require_own_agent()` in `manager.py` is the
  shared helper — use it for anything new. A structural test
  (`test_every_manager_route_that_reads_tenant_data_is_scoped`,
  `test_manager_endpoints.py:494`) walks every route in the file and fails on one
  that neither scopes nor is allowlisted. **It is textual**, so it verifies the
  endpoint *mentions* scoping — it cannot see a service that drops it, which is
  exactly how the `export-decisions` leak survived it.
- All LLM calls go through `core/llm.py` — one seam, provider by settings
  (`groq` default, `openai`, `none`), classified failures, Redis-or-memory cache,
  per-purpose counters on `GET /manager/ai/health`. It never raises; callers read
  `ai_generated` and label the fallback rather than passing it off as AI. It
  **does** support `response_format: json_object`, which matters for feature #2
  below. Same shape as `core/transcription.py` for Whisper.
- Root `main.py` is the old `:8300` Command Center dev stub. **Nothing consumes
  it.** Safe to delete. **So is `backend/stub_main.py`** (163 lines, tracked):
  its own docstring says it serves `:8300` from `field-ops-stub/backend`, a path
  in the *other* repo, and nothing here imports or runs it. *(This line used to
  claim stub_main.py was "already gone". It is not — verified 2026-09-07 by an
  AST pass over every import, celery `include=[]` string and router
  registration. Corrected rather than deleted so the wrong claim is visible.)*
  Root `requirements.txt` (two lines) fed only those two stubs; CI and both
  Dockerfiles use `backend/requirements.txt`. `app/schemas/manager.py` is dead
  too — all three of its models are referenced nowhere.
- **Do not regress these**, they are load-bearing and were each fixed once: JWT
  with `jti` + `device_id` binding; bcrypt; single-use quick-login tokens (the
  90-day-reusable-token incident is documented in `core/security.py:1`); slowapi
  rate limiting; presigned MinIO URLs; the SPA catch-all in `main.py` that refuses
  to swallow `/api` paths; and `core/transcription.py`, which is visibly debugged
  against real mic audio rather than clean test files — every filter in it is a
  fallback, not a hard gate, because hard gates made it return empty strings.

## Feature coverage

Specified against a 20-feature *AI-Powered Field Recovery Platform* reference
document. Re-verified against the code on 2026-09-07: **6 built · 10 partial ·
4 missing.**

| # | Feature | | Where it stands |
|---|---|---|---|
| 1 | AI Field-Agent Copilot | ✅ | `/agent/cases/{id}/visit-strategy` — LLM brief with a rule-based fallback, and the agent is told which one they got |
| 2 | AI Voice → Automatic Visit Report | 🟡 | STT is real and hardened; the report is prose built from *already-structured* fields. Nothing extracts disposition / PTP amount / date out of the speech — the agent still types all of it. **`core/llm.py` already supports JSON-mode output**, so this is one structured-output call away. Highest-value gap on the list |
| 3 | AI Recovery Priority Score | ✅ | `ml/visit_priority.py` — recoverable value, urgency around the NPA line, effort spent. Scorecard, not a model |
| 4 | AI Next-Best-Action Engine | ❌ | No endpoint. Nothing chooses visit vs call vs reminder vs settle vs escalate |
| 5 | Recovery-Optimized Route Planning | 🟡 | The *assignment* weights expected recovery, and the planner now keeps the road matrix it fetches, applies RBI + preference time windows, and persists real per-leg figures. A prize-collecting multi-vehicle CVRPTW (`plan_fleet`) exists and is tested but is **not yet wired into the nightly run** — the sequence inside a beat is still travel-time TSP |
| 6 | Borrower 360° Profile | ✅ | `AgentCaseDetailPage`, 6 tabs. Disputes are still a visit outcome, not an object with a lifecycle |
| 7 | AI Recovery Probability & Expected Recovery | 🟡 | `ml/recovery_scorecard.py` computes rate 30/60/90 + `expected_recoverable_amount`, snapshotted and surfaced. Hand-weighted and **uncalibrated** — no real outcome matures before 2026-11-22. A *trained*, calibrated alternative now exists (`ml/artifacts/recovery_risk`, Gini 0.515 out-of-time) but is gated off and fitted on synthetic data |
| 8 | AI Settlement Recommendation | ❌ | `loan.settlement_status` is a read-only bank flag. No range, no policy, no approval workflow |
| 9 | AI Agent Performance Intelligence | ✅ | Performance, AI insight, reallocation plan, monthly report, leaderboard, DPD and attendance breakdowns |
| 10 | AI Fraud & Anomaly Detection | ✅ | `services/fraud_service.py` — 7 finding types (impossible travel, overlapping visits, photo-location mismatch, duplicate photos, short visits, far-from-customer, trail contradiction) over evidence already captured. Manager review; verdicts stored as future training labels. Rules, not a model, deliberately |
| 11 | AI Compliance Monitor | 🟡 | Rules genuinely enforced (RBI hours, 100m fence, DNC, consent). No AI pattern analysis; thresholds hardcoded, not configurable |
| 12 | Live Recovery Command Center | 🟡 | Map and location trail exist, and the agent beat map is now real (Leaflet + OSRM road geometry) rather than a decorative SVG. **Nothing is push-based** — verified: no WebSocket, no SSE anywhere in the tree, only polling. The three "WebSocket" hits are all comments |
| 13 | Recovery Risk Radar | ❌ | Every input already exists and is already on the wire. **Pure frontend work — cheapest item on this list** |
| 14 | Digital Payment & Instant Receipt | ✅ | Unique receipt numbers, Razorpay UPI QR, SMS + WhatsApp receipts, borrower-OTP verification. No reconciliation workflow |
| 15 | Offline-First Field App | ❌ | The banner is honest and text drafts persist per case, but — verified — there is **no service worker, no IndexedDB, no outbox** anywhere. An agent still cannot complete a visit without signal |
| 16 | Evidence & Immutable Case Timeline | 🟡 | Capture is thorough. The audit trail is 8 of 22 actions (issue 4) and there is no unified timeline view — evidence is scattered across tabs |
| 17 | Customer Engagement Hub | 🟡 | Agent-triggered one-offs only. No campaigns, scheduling, PTP follow-up automation, templates or unified comms log |
| 18 | Smart Work Queue & Gamification | 🟡 | The queue is real. Gamification is manager-side only — the agent cannot see their own standing |
| 19 | Recovery Forecasting & Portfolio Analytics | 🟡 | Analytics are strong and all historical or current-state. **No forecasting anywhere** |
| 20 | Continuous Learning & Management Insights | 🟡 | **The loop closed on 2026-09-08** — a full development pipeline (WOE/IV → VIF → SFS → sign check → KS/Gini/decile/PSI gates), committed pickle artifacts, a `model_predictions` table and a drift monitor with retrain triggers. Still 🟡 for one reason and it is the honest one: **nothing has been trained on a real outcome yet.** The models are fitted on a synthetic book |

The four missing items split cleanly: **#13 and #15 need no ML at all**; #4 and #8
depend on judgement layers that do not exist yet.

## Known issues — open

1. **The frontend lint job fails — 18 errors, all `react-hooks/set-state-in-effect`.**
   Down from 53 on 2026-09-06; the other 35 were fixed on 2026-09-07 (20
   `no-explicit-any`, 7 unused vars, 2 needless exports, and four that were real
   bugs — see the fixed list below). What remains is concentrated in the six
   largest pages, `ManagerAnalyticsPage` alone holding 7. Each is a
   behaviour-affecting restructure on pages with **no test coverage at all**
   (issue 6), which is why they were not swept up with the rest. This is the only
   thing between CI and green.
2. **No `/verify-agent` endpoint exists.** `core/security.py:78`'s
   `create_agent_verify_token` mints a signed token for the QR on the agent's ID
   card, and its own docstring describes "the public /verify-agent endpoint" that
   validates it. Verified: no such route exists anywhere in `api/`. A borrower
   scanning the card has nothing to check it against, so the anti-impersonation
   control is inert. The Compliance page states this rather than claiming ID
   verification.
3. **The audit trail is mostly declared and unwritten.** `AuditLog` defines 22
   action types; **8 are emitted** — `LOGIN`, `LOGIN_FAILED`, `LOGOUT`,
   `TOKEN_REFRESH`, `DEVICE_MISMATCH`, `PAYMENT_VERIFIED`, `PTP_UPDATED`,
   `ANOMALY_REVIEWED`. `VISIT_RECORDED`, `PTP_SET`, `CASE_ASSIGNED`,
   `BEAT_GENERATED`, `DATA_EXPORT` and nine others are defined and never written.
   Immutability is convention only — no trigger, no revoked grant.
4. **The audit-log read path has one deliberate blind spot.** `GET
   /manager/audit-log` and `/audit-log/export` share `_audit_log_query`, so the
   tenant scope cannot be dropped on one path and not the other. But the scope is
   `user_id IN (this manager + their agents)`, and `IN` drops NULLs — so
   system-written rows (a `PTP_UPDATED` when a payment honours a promise) never
   appear. Correct as a default; a real gap nonetheless. The API declares it
   (`excludes_system_rows: true`) and the page says so. Scoping them through
   `PTP → agent → manager` is the obvious extension and is not done.
5. **Two competing schema authorities.** Eight Alembic migrations exist, but
   `seed_data.py` does `drop_all` + `create_all` and — verified — **never touches
   `alembic_version` at all**. `docker-entrypoint.sh` arbitrates by checking for
   `public.agents`. Fine for a demo box; for production `alembic upgrade head` has
   to be the only path.
6. **`manager.py` is 3,967 lines of business logic in the route layer** — 33
   routes and **114 `db.query()` calls** sitting directly in endpoints while a
   working `services/` layer exists and is used by every agent flow. There is no
   `manager_service.py`. This is *why* the tenancy leaks happened: there is no
   single place where "the agents this manager owns" is defined, so it gets
   retyped. (Flagged at 2,316 lines on 2026-08-17 and 3,756 on 2026-09-06; still
   growing.)
7. **Frontend has no test tooling at all** — no vitest, jest, playwright or
   cypress. TS `strict` and eslint are configured, which is a good base. The six
   largest pages are 1.2k–2.3k lines each.
8. **Analytics have three dimensions: agent, DPD bucket, month.** Every `group_by`
   in the manager router is one of those (plus beat date). There is no breakdown by
   branch, city/geography or loan product — though `Customer.city`, `Loan.loan_type`
   and `Loan.branch_code` are all on the models and already populated.
9. **Two rollout gates are closed by default**, and that is deliberate — but it
   means the running system is not doing what a reader of `ml/` might assume:
   `REPAYMENT_WRITE_RISK_SCORE=False` (the scorer never touches
   `Customer.risk_score`) and `RECOVERY_WRITE_LABEL=False` (`Loan.recovery_potential`
   still holds whatever it held before, which on a seeded database is
   `random.choices()` output). Snapshots are written either way, and every manager
   surface reads the snapshot, never the Loan column. `REPAYMENT_REPRICE_OPEN_CASES`
   is a third flag that is honestly reported as *not implemented* rather than
   silently ignored.
10. **`RepaymentService._received_within` (`repayment_service.py:994`) sums
    payments across EVERY CASE of the loan.** For the recovery scorecard, which
    predicts a LOAN-level rate, that is correct and intended. It stops being
    correct the moment anything asks a per-agent or per-case question: where a
    loan has had two cases under two agents, the money lands wherever the join
    happens to put it. Nothing reads it that way today — recorded because the next
    thing that wants "how much did this agent recover" will reach for this
    function first, and it will look right.

## Fixed on 2026-09-08 — routing (Workstream D)

- **The planner threw away the OSRM matrix it had just paid for.**
  `planner_service.py` called `optimize_route()`, took only the ordering, and
  then recomputed the distance itself as Haversine x 1.15 and the duration as
  km/25 + 20 minutes a stop. The road matrix was discarded on every beat, every
  night, so **the ETA shown to every agent and manager was crow-flies even when
  OSRM answered perfectly.** `plan_route()` now returns a `RouteResult` carrying
  the per-leg seconds and metres the solver used, and the planner persists them.
  Pinned by `test_plan_route_legs_reconcile_with_the_reported_totals`.
- **Four different per-visit constants.** 15 (`AVG_VISIT_DURATION_MINUTES`,
  which is what the route was actually made *feasible* against) and 30, 20 and
  25 hardcoded at three points in the planner — so a day was routed against one
  number and reported against another. `routing.avg_visit_seconds()` is the only
  one now, with a textual guard test against reintroduction.
- **No road geometry existed anywhere.** Only `/table` was ever called, never
  `/route`. `fetch_osrm_route()` returns the encoded polyline; `Beat` stores it.
  This is what made Workstream E possible at all.
- **The Haversine fallback was silent.** Every result and every `Beat` now
  records `route_source`, so "OSRM answered" and "we guessed with straight
  lines" are distinguishable after the fact.
- **The nightly plan passed no time windows.** VRPTW has been supported since
  2026-07-13 but only the agent's on-demand re-optimise used it, so the planner
  could hand an agent a day whose later stops fall outside RBI contact hours —
  a route the compliance rules would then block. `_contact_windows()` applies
  the legal window plus the borrower's own preference. Windows stay soft: an
  infeasible set retries without them, because an agent with no beat is worse
  than one with an imperfect beat.
- **`plan_fleet()` — a real multi-vehicle CVRPTW.** Per-vehicle depots,
  capacity, shift windows, and drop penalties proportional to what a case is
  worth, which is "recovery-optimised routing" with no ML in it. **It is built
  and tested but NOT yet wired into the nightly planner**, because doing so
  changes which agent gets which case and that is a behavioural change needing
  its own measured release. `allowed_vehicles` is the mechanism that makes it
  safe when it is: the allocator's five hard gates (DNC, hostility,
  female-agent, territory, PTP fatigue) are passed in as per-stop vehicle
  restrictions, so the guarantees hold by construction rather than by re-testing.
- **Determinism cost something, and the cost is recorded.** GUIDED_LOCAL_SEARCH
  bounded by `solution_limit` was tried first, on the reasoning that a count of
  improvements is hardware-independent where a wall-clock limit is not. It is —
  but the limit is a ceiling, not a guarantee: on a 4-stop, 2-vehicle instance
  the search never found 40 improving solutions and **never terminated**, and the
  routing tests hung until killed. Search is now plain local descent from
  PATH_CHEAPEST_ARC: always terminates, always reproducible, gives up some
  quality on large instances. A reproducible plan is worth more here than an
  unreproducible better one — the audit trail is the product.
- **`beat_reconciliation`** (02:00, before the 03:00 retention sweep that would
  otherwise erase its evidence) reconstructs each completed beat's real distance
  from the `AgentLocation` trail and its real duration from visit check-in to
  check-out. It skips gaps over 15 minutes rather than assuming an agent
  teleported, ignores sub-25-metre jitter, and leaves the columns NULL where
  there is no evidence — "not measured" must stay distinguishable from
  "measured as zero".

## Fixed on 2026-09-08 — maps (Workstream E)

`BeatMapPage` drew a decorative `<svg viewBox="0 0 300 120">` that laid the next
eight stops on a fixed 4x2 grid: two stops 200 m apart and two 30 km apart
rendered identically, so the one thing a route map exists to show was the one
thing it could not. It is now a real Leaflet map — numbered stops in beat order,
the OSRM road polyline, the agent's base, tap-a-stop to open the case. The
Google Maps deep-link is **kept** as the Navigate action: it is a free URL
scheme, needs no key, and handing turn-by-turn to the phone's own nav app is the
right call. `components/map/MapCanvas` is the shared bootstrap; the tile server
is overridable with `VITE_TILE_URL`, which a production deployment will need
because OSM's public tiles rule out heavy use.

*Two things not done, deliberately.* `ManagerLiveMapPage` still has its own
Leaflet bootstrap and has not been migrated onto `MapCanvas` — its polling and
trail logic make that a separate change. And the manager-side route review with
drag-to-reorder (plan item E4) is not built.

## Fixed on 2026-09-07, with the measurement

Kept because the numbers are the useful part, and because each was invisible until
something measured it.

- **The allocator's `spec_match` term had never once been 1.0.**
  `global_allocator.py` compared an `AgentSpecialization` (`SECURED` / `UNSECURED`
  / `BOTH`) with a `LoanType` (`HOME` / `AUTO` / `PERSONAL` / …) — **enums that
  share no member** — so the term was the constant 0.5 and the specialisation half
  of `skills_score` was a no-op. Now delegates to
  `ml/eligibility.specialisation_fit`, which both scorecards already imported.
  Measured on the demo book by loading the pre-fix file out of `git show HEAD:`:
  **2 of 227 assignments moved (0.9%)**, 4 cases swapped in and out of the plan,
  expected recovery total **−0.80%**, and the **BLOCKED set was identical (15 of
  15)** — the hard gates did not move, which was the safety property. Pinned by
  `tests/test_global_allocator.py`, which is also the first test coverage that
  file has ever had.
- **`RepaymentSnapshot.case_id` named an arbitrary case of the loan.** `score_loan`
  set it with `next((c.id for c in cases), None)` over a list built with no status
  filter and no `ORDER BY`. Measured on a 700-borrower synthetic book: **4,984 of
  6,459 snapshots (77.2%) named a case whose last visit was a median of 130 days
  earlier**, with up to 9 cases per loan. Not cosmetic —
  `scripts/backfill_eb_features.py` bridges snapshot→agent through this column, so
  the shadow model's own `eb_shrunk_win` feature was looked up via the wrong case
  on the same ~77% of rows. Now `_case_as_of` takes the most recently *created*
  case that existed by `as_of_date`. **Existing rows are not corrected.**
- **Seven copies of the DPD→bucket rule, two disagreeing with the enum's own
  comments.** `models/loan.dpd_bucket_for` is now the only one. No behaviour
  changed: the disagreeing copies lacked branches their DPD ranges (starting at 32
  and 35) cannot reach, which `tests/test_dpd_bucket.py` proves exhaustively
  rather than asserts. Worth doing because `EmpiricalBayesAgentAdjuster` is keyed
  on the bucket. *Reading the code found six; the test that asserts nobody
  restates the rule found the seventh.*
- **Four real frontend bugs**, found while clearing lint: a **conditionally-called
  hook** in `BeatMapPage` (called after two early returns, so hook order changed
  between renders); a **`Date.now()` read during render** in the SLA countdown;
  **two refs written during render** in `useAnimatedValue` (rewriting it revealed
  its state was only ever 0, so the hook collapsed to a delay flag); and — hidden
  by an `as any` — the gallery-upload path stamping photo GPS with only
  `lat/lon/time`, missing `accuracy`, `altitude` and `iso`. Because the submit
  payload reads `form.<x>PhotoGps?.iso ?? new Date().toISOString()`, a gallery
  photo's `captured_at` was recorded as **the moment of submission, not of
  capture** — on evidence attached to a compliance record.

## Fixed on 2026-09-06

Listed only so the next reader does not go looking for them: a `NameError` in
`payment_service._get_accessible_case` (`Agent` used, never imported); a
cross-tenant `GET /manager/allocation/export-decisions` (scoped in the *service*,
because the structural sweep is textual and could not see it); `plan_next_day`
crashing on its own documented default; the CI Python pin; a **broken frontend
production build**; a CI typecheck step that could not fail; the Compliance page's
six fabricated audit rows, replaced by real endpoints; and two scheduled tasks
reporting work they never did — `ptp_reminders` was marking `reminder_sent = True`
without sending, which *removed* those promises from every future run.

*`docs/AUDIT.md` — the 2026-08-17 audit these sections came from — was deleted on
2026-09-06 rather than left to drift beside this file, per the one-definition rule
above. Retrieve it with `git show 674402e:docs/AUDIT.md` (311 lines).*
