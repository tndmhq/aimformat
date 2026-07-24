---
date: 2026-07-22 15:59
type: plan
status: done
related:
  - 2026-07-22_0844_plan_literal-element-paint.md
  - 2026-07-22_1451_decision_literal-paint-implementation-choices.md
---

# Plan: literal-paint self-review fixes

The implementation on PR #26 passes its existing suite, but a fresh review
against the confirmed plan reproduced five behavioural gaps:

1. A failed painted `add_chunk` upgrades a v0.2 document before validating
   its destination. The operation raises, yet the document stays at v0.3 with
   an `aim:version` event even though no paint was added.
2. DOCX resolves each body construct as a root. Legal paint on `<body>` does
   not reach its children, and a top-level pending payload is resolved without
   the body context.
3. A pending replacement of a container is resolved without its actual parent
   context. Replacing a list inside a painted slide therefore loses the
   inherited colour in the tracked insertion.
4. `class="border-t border-red-600"` renders a grey top border in a browser,
   but DOCX emits no border. Suppressing a shorthand's default ink is correct
   when no colour was requested; once a colour declaration participated in
   the cascade, the computed winner must cross the format boundary, including
   a later shorthand reset.
5. Tracked block/cell box paint sits on `w:pPr`/`w:tcPr`, outside the revision
   runs. In a cell replacement only the proposed shading is written, so
   rejecting the Word revision leaves the old text under the proposed paint.
   The confirmed plan requires old and new box paint to ride the deleted and
   inserted runs as a documented approximation; accept-all and reject-all
   keep exact paragraph/cell paint.

## Implementation

- Add failing regressions for each case before changing implementation.
- Preflight every first-paint write that can still fail after the version
  event, and keep a failed operation byte-identical.
- Resolve the live body once. Resolve pending payloads against the exact DOM
  parent they will replace or the exact container they will enter.
- Keep an explicit-colour signal through later shorthand resets, while a bare
  border utility remains unpainted in Word.
- Put tracked box paint on revision runs and omit paragraph/cell box paint in
  tracked paths. Keep the clean export mappings unchanged.
- Correct the public DOCX degradation note and the stale v0.2 architecture
  heading, then run every generator and the full Python/TypeScript gates.

No format syntax, grammar, canonical order, cascade, or version decision
changes in this batch.

## Verification

- Added regressions for failed painted add, modify, and accept operations;
  body and pending-parent inheritance; shorthand-reset border colour; and
  tracked paragraph/cell box paint.
- Re-ran all six generators. Deterministic artifacts remained unchanged; the
  documented random fixture/proposal ids and their dependent hashes/goldens
  refreshed together.
- Python: 1,111 passed, 3 skipped. TypeScript: 86 passed.
- Ruff check and format, mypy, TypeScript type checking, Prettier, and
  `git diff --check` all passed.
