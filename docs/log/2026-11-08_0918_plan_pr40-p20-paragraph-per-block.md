---
date: 2026-11-08 09:18
type: plan
status: active
related: [2026-11-08_0918_decision_pr40-p20-paragraph-per-block.md, 2026-11-07_1741_decision_pr40-choices-decided.md, 2026-11-05_1816_plan_pr40-rework.md]
---

# W1 in #40: one moved paragraph per old block

## Why

The [decision](2026-11-08_0918_decision_pr40-p20-paragraph-per-block.md): with a chunk of
two or more blocks both moved and rewritten, the tracked export's single moved paragraph
makes Word's Reject All, and accepting the move while rejecting the rewrite, give the old
blocks back as one paragraph. The maintainer chose one moved paragraph per old block.

## The change

`_emit_moved_replacement` in `src/aimformat/export_docx.py`, for a pending move and a
pending modify of the same chunk of N paragraph blocks (N >= 1) whose replacement has M
blocks (M >= 0):

1. N moved paragraphs at the destination, paragraph K styled and numbered as old block K.
   Today: one paragraph, styled and numbered as the last old block.
2. Paragraph K holds `moveTo(del(runs of block K))`: the move's author and date on
   `moveTo`, the rewrite's on `del`, as today.
3. Marks. K < N-1: `del` (rewrite) then `moveTo` (move), the CT_ParaRPr order. K = N-1:
   `moveTo`, preceded by `del` when M = 0. When M >= 1 the last paragraph also holds
   `ins(runs of new block 1)` after its moved content, as today.
4. The move range opens in paragraph 0 before its content and closes in paragraph N-1
   after all of it, `ins` included, as today.
5. New blocks 2 to M stay inserted paragraphs (`_emit_inserted_blocks`).
6. Within each paragraph the calls run in today's order (number, marks, range start,
   `moveTo`, nested `del`, `ins`, range end), so revision ids are allocated as today and a
   chunk of one block exports byte for byte as today.
7. The docstring says why one paragraph per block (what Word does with one paragraph) and
   what it costs in LibreOffice.

Unchanged: the move source (`_emit_move_source`, already one paragraph per block), moves of
containers, figures and tables (a deletion plus an insertion), a modify that did not move.
The last moved paragraph keeps the last old block's style and numbering, so a chunk moved
and rewritten into another block type still keeps the old type in Accept All: Codex round
4's finding, on the tracked list with the other findings in older code.

## Tests (`tests/test_docx_tracked.py`, `TestRoundTrip`)

1. New, parametrized like the existing P20 test plus a 1-to-1 case: the tracked export
   resolved as "accept the move, reject the rewrite" gives what accepting the move card and
   rejecting the modify card gives in the SDK. It uses the repo's resolver: in a copy of
   the tracked `document.xml` the rewriter's revisions are swapped for their inverses
   (`w:ins` and `w:del` exchanged, and `w:t` and `w:delText` inside them), then
   `resolve_view(..., "accept")`. Accepting the swapped file accepts the mover's revisions
   and rejects the rewriter's. Fails today on 2-to-1, 3-to-2 and 2-to-none (one paragraph,
   `Old1Old2`).
2. In the same test, a layout check: the destination holds N moved paragraphs, paragraph K
   holding old block K's text. Word restores a rejected move from these paragraphs; the
   repo's resolver restores it from the source, so a Reject All through the resolver cannot
   see a merge.
3. The existing `test_a_moved_and_modified_chunk_resolves_like_its_two_cards` (Accept All
   and Reject All against the SDK's own outcomes) stays as it is and must pass.
4. The whole suite, including the hypothesis lanes in `test_resolution_order_properties.py`.

## Checks outside the suite

- A chunk of one block is unchanged: the Word-check files `mm-1-to-1`, `mm-1-to-2`,
  `mm-1-to-3` and the six `noop-*` lanes, exported from the same `.aim` files by 7f0a254
  and by the change, have identical `document.xml`.
- Word for Mac 16.114.4, the method of the 2026-11-08 check: `mm-2-to-1`, `mm-2-to-none`
  and `mm-3-to-2` exported by the change, each with Reject All, Accept All, and accepting
  the move while rejecting the rewrite. Nine resolutions, all expected right.
- Re-import (`import_docx(..., tracked="propose")`) of the tracked export: Accept All and
  Reject All of the imported cards must still give the SDK's texts. Today, a chunk of two or
  more blocks re-imports as deletions plus additions, the first new block credited to the
  importer instead of its author; the report says what the new layout gives.
- Not measured: LibreOffice on the new layout (the CI oracle checks only the import
  shapes). The expected cost stays documented in the docstring, the decision and the PR.

## Outcome (2026-11-08)

Built in 6b4b2e7.

- The layout check and the mixed resolution fail on the old layout for 2-to-1, 3-to-2 and
  2-to-none (`Old1Old2`, as Word showed) and pass with the change, all six cases. The test
  extends the existing P20 test (same cases plus 1-to-1) instead of adding a second one.
- Byte for byte: `document.xml` is identical for `mm-1-to-1`, `mm-1-to-2`, `mm-1-to-3` and
  the six `noop-*` lanes; only the three files with two or more old blocks changed. Their
  body paragraphs match the hand-made probe Word passed, ids and dates aside.
- Word for Mac 16.114.4: Reject All, Accept All, and accepting the move while rejecting the
  rewrite give the expected text on all three changed files, 9 of 9. No file was saved.
- Re-import: the same cards as before the change (a deletion per old block and an addition
  per new block; the first new block, in a paragraph with revisions by two authors, credited
  to `docx-import` by IMPORT-D5's rule) and the right Accept All and Reject All texts.
- Whole suite on macOS, Python 3.12: 2,376 passed, 5 skipped, 2 xfailed; ruff and mypy
  clean.

## Records

- #40: the P20 line under Decisions, the Word-check line of the test plan, and a comment
  with the Word results.
- This plan's status and index row when #40 merges.

## Plan review

Codex `gpt-6-astra` at xhigh, 2026-11-08 09:20 UTC: **APPROVE**, no findings ("the plan
matches the current code and recorded maintainer decision"). It reran the three supplied
probes read-only and confirmed the nine resolver outcomes and the re-imported texts. Added
after the review, as a note on what stays unchanged: the round-4 finding on the last moved
paragraph's style.
