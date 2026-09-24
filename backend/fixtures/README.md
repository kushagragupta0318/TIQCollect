# fixtures/

## `fieldops-demo.dump` — the demo book

A `pg_dump -Fc` of the field-ops database as it stood on the development box,
committed so that a fresh clone comes up with the book the demo actually shows
rather than the day-zero one `scripts/seed_data.py` generates.

`docker-entrypoint.sh` restores it on an empty database (then runs
`alembic upgrade head`, so migrations newer than the dump apply on top), and
falls back to the seed only when the file is absent or `SEED_FROM_FIXTURE=false`.
A database that already has `public.agents` is never touched either way.

| | |
|---|---|
| Taken | 2026-09-21 11:36 UTC, `pg_dump 16.14`, custom format, gzip |
| Source | `tiqcollect-product_pgdata16` (this repo's `docker compose` Postgres) |
| Schema | alembic `a1c3e5f7b9d2` (`add_leave_requests`), 24 tables, 476 columns, 28 enums |
| Contents | 18 agents · 1,378 customers · 1,478 loans · 1,698 cases · 2,400 visits · 1,036 payments · 663 PTPs · 142 allocation runs / 78,809 decisions · 34,241 model predictions · 4 leave requests · 57 agent locations |
| Not in it | MinIO objects (no visit in this book references a photo), Redis, `.env` |
| Size | 14.9 MB |
| sha256 | `1f452da44cb805d1…` (first 16; verify with `sha256sum`) |
| Logins | the demo master login — see below. *(This row read "as the seed: manager1 / Manager@123" until 2026-09-24; those seed passwords are published and are retired on any box that sets `DEMO_MASTER_PASSWORD`.)* |

### The demo master login (2026-09-24)

One password, three accounts — one per role v1 has. Set both in the API's
environment (e.g. `backend/.env`); `docker-entrypoint.sh` applies them on every
boot through `scripts/apply_demo_logins.py`:

```
DEMO_MASTER_ACCOUNTS=admin@tiqcollect.in,manager1@tiqcollect.in,agent002@tiqcollect.in
DEMO_MASTER_PASSWORD=          # ask the team; at least 16 characters
DEMO_MASTER_KEEP_ACCOUNTS=     # Command Center's service logins — see below
DEMO_MASTER_DISABLE_OTHERS=false
```

| Role | Account |
|---|---|
| AGENCY_ADMIN | `admin@tiqcollect.in` |
| AGENCY_MANAGER | `manager1@tiqcollect.in` |
| FIELD_AGENT | `agent002@tiqcollect.in` |

**Password: ask the team.** It is not in this repository, the UI, the logs or
any compose default, and it must not be. With it unset nothing changes, and the
seed's published passwords still work. That is why a shared or public box
must set it.

**Retiring the other accounts takes a second, separate switch.**
`DEMO_MASTER_DISABLE_OTHERS=true` makes every account that is neither a master
account nor on the keep-list unusable, which is what actually retires the
published passwords. It refuses and changes nothing if any such account's
email falls outside `DEMO_EMAIL_DOMAINS` (default `tiqcollect.in`), or if the
box has more users than the demo fixture's 21. *(This paragraph read "With it
set, every other account's password is made unusable" until the hotfix's
third round. `DEMO_MODE` cannot tell a demo box from a real one:
`.env.example` ships it `true` and the compose file defaults it on. So the
master password alone would have locked out every real user of any
deployment that set it, on every boot.)*

**Command Center's logins must keep working.** The platform's Command Center
signs in here as agency managers, using the passwords in its
`TIQCOLLECT_AGENCY_ACCOUNTS` (the platform's environment). If this script changes
one of those passwords, every `/field/*` page Command Center serves turns into a
502. Its `.env.example` uses `manager1@tiqcollect.in`, which is also the master
manager above and the account that holds the demo book. For each Command
Center account, do one of these:

- **It is a master account** (e.g. `manager1@`): in the same deploy, change its
  password in `TIQCOLLECT_AGENCY_ACCOUNTS` to the master password.
- **It is any other account**: list it in `DEMO_MASTER_KEEP_ACCOUNTS`. A kept
  account is never touched, and it may sit outside the demo domains.

An account cannot be both a master account and a kept account; the script
refuses that.

A deploy that retires the published passwords, in order:

1. Read `TIQCOLLECT_AGENCY_ACCOUNTS` from the platform's environment. Sort its
   accounts as above: master accounts get the master password there; every
   other account goes in `DEMO_MASTER_KEEP_ACCOUNTS`.
2. Set `DEMO_MASTER_ACCOUNTS`, `DEMO_MASTER_PASSWORD` and
   `DEMO_MASTER_DISABLE_OTHERS=true`, then restart the API. Restart Command
   Center too if step 1 changed its settings.
3. Check the API log for `[entrypoint] demo master login applied` and the
   line that follows it (set / unchanged / kept / retired counts). The
   entrypoint prints `NOT applied` on a refusal, with the reason just above it,
   and `not configured` when the password is unset. Neither stops the API.
4. Sign in to one Command Center `/field/*` page.

The script also refuses, and changes nothing, if `DEMO_MODE` is off, the
password is under 16 characters, or the three accounts are not one of each
role. While the master login is on, model approval and promotion answer 409:
one password that opens both an admin and a manager would let one person
supply both halves of the four-eyes check. A retired account's quick-login
links stop working as well.

**It is a snapshot, and it is date-anchored.** Plans exist up to 2026-09-22 and
every DPD is as of that date. Brought up weeks later it reads as a system that
paused; the nightly tasks and the 05:30 demo feed move it forward from the
first night, but the gap shows until they do. When that matters, refresh it.

### Refreshing

From a box whose database is the book you want to ship:

```bash
docker compose exec postgres pg_dump -U fieldops -d fieldops -Fc --no-owner --no-acl \
    -f /tmp/fieldops-demo.dump
docker compose cp postgres:/tmp/fieldops-demo.dump backend/fixtures/fieldops-demo.dump
```

Then update the table above — date, alembic revision (`SELECT version_num FROM
alembic_version`), counts, sha256 — and commit. The restore needs a `pg_restore`
at least as new as the `pg_dump` that wrote the file; the API image ships
Postgres 17 client tools.

### Why plain git and not LFS

15 MB once. The repo already commits ~400 model-artifact files under
`app/ml/artifacts/`; LFS would add a client requirement to every clone for one
file that changes rarely. Revisit if it is refreshed often enough to matter.
