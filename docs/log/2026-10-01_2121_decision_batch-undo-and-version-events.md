---
date: 2026-10-01 21:21
type: decision
status: proposed — awaiting maintainer approval
related: [2026-10-01_2121_decision_auto-accept-policy-in-aim-doc.md, 2026-10-01_2121_decision_auto-accept-resolution-marker.md, 2026-10-01_2121_decision_auto-accept-version-floor.md, 2026-10-01_2121_decision_auto-accept-sdk-batch-close.md, 2026-10-01_2121_decision_auto-accept-guards.md, 2026-10-01_2121_decision_auto-accept-agent-surfaces.md, spec.md]
---

# Decision: whole-batch undo, `revert_batch`, and undo never inverts `aim:version`

**Decision ids:** AA-D10 (as extended), AA-D29.
**Status:** proposed, awaiting maintainer approval (draft PR, not merged).

## Context

An auto-accepted agent turn is one batch, and the natural "undo that" means
the whole batch. The existing undo model is a stack: `undo()` inverts the
newest not-yet-undone state-changing event; undo events carry
`origin: "undo"` and no pointer to what they invert, so the stack can only be
unwound from the top.

Two problems:

1. As soon as anyone edits after the batch (an editor autosaving keystrokes
   writes `direct_edit`s within seconds), the batch is no longer on top, so a
   top-of-stack undo of it is impossible. That is the main scenario, not an
   edge case.
2. `aim:version` upgrade events are state-changing `direct_edit`s whose
   inverse can never apply while history retains the gated construct
   (`_apply_data` refuses). A plain undo that walks back past one raises and
   blocks all older undos. With auto-accept, the first per-call accept on a
   0.5 document puts one inside the batch.

## Options considered

1. **Top-of-stack only.** Rejected alone: undo dies the moment anyone types.
2. **Selective undo with a new `undoes: <seq>` field on undo events.**
   Correct stack semantics for out-of-order undo, but a spec change. Deferred
   to the backlog.
3. **Top-of-stack whole-batch undo when possible, otherwise a conflict-checked
   revert written as ordinary edits.** Chosen. No spec change.

## Decision

**AA-D10, stack operations.**

- `undo(*, author, at=None, whole_batch=False, batch=None)`: with
  `whole_batch=True`, take the undo candidate plus every further candidate
  sharing its `batch`, contiguous from the top; invert newest first, one
  `origin: "undo"` event per target, all in one new batch, after a dry run on
  a clone. `batch=` states which batch is meant; a mismatch raises
  `InvalidOperation`.
- `redo(*, author, at=None, whole_batch=False)`: redo every uncancelled undo
  of the newest undo batch.
- Undo candidate selection and the redo walk **skip `aim:version` events**.
  Undoing a batch leaves its upgrade in place; the document stays at the
  higher version. This also fixes today's bug where undo past a paint upgrade
  blocks the stack.
- Spec §6.6 (informative): tools may group undo by batch; `aim:version`
  events are never inverted by undo.

**AA-D29, `revert_batch(batch, *, author, at=None) -> list[Event]`.**

- Batch on top: delegates to `undo(whole_batch=True, batch=batch)` (true undo
  events; redo works).
- Otherwise: writes the batch's inverse as ordinary direct edits
  (`origin: "user"`, explanation "Reverted auto-accepted changes from batch
  bNN") in one new batch, newest first, all or nothing after a dry run.
- Refuses only on a genuine conflict, a target the batch touched that changed
  since: modify (current serial is not the event's `after`/`proposed`), add
  (the chunk is not what was added), delete (the id is back or the anchor is
  gone), move (the chunk is no longer at the destination).
- For `aim:doc` it restores `page` and keeps the live `review`
  ([auto-accept-guards](2026-10-01_2121_decision_auto-accept-guards.md)). `aim:version` events are skipped.
- A redo of a revert is a plain undo of the revert batch, which is on top by
  then.
- `auto_accepted_batches()` reports `revertable` (a cheap per-target serial
  comparison) instead of `undoable`.

## Consequences

- No spec change beyond the informative §6.6 sentences.
- Backlog: true undo-stack semantics (an `undoes` pointer) for batches that
  are not on top.
- Tests: whole-batch undo and redo; `batch=` mismatch refused; `aim:version`
  skipped so a following plain undo reaches older edits; `revert_batch`
  succeeds after an unrelated later edit and refuses after an edit to a
  touched chunk; `verify()` clean after each.
