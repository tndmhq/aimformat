---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_history-baseline-event.md, 2026-10-01_2135_decision_importers-record-baseline.md, 2026-10-01_2135_decision_import-versioning-and-sequencing.md]
---

# Decision IMPORT-D13: TOC on import, optional `summary`, `toc_doc_hash`, SDK-maintained TOC, and a live outline in `aim_read`

**Status: proposed — awaiting founder approval.** Implemented, uncommitted, on `wt/aim-import-tracked`; deviations are listed in the [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D13**. Related decisions: [IMPORT-D9](2026-10-01_2135_decision_history-baseline-event.md), [IMPORT-D10](2026-10-01_2135_decision_importers-record-baseline.md), [IMPORT-D14](2026-10-01_2135_decision_import-versioning-and-sequencing.md).

## Context

No importer builds a TOC. Calling `generate_toc()` on an import makes lint fail
(M004: §8.1 requires `summary` in the meta block). After a heading edit the TOC
keeps the old title, and nothing marks it stale. Measured TOC cost on the
fixtures: 217–4,164 raw tokens (legal-addendum: 567 tokens for 4 entries, because
each entry lists every chunk id; 3% on long-report). A stored TOC pays off only
for outline reads; any reader of the whole body pays for it and gains nothing.

## Options considered

- No stored TOC, live outline only — cheapest raw file; rejected as the only
  mechanism because the TOC on import was explicitly requested and a head-only raw
  read benefits from it.
- Stored TOC with no freshness marker — silently stale after any non-SDK write.
- **Stored TOC when there are headings + freshness marker + SDK refresh + live
  derivation in `aim_read`** — chosen.
- Generate a summary too — rejected: a summary needs a model (`summary.model`), and
  an extractive stand-in would be a misleading cache.

## Decision

- **§8.1 new text:** "holds one JSON object with optional `summary {…}`, optional
  `toc [...]` and, when `toc` is present, optional `toc_doc_hash`: the `doc_hash`
  the TOC was derived from. A block with neither `summary` nor `toc` is an error."
  `toc` stays a list; the sibling marker is additive, so v0.5 documents with a bare
  `toc` stay valid.
- **Lint:** M004 retargeted to "aim-meta block has neither summary nor toc". New
  **M005** (warning): "toc cache is stale (`toc_doc_hash` mismatch)". A bare `toc`
  without a hash is not checked.
- **SDK:** `generate_toc()` writes `toc_doc_hash`. `dumps()` refreshes a present
  TOC, as it already refreshes the machine-managed stylesheet: O(n),
  deterministic, outside `doc_hash`, not evented (§7). Staleness can then come only
  from non-SDK writers, and the marker catches those.
- **Importers** call `generate_toc()` when the body has at least one heading or
  slide (without headings a TOC is one untitled entry listing every chunk id).
- **MCP `aim_read`** returns the stored TOC when fresh; otherwise derives the
  outline live (O(n)) and reports `toc_source: "cache" | "derived"`. This works
  for every document, v0.5 included. A body with no heading or slide gets
  `toc: null, toc_source: null` (review fix: its outline would be one untitled
  entry repeating every chunk id the response already lists).

## Consequences

- Imported documents with headings carry a TOC (+217 to +4,164 raw tokens on the
  fixtures).
- MCP `aim_read` gains the `toc_source` field and never serves a stale outline.
- **tndm editor:** saves through the SDK refresh a present TOC; no other change.

## Compatibility and migration

A TOC-only meta block fails M004 under a v0.5 linter, so this ships in **spec
v0.6** together with IMPORT-D9. Existing v0.5 documents (summary present, TOC
with or without hash) stay valid; nothing is migrated. The live outline in
`aim_read` benefits v0.5 documents with no stored TOC.

## Tests

Conformance: TOC with `toc_doc_hash` (ok); TOC-only meta (ok); empty meta (M004);
stale TOC (M005). SDK: `generate_toc` on an import lints clean; heading edit +
`dumps()` refreshes the TOC; `aim_read` on a v0.5 document with no TOC returns a
derived outline.

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
