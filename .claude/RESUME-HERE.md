# RESUME CHECKPOINT — owner went offline 2026-09-30 ~18:00 IST

## Where things stand
- main (TIQCollect-app) = 1678e2b: bank-overview P0 fix (v2_0017/0018), directory, onboarding,
  placements, prod stack, maps, lint, 2 docs (ONBOARDING, MOBILE-APP-PLAN), C08/C09 tasks added,
  v1 dump removed. Repo PUBLIC. Dev DB upgraded to v2_0018.
- Groq key IS in backend/.env (works — tested, PONG from groq). But redaction is NOT merged yet,
  so DO NOT trigger AI features until l3-redaction merges (agent/borrower names would go unredacted).

## Branches waiting to merge (CI was running when owner left; results will be on GitHub)
- l3-redaction (4a2a3af): PII redaction + F12 + prompt fencing. Seam tests PASSED (273). SAFE to merge.
  → After merging, recreate api container so redaction is live, THEN AI features are safe to use.
- l5-bank-ui (1fcc14c): AI Models showcase page. Mutation-checked.
- l8-rls-s1 (95b16b5) + l8-rls-s1b (f7d36e9) + l8-agency-decisions (6f48f0c): RLS + tenant-leak fix.
- p4-strategy (9e64575): Monte Carlo E01 + ADR 0013/0014.
- l9-agency-fixes: IST-date bug, leave-status, real QR (may be unpushed — check worktree C:/dev/tiq/l9).
- l7-n1-evidence-honesty: fake-liveness / evidence honesty (unpushed — worktree C:/dev/tiq/l7).
- l7-payment-idempotency: the CRITICAL payment double-count fix (in progress).
- hotfix/collection-efficiency-scope: KPI per-agency abstain fix (I applied it, in CI).

## backend-pg CI note
It's SLOW (~30 min), not hung — restores the 52MB fixture + full pg suite on 2-core runners.
Merge a branch when its backend-pg goes green. If one passes 35 min, THEN it's a real hang.

## Demo book (L6, tiqcollect-37) — the thing that fills the owner's empty portal
- Rebuild DONE: 56MB, 8 active agencies, performing book + seasonality, real per-agency/agent perf.
- collection_efficiency KPI fix applied (hotfix branch, in CI) — merge it before restore.
- L6 building docs/DEMO-LOGINS.md (agencies + logins + performance) + apply_agency_staff() login mechanism.
- ON RESUME: once hotfix merges + L6 confirms dump green (30/30), RESTORE the new book into
  fieldops_dev_postgres/fieldops_v2 so owner sees agencies+logins+KPIs on localhost:5473. Owner pre-approved.


## DEMO BOOK READY (added after checkpoint)
- L6 dump + logins doc + staff mechanism all pushed: personal/l6-rebuild @ 70508d3.
  f4c1b2d=fixture (56.2MB, perf book+seasonality, Awadh 30%/4%, Compliance 76.8),
  b4f5b4f=apply_agency_staff() (off by default), 70508d3=docs/DEMO-LOGINS.md + generator.
- 8 sections (bank + 7 agencies), unique emails, real per-agency + per-agent perf, shared demo password.
- Aravalli shows collection_efficiency 'not available' honestly; other figures real.
- RESTORE NOT DONE (laptop went offline mid-session — did not risk a mid-write). On resume:
  merge hotfix/collection-efficiency-scope first, L6 merges it into l6-rebuild for genuine 30/30,
  THEN restore l6-rebuild's dump into fieldops_dev_postgres/fieldops_v2. Owner pre-approved.

## Owner's open decisions (in .claude/owner-tasks.md)
- Where to keep an off-repo master copy of the v1 demo dump (removed from tree).
- Payment REVERSAL path (PaymentStatus.REVERSED has no writer) — product decision.

## RESUME SEQUENCE
1. Check .claude/fullsuite.lock — if L6's stale, clear it. Check no orphan containers.
2. Merge every branch whose backend-pg is green (l3-redaction first — unblocks safe AI use).
3. After l3-redaction merges: docker compose up -d api celery_worker celery_beat (picks up redaction). Tell owner AI is safe.
4. When L6 confirms dump green + hotfix merged: restore the new book into dev DB.
5. Read .claude/owner-tasks.md and coord-state for the full picture.
