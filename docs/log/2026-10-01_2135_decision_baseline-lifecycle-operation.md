---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_history-baseline-event.md, 2026-10-01_2135_decision_flatten-collapses-to-checkpoint.md, 2026-10-01_2135_decision_reconcile-adopts-as-baseline.md]
---

# Decision IMPORT-D11: a public `baseline()` lifecycle operation (SDK and CLI only, never MCP)

**Status: proposed — awaiting founder approval.** Implemented, uncommitted, on `wt/aim-import-tracked`; deviations are listed in the [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D11**. Related decisions: [IMPORT-D9](2026-10-01_2135_decision_history-baseline-event.md), [IMPORT-D12](2026-10-01_2135_decision_flatten-collapses-to-checkpoint.md), [IMPORT-D16](2026-10-01_2135_decision_reconcile-adopts-as-baseline.md).

## Context

§6.9 (IMPORT-D9) says importers SHOULD record a baseline; third-party importers
built on the SDK need a way to do so. Long-lived documents need a
history-compaction tool that keeps the file reconcilable. And a file whose history
cannot explain its body (e.g. flattened, then hand-edited — IMPORT-D12) needs a
documented recovery: "accept the file as the new starting point".

## Options considered

- Keep the operation private to the importers — leaves third-party importers and
  the recovery case without a tool.
- Names considered: `rebase_history`, `squash_history`, `compact`. `baseline`
  matches the event kind and says what the result is.
- Expose over MCP — rejected: the operation discards undo and provenance, so an
  agent must not run it unprompted.

## Decision

- `AimDocument.baseline(label: str, *, author: Actor | None = None,
  explanation: str | None = None, source: list[str] | None = None,
  at: str | None = None) -> Event`.
  - Replaces the retained log with one baseline for the current state, at
    `seq+1` (seq stays monotonic).
  - Pending proposals and caches stay.
  - Assets are garbage-collected as the final pass (§9.3).
  - Raises the declaration to at least 0.6, per §6.9 (the user asked for a
    baseline, so this is not a side-effect raise).
  - Refuses a body with a missing, duplicated or invalid unit id (review fix):
    the snapshot is reconcile's expected origin, and one carrying a broken id
    can never converge with the fixed-up actual body. It also refuses a log
    it cannot parse (H002) — not yet a recovery for a corrupt log; open
    question for review.
- CLI: `aim baseline FILE [--label L]`.
- Not an MCP tool. No new MCP tool takes a path.

## Consequences

- New public SDK/CLI surface; docs (`llms.txt`, skill, CLI reference) updated.
- **tndm editor:** follow-up — an "accept the file as it is" action that calls
  `baseline()` when desktop open meets a body its history cannot explain
  (IMPORT-D12).

## Compatibility and migration

Requires spec v0.6 (the event it writes). Running it on a v0.5 document raises
its declaration to 0.6, and the docstring and CLI help say so.

## Tests

Seq stays monotonic across `baseline()`; pending proposals survive; flatten →
hand edit → verify reports the mismatch and `baseline()` then recovers.

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
