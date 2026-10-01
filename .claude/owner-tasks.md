# Owner task ledger (coordinator-only)

NOT the product backlog (that's docs/STANDALONE-TASKS.md). This is every task the OWNER
gave the coordinator directly. Rule: if it's done immediately, file it here as DONE the same
turn. If it can't be done immediately, it goes here as OPEN so it is never lost. Review this
list every few turns and before every "what's left" answer to the owner.

Status: ✅ done · 🔄 in progress (lane) · ⏳ open/queued · 🧍 needs owner

## Done
- ✅ Restart 9 lanes for max-parallel development (2026-09-30)
- ✅ Add Kavya Reddy as 4th demo master login (four-eyes demo)
- ✅ MinIO: keep pinned digest + free public ghcr mirror, no paid service
- ✅ Repo public → private (disclosure) → public again; v1 dump removed from tree first
- ✅ Widen CI to run on lane branches
- ✅ Merge view-perf (v2_0017) — fixes bank/overview P0 (500→200)
- ✅ AI decision: build showcase (models/simulator), skip agent platform (F02-F06)
- ✅ P4 correction: build+demo Monte Carlo/ML on synthetic; never CLAIM real performance
- ✅ Add C08 Customer 360, C09 audit trails; split K01
- ✅ docs/ONBOARDING.md (dev handoff + Cloudflare-tunnel demo delivery + PWA install)
- ✅ docs/MOBILE-APP-PLAN.md (Capacitor route, prerequisites, honest limits)
- ✅ Explain the 4173/5473 port confusion (app is on 5473; prod behind Caddy at tiq.localhost)

## In progress
- 🔄 Payment double-count fix — CRITICAL money bug (L7 / l7-payment-idempotency)
- 🔄 Agent-can't-get-password-on-prescribed-config deploy blocker (L4)
- 🔄 Placement-engine cross-tenant id leak (L8)
- 🔄 PII redaction + prompt fencing across 7 LLM sites (L3 / l3-redaction — CI)
- 🔄 Deployment robustness: reboot survival, restore, upgrade-over-populated-DB (L4)
- 🔄 Fixture rebuild: performing book + seasonality → fills empty dashboard KPIs (L6)
- 🔄 Monte Carlo E01/E02 + endpoint on synthetic data (L2)
- 🔄 AI Models showcase page + per-account explanation (L5)
- 🔄 Customer 360 (C08) after Models page (L5)

## Open / queued
- 🔄 Realistic demo: multiple onboarded agencies + admins/managers/agents, all logging in
  via the shared demo password, plus docs/DEMO-LOGINS.md (organised by agency). (L6, in rebuild)
- ⏳ Mobile-app perf optimisation: bank view "not optimised at all", agent view worse than
  manager (owner saw in F12 devtools). NOT YET ASSIGNED — needs a perf pass on bank + agent.
- ⏳ Restore the rebuilt demo book into the DEV DB so owner sees KPIs on :5473 (after L6)
- ⏳ Groq key: owner has it, will insert on my signal — HELD until redaction CI green
- ⏳ Payment REVERSAL path (PaymentStatus.REVERSED has no writer) — product decision, flagged
- ⏳ Keep dev-team/download docs current (ongoing — ONBOARDING is the entry point)

## Needs owner
- 🧍 Keep one authoritative off-repo copy of the v1 demo dump (fieldops-demo.dump) so v2 can always be rebuilt from v1 — it's removed from the tree; decide where the master copy lives.

## History-purge note
Repo public with demo-password hashes (Agent@123 etc.) still in git HISTORY. Owner accepted.
v1 dump removed from HEAD. Full history rewrite deferred (would break 9 in-flight branches);
revisit only if owner wants it. Rotate those demo passwords on any real deployment.
