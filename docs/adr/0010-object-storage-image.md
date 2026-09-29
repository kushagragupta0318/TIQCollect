# 0010. Object storage after `minio/minio` stopped being published

**Status:** Proposed, 2026-09-29, for the owner's decision.

## Context

- **The image is gone.** Visit photos, signatures, recordings and agency documents live in
  MinIO, in both setups (`docker-compose.yml` and `docker-compose.prod.yml`). On 2026-09-28
  and 2026-09-29, `minio/minio` (Docker Hub, "pull access denied") and `quay.io/minio/minio`
  (401) could not be pulled. This laptop starts only because it has a cached copy, digest
  `14cea493…`, MinIO `RELEASE.2025-09-07`. A fresh clone on any other machine cannot start
  either stack.
- **What the app asks of S3 is small.** Measured by grep of `app/` and `scripts/`:
  - `bucket_exists` and `make_bucket`;
  - pre-signed **PUT** (no POST policy and no conditions, so no size limit of its own) and
    pre-signed **GET**;
  - `stat_object`, `get_object`, and `put_object` (the demo documents).
  - No delete, no bucket policy, no lifecycle, no object lock, no versioning.
  - Deployment adds one bucket policy of its own: `mc anonymous set none` in `minio-init`.
  - Planned but not built: lifecycle rules for media retention, and `pg_dump`s of detached
    partitions written to the bucket (DATA-MODEL-V2).

## Options

Image availability was checked with `docker manifest inspect` and a pull on 2026-09-29.

| | Option | Available | Licence | Change to the product | Risk |
|---|---|---|---|---|---|
| a | Keep the cached upstream image, pinned by digest, and push it to a private registry we control | Only on this laptop until mirrored | AGPL-3.0 (unmodified binary) | None | **Frozen at 2025-09-07 with no security fixes**, on the service that holds borrower evidence. One laptop is the only copy until it is mirrored. `bitnamilegacy/minio` is the same idea: also frozen |
| b1 | **`pgsty/minio`**, a community fork of MinIO (Pigsty) | Yes. Version tags, e.g. `RELEASE.2026-08-04T00-00-00Z` (built 2026-08-04), UBI9-micro base, `mc` included | AGPL-3.0 | None: same binary, flags, env vars and `mc`, so `minio-init` and the healthcheck are unchanged | One community maintainer. If it stalls, we are back here. The on-disk format should read upstream data; to verify on a copy of the dev volume before switching the dev stack |
| b2 | `cgr.dev/chainguard/minio` (Chainguard, rebuilt from source daily) | `:latest` only (built 2026-09-28, `RELEASE.2026-09-22`). Version tags need a paid subscription | AGPL-3.0 (MinIO), Chainguard's image terms | None | A floating tag in production, or a digest pin the free tier may not keep pullable. Good as a reference, not as a pin |
| c1 | SeaweedFS (`chrislusf/seaweedfs`, built 2026-09-28) | Yes | Apache-2.0 | New compose blocks and an identities file instead of the MinIO root user; `minio-init` rewritten (no `mc anonymous`); DEPLOY.md backup and restore rewritten | A 724 MB image with more moving parts (master, volume, filer). Its bucket-policy support is partial |
| c2 | Garage (`dxflrs/garage:v2.1.0`) | Yes, 39 MB | AGPL-3.0 | A config file, a one-time cluster layout, keys created by its CLI; `minio-init` replaced | No bucket policies: access is per key, which covers what we use. Lifecycle support is narrower than MinIO's, and retention rules would need checking. **Its S3 region must be set to match the client** (see probe below) — Garage does not accept a mismatched region the way MinIO silently ignores one |
| c3 | RustFS (`rustfs/rustfs`) | Yes | Apache-2.0 | MinIO-compatible by design | Young project; not a production choice yet |

## S3 compatibility probe, 2026-09-29

The app's own `core/storage.py` — `ensure_bucket`, a pre-signed PUT then GET (byte
round-trip), `stat_object`, `get_object` — run against each candidate, plus two checks
`storage.py` doesn't itself make but the deployment relies on: an anonymous GET on the bucket
(must be refused; `minio-init` sets this) and a pre-signed URL with its signature tampered
(must be refused). Serial, one throwaway container at a time (low RAM that evening), on an
`--internal` Docker network so nothing reaches the real internet.

| Server | bucket | presigned PUT | presigned GET (byte-identical) | stat | get | anon GET | anon list | tampered sig |
|---|---|---|---|---|---|---|---|---|
| minio (cached, digest `14cea493…`) | ok | 200 | 200, identical | ok | ok | **403** | **403** | **403** |
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

## Recommendation

**b1: `pgsty/minio`, pinned to a release tag and its digest, and mirrored into a registry we
control.**
- **It is the only option that changes no code and no operations.** The same API, `mc`,
  healthcheck, init container and backup commands, and the probe above confirms the S3
  behaviour matches upstream MinIO byte-for-byte. Existing volumes should carry over, which
  must be verified before the dev stack switches.
- **The mirror** (GHCR under the org, or any private registry) means a vanished upstream costs
  us nothing: we keep what we pinned.
- **Keep c2 (Garage) as the planned exit.** It now passes the same probe as MinIO, is by far
  the smallest image (39 MB against MinIO's ~220 MB and SeaweedFS's 724 MB), and needs only
  `s3_region = "us-east-1"` in its config plus `minio-init` rewritten (no bucket-policy
  concept; access is granted per key instead — which is exactly what `mc anonymous set none`
  is for today, so the outcome is the same, denial by default). SeaweedFS (c1) also passed but
  is heavier and more moving parts for no compatibility gain measured here.

**Risk accepted with b1:** a single community maintainer for the storage server. It is bounded
by the pin plus the mirror (nothing changes under us), by watching the fork's release cadence,
and by the tested exit above — which is now a *measured*, not assumed, exit.

## If accepted

1. `MINIO_IMAGE` defaults to `pgsty/minio:RELEASE.2026-08-04T00-00-00Z@sha256:b6bfe723…` in
   `docker-compose.prod.yml` and `deploy/.env.prod.example`. `docker-compose.yml` (dev) takes
   the same image for its `minio` service, after the dev volume is checked on a copy.
2. Mirror the image to the org registry. DEPLOY.md names the mirror as the source.
