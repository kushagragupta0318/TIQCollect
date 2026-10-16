# Proposed migration v2_0041 — seed `bank_feed.pii.read` (NOT APPLIED)

**Status: awaiting review. Deliberately NOT placed in `backend/alembic/versions/`,
so it cannot be picked up by `alembic upgrade head` before someone approves it.**

Phase 9 may not add migrations without explicit review (§9), so this is written
out rather than applied.

## Why it is needed

`backend/alembic/versions/v2_0008_permission_seed.py` seeds
`tenancy.permissions` and `tenancy.role_permissions` as **frozen literals**. Its
own header states the rule:

> "A migration must not change what it inserts when the registry later changes.
> A new or changed capability is a new revision, and
> tests/test_permission_seed.py fails the moment these literals and the registry
> disagree."

Phase 9 added `bank_feed.pii.read` to `app/core/permissions.py`, so
`tests/test_permission_seed.py::test_the_seed_equals_the_registry` now fails by
design. That is the drift detector working, not a defect.

## Why it does NOT block the pilot

**Authorization does not read these tables.** Measured on the live dev database,
whose mirror still holds the pre-Phase-9 81 permissions and 3 `bank_feed` grants:

```
BANK_ADMIN   -> True
BANK_TECHOPS -> False
SERVICE      -> False
```

`has_capability()` and `role_capabilities()` are pure functions over `_CATALOG`
in `app/core/permissions.py`. No runtime code queries `tenancy.permissions` or
`tenancy.role_permissions` for an authorization decision — `Permission` and
`RolePermission` are model definitions used for reporting and parity only
(verified by grep across `app/`).

So the enforcement built in Phase 9 is **live and correct right now**. The gap is
that the database's *reflection* of the registry is one capability behind.

## The change, for review

Head is currently `v2_0040`. The revision would insert exactly two rows,
idempotently, matching v2_0008's `ON CONFLICT DO NOTHING` style:

```python
"""Seed bank_feed.pii.read (Phase 9, RISK-1): raw borrower PII is a separate grant.

Revision ID: v2_0041
Revises: v2_0040
Create Date: 2026-10-16
"""
import sqlalchemy as sa
from alembic import op

revision = "v2_0041"
down_revision = "v2_0040"
branch_labels = None
depends_on = None

PERMISSION = (
    "bank_feed.pii.read",
    "bank_feed",
    "read a quarantined feed row as received (borrower PII); writes DATA_EXPORT",
    False,   # requires_second_person
    True,    # is_sensitive
)
GRANTS = (("BANK_ADMIN", "bank_feed.pii.read"),)


def upgrade() -> None:
    from sqlalchemy.dialects.postgresql import insert

    perms = sa.table("permissions", sa.column("code"), sa.column("category"),
                     sa.column("description"), sa.column("requires_second_person"),
                     sa.column("is_sensitive"), schema="tenancy")
    grants = sa.table("role_permissions", sa.column("role"),
                      sa.column("permission_code"), schema="tenancy")
    code, cat, desc, second, sensitive = PERMISSION
    op.get_bind().execute(insert(perms).values([
        dict(code=code, category=cat, description=desc,
             requires_second_person=second, is_sensitive=sensitive)
    ]).on_conflict_do_nothing())
    op.get_bind().execute(insert(grants).values([
        dict(role=r, permission_code=c) for r, c in GRANTS
    ]).on_conflict_do_nothing())


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("delete from tenancy.role_permissions "
                         "where permission_code = :c"), {"c": PERMISSION[0]})
    bind.execute(sa.text("delete from tenancy.permissions where code = :c"),
                 {"c": PERMISSION[0]})
```

## What else must change with it — MEASURED IN PHASE 10

Phase 9 hedged on this. Phase 10 measured it, and the answer is narrower than
feared.

**Exactly one test fails, and the two pinned counts do NOT need changing:**

```
$ python -m pytest tests/test_permission_seed.py -q
FAILED tests/test_permission_seed.py::test_the_seed_equals_the_registry
1 failed, 2 passed
```

| Assertion | Lives in | Reads | Impact of a separate v2_0041 |
|---|---|---|---|
| `len(codes) == ... == 81` | `test_the_seed_is_well_formed` | v2_0008's literals | **none** — v2_0008 still holds 81 |
| `len(ROLE_GRANTS) == ... == 156` | `test_the_seed_is_well_formed` | v2_0008's literals | **none** — v2_0008 still holds 156 |
| registry == seed | `test_the_seed_equals_the_registry` | v2_0008 only | **must read both revisions** |

Measured delta between the registry and v2_0008's literals:

```
seed PERMISSIONS : 81     registry caps   : 82
seed ROLE_GRANTS : 156    registry grants : 157
missing from seed: ['bank_feed.pii.read']
extra in seed    : []
```

One capability, one grant, nothing extra or contradictory.

### The exact test change required

`test_the_seed_equals_the_registry` compares the registry against the literals
of **one** revision. With the capability seeded by a separate revision, the
comparison must be against the **union** of both. The fixture loads a single
file today:

```python
PATH = ... / "alembic" / "versions" / "v2_0008_permission_seed.py"
```

It would load both and union `PERMISSIONS` / `ROLE_GRANTS` before comparing.
The per-row checks (category, description, second-person, sensitive) need no
change — they already iterate whatever rows the fixture supplies, and the
values v2_0041 would insert match the registry exactly (verified):

```
code       : bank_feed.pii.read
category   : bank_feed          <- matches Capability.category (derived)
sensitive  : True
2nd person : False
roles      : ['BANK_ADMIN']
```

**This is a change to how the test ASSEMBLES the expected set, not a weakening
of what it asserts.** The drift detector keeps full strength: any future
capability missing from every revision still fails it.

## Revision graph and deployment implications

Verified with Alembic itself, not by reading filenames:

```
$ alembic heads     -> v2_0040 (v2) (head)       # single head, linear
$ alembic current   -> v2_0040 (head)            # the DB is at head
```

34 revisions, one head. So `down_revision = "v2_0040"` is correct and creates
no branch. Deployment implications:

- **Insert-only and idempotent.** Two `INSERT ... ON CONFLICT DO NOTHING`
  statements. No DDL, no type change, no data rewrite, no lock of consequence.
- **Re-runnable.** An environment that somehow already has the rows is a no-op.
- **Reversible.** `downgrade()` deletes exactly those two rows, grants first.
- **No application behaviour changes when it runs.** Authorization reads the
  code registry, so the running app behaves identically before and after. The
  migration only brings the database's *reflection* into step.
- **Ordering is free.** It can be applied with the next ordinary deployment;
  nothing waits on it.

## Review recommendation

**Recommended: Option 1 — a new revision `v2_0041`,** with the fixture widened
to read both seed revisions.

Reasoning: v2_0008's header states the rule it exists to enforce — *"A
migration must not change what it inserts when the registry later changes. A
new or changed capability is a new revision."* The live database has already
run v2_0008, so editing its literals (Option 2) would leave this and every
other existing environment permanently missing the row, while new environments
received it. That is precisely the silent divergence the frozen-literal
discipline prevents. Option 2's only advantage is a smaller test diff, which is
not worth reintroducing the failure mode.

**Not recommended: Option 3 — leave it unseeded indefinitely.** Authorization
is correct without it (proven below), so this is survivable, but it leaves a
test failing by design, which erodes the signal: a real future drift would look
like the known failure.

### The exact approval required

One decision, from whoever owns schema changes on this repo:

> **Approve creating revision `v2_0041` (parent `v2_0040`) to seed the single
> capability `bank_feed.pii.read` and its single `BANK_ADMIN` grant into
> `tenancy.permissions` / `tenancy.role_permissions`, and widening
> `tests/test_permission_seed.py`'s fixture to read both seed revisions.**

| Field | Value |
|---|---|
| Approver (name, role) | ____________________ |
| Date | ____________________ |
| Option approved | ☐ 1 (new revision) ☐ 2 (edit v2_0008) ☐ 3 (defer) |
| Applied by / date | ____________________ |

Until that is signed, this file stays out of `backend/alembic/versions/` and is
not executable by `alembic upgrade head`.

## Phase 11 — union proof and approval checklist

**Status: PENDING APPROVAL.** No authorized decision-maker has signed this.

### The union was proven, not argued (Phase 11)

Phase 10 reasoned that the equality test must assemble its expected seed from
both revisions. Phase 11 ran it. The prototype loads v2_0008's frozen literals,
adds the exact rows v2_0041 would insert, and applies
`test_the_seed_equals_the_registry`'s three assertions **verbatim**:

```
── v2_0008 ALONE (what fails today) ──
  codes equal registry?  False
  grants equal registry? False
  missing: ['bank_feed.pii.read']

── v2_0008 UNION v2_0041 (the proposed fixture) ──
  codes equal registry?  True
  per-row field mismatches: none
  grants equal registry? True

── strength check: the detector still catches drift ──
  with a capability removed, codes equal registry? False (must be False)

RESULT: UNION SATISFIES THE UNWEAKENED ASSERTIONS
```

Two conclusions:

1. The union satisfies every assertion **as written** — no comparison is
   loosened, no row is exempted, and the per-row category/description/
   second-person/sensitive checks all match the registry.
2. The detector keeps full strength: removing any capability from the union
   still fails it. So this is a fix to how the expected set is **assembled**,
   not a weakening of what is **asserted**.

The prototype ran against the registry only. It touched no test file, no
migration directory and no database.

### Graph and containment, re-verified by Alembic itself

```
$ alembic heads    -> v2_0040 (v2) (head)     # single head
$ alembic current  -> v2_0040 (head)          # the database is at head
$ ls alembic/versions/ | grep -c v2_   -> 35
$ ls alembic/versions/ | grep -ci 0041 -> 0   # NOT in the active directory
```

### Migration approval checklist

For the decision-maker. Each row is already verified by engineering; the
column that matters is the last one.

| # | Item | Verified | Approver confirms |
|---|---|---|---|
| B1 | Parent is the single head `v2_0040`; creates no branch | ✅ Alembic | ☐ |
| B2 | Seed delta is exactly 1 capability + 1 grant, nothing extra | ✅ measured | ☐ |
| B3 | Literals match the registry exactly (category `bank_feed`, sensitive, not second-person, `BANK_ADMIN`) | ✅ measured | ☐ |
| B4 | Upgrade is insert-only `ON CONFLICT DO NOTHING` — idempotent, re-runnable | ✅ reviewed | ☐ |
| B5 | Downgrade removes exactly those two rows, grants first | ✅ reviewed | ☐ |
| B6 | No DDL, no enum change, no data rewrite, no significant lock | ✅ reviewed | ☐ |
| B7 | **No application behaviour change** — authorization reads the code registry | ✅ proven on the live un-synced DB | ☐ |
| B8 | Equality test fixture reads BOTH revisions; assertions unchanged | ✅ proven above | ☐ |
| B9 | Option 1 (new revision) chosen over editing v2_0008 | recommended | ☐ |
| B10 | May ride with any ordinary release; nothing waits on it | ✅ reviewed | ☐ |

### The approval

> **I approve creating Alembic revision `v2_0041` (parent `v2_0040`) to seed the
> capability `bank_feed.pii.read` and its single `BANK_ADMIN` grant into
> `tenancy.permissions` / `tenancy.role_permissions`, and widening
> `tests/test_permission_seed.py`'s fixture to assemble its expected seed from
> both seed revisions.**

| Field | Value |
|---|---|
| Approver (name) | ____________________ |
| Role / authority over schema changes | ____________________ |
| Date | ____________________ |
| Option approved | ☐ 1 new revision (recommended) ☐ 2 edit v2_0008 ☐ 3 defer |
| Applied by / date | ____________________ |
| Applied to environments | ____________________ |

**Nothing may be placed in `backend/alembic/versions/` or executed until the
table above is signed.**

## Until it is applied

- Authorization is correct and enforced (proven above).
- `tests/test_permission_seed.py::test_the_seed_equals_the_registry` fails. That
  failure is **expected and documented**; it must not be silenced by weakening
  the assertion.
- `tenancy.permissions` will not list the new capability, so any report or UI
  built from those tables will under-report it. No such surface exists today.
