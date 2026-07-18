---
date: 2026-07-17 17:42
type: plan
status: done
related: []
---

# Plan: pr15 r2 inbound moves trail modify

Codex PR #15 round-2 P2 (major), `src/aimformat/document.py:227` — "Order
independent moves around the modify".

**Finding.** `resolution_order` delays a container modify behind the moves
whenever any sibling move needs it to wait (a rescued member, a stripped
landing point). But the erase-conflict check then flagged EVERY final move
whose destination sits inside the replaced subtree — including an unrelated
INBOUND move that never needed to precede the modify. A lane with an
outbound rescue out of `l2`, an inbound move into `l2` onto a kept anchor,
and an `l2` replacement was refused, even though
`rescue → modify → inbound move` resolves it cleanly (both targets end in
their requested containers, `verify()` and lint clean).

**Fix.** Track which moves actually FORCE the delay (the `forcing` set per
modify, unioned into `must_precede`). A final move landing inside the
replaced subtree conflicts only when it is also in `must_precede`
(must-run-before + would-be-erased = genuinely unsatisfiable). Every other
inbound landing move goes into a new `trailing_moves` tier scheduled AFTER
the delayed modifies (delayed modify rank 3, trailing move rank 4, deletes
bumped to 5), so it lands in the replacement payload instead of being
erased by it.

**Tests.** Red-first `TestInboundMovesTrailTheDelayedModify` in
`tests/test_review_regressions.py`: clean lane ordering
(`move x → modify l2 → move y`), accept-all leaves both targets in their
requested containers with clean `verify()`, accept-all export resolves, and
a guard that an inbound move onto a payload-dropped anchor still conflicts
(neither order safe). Existing `TestLaterMovesClearEarlierMoveConflicts` /
`TestMovesStayAheadOfAnchorDroppingModifies` guards stay green.
