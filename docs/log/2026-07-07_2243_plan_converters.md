---
date: 2026-07-07 22:43
type: plan
status: done
related: []
---

> **Outcome (2026-07-08):** shipped on this branch as planned — 29 tests,
> full suite green; plus a real `from_docling` fix found live (heading
> nodes parent their section content in DOCX output; walker now descends).
> The tndm editor consumes all of it end-to-end (upload docx/pdf/md/txt,
> export docx/md/html/pdf). Awaiting maintainer review before merge.

# Plan: format converters (`aimformat.convert`)

Maintainer-commissioned (2026-07-07 session): a good converter from the main
formats to `.aim` and back, as helpers in this repo. Developed on
`feat/converters` and pushed for maintainer review — not merged to `main`
autonomously.

## Scope

New subpackage `src/aimformat/convert/` (core stays zero-dependency; every
heavy dependency is an optional extra, lazy-imported with an actionable
error):

**Import**

- `from_text(text, *, title=None, lang="en", author=None) -> AimDocument` —
  stdlib; blank-line-separated paragraphs; first line can become the title.
- `from_markdown(text, ...)` — extra `markdown` (`markdown-it-py`);
  CommonMark + tables mapped into the closed vocabulary (headings, lists,
  tables, code, blockquote, images, inline marks; unsupported constructs
  degrade to text, never dropped silently).
- `from_docx(path, ...)` / `from_pdf(path, ...)` — extra `ingest`
  (`docling`); thin wrappers running docling and delegating to the existing
  `from_docling`.
- `from_path(path, ...)` — extension dispatch (.md/.markdown, .txt,
  .docx, .pdf, .aim/.aim.html passthrough load).

**Export**

- `to_markdown(doc, *, pending="drop"|"criticmarkup") -> str` — stdlib
  chunk walker; tables as pipe tables; slides as sections; default drops the
  pending lane, optional CriticMarkup rendering of proposals.
- `to_html(doc, *, pending="keep"|"accept-all"|"reject-all") -> str` —
  standalone shareable HTML = flattened copy (history/embeddings stripped);
  default keeps the proposals appendix visible (pending must stay visible to
  plain readers — spec §5.5 spirit).
- `to_pdf(doc, path, *, pending="keep"|...)` — extra `pdf` (`playwright`
  Chromium); prints the rendered document.
- `to_docx` already exists in the SDK; re-exported for symmetry.

**CLI** — `aim import IN -o OUT.aim [--title ...]` and
`aim export IN.aim -o OUT.(docx|md|html|pdf) [--pending ...]` with
per-format pending defaults (docx: tracked; md: drop; html/pdf: keep).

**Tests** — round-trip and golden tests for text/markdown both ways;
docling-path tests via `pytest.importorskip("docling")` (CI has only
docling-core fixtures today); CLI tests; lint-clean assertion on every
produced document (the `lint == []` round-trip rule from
[architecture.md](../knowledge/architecture.md)).

## Pinned versions (searched 2026-07-07)

`markdown-it-py==4.2.0`, `docling==2.110.0`, `playwright==1.61.0` — as
extras `markdown`, `ingest`, `pdf`; a convenience extra `convert` bundles
markdown+ingest.

## Non-goals tonight

PPTX in either direction; original-formatting-preserving DOCX export
(maintainer explicitly deferred); HTML *import*.
