---
date: 2026-07-09 23:12
type: plan
status: done
related: []
---

# Plan: `aim normalize` CLI verb + inline formatting in docling ingest

Two format-side items from the HTML-normalization design discussion (editor
counterpart: tndm `docs/log/2026-07-09_2312_plan_editor-vocabulary-roundtrip-and-paste.md`).

## 1. `aim normalize` (tier-2 canonicalization as a verb)

The canonical form already exists (spec §11) and is applied at every SDK
write boundary; on-disk conformance is lint rule C001. What's missing is the
standalone verb for third-party producers (LLM pipelines, generators) whose
output is in-vocabulary but non-canonically spelled.

- `aim normalize FILE [-o OUT] [--check]` — load (transparent parse), then
  `dumps()` (canonical serialization). `--check` reports without writing,
  exit 1 when the file is not byte-canonical.
- Contract: **lossless, idempotent, total over loadable documents**. It
  re-spells; it never coerces. Out-of-vocabulary content passes through
  unchanged and stays the linter's to flag (normalize ≠ import — the tier-3
  lossy importer is explicitly deferred).
- `doc_hash` is computed over the canonical projection, so normalize never
  changes it — pinned by test.

## 2. `from_docling`: inline formatting, hyperlinks, inline groups

Probe (docling 2.110.0 / docling-core 2.86.0, DOCX with mixed runs) showed:

- Mixed-formatting paragraphs export as an **`inline` group** whose children
  are per-run text items carrying `formatting` (bold/italic/underline/
  strikethrough/script sub|super) and `hyperlink`.
- The current walker has no `inline` branch: under body it descends and
  **shatters one paragraph into one `<p>` chunk per run**; inside a list item
  (`_li_markup`) the group is skipped and the item's content is **dropped
  entirely** (item text is empty, content lives in the group). Both are
  data-loss bugs, not just formatting loss.
- Run boundaries are whitespace-stripped by docling (`text` and `orig`
  alike); docling's own serializers re-join with a bare space (`"H 2 O"`,
  space before punctuation).

Plan:

- Render text items through the formatting flags: `<strong> <em> <u> <s>
  <sub>/<sup>` (fixed nesting order), `<a href>` outermost; registry-escaped.
- Handle `inline` groups as a single block (paragraph or list-item content),
  joining children with a space **except** before closing punctuation, after
  opening brackets, and around sub/super runs (chemical formulas, exponents)
  — strictly better than docling's naive join, pinned by tests.
- Apply formatting/hyperlink to plain body text items too (whole-run bold
  paragraphs don't come as groups).
- Tests: docling-core fixture documents (dev-dep only, existing pattern in
  `tests/test_ingest_export.py`) covering marks, links, groups in body and
  list items, and the join heuristic.

Non-goals: table-cell formatting (docling `TableCell` carries no formatting),
heading-internal runs (docling flattens them before we see them), the tier-3
HTML importer.
