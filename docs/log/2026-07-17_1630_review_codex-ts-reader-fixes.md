---
date: 2026-07-17 16:30
type: review
status: done
related: []
---

# Review: Codex local review of the TS reader — 4 findings, all fixed

An external Codex review of `@aimformat/reader` on `wt/reader-ts` (local,
pre-publication; the branch waits for R-AF-1). All four findings were
TS-side bugs — the Python SDK matched `spec.md` in every case:

1. **P1 — attribute sort order** (`ts/src/canonical.ts`): the alphabetical
   middle of the attribute order (spec §11.1) compared UTF-16 code units;
   Python's `sorted()` orders code points. A BMP char above U+D7FF vs an
   astral `data-x-*` name sorted differently → different `Chunk.html` and
   `docHash`. Fixed with `compareCodePoints` (also applied to class-token
   sort, same latent bug — unreachable through lint-clean docs because the
   class vocabulary is closed, V005). New `unicode-attrs` parity fixture
   pins the PUA-vs-astral case; it fails against the old comparator.
2. **P2 — O(n²) view construction** (`ts/src/document.ts`): `chunkView`
   re-walked the whole body per id (`findChunk`). Now one template-skipping
   walk precollects every id's member groups (~3s → tens of ms at 6000
   chunks); a large-doc guard test enforces the complexity.
3. **P2 — explicit `null` page fields** (`ts/src/document.ts`): `??` let
   `{"page":{"size":null}}` silently default; Python (`dict.get`) and the
   spec's forward-compat rule default only on *absence*. Explicit null now
   fails the D003/D004 grammar checks; a whole-`page` null stays "unset".
4. **P3 — numeric character references** (`ts/src/parser.ts`): decoded by
   raw code point (`&#128;` → U+0080, NUL/surrogates passed through).
   Ported CPython's `html._replace_charref` table, pinned against Python
   output.

No Python changes were needed. Durable lesson promoted to
[knowledge/architecture.md](../knowledge/architecture.md) (TS reader
invariant 4: port Python semantics, not JS defaults). Gates green: ruff,
mypy, pytest, `aim lint`, tsc, prettier, vitest (62).
