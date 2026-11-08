---
date: 2026-11-08 14:42
type: decision
status: active
related: [2026-11-08_1442_plan_pr40-x5-self-anchored-move.md, 2026-11-06_1123_plan_pr40-final-batch.md, 2026-11-07_1741_decision_pr40-choices-decided.md]
---

# X5: a move left anchored on its own target exports like accept_all()

## The bug

`propose_move` refuses to anchor a block after itself, but a pending move can still end up
anchored on its own target. Spec section 5.4 rebinds a card anchored on a rejected card to
that card's own anchor: A B C, add N after A, move A after N (the SDK chains the move on N's
card), reject N, and the move reads "A after A". A longer chain rejected link by link, an
accepted delete whose dissolve binds the move to its own target, and reconcile reach the
same card; lint passes it, and a hand-written card can say the same. `accept()` takes such a
move as a no-op.

The tracked DOCX export (`to_docx(pending="tracked")`) registered it as a move and wrote the
target's `moveFrom`, but the move's own arrival fell in neither half of the split of its
anchor's pool (cards made before the move, cards made after it), so no `moveTo` was written.
Word's Accept All therefore lost the target, a pending modify of it, and every card made
after the move on it. 0.5.3 writes no Word moves, so its files keep the target. Found by the
checks of Codex round 3's fixes; reproduced on 2026-11-08 with plain SDK calls.

## The decision

The maintainer, 2026-11-08, after asking how a move after itself arises: "Yes, fix in #40".

## What the export must match

`accept_all()` does not always treat such a move as a no-op. When a position card made
before it (an add or a move whose anchor chain bottoms out at the target) waits in the
target's zone, `accept_all()` lands that card first, and section 5.4's same-anchor rebind
moves the target behind it. So the export:

- writes no move when no such earlier card exists, and lands the cards anchored on the
  target, or chained on the move card itself, after the target in card order;
- otherwise writes the move as an arrival among those earlier cards, ordered like the rest of
  the pool, with the cards made after it following it.

## Left out

Not in this fix, and recorded on the maintainer's tracked list: cards chained on the id of a
move card that the export does write still land at the end of the document (an older family);
the SDK's side of the card (its outcome depends on decision order when an earlier card shares
the zone, and while it is pending, accepting a later card on its target is refused); and
whether a lane should keep such a card at all, a format question for the maintainer.

## Revision after the pre-push review (2026-11-08)

"What the export must match" above overstates one point. An earlier card waiting in the
target's zone moves the target only when it lands something at its turn. It does not when the
card itself lands nothing (a move left where its block already stands, which rebinds nothing),
when it chains through the leftover, when it ranks after it, or when it names another shell.
In those lanes `accept_all()` takes the leftover as a no-op too. The export now asks the lane's
own accept-all replay whenever a card made before the leftover waits in its zone, and writes
no move when accepting the leftover there leaves its block where it stood. The same review
found two lanes this change had broken, both fixed in it: a captured leftover of a table or
list whose every row or item has a pending delete brought the rows back in Accept All, and two
hand-written copies of the leftover (lint P018) wrote the block twice. Details in the plan's
revision section.

## Second revision (2026-11-08)

The table and list fix above was incomplete. The deleted rows still came back when a row was
also reordered inside the container, or when the container had a pending modify. Both are
fixed now, and so is the same table or list written in place with a modify pending. Two
hand-written copies of the leftover write one arrival, but three still arrive more than once
(tracked). Details in the plan's second revision.

## Third revision (2026-11-08 and 09)

The table and list fix still let the deleted rows come back when another card adds a row to the
table or list: that row is never written inside it, but it kept the container from counting as
emptied. Lanes that round 5's head got right went wrong; they are right again. One shape stays
traded, by the maintainer's choice (2026-11-09, "Track it, merge"): a nested list under a
deleted item, in a leftover that moves nothing, keeps its nested items in Word's Accept All, as
every unmoved list does (older, tracked); round 5's head got that view right only by dropping
the list. Details in the plan's third revision.
