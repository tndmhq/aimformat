---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_import-tracked-changes-as-proposals.md, 2026-10-01_2135_decision_import-tracked-chunk-alignment.md, 2026-10-01_2135_decision_import-docx-comments-reported.md]
---

# Decision IMPORT-D4: formatting and property changes on DOCX import

**Status: proposed — awaiting founder approval.** Implemented, uncommitted, on `wt/aim-import-tracked`; deviations are listed in the [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D4**. Related decisions: [IMPORT-D1](2026-10-01_2135_decision_import-tracked-changes-as-proposals.md), [IMPORT-D2](2026-10-01_2135_decision_import-tracked-chunk-alignment.md), [IMPORT-D6](2026-10-01_2135_decision_import-docx-comments-reported.md).

## Context

Today `w:rPrChange` and `w:pPrChange` are ignored, so the *new* formatting
(bold, Normal → Heading 2) is applied silently, as if accepted. Some property
changes have no `.aim` expression. Baked (literal) numbering labels shift when a
list item is inserted, which, if proposed, would turn one insertion into N label
cards that cite no revision on their own paragraph.

## Options considered

- Propose every property difference, including label shifts — floods the lane
  with trivial cards and breaks IMPORT-D2's "no card without a revision" rule.
- Ignore property changes (today) — silently accepts them.
- **Propose when expressible, report otherwise; treat label-only differences as
  converter noise** — chosen.

## Decision

- `rPrChange`, `pPrChange` and the table/cell property changes (`tblPrChange`,
  `trPrChange`, `tcPrChange`) become a `modify` when the two views' markup
  differs. Expressible today: bold, italic, colour, size, face, alignment, heading
  level, cell shading, cell width.
- When the difference is not expressible, no card is written and the import
  report says "not carried" (IMPORT-D6).
- A `sectPrChange` that changes page setup becomes a `propose_page_setup` card on
  `aim:doc`.
- **Baked numbering labels:** label-only differences are converter noise under
  IMPORT-D2; labels follow the original numbering, and the report says so. Dynamic
  `num-*` numbering renumbers itself, so it needs no card.
- The fidelity claim is scoped everywhere to: accept-all equals Word's accept-all
  *except baked numbering labels, which keep the original numbering, and
  formatting the format cannot express; both are reported.*

## Consequences

- Formatting cards export (`to_docx(pending="tracked")`) as whole-paragraph
  del + ins, not as Word "Formatted:" revisions. Accept and reject outcomes are
  equal either way; a formatting-level export is recorded as out of scope in
  `TODO.md`.
- **tndm editor:** a Word "made bold" or "Normal → Heading 2" revision shows as a
  modify card instead of being silently applied.

## Compatibility and migration

No format change. Ships in SDK 0.5.3.

## Tests

Fixtures for `rPrChange` (bold), `pPrChange` (heading), a baked-numbering
insertion (no label cards, report line present) and a dynamic-numbering
insertion.

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
