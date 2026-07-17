---
date: 2026-07-17 14:19
type: plan
status: active
related: []
---

# Plan: pr14 codex round3 minors

1. Reproduce a contentless Docling cycle that passes through an empty list
   item, then omit the empty nested-list wrapper without affecting real nested
   lists.
2. Derive first-heading image alt text from parsed inline children so Markdown
   formatting markers do not leak into the document title.
3. Give the two optional Markdown regression tests the same `markdown_it`
   skip guard used by the converter suite.
4. Commit each finding separately, run the requested full and core-only gates,
   complete this entry, and push `review-cleanup`.
