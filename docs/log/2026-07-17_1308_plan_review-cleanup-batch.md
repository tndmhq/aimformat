---
date: 2026-07-17 13:08
type: plan
status: active
related: []
---

# Plan: review cleanup batch

Fix the 15 confirmed findings assigned from the July deep review. Keep each
change narrow and commit it separately as `fix(review): AF-XX ...`.

1. Add focused regressions and fixes for AF-53, AF-49, AF-50, AF-51, AF-52,
   and AF-11, checking the batch HOLD file before starting each finding.
2. Correct the specified canonicalization, generated-spec, API, changelog,
   agent-guide, architecture, and example documentation drift in AF-58 through
   AF-66. Regenerate Appendix A for generator changes.
3. Run the focused tests after each code fix, then run `ruff check`,
   `ruff format --check .`, `mypy`, `pytest`, and `aim lint examples/*.aim`.
4. Mark this plan done, push `review-cleanup`, and open the requested draft PR
   with a status and commit row for every finding.
