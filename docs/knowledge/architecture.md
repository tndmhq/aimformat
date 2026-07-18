# Toolkit architecture

Curated map of how the v0.2 reference toolkit fits together. Read this
before changing code; update it when the shape changes.

## One registry drives everything

`src/aimformat/registry.json` is the machine-readable vocabulary: elements,
attribute allowlists, class tables, inline-style whitelist + grammars,
theme slots, event field schemas, proposal actions, canonical attribute
order, and the lint-rule code table. Three artifacts are derived from it
and MUST NOT be hand-maintained:

1. the linter's tables (`registry.py` exposes typed accessors, `lint.py`
   consumes them),
2. the stylesheet (`css.py::generate_aim_css()` — element base layer +
   every registered utility + `aim-*` chrome),
3. spec Appendix A (`scripts/gen_spec_appendix.py`; `tests/test_spec.py`
   fails when stale).

`tests/test_spec.py::test_lint_rule_codes_match_registry` keeps the rule
codes bidirectionally in sync with what `lint.py` can actually emit.

## Module map (`src/aimformat/`)

| module | job | key invariant |
|---|---|---|
| `dom.py` | mini-DOM + transparent HTML reader | reports the file as written (no tag inference) so canonical round-trips are byte-exact |
| `canonical.py` | serializer, escaping, `doc_hash`, canonical JSON | THE definition of equality; every other module defers to it |
| `document.py` | `AimDocument`: ops, pending lane, verify, time travel, assets | every state change mutates the tree AND appends the matching event — never one without the other; a lazy per-instance `_HistoryIndex` derives parsed events, id reservations/tombstones, and seq/batch counters from authoritative JSONL + pending state, then the three history writers update it incrementally; replay/verify always run on a deep copy (`DocState` over a clone), never the live tree |
| `events.py` | `Actor`/`Event` over canonical dicts | unknown fields ignored; `x_*` reserved |
| `lint.py` | the verifier; stable codes S/V/X/P/H/M/C | collects all findings in one run; `C001` byte-compares the source against the canonical serialization |
| `reconcile.py` | repair out-of-band edits; adoption path for hand-written files | edit script from expected state E (forward replay of the FULL log) to actual body A, appended as `origin:"reconcile"` events — so `verify()` passes by construction; refuses pruned/damaged logs; never rewrites body content (only ids) |
| `css.py` | deterministic stylesheet | budget guarded by tests (<40 KB raw) |
| `pagesetup.py` | `aim:doc` page validation, resolved geometry, and print CSS | the registry defines sizes, margins, and defaults; PDF, DOCX, and editors consume the same `PageSetup` |
| `ingest.py` | DoclingDocument dict → chunks | dict-shaped input only — docling never becomes a dependency; run `formatting`/`hyperlink` and `inline` groups map to strong/em/u/s/sub/sup/a (safe schemes only) |
| `convert/` | text/Markdown/DOCX/PDF import and Markdown/HTML/PDF export | stdlib directions stay dependency-free; Markdown, Docling, and Playwright imports remain lazy behind extras |
| `export_docx.py` | .aim → Word incl. `w:ins`/`w:del` tracked changes | `accept-all`/`reject-all` resolve a throwaway copy through the real accept/reject machinery |
| `cli.py` | `aim` entry point (also installed as `aimformat`) | exit codes 0/1/2; `--format json` for tooling |
| `note.py` | canonical agent-note template + helpers (spec §2.5) | the note text contains no markup — structural substring checks must never false-positive on it |
| `mcp.py` | MCP server (FastMCP, stdio); extra `[mcp]` | six workflow tools, not a 1:1 SDK mirror; lazy-imported by the CLI so core stays stdlib-only |

## Hard rules

- **Zero runtime dependencies.** Anything heavy goes behind an extra
  (`[docx]` pattern) with a lazy import and an actionable error message.
- **The file is canonical on disk.** `dumps()` refreshes only the
  machine-managed stylesheet; everything else must already be canonical —
  if a test fails with C001, fix the producer, don't post-process.
- **Ids are never reused.** Deleted and recorded payload ids stay burned in
  `_HistoryIndex`; its sole instance-lifetime ledger survives prune/flatten
  without being persisted. `_taken_ids()` combines that history/pending cache
  with a fresh body-tree scan. There is deliberately no mutable id→node tree
  index. Every in-place pending-card mutation must update that document
  instance's `_HistoryIndex` before the next id/history read; this includes
  validation clones, whose indexes are populated when `_clone()` creates them.
- **Pending lanes are creation-order programs.** Every SDK proposal is
  validated against a clone containing all earlier pending cards applied in
  creation order. The projection answers *structural* legality only: no-op
  guards (identical content, no-op move) judge against the current document,
  cards the new proposal supersedes (§5.4) are excluded from structural replay
  while their payload ids stay reserved for replacement normalization, and
  proposal targets plus add/move destination containers must exist in the
  current body. Recorded add/move anchors must also resolve there; the one
  sanctioned exception is a position on a pending add's payload root, which is
  written as the proposal-id chain so accepting or rejecting that add rebinds
  every dependent position (lint P008/P011 parity for the linted target/add
  cases). Amending a payload replays the whole lane on a clone first and fails
  closed if the amendment would break a later card.
  `resolution_order()` preserves creation order one ready card at a time; its
  only exception is moving a foreign-reordered chained add behind the pending
  add it names, without letting later cards overtake either one. A lane whose
  cards passed projected validation applies cleanly in creation order.
  Accept-all first replays the whole lane on a clone and refuses with zero live
  mutation if a foreign-authored card, out-of-band edit, or partial manual
  resolution broke the projection. Reconcile uses the same replay as a
  fail-closed fixpoint and records rejections for cards that no longer apply;
  it never searches for a rescuing order. An unorderable foreign chained-add
  cycle surfaces its actual cycle members so reconcile rejects only cycle
  participants, never unrelated valid cards earlier in the lane. The property
  oracle in `tests/test_resolution_order_properties.py` is exactly this
  creation-order clone replay.
- **Payload equality is the verification primitive.** If you change
  canonical form in any way, every stored payload in every fixture/example
  changes meaning — regenerate fixtures and examples, and expect checkpoint
  hashes to move.
- **The agent note is informative-only by spec (§2.5).** Tooling must never
  execute, install, or fetch anything based on header content — the
  vim-modeline lesson, written into the format.

## The TypeScript reader (`ts/`)

`@aimformat/reader` is the official TypeScript **read** library — a
read-only projection of a canonical document (ordered node tree, chunks
with first-class runs, proposals, theme/page setup, `docHash`). Writes are
Python-only by design; the TS side must never grow write paths. Structure
mirrors the Python modules (`dom.ts`/`parser.ts`/`canonical.ts`/
`document.ts`), and three invariants hold:

1. **`ts/src/registry.data.ts` is generated** from `registry.json` (+ the
   aim-note template) by `scripts/gen_ts_registry.py`;
   `tests/test_ts_registry.py` fails when stale.
2. **Parity is pinned by goldens.** `scripts/dump_projection.py` dumps the
   Python projection of `examples/*.aim` + `tests/parity/fixtures/*.aim`
   (edge fixtures from `scripts/gen_parity_fixtures.py`) to
   `tests/parity/goldens/`; `tests/test_parity_goldens.py` asserts
   regeneration is byte-stable, and the vitest suite asserts the TS
   projection equals the goldens field-for-field (plus `docHash` parity
   over the ok_* conformance kit). Any behavior change on either side
   surfaces as a reviewed golden diff. On divergence, `spec.md` decides
   which implementation is wrong. The `noncanonical-*` fixture tier
   (SDK-built + deterministic string edits) pins agreement on *malformed
   hand-edited* input too — those are exempt from the lint-clean check
   and asserted to keep lint findings.
3. **One parser code path.** The TS parser is a strict scanner for the
   canonical subset (no DOMParser, no Node APIs) — trade-offs documented
   in `ts/README.md`.
4. **Port Python semantics, not JS defaults** (each of these shipped as a
   real divergence, caught in the 2026-07-17 Codex review): string
   ordering is *code point* (Python `sorted`), never JS's UTF-16
   code-unit `sort()` — they differ when BMP chars above U+D7FF meet
   astral chars (`compareCodePoints` in `canonical.ts`; pinned by the
   `unicode-attrs` parity fixture); JSON field fallback is `dict.get` —
   default only when *missing*, never on explicit `null` (so no `??`);
   numeric character references go through HTML's replacement table
   (`html._replace_charref`), not raw `fromCodePoint`. The 2026-07-18
   PR #17 round added the same class on malformed input: duplicate
   attributes are FIRST-wins (`setdefault`, matching `Element.get`);
   self-closed non-void elements serialize open+close (the serializer
   never echoes the slash); semicolonless character references decode
   with full `html.unescape` semantics (legacy no-semicolon table +
   longest-prefix fallback); and a duplicate chunk id reads LOCALLY per
   container, never as the global first hit.

Fixture regeneration re-mints proposal ids (tool-assigned, like
`gen_examples.py`): always regenerate fixtures and goldens together.

## Regeneration commands

```sh
python3 scripts/gen_spec_appendix.py     # after registry changes
python3 scripts/gen_fixtures.py          # after lint/canonical changes
python3 scripts/gen_examples.py          # after SDK-visible changes
python3 scripts/gen_ts_registry.py       # after registry changes (TS tables)
python3 scripts/gen_parity_fixtures.py   # after SDK-visible changes…
python3 scripts/dump_projection.py       # …then always refresh the goldens
python3 -m pytest                        # 680+ tests, a few seconds
cd ts && npm test                        # TS reader + parity suite
```

## Dependency pins (search-then-pin convention)

Runtime: none. Extras/dev: `python-docx==1.2.0`, `docling-core==2.86.0`
(tests only), `pytest==9.1.1`, `mcp==1.28.1` (searched 2026-07-10; latest
stable 1.x — upstream advises `<2`, v2 is in alpha with renames; revisit
after the 2026-07-28 MCP spec finalizes). docling-core is used solely to
build fixture DoclingDocuments in `tests/test_ingest_export.py` and
`tests/test_ingest_inline.py`.

## Lessons from the v0.1 post-ship review (2026-07-07)

Full findings: `docs/log/2026-07-07_1921_review_v01-deep-self-review.md`.
The two rules below exist because breaking them produced five criticals:

1. **Anchor/target resolution goes through validated primitives only**
   (`DocState.resolve_insert_point`, `_resolve_end_anchor`,
   `_direct_members`) — container-scoped, shell-aware, target-excluded, and
   ALL lookups happen before the FIRST mutation. Never resolve an id
   globally when an operation names its context.
2. **The serializer is a normal form, not an echo.** If two spellings of
   the same logical construct can round-trip, `doc_hash` forks and chain
   verification diverges across tools. Any change here ⇒ regenerate
   fixtures/examples and expect checkpoint hashes to move.

Also load-bearing: `lint_text` never raises (hostile input becomes
findings); nok fixtures trip exactly their named code (the generator's
sanity check enforces it); when adding a public operation, end its test
with `lint(dumps()) == []` — three review findings would have been caught
mechanically by that one assertion.
