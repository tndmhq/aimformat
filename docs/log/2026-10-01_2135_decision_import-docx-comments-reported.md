---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_import-tracked-changes-as-proposals.md, 2026-10-01_2135_decision_import-tracked-formatting-changes.md, 2026-10-01_2135_decision_import-report-api.md]
---

# Decision IMPORT-D6: Word comments are reported, never stored; their anchored text stays intact

**Status: proposed — awaiting founder approval.** Implemented, uncommitted, on `wt/aim-import-tracked`; deviations are listed in the [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D6**. Related decisions: [IMPORT-D1](2026-10-01_2135_decision_import-tracked-changes-as-proposals.md), [IMPORT-D4](2026-10-01_2135_decision_import-tracked-formatting-changes.md), [IMPORT-D7](2026-10-01_2135_decision_import-report-api.md).

## Context

Today a Word comment is silently dropped (its anchored text survives). Spec §11.5
has no comment construct, and this change must not invent one.

## Options considered

- (a) Report only.
- (b) A new comment construct — out of scope; a separate design (backlog item G5).
- (c) Carry comments inside proposal explanations, the agent note or the head —
  rejected: it invents a construct through the back door and pollutes fields with
  other meanings.

## Decision

(a).

- The seam parses `comments.xml`, plus `commentsExtended.xml` when present
  (resolved state, reply threading). Both parts are parsed like the existing ones:
  entity resolution off, network off, size-limited.
- The import report carries, per comment: `id, author, date, text, anchor_text,
  chunk_id, resolved, parent_id` (strings sanitized and capped as in IMPORT-D5).
- Anchor text comes from whichever view contains the range (a comment on inserted
  text has an empty range in the reject view). `chunk_id` is the O chunk, or the
  pending card's id for text that exists only in the accept view.
- Everything the import does not carry also raises `AimImportWarning` (one per
  warning group): comments; property changes the format cannot express; converter
  differences; revisions inside parts that are not imported (headers, footers,
  footnotes, endnotes, and body-level `w:sdt` until the content-control fix lands).
- The anchored text itself is always kept.

## Consequences

- Comments are lost on a DOCX → .aim → DOCX round trip, but visibly.
- **tndm editor:** follow-up — switch the upload path to `import_docx` and show
  "N Word comments were not carried" to the user; log warnings alone are invisible
  to them.

## Compatibility and migration

No format change. Ships in SDK 0.5.3. The comment construct (G5) stays in
`TODO.md`.

## Tests

Fixtures for a comment on inserted text and a comment with a reply; a snapshot
test of the report on a Word-authored redline of `legal-addendum.docx` (about 20
tracked edits by two users plus a comment; provenance recorded in
`tests/fixtures/docxs/README.md`).

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
