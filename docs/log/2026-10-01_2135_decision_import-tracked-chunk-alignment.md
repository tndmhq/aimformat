---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_import-tracked-changes-as-proposals.md, 2026-10-01_2135_decision_import-tracked-moves.md, 2026-10-01_2135_decision_import-tracked-formatting-changes.md, 2026-10-01_2135_decision_import-batch-propose-and-validate.md]
---

# Decision IMPORT-D2: one card per changed chunk, identity alignment, no card without a revision

**Status: proposed — awaiting founder approval.** Implemented, uncommitted, on `wt/aim-import-tracked`; deviations are listed in the [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D2**. Related decisions: [IMPORT-D1](2026-10-01_2135_decision_import-tracked-changes-as-proposals.md), [IMPORT-D3](2026-10-01_2135_decision_import-tracked-moves.md), [IMPORT-D4](2026-10-01_2135_decision_import-tracked-formatting-changes.md), [IMPORT-D8](2026-10-01_2135_decision_import-batch-propose-and-validate.md).

## Context

IMPORT-D1 converts the reject view (O) and the accept view (F) independently with
the existing converter and writes their difference as proposals. Two problems
surfaced while prototyping:

1. **Positional pairing is wrong.** It paired a deleted row with an inserted row
   as a `modify`, and paired our own exporter's add + delete (by different
   authors) as a `modify`.
2. **Converter decisions are not local.** List grouping, heading-level
   inference, explicit list `start`, baked numbering labels and title detection
   can change how an *untouched* paragraph converts when a neighbour is inserted.
   A naive diff would emit a `modify` on a paragraph nobody edited and attribute
   it to a Word author — fabricated attribution, which is worse than a missing
   card.

## Options considered

- Per-run cards — impossible (whole-target payloads, §5.1; one pending modify per
  target, §5.4).
- Per-paragraph cards with positional pairing — the prototype showed wrong
  pairings.
- **Per-chunk cards with identity alignment** — chosen.
- One card per author batch — too coarse to review; a reviewer cannot accept one
  edit and reject its neighbour.

## Decision

- **Source identity.** The seam stamps every `w:p` and `w:tr` (recursively,
  including cells and textboxes) with a private attribute
  `{urn:aimformat:import}src` before resolving the views. The converter records
  provenance for each emitted block: a chunk → the src of the paragraph whose mark
  ends it; a list/table container → the src of each item (`li` → paragraph,
  `tr` → row); a generated `<aim-page-break>` → a derived key `<src>#before|after`.
- **Chunk identity** is the src key. Same key in O and F = same chunk. Same key in
  different carriers (an `li` in O, a body `p` in F) = delete + add, because a
  modify cannot move a chunk between containers.
- **Container identity.** An F container matches the first O container it shares
  an item key with; items inside matched containers align the same way,
  recursively. No shared item → whole-container add or delete. When a list splits
  in F, the first F container keeps the identity; the others are adds with fresh ids.
  When two lists JOIN in F (a deletion removed what separated them) and the
  second one is deleted, its untouched items are adds into the first,
  citing that deletion's revisions — never dropped as converter noise (review
  fix: accept-all used to lose them while validating clean).
- **Merge and split follow the surviving mark.** Merge = delete of the paragraph
  whose mark was deleted + modify of the paragraph that owns the surviving mark.
  Split = add + modify of the mark owner. This mirrors §4.5's informative split
  convention.
- **Replacement pairing.** An O-only chunk immediately followed by an F-only chunk
  in the same container, from the same single author with the same date (two
  absent dates are equal), becomes one `modify` — exactly the shape `to_docx`
  writes for a modify.
- **No card without a revision.** An aligned difference becomes a card only when
  it cites at least one revision record whose source element lies inside that
  chunk (or a property change on it). Any other difference is converter noise: F
  adopts O's markup for that chunk, and the import report lists it as "converter
  difference, original kept". Validation (IMPORT-D8) compares against this
  adjusted F view.
- **Lane emission.** Cards are created in accept-view document order, which is
  their creation order (the SDK orders cards by lane position; `data-at` is
  advisory). Deletes sit at their reject-view position. Adds chain: the first
  anchors after the previous matched block, later ones on the preceding card's id
  (§5.2 permits chains onto pending adds). Table adds carry `data-anchor-shell`.

## Consequences

- A list-item or row edit is a `modify` on that `li`/`tr`; inserted or deleted
  rows/items are add/delete cards in their container; new lists and tables are
  container adds.
- Differences caused by converter non-locality never become cards; they are
  reported instead. The baked-label rule of IMPORT-D4 is one case of this rule.
- Word-level diffs inside a card are a viewer concern (§5.5, Appendix B), as the
  exporter already assumes.
- **tndm editor:** sees one review card per changed paragraph, row or item.

## Compatibility and migration

No format change. Import-output shape only; ships in SDK 0.5.3.

## Tests

The fixture set includes a revision-free paragraph whose conversion shifts because
of a neighbouring insertion (must produce no card), a list split by an inserted
plain paragraph, a list item turned plain by `pPrChange`, and an assertion that
every card cites at least one revision. Removing the mark-join rule or the
no-card-without-revision rule must fail tests.

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
