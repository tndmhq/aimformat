---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_import-tracked-changes-as-proposals.md, 2026-10-01_2135_decision_import-docx-comments-reported.md]
---

# Decision IMPORT-D7: `import_docx()` returns an `ImportResult` with an importer-agnostic `ImportReport`

**Status: proposed — awaiting founder approval.** Implemented, uncommitted, on `wt/aim-import-tracked`; deviations are listed in the [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D7**. Related decisions: [IMPORT-D1](2026-10-01_2135_decision_import-tracked-changes-as-proposals.md), [IMPORT-D6](2026-10-01_2135_decision_import-docx-comments-reported.md).

## Context

The import now has things to say that do not fit in a document: which revisions
became which cards, which were not carried and why, the comments, converter
differences. `from_docx` returns an `AimDocument`, and changing that breaks every
caller, including the tndm editor's upload conversion.

## Options considered

- (a) `from_docx` returns `(doc, report)` — breaks every caller.
- (b) A transient `doc.import_report` attribute — silently lost on `dumps`, and a
  hidden side channel on the document type.
- (c) A new function that returns a result object.
- (d) Python warnings only — machine-unfriendly, easy to lose.

## Decision

(c) + (d). The types are importer-agnostic so the PDF/docling and Markdown
importers can report later without a new API:

```python
@dataclass
class ImportReport:
    source: str                     # base name only, never a directory path
    tracked: str | None             # "propose" | "accept" | "reject" | None (no revisions)
    revisions: list[RevisionNote]   # kind, author, date, excerpt, card id or None + reason
    proposals: list[str]            # card ids written
    comments: list[CommentNote]
    warnings: list[str]             # everything not carried, in words

@dataclass
class ImportResult:
    document: AimDocument
    report: ImportReport

def import_docx(source, *, title=None, lang="en", author=None, theme=None,
                tracked="propose", max_revisions=5000) -> ImportResult

class AimImportWarning(UserWarning): ...
```

- `from_docx(...)` is `import_docx(...).document` and emits one
  `AimImportWarning` per warning group.
- `aim import` prints a one-paragraph summary to stderr.
- No MCP tool is added; `aim import` keeps its output-path handling.

## Consequences

- New public names in `aimformat.convert`, re-exported at the top level.
- **tndm editor:** unchanged unless it opts in; switching its upload path to
  `import_docx` is the follow-up named in IMPORT-D6.

## Compatibility and migration

Additive SDK surface; no format change. Ships in SDK 0.5.3.

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
