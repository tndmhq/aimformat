---
date: 2026-10-01 21:32
type: decision
status: todo  # proposed — awaiting maintainer approval
related:
  - 2026-10-01_2132_decision_docx-roundtrip-identity-carrier.md
  - 2026-10-01_2132_decision_docx-roundtrip-marker-placement.md
  - 2026-10-01_2132_decision_docx-roundtrip-manifest.md
  - 2026-10-01_2132_decision_docx-roundtrip-marks-default-on.md
  - 2026-10-01_2132_decision_docx-roundtrip-import-revision-entry-point.md
  - 2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md
  - 2026-10-01_2132_decision_docx-roundtrip-id-alignment.md
  - 2026-10-01_2132_decision_docx-roundtrip-conversion-noise.md
  - 2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md
  - 2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md
  - 2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md
  - 2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md
  - 2026-10-01_2132_decision_docx-roundtrip-informative-convention.md
---

# Decision: ROUNDTRIP-D13 — architecture: id stamping plus reused emitters

**Status:** proposed — awaiting maintainer approval. Nothing is implemented yet. This entry is one of the
DOCX round-trip decision set (gap G3: chunk identity lost on
`.aim` → DOCX → edited in Word → DOCX → `.aim`). Sibling decisions:
[ROUNDTRIP-D1](2026-10-01_2132_decision_docx-roundtrip-identity-carrier.md), [ROUNDTRIP-D2](2026-10-01_2132_decision_docx-roundtrip-marker-placement.md), [ROUNDTRIP-D3](2026-10-01_2132_decision_docx-roundtrip-manifest.md), [ROUNDTRIP-D4](2026-10-01_2132_decision_docx-roundtrip-marks-default-on.md), [ROUNDTRIP-D5](2026-10-01_2132_decision_docx-roundtrip-import-revision-entry-point.md), [ROUNDTRIP-D6](2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md), [ROUNDTRIP-D7](2026-10-01_2132_decision_docx-roundtrip-id-alignment.md), [ROUNDTRIP-D8](2026-10-01_2132_decision_docx-roundtrip-conversion-noise.md), [ROUNDTRIP-D9](2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md), [ROUNDTRIP-D10a](2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md), [ROUNDTRIP-D10b](2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md), [ROUNDTRIP-D11](2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md), [ROUNDTRIP-D12](2026-10-01_2132_decision_docx-roundtrip-informative-convention.md). Baseline under test: aimformat 0.5.2 (`79a006b`).

## Shared context (G3)

Measured on `tests/fixtures/docxs/legal-addendum.docx` (81 chunks) with
**no** edits in between: 0 of 81 chunk ids survive `to_docx` → `from_docx`,
and `diff_documents(base, returned)` reports 81 added + 81 deleted, so an
agent has to re-read the whole document (5,804 `o200k_base` tokens for an
`aim_read`-shaped projection) to learn what a colleague changed. Text
survives 81/81 positionally; markup survives 69/81 (bold dropped on three
`num-1` headings, eight `<mark>` highlight colours dropped, a three-image
figure collapsed to one image). Two defects: identity loss, and conversion
noise that is not a colleague edit.

A scratch prototype (outside the repo) measured: bookmarks survive a
LibreOffice 25.8 re-save 81/81; a null round trip aligns 77 by marker +
4 by content with 0 spurious ops (12 noise units suppressed); a synthetic
10-edit set aligns exactly. **Not yet measured:** any Microsoft Word
behaviour, point bookmarks, the custom-XML manifest, the three-way merge,
`tracked`/`reject-all` exports with a non-empty lane, idempotent re-import,
split/merge linking, the fuzzy budget. A manual Word protocol (open a marked
export, perform a fixed list of edits with and without Track Changes, after
Document Inspector, and through a Google Docs round trip; record marker and
manifest survival; commit the saved files as fixtures) gates the
convention-freezing decisions.

## Context

The import has to turn "returned body" into either `direct_edit` events or
proposal cards. `reconcile._drive` already turns "current state into target
body" into correct, invertible `direct_edit` events and has been through many
review rounds. G4 needs a "current body into target body as proposals"
emitter. Writing a third emitter would duplicate the hardest invariants.

## Options considered

- A dedicated emitter inside the revision import.
- **Split into id stamping + reuse of existing/shared emitters:** chosen.

## Decision

- New module `revision_import.py`: the aligner ([ROUNDTRIP-D7](2026-10-01_2132_decision_docx-roundtrip-id-alignment.md)), noise and rebase
  ([ROUNDTRIP-D8](2026-10-01_2132_decision_docx-roundtrip-conversion-noise.md)), three-way merge ([ROUNDTRIP-D9](2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md)) and the report ([ROUNDTRIP-D5](2026-10-01_2132_decision_docx-roundtrip-import-revision-entry-point.md)). Its output is a
  target body A′ whose ids are the base's ids (fresh ids from `ids.new_id`
  against `_taken_ids()` for adds).
- Emission:
  - edits mode → `reconcile._drive(E=current body, A=A′)`, events with
    `origin: "reconcile"` plus `source`;
  - proposals mode → internal `_propose_body_diff(doc, A′, author, …)`,
    shared with G4 (reuse G4's if it lands first). It emits deletes,
    modifies, then adds and moves in document order, chains consecutive adds,
    and sets `depends_on` for split and merge ([ROUNDTRIP-D6](2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md)).
- Supporting changes: `export_docx.py` (marker + manifest writer,
  `roundtrip_marks`); `convert/_docx_seam.py` (`paragraph_markers`, manifest
  reader); `convert/_docx_in.py` (a `marks` list parallel to blocks, items
  and rows, dropped-block marker forwarding, internal
  `convert_docx_marked()`; `convert_docx` unchanged); `document.py`
  (`import_revision` wrapper); `cli.py`; `mcp.py`; docs (`docs/interop/`,
  CHANGELOG, `docs/for-agents.md`, the skill's colleague-loop recipe,
  `docs/knowledge/architecture.md`).
- A `plan` log entry precedes any code.

## Consequences

- Event and card invariants (invertibility, projected-lane validation, §5.4
  refusals) come from already-reviewed code; after every import `verify()`
  passes by construction, and tests assert it (plus `accept_all()` →
  `verify()` and `reject_all()` → body equals base).
- Sequencing: manual Word protocol → G4 merges → phases A + B together in one
  PR (about 4–5 days plus review) → phase C ([ROUNDTRIP-D10b](2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md)).

## Compatibility and migration

Internal structure only; the public surface is defined by [ROUNDTRIP-D4](2026-10-01_2132_decision_docx-roundtrip-marks-default-on.md) and [ROUNDTRIP-D5](2026-10-01_2132_decision_docx-roundtrip-import-revision-entry-point.md).
