---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_import-tracked-changes-as-proposals.md, 2026-10-01_2135_decision_import-tracked-chunk-alignment.md]
---

# Decision IMPORT-D8: the importer writes the lane through a shared internal batch-propose primitive, validates once, then degrades or refuses

**Status: proposed — awaiting founder approval.** Implemented, uncommitted, on `wt/aim-import-tracked`; deviations are listed in the [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D8**. Related decisions: [IMPORT-D1](2026-10-01_2135_decision_import-tracked-changes-as-proposals.md), [IMPORT-D2](2026-10-01_2135_decision_import-tracked-chunk-alignment.md).

## Context

The public `propose_*` path is quadratic: each call replays all earlier cards on
a clone (`_projected_operation`). Measured on a 200-chunk document: 25 cards
14.2 s, 50 cards 49.5 s, 100 cards **203 s**. Bulk-writing a lane and validating
it once is cheap (800 chunks, 300 cards): parse 0.02 s, lint 0.8 s, `accept_all`
4.8 s, `reject_all` 1.9 s.

## Options considered

- (a) Call `propose_*` per card — quadratic, unusable on real redlines.
- (b) An import-private bulk writer — a second proposal path that must stay
  byte-identical with `propose_*` and skips its P-rule checks; the parallel path
  the project rules warn against.
- (c) **A shared internal batch-propose primitive plus one-shot validation.**
- (d) Bulk-write with no validation — would let alignment bugs ship wrong
  outcomes silently.

## Decision

(c).

- The primitive shares id minting, card serialization and the P-rule checks with
  `propose_*`, and runs the lint proposals pass on its result. It is the same
  primitive the planned public batch operations will expose.
- Validation runs on a clone: `accept_all()` must reproduce the adjusted accept
  view (IMPORT-D2), `reject_all()` must reproduce the body, and the lint proposals
  pass must be clean. Markup is compared with ids stripped, construct by construct.
- On a mismatch, fall back to top-level alignment (whole-container `modify`, still
  citing its revisions) and validate again. If that fails too, raise `ParseError`
  naming the first diverging paragraph. Content is never dropped silently.

## Consequences

- A malformed or exotic DOCX that today imports as silently wrong text may now
  raise a clear `ParseError`. **tndm editor:** its upload path already turns a
  `ParseError` into a rejected upload carrying the message.
- Import time grows by one validation: about 7 s at 800 chunks with 300 cards.
- Follow-up recorded in `TODO.md`: the O(n²) projection cost of `propose_*`
  (an incremental projection cache touches lane resolution code with a long review
  history, so it is not on this change's critical path).

## Compatibility and migration

No format change. Failure behaviour of the importer changes (degrade, then
refuse). Ships in SDK 0.5.3.

## Tests

Performance guard: 800 chunks with 300 changed paragraphs imports in under 15 s.
Hypothesis property test: random revision markup over generated paragraph lists
keeps `accept_all` = accept view, `reject_all` = reject view, lint clean.

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
