---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_import-tracked-changes-as-proposals.md, 2026-10-01_2135_decision_import-batch-propose-and-validate.md, 2026-10-01_2135_decision_history-baseline-event.md, 2026-10-01_2135_decision_toc-on-import-and-freshness.md, 2026-10-01_2135_decision_reconcile-adopts-as-baseline.md]
---

# Decision IMPORT-D14: versioning and sequencing — tracked-change import as SDK 0.5.3, compact history + TOC as spec/SDK 0.6

**Status: proposed — awaiting founder approval.** Applied in the working tree as two planned PRs (uncommitted on `wt/aim-import-tracked`); see [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D14**. Related decisions: [IMPORT-D1](2026-10-01_2135_decision_import-tracked-changes-as-proposals.md), [IMPORT-D8](2026-10-01_2135_decision_import-batch-propose-and-validate.md), [IMPORT-D9](2026-10-01_2135_decision_history-baseline-event.md), [IMPORT-D13](2026-10-01_2135_decision_toc-on-import-and-freshness.md), [IMPORT-D16](2026-10-01_2135_decision_reconcile-adopts-as-baseline.md).

## Context

The tracked-change import fix (IMPORT-D1 … D8) is a data-loss fix with no format
change. The compact history and TOC work (IMPORT-D9 … D13, D16) changes the spec.
Bundling them would hold the data-loss fix behind a spec review and a downstream
pin bump. Once 0.6 is on PyPI, files written by it are rejected on upload by any
consumer still pinned to 0.5.x (S002, plus H003 for `baseline`) — the
released-package / pinned-consumer skew the project's pin rule warns about.

## Options considered

- One PR, one release (0.6) — delays the data-loss fix.
- **Two PRs: 0.5.3 first, then 0.6** — chosen.
- Ship 0.6 without coordinating the downstream pin — guarantees a window in which
  freshly imported files are rejected by the editor.

## Decision

- **G4 (IMPORT-D1 … D8) ships first as SDK 0.5.3.** No spec change.
- **G2 (IMPORT-D9 … D11, D13, D16) ships as spec and SDK 0.6:** registry
  `spec_version` → 0.6; `data-aim-css` → 0.6; spec status line and canonical
  aim-note version updated; Appendix A, examples, conformance fixtures and
  `ts/src/registry.data.ts` regenerated; parity goldens checked.
- IMPORT-D12 needs no version and rides the G2 PR. IMPORT-D15 is deferred.
- Two PRs from this worktree, each listing its own decision entries under
  "Decisions for review".
- No other in-flight work claims 0.6; read modes and batch operations are tool
  features with no spec change and can ship in either release.

## Consequences

- **tndm editor:** prepare its pin-bump PR against the 0.6 release candidate
  before the release (a sha-marked exception under its pin rule, retargeted to
  the tag once it exists) and land it the same day. Regenerate its test goldens that
  embed the stylesheet and the import-derived ones. Check the undo "nothing to
  undo" response path. Before the bump, run the contract smoke test: desktop open
  of an imported document after an out-of-band edit adopts the edit; the upload
  lint gate is green; undo after an import is handled.
- For 0.5.3 the editor sees cards on redlined uploads after its bump; no schema
  change.

## Compatibility and migration

0.5.3: no format change. 0.6: new spec version under §3.7's rules; every v0.5
file stays valid and unchanged; nothing is migrated; files that use 0.6 markup
declare it.

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
