# 0009. TIQCollect is a standalone product; PRODUCT_MODE is removed

**Status:** Accepted by the owner, 2026-09-28 (relayed by the coordinator, tiqcollect-64).

## Context

- **How it ran until now.** This repo was built and deployed inside the Collections platform
  monorepo. Its tree was 3-way patched into `field-ops-stub/` and served at
  `fieldops.transorg.ai`, with Command Center calling `/api/v1/manager/*` through service
  logins. `docs/MERGING-INTO-PLATFORM.md` described that path.
- **What the standalone plan added.** `PRODUCT_MODE = standalone | embedded` (task A15),
  meant to switch on the bank portal only in standalone deployments.
- **Measured on 2026-09-28.** `settings.PRODUCT_MODE` was read nowhere: not in `backend/app`,
  `scripts` or `frontend`. The bank portal was served in both modes. Only its four validation
  tests referred to it.

## Decision

- **TIQCollect is a standalone product.** Build, test, release and deploy all end in this repo.
  Nothing is merged into the Collections repo any more. The deployment guide is
  [docs/DEPLOY.md](../DEPLOY.md).
- **`PRODUCT_MODE` is deleted,** together with its four validation tests. With no embedded
  mode left, a switch with one meaningful value is dead configuration that invites someone to
  wire behaviour back onto it. Old env files that still set it are ignored
  (`extra="ignore"`).
- **`docs/MERGING-INTO-PLATFORM.md` is kept as a historical record,** with a banner saying so.
  This includes its Command Center parts: `TIQCOLLECT_AGENCY_ACCOUNTS` and the service-login
  rotation.

## Consequences

- The `SERVICE` role and the `service.manager_api.read` capability still exist. They were
  built for Command Center, and they are neither removed nor wired here. Whether a standalone
  product keeps a machine-to-machine manager API is a separate product decision.
- The v1 deploy and the P1 checklist for `fieldops.transorg.ai` remain valid for that host
  only, for as long as it runs. New deployments follow `docs/DEPLOY.md`.
