---
date: 2026-11-08 14:42
type: plan
status: done
related: [2026-11-08_1442_decision_pr40-x5-self-anchored-move.md, 2026-11-06_1123_plan_pr40-final-batch.md]
---

# X5 in #40: export a move anchored on its own target like accept_all()

## Why

The [decision](2026-11-08_1442_decision_pr40-x5-self-anchored-move.md): the tracked export
loses the target of a move left anchored on its own target, and the maintainer chose to fix
it in #40.

## The change (`src/aimformat/export_docx.py`, `_Exporter`)

1. `_lands_nothing(move)`: true when `move.anchor_after == move.target` and no add or move
   card made before it (by `_card_order`), anchored in the body, has an anchor chain that
   bottoms out at the target. The chain is walked through card ids with a seen-set, as
   `_unsettled_cards` walks it.
2. The loop that registers pending body moves skips a move for which `_lands_nothing` holds:
   it gets no `moveFrom`, no `moveTo` and no place in any pool.
3. Cards keyed on a skipped move's card id (`adds_by_anchor[("body", move.id)]`) are re-keyed
   onto `("body", move.target)`, after the cards already there, so they land after the
   target; the pool's sort orders them as it orders any pool.
4. In the split of a moved block's anchor pool into the cards made before the move and those
   made after it, a registered move anchored on its own target joins the cards made before
   it. Its arrival is then written among them at the target's old place, and the cards made
   after it follow its arrival, as for any move.
5. A skipped move still counts as a pending body move for the replay switch in
   `_sort_arrivals` (which today reads `pending_move`): it is kept in its own list, and the
   switch reads both. Otherwise skipping a document's only move would turn the replay off
   for every other body pool (plan review, below).
6. A comment says why: section 5.4's reject fallback, and `accept_all()`'s same-anchor rebind
   when an earlier card waits in the zone.

Unchanged: moves anchored elsewhere, moves hidden by a pending delete of their target,
container and slide moves, and cards chained on a move card that the export writes (an older
family, on the tracked list). A written move keeps today's Word layout, so no new layout
needs a check in Word.

## Tests (`tests/test_docx_tracked.py`, `TestRoundTrip`)

1. `test_a_move_left_anchored_on_its_own_target_keeps_its_block`, lanes built with
   `_lettered_lane` and a `("reject", "add N")` step: alone; with a later add after the
   target and a card chained on it; with a modify of the target; beside a real move onto the
   target; two leftovers on neighbouring blocks; a card that a later reject chains on the
   leftover move card itself. Each asserts that the lane holds a self-anchored move, that
   Accept All equals `accept_all()` and Reject All the current body, and that the moved
   paragraphs are only those of real moves.
2. `test_a_move_left_on_its_own_target_lands_after_the_cards_made_before_it`: an add made
   before the move on the target; a card chained on the rejected N; a move of another block
   onto N made before the move. Accept All equals `accept_all()`, with the target after those
   cards.
3. A skipped move keeps the replay for another pool: the leftover plus two adds after B,
   the second naming a table shell (hand-written, as in the existing shell test), where
   `accept_all()` gives `A B Second First C D`.
4. All of them fail at 82d466f (the target is lost). The second test also fails with the
   one-condition variant tried first (`anchor_after != target` alone), which keeps the target
   in front of the earlier cards.

## Checks

- Whole suite, ruff, ruff format, mypy.
- The stress fuzz of 2026-11-08 (23,388 lanes, each with the reject-fallback pattern) re-run
  with the final change: no lane worse than with the one-condition and refined variants,
  content loss 0, and the lanes where a later reject chains a card on a skipped move now
  right.

## Outcome (2026-11-08)

Built as planned, with the plan review's change (a skipped move kept in `_noop_moves`, which
the replay switch reads beside `pending_move`).

- Tests: ten new cases (six, three and one). All fail at 82d466f. With the one-condition
  variant five fail: the three cards made before the move, the card chained on the leftover,
  and the shell lane. Without the replay-switch change, the shell lane fails.
- Whole suite on macOS, Python 3.12: 2,404 passed, 5 skipped, 2 xfailed; ruff, ruff format
  and mypy clean.
- The stress fuzz re-run on its 23,430 saved lanes (23,388 compared; 42 that `accept_all()`
  refuses left out): no lane worse than at 82d466f, with the one-condition variant or with
  the refined variant, in Accept All or Reject All. No paragraph is lost, and Reject All
  matches everywhere. 693 Accept All mismatches are left (refined variant 698, one-condition
  variant about 4,740), every one in a family already on the tracked list: a card chained on
  a move card's id, or a move and a delete of the same block.

## Records

- The next push of #40 carries this fix with the regression tests of 82d466f and the records
  of the maintainer's answers; the PR text says so.
- This plan's outcome section, its status and its index row.

## Plan review

Codex `gpt-6-astra` at xhigh, 2026-11-08 14:43 UTC: **APPROVE-WITH-CHANGES**, one finding.

- [P2] Skipping the only registered move empties `pending_move`, which `_sort_arrivals` reads
  to allow the replay for every body pool, so an unrelated pool could lose its order. Codex's
  repro: the leftover plus First and Second after B, Second naming a table shell;
  `accept_all()` gives `A B Second First C D`, the plan as drafted `A B First Second C D`.
  Applied: change step 5 (a skipped move keeps the switch on) and test 3 (that lane).

## Revision after the pre-push review (2026-11-08)

Before the PR's ready round, four reviewers read the pushed change from four angles (agreement
with `accept_all()`, effects on other exports, test strength, docs), and an agent tried to
refute each finding. Seven held, all in this change; none loses text against 82d466f:

1. [P2] The walk in `_lands_nothing` over-counts. An earlier card in the target's zone moves
   the target only when it really lands there at its turn: not a move that is itself a no-op,
   not a card chained through the leftover, not one ranked after it, and only on the same
   shell. There the leftover is written as a Word move of a paragraph to where it stands, and
   a card chained on its id lands at the end of the document (fuzz: 351 of 56,747 lanes write
   the spurious move, 11 give wrong text).
2. [P2] A captured leftover of a table or list whose every row or item has a pending delete
   now writes its arrival, which puts the deleted rows back in Accept All. 82d466f was right
   there only because it lost the container.
3. [P3] Two pending moves of one block anchored on it (hand-written, lint P018) write the block
   twice where 82d466f wrote it once.
4. [P3] No test covers the walk's seen-set, or a move re-keyed from a skipped leftover onto
   its target.
5. [P3] `architecture.md` says cards chained on such a move land after the target, also when
   the move is written; the index row says nine tests, not ten.

The change:

1. `_lands_nothing` keeps the walk as its quick answer: with no position card made before the
   move waiting in the target's zone, the move lands nothing. Otherwise the lane's own
   accept-all replay decides: the move lands nothing when accepting it at its turn leaves its
   block where it stood. A lane that does not replay keeps the walk's answer (the move is
   written). The replay watches only the moves anchored on their own target; it runs in such
   lanes only, so the replay-counting tests keep `_projected_body_index` as theirs.
2. A container's arrival is not written when it has no pending modify and accepting the lane
   leaves it no member: every member has a pending delete and no card adds or moves one into
   it. This also fixes a plain move of such a container, the every-member case of the older
   family "a moved list loses its items' pending edits".
3. A registered move anchored on its own target joins the early cards only when it is its
   block's only registered move.
4. Tests: the lanes the walk over-counts (a no-op move made before; a card chained on the
   leftover then; an accepted delete's dissolve; a capture by a move that lands nothing); a
   table and a list with every member deleted; a hand-written duplicate; a move chained on the
   leftover; the leftover in the cyclic lane.
5. Docs: `_lands_nothing`'s docstring, the tests' comment, `architecture.md`, the index row.

No separate plan review: the change answers verified findings on the reviewed plan, the
reviewers tried each fix on scratch copies against their own corpora, and the PR's ready round
reviews it. Checks: the new tests fail before the change; the whole suite; the reviewers'
corpora re-run on the change, with no lane worse than at df3713d.
