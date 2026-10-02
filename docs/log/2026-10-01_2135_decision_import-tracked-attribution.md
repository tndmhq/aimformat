---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_import-tracked-changes-as-proposals.md, 2026-10-01_2135_decision_import-tracked-chunk-alignment.md, 2026-10-01_2135_decision_import-tracked-moves.md]
---

# Decision IMPORT-D5: attribution, timestamp, batch and explanation for imported revisions

**Status: proposed — awaiting founder approval.** Implemented, uncommitted, on `wt/aim-import-tracked`; deviations are listed in the [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D5**. Related decisions: [IMPORT-D1](2026-10-01_2135_decision_import-tracked-changes-as-proposals.md), [IMPORT-D2](2026-10-01_2135_decision_import-tracked-chunk-alignment.md), [IMPORT-D3](2026-10-01_2135_decision_import-tracked-moves.md).

## Context

Each imported card needs an author (`data-author-*`), a time (`data-at`), a batch
and an explanation that stands alone (§5.5). The DOCX supplies `w:author` and
`w:date` per revision; one paragraph can carry revisions by several authors. The
values come from an untrusted file. Our exporter writes agent actors as
`w:author="agent:<model>"` (`export_docx._actor_label`).

## Options considered

For a card whose revisions come from more than one author: (a) the latest author,
(b) the first author, (c) `external("docx-import")` with the authors listed in the
explanation. (a) and (b) attribute someone's edit to someone else; only (c) does
not misattribute.

## Decision

- **Actor.**
  - `w:author="agent:<model>"` → `Actor(type="agent", model=<model>)`, the exact
    inverse of `export_docx._actor_label`.
  - Any other non-empty author → `Actor(type="human", id=<author>)`.
  - Missing author → `external("docx-import")`.
  - Mixed authors on one card → `external("docx-import")`; the explanation lists
    the authors.
  - Author names, including the `agent:` prefix, are **claims made by the file**,
    exactly like any author name in a DOCX; the SDK does not verify them.
- **Sanitizing.** `w:author` and comment text are stripped of control characters,
  length-capped (author 128, comment 2,000, excerpt 240) and serialized only
  through the canonical escaper.
- **`data-at`.** The latest `w:date` among the card's revisions. A timestamp with
  no timezone is read as UTC. A missing or invalid date uses the import time and
  adds a report warning. `data-at` is advisory and does not affect creation order.
- **Batch.** One batch per author group, mirroring Word's "Accept all changes
  by …".
- **Explanation.** Built from the revision records' own text (never from a
  character diff, so there is no quadratic `difflib` on a hostile 1 MB paragraph),
  capped at 240 characters. Shapes:
  - inline edits: `Word tracked change (Alice Smith): "thirty" → "fifteen"; (Bob Jones): + " of the order date"`
  - whole blocks: `Word: Bob Jones inserted this paragraph`
  - formatting: `Word: Bob Jones changed formatting`

## Consequences

- A mixed-author paragraph re-exports under the `docx-import` label.
- Agent-authored revisions round-trip as agent actors through DOCX.
- **tndm editor:** displays Word user names as card authors; batch-accept by
  author maps onto Word's per-reviewer accept.

## Compatibility and migration

No format change: these are existing proposal attributes. Ships in SDK 0.5.3.

## Tests

Fixtures for a mixed-author paragraph and a missing `w:date`; card-set assertions
check author, batch and explanation prefix; a hostile-input guard checks that a
1 MB single-paragraph revision finishes in linear time.

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
