---
date: 2026-07-17 14:47
type: plan
status: active
related: []
---

# Plan: postmerge pr12 pr13 review fixes

Address the five confirmed post-merge Codex findings from PRs #12 and #13 in
one new branch, without changing unrelated behavior.

1. Before every finding, check the real-workspace `HOLD` sentinel; stop after
   the current committed finding if it appears.
2. Reproduce each defect on `origin/main` with a focused regression in
   `tests/test_review_regressions.py`, then make the smallest implementation
   fix and keep the existing AF-04, AF-07, and codex-r3-4 regressions green.
3. Commit each finding independently as
   `fix(review): postmerge-<n> <one-liner>`.
4. Run targeted tests after each fix, then the complete Ruff, mypy, pytest,
   and example-lint gates before pushing.
5. Consolidate this entry, push `fix/postmerge-sweep`, and open (but do not
   merge or request review on) a draft PR whose status table maps all five
   findings to their commits.
