---
date: 2026-07-17 19:16
type: plan
status: done
related: []
---

# Plan: pr15 constraint ordering property

Two Codex P1 follow-ups expose adjacent failures in the same pending-lane
machinery: repeated moves need hop-local precedence around container modifies,
and reconcile must judge a move destination against the state produced by a
pending container modify. The fix will replace case-specific tiers with an
explicit proposal precedence graph derived from simulated per-proposal effects.

## Work

1. Add red-first concrete regressions for the retained-target repeated-move
   sequence (`first move -> modify -> final move`) and for reconcile retaining a
   move made legal by a pending destination modify (`modify -> move`).
2. Add a red-first Hypothesis property over small list-container lanes with
   repeated move chains, retaining/dropping container payloads, kind changes,
   and reconciled out-of-band mutations. It will compare ordering with safe
   acceptance: a returned lane must resolve without partial failure, duplicate
   or lost logical content, misplaced targets, lint/verify errors, or
   `TargetNotFound`; an impossible lane must be refused before mutation.
3. Refactor `resolution_order` into a coherent per-hop constraint system. Model
   immutable card-order edges between repeated moves plus move/modify edges
   induced by the exact pre/post state each modify supplies; topologically sort
   the graph and reject cycles before accepting anything. Preserve the existing
   per-pair precedence, trailing inbound move, self-subtree, and dangling
   fixpoint contracts.
4. Extend reconcile viability to consider pending modifies of the destination:
   retain a move when at least one schedulable pending replacement makes its
   member kind legal, while keeping current illegal/self-subtree destinations
   rejected when no such replacement exists.
5. Run focused tests and then `.venv` gates: `ruff check`,
   `ruff format --check .`, `mypy`, full `pytest`, and
   `aim lint examples/*.aim`. Commit the logical changes locally only, mark
   this plan done, update its index row, and leave the worktree clean.
