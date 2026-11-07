---
date: 2026-11-07 17:41
type: decision
status: active
related: [2026-11-05_1931_decision_import-packs-images-before-baseline.md]
---

# Decision IMPORT-D15c: BMP, WebP and TIFF images are sized and stored once

Id: **IMPORT-D15c**. Decided by the maintainer on 2026-11-06 (the rework plan's P10,
resolved to its alternative). Replaces point 3 of
[IMPORT-D15b](2026-11-05_1931_decision_import-packs-images-before-baseline.md); the rest of
D15b stands.

## Context

D15b packed only PNG, JPEG and GIF, whose size the SDK reads. BMP, WebP and TIFF stayed
inline, so an import stored them twice (body and baseline snapshot), and `pack_assets()`
gave them a 100x100 frame that lays out as a square with empty bands. Tested in the
Chromium and WebKit engines on 2026-11-06: the size is read once, when the image is stored,
and browsers draw it from then on; Chromium cannot show TIFF at all, stored or inline.

## Decision

`AimDocument._image_dimensions` also reads BMP (core and info headers; a negative height
means top-down), WebP (`VP8 `, `VP8L` and `VP8X`) and TIFF (both byte orders, the first IFD,
tags 256 and 257), standard library only, bounded on hostile input: anything malformed,
zero or truncated returns `None`. Importers then store every raster image they accept once,
and `pack_assets()` frames these formats at their real proportions. Data URIs that are not
strict base64, and formats the SDK cannot size, stay inline as before.

## Consequences

A stored WebP exports to DOCX as the `[image: alt]` placeholder, as an inline one already
did (python-docx cannot embed WebP); BMP and TIFF embed.
