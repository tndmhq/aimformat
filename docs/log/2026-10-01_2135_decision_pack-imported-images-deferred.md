---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_history-baseline-event.md, 2026-10-01_2135_decision_importers-record-baseline.md]
---

# Decision IMPORT-D15: pack imported images into the asset registry before baselining — deferred

**Status: proposed — awaiting founder approval.** Not implemented: this decision defers the work (see [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md) and the workspace TODO). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D15**. Related decisions: [IMPORT-D9](2026-10-01_2135_decision_history-baseline-event.md), [IMPORT-D10](2026-10-01_2135_decision_importers-record-baseline.md).

## Context

A baseline snapshot duplicates inline `data:` images (once in the body, once in
the snapshot). On sample3, 4 `data:` URIs (2 images, each in body and history)
take 22,624 of 35,584 tokens, so the baseline saves only 6.4% there. Packing the
images into the asset registry (§9) before baselining gives about −37%.

## Options considered

- Pack on import now — the biggest win on image-heavy documents, but changes how
  body images are represented (`<svg role="img"><use href="#asset-…">`).
- **Defer** — chosen.
- Never pack — leaves image-heavy imports large.

## Decision

Deferred. Not part of either PR. Revisit once the tndm editor's handling of
packed body images in its TipTap body is verified; then propose it as its own
decision (default output change for image-bearing imports).

## Consequences

Image-heavy imports keep their images inline and duplicated in the snapshot until
this lands. Recorded in `TODO.md`.

## Compatibility and migration

None now. When implemented: no format change (the asset registry already exists),
but a default-output change for importers.

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
