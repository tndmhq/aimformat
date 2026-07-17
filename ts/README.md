# `@aimformat/reader` — TypeScript read library for `.aim`

The official TypeScript reader for the [`.aim` document format](../spec.md).
It parses a `.aim` document string into a read-only projection: ordered
content nodes, chunks with first-class runs, containers with recursive
members, the pending-proposal lane, theme and page settings, and the
document hash.

**Read model only, writes never.** Every mutation of a `.aim` file — edits,
proposals, accept/reject, history — goes through the Python SDK
(`pip install aimformat`), which owns canonical form and the event log.
This library mirrors that SDK's read surface so viewers and editors can
project a document without re-implementing (and silently forking) write
semantics.

## Install and use

The package ships TypeScript source directly (`exports` points at
`src/index.ts`) and is consumed from a checkout of this repository — there
is no build step and no npm release; Vite, Vitest, and other
TypeScript-aware toolchains transpile it in place. It has zero runtime
dependencies and runs identically in browsers, Node, and workers.

```ts
import { AimDocument } from "@aimformat/reader";

const doc = AimDocument.parse(aimFileText);

doc.title; // "Q3 Services Proposal — Acme GmbH"
doc.nodes; // ordered top-level constructs (chunks and containers)
doc.chunks; // every chunk, flat: { id, container, tags, html, text, isRun }
doc.proposals; // the pending lane: { id, action, target, payloadHtml, … }
doc.theme; // { "--aim-brand-1": "#1a73e8", … }
doc.pageSetup; // { size, orientation, marginsMm, contentWidthMm, … }
doc.docHash; // "sha256:…" — the reduced-projection hash (spec §11.3)
doc.get("c42a"); // O(1) by-id lookup (chunk or container)
```

### The projection

- **`nodes`** — the document in order. Each node is a discriminated union:
  a `Chunk` (`kind: "chunk"`) or a `Container` (`kind: "container"`).
  Containers carry their ordered `members`, recursively where the format
  allows nesting (a slide holds chunks and nested list/table containers;
  lists and tables hold item chunks).
- **`Chunk`** — `id`, the `container` it lives in (`"body"`, a container
  id, or a slide id), member `tags` in order, canonical `html` (run members
  concatenated), plain `text`, and `isRun`. Runs — sibling `li`/`tr`
  elements sharing one id — are one chunk with several members, never
  keyed-by-id-with-overwrites.
- **`proposals`** — the pending lane as read-only cards: action, target,
  author, anchor (`anchorContainer` / `anchorAfter` / `anchorShell`),
  canonical `payloadHtml`, explanation.
- **`theme`, `docSettings`, `pageSetup`, `stylesheet`, `note`, `meta`** —
  the head state: theme slots, the `aim:doc` settings object, the resolved
  page geometry (registry defaults fill gaps), the embedded stylesheet, the
  agent note, the metadata cache.
- **`assetIds`** — ids present in the packed-asset registry.
- **`historyJsonl` / `embeddingsJsonl`** — the trailers as opaque blocks.
  The reader does not interpret events; replay, verification, and time
  travel are the Python SDK's job.
- **Lookups** — `get(id)` is O(1); `getAll(id)` is a multimap, so even an
  invalid document with repeated ids never silently drops a node.

Malformed embedded JSON (the meta cache, the settings block) throws on
access, not at parse time — mirroring the Python SDK, where corrupt caches
are an error but don't make the document unreadable.

## Parser: a strict canonical-subset scanner

The reader implements its own small scanner for the canonical `.aim`
serialization (spec §11) instead of a general HTML5 parser. Why:

- **Canonical form has exactly one spelling per construct.** Explicit
  open/close tags, double-quoted attributes, raw UTF-8 with only
  `&amp; &lt; &gt; &quot;` escapes, no implied tags — and `aim lint`
  (rule C001) enforces this byte-exactly. A conforming file simply never
  exercises the hard parts of HTML5 parsing (foster parenting, implied
  tags, error recovery).
- **One code path everywhere.** Pure string scanning — no `DOMParser` in
  browsers with a different parser in tests, no Node-only dependencies.
  What the test suite parses is exactly what production parses.
- **Fidelity to the reference implementation.** The Python SDK's own reader
  (`dom.py`) is the same kind of transparent, non-inferring parser; the two
  implementations agree by construction, which the parity suite
  (`tests/parity/` at the repository root) then pins field by field —
  including `docHash` byte-equality.

The trade-off, deliberately: **non-canonical input is rejected**, with an
error saying why (tag soup, single-quoted attributes, exotic named entity
references, stray `<`). A file that fails here would fail `aim lint`
anyway; run it through `aim normalize` first. Numeric character references
and the five core named references are decoded; unknown named references
are an error rather than a silent misread.

## Development

```sh
npm install
npm test              # vitest, includes the cross-implementation parity suite
npm run typecheck     # tsc --noEmit
npm run format:check  # prettier
```

`src/registry.data.ts` is generated from the format registry
(`../src/aimformat/registry.json`) by `../scripts/gen_ts_registry.py`; a
Python-side test fails if it goes stale. Never edit it by hand.
