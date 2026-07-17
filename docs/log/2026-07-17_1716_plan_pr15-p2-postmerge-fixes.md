---
date: 2026-07-17 17:16
type: plan
status: done
related: []
---

# Plan: PR #15 Codex P2 post-merge fixes

Four P2 findings from the Codex review of PR #15, each verified to reproduce
before fixing, each landed as one commit with a red-first regression test in
`tests/test_review_regressions.py`.

1. **`reconcile._reject_dangling` is order-sensitive** (P2 major). A single
   forward pass validates a chained add B while its anchor add A is still
   pending; when the cards are ordered B/A and A is then rejected, B is
   rebound in place to A's (invalid) concrete anchor after its own
   validation already ran — reconcile returns with B carrying P016. Fix:
   iterate `_reject_dangling` to a fixpoint — repeat the full validation
   pass until a pass rejects nothing, so a rebind can never escape
   revalidation regardless of card order.

2. **`resolution_order` flags per-move conflicts independently**
   (P2 major, `document.py`). Multiple pending moves of the same target are
   legal and execute in card order, so only the LAST move decides where the
   chunk finally lands. The per-move check flagged an earlier move into a
   modified container even when a later move relocates the chunk out again
   before the delayed modify runs. Fix: for a delayed modify, evaluate the
   erase-conflict against each move target's final destination (the last
   pending move per target), not against every move independently.

3. **`_markdown_out._proposal_row_width` counts nested rows** (P2 minor).
   `root.iter()` sees every descendant `<tr>`, so an unmarked table nested
   inside a proposed row's cell inflates the outer CriticMarkup table
   width. Fix: count cells only on the payload's outer `<tr>` roots.

4. **Reconciled move proposals skip the member guard** (P2 major,
   `reconcile.py`). Anchor resolution alone keeps a move viable even when
   an out-of-band edit changed the destination container's kind (ul →
   table with the same id): accept later explodes with "`<li>` cannot be a
   direct member of `<table>`". Fix: after resolving the destination
   anchor, re-run `_guard_item_members` for the moved element(s) against
   the reconciled destination container — the same guard `propose_move`
   applies at creation time — and reject the proposal when it fails.

Gates before each commit: ruff check, ruff format --check, mypy, full
pytest, `aim lint examples/*.aim`.
