---
date: 2026-07-17 18:47
type: plan
status: done
related: []
---

# Plan: pr15 p1 repeated moves before erasing modify

Codex PR #15 P1 in `src/aimformat/document.py::resolution_order`: conflict
analysis currently keeps only the final move per target. That is sufficient
to decide the target's final location, but not to protect it from an erasing
container modify during an earlier hop. The lane `x outside l2`,
`move x -> l2 after a`, `move x -> first in l2`, `modify l2 -> empty`
currently sorts `move -> modify -> move`; accepting in that order deletes
`x`, then the final move raises after the document has already mutated.

## Implementation

1. Add a focused red-first regression in
   `tests/test_review_regressions.py` that asserts all repeated moves precede
   the erasing modify and that accepting the full order resolves atomically
   with clean verification.
2. Extend the per-(move, modify) precedence graph to consider every hop of a
   repeated-move sequence for erase safety, while preserving final-move-only
   trailing behavior for unrelated inbound moves. If the resulting
   constraints cycle, retain the existing pre-mutation lane refusal.
3. Re-run the focused resolution-order regressions, then `ruff check`,
   `ruff format --check .`, `mypy`, full `pytest`, and
   `aim lint examples/*.aim` from `.venv`.
4. Mark this plan done and create one local commit. Do not push.
