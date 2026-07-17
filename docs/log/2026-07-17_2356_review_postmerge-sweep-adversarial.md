---
date: 2026-07-17 23:56
type: review
status: done
related: [2026-07-17_2304_plan_pending-lane-creation-order.md]
---

# Review: adversarial pass over the creation-order acceptance redesign

Local adversarial review of the five redesign commits (`442d6ae..4ea04a4`,
branch `fix/postmerge-sweep`). Five real bugs found, each fixed red-first
with a regression test in `tests/test_review_regressions.py`:

1. **No-op guards ran against the projection** — the *legal* order of an
   interacting chain (move z out of l2, then modify l2 without z) was
   rejected as "modify with identical content", so both orders of the chain
   failed. Meaningfulness is now judged against the current document only
   (`_NoOpEdit` + `_noop_guard`); the projection tolerates edits that
   become no-ops once earlier pendings resolve.
2. **The projection applied cards the new proposal supersedes** — a pending
   delete made its own §5.4 replacement (re-propose modify/delete on the
   same target) look illegal at propose time. Superseded cards are now
   excluded from the projection.
3. **Proposal targets could be projection-only** — propose_modify/move/
   delete accepted targets that exist only inside a pending add's payload,
   writing P008-lint-error cards; the modify variant also reserved the
   add's root id, so accept-all rejected the lane propose had validated.
   Targets must now exist in the current body.
4. **Recorded add anchors could be projection-only** — a projected position
   resolving to a pending add's root was written as a concrete chunk
   anchor (P011 lint error). It is now recorded as the sanctioned
   proposal-id chain (AIM-03, rebinding intact); other projected-only
   positions (pending-container members, behind a pending move-in) are
   refused, mirroring P011/P016.
5. **Amend could poison a validated lane** — an amended container-modify
   payload dropping a member a later pending move anchors on passed amend
   and only failed at accept-all. Amend now replays the whole lane on a
   clone and fails closed naming the card it would break.

Verified clean: accept-all dry-run atomicity (seq/history/body untouched on
failure), clone fidelity, chained-add rejection rebinding, partial manual
resolution + accept-all remainder, repeated moves, undo/redo round-trip over
accept-all, reconcile fail-closed paths (reshaped container, vanished
anchors, rescue lanes), and no new `x_*` vendor fields (the branch only
removed them; `x_remove` in undo pre-dates it).

Residual (accepted): propose-time projection validation is O(doc × pending)
clones per propose call (inherent to naming the first conflicting card);
move-card anchors are not lint-checked (pre-existing lint scope), so a move
anchored on a chunk whose creating add is later rejected fails only at
accept, fail-closed.
