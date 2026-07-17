---
date: 2026-07-17 20:18
type: plan
status: done
related: []
---

# Plan: pr15 cross target move anchor constraints

Two Codex findings expose one missing dependency class in the linear pending-lane
model: a move's destination anchor is mutable state when another proposal moves
that anchor, and a pending destination replacement is viable only if it retains
the move's recorded anchor as well as a legal member kind.

## Work

1. Add a red-first regression for a move anchored after another target that a
   sibling move relocates, including the retained-source modify that currently
   causes partial live mutation. Extend the Hypothesis lane generator and its
   independent permutation oracle with cross-target anchored moves so the
   family, rather than only the concrete repro, proves atomic resolution.
2. Add cross-target move-anchor precedence edges to the existing proposal graph:
   every move using node `N` as `after=` must precede every move card relocating
   `N`. Preserve repeated-move card order and let Kahn's existing cycle path
   reject mutually impossible lanes before live mutation. Run all required
   `.venv` gates and make the first local commit.
3. Add a red-first reconcile regression where an out-of-band incompatible
   destination is restored by a pending modify that removes the recorded
   anchor. Extend the property generator's reconciled-destination family to
   vary anchored versus first-position moves and candidate anchor retention.
4. Validate `after=` and table `shell=` against each candidate destination
   payload before treating its member kind as viable. Reuse the same scoped
   anchor semantics as resolution ordering; retain the fixpoint, self-subtree,
   membership, and pending-modify behavior. Run every required gate and make
   the second local commit.
5. Mark this plan done, update its index row, promote the cross-target-anchor
   invariant into `docs/knowledge/architecture.md`, verify both commit
   boundaries and a clean worktree, and leave the branch unpushed.
