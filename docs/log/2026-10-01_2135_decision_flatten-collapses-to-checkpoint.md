---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_history-baseline-event.md, 2026-10-01_2135_decision_baseline-lifecycle-operation.md]
---

# Decision IMPORT-D12: `flatten()` collapses history to one checkpoint instead of to nothing

**Status: proposed — awaiting founder approval.** Implemented, uncommitted, on `wt/aim-import-tracked`; deviations are listed in the [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D12**. Related decisions: [IMPORT-D9](2026-10-01_2135_decision_history-baseline-event.md), [IMPORT-D11](2026-10-01_2135_decision_baseline-lifecycle-operation.md).

## Context

Today `flatten()` drops all history, leaving an unrecorded non-empty origin.
Measured:

- flatten → SDK edit → `reconcile()` raises `HistoryError: … seq 1: cannot
  replace '…': not found`, because reconcile assumes the origin is empty whenever
  the first seq is 1.
- flatten → hand edit → `verify()` returns `[]`: the edit is undetectable.
- flatten → hand edit → reconcile (no SDK edit in between) *works* today: with no
  history, reconcile adopts everything as adds.

`aim flatten` is the verb for "give me a clean file to share", so it must not
raise the declared version as a side effect (§3.7).

## Options considered

- (a) An opaque (hash-only) baseline — needs 0.6 and version-bumps every
  flattened file; duplicates the v0.5 checkpoint-first pruned log.
- (b) A snapshot baseline — flatten would no longer mean "smallest file", and the
  version bump remains.
- (c) **A checkpoint at `seq+1` as the only retained event** — a pruned log,
  valid v0.5.
- (d) Leave flatten as is, with only the H009 warning.

## Decision

(c) when the document has history. A document with no history is left as today:
there is nothing to collapse, and reconcile adopts it as a hand-written file.
Embeddings are dropped and assets garbage-collected, as today. §6.8's "flatten
(drop history → clean file)" becomes "flatten (collapse history to one checkpoint
→ clean file)".

## Consequences

- A flattened file has one history line instead of none; lint reports H004
  (pruned log) instead of H001.
- Seq stays monotonic.
- `verify` detects later hand edits; reconcile refuses with the pruned-log
  message instead of crashing. The documented recovery is `baseline()`
  (IMPORT-D11).
- **tndm editor:** its store already tolerates seq moving backwards after an
  external flatten; after this change it sees that less often. Desktop open of a
  flattened-then-hand-edited file moves from a silent adopt-all (when no SDK edit
  came between) to a clear refusal; the editor needs an "accept the file as it
  is" action calling `baseline()` (follow-up).
- "Import then flatten" stays the most compact option (−32% to −57% raw tokens on
  the fixtures) and is honest about what it gives up.

## Compatibility and migration

No format version change: the result is valid v0.5. Ships with the G2 PR
(IMPORT-D14). Existing flattened files (no history) are unaffected and keep their
current behaviour.

## Tests

flatten → SDK edit → reconcile refuses cleanly (regression for the crash);
flatten → hand edit → verify reports the mismatch, then `baseline()` recovers; a
checkpoint-first flattened conformance fixture (ok); seq monotonic across
`flatten()`.

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
