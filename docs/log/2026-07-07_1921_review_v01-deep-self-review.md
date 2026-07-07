---
date: 2026-07-07 19:21
type: review
status: done
related:
  - 2026-07-07_1659_report_v01-shipped.md
---

# Review: v0.1 deep self-review (post-ship)

Adversarial review of the freshly shipped v0.1 toolkit, at the maintainer's
request: my own targeted pass over the modules I distrusted most, plus three
independent review agents (document/events state machine; linter + canonical
form + fixtures; ingest/export/CLI/docs), each required to *reproduce* every
finding before reporting it. All fixes land with regression tests in
`tests/test_review_regressions.py`.

## Confirmed findings and fixes

### F1 — critical: table-row delete/undo corrupted documents across shells

Deleting the first `tbody` row of a table with a `thead` recorded anchor
`{container, after: null}`; undo then reinserted the row **into the
`thead`** (first position = before the first row chunk, wherever it sits).
`verify()` flagged the resulting chain break — the invariant machinery
caught its own SDK. *Fix*: anchors for rows in table containers now carry
`shell` (`thead|tbody|tfoot`); `_anchor_of` records it, `DocState.insert`
honors it, spec §6.4 specifies it. Round-trips and `state_at` now
byte-exact across shells.

### F2 — major: `redo()` broken for stacked undos

`undo, undo, redo, redo` raised "nothing to redo" on the second redo — the
zone walk decremented a counter and bailed instead of treating redos as
stack pops. *Fix*: rewrote `redo()` (each redo cancels the nearest earlier
undo; the first uncancelled undo is the target) and documented
`_undo_candidate`'s negative-dip invariant. Full-cycle and interleaved
sequences are now pinned by tests.

### F3 — major: theme payloads bypassed validation on two paths

`accept(pid, applied=…)` on an `aim:theme` proposal took the applied markup
verbatim, and `propose_modify("aim:theme", raw_markup)` skipped grammar
checks — a hostile `body{background:…}` rule could enter the *versioned
theme block* through accept-with-tweaks even though lint would flag it
afterwards. *Fix*: `_validated_theme_markup()` (single
`<style data-aim-theme>`, one `:root` rule, registered slots only) now
guards both paths.

### F4 — design smell: `superseded_by` was patched by string replacement

`_fix_superseded_by` rewrote `"(new)"` placeholders in the raw history
after the fact — fragile by construction. *Fix*: proposal ids are allocated
before superseding, so the resolution event is written correctly the first
time; the helper is gone. A test asserts no placeholder can ever appear.

### F5 — minor: supersede + new proposal split across two batches

The superseded-resolution event and the proposal that triggered it got
different auto-batches despite being one editing intention. *Fix*: the
propose operations wrap both in one batch.

### F6 — trivial: dead loop in `lint.py` (`for … : pass`). Removed.

## Independent agent findings (all reproduced by the reviewers, all fixed)

### Reviewer 1 — document/events state machine (3 critical, 7 major, 3 minor)

The systemic diagnosis, which the fixes follow: **anchor/target resolution
was done by global id lookup that ignored the context it claimed** —
container scope, marker kind, table shell, self-reference, and the
chunk-vs-proposal id namespace — compounded by multi-step mutations that
mutated before all lookups succeeded.

- **critical** `move` to LAST self-anchored and destroyed the chunk with no
  event (remove-then-failing-insert). → LAST pools exclude the target;
  `DocState.move` validates the destination before any mutation; no-op
  moves are rejected like no-op modifies.
- **critical** `_anchor_of` ignored preceding *container* siblings, so
  deletes after a nested container recorded `after:null` and undo resurrected
  content *inside* the neighbor. → previous-sibling scan sees container ids;
  the same rewrite made deleting a nested container itself work (it raised
  before).
- **critical** `pack_assets` mutated chunk-by-image and raised mid-chunk on
  an undecodable image, leaving unrecorded edits. → all images of a chunk
  decode (strict base64) before the first swap; the whole run shares one
  batch.
- **major** accepted-move resolutions wrote `from`/`to` that the event
  schema rejected (H003 on the SDK's own output). → registered in the
  registry and spec §6.2; they are load-bearing for inversion.
- **major** `modify_chunk` on containers: stamped the wrong marker
  (silently demoting containers to chunks), skipped item-id assignment, and
  raised after mutating. → marker derived from the target's live kind,
  target-owned ids reusable, unmarked payload items get covered, container
  return view synthesized.
- **major** insert/LAST resolution wasn't container-scoped (cross-container
  anchors silently landed elsewhere; slide-append landed inside a nested
  list). → `resolve_insert_point` validates direct membership in the stated
  container; LAST pools are direct-members-only.
- **major** the shell fix (F1) didn't cover the public API: first-position
  adds/moves into tables still landed in the `thead`. → table first-position
  anchors default to `tbody`; proposals carry `data-anchor-shell`.
- **major** theme slot *values* were never validated on write (lint V012
  rejected the SDK's own output). → name+value grammar enforced on
  `set_theme` / `propose_theme` / raw-markup paths.
- **major** ids living only inside history payloads weren't burned
  (spec §4.4 violation). → `_taken_ids` harvests payload-interior ids.
- **major** legal chunk ids matching `p-*` were misrouted as proposal ids.
  → the `p-` prefix is reserved for proposals (spec §4.4).
- **minor** pack batching (fixed above), redo of a theme-introduction undo
  wrote a literal `"before":null` (field now omitted), `prune` could drop
  the entire log and reset seq/batch identity (now refused; `flatten` is
  the wholesale path).

### Reviewer 2 — linter + canonical form (1 critical, 4 major, 7 minor)

- **critical** `lint_text` crashed on malformed/mis-shaped cache JSON
  (meta, embeddings, non-object history lines) — a verifier that dies on
  hostile input fails open. → accessors validate shape (`ParseError`),
  `Event.from_json` requires objects, cache findings get codes M003/M004,
  and `lint_text` carries a last-resort net: hostile input becomes
  findings, never a traceback.
- **major** the serializer under-normalized (style order/duplicates,
  void-element slash, foreign self-close, class duplicates, empty attrs) —
  so C001 under-enforced §11 and **`doc_hash` was multi-valued for one
  logical document**, quietly undermining "equality is byte equality". →
  `canonical_attrs`/`serialize` are now a true normal form; a regression
  test asserts hash-equality across spelling variants.
- **major** the asset registry bypassed every attribute/URL/handler check
  (`javascript:` hrefs and `on*` inside `<symbol>` linted clean). → asset
  elements run through `check_element`; `url(` is banned in fill/stroke.
- **major** P010 false-positived on container-modify proposals — the SDK's
  own output failed its own linter. → payload root id is parsed, not
  regexed.
- **major** nested chunks (§4.3 "chunks never nest") were unenforced. →
  S024.
- **minor** `viewBox` case leak in the attr allowlist (V003 false
  positive), weak P013 timestamp check, stray text inside containers
  (S025), nested `aim-slide` (S026), table shells accepted inside `ul`
  containers, nested/non-template card children (P001), empty
  `<aim-proposals>` (P014), duplicate head meta scripts (S027), and —
  notably — **11 of 19 nok conformance fixtures tripped extra codes**,
  violating their own one-rule-per-file contract. → all closed; nok
  fixtures now derive from flattened bases and the sanity check requires
  *exactly* the named code.

### Reviewer 3 — ingest/export/CLI/docs (1 critical, 5 major, 7 minor)

- **critical** the rowspan continuation-skip in `_table_markup` was
  inverted: merged-cell tables ingested *wrong but lint-clean* (dropped
  cells, duplicated spans) and then crashed `to_docx`. → cells belong to
  the row where they start; reproduced fixture now round-trips and exports.
- **major** tracked export silently dropped pending adds anchored in
  containers, and container-targeted modify/delete proposals — the headline
  feature omitted changes. → adds render as inserted list paragraphs /
  real inserted table rows (chains included); container modify/delete
  render as whole-container del + ins.
- **major** tracked modify duplicated the payload once per child for
  sections/atomic lists/multi-child figures. → replacement emitted exactly
  once at chunk level (`emit_tracked_chunk`).
- **major** `from_docling` interpolated picture URIs without escaping —
  attribute breakout, phantom attributes, one crash path. → URIs are
  grammar-checked and attribute-escaped; non-conforming URIs degrade to the
  honest placeholder.
- **major** ingestion silently dropped table captions, tables inside list
  items, group-under-group lists, picture-attached text, and everything
  under `sheet`/`form_area`-style groups. → all mapped; unknown grouping
  nodes are descended rather than skipped.
- **minor** row-modify cell mapping under spans (fixed), atomic table
  chunks exporting as flattened text (now real tables), CLI crashes on
  directories/non-UTF8 (exit codes honored; stdout survives non-UTF8
  pipes), `aim new` clobbering without `--force`, empty ingested headings,
  and cyclic pending-add anchors linting clean (P015) while crashing
  accept-all export (now a clear error naming the cycle).

## Verification of the fixes

Every reviewer repro was re-run against the fixed tree; wave-2 regression
tests pin all of the above (`tests/test_review_regressions.py`, 46 tests
across both waves). Suite: **313 passed**. Conformance fixtures regenerated
(28 files, each nok tripping exactly its code); examples regenerated
byte-identically; spec §3.1/§4.4/§5.2/§6.2/§6.4 updated; appendix
regenerated from the registry.

## Overall opinion

The architecture held up: one DOM as the single source of truth, every
mutation paired with an invertible event, byte-equality replay as the
correctness oracle, and one registry feeding linter/stylesheet/spec. The
oracle earned its keep — it surfaced or confirmed most of the critical
findings, including bugs in its own SDK. The honest criticisms: (1) the
initial implementation resolved anchors and targets by global id lookup,
ignoring the context each claimed — one family that produced two criticals
and five majors before this review closed it with context-validated
resolution primitives; (2) the serializer treated "what I emit" as
canonical instead of normalizing, which weakened C001 and made `doc_hash`
multi-valued — the exact failure mode the format exists to prevent; (3)
interop was demo-solid but not adversarial-input-solid (rowspan inversion,
URI escaping, silent drops). The review process itself — independent
reproduce-before-report agents per subsystem — was markedly more effective
than the original test suite at finding these, and the lesson is now
codified: every public operation should end in a `lint == []` round-trip
assertion, and anchor resolution goes through validated primitives only.
Durable lessons promoted to `docs/knowledge/architecture.md`.
