---
date: 2026-07-17 19:46
type: plan
status: done
related: []
---

# Plan: pr15 move delete and linear ordering

Two Codex P1 findings on PR #15 expose a correctness hole and an algorithmic
regression in the holistic `resolution_order` rewrite: terminal validation
mistakes intentional deletion for a failed move, and exhaustive acceptance
search makes independent cards multiply the state space.

## Work

1. Add red-first concrete regressions for a move followed by deletion of the
   moved target and deletion of its final destination ancestor. Extend the
   permutation oracle with intentionally absent final targets.
2. Make terminal move validation delete-aware without weakening the final
   destination check for moved targets that should survive. Run every required
   `.venv` gate and commit this finding locally.
3. Add a red-first timing regression for eight unrelated modifies plus one
   incompatible move/modify pair, requiring dependency analysis to finish well
   under one second.
4. Replace recursive clone-and-replay search with an explicit proposal graph:
   chained-add and repeated-move edges, hop-local move/modify edges for erased
   sources and anchors, retained-target duplication, destination member
   legality, and final inbound moves. Topologically order it with Kahn's
   algorithm and reject cyclic lanes before live mutation; keep deletes last.
5. Run focused tests and the complete gate set before the second local commit:
   `ruff check`, `ruff format --check .`, `mypy`, full `pytest`, and
   `aim lint examples/*.aim`.
6. Mark this plan done, update the architecture note from search to graph
   ordering, confirm both commit boundaries and a clean worktree, and do not
   push or alter GitHub review state.
