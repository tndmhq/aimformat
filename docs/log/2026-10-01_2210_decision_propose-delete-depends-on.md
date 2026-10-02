---
date: 2026-10-01 22:10
type: decision
status: todo  # proposed — awaiting maintainer approval
related:
  - 2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md
  - 2026-10-01_2210_report_docx-roundtrip-implementation.md
---

# Decision: `propose_delete(..., depends_on=)`

**Status:** proposed — awaiting maintainer approval. Implemented on
`wt/aim-docx-roundtrip` as part of the DOCX round trip (G3).

## Context

ROUNDTRIP-D6 links the cards of a colleague's merge: when two paragraphs are
joined in Word, the import proposes a *modify* of the survivor and a
*delete* of the other, and the delete must say it depends on the modify.
Accepting the delete alone loses the joined text; spec §5.4 makes
independent accept/reject the product, so the link is how an editor knows to
group and warn.

`data-depends-on` is already legal on every card (spec §5.4, lint P012), and
`propose_modify`, `propose_add` and `propose_theme` accept `depends_on=`.
`propose_delete` did not, so the SDK could not write a valid card the format
already allows.

## Options considered

1. **Add `depends_on=` to `propose_delete`** — matches the other proposal
   kinds; no format change.
2. Make the modify depend on the delete instead — wrong direction: the
   hazard is the delete applied without the modify.
3. Write the attribute by hand after creating the card — an unrecorded
   in-place card mutation outside the SDK's own writers.

## Decision

Option 1: `propose_delete(target, *, author, explanation=None,
depends_on=None, at=None)`. `propose_move` is unchanged (nothing needs it
yet).

## Consequences

- Public SDK surface grows by one keyword argument; existing calls are
  unaffected (it is keyword-only and defaults to `None`).
- The tndm editor sees delete cards carrying `data-depends-on` when it
  reads a lane written by `import_revision`; editors already group and warn
  on the attribute for other card kinds.

## Compatibility and migration

None needed. No `.aim` grammar change; files written before this change are
untouched, and older readers already accept the attribute.
