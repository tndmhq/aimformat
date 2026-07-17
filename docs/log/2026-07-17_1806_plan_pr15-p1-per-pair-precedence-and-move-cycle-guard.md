---
date: 2026-07-17 18:06
type: plan
status: active
related: []
---

# Plan: PR #15 round-3 P1 fixes — per-pair move precedence, reconcile move cycle guard

Two Codex P1 findings on `fix/postmerge-sweep`, both in code added by this
branch's own review fixes. One logical commit per finding, red-first
regression tests, full gates green before each commit.

## 1. `resolution_order` — track move precedence per container modify

`7484dd4` collected the moves that force a delayed modify into a single
global `must_precede` union, so a landing move is treated as conflicting
with ANY delayed modify — including one it never forced. Lane
`x: l1→l2`, `z: l2→l3`, replacements of l1 and l2 dropping x/z is
resolvable (`move z → modify l2 → move x → modify l1`) but raised
"would erase moved chunk".

Fix: keep the forcing sets per modify (`forced_by[modify] = {move ids}`)
and judge each landing pair against that modify's own set. Non-forcing
landing moves trail the specific modifies they land in
(`trails[move] = {modify ids}`); scheduling becomes chain-aware
longest-path tiers over these two relations (bounded relaxation; a
non-converging cycle is refused before anything mutates) instead of the
fixed `after_moves`/`trailing_moves` tiers.

## 2. `_reject_dangling` — reject reconciled moves into their own subtree

`621b3c0` re-runs the member guard for pending moves at reconcile, but not
the cycle guard: an out-of-band edit nesting the destination container
INSIDE the moved subtree leaves a clean-verifying document whose pending
move explodes at accept ("cannot move … into itself or its own subtree").

Fix: after anchor + member validation, recompute the moved subtree's ids
(same walk as `DocState.move`) and mark the proposal dangling when the
destination container or after-anchor sits inside it.

Tests in `tests/test_review_regressions.py`
(`TestMovePrecedencePerContainerModify`,
`TestReconcileRejectsMovesIntoOwnSubtree`).
