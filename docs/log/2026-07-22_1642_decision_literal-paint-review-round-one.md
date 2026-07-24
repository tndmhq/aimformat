---
date: 2026-07-22 16:42
type: decision
status: active
related:
  - 2026-07-22_0844_plan_literal-element-paint.md
  - 2026-07-22_1451_decision_literal-paint-implementation-choices.md
  - 2026-07-22_1559_plan_literal-paint-self-review-fixes.md
  - 2026-07-22_1606_decision_literal-paint-review-corrections.md
---

# Decision: literal-paint review round one

This entry supersedes the 16:06 review-corrections record after the first
automated PR review found six gaps. Its border-cascade, failed-edit, pending
parent-context, and tracked-box decisions remain in force. The body-root and
version-batch details are replaced below.

## 1. A declared version must cover every retained paint payload

The registry now names v0.3 as `paint_since`. S032 rejects an older declared
version when literal paint remains anywhere the file retains it: the live
content tree, pending templates, or history fields (`before`, `after`,
`proposed`, or `applied`). Accepting old documents is not permission to place
new syntax under an old declaration.

The first successful painted edit to an older document records the
`aim:version` event and the content event in one batch because they express one
editing intention. Failed preflight still records neither. Reconcile replays
reserved version events and records an out-of-band first-paint upgrade in the
same batch as its repair, so the repaired document converges and verifies.
Undo refuses to invert that upgrade while live, pending, or historical state
still retains paint; otherwise the SDK itself would produce S032. Time travel
below the upgrade remains valid because it removes the later events as well as
reconstructing the older marker.

## 2. Structural body attributes never affect rendering

`<body>` is container chrome, not an addressable content element. Its
attributes are outside the hashed body projection, so accepting paint there
would let rendering change without a tracked or verifiable state change. V003
therefore rejects `class`, `style`, and every other rendering attribute on
body. Export resolves each live construct rather than body; legal paint on a
content ancestor and exact future-parent context for pending payloads still
inherit normally.

## 3. Descendant base selectors participate in the cascade

The generated stylesheet includes type descendants such as `thead th` and
`pre code`. The resolver applies those base rules in stylesheet order before
class and inline declarations. Their values are still recipient-owned defaults
and are not emitted into DOCX, but they can stop inherited author paint exactly
as they do in a browser.

## 4. Grouping borders follow flattened DOCX output

Word has no wrapper box after a grouping `section`, `div`, `blockquote`, or
slide is unpacked. A grouping border therefore travels to every emitted block,
list item, or table cell, matching the established background approximation.
Direct descendant border sides override carried sides. This is a deliberate,
tested degradation rather than silent loss.

## 5. Earlier choices carried forward

The original implementation record's choices on lower-case literal grammar,
`hr`, history-verification refusal, registry version comparison, changelog
version, spec-fence language, grouping-background propagation, and S002/S006
remain active by reference. The 16:06 record's computed-border choice,
operation preflight, exact pending parent context, and tracked revision-box
approximation also remain active.
