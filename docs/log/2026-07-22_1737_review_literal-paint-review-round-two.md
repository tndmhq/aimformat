---
date: 2026-07-22 17:37
type: review
status: done
related:
  - 2026-07-22_1642_decision_literal-paint-review-round-one.md
---

# Review: literal paint review round two

Codex reviewed commit `0723190` on PR #26 in review `4756940701`. The review
body contained five findings and no inline comments. Each finding reproduced
against the reviewed commit:

1. A detached pending row had inherited paint but lacked its future selector
   ancestry, so `thead th` did not mask a table background.
2. An amendment that first introduced paint recorded a version-upgrade batch
   but left the proposal card in its old batch.
3. Clean DOCX paragraph and cell shading leaked an authored ancestor
   background behind `code`'s opaque base background.
4. S032 inspected malformed history before the guarded history pass, which
   collapsed the useful H002 result into S000.
5. Rejection and supersession retained a foreign paint-bearing `proposed`
   payload without upgrading an older declared version. Accepting an
   unpainted tweak had the same problem because it also retained `proposed`.

The fixes carry exact future tag ancestry into detached paint resolution and
use a pending table row's recorded shell as its parent. Clean DOCX export now
falls back to run shading when one box shade cannot represent a descendant
base-background blocker. Proposal amendment and every resolution path share
the required version-upgrade batch. The S032 precheck defers malformed history
to H002.

All five fixes were added red-first. The final local run passed 1,134 Python
tests with 3 skipped, 86 TypeScript tests, Ruff, mypy, TypeScript typecheck,
and Prettier. All six generators completed; their random identifier churn was
discarded because registry and generator inputs did not change. Independent
acceptance produced one paint-only proposal and no theme proposal, then
verified HTML, PDF, and DOCX output, undo/redo, rejection, and exporter
non-mutation. Chromium matched the resolver on the cascade matrix and rendered
the positioned title as `rgb(255, 105, 180)` while the separate brand-linked
subtitle remained `rgb(29, 78, 216)`.
