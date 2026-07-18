---
date: 2026-07-17 20:42
type: plan
status: done
related: []
---

# Plan: pr15 r9 complete ordering model

Round 9 exposes three adjacent gaps in the pending-lane constraint model: an
anchor dependency that mistakes carried ancestors for separation, reconcile
viability that ignores a pending move able to rescue a nested destination, and
destination membership checked against a moved member's obsolete pre-modify
shape. The property test must become a complete small-lane reference model so
future interaction failures surface locally instead of in successive review
rounds.

## Work

1. Add red-first regressions for carried anchor containers, pending-move
   destination rescue after reconcile, and retained members whose carrier kind
   changes incompatibly before their move.
2. Expand the Hypothesis generator across repeated move hops, cross-target
   anchors and carried/separated anchor containers, source/destination
   replacements, deletes, reconciled nesting/wrapping/kind changes, and pending
   rescue moves. Keep the oracle independent: enumerate every card permutation
   only in the test reference model, accept on cloned documents, and decide
   whether any order reaches the requested terminal placements with exact
   logical content, clean `verify()`, and clean lint.
3. Refine the production precedence graph without search: emit anchor edges
   only for moves that separate an anchor from the destination container;
   preserve reconcile moves when an earlier pending move can rescue their
   destination; and validate destination compatibility against post-modify
   retained-member shapes before returning an order.
4. Run the property at a high example budget and fix any additional cases it
   exposes while preserving repeated-hop, per-pair, destination-modify,
   self-subtree, trailing-inbound, move-delete, dangling-fixpoint, and linear
   topological-sort contracts.
5. Before every local logical commit, run `.venv/bin/ruff check`,
   `.venv/bin/ruff format --check .`, `.venv/bin/mypy`, full pytest (including
   the property suite), and `.venv/bin/aim lint examples/*.aim`. Mark this plan
   done, update the index and architecture knowledge, audit the final commits
   and clean worktree, and do not push.
