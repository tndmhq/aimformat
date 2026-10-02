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
  - 2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md
  - 2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md
  - 2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md
  - 2026-10-01_2132_decision_docx-roundtrip-informative-convention.md
  - 2026-10-01_2132_decision_docx-roundtrip-architecture.md
---

# Decision: ROUNDTRIP-D9 — base drift merged three-way; conflicts reported

**Status:** proposed — awaiting maintainer approval. Nothing is implemented yet. This entry is one of the
DOCX round-trip decision set (gap G3: chunk identity lost on
`.aim` → DOCX → edited in Word → DOCX → `.aim`). Sibling decisions:
[ROUNDTRIP-D1](2026-10-01_2132_decision_docx-roundtrip-identity-carrier.md), [ROUNDTRIP-D2](2026-10-01_2132_decision_docx-roundtrip-marker-placement.md), [ROUNDTRIP-D3](2026-10-01_2132_decision_docx-roundtrip-manifest.md), [ROUNDTRIP-D4](2026-10-01_2132_decision_docx-roundtrip-marks-default-on.md), [ROUNDTRIP-D5](2026-10-01_2132_decision_docx-roundtrip-import-revision-entry-point.md), [ROUNDTRIP-D6](2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md), [ROUNDTRIP-D7](2026-10-01_2132_decision_docx-roundtrip-id-alignment.md), [ROUNDTRIP-D8](2026-10-01_2132_decision_docx-roundtrip-conversion-noise.md), [ROUNDTRIP-D10a](2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md), [ROUNDTRIP-D10b](2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md), [ROUNDTRIP-D11](2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md), [ROUNDTRIP-D12](2026-10-01_2132_decision_docx-roundtrip-informative-convention.md), [ROUNDTRIP-D13](2026-10-01_2132_decision_docx-roundtrip-architecture.md). Baseline under test: aimformat 0.5.2 (`79a006b`).

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

Between export and import, the base may have moved on (agent edits, accepted
cards). A two-way diff of the returned file against the current base would
read those later edits as the colleague reverting them. Pending cards also
interact differently with what the colleague saw, depending on the export's
pending mode.

## Options considered

- Always two-way against the current base C: reverts post-export edits.
- Refuse whenever C differs from the exported state X: too strict for normal
  use.
- **Three-way per unit using the manifest's salted hashes ([ROUNDTRIP-D3](2026-10-01_2132_decision_docx-roundtrip-manifest.md)):** chosen.

## Decision

- `base_match` is `exact` when C's `doc_hash` equals the manifest's
  `base-doc-hash`, `advanced` when that hash appears in history or
  checkpoints, `unknown` without a manifest, `mismatch` otherwise (subject to
  the [ROUNDTRIP-D7](2026-10-01_2132_decision_docx-roundtrip-id-alignment.md) guard).
- For each unit the colleague changed: if H(salt ‖ C_u) equals `x`, the
  change applies onto C; otherwise both sides changed it and it is a
  `Conflict` — not applied under `conflicts="report"` (default), proposed
  with "conflicts with an edit made after export" in the explanation under
  `conflicts="propose"`.
- A colleague delete of a unit C already deleted is a no-op. An add anchored
  on a unit C deleted re-anchors to the nearest surviving predecessor.
- **Pending cards on a unit the colleague changed**, by export mode:
  - `accept-all`: the colleague saw the card's text as accepted, so their
    card supersedes it;
  - `reject-all`: the colleague never saw it — conflict ("pending proposal
    the colleague never saw"); agent proposals are never silently retired;
  - `tracked`: handled by [ROUNDTRIP-D10b](2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md) in phase C; before phase C, conflict.
- Import refuses when the base does not `verify()`; `state_at` on a broken
  chain is not trustworthy (matches `reconcile`'s own refusal).

## Consequences

- Works with pruned history: only the manifest hashes and C are needed.
- Conflicts are surfaced, never auto-resolved; the report lists base and
  colleague text for each.
- Without a manifest the merge degrades to two-way against
  `state_at(base_seq)` or C, and the report says so.

## Compatibility and migration

No format change. Supersede uses existing proposal semantics.
