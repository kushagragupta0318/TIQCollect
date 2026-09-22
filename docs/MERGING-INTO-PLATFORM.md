# Merging this repo back into the Collections platform

This repo was extracted from the `Collections` monorepo (`field-ops-stub/`) on
2026-08-17 with `git subtree split`. Work continues in both places, so the two
copies drift. This is how to fold this repo's work back into the platform.

Written 2026-08-21, after doing it once. Read the traps before running anything.

## State this assumes

| | |
|---|---|
| This repo | `github.com/sanyasirao-col/TIQCollect-product`, work on **`TIQCollect-v3-1`** *(read `TIQCollect-v1` until 2026-09-17; the `git diff` below must name the branch you are actually merging — `v3` is the same tree minus the ML 2.2.0 commit `7707139`)* |
| Platform | `github.com/transorg-engineering/Collections`, branch `docker-integrated` |
| Common ancestor | **`tiq/TIQCollect-v3-1@008433f`** — the tree merged on 2026-09-21 onto Collections branch `COLLECTIONS` *(read `field-ops-stub@bc8649c` until then; that base is only correct while no merge has landed, and one now has)* |

A merge was completed once on 2026-08-21 and then **deliberately deleted**
without being pushed. **The first merge that actually landed was 2026-09-21**:
`TIQCollect-v3-1@008433f` applied onto `docker-integrated@9e7e6a8` on the
branch `COLLECTIONS` — 803 files, 2 conflicts (`docker-compose.yml` env
comments; `LandingPage.tsx` copy, this repo's wording kept). Every
platform-side typography change since `bc8649c` was verified present in the
result line by line. **The next merge must diff from `008433f`'s tree, not
from `bc8649c`** — the command below is written for that.

## Procedure

```bash
cd Collections
git fetch origin
git checkout -b merge-tiq origin/docker-integrated
git remote add tiq "../TIQCollect-product"     # or the GitHub URL
git fetch tiq

git config core.longpaths true                 # Windows only — see trap 6

git diff --binary 008433f^{tree} tiq/TIQCollect-v3-1^{tree} \
    -- . ':(exclude)CLAUDE.md' ':(exclude).gitignore' ':(exclude).github' \
    > /tmp/tiq.patch                                # --binary: see trap 5

git apply -3 --directory=field-ops-stub /tmp/tiq.patch
git diff --name-only --diff-filter=U          # conflicts to resolve by hand
```

`git apply -3` is a real three-way merge: it keeps the platform's own changes
wherever this repo did not touch the same lines, and leaves conflict markers
where both moved. After editing a conflicted file you must `git add` it — the
index keeps the unmerged stages until you do, even once the markers are gone.

## Traps

**1. The base is `bc8649c:field-ops-stub`, NOT the subtree split point.**
The first attempt used the split point (`tiqcollect-only`, `7c30095`) and
silently dropped a commit. `index.css` was unchanged between the split and this
repo's tip, so it never entered the patch — leaving the platform's older copy,
which had branched before that work existed. It compiled and looked fine, and
the loss was only caught by grepping for a known class name. Verify a couple of
known-changed files after every merge.

The base stays `bc8649c` only while no merge has landed. Once one does, the base
becomes whatever was merged last.

**2. `git subtree pull` does not work here.** The split was taken bare, without
a matching `git subtree add`, so no linkage is recorded and git refuses with
"unrelated histories". Use the 3-way apply.

**3. Exclude the standalone-repo artifacts.** `CLAUDE.md` states this code must
not be pushed to the org remote, which is false inside Collections. GitHub only
reads workflows at the repository root, so `.github/workflows/ci.yml` would be
inert at `field-ops-stub/.github/`. `field-ops-stub/.gitignore` deliberately
defers to the monorepo's root file. `docs/` is fine to bring across.

**4. Divergence costs.** At two weeks apart the merge was 61 files with only two
conflicts, both one-liners. It will be worse the longer the gap. *(At five weeks
apart, 2026-09-21, it was 803 files and still only two conflicts — the
platform had touched 10 files under `field-ops-stub/` in that time, all
Docker or typography, and this repo had already absorbed the typography.)*

**5. `git diff` needs `--binary`.** Since 2026-09-08 the repo commits ML
artifacts — ~400 PNG / joblib / pickle files under `backend/app/ml/artifacts/`.
Without `--binary`, `git diff` writes a `Binary files differ` stub for each and
`git apply` fails on every one with *"cannot apply binary patch ... without
full index line"*. Found on 2026-09-21; the first dry run failed on all of
them. The patch is ~21 MB with the flag.

**6. Windows path length.** `ml/artifacts/recovery_risk/2.2.0/evaluation/shape_functions/...`
under `Collections/field-ops-stub/` exceeds 260 characters and `git apply`
reports *"Filename too long"* and skips the file. `git config core.longpaths
true` in the Collections checkout before applying.

## After merging

- `python -m compileall app` in `field-ops-stub/backend`
- `npm run build` in `field-ops-stub/frontend` — **not `npx tsc --noEmit`**,
  which this line used to say. The root `tsconfig.json` is a solution file with
  no files of its own, so `tsc --noEmit` resolves nothing and passes
  unconditionally; it reported success for weeks while the production build was
  broken (CLAUDE.md, "Running it"). `npm run build` runs `tsc -b`, which is the
  real typecheck.
- `npm test` in `field-ops-stub/frontend` — 90 vitest tests since 2026-09-17,
  all over pure modules the ported pages depend on (see the ledger below).
- **Apply new migrations.** The platform database is built by `seed_data.py`
  (`drop_all`/`create_all`), which never stamps Alembic — so `alembic_version`
  may not exist and new tables silently will not appear. Check with
  `SELECT version_num FROM alembic_version;`, `alembic stamp <matching rev>` if
  missing, then `alembic upgrade head`.
- **Rebuild the image, do not just restart.** `docker compose up -d` recreates
  containers from the existing image and looks almost identical in the output.
  Confirm with `docker image inspect collections-field-ops --format '{{.Created}}'`
  — the container's own created time tells you nothing about the code inside.

## Related drift — the hand-maintained ports

`command-center/frontend/src/pages/FieldAnalytics.jsx` and `FieldCases.jsx` in
the platform are hand-maintained ports of `ManagerAnalyticsPage.tsx` and
`ManagerCasesPage.tsx` here. They are copy-paste, not imports, so nothing breaks
on merge — they just quietly fall further behind.

**The merge brings the backend but not the ports.** Every `/api/v1/manager/*`
endpoint the pages below need lands in `field-ops-stub/backend` through the
procedure above, and Command Center already reaches that router through its
per-agency service login — so after a merge the DATA is there and the JSX is
what lags. The ledger below is what has to be re-ported, kept current on this
side because the port lives on the other and nobody porting it can see this
repo's history.

### Port ledger

Each row: what changed on the page here, the endpoint it reads, and the pure
module (with its tests) that a port can copy verbatim — the modules are plain
TypeScript with no React in them, so they translate to the platform's JSX
tooling by stripping the types.

**`ManagerAnalyticsPage.tsx` → `FieldAnalytics.jsx`**

| date | change | endpoint | pure module to copy |
|---|---|---|---|
| 2026-09-16 | Header KPIs read the portfolio snapshot (`kpis.total_collected_lakhs` / `total_target_lakhs` / `overall_collection_rate_pct`), not 6-month sums; "on current portfolio" label | `GET /manager/analytics` (unchanged) | — |
| 2026-09-16 | "ABC Collections" heading removed; Recovery outlook legend "Arrears + penalties" → "Current expected", footnote reworded | — | — |
| 2026-09-16 | **Case Pipeline** donut under Collection by DPD Bucket: resolved / in progress / not started, grouped by `models/case.py RESOLVED_STATUSES`; status chips link to `/manager/cases?status=` | `GET /manager/dashboard` → `case_status_counts` | `pages/manager/casePipeline.ts` (+ `.test.ts`); ring is `DonutCard.tsx` |
| **2026-09-17** | **Collection by Payment Mode** — full-width third row of the money grid, after Team Duty / Leave. Left: one 100% composition bar (mode segments, cash in the status amber) with a legend and the cash / digital shares. Right: six stacked columns, one per month, cash / digital / paper (cheque + DD) — the trend the card exists for (cash share 3% → 30% on the 2026-09-17 book). Follows the same `month=YYYY-MM` click as the DPD card, and clicking a column selects that month. Hidden while an agent is selected. *(Earlier the same day it was seven full-width bars, one per mode; replaced before commit.)* | **`GET /manager/analytics/payment-modes?month=YYYY-MM`** (new; VERIFIED payments by the manager's agents; `BANK_DIRECT` excluded because it can never be an agent collection; `monthly[]` is the last six months and is NOT narrowed by `month`) | `pages/manager/paymentModes.ts` (+ `.test.ts`); the columns are Recharts `BarChart`, the composition bar plain divs |
| 2026-09-17 | Scroll-reveal on each card of the money grid and the AI report (`components/ui/Reveal.tsx`) | — | copy the component; it is a 60-line IntersectionObserver wrapper |

**`ManagerCasesPage.tsx` → `FieldCases.jsx`**

| date | change | endpoint |
|---|---|---|
| 2026-09-16 | `?status=` and `?bucket=` URL seeds for the status / DPD dropdowns (the overview's donuts link here) | `GET /manager/cases` (unchanged) |
| 2026-09-17 | `?activity=<stage>&activity_window=today\|7d\|30d[&visit_outcome=…]` and `?ptp_due_from&ptp_due_to` seeds, forwarded server-side, shown as applied-filter chips | `GET /manager/cases` gained `activity`, `activity_window`, `visit_outcome`, `ptp_due_from`, `ptp_due_to` |

**Not ported and not expected to be** (no platform counterpart exists):
the overview's Field Plan strip, Field Activity funnel, Promises card and
Today's Cases by DPD; the `/manager/beat-plan` page; the live-map Navigate
button. If Command Center grows an overview, `pages/manager/fieldActivity.ts`
and `todayDpd.ts` are the modules to start from, and `GET
/manager/dashboard/field-activity?window=` is the endpoint — it and the Cases
list resolve the funnel through ONE service (`services/field_activity_service.py`),
so a port must not re-derive a stage from case state client-side.

### How to keep this ledger honest

Add a row here in the same commit that changes either page. A port that is
done should be recorded as done *with the platform commit hash*, not deleted —
the next merge's author needs to know what was already carried across.
