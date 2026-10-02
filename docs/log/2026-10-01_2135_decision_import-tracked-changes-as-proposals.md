---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_import-tracked-chunk-alignment.md, 2026-10-01_2135_decision_import-tracked-moves.md, 2026-10-01_2135_decision_import-tracked-formatting-changes.md, 2026-10-01_2135_decision_import-tracked-attribution.md, 2026-10-01_2135_decision_import-docx-comments-reported.md, 2026-10-01_2135_decision_import-report-api.md, 2026-10-01_2135_decision_import-batch-propose-and-validate.md, 2026-10-01_2135_decision_import-versioning-and-sequencing.md]
---

# Decision IMPORT-D1: DOCX tracked changes import as pending proposals on the original body; new `tracked=` option

**Status: proposed — awaiting founder approval.** Implemented, uncommitted, on `wt/aim-import-tracked`; deviations are listed in the [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D1**. Related decisions: [IMPORT-D2](2026-10-01_2135_decision_import-tracked-chunk-alignment.md), [IMPORT-D3](2026-10-01_2135_decision_import-tracked-moves.md), [IMPORT-D4](2026-10-01_2135_decision_import-tracked-formatting-changes.md), [IMPORT-D5](2026-10-01_2135_decision_import-tracked-attribution.md), [IMPORT-D6](2026-10-01_2135_decision_import-docx-comments-reported.md), [IMPORT-D7](2026-10-01_2135_decision_import-report-api.md), [IMPORT-D8](2026-10-01_2135_decision_import-batch-propose-and-validate.md), [IMPORT-D14](2026-10-01_2135_decision_import-versioning-and-sequencing.md).

## Context

`from_docx` silently drops or corrupts every Word tracked change (data loss).
Root cause: the DOCX parse layer (`docx-parser-converter==1.0.3`,
`parse_paragraph`) handles only `pPr`, `r`, `hyperlink`, `bookmarkStart` and
`bookmarkEnd`; every other child of `w:p` — `w:ins`, `w:del`, `w:moveFrom`,
`w:moveTo`, comment range marks — is skipped, and `w:delText` has no run parser.
`_docx_seam._body_content_pairs` mirrors that walk, so nothing inside a revision
wrapper reaches `_docx_in`. `w:rPrChange` / `w:pPrChange` are ignored (the new
formatting is applied with no trace) and `w:trPr/w:ins|w:del` rows import with
empty cells.

Measured on a 12-situation probe ("Accept All" / "Reject All" = ECMA-376 Part 1
§17.13.5 semantics):

| Situation | `from_docx` today |
|---|---|
| inline `del`+`ins` (Alice) plus `ins` (Bob) in one paragraph | "…within  days." — matches **neither** version |
| whole inserted / deleted paragraph | dropped (≈ reject / ≈ accept) |
| split / merge (inserted / deleted paragraph mark) | ≈ accept / ≈ reject |
| move (`moveFrom`…`moveTo`) | clause **gone from both places** |
| `rPrChange`, `pPrChange` | new formatting silently accepted |
| table row inserted / deleted | row with **empty cells** |
| Word comment | anchored text intact, comment silently gone |

Our own round trip is broken too: an `.aim` with 4 chunks and 3 pending
proposals → `to_docx(pending="tracked")` → `from_docx` gives 2 chunks and 0
proposals.

## Options considered

| Option | Fidelity | Verdict |
|---|---|---|
| (a) Two views: the reject view becomes the body, the difference to the accept view becomes pending proposals | accept-all and reject-all equal Word's (scoped exceptions in IMPORT-D4) | **chosen as default** |
| (b) Import the accept view only | loses the review | offered as `tracked="accept"` |
| (c) Import the reject view only | drops the reviewers' work | offered as `tracked="reject"` |
| (d) Refuse documents that carry revisions | no data loss, but no import either | rejected |
| (e) One card per `w:ins`/`w:del` run | finest grain | impossible: payloads are whole-target (spec §5.1) and §5.4 allows one pending modify per target |

A prototype of (a) on both probes gave `accept_all()` equal to the accept view,
`reject_all()` equal to the reject view, clean lint, and a tracked re-export that
resolves text-equal to the source.

## Decision

(a) by default; (b) and (c) through a keyword, because the two-view design
yields them for free and they are what a caller who wants only one final text
needs.

- `from_docx(path, *, title=None, lang="en", author=None, theme=None,
  tracked: Literal["propose", "accept", "reject"] = "propose",
  max_revisions: int = 5000)`, mirrored in `convert_docx`, `from_path` and the
  new `import_docx` (IMPORT-D7).
- CLI: `aim import FILE.docx [--tracked propose|accept|reject]`.
- The body is the **original** (reject view), so `.aim` Accept All equals Word's
  Accept All and Reject All equals Word's Reject All — except baked numbering
  labels (they keep the original numbering) and formatting the format cannot
  express; both are reported (IMPORT-D4, IMPORT-D6).
- If the document carries no revision markup, the existing single-view path runs
  unchanged.
- `max_revisions` bounds work on hostile input: above it the import refuses with a
  `ParseError` that names `tracked="accept"|"reject"` as alternatives.

Mechanics (detection, source-identity stamping, view resolution) live entirely in
`_docx_seam.py`, which stays the only module that touches OOXML: revisions
detected include `w:ins`, `w:del`, `w:moveFrom`, `w:moveTo` (with range marks),
`w:rPrChange`, `w:pPrChange`, `w:trPr/w:ins|w:del`, `w:cellIns`, `w:cellDel`,
`w:cellMerge`, `w:tblPrChange`, `w:trPrChange`, `w:tcPrChange`,
`w:sectPrChange`, `w:delInstrText`, `w:numberingChange` and
`w:customXml{Ins,Del,MoveFrom,MoveTo}Range*`. View rules: run content survives
in the accept view iff it has no `w:del`/`w:moveFrom` ancestor, in the reject view
iff it has no `w:ins`/`w:moveTo` ancestor (nesting composes); `w:delText` →
`w:t` in the reject view; a paragraph mark marked inserted (deleted) does not
exist in the reject (accept) view and its content joins the following paragraph,
whose mark and `pPr` survive; `*PrChange` holds the old properties (reject view
swaps them in, accept view drops the record).

## Consequences

- Default output changes for any DOCX that carries revisions: the body becomes the
  original text, and `<aim-proposals>` cards appear, attributed to the Word authors
  (IMPORT-D5).
- **tndm editor** (consumes the SDK pinned): after its pin bump, redlined uploads
  show the colleague's edits in the review lane instead of corrupted text. Its
  proposal UI shows `data-author-id`, which is now a Word user name. No schema
  change. Revision-free uploads are unchanged.
- Rare new refusals on exotic or hostile files (IMPORT-D8); the editor already
  maps `ParseError` to a 422 carrying the message.

## Compatibility and migration

No format change: the output is plain v0.5. Existing `.aim` files are untouched.
Ships in **SDK 0.5.3** (IMPORT-D14). No migration.

## Test plan (summary)

Shape fixtures under `tests/fixtures/docx-tracked/`, generated from declared
outcomes `(reject_text, accept_text, revision shape)` so goldens are independent
of the importer's resolver, confirmed once by hand in Word or LibreOffice (steps
recorded in the fixture README); every §1.1 situation plus nesting, hyperlinks,
cells, textboxes, list splits, whole inserted tables, whole deleted lists,
all-inserted documents and the `max_revisions` ceiling. Per fixture: body =
reject golden, `accept_all()` = accept golden, `reject_all()` = body, lint 0
errors, `verify() == []`, expected card set. A non-gating Linux CI job runs
LibreOffice's Accept/Reject All as an independent oracle. Mutation check: removing
the mark-join rule, the move pairing or the no-card-without-revision rule must
fail tests.

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
