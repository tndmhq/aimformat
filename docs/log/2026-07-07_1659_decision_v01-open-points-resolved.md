---
date: 2026-07-07 16:59
type: decision
status: done
related:
  - 2026-07-07_1619_plan_spec-v01-and-reference-toolkit.md
---

# Decision: v0.1 open points — resolved under maintainer delegation

The maintainer delegated the remaining open design points so v0.1 could
ship, asking for every judgment call to be logged for later review. This
entry is that record. **Each item below is revisitable** — the spec is a
0.x draft and 0.x minors may break. Items marked ⚑ are the ones most worth
a deliberate maintainer look.

## Format-level decisions (now in spec.md)

1. **⚑ Chunk-id scheme** (spec §4.4). Ids are opaque strings unique per
   document, `[a-z0-9][a-z0-9_-]{0,63}`; reference tooling assigns 8-char
   random lowercase ids; UUIDs remain valid but are no longer the stated
   default. *Rationale*: ids appear on every chunk and in every event
   payload — at UUID length they dominate token cost for LLM read/write,
   against the format's stated design goal. Earlier internal notes said
   "UUIDv4, assigned by tooling"; the "assigned by tooling" mechanism is
   unchanged, only the recommended spelling shrank. Ids are never reused
   (deleted ids stay burned), preserving identity semantics.
2. **Vector-asset content addressing** (spec §9.2). Raster assets hash
   their raw bytes; shape-based `<symbol>` assets hash the canonical
   serialization of the symbol's children. *Rationale*: only candidate that
   is deterministic, implementation-independent, and needs no side files.
3. **TOC `chunks` arrays** (spec §8.1) may list chunk *and* container ids,
   so an entry can span a list or a slide. *Rationale*: matches how
   sections actually span content; costs nothing to readers.
4. **Comments** (spec §11.5): legal in `<head>` only, preserved byte-exact;
   body comments are conformance errors. *Rationale*: body comments would
   need construct-line and hashing rules for no benefit; provenance belongs
   in history/explanations.
5. **Embeddings** (spec §8.2): multiple models per chunk, no "primary"
   marker, vectors as plain JSON numbers (no quantized packing in v0.1).
   *Rationale*: readers select by model id anyway; packing is an
   optimization with schema cost — deferred until size data demands it.
6. **`aim:doc`** (spec §6.5): reserved; zero defined fields in v0.1;
   targeting it is an error. *Rationale*: reserving the name is free;
   guessing its field set is not.
7. **Slide raw-tier scaling** (spec §3.4): `zoom` with stepped `@media`
   fallbacks (verified in the round-1 browser checks); viewers override
   `--aim-slide-scale`.
8. **Chained-add resolution** (spec §5.4): accept ⇒ dependents rebind to
   the new chunk id; reject ⇒ dependents rebind to the rejected card's own
   anchor. *Rationale*: the only deterministic, order-independent rule.
9. **Word-level diffs / deep links** stay informative recommendations
   (Appendix B), not conformance requirements.
10. **Deferred to Appendix C** (unchanged from the workspace draft):
    cell-level addressing, pagination, masters, `.aimx`, multi-writer,
    signing, media-type registration, `aim:doc` fields, fonts-as-assets.

## Toolkit-level decisions

11. **Zero runtime dependencies**, stdlib-only; heavy integrations behind
    extras (`[docx]`). docling is consumed as its exported dict — never a
    dependency.
12. **⚑ v0.1.0 CLI scope**: `lint`, `hash`, `new`, `show`, `flatten`,
    `css`. `reconcile` and `open` are specified but ship later (spec §6.8,
    §10) — both are real projects, not afternoon adds.
13. **DOCX exporter semantics**: pending lane as real `w:ins`/`w:del` at
    chunk granularity, attributed to the proposing actor;
    `accept-all`/`reject-all` resolve a throwaway copy through the real
    accept/reject machinery. Moves and theme proposals are not represented
    in tracked mode (documented); slides are skipped (PPTX exporter is
    future work). Links render as text + URL (no rels in v0.1).
14. **Ingestion mapping** (module docstring in `ingest.py`): docling
    `title`→h1, `section_header` level *n*→h(*n*+1), lists→containers with
    item chunks (orderedness from the items' `enumerated` flag — the group
    label alone is unreliable), tables→row-chunk containers with spans,
    pictures→figures (data-URI when embedded, honest placeholder
    otherwise), furniture skipped, page provenance dropped (no pagination
    model in v0.1).
15. **Packaging**: hatchling; `aim` console script; pins per the
    search-then-pin convention (`pytest==9.1.1`, `python-docx==1.2.0`,
    `docling-core==2.86.0` dev-only).
16. **⚑ Process**: this work was committed directly to `main` (pre-v0.1
    repo, no external users, maintainer commissioned the end state and
    reviews via this log + git history). If a review-first flow is
    preferred going forward, say so and future drops become branches.

## Where the evidence lives

Spec: `spec.md` (executable snippets). Enforcement: `src/aimformat/`
(registry-driven). Conformance: `tests/fixtures/`. Worked artifacts:
`examples/`. Report: `2026-07-07_1659_report_v01-shipped.md`.
