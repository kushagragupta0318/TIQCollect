# Claude usage policy for TIQCollect sessions (from the owner, 2026-09-24)

Goal: the best result for the fewest tokens. Opus and extra-high effort are NOT banned. Use
them where they change the outcome, and use cheaper tools everywhere else. Quality gates stay
exactly as they are.

## 1. Pick the model and effort per piece of work

| Work | Model / effort |
|---|---|
| Security or money design (auth, tenancy, payments, OTP, voice, migrations, data model), architecture, a hard bug whose cause is unclear, judging a HIGH audit finding | Opus, xhigh |
| Normal implementation in a complex module; reviewing your own diff before handover | Opus, high |
| Well-specified change, test writing, mechanical refactor, fixture fallout, docs, re-verifying a fix | subagent `model: "sonnet"` (or `tiq-auditor` for review) |
| Search, counting, listing, grepping logs, "where is X / who calls Y" | subagent `model: "haiku"` (or `tiq-explorer`) |

The owner does NOT want to switch models by hand. Optimise yourself: keep your own session on its
set model, and push mechanical work into subagents with the right model (that's the automatic
lever). Ask the owner for a switch only when MORE than an hour of purely mechanical work is ahead
of you with nothing to delegate. When a task ends and your context is very large, say once "good
point for `/compact`".

## 2. Keep context small (every turn re-reads it)

- Read line ranges, not whole files. Grep with `-n` and a little context instead of opening
  large files. Run `git show --stat` before any diff, and diff only the paths you need.
- Don't re-read CLAUDE.md (about 40k tokens). Look up the one section you need.
- Cap tool output: `| head`, `| tail -40`, `-q`, `--shortstat`. Never dump full logs, lockfiles
  or test output. Keep only the failures.
- Send broad searches to `tiq-explorer`, so the file dumps stay in its context, not yours.
- Subagent briefs name exact files and lines, what to decide and an output budget ("max 30
  lines"). Ask for findings, not narration.

## 3. Don't pay twice for the same work

- Clear `tiq-self-review` before "ready for audit". An audit round costs 30-60 min for two sessions.
- Verify with `tiq-verify`: targeted tests while working, ONE full run (-n 4, under the lock)
  per batch of fixes. Never re-run a suite that already passed on the same SHA. Never run a
  suite that a pending fix will supersede.
- Batch related fixes into one commit and one verification run.
- Make independent tool calls in parallel in one message. Run long jobs in the background and
  wait for the notification; don't poll.
- Don't re-derive what the board or an audit already established. Cite the id (AU-2, H14-1…).

## 4. Talk less, say more

- Messages to other sessions and the coordinator: the result first, then file:line, numbers and
  what's left. No restating the plan, no pleasantries, no repeated acknowledgements.
- Replies to the owner: short and plain, decisions needed at the end.

## 5. Never trade away

Tests that execute behaviour, the full DoD before handover, honest reporting of failures, and
Opus for security, tenancy, payments and data-model decisions. Saving tokens never justifies
skipping a check.
