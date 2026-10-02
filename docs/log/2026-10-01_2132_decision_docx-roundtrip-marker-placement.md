---
date: 2026-10-01 21:32
type: decision
status: todo  # proposed — awaiting maintainer approval
related:
  - 2026-10-01_2132_decision_docx-roundtrip-identity-carrier.md
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

# Decision: ROUNDTRIP-D2 — marker placement and naming

**Status:** proposed — awaiting maintainer approval. Nothing is implemented yet. This entry is one of the
DOCX round-trip decision set (gap G3: chunk identity lost on
`.aim` → DOCX → edited in Word → DOCX → `.aim`). Sibling decisions:
[ROUNDTRIP-D1](2026-10-01_2132_decision_docx-roundtrip-identity-carrier.md), [ROUNDTRIP-D3](2026-10-01_2132_decision_docx-roundtrip-manifest.md), [ROUNDTRIP-D4](2026-10-01_2132_decision_docx-roundtrip-marks-default-on.md), [ROUNDTRIP-D5](2026-10-01_2132_decision_docx-roundtrip-import-revision-entry-point.md), [ROUNDTRIP-D6](2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md), [ROUNDTRIP-D7](2026-10-01_2132_decision_docx-roundtrip-id-alignment.md), [ROUNDTRIP-D8](2026-10-01_2132_decision_docx-roundtrip-conversion-noise.md), [ROUNDTRIP-D9](2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md), [ROUNDTRIP-D10a](2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md), [ROUNDTRIP-D10b](2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md), [ROUNDTRIP-D11](2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md), [ROUNDTRIP-D12](2026-10-01_2132_decision_docx-roundtrip-informative-convention.md), [ROUNDTRIP-D13](2026-10-01_2132_decision_docx-roundtrip-architecture.md). Baseline under test: aimformat 0.5.2 (`79a006b`).

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

Given hidden bookmarks ([ROUNDTRIP-D1](2026-10-01_2132_decision_docx-roundtrip-identity-carrier.md)), the exporter must decide where they sit and
what they are called. Word bookmark names are limited to 40 characters from
`[A-Za-z0-9_]`; spec §4.4 ids may contain `-` and run to 64 characters. The
prototype used *spanning* bookmarks, which makes split and section semantics
depend on where Word moves `bookmarkEnd` when paragraphs are split or
joined, which is unspecified.

## Options considered

- **Spanning bookmarks** around each unit (what the prototype measured):
  read meaning from where `bookmarkEnd` lands. Rejected as the default
  because that position is unspecified behaviour; a split and a
  "new paragraph after" also produce the same ops anyway.
- **Hashing every id** into the name: uniform, but opaque, and every name
  then depends on the base to resolve.
- **Collapsed (point) bookmarks on every exported paragraph, start-only
  reading:** chosen, subject to the gate below.

## Decision

- Every exported paragraph of a unit carries exactly one **collapsed**
  bookmark (`bookmarkStart` immediately followed by `bookmarkEnd`), placed
  after `w:pPr`. Identity reads only `bookmarkStart` names.
- Units follow `reconcile._units`: top-level chunks, container items (a run
  counts as one unit), containers.
  - first paragraph of a unit: `_aim_<id>`;
  - k-th paragraph (k ≥ 2): `_aim<k>_<id>` (used only to regroup
    multi-block chunks such as `section`, `div`, `blockquote`);
  - a container's `_aim_<containerid>` goes on its first paragraph, next to
    the first item's marker;
  - a table row's markers go in its first cell.
- Names: `_aim_` + id when the id matches `[a-z0-9_]{1,32}` (every id the
  reference tooling mints); otherwise `_aimh_` + the first 12 hex digits of
  SHA-256(id), resolved at import by hashing the base's ids. Grammar:
  `^_aim(h?)(\d*)_(.+)$` — unambiguous because the digits precede the
  separator.
- Under `pending="tracked"`, revision paragraphs carry the marker of the unit
  they render (used by [ROUNDTRIP-D10b](2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md)).
- Bookmark `w:id`s are allocated above any id already present.
- A marker on a paragraph the importer drops (e.g. the empty paragraph left
  by pressing Enter at the start) is forwarded to the next emitted block that
  has no marker of its own.

**Gate:** point vs spanning is decided by the manual Word protocol. Phase A
also re-runs the LibreOffice survival test for point bookmarks (unmeasured).

## Consequences

- The exporter threads a `_mark(unit_id, paragraphs)` call through
  `emit_block`, `emit_tracked_chunk`, `emit_tracked_list_container`,
  `emit_list`, `emit_table`, `emit_figure`.
- The marker grammar becomes part of the documented convention ([ROUNDTRIP-D12](2026-10-01_2132_decision_docx-roundtrip-informative-convention.md));
  once marked files are in circulation it cannot be changed without a new
  namespace version.

## Compatibility and migration

No `.aim` change. Readers that do not know the convention see ordinary
hidden bookmarks. A future naming change ships under a new manifest
namespace version ([ROUNDTRIP-D3](2026-10-01_2132_decision_docx-roundtrip-manifest.md)); readers keep accepting the old names.
