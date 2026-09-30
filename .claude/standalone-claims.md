# Standalone tasks — claims board (shared, untracked)

Every session working docs/STANDALONE-TASKS.md: add your claim here BEFORE
starting a task, and mark it done when committed on your branch. Untracked on
purpose (.claude/ is not in git) so it never merge-conflicts.

Rules
- One worktree + branch per session. Never edit the main tree
  (TIQCollect-app) — that is the user's checkout and the integration target.
- Do NOT tick boxes in docs/STANDALONE-TASKS.md on feature branches: adjacent
  line edits conflict on merge. Record status here; the ticks are applied
  once, at integration.
- Integration into TIQCollect-app happens only with the user's go-ahead.
- Touching a file outside your lane? Say so here first (Shared files below).

| session | worktree / branch | tasks | status |
|---|---|---|---|
| tiqcollect-43 | .worktrees/p1 · standalone-p1 | all of P1-B (B02–B22: models/**, core/database.py, alembic/**, scripts/seed_data.py, ml/simulation/ledger/materialise.py, the generator + fixture + backend/fixtures/README.md, tests/_db.py + every test that builds an engine) · P1-A except A10 (A01–A09, A11–A16: core/dependencies.py, core/security.py, services/auth_service.py, endpoints/auth.py, new core/permissions.py + scope.py) · then P2 G01–G05, G07, D01–D04 and P3 C*/D05–D09/K01 | **MILESTONE 2026-09-28 (owner offline).** DONE: CI red fixed 771b5a4; A09b audit e359636 (v2_0010); A14 4d77b02 + receipts verified; p1-d4 merged 014fe58; auth LOWs 786a5c2; A13 step 1 merged b067770 (v2_0012) + follow-up 3dc8180; D02 enum e500bde (v2_0011); B22 verified complete. OPEN on branch b13b (C:/dev/tiq/b13b): 65beb44 B13b v2_0013 (full suite 2541 passed) · 860c409 v2_0014 storage_key UNIQUE · 2cf2e1d Opus-audit fixes (targeted 164 passed; full suite NOT run: coordinator's CI). Not pushed by me. NEXT: coordinator CI + merge of b13b; then B20 (ML adapter/equality harness check), B21 (stress timings), B13c (business-day calendar), A13b (owner-gated). No lock held, no containers running. || **2026-09-28 15:45: standalone-p1 @ 014fe58**. CI red fixed 771b5a4. A09b audit fixes e359636 (v2_0010 DEVICE_BOUND). A14 4d77b02 (receipt items NOT done). p1-d4 merged 014fe58 (d4-reviewed login/complete_login). Full suite 2319 passed, 18 skipped. Not pushed. Next: A13 RLS. || 2026-09-24 16:15 (**P1 split accepted**: 43 keeps data model, B-chain, A03, A05, A09, A13, A14; ce A01/A02/A04/A12/A15; d4 A06/A07/A08/A11/A16-own): B02–B10 built; B11 steps 1-3 + audit gates 1-4 + MED 5-7 (19e91ad, 0eb7df6); A03 scope.py wired + set_ptp cap; auth gates 1/2/3/6/8; ~~BL-5 neutral post-visit SMS~~ (dropped: d4's hotfix-1 copy is the one definition); frozen interfaces (scope.agents_in_scope/cases_in_scope, auth_service.open_session/revoke_user_sessions) written. Fixture repair on 8 files in progress; stable SHA for ce/d4 after that targeted run; full suite via lock (after ce, bb). **2026-09-28 (latest): head `a5c864a`.**
- B15 fixture 7fa1cf5; v2 preview stack b21fe8a (boot-tested under the lock as tiq-v2-bt: 3 logins OK, then down -v).
- B12 partitions 1057c30 (MED 8 proven on PG); B14 DB config b9771a2; B13a analytics part A a5c864a (reconciles exactly on the fixture).
- Container files green, except test_llm_providers, which fails only because the fieldops-test image is stale (no anthropic SDK); 81/81 in tiq-v2-dev.
- **Open for 43:** B13b (after loan_dpd_history/cost_rates/A13), permission seed v2_0008 (after ce A01 is green), "complaints table?" (from d4), B19, A09, A13, A14.

**Earlier, 2026-09-28: P1 BASE = `3498391`** = v1 main 0a4513f MERGED in + main's tests on v2 + v2_0006 AGENT_UPDATED. Opus audit PASSES all 7 items.
- **DELIBERATE LOOSENING (accepted by 64):** the payment link uses the scope rule (assigned OR on today's beat, own agency), not hotfix-1's strict `agent_id ==` check.
- **Roster deviation ACCEPTED:** Vikram Malhotra stays AGENCY_MANAGER (master login); v1 System Admin → Meera Khanna, Aravalli AGENCY_ADMIN.
- Head after: from_our_app fail-closed LOW fixed.
- **B15 in progress:** the transform runs end to end on PG (fieldops_v1src → fieldops_v2fix). Next: the dump + boot test.

Before that, **2026-09-28: head `16370be`.**
- cbde112: v2_0005 op.f fix. PG round trip + `alembic check` CLEAN on fieldops_p1.
- 9ba2dc8: CUSTOMER_TAG_DECEASED mirrored from d4's 24b0ed7.
- 16370be: zero-amount ledger promises are dropped by ONE product rule (models/ptp.promise_is_for_money, via ledger/product_rules) in BOTH the panel and the materialiser. Phase 3 harness: 80 green. **NOTE for bb's doc pass:** panel PTP features moved slightly, so the Phase 1-3 headline numbers in CLAUDE.md need a re-run and a visible correction. The 2.2.0 artifacts are untouched.
- **LOCK SLOT (after ce, bb, d4):**
  - ml_scoring_adapter, ml_lifecycle_api, monitoring_pipeline_wiring, entrypoint_fixture (-Repo), recovery_migration;
  - then test_ledger_simulator + test_ledger_phase2.

Earlier: Opus audit of 0eb7df6..4e22657: no HIGH or MED left. Its 2 LOWs are fixed at f17cf90. Heads since:
- v2_0004 e8b7d22: d4's identity audit actions + 8 declared P2 lifecycle members, totp_secret TEXT, totp_last_step BIGINT. Audit CLEAN.
- DB_WAIT validation e5d8524.
- v2_0005 44e2ec4: revoke reason MFA_CHANGED, CHECK pinned to the model. Audit CLEAN; its downgrade LOW is fixed at 3d4ebdb.
- A01 permission seed → **v2_0006**, waiting on ce's green SHA. Generator ready: 75 permissions / 139 grants.
- The host venv (scratchpad) is shared read-only with ce and d4. **P1 BASE = `4e22657`** (posted to 64 with the frozen interface list). Tested on the host venv while Docker was wedged: all green except 16 host-only sklearn-1.8 artifact failures. **PENDING in the container:** ml_scoring_adapter, ml_lifecycle_api, monitoring_pipeline_wiring, entrypoint_fixture, alembic_v2_baseline, recovery_migration. Run them after "docker back". Superseded below: **MILESTONE 17:07 (OWNER STOP)**:
  - **Head:** `187c1b3` (WIP), on 0eb7df6.
  - **Audited:** 0eb7df6 (64).
  - **Unaudited, in 187c1b3:** all re-audit fixes.
    - HIGH 1 via scope.today_beat_cases in case_service AND agent_service get_beat/home_summary/profile (the tiq-auditor Sonnet pass found agent_service).
    - MED 2 OTP issuer check; its 400 deviation was accepted.
    - MED 3: the sync_assignee kwarg was removed; scope.sync_assignee now runs at commit.
    - MED 4-6 entrypoint, plus check_migrations and ensure_db_settings.
    - LOWs.
    - Voice identity agent_<hex>_<hex>.
    - BL-5 copy dropped.
    - Fixture repairs.
  - **Tested green (partial serial run):** scope 24, interfaces 4, otp 24, schema_v2 18, voice 13, EB 17, live_events 11, labeller 43/44, entrypoint 18/19 (the assertion is fixed).
  - **Written but untested:** beat/home-summary tests, the ptp_counts IST pin, the check_migrations tests, the entrypoint empty notice, manager_priority_list, the point_in_time EB seed, planner (partial), ml_scoring_adapter, the 4 ML lifecycle files. labeller::test_a_fully_matured_caseless_row_leaves_the_scan FAILS (traceback unseen).
  - **NEXT on resume:** once the lock is free, run 3 serial batches of ≤10 files:
    - (A) agent_case_scope, agent_ptp_counts, otp, entrypoint(-Repo), scope_interfaces, voice, schema_v2, alembic_v2_baseline, recovery_migration;
    - (B) visit_priority, manager_priority_list, point_in_time, repayment_labeller, planner, ml_scoring_adapter, empirical_bayes, live_events;
    - (C) the ML lifecycle 4.
    Fix, then commit, then send ce/d4 the base SHA plus the frozen list (the interface change is in the commit message).
  - **Open decisions:** none pending. 64 settled both: the OTP 400 deviation and no auto-upgrade at boot. At rebase: map d4's BL-5 to brand_for plus the tenant-gated send, and give d4's identity_for the (user, sid) form. |
| tiqcollect-43 (bg agents) | own isolated worktrees, auto-named branches | UI02–UI05 (frontend/src/bank/**, tailwind.bank.config.js, /bank routes in App.tsx, /bank/_gallery) · E02+E04 (backend/app/strategy/monte_carlo.py, backtest.py, tests/test_monte_carlo.py) | UI02–05: **merge-ready at 553d746** (UI-1..3 + LOWs closed; vitest 286/286, lint 7/7, main CSS byte-identical) · E02/E04: 5bb5bed (all asks closed, 39 passed) — **PARKED per owner (pilot-first), not merging**; E04 CI-on-dev half PARTIAL (16:40) |
| tiqcollect-ce | .worktrees/ui · standalone-ce | F01 (core/llm.py anthropic provider + tool calling) · I01 (PWA: vite.config.ts, public/ icons+manifest, main.tsx SW register) · G06 (ManagerLayout.tsx mobile nav) | **MILESTONE 17:15, owner-stop — clean, nothing uncommitted.** Head SHAs: standalone-ce `273a262` (merge of hotfix into ce, includes ce's own *.js-404 rule on top) · hotfix/spa-containment `e8f2eb4` (C:/Users/gupta/tiq-hotfix-spa, from c75053a). **Done, AUDITED:** F01 all coordinator gate/should/LOW items (2 audit rounds) · I01 all audit items (preview-port fix, per-tab reload not all-tab, hourly+focus update check, precache images, rollback doc) · G06 (no findings) · hotfix hardening rounds 1–3 (containment, NUL-safe, root-realpath guard) — all closed in code and targeted-tested, per-commit mutation-checked where the coordinator asked. **Done, UNAUDITED (targeted tests green, full suite never completed):** nothing new since last audit — the 3rd hotfix round (e8f2eb4) and the resulting ce merge (273a262) were sent for audit and CLOSED by the coordinator already; what's missing is not review, it's the LOCKED run (pytest -n 4 full suite, npm build/test/lint, both prod images, the isolated live-SSE-under-installed-SW check) — never completed end to end, stopped by CPU contention then by this owner-stop. Targeted-only evidence so far: ce backend 136/136, hotfix backend 20/20, frontend src/lib vitest 62/62, hotfix-branch's own 20/20 — none of that is the full suite. **Written but NOT STARTED (queued, no code yet):** llm.bad_response PII redaction (coordinator request via d4/H14: drop `preview=text[:120]` from the log line, replace with length+hash, one test asserting a PII-like string never appears in the log record) — nothing edited, safe to pick up fresh. **Exact next step on resume:** (1) run tiq-verify's full sequence once, uncontended, via `.../scratchpad/final_run.sh` (hotfix stage first, tag `tiq-prod-check:hotfix-spa` + `tiq-prod-check:ce`, containment probe on both images, then the isolated postgres+redis stack + live_sse_check.py for I01's SW/SSE condition) — nothing in that script needs edits, just re-run; (2) do the llm.bad_response PII fix on standalone-ce, commit; (3) send both hotfix + ce numbers to the coordinator, "ready for audit" only after the full suite is actually green; (4) then P1: A01/A02/A04/A12/A15 once 43 publishes the base SHA + interface list. **Open decisions (owner's):** whether/when to hotfix the live `fieldops.transorg.ai` deployment for the same static-file-containment issue (COLLECTIONS-side, out of this repo); merge order of hotfix-3 vs hotfix-2 vs ce (coordinator's call, pending the full-suite numbers). *Dropped UI02–05/E02 on 43's word; ceded A10 to d4.*
| tiqcollect-64 | none (read-only) | coordinator: progress, cross-lane audit, integration plan | active |
| tiqcollect-2b (L3 AI runtime, from 2026-09-30) | C:/dev/tiq/l3 · l3-lint from TIQCollect-app 3d3e2dc | brief from tiqcollect-06: T1 lint unblock (done), then F02 agent runtime + F03 tool registry. Planned files: NEW core/redaction.py (SHARED: announcing — no PII helper exists today), NEW services/ai/** , NEW endpoints under /bank/ai, workers/tasks thin wrapper. Uses the capabilities that already exist (ai_agents.read/manage/run, ai_actions.approve) — no permissions diff needed so far | 2026-09-30: **T1 DONE @ 7b0f5d7** (lint 4 errors -> 0; build OK; vitest 633 passed), not pushed. **FLAG: F02/F03 are P5, which the owner PAUSED on 09-24 ('P5 beyond F01'); 0c reported an owner-approved P4-P6 restart on 09-29. Asked the owner before building the runtime.** My P4 E01/E02 (ADR 0011) stays parked. No lock held |
| tiqcollect-37 (L5 bank UI, from 2026-09-30) | C:/dev/tiq/l5 · l5-bank-ui from TIQCollect-app 3d3e2dc | brief from tiqcollect-06: (1) verify coverage-map fix (MapCanvas leaflet.css) on AgencyDirectoryPage + sweep MapCanvas consumers + polish directory; (2) F06 AI Agents UI (list, editor, test console, run traces, approvals inbox) — owner DROPPED F06 on 09-24, asking 06 to confirm reinstatement before code; (3) C04–C08 placeholder pages, KPI list from 06 first. Files: frontend/src/bank/** (pages/directory, new pages/agents/*, pages C04–C08), components/map/** only if a map defect needs it | 2026-09-30: item 1 in progress. No lock held |
| tiqcollect-37 (P7, done) | C:/dev/tiq/p7 · p7-offline from TIQCollect-app 4d6c796 | I02 offline outbox (IndexedDB: visits incl. PTP/stance/notes, photos, signature, GPS trail, call logs if cheap; payments NOT queued, refuse offline) + P0-A6 (slot-namespaced offline keys). I03 OUT (0c). Files: frontend/src/lib/outbox*.ts (new), lib/locationReporter.ts, pages/agent/RecordVisitPage.tsx, payment screen, AgentLayout pending badge; backend visit create idempotency (client_submission_id; migration requested from 43/77) + capture-time checks | **PARKED 2026-09-29 evening (0c stand-down).** PR#24 head 50da2cd (merged TIQCollect-app 93a6639; storage.py conflict kept both sides; targeted 186 passed, vitest 531/531); 0c merges on green CI. Tree clean, no lock, no p7 containers. Next possible: Wave C T15 (H12) per 0c. Earlier — 2026-09-29 13:00: I02 READY FOR MERGE at 216328c (pushed personal/p7-offline, PR#24). tiq-verify: pytest 2601 passed/0 failed/69 skipped, vitest 512/512, lint 4/4 baseline, prod image 3.08 GB OK. Opus audit fixes fed4e09; rename device_id->capture_device_ref 216328c. No lock held, no p7 containers. Earlier: 7aa218e server clock, daf4f84 read cache (outbox + capture-time + signals + in-app read cache + P0-A6); targeted green (backend offline 42 + neighbours 123, vitest 506/506, 10 mutation checks caught); full tiq-verify RUNNING; tiq-auditor running; then Opus audit slot from 0c. ADR 0011 ACCEPTED by 0c. Migration from 43/77 on `offline-keys` (v2_0015, csid on visits/call_logs/ptps + agent_devices seq cols). **BEHAVIOUR CHANGE (0c-approved): location_service max fix age 24 h → 48 h** (one OFFLINE_MAX_AGE_HOURS for visits + GPS trail), pinned by a test |
| tiqcollect-bb | C:\dev\tiq\standalone (personal remote) | LEAD DEV: restructure Wave 1, merge gate, standalone deploy (ADR 0009) | STOPPED 2026-09-29 evening (owner offline; stand-down from coordinator). Tree clean, no lock, no bb-* containers/networks. MILESTONE: lead/standalone-deploy pushed to personal remote, PR #22 open (kushagragupta0318/TIQCollect, base main). HEAD d48fcbb. DONE TODAY: cold start #2 + audit fixes verified and recorded (SECRET_KEY>=32 in prod, CSP-RO+XFO, MFA key check, body caps, minio-init anon-none, /minio blocked, healthchecks, create_first_admin, OSRM default '' + no-call-when-unset) -- 97 targeted tests passed. ADR 0010 (object storage after minio/minio unpullable): S3 compatibility probe run serially (low RAM), all 5 candidates (cached minio, pgsty/minio, chainguard/minio, SeaweedFS, Garage) pass storage.py round-trip + anon-denial + tampered-sig checks; Garage needed s3_region=us-east-1 to match the client, now documented. Recommendation: pgsty/minio pinned+mirrored, Garage as tested exit. Also landed: mapbox CSP entries (corrected per 4d's real trace: img-src only, no connect-src), VITE_MAPBOX_TOKEN build-arg wiring for p3-map, one-time reconcile_placements (v2_0016) doc step, a broken bash example in DEPLOY.md fixed. OPEN, flagged on PR22 comment: (1) CI has not re-run since the initial 7db0228 check (frontend lint failure) despite 5 more pushes -- pull_request trigger not queuing on this PR/branch pairing, needs repo/Actions visibility I don't have. (2) PR shows CONFLICTING but it's two small append-only conflicts (docs/STANDALONE-TASKS.md, docs/adr/README.md) against personal/main, confirmed via git merge-tree -- not attempted myself, touches other lanes' concurrent doc edits. (3) owner still needs to pick the MinIO replacement (ADR 0010 recommends pgsty/minio; not yet applied to compose defaults). (4) deploy-audit LOW not done: separate front network for caddy+api; CSP report-only -> enforce after SPA check. (5) Wave 1 remainder untouched: 1.15 exploration test, 1.7b requirements split, lane-deferred dead code. NEXT STEP (tomorrow, Wave A slot T4 per coordinator): apply ADR 0010 -- MINIO_IMAGE default to pinned pgsty/minio digest in docker-compose.prod.yml + deploy/.env.prod.example, mirror the image to a registry we control, note the mirror in DEPLOY.md, then resolve the two PR22 doc conflicts and chase why CI isn't re-running. |
| tiqcollect-fb (business lead) | C:\dev\tiq\biz (outside OneDrive) · business-lead | BUSINESS LEAD (owner's brief, .claude/business-lead-prompt.md): persona journeys walked in the running app, usage reality check (demo DB read-only), commercial sort of the task list, unit economics + pricing, demo/pilot readiness → docs/business/{REVIEW,JOURNEYS,PRIORITIES,ECONOMICS}.md only. Read-only on all code, branches and worktrees; no Docker/seed/migration/test-suite runs; any demo-DB write labelled "business walkthrough". Talks to other sessions only via tiqcollect-64 | **Deliverables committed on business-lead**: `0bdd9bb` (REVIEW/JOURNEYS/PRIORITIES/ECONOMICS + img/), `80ff407` (ECONOMICS.md reconciled against bb's cost model — floor ~₹549/agent-month DLT+Groq, ~₹4,549 Twilio), `0329475` (demo naming collision check: Girivan Finance Ltd picked; Konkan Asset Recovery flagged for rename). Demo-DB writes: visit 5ae30392 + PTP 20b97a0e (EMP0006, labelled). No tests/builds (docs only). **MILESTONE (owner stop, this session going idle): HEAD `e26dd90`, all committed, clean tree.** Done: REVIEW/JOURNEYS/PRIORITIES/ECONOMICS all written; ECONOMICS reconciled with bb; full naming roster screened and picked (lender Girivan Finance Ltd; 2nd tenant Kumaon Finance Ltd; Konkan→Sahyadri Field Recovery; 2nd-tenant agency Sahyadri Recovery Desk→Almora Recovery Desk). Open: none of mine pending — full roster already sent to 43 via coordinator for B16. Idle now |
| tiqcollect-4d (MAPS) | C:/dev/tiq/map · p3-map from 93a6639 | P3-map (coordinator brief 2026-09-29): Mapbox raster tiles via ONE config frontend/src/lib/mapTiles.ts (VITE_MAPBOX_TOKEN, OSM fallback, host allowlist, prod-build warning), night variant on manager live map, tile perf options, leaflet.markercluster (>40 pts), polyline simplify, hover debounce, Mapbox attribution + wordmark, tests. SHARED (to announce): package.json + lock (new dep), Dockerfile frontend-stage ARGs, docker-compose.yml web env, vite.config.ts (build warning plugin) | **MERGED 2026-09-29**: p3-map 7025767 at 96af078, README follow-up ba0ddea at 9c86b79 (verified locally: both ancestors of TIQCollect-app). DEPLOY/prod-compose token build-arg lands with bb's lead/standalone-deploy. Owner: Mapbox token URL restriction + eyeball /manager/live-map with the token. Lane idle. |
| tiqcollect-d4 (hotfix) | C:\dev	iq\hotfix · hotfix/live-security from c75053a (v1) | OWNER-APPROVED live hotfix: A10 (cherry-pick 461fee2), AU-2 voice (agent.py voice/outbound + voice/token, useVoiceCall), PAY-1/PAY-2 UPI (RecordVisitPage demo auto-confirm behind VITE_DEMO_UPI_AUTOCONFIRM, collect_payment UPI reference rule, UPI_VPA/UPI_PAYEE_NAME settings) · DEMO-LOGIN · PL-1 · BL-5 | 461fee2..b83fd33 (7 commits). **AUDIT CLOSED** by coordinator at b83fd33 (16:5x). Round 3 bb4371a+5b88b27: 16/16 mutations caught. Re-audit b83fd33: 6/6. Targeted 137 passed. Locked tiq-verify at b83fd33 QUEUED behind ce, then bb (container d4-verify-HHMM); merge follows green. **MILESTONE (OWNER STOP, d4 idle; all three trees clean, no d4 containers, the verify wrapper cancelled; the lock was never taken)**:
  - **SHAs:**
    - hotfix/live-security `b83fd33` (C:\dev\tiq\hotfix);
    - feat/ml1-stance `e5fed3e` = cf433a8 plus a wording commit (C:\dev\tiq\ml1; frontend/node_modules is a junction to the hotfix worktree's);
    - standalone-d4 `0b7d7a9` (WIP) on 759dfa8;
    - parked/e09 `1eb224f`;
    - backup/d4-pre-park `45bf939` (delete once standalone-d4 has merged).
  - **Audited:**
    - hotfix-1 b83fd33: AUDIT CLOSED.
    - ML-1 A cf433a8: AUDIT CLOSED. e5fed3e is wording plus a comment, no behaviour change.
    - H14 r2 759dfa8: closed except H14-2. 0b7d7a9 fixes H14-2 and is UNAUDITED.
  - **Untested:**
    - hotfix-1: the full suite, npm build/test/lint and the prod image (the locked verify never ran).
    - ML-1: the full suite, npm build, the prod image, and a component test of "required" (none exists).
    - 0b7d7a9: mutation checks, the full suite and vitest (the extraction file alone is 127 passed).
  - **NEXT on resume:**
    1. The hotfix-1 locked tiq-verify at b83fd33: `bash <scratchpad>/verify_after_bb.sh`, or `verify_hotfix.sh` directly when it is our turn on the lock. If the GitHub CI pytest leg is green by then, run compileall + npm + prod image only.
    2. Then the same for feat/ml1-stance. Merge order: hotfix-1, then ML-1.
    3. Mutation-check 0b7d7a9 (comma, month-like lookahead, sentence split, backward year roll), drop "WIP" in a follow-up commit, and send it for audit. standalone-d4 merges after that.
    4. P1 A06/A07/A08/A11/A16 on 43's stable SHA, using the identity_for(user, sid) form.
  - **Open decisions:** none pending with the owner.
    - The Command Center deploy step (put the master password into TIQCOLLECT_AGENCY_ACCOUNTS for manager1, or keep-list the account) is ops, at deploy time.
    - The live demo loses the fake "Payment received" unless the API sets DEMO_UPI_ACCEPT=true. That is intended, and ops should know. |
| tiqcollect-d4 (ML-1) | C:\dev\tiq\ml1 · feat/ml1-stance from b83fd33 (v1) | ML-1 option A: borrower stance (BorrowerDisposition) on the visit form (Borrower path, replaces Borrower Tone, REQUIRED) + call-log form (ANSWERED, optional); services/borrower_stance.py (422 DISPOSITION_WITHOUT_BORROWER); schemas + visit_service + agent.py log_call | cf433a8 + e5fed3e (wording): 21 backend + 31 vitest, 7/7 mutations, targeted 211 passed. **AUDIT CLOSED** (7/7). **Visible deviation (accepted by coordinator): the stance is REQUIRED on the Borrower path — UI-only; the server stores NULL when none is sent (deliberate: ce's I01 outbox replays pre-change visits).** Merges right after hotfix-1. Full suite + prod image: after hotfix-1's locked slot |
| tiqcollect-d4 (P1) | C:\dev\tiq\p1d4 · p1-d4 from 43's 4e22657, rebased onto 44e2ec4 (v2_0005) | A06 invites · A07 password lifecycle · A08 TOTP for bank roles · A11 frontend auth pages · A16 audit wiring. NEW: services/{credentials,invite_service,password_service,mfa_service}.py, endpoints/accounts.py (/auth account routes + /admin), schemas/accounts.py, core/audit.stage_audit, frontend lib/authFlow.ts + pages/auth/{SetPassword,ResetPassword,ForgotPassword,MfaSetup,AccountSecurity}Page + components/auth/AuthCard. SHARED (announced, 43 OK): auth_service.login/quick_login/refresh_tokens/revoke_user_sessions + complete_login, schemas/auth.py, endpoints/auth.py login route, router.py, config.py (TOTP_ENC_KEY, BANK_MFA_REQUIRED), errors.py, api/axios.ts (401 fix), App.tsx (5 routes). Cherry-picked A10; lib/roles.ts byte-copied from 553d746 (both confirmed) | Head 1ce4fba. 43 reviewed the shared hunks (HIGH quick-login fixed). Coordinator audit of ..a4f0b74: all fixed in 1ce4fba (untested). Frontend: vitest 46/46, tsc clean. **Backend NOT RUN (Docker down)**: test_accounts_{invites,password,mfa,session_gates}.py. Next on docker back: those, serially |
| tiqcollect-d4 | .worktrees/d4 · standalone-d4 | A10 frontend half (LoginPage, LandingPage, ManagerBridgePage + route, public/collection_dashboard/, grep test over src/public/e2e, e2e creds env-required, compose comment per 43). NOT /quick-login, backend auth, fixtures/README.md (43) · H14 (voice → structured visit report: new services/visit_report_extraction.py, one agent.py route, RecordVisitPage) · then E09 (report renderers, new backend/app/reports/) | standalone-d4 = A10 dd2b11a · H14 a4c834b + 71c2eba (was 5d70298) + round 2 759dfa8 (extraction 1.2.0, 94 tests, 10/10 mutations). **E09 moved to `parked/e09` (1eb224f)** per coordinator; standalone-d4 no longer carries reports/ or the requirements.txt appends. Next: ML-1 A stance row, then P1 A06/A07/A08/A11/A16 on 43's stable SHA. Full backend suite: not run on this branch yet (lock). *Ceded G05 to 43* |
| tiqcollect-2b (P3 placements → P4 E01/E02) | C:/dev/tiq/placements · p3-placements from TIQCollect-app 4d6c796 | P3 D08 manual placement, then D09 placement engine (0c's brief). Files: services/placement_service.py (gates), NEW services/manual_placement_service.py, placement_read_service.py, endpoints/bank_placements.py; later services/bank/{agency_effect,placement_engine}.py; frontend src/bank placement page. SHARED (announced): router.py (1 include + own import line at end) · scripts/ingest_daily.py (feed RECALL ends placement; next: PAID_DIRECT/SETTLED→RESOLVED, WRITTEN_OFF→RETURNED) · services/scope.py (additive region_limit_path) · src/bank/BankApp.tsx + navigation (d4 owns collisions; coordinate before commit) · cherry-picked ce's df44cbd expected_recovery.py · workers/tasks/demo_daily_feed.py (branch inside coverage) · core/config.py (+PLACEMENT_EXPLORATION_RATE) · ml/empirical_bayes.py (eb_shrink extracted, math unchanged) · docs/adr/0010 + README row | P3 D08/D09 MERGED (8189876) + f707475 demo-feed product fix MERGED; 003b785 Engine-tab confirm + component tests pushed by 0c, awaiting CI/merge. Last locked verify at b8c5842: full 2813 passed/0 failed/75 skipped, tests/pg 71, vitest 578, lint baseline, prod image 3.07 GB; targeted 52 passed at f707475. 2nd demo BANK_ADMIN prepared by d4 (Kavya Reddy, unusable hash, NOT in master accounts: owner decision). NEXT: P4 E01/E02 per ADR 0011 (approved by 0c): cherry-pick 43's 5bb5bed strategy/ with -x, dedupe states.py onto models/loan, transitions.py Dirichlet posterior + abstention, stamping, E03 seam, endpoint (capability diff to 0c first). **PARKED 2026-09-29 evening (0c stand-down, owner offline).** Tree clean at 003b785, no lock, no tiq2b containers. Resume tomorrow per 0c's scratchpad/tomorrow-plan.md: T2 = the 5bb5bed cherry-pick (Wave A), after the Overview merges. No code started on P4 |

**Status marks (tiqcollect-43, 2026-09-24 15:xx):** A05 PARTIAL (list/revoke endpoints + the four revoke reasons not yet written) · A09b server-issued device secret PARTIAL (A09 binds on a client-chosen device_id) · E04 PARTIAL (the in-CI-on-dev half: loan_dpd_history panel builder + CI job not built) · A14 PARTIAL (brand_for resolver + all SMS/QR text; receipt PDF / verify card not yet).

## Shared files — announce before editing

| file | who | why |
|---|---|---|
| frontend/src/App.tsx | d4 (A10: drop /manager-bridge route) · 43's UI agent (UI04: add /bank routes) | small, separate hunks. ce does not touch it |
| frontend/vite.config.ts | ce (I01: add PWA plugin) · 43's UI agent? (if bank tailwind needs it) | keep VITE_WATCH_POLLING block intact |
| frontend/tailwind.config.js (MAIN config) | 43's UI agent (UI04, c80b08d): adds a `"!./src/bank/**"` content exclusion | claim 'agency/agent CSS byte-identical' UNMEASURED: build c75053a vs UI branch, diff main CSS asset, result goes in the UI04 commit (coordinator 2026-09-24) |
| frontend/package.json + lock | ce (I01: vite-plugin-pwa) · anyone adding deps | lockfile conflicts: whoever integrates second re-runs `npm install` |
| G05 (ID-card QR) | ~~CONFLICT~~ settled: 43 keeps it (d4 ceded, 2026-09-24) | |
| docker-compose.yml lines 14-15 (header comment only) | d4 (A10, commit on standalone-d4): demo creds replaced by a pointer to backend/fixtures/README.md | 43: B18 should not re-add creds there; the pointer is the whole comment |
| frontend/e2e/simulator_acceptance.py | d4 (A10): credential defaults removed, TIQ_* env vars now required | whoever owns P0-08 next: export them from fixtures/README.md |
| backend/requirements.txt | d4 (E09): appends reportlab==4.5.1, python-pptx==1.0.2, openpyxl==3.1.5 at the END of the file · ce (F01): anthropic + transitives | both append-only; trivial merge at integration, then rebuild both images |
| backend/app/core/storage.py | d4 (E09): ADDS one function, upload_bytes(key, data, content_type); nothing existing changed | anyone moving visit media (B23) — keep it |
| backend/app/main.py (SPA catch-all block only, ~L183-198) | ce (I01 audit gate 2): a missing *.js answers 404 instead of index.html; decision extracted to a tested helper; /api + /ws refusal unchanged | small hunk inside `if os.path.isdir(_STATIC_DIR):`; 43's A15 may touch main.py elsewhere |
| frontend/src/pages/auth/LoginPage.tsx, QuickLoginPage.tsx, components/layout/ProtectedRoute.tsx | d4 (A10, committed dd2b11a: demo buttons removed) · 43's UI agent (UI-1 fix: one homeFor(role)) | the UI agent rebases on A10 after it merges and keeps its hunk to the homeFor call only (coordinator, 2026-09-24) |
| backend/app/main.py (header + 1 import + ProxyHeadersMiddleware after the limiter lines ~L94-104) · core/config.py (+FORWARDED_ALLOW_IPS after AUTH_RATE_LIMIT_PER_MINUTE, +DEMO_OTP_ECHO after DEMO_MODE) · otp_service.py L323 · docker-compose.yml api env (+DEMO_OTP_ECHO after DEMO_MODE) · backend/.env.example | bb (hotfix/live-security-2, 062e019) | DEMO_OTP_ECHO is the SAME name 43 introduces on p1 (coordinator) — p1 keeps one definition at rebase |
| backend/app/core/llm.py | ce owns (F01). d4 only CALLS `llm` for H14 — please keep the existing call signature + `response_format` json mode working |  |

## Coordinator — tiqcollect-64 (added 2026-09-24, on the user's instruction)

The user asked tiqcollect-64 to run these sessions as one team: track progress,
audit each lane's diff, report to the user, and flag problems to the owner.
Message `tiqcollect-64` with blockers, cross-lane questions or "ready for audit".
The coordinator does not edit any worktree; it reads branches and sends findings.

**Team rules (on top of the rules above):**

1. **Commit WIP to your branch at every logical step** (`wip(B03): ...` is fine).
   Uncommitted work cannot be audited and is lost if a worktree breaks. Squash at
   integration if you like.
2. **Keep your row's status column current** — one line: task, state, last green
   test run. That column is what the user sees.
3. **The Docker stack is shared and mounts the MAIN tree** (`fieldops_dev_*`,
   fixed `container_name`s, ports 8400/5473/15432). Do not `docker compose
   up/down/restart/build` it and do not run alembic, seed or restore against
   postgres :15432 — that is the user's demo DB. For live checks from a worktree,
   run uvicorn/vite from your worktree on your own port (43: 8410/5483, ce:
   8420/5493, d4: 8430/5503). Anything needing a DB schema change uses a
   separate database (for example `createdb fieldops_<lane>` on the same server),
   never `fieldops`.
4. **Alembic and `models/**` belong to 43** (B02–B11 rewrites the schema into 10
   Postgres schemas and moves to a v2 baseline). ce and d4: no new migrations and
   no model columns. If H14 or F01 need storage, ask 43 for the column.
   **4a (added 2026-09-29, d4 + coordinator 0c; 43 agrees): a migration on `TIQCollect-app` is frozen.**
   Alembic stamps a revision id, not its content, so editing a landed revision's SQL leaves every
   database already stamped at it running the old version, with no warning on `upgrade head`.
   - Change a landed view or MV only through a NEW `v2_00xx` that re-creates it (`CREATE OR REPLACE VIEW`,
     or drop, create and refresh an MV) and restores the old version on downgrade, so `upgrade head` fixes
     drifted databases by itself.
   - Amending an UNMERGED revision in place is allowed, but say so in its commit, and anyone who ran it
     re-applies it: `alembic downgrade <prev> && alembic upgrade head`.
     (v2_0013 was amended in place on b13b at e46b7cc, the consent predicate, before it landed.)
5. **Heads-up from the audit of 43's WIP:** `Agent`, `AgentLocation`,
   `AllocationRun`, `AllocationDecision` and `AllocationSetting` gain
   **NOT NULL `bank_id` + `agency_id`**; several `str` timestamps become real
   `datetime`/`date` (`Agent.last_location_update`, `sos_triggered_at`,
   `AgentPerformance.month`); ids become native UUIDs (still `str` in Python).
   New tests in other lanes that insert those rows will break after the merge.
   Keep new tests DB-free where you can (mock the service/LLM) or build rows
   through one helper that 43 can repoint.
6. **Before you hand over, run the full test suite** (`pytest`, `compileall`,
   `npm run build`, `npm test`, lint = no new errors). While developing, run
   targeted tests only: three concurrent 13-minute full runs starve the machine
   and the Docker stack.
8. **Stay on script (the user's words, 2026-09-24: "high quality work and
   nothing goes off script").** Work only on your claimed tasks, exactly as
   docs/STANDALONE-TASKS.md defines them. Before you start, message
   tiqcollect-64 about any scope change, dropped sub-item, extra feature,
   drive-by refactor, new dependency or deviation from STANDALONE-PRODUCT-PLAN
   / DATA-MODEL-V2. Record it in the commit message too.
9. **Quality bar:** a task counts as done only when its **Done when** holds and
   the Definition of Done in STANDALONE-TASKS.md passes (its rule 2; board rule 6
   above). Tests must execute behaviour (not assert on source
   text), each non-obvious file gets a CHANGELOG header with the measured
   number, and any failing or skipped check is reported as such, never
   rounded up to green.
11. **Trap: `isolation: "worktree"` subagents start from `origin/main`**
   (`b01a9be`, the June standalone repo). It shares NO history with
   TIQCollect-app. Both of 43's background agents started there (found
   2026-09-24). Any isolated agent you launch must `git reset --hard c75053a`
   (or your branch head) before its first write, and must say so in its first
   commit. Subagents working inside your own worktree are not affected.
12. **No Twilio credentials anywhere a demo book runs.** A visit or SOS on the
   demo fixture texts the fixture borrower's number, and those numbers are
   valid-format Indian mobiles (`customers.csv`). `NotificationService` gates
   only on credentials being present (`notification_service.py:93,122`). The
   shared stack has TWILIO_* unset (verified 2026-09-24) and must stay that way:
   don't set them in any worktree `.env`, throwaway container or e2e run until
   B22's suppression has landed.
   **Trap (found by ce):** `backend/.env.example:94-102` holds
   `TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID}` and similar. `docker run --env-file`
   does NOT interpolate, so the container gets the literal, non-empty string
   `${TWILIO_ACCOUNT_SID}`. NotificationService then counts Twilio as configured
   and makes real HTTP calls to api.twilio.com. Any throwaway container built
   from `.env.example` must blank every TWILIO_* with `-e TWILIO_ACCOUNT_SID=`
   (and the rest), and print the blank count at the start of the run.
13. **Both Docker setups stay, and both keep working (the user's instruction,
   2026-09-24).**
   - **Local dev:** `docker-compose.yml` + `Dockerfile.dev`. Vite and uvicorn
     hot-reload over bind mounts, and a fresh clone runs with no `.env`.
   - **Production:** `Dockerfile`, a single container serving the built SPA and
     the API. The Collections platform builds it for fieldops.transorg.ai.
   - `docker-entrypoint.sh` is shared by both.
   - Nobody deletes, merges or rewrites either setup. A change to anything they
     consume (requirements.txt, package.json, vite.config.ts, the entrypoint,
     env names) must say in its commit how it affects dev AND prod.
   - Before handover, run `docker compose config -q`, and build the prod image
     under a lane tag (`docker build -t tiq-prod-check:<lane> .`), never the
     `fieldops-dev` tag and never the shared stack.
14a. **Targeted runs too (added 16:45):** any run using -n, or more than 10 test files, or planner/ML/routing tests, takes the lock. Smaller runs are serial and named <lane>-<purpose>-<HHMM>. Nothing runs beside a locked run (a 13 min suite took 40+ min under 3 concurrent containers).
14. **ONE full test suite at a time, machine-wide (added 13:35 on 2026-09-24,
   after the machine hit 100% CPU with 1 GB of 15.7 GB free and Docker plus the
   user's stack stopped responding).** Before any full `pytest` run, or any
   `docker build`, create `.claude/fullsuite.lock` containing
   `<session> <start time> <command>`. If the file already exists, wait. Delete
   it when the run finishes, pass or fail. Use at most `-n 4`. Never run tests
   inside the shared `fieldops_dev_api` container (`docker compose exec api
   pytest`). Targeted test files stay unrestricted.
10. **Proposed integration order (the user decides):** small, independent lanes
   first (ce: F01/I01/G06; d4: A10, then H14), then 43's P1 rebased onto them.
   43: rebase `standalone-p1` onto whatever has landed before you ask to merge.
   (This was rule 7; renumbered when 8–9 were added.)

## Audit findings on work already committed — open (coordinator, 2026-09-24)

Audit of c75053a (P0 simulator). Verified correct: the SSE token travels in a Bearer
header, never in a URL; every event goes to its manager's own channel (no global
channel); all 10 publish points come after the commit; publish never raises; the
heartbeat and GZip exemption are right; the claimed test counts exist.

| # | sev | where | problem | owner |
|---|---|---|---|---|
| P0-A1 | HIGH (latent) | e2e/simulator_acceptance.py:162 → visit_service.py:280, notification_service.py:93 | every run records a real visit, which would text a real-format borrower number (and the SOS step texts the manager). Only credential presence gates the send. Not firing today: the shared stack has no TWILIO_*. | 43 — pull B22's suppression forward (DEMO_MODE / is_demo) |
| P0-A2 | MED | lib/deviceLocation.ts:55, simulator go-to-stop | simulated GPS is indistinguishable server-side: the visit reads geo_verified=true and AgentLocation source=HEARTBEAT. It feeds fraud_service training labels and ML features, and the fixture is refreshed from this DB | 43 (needs a column/enum value: SIMULATED source) + frontend header |
| P0-A3 | MED | hooks/useLiveEvents.ts:33 | the stream singleton isn't keyed on the user. A manager who logs in within 5 s of another keeps the previous manager's channel for up to 600 s | frontend P0 follow-up |
| P0-A4 | MED | lib/eventStream.ts:171; simulator/EventTimeline.tsx:62 | a 403 is retried forever, and each retry writes a ROLE_VIOLATION_ATTEMPT audit row (~240/h if an agent is logged into the manager frame) | frontend P0 follow-up |
| P0-A5 | LOW | ManagerLiveMapPage.tsx:260; ManagerLayout | a full reload per location event: ~60 reads/min per tab against 4 before | frontend P0 follow-up |
| P0-A6 | LOW | lib/locationReporter.ts:60; RecordVisit drafts | the offline queue key isn't slot-namespaced, so a frame can upload another tab's queued fixes under its own token | frontend P0 follow-up |
| P0-A7 | LOW | deviceLocation.ts:55, sessionSlot.ts:58 | slot and simulated-GPS mode are not gated on SIMULATOR_ENABLED. Framing /?slot=x in prod switches off the real GPS sensor. No frame-ancestors header | frontend + backend header |
| P0-A8 | LOW | App.tsx:40 | the SimulatorPage lazy import is probably shipped in the prod bundle (unverified; needs a build) | frontend P0 follow-up |
| P0-A9 | LOW | endpoints/events.py:95-101 | CancelledError at pubsub.aclose() skips client.aclose() | P0 follow-up (backend) |
| P0-A10 | LOW | tests | the tenancy sweep doesn't cover events.py; nothing tests stream role/channel isolation, the publish points beyond check-in/location, or "failed write publishes nothing" | P0 follow-up |
| P0-A11 | LOW | repo rules | no CHANGELOG headers on the 8 touched files that carry them; CLAUDE.md feature #12 still says "no SSE anywhere"; event-type strings restated in 4 frontend maps | P0 follow-up + X01 |

Audit of d158d95 (fixture CSVs) + the planning docs in c75053a:

| # | sev | where | problem | owner |
|---|---|---|---|---|
| FX-1 | HIGH | fixtures/tables/users.csv, fieldops-demo.dump | all 21 bcrypt hashes crack to the documented Agent@123 / Manager@123 / Admin@123. The same dump, LoginPage and /manager-bridge ship on origin/COLLECTIONS, which Caddy routes to fieldops.transorg.ai, and command-center .env.example:32 uses manager1/Manager@123 as its service login. The live site probably accepts them (unverified) | USER / ops: rotate on the deployed DB. Code side: A10 (d4), A15 + B18 (43) |
| FX-2 | MED | fixtures/tables/*.csv | an undocumented second copy of the data. Nothing reads it, it has drifted from the dump, and its commit message is wrong. Not in B18's scrub or its grep | 43 (B18) |
| FX-3 | MED | customers.csv phone_primary | random real-format mobiles, and no outbound suppression exists (see rule 12) | 43 (B16/B18/B22) |
| FX-4 | MED | DATA-MODEL-V2 §9.2/§9.3/Q1/Q4, TASKS B15 | the transform still outputs "ABC Bank"/"ABC Collections", contradicting Appendix C and B18 | 43 |
| FX-5 | MED | TASKS D02, P2-E2E, E11 | dependencies scheduled after their dependents (UI02–04 in P3, F01 in P5). In practice they're being done now | docs, at integration |
| FX-6 | LOW | DATA-MODEL-V2 App. C | "Meridian Trust" is the brand of a real US credit union and Northfield Bank is a real US bank. meridiantrust.in / aravallifs.in are real domains | 43 proposes names, USER decides |
| FX-7 | LOW | CLAUDE.md | CC source is `Desktop/Transorg/CR dashboard/collections-platform/command-center/frontend` (not Desktop/Collections). This checkout's only remote is origin = transorg-engineering/TIQCollect | X01 at integration |

Audit of 43's P1 WIP (2d2ace7 plus the edits in progress when read), sent to 43. Items 1–5 are MERGE GATES:

| # | sev | where | problem |
|---|---|---|---|
| P1-W1 | HIGH (gate) | models/tenancy_listener.py:46-60 | quadratic, plus a SELECT per row. Measured 36.9 s against 1.0 s for 8k Visits; materialise adds ~200k rows |
| P1-W2 | HIGH (gate) | case.py:128 | cases.agency_id NOT NULL, fillable only from a placement, and nothing creates placements. Every production case creator (ingest_daily, demo_daily_feed, seed, materialise) fails. tests/_db default_tenant hides it |
| P1-W3 | HIGH (gate) | tenancy_listener.py:63-86, tests/_db.py:51-53 | the listener never checks mismatches (§2.5 says raise); SQLite FKs are off, so no composite-FK/tenant guarantee is tested before B19 |
| P1-W4 | HIGH (gate) | loan.py:183-185 | Loan.customer joins on (id, bank_id). On a mismatch it's silently None and build_features drops features. 4 SAWarnings on overlapping relationships |
| P1-W5 | MED (gate) | case.py:159, model_prediction.py:76 | FK cycles with no use_alter |
| P1-W6 | MED | core/database.py, entrypoint | search_path: ALTER DATABASE isn't carried by pg_restore without --create; tenancy-first breaks demo_service + alembic include_schemas; 7 scripts + alembic env skip the hook; 2 bare-SQLite create_all scripts break (coordinator's earlier ALTER DATABASE advice corrected) |
| P1-W7..W10 | MED | partitioned PKs (§7.2), deferrable self/cyclic FKs, SET NULL vs DETACH PARTITION, RESTRICT vs NO ACTION | DDL correctness before B11/B12 |
| P1-W11 | MED | materialise.py:151,170-175,350; simulator.py:~603 | strftime strings into DATE; unrounded committed_amount against NUMERIC(14,2) means Postgres/SQLite harness divergence |
| P1-W12 | MED | Loan(bank_name=…) ×6 callers; branch FK | constructor breaks; unknown branch now fails the insert instead of being quarantined |
| P1-W13 | MED | all routers | a malformed UUID gives DataError, so a 500. Needs one shared validator in core (43), used by every lane |
| P1-W14 | LOW | audit_log, users, CHECKs, indexes, commission_pct, Loan.bank joined | batch |

Finding on the live system (coordinator, measured 2026-09-24 on the shared DB):

| # | sev | where | problem | owner |
|---|---|---|---|---|
| ML-1 | HIGH | frontend (0 refs), backend (only materialise.py writes it), live DB | `borrower_disposition` is never recorded by the product: 0 of 2,404 visits and 0 of 1,089 call_logs. All 11,049 predictions served by recovery_risk **2.2.0** have `latest_disposition` = NONE and `disposition_recency_class` = NONE. The champion's strongest behavioural feature (IV 0.31) is therefore constant in production: served out of distribution, feeding every allocation since 2026-09-17. Effect on live Gini and calibration not measured | USER decision (record the stance on the visit/call forms, or roll back the champion). d4's H14 deliberately does not wire it |

## Integration checklist — user steps (collected from lanes)

- After merging F01 (ce): rebuild api / celery_worker / celery_beat images (needed only for LLM_PROVIDER=anthropic; groq keeps working unrebuilt thanks to the lazy import).
- After merging I01 (ce): `docker compose restart web`, or the dev web breaks on the missing vite-plugin-pwa.
- Open decision: backend/Dockerfile + backend/pyproject.toml are a stale third image (nothing references them; their deps lack openai/ortools/twilio). Delete or sync: the user decides.

Audit of ce's F01 (9204955), sent to ce. No HIGH findings. Handover gates:

| # | sev | where | problem |
|---|---|---|---|
| F01-1 | MED (gate) | llm.py:508-527 | our own request-building/parsing bugs are classed UPSTREAM_ERROR, then retried and sent to the fallback. Breaks the approved "never on invalid request" |
| F01-2 | MED (gate) | llm.py:286-289,327 | a missing anthropic SDK reads as MODEL_NOT_FOUND, triggers the fallback, and health says usable |
| F01-3 | MED (gate) | llm.py:211-213,814-831 | chat() marks a max_tokens-truncated tool call as runnable (partial args) |
| F01-4 | MED (gate) | tests | nothing proves the claimed 9→3 through the production constructors |
| F01-5 | LOW-MED (gate) | llm.py:265-272 | LLM_PROVIDER="none" is no longer a kill switch when a fallback is set |
| F01-6 | LOW (gate, rule 8) | llm.py:701 | unrecorded groq-path change: fence stripping; lost attempt= log field |
| F01-7..9 | MED/LOW | timeout for chat(); both-fail error masking; _cache_key can raise | should-fix |

Audit of d4's H14 (a4c834b), sent to d4. Gates:

| # | sev | where | problem |
|---|---|---|---|
| H14-1 | HIGH | visit_report_extraction.py:394-403 | negation before the verb is missed: "not going to pay Rs 5000 on Friday" gives a PTP. The conflicting RTP candidate is dropped silently |
| H14-2 | HIGH | :289 _DATE | "on 15th November" gives 2026-10-15 (month lost) |
| H14-3 | MED | :362 | the amount is taken from before the verb (overdue / already-collected figure) |
| H14-4 | MED | :213-216 | the evidence guard is bypassable ("." or evidence unrelated to the value) |
| H14-5 | MED | :241, agent.py:869 | no upper bound when the remaining target is 0/None; "inf" accepted |
| A03-in | MED | agent.py:476-498 `_get_accessible_case_or_404` | the pre-existing permissive rule (unassigned cases in any tenant, peer agents' cases, 403 vs 404) sent to 43 for A03. PaymentService.set_ptp:351 has the same `> 0` hole: sent to 43 |

Audit of 43's c9be844/b93c527/61fb026 (auth + B22), sent to 43. Gates:

| # | sev | where | problem |
|---|---|---|---|
| AU-1 | HIGH | notification_service.py:86 + 7 send sites | demo-tenant suppression is dead code (demo_tenant defaults False, never passed, is_demo never read); not fail-closed |
| AU-2 | HIGH, LIVE in main + Collections | agent.py:1043-1089 voice | /voice/outbound is unauthenticated, has no Twilio signature check, and dials the client-supplied PhoneTo on our Twilio number. Any agent login, or the published demo passwords, allows calls to any number worldwide. /voice/token not demo-gated |
| AU-3 | HIGH | A09 | no reset endpoint (claimed in auth_service.py:19-20) |
| AU-4 | HIGH | A05 | no list/revoke endpoints; revoke reasons never written: PARTIAL |
| AU-5 | gate | alembic | no migration for v2 schemas / user_sessions / agent_devices: login 500s on a real DB until B11 |
| AU-6 | MED | auth_service.py:239-265 | refresh race (no compare-and-swap) |
| AU-7 | MED | device binding | client-chosen device_id; DEMO_MODE default true disables binding and echoes OTP |
| AU-8 | MED | tests | no tests for A05/A09/quick-login |

Audit of ce's 651d0e3/249a277/5f89265, sent to ce. F01 items 1-7 and 9 closed; G06 clean. Gates:

| # | sev | where | problem |
|---|---|---|---|
| I01-1 | HIGH | frontend/README.md:15, registerServiceWorker.tsx:20 | preview on :5473 installs a scope-"/" SW over the dev origin; it serves the stale shell to dev and /simulator and is never evicted |
| I01-2 | MED | main.py:196-198 | no SW rollback (the catch-all answers /sw.js with index.html) |
| F01-8b | MED | llm.py:673 | the primary's failure is dropped when the fallback fails with a non-_FALLBACK_ON status |

Audit of 43's agents: UI02-05 (fc53ddc) + E02/E04 (daadc17), sent to 43. Gates:

| # | sev | where | problem |
|---|---|---|---|
| UI-1 | HIGH (latent, bites at A11) | LoginPage.tsx:36, ProtectedRoute.tsx:19-20, QuickLoginPage.tsx:28 vs App.tsx:69 | only RootRedirect knows bank roles, so bank users loop. Needs one homeFor(role) plus a routing test |
| UI-2 | MED | /bank/_gallery, BankTopBar "Live System" | fake KPIs/accounts/alerts reachable in prod; unmarked in overlays; "Derived live" is false |
| UI-3 | MED | BankSidebar, dialog/DrillPanel/WorkspaceModal, DataTable | hover-only flyouts; no focus trap/return; rows not keyboard-operable |
| MC-1 | MED | states.py:67, monte_carlo.py:1312-1316 | WRITTEN_OFF kept in stage-3 ECL (IFRS 9 derecognises), so it double-counts |
| MC-2 | MED | monte_carlo.py:1036-1041 | SUB→DOUBTFUL is a random hazard, not the 12-month rule labelled REGULATORY |
| MC-3 | MED | headline()/to_records() | assumptions + SYNTHETIC_WARNING dropped on the way out |
| E04 | PARTIAL | backtest.py | the "in CI on dev" half is missing |
| MC-L | LOW | states.py:77-83 | DPD_RANGE is an 8th copy of the DPD→bucket rule |

Found by the business lead (fb) on the walkthrough, verified by the coordinator in main (present since 68cee05, 2026-08-05, so also in the Collections copy):

| # | sev | where | problem | owner |
|---|---|---|---|---|
| PAY-1 | HIGH (pilot blocker) | RecordVisitPage.tsx:338,922-934,760,1846,2148 | once the UPI QR is shown, the screen flips to "Payment received ₹X" plus a chime after 10 s, in EVERY build (no demo gate). qrPaidDemo then waives the UPI reference, and the server accepts UPI with `upi_reference: Optional` (schemas/agent.py:115). Result: a UPI collection recorded with no UTR, and a false "received" shown to the borrower | proposed for the owner's hotfix (d4) + a server-side rule |
| PAY-2 | HIGH | RecordVisitPage.tsx:1825, config.py:163 | the QR pays a hardcoded `pa=8015935790@ptsbi`, `pn=ABC Bank` (DEMO_CONTACT_PHONE's VPA) for every tenant | A14 brand/VPA as data (43); hotfix interim |

**HOTFIX (owner-approved 2026-09-24 15:0x): `hotfix/live-security` from c75053a. Owner: d4.** Scope: A10 (cherry-pick dd2b11a), AU-2 voice (minimal v1: Twilio signature over PUBLIC_BASE_URL, server-resolved case, strict agent_id), PAY-1/PAY-2 (demo-only auto-received behind VITE_DEMO_UPI_AUTOCONFIRM; the server requires upi_reference for UPI; VPA from settings, no default). Flow: audit, merge into TIQCollect-app, then the Collections carry-over + push ONLY on the owner's explicit go-ahead at that moment.

More walkthrough flags from fb:
| # | sev | where | problem | owner |
|---|---|---|---|---|
| EVIDENCE-1 | HIGH | core/storage.py presign (public client region lookup to localhost:19000 from the container) + RecordVisitPage | photo upload URL 500 → the evidence is lost silently while the UI says "Visit Recorded". 0 of 2,404 visits have evidence | d4, after the hotfix |
| ALLOC-G | MED (data) | agents.gender NULL ×18 | 926 decisions BLOCKED "needs female agent" | 43 (B16/B18) |
| DATA-R | MED (data) | fixture visits | 792 out-of-hours visits flagged within_contact_hours; 400 over 100 m flagged geo_verified | 43 (B16/B18): derive the flags through product code |

**HOTFIX-2 (bb, OWNER-APPROVED 16:3x): `hotfix/live-security-2` from c75053a.** (a) rate limiter: uvicorn --forwarded-allow-ips pinned to the proxy network (today every user shares one login bucket behind Caddy; inferred from code); (b) OTP-1 HIGH: otp_service.py:323 returns the borrower OTP to the AGENT whenever DEMO_MODE (the platform .env sets DEMO_MODE=true, and compose defaults it to true). With the published demo passwords, anyone can post "borrower-verified" payments. Fix: flag `DEMO_OTP_ECHO` (default False), the same name as 43's p1 split.
Lock: ce stopped its superseded serial run at 15:3x. Next: d4's hotfix run, then bb's hotfix-2, then ce's final run, then 43.

**OWNER DECISION (2026-09-24): demo access = ONE master password, 3 accounts.** No auto-fill buttons. On v1 the three accounts are one each of AGENCY_ADMIN, AGENCY_MANAGER and FIELD_AGENT; on v2 (p1) they become bank user, agency manager and field agent. The password comes only from the private setting `DEMO_MASTER_PASSWORD` (accounts in `DEMO_MASTER_ACCOUNTS`) and is never in the repo, UI, logs or docs. It is applied idempotently on every boot; every other seeded user gets an unusable hash. The hotfix (d4) adds it as DEMO-LOGIN, plus PL-1 (a payment-link access check, missing on main).
43 rule-8 A03 changes approved: one scope rule (agency AND (assigned OR today's beat, IST calendar date)), uniform 404; set_ptp caps at total_outstanding when there's no target; the notify endpoints report their status truthfully; brand from the tenant.
43 re-audit of 0eb7df6 (tiqcollect-43, 2026-09-24): **DEVIATION (accepted by 64):** OTP verify returns a foreign OTP (issued by another agent) exactly like a missing one, i.e. the existing 400 "OTP not found or expired", not a 404. The case check before it IS the uniform 404. **INTERFACE CHANGE:** `scope.agent_case_or_404` lost its `sync_assignee=` kwarg (the read never mutates). Use `scope.sync_assignee(case, agent)` at the business commit instead. New frozen helper: `scope.today_beat_cases(db, agent)`. Voice identity is `agent_<user hex>_<sid hex>` (d4's format plus the session). **GAP, not built:** none. Boot now refuses when the alembic revision is not head (no auto-upgrade), via scripts/check_migrations. BL-5: 43's copy dropped; d4's is the one definition.

**HOTFIX-3 (ce): `hotfix/spa-containment` from c75053a.** SEC-1 HIGH: static file serving in the prod SPA catch-all (main.py) could read files outside the static root. Present since 526c2d6 (2026-08-06), so in every build merged into Collections. Confirmed on a local prod image only. Fix: real-path containment + tests. Merges with the other hotfixes after audit. The live-site check and credential rotation are with the owner/ops.

## OWNER DECISIONS (2026-09-24, ~16:50)
- **Lead dev (bb): GO Wave 1**, and D2–D12 accepted as recommended. D4 (/api/field-ops delete vs A15) and D10 (SMS on every visit) come back to the owner, as do BL-4 (GPS from login) and BL-5 (outstanding amount in SMS).
- **PILOT-FIRST:** PAUSE P4, P5 beyond F01, and P6 beyond H14. DROP UI06, E05, E08, F06, H06. ADD 9 buyer items (bb drafts N01–N09 in docs/PILOT-PLAN.md; fb validates). Target: a paid pilot (1 lender, 1 agency, 100 agents, dedicated India deployment). E02/E04 and E09 are PARKED (not merged). UI02–05 continues.
- **ML-1 = A + C.** A: a stance row replaces "Borrower Tone", pre-selected from the outcome, plus the call log (d4). C: measure the 2.2.0 degradation with the stance at NONE (ce).
- **P1 split:** ce + d4 take P1-A security tasks after their current work merges (43 proposes which).
- NOT chosen: the Sonnet default for helpers (not set); demo names stay open.

Audit of 43's B11 19e91ad (v2 Alembic baseline), sent to 43. Merge gates:
| # | sev | where | problem |
|---|---|---|---|
| B11-1 | HIGH | docker-entrypoint.sh:94, alembic.ini | v1 chain invisible: the v1 fixture restore then `upgrade head` makes the container exit, and alembic can't operate on the live v1 DB. Needs generation detection + alembic_v1.ini |
| B11-2 | HIGH (data loss) | docker-entrypoint.sh:69 | the public.agents probe never matches on v2, so the seed path drops and reseeds on every restart; the fixture path crash-loops |
| B11-3 | HIGH | core/database.py vs v2_0001-0003 | search_path/timezone never set at DB/role level; public ordered last |
| B11-4 | HIGH | v2_0001 ENUMS | no enum-drift test; new enum values fail only on Postgres at INSERT |
| B11-5..8 | MED | post-processor not committed; env.py compare flags; RESTRICT vs NO ACTION + deferrable; DETACH vs FKs from unpartitioned tables | |

## OWNER DECISIONS (2026-09-24, ~16:00)
- **Merges into TIQCollect-app: APPROVED when audited + green**, without asking each time. The coordinator performs them (--no-ff) in the order hotfix-3, hotfix-1, hotfix-2, UI02-05, ce, d4. The Collections carry-over and deploy still need a separate owner "go".
- F10 and F12 stay ACTIVE despite the P5 pause.
- DEMO_CONTACT_PHONE 8015935790 is the owner's team phone: keep.
- BL-5/D10: the post-visit SMS becomes a neutral text (no amount, no loan details) and is never sent after deceased/dispute/hostile outcomes. d4 on the hotfix; 43 in A14.
- D4: delete /api/field-ops/* IF no caller exists in the Collections code (bb checks); otherwise keep.
- BL-4: GPS from login KEPT (won't-fix).
- Demo names: a fictional "… Finance Ltd"; fb proposes screened options, the owner picks before B16.

Audit of hotfix-1 (461fee2..4dcd9dc), sent to d4. Merge gates:
| # | sev | problem |
|---|---|---|
| HF1-1 | HIGH (deploy-breaking) | the master-login script disables the Command Center service accounts (TIQCOLLECT_AGENCY_ACCOUNTS), so Collections /field/* returns 502. Needs a keep-list + deploy step |
| HF1-2 | HIGH | DEMO_MODE defaults true, so a real box that set the master password would disable every real user. Needs a second opt-in + domain/user-count guards |
| HF1-3 | MED | a shared password defeats four-eyes promotion. Refuse ML approve/promote while master mode is on |
| HF1-4/5 | MED | voice identity from From alone; ':' '-' in the identity. Need AccountSid/AppSid checks + HMAC CallTicket; agent_<hex> |
| HF1-6/7 | MED | DEMO-UPI accepted under DEMO_MODE; no server reference rule for NEFT/RTGS/DD/CHEQUE |

**ML-1 C RESULT (ce, measured, synthetic):** with the borrower stance at NONE (live reality: 0% coverage), recovery_risk 2.2.0 scores OOT Gini 0.4796 / KS 35.74 / Brier 0.18490, against 0.5122 / 38.66 / 0.18021 as trained (−6.4% Gini, −7.6% KS). Mean P(pay) is 4.1% lower, concentrated in early DPD (CURRENT/BUCKET_1 pushed riskier). The sanity gate reproduced the artifact's OOT exactly. Artifacts are in ce's scratchpad (mlc/work). Owner already chose A (capture the stance): d4 is building it.
**OWNER (16:1x): demo lender = "Girivan Finance Ltd".** Konkan Asset Recovery → rename (collides with a real agency; candidate "Sahyadri Field Recovery Pvt. Ltd.", being screened); second tenant "Northfield Small Finance Bank Ltd" → a fictional "… Finance Ltd" (fb proposing). The other 8 agencies passed. Checks are web + registry only, not legal clearance.
**FINAL DEMO ROSTER (fb 2939dc7 + e26dd90, owner-picked lender):** Kumaon agency "Sahyadri Recovery Desk LLP" → "Almora Recovery Desk LLP" (Nainital rejected: a real bank with its own recovery-agent list).   Girivan Finance Ltd (lender) · Kumaon Finance Ltd (second tenant, replaces Northfield Small Finance Bank) · Sahyadri Field Recovery Pvt. Ltd. (replaces Konkan Asset Recovery) · the other 8 agencies unchanged · emails on .test. Checks are web + MCA only; legal clears before anything external.

**B16-B18 (d4, b16-d4 @ b8f0a48 on TIQCollect-app e45978b, 2026-09-28; rebuilt so exactly one fixture blob exists).** `backend/fixtures/tables/*.csv` deleted in 6a91f2d (43's brief + FX-2/SEC-3; coordinator GO). **The 21 published-password bcrypt hashes and 8 hashed_refresh_token values REMAIN IN GIT HISTORY** (d158d95; history not rewritten). Mitigated: those passwords are published and retired by the master login on any box that sets DEMO_MASTER_PASSWORD, and the v2 fixture holds no usable hash. FX-1/FX-2/DATA-R/ALLOC-G code side: done in B16-B18 (see b8f0a48). Open for 43: "complaints table?" (complaints are disputes.kind=COMPLAINT + customers.complaints_raised for now).

**OPEN (coordinator, 2026-09-28): the demo fixture's git cost.** Every change to `backend/fixtures/fieldops-demo-v2.dump` adds ~50-60 MB to git history for good (B15 15.7 MB, B18 50.6 MB, P3 history follow-up next). Before the NEXT fixture change after P3's, decide: Git LFS for `*.dump`, or build-at-boot (generate the demo book in the seed container from the committed generator + the v1 dump, ~5 min). Batch fixture changes meanwhile. Owner: coordinator / d4.

**MILESTONE d4 (2026-09-28, owner stop).** Branch `p3-d4` (worktree C:\dev\tiq\p3) at **af1dce4**, based on TIQCollect-app e3610dc + a merge of 43's `b13b` (65beb44, v2_0013; NOT yet on TIQCollect-app).
- DONE: B16-B18 merged (e3610dc). P3: c189762 (DPD history for every loan, windowed instalments, origination-date fix), d83433d (C01 KPI catalog + C03 Overview), 329487b (C02 filter bar in the URL), af1dce4 (catalog on v2_0013 via AnalyticsDb; all 12 KPIs defined; pg Overview test: 30 passed with the fixture tests).
- OPEN: (1) the rebuilt fixture is UNCOMMITTED in the worktree (dump 52.7 MB, sha256 05a91e490968c7d6…, built and tested on v2_0013): it is p3-d4's one fixture commit, after b13b merges. (2) full tiq-verify on p3-d4 not run. (3) audit not run. (4) realism pack queued (owner-approved, after C03): plan to be sent first; not started.
- NEXT STEP: once b13b is on TIQCollect-app, re-merge (or rebase onto) it, commit the fixture (README + truth manifest + dump) as the single fixture commit, run tiq-verify, spawn tiq-auditor, send the coordinator the SHA. Scratch scripts: build_final_p3.sh (rebuild), pgfixture_p3.sh (pg tests); private DB container d4-b16-pg (stopped; `docker start d4-b16-pg`) holds the templates fieldops_b15base (v2_0013) and fieldops_final.
- No lock held. d4-* containers stopped.

15. **(added 2026-09-29)** Sync with `TIQCollect-app` every morning and before every handover:
    merge the latest integration head into your branch. At most ONE extra Docker stack
    machine-wide (besides the shared dev stack), treated like the test lock. Tear it down as
    soon as the proof is done.

16. **(added 2026-09-30, from a real incident)** After resolving a merge conflict, verify the
    file SURVIVED, not merely that conflict markers are gone. `grep -c '<<<<<<<'` returning 0
    is satisfied perfectly by an empty file. Check size or content: `wc -c`, or diff the
    resolved file against both sides. The incident: a Python heredoc doing `open(p,"w")`
    crashed on a UnicodeEncodeError (a `−`, U+2212) AFTER the open had already truncated the
    file; the retry then read the empty file, found no markers, and wrote the emptiness back.
    `docs/adr/README.md` — the entire ADR index — landed on `TIQCollect-app` as 0 bytes in
    merge 2bd334d and stayed there through two more commits. Corollaries: in Git Bash prefer
    `PYTHONUTF8=1` and explicit `encoding="utf-8"` for any file write; write to a temp file and
    rename rather than truncating in place; and before committing a merge, run
    `git ls-tree -r -l HEAD | awk '$4==0'` to catch any file that went to zero.
