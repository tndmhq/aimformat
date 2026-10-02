---
date: 2026-10-01 21:32
type: decision
status: todo  # proposed — awaiting maintainer approval
related:
  - 2026-10-01_2132_decision_docx-roundtrip-identity-carrier.md
  - 2026-10-01_2132_decision_docx-roundtrip-marker-placement.md
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

# Decision: ROUNDTRIP-D3 — export manifest as a custom XML part

**Status:** proposed — awaiting maintainer approval. Nothing is implemented yet. This entry is one of the
DOCX round-trip decision set (gap G3: chunk identity lost on
`.aim` → DOCX → edited in Word → DOCX → `.aim`). Sibling decisions:
[ROUNDTRIP-D1](2026-10-01_2132_decision_docx-roundtrip-identity-carrier.md), [ROUNDTRIP-D2](2026-10-01_2132_decision_docx-roundtrip-marker-placement.md), [ROUNDTRIP-D4](2026-10-01_2132_decision_docx-roundtrip-marks-default-on.md), [ROUNDTRIP-D5](2026-10-01_2132_decision_docx-roundtrip-import-revision-entry-point.md), [ROUNDTRIP-D6](2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md), [ROUNDTRIP-D7](2026-10-01_2132_decision_docx-roundtrip-id-alignment.md), [ROUNDTRIP-D8](2026-10-01_2132_decision_docx-roundtrip-conversion-noise.md), [ROUNDTRIP-D9](2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md), [ROUNDTRIP-D10a](2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md), [ROUNDTRIP-D10b](2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md), [ROUNDTRIP-D11](2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md), [ROUNDTRIP-D12](2026-10-01_2132_decision_docx-roundtrip-informative-convention.md), [ROUNDTRIP-D13](2026-10-01_2132_decision_docx-roundtrip-architecture.md). Baseline under test: aimformat 0.5.2 (`79a006b`).

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

Two questions cannot be answered from the returned file and the current base
alone: *which* version of the document was exported, and has the base moved
on since then (agent edits after export)? Without that, edits made after
export read as the colleague reverting them.

## Options considered

- **Nothing:** the import cannot detect drift.
- **Custom document property** (what the prototype used): the colleague sees
  it under File > Properties.
- **Full text copy of the exported units:** leaks deleted text to anyone
  holding the file.
- **Per-unit salted hashes in a custom XML part:** chosen.
- An earlier draft also stored per-unit null-round-trip hashes (`n`) and an
  id → hashed-name map. Both dropped: stored `n` hashes go stale whenever
  the importer changes between export and import (see [ROUNDTRIP-D8](2026-10-01_2132_decision_docx-roundtrip-conversion-noise.md)); the name map
  is redundant because hashed names resolve by hashing the base's ids.

## Decision

- A `customXml/itemN.xml` part with its `itemProps`, relationship and
  content-type override.
- Root `<aim:roundtrip xmlns:aim="urn:aimformat:docx-roundtrip:1">` with
  attributes `exporter` (SDK version), `base-doc-hash`, `base-seq`,
  `pending` (the export's pending mode) and `salt` (128 random bits).
- Children `<aim:u id="…" scope="…" shell="…" x="…"/>`, one per unit in
  document order. `x` = SHA-256(salt ‖ serialization of the exported unit),
  truncated to 64 bits. For `accept-all` / `reject-all` exports the exported
  unit is the resolved copy's unit.
- Readers take the first part in this namespace (LibreOffice duplicates the
  part into a second item), ignore unknown namespace versions, parse with
  entity resolution and network access off, and cap the part at 1 MB.
- If the manifest is missing (Document Inspector, Google Docs), the import
  falls back: the exported state X is taken as `state_at(base_seq)` when
  `--base-seq` is given and history is retained, otherwise as the current
  base. The report says which.

## Consequences

- **Disclosure.** The manifest never contains document text, but it reveals
  the revision count (`base-seq`), and because the salt sits in the same
  file, anyone holding the DOCX can confirm a guess about a short unit's
  exact text. Users sending a file to an external party with no metadata use
  `--no-roundtrip-marks` ([ROUNDTRIP-D4](2026-10-01_2132_decision_docx-roundtrip-marks-default-on.md)) or Word's Document Inspector (which removes
  the part; the bookmarks then remain, carrying only ids).
- Without a manifest, agent edits made after export may surface as proposed
  reverts. Under the default proposals mode ([ROUNDTRIP-D6](2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md)) they are reviewable, not
  applied.
- Works with pruned history: only the manifest hashes and the current state
  are needed for drift detection ([ROUNDTRIP-D9](2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md)).

## Compatibility and migration

The namespace URI version is the unit of compatibility for the whole DOCX
convention. Changes bump it (`…:2`); readers keep reading `…:1`. No `.aim`
change.
