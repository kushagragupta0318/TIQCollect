# 0006. Comments say why; history lives in git and ADRs

**Status:** Accepted by the owner, 2026-09-24 (RESTRUCTURE-PLAN D2). It replaces the
CLAUDE.md convention of `# ─── CHANGELOG (prototype → product) ───` file headers and "this used
to say…" paragraphs.

## Context

- **Prose volume.** 31.3% of `backend/app` lines were comments or docstrings (8,116 + 4,250 of
  39,508). 392 comment lines carried a date; 81 files had CHANGELOG headers (1,936 lines).
- **Where the prose sat.** Most of it was in function bodies: `core/config.py` was 64.8%
  prose, `global_allocator.py` 49.2%.
- **CLAUDE.md.** It was 2,702 lines and every session loaded it. A 27-claim spot-check found 8
  false and 4 stale; 28 of its parentheticals corrected itself.
- **The history is valuable.** It records why numbers are what they are, usually a
  measurement. But inline, it goes stale and costs every reader.

## Decision

- **A comment says *why*, next to the code, in about three lines or fewer.** A measured number
  that justifies a constant stays in that comment, with its date.
- **No new CHANGELOG headers or "this used to say" paragraphs.** The history of a change goes
  in its **commit message**, including what was measured and what was not run. A decision with
  lasting consequences gets an **ADR** here.
- **Existing headers are migrated, not deleted in bulk.** When a file is restructured, its
  still-relevant decisions move to an ADR and the header goes in the same commit.
- **CLAUDE.md is an operational guide of about 300 lines or fewer.** It holds what the product
  is, how to run and verify it, where rules live, and pointers. Nothing in it should be true
  for only a week. Numbers that drift (line counts, test counts) are measured by a command, not
  written down.
- **Generated reports stay generated.** `MODEL_DEVELOPMENT.html` is built from the artifact.

## Consequences

- Reviews check that comments explain *why* and that the commit message carries the evidence.
- `git log -- <file>` and `docs/adr/` are where the history is. The pre-2026-09-24 CLAUDE.md
  is retrievable with `git show 495e4c6:CLAUDE.md` (or any earlier commit).
