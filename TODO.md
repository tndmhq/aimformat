# TODO

Known, reproduced and deliberately deferred work. Each entry names the code,
a repro and the suggested fix. Read the entries for an area before working in
it, and add to this file rather than to a log entry.

## Resolving a card costs time linear in the lane, so a lane costs quadratic time

- **Where:** `src/aimformat/document.py`: `AimDocument.proposals` (re-parses
  every card, payloads serialized, on each read), `accept()` / `reject()` →
  `proposal(pid)` and `_resolve()` (both read `proposals`, and `_resolve`
  recomputes `resolution_order` over the whole lane), and `_taken_ids()` /
  `DocState.all_ids()` (a full body scan per payload normalization).
- **Repro:** `accept_all()` on a document with *n* pending cards, or
  `import_docx` of a `.docx` with one tracked change per paragraph (the
  importer validates its lane by accepting and rejecting every card on
  clones). Measured on one laptop: 250 cards 2.2 s, 600 cards 11 s, 1,200
  cards 50 s, 2,500 cards about 6.5 min.
- **Mitigation in place:** `import_docx(max_proposals=500)` refuses a larger
  tracked-change lane before any card is written.
- **Suggested fix:** keep a pending-card index (id → `Proposal`, creation
  order, chain parents) and a live id set on the document, updated by the
  few writers of the `aim-proposals` section and of the body, instead of
  re-deriving them per call; or give `accept_all` / `reject_all` a one-pass
  path over a projection, as `_propose_lane` already does for writing. Then
  raise the importer's default `max_proposals` and add a timing test at the
  new cap.

## A direct `delete_chunk` can strand pending cards (P008/P011)

- **Where:** `src/aimformat/document.py:1884` (`delete_chunk`); the only
  dependent check is `_cards_anchored_on(cid)` at line 1898.
- **What:** a direct delete refuses only when a pending add/move card's
  `data-anchor-after` is the deleted id itself. It does not refuse when:
  1. a pending proposal targets the deleted chunk (`propose_modify("a", …)`
     then `delete_chunk("a")`): the file then fails lint with `P008 proposal
     targets unknown chunk 'a'`;
  2. a pending proposal targets a chunk nested inside a deleted container
     (`propose_modify("l2", …)`, then `delete_chunk("<list container id>")`):
     `P008 … 'l2'`;
  3. a pending add/move card is anchored on a nested item
     (`data-anchor-after="l2"`) or on the deleted container itself
     (`data-anchor-container`): `P011 add anchor 'l2' is neither a chunk nor a
     pending position card`.
- **Repro:** add `<p data-aim="a">` and
  `<ul data-aim-container="lista001"><li data-aim="l1">…</li><li data-aim="l2">…</li></ul>`,
  create one of the pending cards above as a human, then call `delete_chunk`
  on `a` or `lista001` and lint the result. Present on `main` before spec 0.6.
- **Fix:** reuse the subtree rule that `_guard_removal_dependents` applies to
  undo/redo/`revert_batch` (collect every id in the removed block's markup;
  refuse if any pending card targets it or anchors on it via
  `data-anchor-after` or `data-anchor-container`), and call it from
  `delete_chunk` in place of the root-only `_cards_anchored_on` check. Check
  whether `aim_edit`'s delete path and accepted delete *proposals* (which
  dissolve anchored cards on purpose) need the same treatment for targeting
  cards on nested ids.
