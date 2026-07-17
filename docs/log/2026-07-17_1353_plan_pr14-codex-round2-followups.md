---
date: 2026-07-17 13:53
type: plan
status: active
related: []
---

# Plan: pr14 codex round2 followups

1. Extend the AF-49 regression to exercise all four independent accept/reject
   outcomes for consecutive pending slide additions, then assign each shared
   page boundary so the surrounding accepted content remains paginated.
2. Put authored-file linting before normalization in the agent workflow, with
   a second lint after normalization to validate canonical output.
3. Reproduce a contentless cyclic Docling list edge, omit its empty synthetic
   wrapper, and preserve real nested lists.
4. Commit each finding separately, run all requested gates, complete this
   entry, and push `review-cleanup`.
