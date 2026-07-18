---
date: 2026-07-17 12:26
type: plan
status: done
related: []
---

# Plan: AF-06 empty registry SVG canonical form

Address the remaining Codex finding on PR #13 without changing C002's
already-correct HTML-only scope.

1. Clarify spec §11.1 as three disjoint canonical rules: slashless HTML void
   elements, explicit tags for HTML non-void elements, and self-closing empty
   SVG-context elements.
2. Add a regression proving an empty asset-registry SVG round-trips as `/>`
   and does not produce C002; correct only the full-document block-layout path
   if the regression exposes a mismatch with inline serialization.
3. Run the focused regression and every requested CI gate, then consolidate
   this entry, commit the minimal fix, and push
   `fix/af-06-canonical-self-closing`.
