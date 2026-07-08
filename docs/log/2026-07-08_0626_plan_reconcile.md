---
date: 2026-07-08 06:26
type: plan
status: done
related: []
---

# Plan: reconcile

> **Outcome (same day):** implemented as planned on `feat/reconcile` —
> `src/aimformat/reconcile.py`, `AimDocument.reconcile()`, `aim reconcile`
> CLI verb, 30 tests in `tests/test_reconcile.py` (mock tampered/hand-written
> files with exact expected events); full suite green. One design change
> found during implementation: a theme block **no event ever touched**
> (constructor-set) has no recoverable baseline, so it is treated as
> untracked rather than flagged — otherwise every `new_document(theme=…)`
> file would false-positive and checkpoint hashes would break.

Implement the reconcile lifecycle operation (spec §6.8): detect out-of-band
edits — a body changed by hand or corrupted without matching history events —
and repair the document by synthesizing `direct_edit` events with
`author: {type: external}` and `origin: "reconcile"`, so the current body
becomes the declared truth and the chain verifies again. Also the adoption
path for hand-written `.aim`/HTML files with no history.

## Design

The correctness frame: reconcile computes an **edit script from the expected
state E to the actual body A**, expressed as ordinary events. Verification
walks inverses from A back through the new events to E, then through the old
events as before — so `verify()` is clean by construction.

1. **E (expected)** = forward replay of the full log over an emptied clone
   (payload-driven: `after`/`applied` + anchors; `aim:theme` removal = modify
   with `before` and no `after`, mirroring undo's shape). Requires the full
   log: **reconciling a pruned history raises** `HistoryError` — the baseline
   below the prune floor is unrecoverable, so a repair guarantee would be a
   false promise. A broken log (bad JSON, seq gaps, unknown kinds, events
   that don't replay) also raises: reconcile repairs bodies, not logs.
2. **A (actual)** = a clone of the live document after an **id fix-up pass**
   (ids are tooling's job, spec §4.4): assign fresh ids to unmarked
   constructs/items, to ids burned by history, to duplicates (first
   occurrence in document order keeps the id), and to invalid/reserved
   spellings. Reported as `assigned_ids`.
3. **Drive** a scratch document S from body E to body A through raw
   event-generating primitives (same data shapes as `add_chunk` & co., plus
   `origin: reconcile`), one batch: theme modify; deletes (reverse document
   order); whole-container modifies where the container's shell/attrs/
   skeleton changed (subsuming item diffs); per-chunk modifies; then a
   per-scope order walk emitting add/move with exact anchors (shell-aware
   for table rows). Pending proposals whose target/anchor vanished are
   rejected (`decided_by` the reconcile actor).
4. **Converge check**: S's `doc_hash` must equal A's, else internal error.
   Commit = swap the live document's tree for S's (no mutation on
   `dry_run=True`). Report: synthesized events, assigned ids, rejected
   proposals, and `residual` = remaining `verify()` problems (e.g. a
   hand-tampered checkpoint hash — detectable, not repairable append-only).

## Surface

- `AimDocument.reconcile(*, author=None, at=None, dry_run=False) ->
  ReconcileReport` (author defaults to `external()`).
- `ReconcileReport` exported from the package root.
- CLI verb: `aim reconcile FILE [-o OUT] [--check]` — `--check` = detect
  only, exit 1 on drift; fix mode writes and exits 1 only when residual
  problems remain.
- New module `src/aimformat/reconcile.py`; `document.py` gains the method +
  a `_recorded_ids()` split out of `_taken_ids()`.
- Tests: `tests/test_reconcile.py` — mock tampered/hand-written files with
  target results (expected events, `verify() == []`, no lint errors,
  `state_at` time travel across the reconcile boundary, idempotence, CLI).
- Docs: spec §6.8 + Appendix C status sentences, README ops table + roadmap,
  CHANGELOG, `docs/knowledge/architecture.md` module row.
