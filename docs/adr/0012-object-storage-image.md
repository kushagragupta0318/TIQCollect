# 0012. Object storage after `minio/minio` stopped being published

**Status:** **Accepted by the owner, 2026-09-30.** Keep the pinned upstream `minio/minio`
digest we already hold, and mirror it to a free registry we control (ghcr.io under the
personal account) so Docker Hub removing it cannot break a rebuild. No fork, no hosted S3,
nothing billable.

This supersedes the recommendation this file carried while it was *Proposed* (the
`pgsty/minio` community fork). That analysis is kept below, because it is the evidence the
decision was taken against, and because the fork is still the first thing to reach for if the
accepted option's one real risk (below) ever bites.

> ### Mirrored and verified, 2026-09-30 — with one step still open
>
> The single-copy problem this ADR exists to fix is **fixed**: the image now lives in a second
> place we control, and the prod stack pulls it from there. What is still open is the package's
> **visibility**.
>
> ```
> local store   minio/minio@sha256:14cea493…              PRESENT, 241 MB (was the only copy)
> Docker Hub    docker manifest inspect                    NOT resolvable
> ghcr mirror   ghcr.io/kushagragupta0318/minio            PUSHED, digest sha256:a1a8bd4a…
> in use        tiq-localprod's minio container            running FROM the ghcr digest
> visibility    anonymous inspect, logged out of ghcr      PUBLIC (owner flipped it, verified)
> ```
>
> Before this, one `docker system prune -a` would have left both stacks unstartable with no
> recovery path, and the only local reference was the *floating* tag `minio/minio:latest`, so
> nothing on disk even recorded which release it was.

## Context

- **The image is gone.** Visit photos, signatures, recordings and agency documents live in
  MinIO, in both setups (`docker-compose.yml` and `docker-compose.prod.yml`). On 2026-09-28
  and 2026-09-29, `minio/minio` (Docker Hub, "pull access denied") and `quay.io/minio/minio`
  (401) could not be pulled. This laptop starts only because it has a cached copy, digest
  `sha256:14cea493…`, MinIO `RELEASE.2025-09-07T16-13-09Z`. A fresh clone on any other machine
  cannot start either stack.
- **The deployment target changed, and it changes the risk.** The owner is **not** deploying
  to a public domain yet; the target is a local production server on one machine, to get every
  feature working before anything is market-ready (`docs/LOCAL-PROD.md`). Nothing in this
  stack is internet-reachable today.
- **What the app asks of S3 is small.** Measured by grep of `app/` and `scripts/`:
  - `bucket_exists` and `make_bucket`;
  - pre-signed **PUT** (no POST policy and no conditions, so no size limit of its own) and
    pre-signed **GET**;
  - `stat_object`, `get_object`, and `put_object` (the demo documents).
  - No delete, no bucket policy, no lifecycle, no object lock, no versioning.
  - Deployment adds one bucket policy of its own: `mc anonymous set none` in `minio-init`.
  - Planned but not built: lifecycle rules for media retention, and `pg_dump`s of detached
    partitions written to the bucket (DATA-MODEL-V2).

## Decision

1. **`MINIO_IMAGE` is the pinned digest, served from our own mirror.**
   `ghcr.io/kushagragupta0318/minio@sha256:a1a8bd4ac40ad7881a245bab97323e18f971e4d4cba2c2007ec1bedd21cbaba2`
   (MinIO `RELEASE.2025-09-07T16-13-09Z`, the copy validated by the probe below; see the digest
   note above for why this is not the Hub digest). A digest pin means the mirror cannot serve
   us a different image than the one we tested, whoever controls the registry.
2. **The dev stack is pinned to the same digest.** `docker-compose.yml` used
   `minio/minio:latest` — a floating tag against a repository that no longer publishes. That
   works only until something prunes the local cache, and then the dev stack stops starting
   too, for the same reason prod would. One image, pinned, in both places (ADR 0001).
3. **Docker Hub stays as a documented fallback, not a default.** `MINIO_IMAGE` is an env var;
   pointing it back at `minio/minio@sha256:14cea493…` is a one-line change if the mirror is
   ever the thing that is down.

## The mirror

**Pushed 2026-09-30**, after the owner granted `write:packages` and chose a public package.

### The digest changed on push, and that is expected

```
pushed:  sha256:14cea493…  ->  sha256:a1a8bd4a…
docker:  "Not all multiplatform-content is present and only the available
          single-platform image was pushed"
```

The Docker Hub reference was a **multi-architecture manifest list**; this machine only ever
held the `linux/amd64` member of it, so the push produced a single-platform manifest, which
hashes differently. **The digest in the pins below is therefore the ghcr one
(`a1a8bd4a…`), not the Hub one** — pinning the Hub digest against ghcr would simply not
resolve. Checking this was the difference between a mirror and a mirror-shaped hole.

**The content is byte-identical**, which is the claim that actually matters:

| | Hub original (cached) | ghcr mirror |
|---|---|---|
| platform | amd64/linux | amd64/linux |
| layer count | 9 | 9 |
| sha256 of the layer diff-ID list | `e49b4a61091b…` | `e49b4a61091b…` |
| entrypoint / cmd | `docker-entrypoint.sh` / `minio` | identical |
| `minio --version` inside | — | `RELEASE.2025-09-07T16-13-09Z` |

The layer diff-ID list hashing the same means the filesystem is the same content; the manifest
digest differs only in its multi-arch wrapper. **Verified as the live path, not just in
theory:** after removing the Docker Hub override, `tiq-localprod`'s MinIO container came up
from `ghcr.io/kushagragupta0318/minio@sha256:a1a8bd4a…` and the stack passed its checks.

**One consequence: the mirror is amd64-only.** The multi-arch list was not reproduced, because
only that one platform existed locally. An arm64 host (Apple Silicon) cannot use this mirror
and would need its own push from an arm64 machine, or an upstream source that still serves the
list. Fine for this laptop and any x86 server; a genuine limitation to know before assuming it
is portable.

### Visibility: public, verified

The owner made the package public on 2026-09-30. Verified rather than taken on trust: after
`docker logout ghcr.io`, an anonymous `docker manifest inspect` of the pinned digest succeeds,
so a fresh host needs no credentials to pull it.

Public was chosen on cost. The free personal tier is 500 MB storage and 1 GB/month transfer,
nothing is charged until a cap is exceeded, and there is no per-push fee — but at 241 MB an
image clears 1 GB of transfer in four or five pulls, so a private package would have started
metering in normal use, against the owner's standing "nothing billable" rule. Public is
unmetered, and redistributing an *unmodified* AGPL-3.0 image is clean with attribution and an
upstream source pointer (`github.com/minio/minio`) in the package description.

Both compose files now point at the mirror: prod, and dev too, since a public package needs no
`docker login` on the machines other lanes run the dev stack on.

## Why this over the fork

- **No new maintainer to trust.** It is the same unmodified upstream binary already running
  here, at a digest the probe below validated against the app's own storage code. The fork
  (`pgsty/minio`) is one community maintainer; choosing it trades a stale-image problem for a
  supply-chain-trust problem, on the service that holds borrower evidence.
- **Free, and it stays free.** ghcr.io public packages are unmetered. Every hosted-S3 route
  (R2, S3, Backblaze) is billable and was ruled out by the owner.
- **Nothing to re-validate.** Same image, same `mc`, same healthcheck, same `minio-init`, same
  backup commands, same on-disk format — so no volume migration and no second probe run.

## The risk being accepted, and when it stops being acceptable

**The image is frozen at `RELEASE.2025-09-07` and will receive no security fixes.** It stores
borrower photos, signatures, recordings and documents. That is tolerable right now for exactly
one reason: this stack is **local and not internet-reachable**, so the MinIO API is reachable
only from the Docker network, and Caddy's `/minio/*` block (`deploy/Caddyfile`) keeps its admin
paths shut even from the host.

**Revisit before any public deployment.** At that point a frozen storage server behind a
public hostname is a real exposure, and the options are, in order of effort:

1. `pgsty/minio` — measured compatible below, a one-line `MINIO_IMAGE` change, rebuilt from
   current upstream source, and it publishes release tags.
2. Garage — also measured compatible below, 39 MB, actively maintained, needs
   `s3_region = "us-east-1"` in its config plus `minio-init` rewritten to per-key grants.

Both were probed on 2026-09-29 and both passed identically, so this is a *measured* exit, not
an assumed one. The decision above is cheap to reverse precisely because `MINIO_IMAGE` is one
env var.

## Options considered

Image availability was checked with `docker manifest inspect` and a pull on 2026-09-29.

| | Option | Available | Licence | Change to the product | Risk |
|---|---|---|---|---|---|
| **a** | **Keep the cached upstream image, pinned by digest, mirrored to a registry we control** — **ACCEPTED** | Only this laptop's cache until the mirror is pushed | AGPL-3.0 (unmodified binary) | None | **Frozen at 2025-09-07 with no security fixes.** Bounded today by the stack being local-only; see the section above |
| b1 | `pgsty/minio`, a community fork of MinIO (Pigsty) | Yes. Version tags, e.g. `RELEASE.2026-08-04T00-00-00Z`, UBI9-micro base, `mc` included | AGPL-3.0 | None: same binary, flags, env vars and `mc` | One community maintainer. The on-disk format should read upstream data; verify on a copy of the dev volume before switching |
| b2 | `cgr.dev/chainguard/minio` (rebuilt from source daily) | `:latest` only. Version tags need a paid subscription | AGPL-3.0 (MinIO), Chainguard's image terms | A floating tag in production, or a digest pin the free tier may not keep pullable. **Paid for pinning — ruled out** |
| c1 | SeaweedFS (`chrislusf/seaweedfs`) | Yes | Apache-2.0 | New compose blocks and an identities file; `minio-init` rewritten; DEPLOY.md backup/restore rewritten | A 724 MB image with more moving parts (master, volume, filer). Partial bucket-policy support |
| c2 | Garage (`dxflrs/garage:v2.1.0`) | Yes, 39 MB | AGPL-3.0 | A config file, a one-time cluster layout, keys via its CLI; `minio-init` replaced | No bucket policies: access is per key, which covers what we use. **Its S3 region must match the client** (see probe) |
| c3 | RustFS (`rustfs/rustfs`) | Yes | Apache-2.0 | MinIO-compatible by design | Young project; not a production choice yet |
| d | Hosted object storage (S3, R2, Backblaze) | Yes | — | `MINIO_*` settings repointed; no MinIO container | **Billable — ruled out by the owner** |

## S3 compatibility probe, 2026-09-29

The app's own `core/storage.py` — `ensure_bucket`, a pre-signed PUT then GET (byte
round-trip), `stat_object`, `get_object` — run against each candidate, plus two checks
`storage.py` doesn't itself make but the deployment relies on: an anonymous GET on the bucket
(must be refused; `minio-init` sets this) and a pre-signed URL with its signature tampered
(must be refused). Serial, one throwaway container at a time (low RAM that evening), on an
`--internal` Docker network so nothing reached the real internet.

| Server | bucket | presigned PUT | presigned GET (byte-identical) | stat | get | anon GET | anon list | tampered sig |
|---|---|---|---|---|---|---|---|---|
| **minio (cached, digest `14cea493…`) — the accepted image** | ok | 200 | 200, identical | ok | ok | **403** | **403** | **403** |
| `pgsty/minio` | ok | 200 | 200, identical | ok | ok | **403** | **403** | **403** |
| `cgr.dev/chainguard/minio` | ok | 200 | 200, identical | ok | ok | **403** | **403** | **403** |
| SeaweedFS | ok | 200 | 200, identical | ok | ok | **403** | **403** | **403** |
| Garage v2.1.0, default config (`s3_region = "garage"`) | — | — | — | — | — | — | — | `AuthorizationHeaderMalformed`: client sent scope `us-east-1`, server expected `garage` |
| Garage v2.1.0, `s3_region = "us-east-1"` | ok | 200 | 200, identical | ok | ok | **403** | **403** | **403** |

**Four of five pass identically out of the box.** Garage fails until its `s3_region` is set to
match the client (our storage code, via `minio-py`, never sets a region — it defaults to
`us-east-1`); with that one line changed, it passes the same as the others. Not a
compatibility gap, a one-time configuration step for `garage.toml`, recorded here so nobody
rediscovers it.

## Consequences

- `MINIO_IMAGE` points at the mirror digest in `docker-compose.prod.yml`,
  `deploy/.env.prod.example` and `docker-compose.yml`. Both setups run one pinned image.
- The mirror is pushed and is the live path for prod. **The package still needs flipping to
  public**; until then a fresh host must `docker login ghcr.io`, and the dev stack stays on the
  Hub digest for that reason.
- **The mirror is amd64-only**, so it does not serve an arm64 host.
- `docs/DEPLOY.md`'s "State today" row for this changes from "an owner decision, proposed" to
  the accepted decision plus the unpushed-mirror caveat.
- Nothing about the image changes, so no volume migration, no re-probe, and the dev stack's
  existing data is unaffected by the pin.
