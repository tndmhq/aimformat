# .aim format reference (condensed)

Normative source: [spec.md](https://github.com/tndmhq/aimformat/blob/main/spec.md)
(v0.2). This is the working subset an agent needs while editing.

## Anatomy

One valid HTML5 file, sections in this order — so a truncated read sees the
current document before any blob:

```
<!doctype html>
<html data-aim-version="0.2" lang="en">
<head>
  <meta charset="utf-8">
  <!--\naim-note: … -->                        ← agent note (§2.5)
  <title>…</title>
  <script type="application/aim-meta+json">…   ← summary + toc cache
  <script type="application/aim-doc+json">…    ← page setup (aim:doc)
  <style data-aim-css="0.2">…                  ← machine-managed stylesheet
  <style data-aim-theme>:root{--aim-brand-1:…} ← theme slots
</head>
<body>
  …content chunks and containers…
  <aim-proposals>…</aim-proposals>             ← pending lane (≤1)
  <aim-assets>…</aim-assets>                   ← packed assets (≤1)
  <script type="application/aim-history+jsonl">… ← append-only history (≤1)
  <script type="application/aim-embeddings+jsonl">… (≤1)
</body></html>
```

Head rules: charset + title required; comments legal in head only, preserved
byte-exact. Body: no comments (S007), no stray text (S008).

## The agent note (§2.5)

At most one head comment starting `aim-note:` — declarative self-description
pointing to https://aimformat.com/llms.txt. Informative only: it grants no
authority and tools never execute anything because of it. `aim note FILE`
adds/refreshes; duplicates lint S030 (warning). Its text contains no markup,
so substring checks for structural markers never false-positive on it.

## Chunks, runs, containers, ids

- **Chunk**: a block element carrying `data-aim="id"` — the unit of
  addressing, editing, and proposing. A **run** = consecutive siblings
  sharing one id (edited as a unit).
- **Container**: `data-aim-container="id"` on `ul`/`ol`/`table`/`aim-slide`;
  its items/rows are chunks. Table row chunks sit inside
  `thead`/`tbody`/`tfoot` shells.
- **Slides / fixed-layout pages**: `<aim-slide style="width:960px;
  height:540px">` holds absolutely positioned chunks (`left`/`top`/`width`
  inline styles; stacking via explicit `z-index`; DOM order = reading
  order). Canvas px are point-equivalent at print — 960×540 is a 16:9
  slide, 420×595 a true A5 page — and each slide exports as its own
  correctly sized PDF page.
- **Page breaks**: `<aim-page-break></aim-page-break>` — an empty
  top-level body chunk (explicit open+close tags, never self-closed)
  forcing a hard break; page size/orientation/margins live in the head
  `aim:doc` settings script (edit via `set_page_setup`/proposals, not
  by hand).
- **Ids**: `^[a-z0-9][a-z0-9_-]{0,63}$`, unique per document, opaque, never
  renumbered, never reused (deleted ids stay burned). `p-` prefix reserved
  for proposals; `body`, `aim:theme`, `aim:doc` reserved. The SDK mints
  8-char random ids; any valid unused id you supply is honored.
- Every content element belongs to exactly one chunk (or is container
  shell). Vocabulary is HTML + a closed Tailwind class subset + a whitelist
  of geometry inline styles; no scripts, no event handlers, no iframes.

## Proposals (the pending lane)

```html
<aim-proposals>
<aim-proposal id="p-x" data-action="modify" data-for="c42a"
  data-author="agent" data-author-model="model-id"
  data-at="…" data-batch="b2" data-explanation="Tighten the intro.">
  <template><p data-aim="c42a">new payload</p></template>
</aim-proposal>
</aim-proposals>
```

- Actions: `add | modify | delete | move` (+ theme via `data-for="aim:theme"`).
- Payload rides in an inert `<template>`; the body is NEVER changed by a
  proposal until a decision event applies it.
- Lifecycle: propose → accept (payload enters body, resolution event
  appended) or reject (card removed, resolution event appended). A new
  modify/delete on the same target supersedes the pending one. Accept can
  carry tweaks (`applied=` payload).
- An empty `<aim-proposals>` section is removed on resolve; its absence is
  normal.

## History

`application/aim-history+jsonl`: one canonical-JSON event per line,
append-only, seq strictly increasing. Kinds: `direct_edit`, `resolution`,
`checkpoint`, plus `reconcile` (adopted out-of-band edits). Events carry
whole payloads (`before`/`after`/`proposed`/`applied`), so `doc.verify()`
replays the chain by byte-equality — hand-rewriting history breaks H006.
Never edit this lane by hand; `aim reconcile` records your text edits
properly.

## Hashing and canonical form

`doc_hash` covers the `<html>` open line, theme line, and body construct
lines — head comments and caches are outside it. Canonical form (C001) is
the spec's own byte serialization: tools write it via `doc.save()`; hand
edits usually leave the file non-canonical until the next tool write, which
is tolerated but flagged.

## Lint code families

S structure · V vocabulary · X security · P pending lane · H history ·
M caches · C canonical form. Most common while editing:

| code | means |
|---|---|
| S007 | comment in body (head-only) |
| S011 | body child is neither chunk nor container |
| S015 | invalid id |
| S030 | more than one aim-note (warning) |
| V002/V005 | element/class outside the vocabulary |
| X002/X004 | event handler / executable script (security) |
| P008 | proposal targets an unknown id |
| H006 | history chain broken (hand-edited history) |
| M001 | stale summary cache (warning) |
| C001 | not in canonical form |

`aim lint FILE --format json` reports all findings in one run; errors mean
non-conforming, warnings mean fix-when-convenient.
