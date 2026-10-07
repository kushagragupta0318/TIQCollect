# RESUME CHECKPOINT — 2026-10-07 (owner off the PC, build continues)

## Integration branch: TIQCollect-app @ ead163d (pushed personal/main). dev DB fieldops_v2 @ v2_0029.
MERGED: everything through reversal. Chain single-head v2_0029.
- Done+merged: l8-rls(0026), l7-n1(0027), D07/E05, analytics(8 tabs), Audit page(+nav wired),
  REVERSAL two-stage (owner-authorized 2026-10-07, bank stage live, v2_0028/0029).
- CI: green path (verify on resume).

## FAST PROD DEMO APP: container tiq-prod :8500 (STALE — built at v2_0026). REBUILD after the page wave:
  docker build -t tiqcollect:local .  &&  docker rm -f tiq-prod  &&  docker run -d --name tiq-prod \
    --network tiqcollect_default -p 8500:8300 --env-file backend/.env \
    -e DATABASE_URL="postgresql+psycopg2://fieldops:fieldops_dev_pass@postgres:5432/fieldops_v2" \
    -e RUN_SEED=false -e WEB_CONCURRENCY=4 -e "ALLOWED_ORIGINS=http://192.168.1.217:8500,http://localhost:8500" tiqcollect:local

## BUILDING NOW (7 bank pages, fanned across lanes + 2 subagents):
- 12 → MLOps console (Tech Ops) — reuse ModelCandidate/monitor.py/bank_models
- 68 → Alerts(C06) then Data Quality(F10)
- dd → Regions (admin)
- 84 → (Board Reports or Cash Forecast)
- ed → Board Reports(E10) — reuse parked/e09 engine (1eb224f)
- subagent ac38304 → Settings
- subagent afe1ed59 → Bank Users
- NEW lane → Usage & Cost(F11, needs ai.llm_calls table)
- 72 → MESSAGING (bank↔agency) after reversal merge
NONE touch BankApp.tsx/navigation.ts — COORDINATOR wires nav in ONE pass per page (BUILT_PAGES in
BankApp.tsx:38 + BUILT_NAV_PATHS in navigation.ts:248). Nav items already declared in navigation.ts.

## COORDINATOR LOOP (while owner away): on each "X green, head <sha>" ping →
  1. merge the branch, 2. wire its nav (BUILT_PAGES + BUILT_NAV_PATHS), 3. if it adds a migration:
  alembic upgrade head on fieldops_v2 (`docker compose run --rm --no-deps --entrypoint alembic api upgrade head`)
  ELSE the stack crash-loops, 4. push. Migration numbers: give each lane the next free at merge.
  Audit each page (tiq-auditor) before/after merge; lead-BD honesty pass at the end.

## BLOCKED ON OWNER (do NOT route around — classifier guards shared-DB writes):
- Placement backfill (Recovery tab + P(pay) column show data) — script ready, owner must approve the
  write in 68's session or run it. Until then those two surfaces are honest-empty.

## ON RESUME: docker compose up -d + docker start tiq-prod; clear a stale .claude/fullsuite.lock;
  continue merging page branches; rebuild :8500.
