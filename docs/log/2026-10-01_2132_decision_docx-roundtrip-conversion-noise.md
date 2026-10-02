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
  - 2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md
  - 2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md
  - 2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md
  - 2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md
  - 2026-10-01_2132_decision_docx-roundtrip-informative-convention.md
  - 2026-10-01_2132_decision_docx-roundtrip-architecture.md
---

# Decision: ROUNDTRIP-D8 — conversion noise is never reported; base markup is kept

**Status:** proposed — awaiting maintainer approval. Nothing is implemented yet. This entry is one of the
DOCX round-trip decision set (gap G3: chunk identity lost on
`.aim` → DOCX → edited in Word → DOCX → `.aim`). Sibling decisions:
[ROUNDTRIP-D1](2026-10-01_2132_decision_docx-roundtrip-identity-carrier.md), [ROUNDTRIP-D2](2026-10-01_2132_decision_docx-roundtrip-marker-placement.md), [ROUNDTRIP-D3](2026-10-01_2132_decision_docx-roundtrip-manifest.md), [ROUNDTRIP-D4](2026-10-01_2132_decision_docx-roundtrip-marks-default-on.md), [ROUNDTRIP-D5](2026-10-01_2132_decision_docx-roundtrip-import-revision-entry-point.md), [ROUNDTRIP-D6](2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md), [ROUNDTRIP-D7](2026-10-01_2132_decision_docx-roundtrip-id-alignment.md), [ROUNDTRIP-D9](2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md), [ROUNDTRIP-D10a](2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md), [ROUNDTRIP-D10b](2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md), [ROUNDTRIP-D11](2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md), [ROUNDTRIP-D12](2026-10-01_2132_decision_docx-roundtrip-informative-convention.md), [ROUNDTRIP-D13](2026-10-01_2132_decision_docx-roundtrip-architecture.md). Baseline under test: aimformat 0.5.2 (`79a006b`).

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

Even with identity fixed, 12 of 81 units come back changed when nobody
edited them (importer defects in the F-series backlog), plus one more after
a LibreOffice re-save (adjacent identical runs merged). These must not reach
the agent or reviewer as colleague edits, and a colleague's real edit must
not strip markup DOCX could not show them (bold headings, highlight colours,
a three-image figure that came back as one image).

## Options considered

- **Store N = import(export(X)) hashes in the DOCX at export time:**
  rejected. A file exported with SDK version k and imported with k+1 would
  read every importer fix in between as a colleague edit; the open F2–F12
  items are exactly such fixes. Also costs ~1 s per export.
- **Compute N at import time with the running exporter and importer:**
  chosen.
- **Take the returned unit as the payload verbatim:** loses base markup on
  every edited unit.

## Decision

- **Unchanged test:** a returned unit R_u is unchanged when it is
  run-normalised-equal to N_u, where N = import(export(X)) computed now.
  Run normalisation merges adjacent inline siblings with equal tag and
  attributes and adjacent text nodes; text compares after NFC and whitespace
  collapsing.
- **Text rebase:** if N → R is text-only (same inline skeleton with text
  blanked) and base unit B has the same normalised text as N, the character
  edits are replayed onto B's text nodes. Measured:
  `<h1><strong>Authority</strong></h1>` + "Scope of " gives
  `<h1><strong>Scope of Authority</strong></h1>`.
- **Fallback for non-lossy units:** payload R, with B's root class tokens
  and whitelisted inline-style properties that N also lacks restored, if the
  result still validates; otherwise without the restoration.
- **Lossy units** (N differs from X beyond droppable inline formatting:
  block children, images, element kinds, geometry, slide content): applied
  only via the text rebase; otherwise a `Conflict` "change not representable
  through DOCX". Never the fallback payload.

## Consequences

- A colleague's edit can never silently remove content or structure DOCX
  could not show them.
- Formatting changes made in Word arrive as Word renders them, minus what
  this rule restores.
- Import pays one export + import of X (about 1 s on 81 chunks).
- Fixing importer defects (F-series) reduces suppressed noise but never
  creates spurious edits for files already in circulation.

## Compatibility and migration

No format change. The conversion defects themselves are not fixed here; the
report counts them (`noise_suppressed`).
