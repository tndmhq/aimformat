---
date: 2026-07-24 14:09
type: plan
status: active
related: []
---

# Plan: native DOCX importer + v0.4 typography vocabulary

Replace docling on the `from_docx` path with a purpose-built importer that
preserves source styling, and extend the spec so that styling is
expressible. Motivation: docling's document model carries only five boolean
formatting flags + hyperlinks — font family/size, color, highlight, and
alignment are structurally unrepresentable in it, so DOCX ingestion loses
essentially all typography regardless of what the mapper does (verified
empirically on a styled corpus, 2026-07-24). The format itself already
expresses most of what is lost; the remainder is this plan's spec delta.

## 1. Spec v0.4 delta (typography, following the v0.3 literal-paint doctrine)

§3.3 doctrine: continuous-or-local values → validated inline `style`;
finite sets → classes; document-wide constants → theme slots. Applied:

- `font-size` — new whitelisted inline style prop, grammar
  `^\d+(\.\d+)?pt$` (pt only; renderers compute).
- `font-family` — new whitelisted inline style prop, value grammar shared
  with the theme font-stack slots (`^[A-Za-z0-9 ,'\-]+$`). Theme roles
  (`font-heading/body/mono`) remain the document-wide mechanism; a literal
  family is the local exception — same duality as literal vs. brand paint.
- `text-justify` — joins the alignment classes (finite set stays classes).
- `text-7xl/8xl/9xl` — type-scale extension (already slated for this rev).
- Normative pt↔type-scale table (appendix) so class-based sizes export
  deterministically to DOCX/print.
- Gating: the new props/classes are marked since-0.4 in the registry + an
  S-rule mirroring S032 (declared version < 0.4 + new construct → error);
  `aim.css` and Appendix A regenerate from the registry; version upgrades
  recorded as `aim:version` events (paint precedent).

Explicitly rejected: arbitrary units (px/rem/%), `font-weight`/`line-height`
inline (classes suffice for now), open-ended CSS of any kind.

## 2. Importer architecture (`from_docx` v2)

Parsing backbone: **`docx-parser-converter` (MIT, pinned)** — selected after
an ecosystem review (docling's DOCX backend: strong structural machinery but
the 5-flag model ceiling; mammoth: semantic-by-design, refuses visual
styling; docx2python: flat extraction, no document model; commercial SDKs:
license-incompatible). Deciding factors: typed, cleanly separable parsing
layer (~8.3k LOC; parsers/models import no converters), full run/paragraph
property coverage incl. justification, real style-inheritance resolver
(basedOn chain + docDefaults + circular guards), numbering engine with
counters/restarts, tables with vMerge/gridSpan/borders/shading, images
(blip → base64), 1,622 tests + 19 real .docx fixtures with goldens, active
maintenance, lxml+pydantic-only dependencies.

Structure (new code under `src/aimformat/convert/`):

- **Adapter seam** — a single module owns every dpc import and exposes a
  narrow internal interface; dpc models never leak into the public API.
  Escalation ladder for upstream needs: PR upstream first (initial PR:
  officially export the parse API, currently deep-import only) → wrap or
  subclass at the seam → temporary fork pin as stopgap → vendor (MIT
  permits) only if the project is abandoned.
- **AimConverter** — walks the parsed Document model and emits `.aim`
  chunks: one heading/paragraph/list-item/table-row per chunk (granularity
  unchanged), containers via the existing containerize step. Styling
  emission: run color/highlight → literal paint (+`<mark>`); font size →
  inline pt; non-theme font runs → inline family; alignment →
  `text-left/center/right/justify`; bold/italic/underline/strike/sub/sup →
  existing tags. Emit-only-when-differing-from-inherited-default rule keeps
  unstyled documents class/style-free and diffs clean. OOXML toggle-property
  semantics (`w:b` in style layer vs. direct formatting) handled at the
  seam.
- **Theme resolver** — `theme1.xml` major/minor fonts + docDefaults →
  `--aim-font-heading/body/mono`; accent1..4 → `--aim-brand-1..4`;
  themeColor/themeTint references on runs resolved to literal hex.
- **Edge-case ports** (adapted from docling's MIT msword backend, with
  attribution notice): strict-OOXML namespace normalization, textbox
  content, OMML equations → text, checkbox glyphs; Wingdings/symbol →
  Unicode mapping re-derived from public correspondence tables.
- **Pagination**: the existing `_docx_pages` side pass (sectPr + explicit
  breaks) folds into the importer natively.
- `from_docling` stays as-is for PDF and any docling-shaped input, with
  bug fixes: table parented under a list group must not be dropped
  (violates the module's no-silent-drop contract), table cells keep inline
  formatting, heading-level mapping no longer demotes (Heading N → hN when
  no separate title node claims h1).

Packaging: extra `docx` = `python-docx` (export) + `docx-parser-converter`
(import; lxml+pydantic) — `from_docx` no longer requires the heavy `ingest`
extra; `ingest` keeps docling for `from_pdf`/`from_docling`.

## 3. Export symmetry

`to_docx` honors the new vocabulary: inline `font-size`/`font-family` →
run properties; alignment classes (incl. justify) → paragraph alignment;
type-scale classes → pt via the normative table; theme font-stack slots →
Normal/Heading style fonts (closing the existing gap where only color
slots export). `to_html`/`to_pdf` are free — the generated stylesheet and
inline styles already flow through.

## 4. Tests

- Golden corpus: dpc's MIT .docx fixtures + our styled corpus → committed
  `.aim` goldens; red-first tests for the three `from_docling` bugs.
- Round-trip: DOCX → .aim → DOCX property assertions (size/family/color/
  alignment idempotent under the normative table).
- Lint/spec: new-construct gating (0.3-declared file with v0.4 typography
  → error), css regen determinism, registry→Appendix A regen.

## 5. Open questions (deferred, recorded)

- Numbering fidelity: Word multi-level literal markers (`1.1.1`, `(a)`)
  currently renumber semantically; list-format classes or an `ol` start
  mechanism are a possible later rev — decide with real-user evidence.
- `h1` policy when Word Heading 1 and a Title style coexist.
