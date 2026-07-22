---
date: 2026-07-22 16:06
type: decision
status: active
related:
  - 2026-07-22_0844_plan_literal-element-paint.md
  - 2026-07-22_1451_decision_literal-paint-implementation-choices.md
  - 2026-07-22_1559_plan_literal-paint-self-review-fixes.md
---

# Decision: literal-paint review corrections

This entry supersedes the earlier implementation-choice record after a fresh
review against the confirmed plan. Its choices 2–10 remain in force unchanged;
choice 1 is replaced below. The review also settled three failure and export
details that the original record did not cover.

## 1. Export the computed border value after an explicit colour participates

A bare border utility still exports no colour: its default ink comes from the
stylesheet, not from a colour choice. Once a class or inline colour declaration
participates in that side's cascade, the converter exports the final computed
value. This remains true when a later shorthand resets the longhand.

For `class="border-t border-red-600"`, the browser computes the grey from
`.border-t`, which sorts after `.border-red-600` in the generated stylesheet.
Exporting red would report the losing declaration; exporting nothing would
omit paint after the author explicitly selected a colour. DOCX therefore gets
the computed grey top border. `class="border-t"` alone still gets no explicit
Word border.

## 2. A failed first-paint edit leaves an older document unchanged

The version-upgrade event must precede the edit that requires v0.3, but the
operation can still fail on its target, container, or payload shape. The write
path therefore proves the operation on a clone before recording the upgrade.
Only the first painted edit to an older supported document pays that cost.

## 3. Paint resolution starts at the live body and keeps the real parent

DOCX resolves the body once, so legal body paint reaches descendants. A pending
payload is resolved with the context of the exact container it will enter or
the exact DOM parent of the node it will replace. This preserves inheritance
for top-level proposals and replacements nested inside painted containers.

## 4. Tracked block and cell boxes ride their revision runs

Word stores paragraph and table-cell properties outside `w:ins` and `w:del`.
Writing proposed shading or borders there would make rejected content retain
the proposal's paint. Tracked exports therefore approximate each old and new
box with run shading and one whole-run border inside its matching revision.
Clean, `accept-all`, and `reject-all` exports keep exact paragraph and cell
paint.

## 5. Earlier choices carried forward

The earlier record's choices 2–10 remain active by reference: the `hr`
degradation, history-verification guard, version-event batching and reserved
target, registry version comparison, changelog version, spec-fence language,
grouping-background propagation, and updated S002/S006 tests are unchanged.
