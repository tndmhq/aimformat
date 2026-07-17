---
date: 2026-07-17 16:43
type: plan
status: done
related: []
---

# Plan: revalidate rebound chained add anchors

Fix the PR #15 reconciliation finding without changing proposal semantics
outside the stale-snapshot path.

1. Add a focused regression in `tests/test_review_regressions.py`: a wrapping
   out-of-band edit makes add A's body anchor cross-container, add B is chained
   after A, and reconciliation rejects both rather than leaving B with P016.
2. Run that regression before the implementation change and record the
   expected failure.
3. Make `_reject_dangling()` re-read each still-pending proposal before anchor
   validation, so `_rebind_chained()` mutations made by earlier rejections are
   validated in the same pass, including longer chains.
4. Run the focused reconciliation tests, then Ruff check/format, mypy, the full
   pytest suite, and `aim lint examples/*.aim`.
5. Mark this plan done and commit the regression, fix, and technical log as one
   local commit. Do not push or perform any GitHub write.
