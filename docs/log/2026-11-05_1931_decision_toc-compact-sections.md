---
date: 2026-11-05 19:31
type: decision
status: active
related: [2026-10-01_2135_decision_toc-on-import-and-freshness.md, 2026-11-05_1816_plan_pr40-rework.md, 2026-11-05_1931_decision_import-packs-images-before-baseline.md]
---

# Decision IMPORT-D13b: one compact TOC entry per section, a section read, a TOC on every 0.6 save

Id: **IMPORT-D13b**. Supersedes the entry shape and the "only importers build a TOC" rule of
[IMPORT-D13](2026-10-01_2135_decision_toc-on-import-and-freshness.md). The entry shape and the
section read are maintainer decisions (2026-11-05). Items marked *provisional* were chosen
during the rework without the maintainer and await review
([plan](2026-11-05_1816_plan_pr40-rework.md), items P1-P8). D13's freshness marker
(`toc_doc_hash`), the retargeted M004, M005, the optional `summary`, the refresh on save and
`aim_read`'s `toc_source` carry over as D13 proposed them, still awaiting approval.

## Context

D13 kept v0.5's entry shape, `{title, level, chunks: [ids]}`, which lists every top-level id
of every section and repeats what any body read returns (stored TOC at 4eb432e, o200k tokens:
legal-addendum 605 for 4 entries over 81 chunks; long-report 4,211 for 109 entries). An agent
orienting from the TOC needs each section's size before reading it, and a way to read one
section. And only importers built a TOC: a document written through the SDK had none unless
the caller ran `generate_toc()`.

## Options considered

- Keep the every-id lists (D13): rejected for the cost above.
- `[first..last]` ranges per entry, as the draft agent read in #42 prints them: rejected for
  the stored cache and the agent read alike (maintainer, 2026-11-05).
- **One compact entry per section, with counts**: chosen (maintainer, 2026-11-05).

## Decision

Decided (maintainer, 2026-11-05):

1. §8.1: `toc` is a list of one entry per section, stored as
   `{"chunk_count", "id", "level", "title", "word_count"}` (keys sorted by canonical JSON,
   §11.2). No list of block ids. The stored cache, `AimDocument.outline()` and MCP
   `aim_read`'s `toc` share the shape.
2. SDK: `AimDocument.section(section_id) -> Section` reads one section by the id that opens
   it and gives its ids, its text (one line per chunk) and its exact markup (the file's
   construct lines), plus the counts and `entry()`. An id that opens no section raises
   `TargetNotFound`. The MCP side belongs to the agent-read work (#42).

Provisional (awaiting the maintainer):

3. A section opens at a top-level addressable `h1`-`h6` (level = its rank) or an
   `aim-slide` (level 1, id = the slide's container id, title = its first heading) and runs
   to the next opener of the same or a higher level: it includes its subsections, and its
   counts include theirs. Content before the first opener is an untitled level-1 entry named
   by the first construct that has an id.
4. `chunk_count` counts distinct chunk ids (list items and table rows one each, a run once,
   containers zero). `word_count` counts whitespace-separated words of the visible text;
   block, item, row and cell boundaries and `<br>` separate words, inline tags do not, and
   `img`, `svg`, `template`, `script` and `style` contribute none. The counts are
   informative: lint checks the shape and the freshness hash, never recomputes counts.
5. `dumps()` builds the TOC when the body has a heading or a slide, refreshes it when stale
   and reshapes old-shape or malformed entries, **only in documents declared 0.6 or later**.
   Below 0.6 a save leaves the meta block byte-identical, so a 0.5 tool never meets a
   TOC-only meta block written by a save (its M004 requires a summary). An explicit
   `generate_toc()` is not gated.
6. Lint: an old-shape entry in a document declared 0.6 or later is M005 (stale), with no
   finding below 0.6, where that shape is the version's own; an entry of neither shape is
   M003 at any version.
7. A document with no heading or slide gets no TOC from a save. One whose stored TOC loses
   its last heading keeps a fresh TOC of one untitled entry.
8. Numbered clauses (`num-1`/`num-2` blocks) open no sections here; #42 decides that for its
   agent outline.

## Consequences

- Stored TOC sizes now scale with sections, not blocks: legal-addendum about 605 → 166
  tokens; long-report about 4,211 → 3,500 (109 titled entries).
- Every 0.6 document saved through the SDK with a heading or a slide carries a TOC,
  agent-written documents included. `dumps()` also adds it to the caller's in-memory
  document (exports call `dumps()`).
- #42's agent read adopts this shape when it is reworked (`views.py` derives
  `[first..last]` ranges today).

## Compatibility and migration

Spec v0.5 defined `toc [{title, level, chunks: [ids]}]`. Such files stay valid: below 0.6
nothing changes and lint is silent; in a 0.6 document the old shape is stale and the next
save rewrites it. Nothing is migrated in place. Ships in spec 0.6 with IMPORT-D9.

## Tests

`tests/test_toc.py` (entry keys and types, nesting and nested counts, the untitled entry,
slides, items and rows, the word rules, `section()`, the 0.6 gate, old-shape and malformed
entries, a linear-time guard), a property in `tests/test_properties.py`, and the conformance
fixtures `nok_M005_pre_0_6_toc_shape` and `nok_M003_malformed_toc_entry`.
