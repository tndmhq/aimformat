---
date: 2026-07-17 13:34
type: plan
status: active
related: []
---

# Plan: pr14 codex followups

1. Extend AF-49's DOCX regression coverage for a pending slide between
   accepted blocks, then make its trailing page break part of the same tracked
   insertion unless accepted structure already requires that break.
2. Narrow the normalization claim in `CHANGELOG.md` and recommend linting the
   authored input before normalization can discard invalid declarations.
3. Commit each finding separately as `fix(review): ...`, run every requested
   CI gate, mark this plan done, and push `review-cleanup`.
