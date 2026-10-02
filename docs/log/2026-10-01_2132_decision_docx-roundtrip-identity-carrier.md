---
date: 2026-10-01 21:32
type: decision
status: todo  # proposed — awaiting maintainer approval
related:
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
  - 2026-10-01_2132_decision_docx-roundtrip-architecture.md
---

# Decision: ROUNDTRIP-D1 — chunk identity travels through DOCX as hidden bookmarks

**Status:** proposed — awaiting maintainer approval. Nothing is implemented yet. This entry is one of the
DOCX round-trip decision set (gap G3: chunk identity lost on
`.aim` → DOCX → edited in Word → DOCX → `.aim`). Sibling decisions:
[ROUNDTRIP-D2](2026-10-01_2132_decision_docx-roundtrip-marker-placement.md), [ROUNDTRIP-D3](2026-10-01_2132_decision_docx-roundtrip-manifest.md), [ROUNDTRIP-D4](2026-10-01_2132_decision_docx-roundtrip-marks-default-on.md), [ROUNDTRIP-D5](2026-10-01_2132_decision_docx-roundtrip-import-revision-entry-point.md), [ROUNDTRIP-D6](2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md), [ROUNDTRIP-D7](2026-10-01_2132_decision_docx-roundtrip-id-alignment.md), [ROUNDTRIP-D8](2026-10-01_2132_decision_docx-roundtrip-conversion-noise.md), [ROUNDTRIP-D9](2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md), [ROUNDTRIP-D10a](2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md), [ROUNDTRIP-D10b](2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md), [ROUNDTRIP-D11](2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md), [ROUNDTRIP-D12](2026-10-01_2132_decision_docx-roundtrip-informative-convention.md), [ROUNDTRIP-D13](2026-10-01_2132_decision_docx-roundtrip-architecture.md). Baseline under test: aimformat 0.5.2 (`79a006b`).

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

Something inside the DOCX must name the `.aim` unit each paragraph came
from, survive editing and re-saving in Word and LibreOffice, and stay
invisible to the person editing.

## Options considered

| option | verdict |
|---|---|
| A. hidden bookmarks (names starting `_`) | **chosen.** LibreOffice keeps 81/81 through a re-save. Word documents that it keeps hidden bookmarks (to confirm in the manual protocol). Invisible unless "Hidden bookmarks" is ticked. Cut and paste carries them; deleting the text removes them; copy and paste does not duplicate them. |
| B. block content controls (`w:sdt` with `w:tag`) | rejected: visible chrome; Enter inside a control extends it; today's importer drops their content (backlog F2). Survive a LibreOffice re-save 3/3. |
| C. `w14:paraId` | rejected: LibreOffice drops 81/81; Word regenerates them. |
| D. hidden text (`w:vanish`) | rejected: visible with formatting marks on; copied with the text; breaks find/replace and word counts. |
| E. no carrier, content matching only | kept as the fallback path, and as the only path for formats and tools without markers (Google Docs, Pages, a later Markdown round trip). |

## Decision

Option A: hidden bookmarks carry identity. Content-only alignment (see
[ROUNDTRIP-D7](2026-10-01_2132_decision_docx-roundtrip-id-alignment.md)) is a first-class fallback, tested as a main path, not an edge case.

## Consequences

- Today's `from_docx` ignores bookmarks: a marked file imports to the same
  blocks as an unmarked one.
- Tools that drop bookmarks degrade to content alignment, which loses some
  identity on heavy rewording (prototype with all bookmarks stripped:
  14 ops vs 12 true ops; reword and merge become delete + add).
- Bookmarks are hints, not identity: identity is declared by the events or
  cards the import writes (spec §4.5 already says markers are hints).

## Compatibility and migration

No `.aim` grammar, conformance or `data-aim-version` change. DOCX files
exported before this change carry no markers and import onto a base by
content only, with a warning in the report.
