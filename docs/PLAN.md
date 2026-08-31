# Smart Nightly Case Sharing

## Summary
Build the last workbook feature: nightly case sharing that assigns tomorrow’s field work to the best eligible agent, keeps travel practical, and spreads work fairly. It will reuse the completed visit-priority and recovery scores. :codex-file-citation{path="C:/Users/TransOrg/Downloads/Field Recovery.xlsx" purpose="source" artifact_kind="workbook" sheet="Features — In Progress & Next" range="A10:I11"}

Current review findings:
- The existing allocator already ranks unassigned cases by visit priority and applies safety, language, specialisation, territory, and capacity rules.
- It cannot deliver this feature alone: the live database has zero unassigned cases, 593 open cases already assigned, and no beat for today.
- Routing infrastructure already exists: OSRM plus OR-Tools can sequence a beat after allocation.
- All 18 agent gender values are blank, while 31 customers require a female agent. Those cases must remain withheld until data is corrected.

## Implementation Changes
- Replace the one-purpose nightly allocator with a shared next-day planner. It runs at 8 PM Monday-Saturday and plans the next working day, with Saturday producing Monday’s plan.
- Include unassigned cases and unfinished open cases that are eligible for another field visit. Never alter an active, completed, or historical beat; only create or replace the next-day `PLANNED` beat.
- Select work in visit-priority order, then assign each case only to agents passing existing hard eligibility rules. Cases blocked by do-not-contact, female-agent requirements, no capacity, or no suitable agent receive an explicit exception reason.
- Score eligible agents transparently using recent collection performance, language/specialisation fit, distance from the agent’s operational starting point, available capacity, and tier/ranking. Capacity is a hard limit; among similarly suitable agents, choose the least-utilised agent to keep workloads balanced.
- Reuse the route optimizer to create each agent’s ordered case list, distance, duration, and visit-window-aware route. Use a valid current location when available; otherwise use the agent’s base location.
- Extract the current allocator into a `legacy` strategy and add a `smart` strategy. The nightly job reads a persisted allocation mode at runtime so operations can switch the next run back to legacy without a code rollback.
- Replace the manager’s separate reallocation scoring path with the shared planner policy. Remove the current fake “Apply Plan” behavior; any applied next-day reassignment must be a recorded planner decision.

## Data And Interfaces
- Add an allocation-run record and per-case allocation-decision records, linked to generated beats. Store strategy, plan date, previous/new agent, priority and fit explanations, deferred/blocked reason, and rollback state.
- Add a uniqueness constraint for one beat per agent per date after a migration preflight confirms the live database has no duplicates.
- Add manager APIs for the latest allocation run, per-agent workload/exception detail, changing strategy, and rolling back an untouched future plan.
- Add a manager “Tomorrow’s Allocation” view showing assigned/deferred/withheld cases, capacity by agent, expected recoverable value, route totals, and explainable assignment reasons.

## Test Plan
- Verify high visit-priority cases are selected before lower-priority cases while retaining hard safety blocks.
- Verify no female-only customer is assigned when agent gender is missing; show a data-configuration exception instead.
- Verify daily capacity, balanced relative workload, territory/travel preference, language and specialisation scoring, and fallback behavior.
- Verify reruns are idempotent, do not duplicate beats, and never change completed or in-progress beats.
- Verify Saturday plans Monday, Sunday has no planner run, and recovery scoring completes before allocation.
- Verify legacy mode, smart mode, rollback, decision audit records, manager API scoping, and route generation metrics.

## Assumptions
- Use next-day planning only: no mid-day ownership churn.
- Field work is Monday-Saturday.
- Agent gender is a required operational data cleanup before female-agent-required cases can be assigned.
- The first release remains a transparent rule-based planner, not a trained ML allocation model.
