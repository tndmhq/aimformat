---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_import-tracked-changes-as-proposals.md, 2026-10-01_2135_decision_import-tracked-chunk-alignment.md, 2026-10-01_2135_decision_import-batch-propose-and-validate.md]
---

# Decision IMPORT-D3: Word moves become `move` proposals, and `to_docx` exports pending paragraph moves

**Status: proposed — awaiting founder approval.** Implemented, uncommitted, on `wt/aim-import-tracked`; deviations are listed in the [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D3**. Related decisions: [IMPORT-D1](2026-10-01_2135_decision_import-tracked-changes-as-proposals.md), [IMPORT-D2](2026-10-01_2135_decision_import-tracked-chunk-alignment.md), [IMPORT-D8](2026-10-01_2135_decision_import-batch-propose-and-validate.md).

## Context

Today a Word move (`w:moveFrom` … `w:moveTo`, named range marks) loses the
clause from both places on import. In the other direction, `to_docx(pending=
"tracked")` writes a pending `move` as unchanged content (measured), so Word's
Accept All differs from `.aim`'s `accept_all()`, and an agent's move is invisible
to a Word reviewer. The exporter docstring says "Not represented in v0.1: move
proposals".

Word tracks moves of paragraph text only. Table rows and whole containers have no
move markup. A pending move combined with a pending modify on the same target
needs nested markup that Word and LibreOffice may resolve differently.

## Options considered

- (a) A `move` card (plus a `modify` when the moved text was also edited);
  `to_docx` emits `w:moveFrom`/`w:moveTo` with named range marks.
- (b) Delete + add on import, no exporter change. Simpler, but loses chunk
  identity and the reviewer's notion of "moved", and leaves the export bug.

## Decision

(a), for paragraph-level chunks.

- **Import.** An O-only block whose mark is `moveFrom` named N and an F-only block
  whose mark is `moveTo` named N become one `move` card. If the moved text was also
  edited, a `modify` card is added (§5.4 allows one pending move and one pending
  modify per target). Unnamed pairs become delete + add.
- **Export.** Paragraph-level chunks export as `moveFrom`/`moveTo`. Rows and
  containers export as delete + insert (identity is lost on re-import, and the
  import report says so). Moved-and-modified exports as `moveFrom(old)` plus a
  `moveTo` wrapping `del(old) ins(new)`; that shape is checked once against the
  oracle, and if Word and LibreOffice disagree it falls back to delete + insert.
- Moves carry the lane's documented out-of-order limitation (§5.4), so they are
  imported only when name-paired, and a property test checks that random
  resolution orders over imported move-free lanes converge to the creation-order
  result.

## Consequences

- `to_docx(pending="tracked")` output changes for documents with pending moves:
  agents' moves become visible to a Word reviewer, where today they are not.
- The exporter docstring is updated.
- **tndm editor:** its DOCX export of a document with a pending move changes
  shape; its review lane shows imported moves as `move` cards.

## Compatibility and migration

No format change. Ships in SDK 0.5.3.

## Tests

Round trip: an `.aim` with modify, add, delete, paragraph move,
moved-and-modified and table-row cards → `to_docx(pending="tracked")` →
`import_docx` gives back the same actions, target texts, actors and outcomes.
Fixtures for an unnamed move and a moved-and-modified paragraph. Removing the move
pairing must fail tests.

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
