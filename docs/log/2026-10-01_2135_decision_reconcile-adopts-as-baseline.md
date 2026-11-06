---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_history-baseline-event.md, 2026-10-01_2135_decision_baseline-lifecycle-operation.md, 2026-10-01_2135_decision_import-versioning-and-sequencing.md]
---

# Decision IMPORT-D16: reconcile adopts a history-less file as one baseline when it declares ≥ 0.6

**Status: proposed — awaiting founder approval.** Implemented, uncommitted, on `wt/aim-import-tracked`; deviations are listed in the [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D16**. Related decisions: [IMPORT-D9](2026-10-01_2135_decision_history-baseline-event.md), [IMPORT-D11](2026-10-01_2135_decision_baseline-lifecycle-operation.md), [IMPORT-D14](2026-10-01_2135_decision_import-versioning-and-sequencing.md).

## Context

Adopting a hand-written file today writes one `add` event per construct — the same
2× blow-up as an import. Agents that write `.aim` HTML directly and then run
`aim reconcile` pay it. §3.7 forbids raising a declared version as a side effect.

## Options considered

- (a) Always adopt as a baseline — silently raises v0.5-declared hand-written
  files to 0.6.
- (b) **A baseline only when the declaration is already ≥ 0.6; N adds below
  that** — chosen.
- (c) Leave as is.

## Decision

(b). This follows §3.7 (never raise a declaration as a side effect); it is not a
compatibility shim. The event has `label: "adopt"` and an `external` author, and no
`origin` field, because a baseline is not an edit.

## Consequences

- History-less v0.6 files adopt into one history line; v0.5 files behave as today.
- **tndm editor:** desktop open of a history-less 0.6 file (after its pin bump)
  produces one baseline instead of N adds; undo after that adoption has nothing to
  undo.

## Compatibility and migration

Requires spec v0.6 (IMPORT-D9); separable from the rest of the G2 PR. No effect
on v0.5 documents; nothing is migrated.

## Tests

Reconcile of a history-less document under a 0.5 declaration (N adds, declaration
unchanged) and under a 0.6 declaration (one baseline, label `adopt`).

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
