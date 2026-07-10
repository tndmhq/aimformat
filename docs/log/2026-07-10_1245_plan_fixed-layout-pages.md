---
date: 2026-07-10 12:45
type: plan
status: done
related: [2026-07-07_1659_decision_v01-open-points-resolved.md, 2026-07-07_2243_plan_converters.md]
---

# Fixed-layout pages: canvas-pt convention, PDF page geometry, DOCX degradation

Makes `aim-slide` a correct *fixed-layout page* end to end: a slide's canvas
becomes a real page at its own size in PDF, degrades honestly in DOCX, and
canvas numbers gain a physical meaning. PPTX conversion (either direction)
stays future work; nothing here blocks it — the convention below makes the
eventual geometry map an identity.

## 1. Canvas convention: canvas px ≡ points (informative, stays v0.2)

A slide canvas declared `style="width:Wpx; height:Hpx"` is *conventionally*
sized so that 1 canvas px prints as 1 typographic point (1/72 in):

- 16:9 deck slide: **960×540** (the native PPTX point size) — replaces the
  1920×1080 convention used in examples so far;
- paper-sized pages are their point size: A5 portrait ≈ **420×595**, A4
  portrait ≈ **595×842**, etc. (decimals are legal per the style grammar).

This is a *convention*, not a constraint — any canvas size remains valid, and
existing documents keep their declared sizes (the PDF exporter reads each
slide's own canvas). Normatively nothing changes in v0.2: the registry, the
stylesheet, and hashes are untouched (the v0.2 `aim.css` is frozen on PyPI —
any stylesheet change is v0.3 territory, see §5).

Changes: one informative sentence in spec §3.3 + an Appendix B
recommendation; regenerate examples/fixtures/spec snippets to 960×540 (halved
geometry); add a **booklet example** (`examples/booklet.aim`: A5 canvas,
positioned image figure, decorative text) exercising the paper-page case;
mention the convention in `skills/aimformat/references/format.md`.

## 2. PDF export: each slide prints as its own page, at its own size

Today `to_pdf` gives every page the document's global page setup (A4
default): a slide canvas lands *on* an A4 portrait page, overflowing/clipped.
Fix, entirely inside the exporter's injected print CSS (never `css.py`):

- per slide, emit a named page and assign it:
  `@page pg-<id> { size: <W>pt <H>pt; margin: 0 }` and
  `aim-slide[data-aim-container="<id>"] { page: pg-<id> }`;
- print-scale slides ×4/3 (zoom 1.3333) so W css-px of content fills W pt of
  paper (1 px = 0.75 pt physically) — the px≡pt semantics;
- mixed documents: flowing content keeps the `aim:doc` page setup; a named
  page switch forces the break on both sides of a slide, which matches the
  existing "each slide is its own page" print rule.

Verify Chromium named-page support empirically first (Playwright-pinned
Chromium is recent; expected fine). Fallback if not: single-`@page` sizing
for all-slide documents + scale-to-fit for mixed ones. Tests parse `MediaBox`
sizes straight from the PDF bytes (no new dependency): a 960×540 deck yields
960×540 pt pages; an A5 booklet page yields 420×595 pt; a mixed doc yields
A4 pages with canvas-sized pages interleaved.

## 3. DOCX export: linearize slides instead of dropping them

`export_docx.py` currently returns on `aim-slide` — a deck exports as an
**empty document with no warning**. Replace with the Markdown exporter's
degradation contract, adapted to Word:

- a page break before each slide's content (mirrors print semantics), then
  the slide's chunks as ordinary flowing blocks in reading order (geometry
  and classes drop, text/structure/marks survive);
- in-slide chunks now flow through the ordinary chunk machinery, so pending
  proposals on them ride the tracked-changes path like any other chunk;
  retire the "orphan in-slide adds surface at the document end" special case;
- `emit_figure`: honor an authored `style` width on `img`/`figure`
  (px → inches at 96 dpi, capped to the content width) instead of the
  hardcoded 4.5 in.

## 4. Out of scope here

PPTX import/export; `from_docling` slide reconstruction; speaker notes;
masters/layouts. §5 items ride the next spec rev.

## 5. Deferred to the next spec rev (v0.3 stylesheet changes)

Type scale `text-7xl/8xl/9xl` (72/96/128 px — hero sizes for canvases),
speaker-notes vocabulary, named slide-size defaults in `aim:doc`. All change
the generated stylesheet and therefore X006-invalidate existing v0.2
documents if done in-place; they take the same train as the planned
2026-07-28 spec revision.

## Definition of done

Full suite green (incl. new PDF geometry + DOCX linearization + image-width
tests); spec snippets lint; examples/fixtures regenerated; CHANGELOG entry;
this entry flipped to done.
