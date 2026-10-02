---
date: 2026-10-01 22:10
type: report
status: active
related:
  - 2026-10-01_2142_plan_docx-roundtrip-implementation.md
  - 2026-10-01_2132_decision_docx-roundtrip-architecture.md
  - 2026-10-01_2210_decision_propose-delete-depends-on.md
---

# Report: DOCX round trip (G3), first implementation

Branch `wt/aim-docx-roundtrip`, on top of 0.5.2 (`79a006b`). Uncommitted at
the time of writing; nothing merged. Implements the
[plan](2026-10-01_2142_plan_docx-roundtrip-implementation.md) against the
ROUNDTRIP decision set.

## What exists

- `docx_marks.py`: marker names (`_aim_<id>`, `_aim<k>_<id>`, `_aimh…`),
  their parser, the manifest writer/reader (`urn:aimformat:docx-roundtrip:1`).
- `export_docx.py`: `to_docx(..., roundtrip_marks=False)` and an in-memory
  `docx_bytes()`. Paragraph creation is wrapped so every paragraph a unit
  emits records its marker; synthetic page breaks and pending adds carry
  none; table rows mark their first cell.
- `convert/_docx_in.py`: `convert_docx_marked()` carries each paragraph's
  markers as a transient attribute on the block, list item or row it became;
  markers of paragraphs that emit nothing forward to the next block.
  `convert_docx()` is unchanged.
- `revision_import.py`: `import_revision()` and `RevisionImportReport`
  (design D5–D9, D13). `AimDocument.import_revision()` wraps it.
- CLI `aim import X.docx --onto F.aim [--as --author --conflicts --base-seq
  --dry-run --format]`, `aim export --roundtrip-marks`; MCP
  `aim_import_revision` and `aim_export(roundtrip_marks=)`.
- `docs/interop/docx-roundtrip.md`, one informative spec §10 bullet, one
  Appendix B line, README/for-agents/skill/CHANGELOG/architecture.
- `tests/test_docx_roundtrip.py` (81 tests) plus LibreOffice-re-saved
  fixtures from `scripts/gen_roundtrip_fixtures.py`.

## Measured (legal-addendum fixture, 81 units; `o200k_base`)

| measure | before | after |
|---|---|---|
| chunk ids surviving export → import | 0 / 81 | 81 / 81 (also through a LibreOffice 25.8 re-save) |
| `diff_documents` after an unedited round trip | 81 added + 81 deleted | 0 (import writes nothing) |
| synthetic edit set (3 modify incl. split/merge, 1 add, 2 delete, 1 move) | whole document replaced | exactly the 8 ground-truth cards, also after LibreOffice re-save and with all bookmarks stripped |
| tokens to learn what changed | 5,689 (re-read a fresh import) | 1,913 (report 299 + pending lane with payloads 1,004 + current text of changed ids 610) |
| full read after import (chunks + lane) | — | 6,883 |
| DOCX size | 42,361 B | 46,257 B with marks (+9.2%: bookmarks + per-unit manifest) |
| export time | 0.05 s | 0.05 s with marks |
| import time, edit set | — | 0.7 s |

The edit set is synthetic (XML edits made the way Word leaves them). No
figure here comes from a Word-saved file.

Null round trips (export with marks, import onto the same document) give zero
cards and zero events for every example (`deck`, `booklet`, `proposal`) in all
three pending modes and every DOCX fixture, in both change modes.

## Deviations from the decision entries

1. **D4: marks are off by default.** D4 makes turning them on conditional on
   the manual Word check (design §7.4), which has not been run. Default DOCX
   exports are byte-for-byte unchanged, so the tndm editor sees nothing until
   both the check passes and its pin is bumped. Turning the default on later
   is a one-line change plus regenerating any byte-compared DOCX fixtures.
2. **D3: the manifest carries two more fields:** `exported-at` (for D6's
   time clamp) and one `<aim:card id>` per card pending at export (to rebuild
   the exported view and to tell cards the colleague saw from cards created
   later). Both are ids or timestamps, no text.
3. **D10a, interim:** today's importer drops *all* `w:ins`/`w:del` content.
   Rather than refuse, the import accepts the colleague's own tracked changes
   (any revision whose author/date the export did not write) before reading,
   attributes the result to the file's last editor, and warns. Per-revision
   authors wait for G4.
4. **Units the pipeline cannot read back are hidden, not deleted.** A unit
   whose paragraphs the importer drops (an empty paragraph; a pending card
   exported as tracked revisions, since revisions are dropped) is excluded
   from alignment instead of being proposed for deletion.
5. **Phantoms.** Unmarked blocks the export → import pipeline makes on its
   own (a page break before a slide) are matched against the null round trip
   and never proposed as additions.
6. **Edits mode is stricter than D9:** any pending card on a changed unit is
   a conflict (no supersede in edits mode), and the import refuses if the
   direct edits would leave the pending lane unresolvable.
7. **Proposal emission uses the SDK's `propose_*` directly** rather than a
   `_propose_body_diff` shared with G4 (G4 is not merged). The op list in
   `revision_import._Planner` is the shape such a shared emitter would take.
8. **New SDK surface not in the decision set:** `propose_delete(depends_on=)`
   — its own [decision entry](2026-10-01_2210_decision_propose-delete-depends-on.md).

## Known limits / left to do

- Design §7.4: the manual Word protocol, and Word-saved fixtures in CI.
- D10a proper (per-author cards from tracked changes) — after G4.
- D10b (Word accept/reject of exported tracked cards) — phase C. Until then
  a changed or resolved exported revision only produces a warning.
- Proposal emission is quadratic in the number of cards because each
  `propose_*` validates against a clone with the whole lane replayed: 60
  cards on the 81-unit fixture take about 24 s. An import that touches most
  of a long document needs a batched validation path in the SDK.
- A whole-container modify (e.g. a list kind flip) takes only the first
  element of an item *run*.
- Moves across containers become delete + add (as designed); an addition
  placed between two units of one slide is a conflict.
- Comments, headers, footers and footnotes are not imported (warned).
