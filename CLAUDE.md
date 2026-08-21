# TIQCollect — Field Collections Platform

Feet-on-street debt recovery: field agents visit delinquent borrowers, log visits
with GPS/photo/signature evidence, collect payments, and record PTPs. Managers get
analytics, compliance monitoring and AI performance insight over their own team.

FastAPI + Postgres + Redis + MinIO + Celery on the backend; React 19 + Vite +
TypeScript + Tailwind on the frontend. Python >= 3.12.

## Provenance — read this first

This repo was extracted on **2026-08-17** from the `Collections` platform monorepo
(`field-ops-stub/`) via `git subtree split`, so its 20 inherited commits are that
subdirectory's history with paths rebased to root.

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
still is. Pushing to the personal remote is fine on explicit instruction, which
is how `TIQCollect-v1` came to exist (2026-08-21).

*(This block used to read "Remotes: none, deliberately — this repo is
local-only." That stopped being true some time before 2026-08-21 and misled
anyone reading it; corrected rather than deleted so the change is visible.)*

Command Center (in the platform monorepo) consumes this service's
`/api/v1/manager/*` endpoints through a per-agency service login. Its
`FieldAnalytics.jsx` / `FieldCases.jsx` are **hand-maintained ports** of
`ManagerAnalyticsPage.tsx` / `ManagerCasesPage.tsx` — changes here silently drift
from those.

## Running it

```bash
docker compose up -d
```

`backend/.env` is required and gitignored. It was written for the platform and
refers to shared values as `${VAR}`, which no longer resolve — either substitute
real values or delete those lines and rely on the `${VAR:-default}` fallbacks in
`docker-compose.yml`.

| Service | Host port |
|---|---|
| API | `8400` → 8000 |
| Web | `5473` |
| Postgres · Redis · MinIO | `15432` · `16379` · `19000`/`19001` |

Seeding is **destructive** (`scripts/seed_data.py` drops every table and every
public-schema enum with CASCADE). `docker-entrypoint.sh` gates it on whether
`public.agents` exists, so it runs only on an empty database, and only in the API
container (`RUN_SEED=true`).

CI (`.github/workflows/ci.yml`) runs `compileall` + `pytest` + `tsc --noEmit` +
`eslint`. **It has never actually run** — it was authored 2026-07-21 in a repo that
was never pushed. Expect first-run failures on code that predates any checking.
`python -m compileall app` passes as of extraction.

## Layout

```
backend/app/
  api/v1/endpoints/   agent.py · manager.py · auth.py · field_ops.py · health.py
  services/           case · visit · payment · otp · auth · agent · media · notification · ai_report · demo
  core/               config · security · database · routing (OSRM+OR-Tools) · transcription · storage · geo
  models/             16 SQLAlchemy models
  workers/            Celery tasks (allocation, beat generation, transcription, demo feed)
  ml/                 allocator.py only — rule-based, self-described as "dummy"
frontend/src/
  pages/agent/        AgentHome · AgentCases · AgentCaseDetail · RecordVisit · BeatMap · AgentProfile
  pages/manager/      Overview · Cases · Agents · Analytics · Compliance
```

## Conventions

- Files carry `# ─── CHANGELOG (prototype → product) ───` headers documenting what
  changed and **why**. Follow this when making non-obvious changes — it is the most
  valuable documentation in the codebase.
- Those headers cite `changelog.md` and `final_changes.md`. **Neither file exists
  anywhere** — 31 files reference them. Don't hunt for them; the header text is the
  whole record.
- Services raise `AppException` with a typed `ErrorCode`, not free-text detail.
  `main.py` maps it to `{detail, code}`.
- Tenant scoping is by `Agent.manager_user_id == current_user.id`, hand-repeated in
  each manager endpoint. See the known bugs below.
- `stub_main.py` + `main.py` at root are the old `:8300` Command Center contract
  stub. **Nothing consumes them.** Safe to delete.

## Known issues

Full analysis in [docs/AUDIT.md](docs/AUDIT.md). The ones that matter most:

1. **Three unscoped manager endpoints** (cross-tenant leaks). `GET /manager/compliance`
   and `GET /manager/ai/monthly-report` compute over *all* agents platform-wide;
   `PUT /manager/agents/{agent_id}/status` has no ownership check at all, so any
   manager can change any agent's duty status. Fix with one shared dependency, not
   three patches — the copy-pasted scoping is the root cause.
2. **The offline banner is not backed by anything.** `AgentLayout.tsx` toasts
   "actions will queue" and shows "visits will sync when reconnected". There is no
   service worker, no manifest, no IndexedDB and no outbox. Field work submitted on
   a dead connection is lost. Either build it or remove the banner.
3. **No prediction layer.** `app/ml/` holds one rule-based file. `risk_score`,
   `bank_risk_score`, `collection_priority_score` and `recovery_potential` are all
   bank-supplied or randomly seeded, never predicted.
4. **The audit trail is mostly unwritten.** `AuditLog` declares 21 action types;
   only 3 call sites write to it (auth, OTP, payment). `VISIT_RECORDED`, `PTP_SET`,
   `DATA_EXPORT` and 15 others are defined and never emitted.
5. **Test coverage ~5%** — 523 lines against ~10.2k backend lines, none covering
   `manager.py` (2316 lines). Zero frontend test tooling.
