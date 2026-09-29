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
| c1 | SeaweedFS (`chrislusf/seaweedfs`, built 2026-09-28) | Yes | Apache-2.0 | New compose blocks and an identities file instead of the MinIO root user; `minio-init` rewritten (no `mc anonymous`); DEPLOY.md backup and restore rewritten | A 724 MB image with more moving parts (master, volume, filer). Its bucket-policy support is partial. Pre-signed SigV4 is expected to work; the compatibility probe has not been run |
| c2 | Garage (`dxflrs/garage:v2.1.0`) | Yes, 39 MB | AGPL-3.0 | A config file, a one-time cluster layout, keys created by its CLI, region `garage`; `minio-init` replaced | No bucket policies: access is per key, which covers what we use. Lifecycle support is narrower than MinIO's, and retention rules would need checking. The probe has not been run |
| c3 | RustFS (`rustfs/rustfs`) | Yes | Apache-2.0 | MinIO-compatible by design | Young project; not a production choice yet |

## Recommendation

**b1: `pgsty/minio`, pinned to a release tag and its digest, and mirrored into a registry we
control.**
- **It is the only option that changes no code and no operations.** The same API, `mc`,
  healthcheck, init container and backup commands. Existing volumes should carry over, which
  must be verified before the dev stack switches.
- **The mirror** (GHCR under the org, or any private registry) means a vanished upstream costs
  us nothing: we keep what we pinned.
- **Keep c2 (Garage) or c1 (SeaweedFS) as the planned exit** if the fork stalls or its AGPL
  status becomes a concern. The app's S3 surface is small enough that the switch is mostly
  compose and ops work. Run the compatibility probe against both before we need it: the app's
  own storage seam doing a bucket, a pre-signed PUT and GET, stat, get, and anonymous denial.

**Risk accepted with b1:** a single community maintainer for the storage server. It is bounded
by the pin plus the mirror (nothing changes under us), by watching the fork's release cadence,
and by the tested exit above.

## If accepted

1. `MINIO_IMAGE` defaults to `pgsty/minio:RELEASE.2026-08-04T00-00-00Z@sha256:b6bfe723…` in
   `docker-compose.prod.yml` and `deploy/.env.prod.example`. `docker-compose.yml` (dev) takes
   the same image for its `minio` service, after the dev volume is checked on a copy.
2. Mirror the image to the org registry. DEPLOY.md names the mirror as the source.
3. Run the S3 compatibility probe for Garage and SeaweedFS once the machine has the memory,
   and record the result here.
