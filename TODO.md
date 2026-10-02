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
