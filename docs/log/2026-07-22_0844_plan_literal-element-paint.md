---
date: 2026-07-22 08:44
type: plan
status: done
related:
  - 2026-07-22_0814_report_colour-model-problem.md
---

# Plan: canonical per-element paint

## Outcome

Add a first-class, local paint mechanism to `.aim` without opening arbitrary
CSS. A conforming element may carry these three inline style properties:

```aim
<h1 style="color:#ff69b4">Pink title</h1>
<p style="background-color:#fff1f7">Tinted paragraph</p>
<p class="border" style="border-color:#ff69b4">Pink border</p>
```

The values are literal sRGB paint. They affect only the element and the
ordinary CSS painting/inheritance beneath it; they do not consume or mutate a
document theme slot. Existing palette classes and theme-backed brand classes
remain useful for reusable design tokens. Literal paint and theme paint are
different semantics, not two spellings that canonicalization must collapse.

The change is complete only when the Python SDK, official TypeScript reader,
HTML/PDF path, DOCX exporter, and every consumer agree. The reported title case must become one
chunk proposal, not a coupled theme-plus-chunk proposal.

## Fixed design decisions

**Decided by the maintainer on 2026-07-22**, not inferred by an agent — this
repo's open format questions are the maintainer's to settle (AGENTS.md
§Repository), and Codex was right to ask on #20 that the approval be recorded
rather than assumed. These are implementation requirements, not questions for
the implementing agent.

1. **Syntax:** use native inline CSS properties in `style`, not a new
   `data-aim-*` attribute and not an arbitrary-value class.
2. **Properties:** add exactly `color`, `background-color`, and
   `border-color`. Do not admit any other presentation property in this work.
3. **Value grammar:** exactly lowercase six-digit sRGB hex,
   `^#[0-9a-f]{6}$`. No three/eight-digit hex, uppercase, named colours,
   `rgb()`, HSL, alpha, `transparent`, `currentColor`, `var()`, `url()`,
   `!important`, or other CSS functions. Removing the declaration is how an
   override is cleared. Theme values keep their existing grammar in this
   change.
4. **Canonical order:** retain the existing geometry order, then append paint
   in this order:
   `left, top, width, height, transform, z-index, color, background-color,
   border-color`. Appending rather than interleaving means an existing
   document's BODY serializes unchanged; a mixed slide title serializes
   geometry before paint. (The embedded stylesheet still refreshes on write,
   as it always has — decision 8.)
5. **Cascade:** native CSS semantics are normative. Inline paint wins over
   every class. `color` inherits. `background-color` and `border-color` do not
   inherit. `border-color` does not create a border; it only recolours a border
   supplied by `border`, `border-t`, `border-b`, or another rendering default.
6. **Classes and theme stay:** do not remove the fixed palette, four brand
   slots, or brand utilities in this implementation. A brand class means
   “follow this document token”; an inline value means “use this exact paint.”
7. **Conflicting classes stay as-is:** the existing alphabetic stylesheet
   cascade for multiple colour utilities is not redesigned here. Inline paint
   simply outranks it. A separate change may lint mutually exclusive classes.
8. **Versioning: this ships as v0.3.** Maintainer decision, 2026-07-22,
   reversing the earlier "additive to draft 0.2" line after Codex pointed out
   what it costs: a document using the new properties is rejected by an older
   0.2 validator (V007) and accepted by a newer one, so two tools claiming to
   implement the same spec version would disagree about conformance. The
   version is the compatibility signal, and it must keep meaning one thing.
   So bump `data-aim-version` to `0.3` for documents that use paint, and take
   the toolkit to the matching version.

   **What that costs, checked in the code rather than assumed** (Codex on
   #20): `dumps()` refreshes the machine-managed stylesheet and stamps
   `data-aim-css` with `REGISTRY.spec_version` unconditionally
   (`document.py`), and lint compares both markers against the toolkit's
   version (S002/S006). So three things follow, and the plan must say them:

   - **"Byte-identical" narrows to the BODY and the authored head.** The
     embedded stylesheet is machine-managed and already refreshes on every
     write — that is true of any registry change, not just a version bump, so
     the earlier blanket promise was never accurate.
   - **`data-aim-version` is authored, not machine-managed: never rewrite it.**
     A 0.2 document stays a 0.2 document. It becomes 0.3 when, and only when,
     paint is added to it.
   - **S002/S006 must become "accept older, warn on newer".** Today they warn
     whenever the document's version differs from the tool's in either
     direction, so a 0.3 toolkit would warn on every existing 0.2 document —
     noise on files that are perfectly valid. A tool implementing 0.3
     understands 0.2; it should warn only for a version it does NOT implement.

   **Adding paint to an existing 0.2 document needs a recorded upgrade, not a
   silent edit.** Verified rather than assumed: `data-aim-version` lives on the
   `<html>` open tag, which `doc_hash` covers — flipping it changes the hash,
   so an in-place bump breaks checkpoint verification, while leaving it at 0.2
   would declare a version the document no longer conforms to. Neither is
   allowed, so the toolkit must offer the third option and this repo already
   has the shape for it: *every state change mutates the tree AND appends the
   matching event*. The version bump is a state change, so it gets an event,
   and `verify()` passes by construction the way `reconcile` does.

   Concretely: adding the first paint declaration to a 0.2 document emits an
   upgrade event alongside the paint proposal/edit, and a document whose
   history cannot take that event (pruned, damaged) refuses paint rather than
   producing an unverifiable history. Do not rewrite historical checkpoint
   hashes or migrate documents that never use paint.

9. **No source-tree mutation during export:** computed paint is derived once
   into export-local state. Never copy inherited styles or classes back onto
   the parsed document.

## Scope boundaries

Included:

- conforming authoring, linting, canonical serialization, hashing, events,
  proposals, and Python/TypeScript parity for all three properties;
- exact browser/HTML/PDF rendering through native CSS;
- DOCX clean export for text colour, run/paragraph/cell backgrounds, and
  borders where the AIM element has a visible border;
- DOCX text-colour inheritance across every existing leaf emitter;
- honest, documented DOCX approximations where Word has no CSS box analogue;
- consumer-side AI creation/review/acceptance and manual round-trip
  preservation, planned in the consumer's own repo;
- cross-repo dependency pin bump after the format change reaches
  `aimformat/main`.

Excluded:

- a colour-picker toolbar or other new manual UI;
- arbitrary CSS or additional paint syntaxes;
- redesigning the theme slots, link colour, proposal chrome, or fixed palette;
- preserving source colour during DOCX/PDF/Markdown ingestion. Current
  ingestors drop presentation generally; this change creates the correct
  target representation for a later import project. Measured 2026-07-22, so
  that project starts from fact rather than a survey: a DOCX round trip keeps
  **bold and italic** (they ride markup) and loses **colour, paragraph
  alignment, and font size** — a centred, red, 24 pt paragraph comes back
  bare. Word's Quote style flattens to `<p>` too. Structure, by contrast,
  survives: headings, ordered vs unordered lists, tables and page breaks all
  round-trip cleanly on a realistic business document, with no lint findings. The loss is upstream of us: docling's `formatting` model carries only
  `bold`/`italic`/`underline`/`strikethrough`/`script`, so those properties
  never reach `ingest.py`. Recovering them means a python-docx side pass over
  the original file, as `convert/_docx_pages.py` already does for pagination
  — not a mapping fix. Recorded in
  [`docs/knowledge/architecture.md`](../knowledge/architecture.md);
- exact background/border fidelity for every possible grouping box in Word;
- PPTX export (none exists) and Markdown colour extensions.

## Required behavioural contract

### Conformance and canonical form

The following are lint-clean and byte-stable:

```aim
<h1 style="color:#ff69b4">Title</h1>
<p style="background-color:#fff1f7">Body</p>
<p class="border" style="border-color:#ff69b4">Callout</p>
<h2 style="left:48px; top:32px; width:450px; color:#ff69b4">Slide title</h2>
<p><span style="color:#ff69b4">one run</span> only</p>
```

The following fail V008, not V007: `color:red`, `color:#fff`,
`color:#FF69B4`, `color:rgb(255,105,180)`, `background-color:transparent`,
and `border-color:var(--aim-brand-1)`. An unrelated property such as
`opacity:.5` continues to fail V007.

Canonicalization orders declarations by the registry, removes empty styles,
and keeps the existing last-duplicate-wins rule. Thus:

```html
style="color:#111111; left:2px; color:#ff69b4"
```

serializes as:

```html
style="left:2px; color:#ff69b4"
```

### Rendering and conversion

- HTML export preserves the declaration verbatim after canonicalization.
- PDF uses Chromium and therefore needs no paint translation; add a focused
  regression proving the generated print HTML retains mixed geometry + paint.
- Inline paint beats a conflicting palette or brand class in HTML, PDF, and
  DOCX.
- DOCX `color` resolves to an RGB run colour and inherits through block and
  inline wrappers.
- DOCX inline `background-color` becomes run shading; block/list-item
  background becomes paragraph shading; cell background becomes cell shading.
- A grouping background (`section`, `div`, `blockquote`, list/table wrapper)
  is approximated by shading the emitted descendant paragraphs/cells whose
  own background is transparent. Document this as the Word degradation
  contract; do not claim a single contiguous CSS box.
- DOCX border colour follows the element's border, from a border-making
  utility OR from a base-stylesheet default — `hr` and `blockquote` carry a
  border with no utility present, and `border-color` recolours them in
  HTML/PDF, so their DOCX emitters are in scope too. `style="border-color:…"`
  on an element with no border at all emits nothing, matching CSS.
  (Codex on #20.)
- **Word has no per-side border on a RUN.** `w:rPr/w:bdr` is one border for
  the whole run; per-side borders exist only for paragraphs and cells. So
  `border-t`/`border-b` plus `border-color` on an INLINE element cannot map
  exactly. Documented degradation: colour the whole-run border, and do not
  write a test that demands side fidelity for inline spans. Block and cell
  borders keep exact per-side mapping. (Codex on #20.)
- In tracked mode, inline paint belongs in the `w:ins`/`w:del` run properties
  so old and new colours/backgrounds remain reviewable. Block box paint has
  one paragraph/cell property in Word; prefer an honest run-level tracked
  approximation over silently showing only the proposed paint, and document
  that limitation. `accept-all`/`reject-all` clean exports must have the exact
  chosen block paint.

## Implementation sequence

Work red-first. Do not combine the two repositories in one commit or PR.

### 1. Add failing format and parity tests

In `aimformat`:

1. Extend `tests/test_lint.py` with acceptance for all three properties on a
   block, inline span, and positioned slide child. Add one parameterized V008
   test covering every forbidden spelling above.
2. Change the V007 unit and generated fixture seed from `color:red` to an
   actually unknown property such as `opacity:.5`; otherwise the fixture stops
   testing V007 after `color` becomes registered. Update
   `scripts/gen_fixtures.py` and regenerate, never hand-edit only the fixture.
3. Extend `tests/test_canonical.py` and the normal-form regression tests with
   mixed geometry/paint ordering, duplicate paint last-wins, empty style
   removal, and a `loads(dumps(x)) == x` case.
4. Add matching `ts/test/canonical.test.ts` cases. Include a parity fixture
   containing block paint, inline span paint, and mixed slide geometry/paint,
   then regenerate the Python goldens. This pins `Chunk.html`, proposal
   payloads, and `docHash` across both implementations.
5. Add an SDK lifecycle test: add a painted chunk, propose a paint-only
   modify, accept and reject it on separate clones, verify history, time
   travel, undo/redo, and end every path with `lint(doc.dumps()) == []`.

Do not change implementation until these tests fail for the expected reason.

### 2. Extend the registry and generated artifacts

1. In `src/aimformat/registry.json`, append the three properties to
   `style_props.order` and give each the exact six-digit lowercase grammar.
   Do not reuse the looser theme colour pattern.
2. Let the existing Python `Registry.style_prop_order/style_patterns`, linter,
   and canonical serializer consume the registry change; do not add parallel
   hard-coded Python lists.
3. Extend `scripts/gen_ts_registry.py` to emit both `STYLE_PROP_ORDER` and
   `STYLE_PROP_PATTERNS`. Export them from `ts/src/index.ts`. Generate JS
   regexes from the same registry patterns so consumers can validate rather than
   maintaining a third grammar.
4. Regenerate:

   ```sh
   python3 scripts/gen_spec_appendix.py
   python3 scripts/gen_fixtures.py
   python3 scripts/gen_ts_registry.py
   python3 scripts/gen_parity_fixtures.py
   python3 scripts/dump_projection.py
   ```

5. Review regenerated diffs. Existing fixture/example hashes should change
   only where regeneration re-mints fixture ids or the new parity fixture is
   added; the registry change alone must not change old document bytes.

### 3. Update the normative spec and authoring guidance

1. Rename spec §3.3 from geometry-only language to validated inline styles.
   State the rule as: registered continuous/local values use a closed
   property-and-grammar registry; discrete/reusable choices use classes;
   document-wide constants use theme slots.
2. Explain literal-vs-theme semantics and native precedence with one example
   of each. State explicitly that a local literal is one chunk edit and never
   requires a theme proposal.
3. Update the security rationale: this is not arbitrary CSS because both
   property names and value grammars remain closed; functions, URLs,
   `!important`, and unregistered properties remain invalid.
4. Update `README.md`, `docs/for-agents.md`, the bundled Agent Skill references,
   and `CHANGELOG.md`. The agent guide should prefer theme classes when the
   user asks for a reusable document role and literal style when they ask for
   one exact/local colour — and it should give the **reason**, not just the
   rule: an agent editing a document usually sees part of it, and a literal
   value is the only one of the two that cannot repaint something it was never
   shown. That is the safety property this change buys. A rule carrying its
   reason survives paraphrase into someone else's system prompt; a bare
   instruction does not.
5. **Say what the guidance must STOP saying, not only what it gains.** Any text
   of the shape "colouring an element means setting a slot AND adding the
   matching class" describes the mechanism this change replaces, and a guide
   carrying the old rule beside the new one will produce both edits — a coupled
   theme-plus-chunk proposal is precisely the reported defect. Sweep
   `for-agents.md`, the Skill, the spec prose and the README for it, and keep
   the shared-slot warning ("changing a slot repaints every element using it")
   scoped to deliberate theme edits so it no longer fires on a one-off colour.
6. `docs/for-agents.md` and the Agent Skill state these rules independently and
   will drift apart. Make them agree on substance, and write both for **any**
   client of the format — an MCP client, a coding agent, some editor's own
   model — never for one consumer's edit loop.
7. Re-read the changed README/spec/guide prose for plain, non-marketing
   language before landing. (Deliberately not a named checklist: the one this
   line used to invoke lives outside this repo, so it was a gate no reader
   here could run.)

### 4. Replace DOCX's colour special case with a computed-paint resolver

Create one small internal module (for example `src/aimformat/paint.py`) rather
than expanding regexes throughout `export_docx.py`.

The resolver must:

1. Parse registered class declarations from `REGISTRY.class_declarations` and
   canonical inline declarations from `style`.
2. Resolve theme-backed `var(--aim-brand-N)` values through the document
   theme/default palette.
3. Match the generated stylesheet's class cascade — **including shorthand
   resets, which same-property matching gets wrong.** Measured in the current
   stylesheet: `.border-red-600{border-color:#dc2626}` is emitted at byte
   3353 and `.border-t{border-top:1px solid #e5e7eb}` at 3391, so the
   shorthand lands LAST and resets the colour. `class="border-t
   border-red-600"` therefore renders GREY in a browser today. A resolver
   matching only `border-color` declarations would emit red and disagree with
   every other renderer (Codex on #20).

   So the rule is: among class declarations affecting the same paint
   property — longhand or via a shorthand that sets it — the alphabetically
   last registered class wins, exactly as the browser computes it. Inline
   style then wins over all classes, which is the point of this change and
   sidesteps the trap entirely for anyone who uses it.
4. Traverse a root once, carrying inherited text colour and visible ancestor
   background, and store immutable computed records keyed by Python object
   identity. Border state remains direct/non-inherited. The record should
   include text RGB, direct/effective background RGB, border RGB, and visible
   border sides derived from the border-making utilities.
5. Resolve live document roots and separately parsed pending payload roots.
   Give synthetic blocks created by `_block_children/_wrapped` an explicit
   paint context from their source; do not copy attributes into the source
   DOM. Remove the current `_wrapped` “copy colour classes” workaround once
   all emitters use the resolver.
6. Leave browser base-layer colours and Word template defaults alone when the
   author declared no class/style paint.

Unit-test the resolver independently before wiring it to Word. Include class
vs inline precedence, brand resolution, inheritance, non-inheritance,
grouping backgrounds, a border colour without a border, and source-tree
non-mutation.

### 5. Wire computed paint through every DOCX emitter

Refactor run creation so plain and tracked runs share the same paint
application helpers.

1. Text colour: replace `_color_for` and leaf-specific patches with the
   computed resolver. Cover ordinary blocks, nested inline runs, links, lists,
   `pre/code`, figures/captions, table cells, grouping wrappers, slides after
   linearization, pending adds, pending modifies/deletes, and structural table
   replacements.
2. Run background: add a `w:shd` run property with `w:fill=RRGGBB`; do not use
   Word's limited highlight enum.
3. Paragraph/cell background: add idempotent helpers for `w:pPr/w:shd` and
   `w:tcPr/w:shd`.
4. Borders: add idempotent helpers for `w:rPr/w:bdr`, `w:pPr/w:pBdr`, and
   `w:tcPr/w:tcBorders`; use a stable single-line style/size matching AIM's
   one-pixel border utility. Emit only the sides created by `border`,
   `border-t`, or `border-b`.
5. Apply paint before detaching runs into `w:ins`/`w:del`, so revision runs
   retain `w:rPr` identically to ordinary runs.
6. Preserve the exporter invariant that an unpainted document gains no
   explicit Word colour/shading/border and therefore still follows the
   recipient's Word template.

Expand `TestDocxTextColour` into paint-focused test groups. Assert the OOXML,
not only `python-docx`'s high-level view. Required cases:

- literal direct and inherited text colour;
- inline value overriding fixed and brand classes;
- nested spans and mixed `pre` content (the current documented hole);
- block, inline, list-item, and table-cell backgrounds;
- full/top/bottom border recolouring and no border from colour alone;
- clean, tracked, accept-all, and reject-all paths;
- every grouping/leaf family named above;
- no mutation of `doc.dumps()` in any pending mode.

### 6. Verify HTML and PDF paths

`to_html` should require no implementation beyond canonical preservation.
Add a regression that flattens a painted pending proposal under keep,
accept-all, and reject-all.

For PDF, test `_print_html` rather than screenshot colour matching in the
unit suite: assert the canonical mixed geometry/paint style reaches Chromium
after page CSS injection. Add one browser-backed smoke test if the existing
Playwright test environment makes computed-style inspection cheap; do not add
a brittle pixel-golden system for three CSS properties.

### 7. The contract consumers migrate against

Consumer-side migration is out of scope here; each consumer plans its own. All
this plan owes them is the contract:

- the three properties, the `^#[0-9a-f]{6}$` grammar, and the canonical order
  are published in `registry.json` and projected to `@aimformat/reader`, so a
  consumer validates and orders against registry data rather than a hard-coded
  list of its own;
- inline paint outranks every class, `color` inherits, `background-color` and
  `border-color` do not (§Cascade);
- `style` carries geometry AND paint after this change, so anything that
  filters `style` must filter by property rather than dropping the attribute;
- a document without the new properties has its BODY serialized unchanged
  and its `data-aim-version` untouched (the machine-managed stylesheet
  refreshes as it does for any registry change — see §Fixed design
  decisions 8).

### 8. Land in dependency order

Gate:

```sh
python3 -m pytest
cd ts && npm test
```

Rerun every generator and require a clean generated-artifact diff. Update
`src/aimformat/__init__.py` and `CHANGELOG.md` to the 0.3 line (§Fixed design
decisions 8), and make sure the spec, Appendix A, and the conformance fixtures
carry a document at each version — a 0.2 file must still verify.

**This repo lands first**; consumers pin an exact merged SHA afterwards. How
any given consumer sequences its own work is its own business and is not
planned here.

## Commit and review boundaries

- One focused commit/PR here: registry + canonical/parity + spec + converters
  + tests. The plan and report log entries can ride that PR.
- Preserve unrelated working-tree changes, and never stage from a workspace
  root. (Deliberately no snapshot of any particular checkout's dirty state:
  this log is durable memory, and a future agent reading a stale list of
  "unrelated modified files" would work around changes that no longer exist —
  Codex on #20.)

## Completion checklist

- [ ] All three properties share one registry grammar across Python, TS,
      and any consumer.
- [ ] A 0.2 document's BODY serializes identically and its
      `data-aim-version` is untouched; only the machine-managed stylesheet
      refreshes, as it does for any registry change.
- [ ] Documents using paint declare `0.3`; a 0.2 document verifies under the
      0.3 toolkit with NO version warning (S002/S006 accept older).
- [ ] Adding paint to an existing 0.2 document records an upgrade event, and
      `verify()` still passes over the whole history afterwards; a history
      that cannot take the event refuses paint instead.
- [ ] A class-based border colour resolves the same in DOCX as in the browser,
      shorthand resets included (`class="border-t border-red-600"`).
- [ ] Literal local paint needs no theme mutation.
- [ ] No agent-facing text anywhere in this repo still teaches the coupled
      slot-plus-class recipe for a one-off colour.
- [ ] Inline paint wins over classes in every renderer.
- [ ] DOCX text colour inherits across every leaf emitter.
- [ ] DOCX background/border mappings and Word-only degradations are tested
      and documented.
- [ ] Consumer editing/preview paths preserve paint and geometry (tracked in
      the consumer's own plan).
- [ ] This repo merges before any consumer pins it; one commit boundary per
      repo.
- [ ] Full Python and TypeScript gates pass here; consumers gate their own.
- [ ] This entry and its index row are marked `done`; the colour-problem
      report is marked `superseded` or linked to the shipped decision; durable
      styling/export facts are promoted into `docs/knowledge/architecture.md`.
