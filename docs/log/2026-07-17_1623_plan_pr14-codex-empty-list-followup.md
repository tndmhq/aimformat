---
date: 2026-07-17 16:23
type: plan
status: done
related: []
---

# Plan: pr14 codex empty list followup

1. Add focused regressions showing that a noncyclic blank Docling list item
   remains an empty `<li>` while a contentless cyclic nested-list edge is
   omitted.
2. Make the smallest ingestion change that distinguishes those two recursive
   outcomes without changing ordinary nested-list rendering.
3. Run the focused tests and every requested repository gate, complete this
   log entry, then create one `fix(review): ...` commit and push
   `review-cleanup` to `origin`.
