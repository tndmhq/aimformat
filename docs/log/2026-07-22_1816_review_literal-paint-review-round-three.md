---
date: 2026-07-22 18:16
type: review
status: done
related:
  - 2026-07-22_1737_review_literal-paint-review-round-two.md
---

# Review: literal paint review round three

Codex reviewed commit `5e230cf` on PR #26 in review `4757358281`. All three
findings reproduced against that commit:

1. Reconcile accepted a hand edit that added paint and also changed the
   declared version. Because replay began from the already-bumped marker, no
   version event was synthesized and earlier checkpoints remained broken.
2. S032 deferred malformed JSONL but still let a `ParseError` from malformed
   retained markup collapse lint into S000 before the history pass.
3. DOCX cleared inherited parent colour from external-link text but painted
   the exporter-generated ` (URL)` suffix with the parent's colour.

Reconcile now refuses the ambiguous hand-bumped case before mutating the
document and tells the caller to restore the old marker so the normal upgrade
path can record it. The S032 precheck defers retained-markup parse failures to
H006. External URL suffixes use the link's paint-adjusted formatting.

Each fix has a focused red-first regression. The final local run passed 1,138
Python tests with 3 skipped, 86 TypeScript tests, Ruff, mypy, TypeScript
typecheck, and Prettier. Independent acceptance again produced one paint-only
proposal and no theme proposal, then verified pink HTML/PDF/DOCX output,
unchanged brand-linked content, undo/redo, rejection, and exporter
non-mutation.
