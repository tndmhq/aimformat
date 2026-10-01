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
  - 2026-10-01_2132_decision_docx-roundtrip-conversion-noise.md
  - 2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md
  - 2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md
  - 2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md
  - 2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md
  - 2026-10-01_2132_decision_docx-roundtrip-informative-convention.md
  - 2026-10-01_2132_decision_docx-roundtrip-architecture.md
---

# Decision: ROUNDTRIP-D7 — id stamping by validated markers, then bounded content matching

**Status:** proposed — awaiting maintainer approval. Nothing is implemented yet. This entry is one of the
DOCX round-trip decision set (gap G3: chunk identity lost on
`.aim` → DOCX → edited in Word → DOCX → `.aim`). Sibling decisions:
[ROUNDTRIP-D1](2026-10-01_2132_decision_docx-roundtrip-identity-carrier.md), [ROUNDTRIP-D2](2026-10-01_2132_decision_docx-roundtrip-marker-placement.md), [ROUNDTRIP-D3](2026-10-01_2132_decision_docx-roundtrip-manifest.md), [ROUNDTRIP-D4](2026-10-01_2132_decision_docx-roundtrip-marks-default-on.md), [ROUNDTRIP-D5](2026-10-01_2132_decision_docx-roundtrip-import-revision-entry-point.md), [ROUNDTRIP-D6](2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md), [ROUNDTRIP-D8](2026-10-01_2132_decision_docx-roundtrip-conversion-noise.md), [ROUNDTRIP-D9](2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md), [ROUNDTRIP-D10a](2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md), [ROUNDTRIP-D10b](2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md), [ROUNDTRIP-D11](2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md), [ROUNDTRIP-D12](2026-10-01_2132_decision_docx-roundtrip-informative-convention.md), [ROUNDTRIP-D13](2026-10-01_2132_decision_docx-roundtrip-architecture.md). Baseline under test: aimformat 0.5.2 (`79a006b`).

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

The returned DOCX's units must be aligned to the exported units so they keep
their ids. Markers can be missing (tool dropped them), misplaced (Enter at
the start of a paragraph leaves the marker on an empty paragraph), or
hostile (a crafted DOCX through the MCP server).

## Options considered

- Content matching only: 77/81 aligned on the null round trip; rewordings
  degrade to delete + add.
- Markers trusted blindly: fails the Enter-at-start case.
- Global optimal assignment (Hungarian): O(n³), ignores order, invents
  moves.
- **Ordered passes, markers first, each validated and bounded:** chosen.

## Decision

Returned units (paragraph blocks after multi-block regrouping, list items,
table rows) are aligned to exported units in passes:

1. **Markers.** A unit takes the first `_aim_` start in it naming an
   unclaimed exported unit of the same kind. A second first-marker in the
   same block is a merge (survivor modified, other deleted, cards linked).
   Markers on dropped blocks are forwarded ([ROUNDTRIP-D2](2026-10-01_2132_decision_docx-roundtrip-marker-placement.md)). Unknown, foreign,
   duplicate and LibreOffice "… Copy N" names are ignored.
2. **Cross-check.** If a marked unit's similarity to its exported unit is
   below 0.35 and an adjacent unmarked unit scores at least 0.8, the id moves
   to that unit.
3. **Exact content LCS** within the gaps between marker anchors, keyed on
   (tag, normalised text).
4. **Fuzzy matching** within remaining gaps on word tokens, ratio ≥ 0.55,
   compatible tags, greedy best-first, order-preserving;
   `real_quick_ratio`/`quick_ratio` prefilters; per-unit text capped at
   2,000 characters for scoring; hard budget of 200,000 scored pairs per
   import. Past the budget, remaining units stay unaligned and the report
   warns.
5. **Moves without markers:** normalised text ≥ 40 characters, similarity ≥
   0.9, match unique on both sides — so repeated boilerplate ("Signature:",
   "N/A") never invents moves.

Unaligned returned units are adds; unaligned exported units are deletes.
Within each scope, moves are the survivors outside the longest increasing
subsequence (as in `diff._stable_ids`). A container takes its own marker,
else the majority container of its items. A move between containers is
delete + add.

**Guard:** refuse (exit 1) when the manifest's `base-doc-hash` matches
neither this document's history nor its checkpoints **and** fewer than 50%
of units align. No `--force`; the user imports the file as a new document.

Untrusted input: existing archive guards (`_docx_seam._guard_archive`) run
first; marker names are parsed with one anchored regex; markers naming ids
not in the export are ignored.

## Consequences

- Thresholds are module constants, not public API; they may be tuned
  without a decision.
- O(n) with markers; bounded without them.
- Without markers, alignment quality depends on edit density; tests require
  at least 90% aligned and zero ops on unchanged units for the
  stripped-markers fixture.

## Compatibility and migration

Internal behaviour of the new entry point ([ROUNDTRIP-D5](2026-10-01_2132_decision_docx-roundtrip-import-revision-entry-point.md)); nothing existing changes.
