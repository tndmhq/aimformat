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
  - 2026-10-01_2132_decision_docx-roundtrip-id-alignment.md
  - 2026-10-01_2132_decision_docx-roundtrip-conversion-noise.md
  - 2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md
  - 2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md
  - 2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md
  - 2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md
  - 2026-10-01_2132_decision_docx-roundtrip-informative-convention.md
  - 2026-10-01_2132_decision_docx-roundtrip-architecture.md
---

# Decision: ROUNDTRIP-D6 — colleague changes land as proposals by default

**Status:** proposed — awaiting maintainer approval. Nothing is implemented yet. This entry is one of the
DOCX round-trip decision set (gap G3: chunk identity lost on
`.aim` → DOCX → edited in Word → DOCX → `.aim`). Sibling decisions:
[ROUNDTRIP-D1](2026-10-01_2132_decision_docx-roundtrip-identity-carrier.md), [ROUNDTRIP-D2](2026-10-01_2132_decision_docx-roundtrip-marker-placement.md), [ROUNDTRIP-D3](2026-10-01_2132_decision_docx-roundtrip-manifest.md), [ROUNDTRIP-D4](2026-10-01_2132_decision_docx-roundtrip-marks-default-on.md), [ROUNDTRIP-D5](2026-10-01_2132_decision_docx-roundtrip-import-revision-entry-point.md), [ROUNDTRIP-D7](2026-10-01_2132_decision_docx-roundtrip-id-alignment.md), [ROUNDTRIP-D8](2026-10-01_2132_decision_docx-roundtrip-conversion-noise.md), [ROUNDTRIP-D9](2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md), [ROUNDTRIP-D10a](2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md), [ROUNDTRIP-D10b](2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md), [ROUNDTRIP-D11](2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md), [ROUNDTRIP-D12](2026-10-01_2132_decision_docx-roundtrip-informative-convention.md), [ROUNDTRIP-D13](2026-10-01_2132_decision_docx-roundtrip-architecture.md). Baseline under test: aimformat 0.5.2 (`79a006b`).

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

A returned DOCX carries a colleague's edits. They can be applied directly
(history events) or offered for review (pending proposal cards). The
tracked-changes import (G4) maps Word revisions to proposals; this should be
consistent with it.

## Options considered

- **Direct edits by default:** the body changes immediately; review happens
  after the fact via history/undo.
- **Proposals by default, edits on request:** chosen. Review-friendly,
  attributable per change, reversible by reject, consistent with G4.
- Author as `external(...)` vs `human` actor: `human` with an explicit
  unverified prefix chosen (see below).

## Decision

- `changes="proposals"` by default; `changes="edits"` available. One batch
  per import.
- **Author:** `Actor("human", id="docx:" + cp:lastModifiedBy)`, the name
  trimmed and capped at 64 characters; `external("docx-import")` when the
  property is absent. The `docx:` prefix marks an unverified name taken from
  a file. The caller can override with `author=`. G4 uses the same helper for
  `w:author`.
- **Time:** `dcterms:modified`, clamped to [export time, now], otherwise now.
- **Explanations:** generated, standalone (Appendix B), at most 120
  characters, quoting at most 40 characters of payload text, naming the file
  by basename only, e.g.
  `docx:Rosa Lind in Word (returned.docx): "Authority" → "Scope of Authority"`.
- **Split and merge** are linked with `data-depends-on` (spec §5.4): a split
  is modify(first part) + add(rest), the add depends on the modify; a merge
  is modify(survivor) + delete(other), the delete depends on the modify.
  Their explanations name the operation. This prevents accept/reject
  combinations that duplicate or lose text.
- **Edits mode:** `direct_edit` events through the reconcile driver
  ([ROUNDTRIP-D13](2026-10-01_2132_decision_docx-roundtrip-architecture.md)) with `origin: "reconcile"`, `source: ["docx-sha256:<hash of the
  returned file>"]` (existing optional field), and the same author.

## Consequences

- History gains nothing until cards are resolved; then ordinary `resolution`
  events whose `proposed_by` is a `human` actor with a `docx:` id.
- The tndm editor's review and history UI must label `docx:` humans as
  unverified names from a file, never match them to accounts.
- `lastModifiedBy` names only the last person to save: silent edits by
  several people collapse to one author. Tracked revisions ([ROUNDTRIP-D10a](2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md)) keep
  per-revision authors.
- The colleague's name enters `.aim` history; it was already in the DOCX
  the user received.
- Proposal cards have no `source` attribute; adding one would be a format
  change and is left as a possible later improvement.

## Compatibility and migration

No grammar change: `human` actors, `data-depends-on`, `origin: "reconcile"`
and `source` all exist. Consumers that key human actors by account id must
not treat `docx:` ids as accounts.
