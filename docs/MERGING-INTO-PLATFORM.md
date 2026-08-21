# Merging this repo back into the Collections platform

This repo was extracted from the `Collections` monorepo (`field-ops-stub/`) on
2026-08-17 with `git subtree split`. Work continues in both places, so the two
copies drift. This is how to fold this repo's work back into the platform.

Written 2026-08-21, after doing it once. Read the traps before running anything.

## State this assumes

| | |
|---|---|
| This repo | `github.com/sanyasirao-col/TIQCollect-product`, work on `TIQCollect-v1` |
| Platform | `github.com/transorg-engineering/Collections`, branch `docker-integrated` |
| Common ancestor | `field-ops-stub@bc8649c` in the Collections history |

A merge was completed once on 2026-08-21 and then **deliberately deleted**
without being pushed, so as of writing nothing from this repo has ever landed
in the platform. The base below is therefore still correct.

## Procedure

```bash
cd Collections
git fetch origin
git checkout -b merge-tiq origin/docker-integrated
git remote add tiq "../TIQCollect-product"     # or the GitHub URL
git fetch tiq

git diff bc8649c:field-ops-stub tiq/TIQCollect-v1^{tree} \
    -- . ':(exclude)CLAUDE.md' ':(exclude).gitignore' ':(exclude).github' \
    > /tmp/tiq.patch

git apply -3 --directory=field-ops-stub /tmp/tiq.patch
git diff --name-only --diff-filter=U          # conflicts to resolve by hand
```

`git apply -3` is a real three-way merge: it keeps the platform's own changes
wherever this repo did not touch the same lines, and leaves conflict markers
where both moved. After editing a conflicted file you must `git add` it — the
index keeps the unmerged stages until you do, even once the markers are gone.

## Traps

**1. The base is `bc8649c:field-ops-stub`, NOT the subtree split point.**
The first attempt used the split point (`tiqcollect-only`, `7c30095`) and
silently dropped a commit. `index.css` was unchanged between the split and this
repo's tip, so it never entered the patch — leaving the platform's older copy,
which had branched before that work existed. It compiled and looked fine, and
the loss was only caught by grepping for a known class name. Verify a couple of
known-changed files after every merge.

The base stays `bc8649c` only while no merge has landed. Once one does, the base
becomes whatever was merged last.

**2. `git subtree pull` does not work here.** The split was taken bare, without
a matching `git subtree add`, so no linkage is recorded and git refuses with
"unrelated histories". Use the 3-way apply.

**3. Exclude the standalone-repo artifacts.** `CLAUDE.md` states this code must
not be pushed to the org remote, which is false inside Collections. GitHub only
reads workflows at the repository root, so `.github/workflows/ci.yml` would be
inert at `field-ops-stub/.github/`. `field-ops-stub/.gitignore` deliberately
defers to the monorepo's root file. `docs/` is fine to bring across.

**4. Divergence costs.** At two weeks apart the merge was 61 files with only two
conflicts, both one-liners. It will be worse the longer the gap.

## After merging

- `python -m compileall app` in `field-ops-stub/backend`
- `npx tsc --noEmit` in `field-ops-stub/frontend`
- **Apply new migrations.** The platform database is built by `seed_data.py`
  (`drop_all`/`create_all`), which never stamps Alembic — so `alembic_version`
  may not exist and new tables silently will not appear. Check with
  `SELECT version_num FROM alembic_version;`, `alembic stamp <matching rev>` if
  missing, then `alembic upgrade head`.
- **Rebuild the image, do not just restart.** `docker compose up -d` recreates
  containers from the existing image and looks almost identical in the output.
  Confirm with `docker image inspect collections-field-ops --format '{{.Created}}'`
  — the container's own created time tells you nothing about the code inside.

## Related drift

`command-center/frontend/src/pages/FieldAnalytics.jsx` and `FieldCases.jsx` in
the platform are hand-maintained ports of `ManagerAnalyticsPage.tsx` and
`ManagerCasesPage.tsx` here. They are copy-paste, not imports, so nothing breaks
on merge — they just quietly fall further behind.
