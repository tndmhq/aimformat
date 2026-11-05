---
date: 2026-11-05 19:31
type: decision
status: active
related: [2026-10-01_2135_decision_pack-imported-images-deferred.md, 2026-10-01_2135_decision_history-baseline-event.md, 2026-11-05_1816_plan_pr40-rework.md, 2026-11-05_1931_decision_toc-compact-sections.md]
---

# Decision IMPORT-D15b: importers store each embedded image once, in the asset registry, before the baseline

Id: **IMPORT-D15b**. Supersedes [IMPORT-D15](2026-10-01_2135_decision_pack-imported-images-deferred.md),
which deferred this work. Decided by the maintainer on 2026-11-05: the baseline keeps its
full copy of the text, and imported images are stored once. Items marked *provisional* were
chosen during the rework and await review ([plan](2026-11-05_1816_plan_pr40-rework.md),
items P9-P15).

## Context

IMPORT-D9's snapshot copies the body, so every inline `data:` image was stored twice. On
sample3, two images took 22,624 of 35,584 tokens. §9 already defines the packed form
(`<svg role="img" aria-label="…"><use href="#asset-…"/></svg>` with the bytes once in
`<aim-assets>`).

## Options considered

- Defer (D15): superseded.
- A snapshot that refers to body images instead of copying them: rejected; the baseline
  keeps its full copy of the text (IMPORT-D9 as designed, maintainer 2026-11-05).
- **Pack on import, before any history is written**: chosen.

## Decision

Decided (maintainer, 2026-11-05):

1. Importers move embedded images into the asset registry before the baseline, so the body
   and the snapshot reference one stored image. No format change; the importers' default
   output changes.

Provisional (awaiting the maintainer):

2. Packing happens before any history is written. A tracked DOCX import packs its reject
   and accept views through one collection before alignment (packing only the body made
   every tracked import with an image raise `ParseError`), and stores an image only Word's
   Accept All has for the card that brings it. The docling and Markdown importers pack each
   block before `add_chunk`, so the `add` events kept on the H008 fallback path hold
   references, not bytes.
3. PNG, JPEG and GIF are packed (the SDK reads their size). BMP, WebP, TIFF and data URIs
   that are not strict base64 stay inline, and the import goes on; the DOCX import report
   counts them.
4. §9.3: a baseline snapshot is retained history; `gc_assets` counts its references.
5. Two new verifier warnings, since packed bytes sit outside `doc_hash`: **S036**, a
   `use href="#asset-…"` in the body, a pending payload or the retained history (snapshots
   included) that resolves to no symbol; **S037**, a raster asset whose id is not the
   sha256 of its bytes (§9.2). Warnings, not errors.
6. §7 and §9: the asset registry is content-addressed state, outside `doc_hash` but not
   regenerable (after an import it holds the only copy of each image).
7. `to_docx` and `to_markdown` export packed images (both dropped them).

Not decided here: images that agents or people add after the import stay inline in their
history events unless every save packs them.

## Consequences

- Whole-file size: sample3 67,614 → 50,862 characters (about 33,900 → 22,900 o200k tokens);
  legal-addendum 63,087 → 59,529 (about 22,100 → 19,500). Image-free fixtures do not change.
- Raw readers meet `<svg role="img"><use>` in the body; the bytes sit once in `<aim-assets>`
  after the content. Browsers render it natively.
- The condition D15 named, a downstream editor that shows and edits packed images, is not
  met: an editor built on today's form loads such a chunk as a read-only atomic node and
  draws no image. That editor work is a prerequisite before an editor adopts a release with
  this change.

## Compatibility and migration

No format change: existing files are unchanged and nothing is migrated. S036 and S037 are
warnings, so no file that passed lint before fails now.

## Tests

`tests/test_packed_images.py` (a tracked import with images, the H008 fallback, exporter
round trips, gc after an out-of-band delete, both warnings, a bad data URI and a BMP staying
inline), the existing DOCX import tests, and the conformance fixtures `ok_packed_image`,
`nok_S036_unresolved_asset_reference` and `nok_S037_asset_id_mismatch`.
