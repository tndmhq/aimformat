---
date: 2026-07-18 01:50
type: plan
status: done
related: []
---

# Plan: pr16 trial clone index refresh

Fix the single Codex P2 at the seam between R-AF-1's incremental
`_HistoryIndex` and the creation-order validation added after PR #15.

1. Add a foreign-authored pending-lane regression in which an earlier add
   initially reserves a nested id, a later add uses that id, and amending the
   earlier payload drops it. Prove the current amend path rejects the valid
   lane before changing production code.
2. Mirror the amended card replacement into the trial clone's populated
   `_HistoryIndex` before creation-order replay, using the same proposal
   replacement operation as the live document.
3. Assert the amendment succeeds, `verify()` is clean after replay, and the
   live incremental index exactly matches a scratch rebuild.
4. Run the focused regression and existing history/property coverage, then
   ruff check, ruff format check, mypy, the full pytest suite at its healthy
   property budget, and `aim lint examples/*.aim`.
5. Mark this plan done and make one local commit without pushing.
