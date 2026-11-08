---
date: 2026-11-08 09:18
type: decision
status: active
related: [2026-11-07_1741_decision_pr40-choices-decided.md, 2026-11-08_0918_plan_pr40-p20-paragraph-per-block.md]
---

# W1: a chunk both moved and rewritten exports one moved paragraph per old block

Supersedes P20 in [pr40-choices-decided](2026-11-07_1741_decision_pr40-choices-decided.md),
which approved the export layout as built on 2026-11-06, pending a check in Word itself.
The rest of that entry stands.

## What the check in Word found

Word for Mac 16.114.4, 2026-11-08, on the files of #40 at 7f0a254
([results](https://github.com/tndmhq/aimformat/pull/40#issuecomment-6715823375)). A
pending move and a pending modify of the same chunk export as `moveTo(del(old))` +
`ins(new)`, with every old block's deleted text in one moved paragraph. With two or more
old blocks, Word's Reject All puts the old blocks back at their old place as one paragraph
(`Old1Old2`), and so does accepting the move while rejecting the rewrite. Accept All is
right. With one old block, all three resolutions are right. Neither the repo's resolver
nor LibreOffice merges the old blocks, so neither showed this.

## The decision

The maintainer, 2026-11-08: "Let's do (b) for W1, implement".

(b): one moved paragraph per old block, each holding its block's text, moved and deleted.
The mark of every moved paragraph but the last is tracked as deleted too (`del` +
`moveTo`). The last mark takes over the first new block, and further new blocks stay
inserted paragraphs. With no new block, every mark is tracked as deleted. A chunk of one
block exports as before. Implemented in #40 before it merges
([plan](2026-11-08_0918_plan_pr40-p20-paragraph-per-block.md)).

The options put to him: (a) keep the one paragraph and document the merge in Word;
(b) above, the recommendation; (c) export the chunk as a deletion plus an insertion
instead of a Word move.

A probe on 2026-11-08 rewrote the three failing files into (b) by hand: Word gave the
expected text on all nine resolutions.

## Cost

LibreOffice's Reject All keeps a paragraph mark tracked as both deleted and moved as an
empty paragraph (seen on 2026-11-05, when the one-paragraph layout was chosen over this
one). So on a chunk of several blocks it is expected to leave empty paragraphs at the
move's destination, up to one per old block. Not measured for this layout: the CI oracle
checks only the import shapes.
