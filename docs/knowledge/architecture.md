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
| `document.py` | `AimDocument`: ops, pending lane, verify, time travel, assets | every state change mutates the tree AND appends the matching event — never one without the other; replay/verify always run on a deep copy (`DocState` over a clone), never the live tree |
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
- **Ids are never reused** (deleted ids stay burned; `_taken_ids()` scans
  body + history + pending payloads).
- **Pending lanes are creation-order programs.** Every SDK proposal is
  validated against a clone containing all earlier pending cards applied in
  creation order. The projection answers *structural* legality only: no-op
  guards (identical content, no-op move) judge against the current document,
  cards the new proposal supersedes (§5.4) are excluded from the projection,
  and proposal targets and recorded add anchors must exist in the current
  body (lint P008/P011 parity — an add position that resolves to a pending
  add's root is written as the proposal-id chain instead). Amending a payload
  replays the whole lane on a clone first and fails closed if the amendment
  would break a later card. `resolution_order()` preserves creation order;
  its only exception is moving a chained add behind the pending add it names
  so normal acceptance or rejection rebinding can materialize the anchor. A
  lane whose cards passed projected validation applies cleanly in creation
  order. Accept-all first replays the whole lane on a clone and refuses with
  zero live mutation if a foreign-authored card, out-of-band edit, or partial
  manual resolution broke the projection. Reconcile uses the same replay as a
  fail-closed fixpoint and records rejections for cards that no longer apply;
  it never searches for a rescuing order. The property oracle in
  `tests/test_resolution_order_properties.py` is exactly this creation-order
  clone replay.
- **Payload equality is the verification primitive.** If you change
  canonical form in any way, every stored payload in every fixture/example
  changes meaning — regenerate fixtures and examples, and expect checkpoint
  hashes to move.
- **The agent note is informative-only by spec (§2.5).** Tooling must never
  execute, install, or fetch anything based on header content — the
  vim-modeline lesson, written into the format.

## Regeneration commands

```sh
python3 scripts/gen_spec_appendix.py   # after registry changes
python3 scripts/gen_fixtures.py        # after lint/canonical changes
python3 scripts/gen_examples.py        # after SDK-visible changes
python3 -m pytest                      # 598+ tests, a few seconds
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
