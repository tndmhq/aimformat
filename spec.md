# The `.aim` document format — specification v0.1

**Status: v0.1 (first published draft).** This is the normative specification
for `.aim`, an AI-native document format in which AI proposals and human
accept/reject decisions are first-class file primitives. The reference
toolkit in this repository (`aimformat` on PyPI, the `aim` CLI) implements
everything specified here; the conformance fixtures under `tests/fixtures/`
pin the rules one file at a time.

Maintained by the aimformat project. Licensed MIT, like everything in this
repository. Contributions: see [`CONTRIBUTING.md`](CONTRIBUTING.md).

**Versioning.** A document declares the spec version it targets in
`<html data-aim-version="0.1">`. The spec follows SemVer with the 0.x
caveat: **every 0.x minor may break**; parsers MUST ignore unknown JSON
fields (with `x_*` reserved for vendor extensions) and MUST treat unknown
event kinds or elements as errors within the same minor version. The
embedded stylesheet is versioned with the spec (`data-aim-css="0.1"`).

The key words MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY are to be
interpreted as described in RFC 2119. Sections marked *informative* define
no conformance requirements. `element@attribute` in prose refers to an
attribute on an element.

---

## 1. Introduction

A `.aim` document is a **single, valid HTML5 file** that is simultaneously:

1. **the rendered artifact** — double-click or serve it, and any browser
   shows the current styled document;
2. **the accepted current version** — a body whose content is fully covered
   by author-chosen, uniquely identified *chunks*;
3. **the pending-change lane** — proposals from AI or humans collected in an
   in-file appendix, visible to any reader, applied only on acceptance;
4. **the full edit history** — an append-only, invertible event log from
   which any past version can be reconstructed and verified against
   checkpoint hashes;
5. **derived caches** — an AI summary, a table of contents, per-chunk
   embeddings, packed assets — that accelerate agents but are never
   load-bearing.

It is written and read accurately by LLMs (HTML plus a finite
Tailwind-vocabulary class subset), reviewable by humans, and manipulable by
any tool with a text editor and the linter. There is no compiler, no
runtime, and no privileged editor anywhere in the document lifecycle.

### 1.1 Motivation (informative)

Documents are increasingly written *with* AI, in formats designed for a
single human at a single cursor. Every team building document workflows
with AI reinvents the same primitives: how to address a region of a
document, how to track which edits came from the model, how to let a human
accept or reject them, how to prove what the document looked like before.
`.aim` makes those primitives part of the file.

| Instead of… | …because |
|---|---|
| Markdown | no layout or geometry, no identity, no change lane — excellent for prose, insufficient wherever formatting is part of the deliverable |
| raw HTML | unbounded vocabulary drifts across models and tools; no identity, history, or proposal semantics |
| DOCX / PPTX | zipped XML that models handle unreliably; revision markup is bound to one editor lineage; not renderable without an office suite |
| PDF | a print format; hostile to programmatic editing |
| a database + API | documents stop being files: no email attachment, no git, no offline, vendor lock-in |

### 1.2 Design principles (informative)

1. **The file is the artifact.** Rendering requires no tooling; proposals,
   history, embeddings, and assets ride along without breaking that.
2. **LLM-familiar substrate, LLM-friendly ordering.** HTML + a closed
   Tailwind-vocabulary subset for fidelity; section order (metadata →
   body → proposals → data trailers) so even a truncated read encounters
   the current document state before any blob.
3. **Propose/accept as format primitives** — proposals with stable identity,
   persisted acceptance state, attribution, and explanations.
4. **Provenance, not policy.** The format records who did what and through
   which flow (proposal vs direct edit); *whether* an agent may write
   directly is tool policy. Structural rule: the body **is** the accepted
   document; proposal content enters it only via a decision event.
5. **Materialized latest, reconstructible past.** History is auxiliary,
   invertible, verifiable, and strippable.
6. **Documents and slides unified** — same chunks, same proposals, same
   history; slides add a fixed-canvas container with positioned children.
7. **Graceful degradation tiers**: raw browser (styled current document plus
   a readable pending-changes memo) < viewer (word-level diffs, slide
   navigation) < editor (full review UX). Corruption of any trailer never
   corrupts the document.
8. **Canonical form is the format's own.** Equality is byte equality of a
   spec-defined serialization — no editor's parser is the arbiter of truth.
   (Systems that let a rendering library's serializer define canonical form
   implicitly end up chasing phantom diffs indefinitely.)

---

## 2. Document anatomy

A conforming document has this shape (ids are shortened for readability;
see §4.4 for id rules):

```html
<!doctype html>
<html data-aim-version="0.1" lang="en">
<head>
<meta charset="utf-8">
<title>Q3 Proposal — Acme GmbH</title>
<script type="application/aim-meta+json">
{"summary":{"as_of_seq":58,"doc_hash":"sha256:9f2c…","model":"model-id","text":"Three-year services proposal…"},"toc":[…]}
</script>
<style data-aim-css="0.1">/* machine-managed stylesheet, §3.4 */</style>
<style data-aim-theme>:root{--aim-brand-1:#1a73e8}</style>
</head>
<body>
<h1 data-aim="8b1f" class="font-bold text-3xl text-brand-1">Proposal</h1>
<p data-aim="c42a">We propose a three-year engagement…</p>
<section data-aim="b31f"><h2>Scope</h2><p>…</p></section>
<ul data-aim-container="l2"><li data-aim="c90">Discovery</li><li data-aim="c91">Implementation…</li><li data-aim="c91">…and rollout</li></ul>
<aim-slide data-aim-container="s01" style="width:1920px; height:1080px"><h2 data-aim="77de" class="text-5xl" style="left:96px; top:64px; width:900px; z-index:2">Timeline</h2></aim-slide>
<aim-proposals>
<aim-proposal id="p-19" data-action="modify" data-at="2026-07-07T09:41:02Z" data-author="agent" data-author-model="model-id" data-batch="b7" data-explanation="Tighten the intro." data-for="c42a"><template><p data-aim="c42a">Acme saves €2.1M over three years…</p></template></aim-proposal>
</aim-proposals>
<aim-assets>
<svg aria-hidden="true" height="0" width="0">
<symbol id="asset-691865f28dde" viewBox="0 0 160 100"><image height="100" width="160" href="data:image/png;base64,…"/></symbol>
</svg>
</aim-assets>
<script type="application/aim-history+jsonl">
{"action":"modify","after":"…","batch":"b6","before":"…","kind":"direct_edit","seq":57,"t":"2026-07-07T09:14:31Z","target":"c42a","author":{"id":"ada","type":"human"}}
{"doc_hash":"sha256:9f2c…","kind":"checkpoint","label":"sent-to-client","seq":58,"t":"2026-07-07T09:45:00Z"}
</script>
<script type="application/aim-embeddings+jsonl">
{"chunk":"c42a","model":"embed-model","text_hash":"sha256:…","vec":[0.0182,-0.0441]}
</script>
</body>
</html>
```

### 2.1 Head

The head MUST contain `<meta charset="utf-8">` and a `<title>`. It MAY
contain, in this order: the metadata cache
(`<script type="application/aim-meta+json">`, §8.1), the embedded
stylesheet (`<style data-aim-css="…">`, §3.4), and the theme block
(`<style data-aim-theme>`, §3.5). HTML comments are legal in the head only,
and tooling MUST preserve them byte-exact.

### 2.2 Body section order

Direct children of `<body>` MUST be, in order:

1. **content constructs** — chunk elements (`data-aim`) and containers
   (`data-aim-container`, including `<aim-slide>`);
2. at most one `<aim-proposals>` (§5);
3. at most one `<aim-assets>` (§9);
4. at most one history script (`application/aim-history+jsonl`, §6);
5. at most one embeddings script (`application/aim-embeddings+jsonl`, §8.2).

Each section is optional; the order among those present is fixed. Comments
and non-whitespace text are not allowed as body children.

### 2.3 Coverage

Every content element in the body MUST belong to exactly one chunk (have
ancestor-or-self carrying `data-aim`) **or** be container shell scaffolding:
the container element itself plus the structural wrappers `thead`/`tbody`/
`tfoot` between a table container and its row chunks. `<template>` contents
and the asset registry are exempt — they are not document content.

### 2.4 Security constraints

A format that renders on double-click inherits HTML's threat surface; the
constraints below are conformance requirements, enforced by the linter.

- No executable script: the only `<script>` elements allowed are the three
  typed data blocks (§2.2, §2.1), which browsers treat as inert data.
- No event-handler attributes (`on*`).
- No `javascript:` or `data:text/*` URLs. `a@href` MUST be `https:`,
  `http:`, `mailto:`, or a fragment; `img@src` MUST be `https:`, `http:`,
  or `data:image/*`.
- No embedding or interactive elements (`iframe`, `object`, `embed`,
  `form`, `input`, …) — these are outside the vocabulary and additionally
  called out as security errors.
- No `<style>` other than the embedded stylesheet and the theme block.

---

## 3. Substrate and styling

The document lifecycle contains **no compilation step**. Class names resolve
statically against the versioned stylesheet; values resolve dynamically
(`var()` → theme block → cascade).

### 3.1 Element vocabulary

The allowed elements are a closed registry (Appendix A). Unknown elements
are conformance errors — a finite vocabulary is what keeps output stable
across models and tools. Attributes are likewise registry-listed per
element; `data-x-*` is the vendor-extension escape hatch (ignored by
conformance, mirroring `x_*` in JSON).

### 3.2 Class vocabulary

`class` attributes MUST use only registered utility names (Appendix A.2) —
a curated subset of standard Tailwind spellings plus theme-backed brand
utilities. Arbitrary-value classes (`w-[347px]`) are invalid. Rationale:
finiteness makes one static stylesheet possible and eliminates cross-model
drift.

### 3.3 Geometry: inline styles

Continuous values go in `style=""`, restricted to the whitelist
`left, top, width, height, transform, z-index` with per-property value
grammars (Appendix A.3). Discrete design choices are classes; measurements
are inline styles; document-wide constants are theme slots. Slide canvas
size is expressed the same way (`<aim-slide style="width:1920px;
height:1080px">`).

### 3.4 The embedded stylesheet (`aim.css`)

One static stylesheet per spec minor version — an element base layer,
every registered utility, theme-slot defaults, and the `aim-*` chrome
(slide canvas framing/scaling, proposal cards). It is embedded by default
(`<style data-aim-css="0.1">`) so documents are self-contained, offline,
and archival; it is **machine-managed and derived**: tools regenerate it
freely, and it is excluded from content hashing. Documents SHOULD embed it;
a document without it still conforms but degrades at the raw tier.

Slide scaling chrome uses the `zoom` property (which scales the layout box,
so scaled canvases sit in normal flow without wrappers or JS), with stepped
`@media` fallbacks for narrow viewports; viewers override
`--aim-slide-scale`.

### 3.5 The theme block

`<style data-aim-theme>` contains **exactly one `:root { … }` rule**
assigning registered slots only (`--aim-brand-1…4`, `--aim-font-heading`,
`--aim-font-body`, `--aim-font-mono`), with per-type value grammars
(Appendix A.4). `aim.css` ships a default for every slot, so there are no
dangling references; cascade order (stylesheet first, theme after) is the
entire override mechanism. The theme block is versioned document state,
addressable in events as the reserved target `aim:theme` (§6.5).

---

## 4. Chunks

### 4.1 Semantic chunking

Chunk boundaries are **authorial decisions made by whoever writes** —
usually the model. The chunk is the unit of meaning: of retrieval, edit
targeting, and explanation. The format imposes no mechanical granularity.

### 4.2 Marking

A chunk is a **maximal run of consecutive sibling elements sharing a
`data-aim` value**:

- the common case is a single element: `<p data-aim="c42a">…</p>`;
- a grouping element makes a multi-block chunk:
  `<section data-aim="b31f"><h2>…</h2><p>…</p></section>`;
- a **run** groups siblings where HTML permits no grouping element:
  `<li data-aim="c91">…</li><li data-aim="c91">…</li>`.

The same value non-consecutive, or under two parents, is a conformance
error. At top level, multi-block chunks MUST use a grouping element; runs
are canonical only inside list/table content. Legal chunk carriers are
listed in Appendix A.1; item carriers are `li` (in `ul`/`ol`) and `tr`
(in `table`).

### 4.3 Containers

A list or table whose items are individually addressable carries
`data-aim-container="id"` — mutually exclusive with `data-aim` on the same
element (a list is *either* one atomic chunk *or* a container of item
chunks). `<aim-slide>` is a container of positioned chunks. `body` is the
implicit root container with reserved id `body`. Chunks never nest;
containment happens only through containers.

### 4.4 Ids

Ids are opaque strings unique within the document, matching
`[a-z0-9][a-z0-9_-]{0,63}`; proposal ids are the same with a `p-` prefix,
and the `p-` prefix is therefore **reserved** — chunk and container ids
MUST NOT use it, so an anchor reference dispatches on the id alone.
`body`, `aim:theme`, and `aim:doc` are reserved. **Ids are tooling's job**:
models choose boundaries (emitting placeholder ids at most); the SDK
assigns real ids at write time. The reference tooling assigns 8-character
random lowercase ids; longer ids, including UUIDs, are equally valid. Ids
are never reused — an id that was deleted stays burned for the document's
lifetime.

### 4.5 Identity is declared, not inferred

An edit *states* its target id, so "is this the same chunk?" is answered by
the event, deterministically. MODIFY keeps the id regardless of content
distance. A split is MODIFY (the original id keeps the first part) plus
ADD(s); a merge is MODIFY of the survivor plus DELETE(s). Matching
externally rewritten files is a reconciliation tool's job (§6.8), not the
format's.

### 4.6 Sections don't exist in the body

The document outline is derived from heading chunks; the table of contents
(§8.1) groups chunk ids as a derived cache; SDKs expose section-range
operations that expand to per-chunk events in one batch.

---

## 5. Proposals — the pending lane

Pending proposals live in **one `<aim-proposals>` appendix** at the end of
document content. The body is the accepted document, period.

```html
<aim-proposal id="p-19" data-action="modify" data-at="2026-07-07T09:41:02Z"
              data-author="agent" data-author-model="model-id" data-batch="b7"
              data-explanation="Tighten the intro; lead with the outcome."
              data-for="c42a">
  <template><p data-aim="c42a">…proposed replacement…</p></template>
</aim-proposal>
```

### 5.1 Payloads are inert templates

The payload sits in a `<template>`: parser-safe (template contents parse in
an isolated fragment — a `<tr>` payload parses correctly, nothing
auto-closes or foster-parents), apply-safe (a proposed `<style>` block
cannot take effect), and selector-safe (template contents are outside the
document tree, so the payload's `data-aim` never duplicates the live
chunk's). The payload is the exact whole-serialization of the proposed
state, including its `data-aim`.

### 5.2 Actions and anchors

`data-action` is one of `add`, `modify`, `delete`, `move`. `modify` and
`add` carry payloads; `delete` and `move` are payloadless cards. `add` and
`move` carry an anchor: `data-anchor-container` (a container id or `body`)
plus `data-anchor-after` — a chunk id, the id of another *pending add*
(chains), or **omitted, meaning first position** (the attribute spelling of
JSON `after: null`) — plus, for rows in table containers,
`data-anchor-shell` mirroring the event anchor's `shell` (§6.4).

### 5.3 Attribution

`data-author` is the actor type (`human`/`agent`/`external`), with optional
`data-author-id` and `data-author-model` mirroring the actor object (§6.3),
so the eventual resolution event is synthesizable from the card alone.
`data-at` is the ISO-8601 UTC creation time; `data-explanation` is the
one-line "why".

### 5.4 Invariants

- At most one pending `modify`-or-`delete` proposal per target: a new one
  resolves the old as `superseded` (§6.2).
- `add` chains: if the anchor proposal is **accepted**, dependent adds
  rebind to the accepted chunk's id; if it is **rejected**, they rebind to
  the rejected proposal's own anchor. Chains therefore always resolve
  deterministically.
- `data-depends-on` is advisory metadata for coupled proposals (e.g. a
  chunk edit plus a theme recolor): editors group and warn; the format does
  not police partial acceptance.
- Theme proposals use the same mechanism with `data-for="aim:theme"` and a
  whole theme block as payload.
- Editing a pending payload in place is allowed and unrecorded; provenance
  is preserved at resolution via `proposed` vs `applied` (§6.2).

### 5.5 The raw-tier change memo (informative)

The embedded stylesheet renders each card as a readable memo — action,
target, author, time, explanation — via `content: attr(…)`, pure CSS.
Pending changes are therefore always *visible* to a plain reader; the
payload text itself is template-inert and not shown at the raw tier, so
tooling SHOULD write explanations that stand alone. Word-level diffs and
in-place previews are viewer affordances.

---

## 6. History

### 6.1 Storage

`<script type="application/aim-history+jsonl">` holds one event per line,
append-only. Inside JSON strings, `</` MUST be written `<\/` (a standard
JSON escape), which keeps `</script>` sequences out of the block while
staying byte-canonical. JSON-in-script is chosen over custom elements for
DOM hygiene: historical chunk copies must not pollute selectors,
find-in-page, or the accessibility tree.

### 6.2 Event kinds

| kind | required fields | optional fields | state-changing |
|---|---|---|---|
| `direct_edit` | `seq, kind, t, target, action, author, batch` | `before, after, anchor, from, to, origin, explanation, source` | yes |
| `resolution` | `seq, kind, t, proposal, target, action, decision, proposed_by, proposed_at, decided_by, batch` | `before, proposed, applied, anchor, from, to, superseded_by, explanation, source` | only if `decision:"accepted"` |
| `checkpoint` | `seq, kind, t, label, doc_hash` | | no |

`action` ∈ `add | modify | delete | move`; `decision` ∈ `accepted |
rejected | superseded`; `origin` ∈ `user | undo | redo | reconcile`.

One **resolution event per proposal lifetime**: creation is not logged
separately; `proposed_at`/`proposed_by` travel inside the resolution, so
chronology survives without duplicating pending content. **`applied` vs
`proposed`**: accept-with-tweaks is a plain `accepted` where
`applied ≠ proposed` (the field is omitted when identical) — not a fourth
decision type. This preserves "verbatim AI vs human-corrected" attribution
without a phantom intermediate version.

### 6.3 Ordering, actors, batches

Ordering is defined by `seq` alone — strictly contiguous within the
retained log (a documented gap at the start after pruning). `t` (ISO-8601
UTC) is informational; wall clocks skew and MUST NOT be used for ordering.
Actors are `{type: human|agent|external, id?, model?}` — `id` free-form,
`model` an exact model identifier, `external` for tool-synthesized events.
`batch` groups one editing intention (one AI turn, one autosave window)
across many targets: **one event per target, grouped by batch** — never
compound multi-target events; independent accept/reject is the product.
`source` (e.g. chat-message references) is a free-form array — provenance
is never load-bearing for existence.

### 6.4 Anchors

`{container, after}` where `container` is `body`, a slide id, or a
list/table container id, and `after` is a chunk id or `null` = first
position. For rows in table containers the anchor additionally carries
`shell` (`thead` | `tbody` | `tfoot`) naming the row section the position
resolves in — required for invertibility, since "first position" is
otherwise ambiguous across row sections (a deleted first body row must not
un-delete into the header). Inside slides, sibling order defines **reading
order only**; stacking MUST use explicit `z-index`. Slides are themselves
anchored in the body sequence — adding/moving/deleting a slide is an
ordinary event targeting the container, whose payloads are the container's
whole subtree serialization.

### 6.5 Targets

Chunk ids, container ids, plus **reserved singleton ids**: `aim:theme` (the
whole theme block as before/after; introducing the block is a `modify` with
no `before`) and `aim:doc` (reserved for document-level settings; defined
fields arrive in a later version — targeting it in v0.1 is an error).

### 6.6 Invertibility

Every state-changing event carries enough to undo it:

- `modify`: `before` and `after` as whole-target serializations (runs
  concatenated in order);
- `add`: the inserted payload in `after` plus its `anchor`; the inverse is
  removal;
- `delete`: the removed content in `before` **and the anchor it occupied**
  — without the anchor the inverse cannot reinsert;
- `move`: both positions, as `from` and `to`.

Undo/redo are *new appended events* (`origin: undo|redo`), never rewrites.
Whether a tool exposes an undo *zone* algorithm is tool-level; the format
only records origins.

### 6.7 Time travel and verification

State at seq *N* = current body and theme, then apply inverses of
state-changing events from latest down to *N+1*. Verifiers MUST check
**payload byte-equality along the chain** (each event's recorded result
must equal the reconstructed serialization of its target — this is what
detects out-of-band edits) and MUST verify `doc_hash` at every checkpoint
crossed. Checkpoints are zero-copy: named, pinned `(seq, label, doc_hash)`
anchors.

### 6.8 Lifecycle operations

Defined operations (SDK/CLI verbs): **flatten** (drop history → clean
file), **prune** (truncate events before a seq/checkpoint — limits how far
back travel goes, trivially safe), resolve-all-pending on export
(accept-all / reject-all, caller's choice), **gc** (§9.3), and
**reconcile** (detect out-of-band edits by comparing the body against the
last consistent state, then synthesize `direct_edit` events with
`author: {type: external}`, `origin: reconcile`; fuzzy matching lives in
the tool, and whatever it declares becomes truth going forward — also the
adoption path for hand-edited files). The reference toolkit implements
reconcile as `AimDocument.reconcile()` / `aim reconcile`; it requires the
full retained log (reconciling a pruned history is an error there — the
baseline below the prune floor is unrecoverable).

---

## 7. Versioned state vs derived caches

| Versioned (in history, hashed) | Derived caches (unversioned, excluded from `doc_hash`) |
|---|---|
| chunk content + order | `aim-meta` (summary, TOC) — carries `as_of_seq`/`doc_hash` staleness markers |
| containers (slides, list/table shells) + geometry | embeddings (per-chunk `text_hash` + `model`) |
| theme block (`aim:theme`) | `aim.css` (pinned by `data-aim-css`, regenerable) |
| future `aim:doc` settings | packed asset registry (content-addressed, immutable) |

Caches are never load-bearing: delete them all and the document plus
history are intact and regenerable. Pending proposals are neither: they are
live document state, resolved into history — not hashed into `doc_hash`,
not regenerable.

---

## 8. Retrieval layer

### 8.1 Metadata cache

`<script type="application/aim-meta+json">` in the head holds one JSON
object: `summary {text, model, as_of_seq, doc_hash}` and optional
`toc [{title, level, chunks: [ids]}]` — `chunks` MAY list chunk *and*
container ids, so an entry can span a list or a slide. The TOC is derived
from heading chunks (deterministic, regenerable). This is the first thing
an agent reads; staleness is checkable against the current `doc_hash`.

### 8.2 Embeddings

`application/aim-embeddings+jsonl`: one line per chunk per model —
`{chunk, model, text_hash, vec}`. Multiple models per chunk are allowed;
there is no "primary" marker (readers select by `model`). Vectors are JSON
numbers (no quantized packing in v0.1). Stale = `text_hash` differs from
the hash of the chunk's current canonical serialization; cosmetic changes
over-invalidate, which is safe. Last section in the file — least useful to
raw readers.

### 8.3 LLM projection (informative)

Read-path defaults for agent tooling: strip the embedded stylesheet, elide
data-URIs to `…[elided: 480KB, sha256:ab12…]` stubs, optionally drop
history and embeddings. The raw file is already dumb-reader-friendly by
ordering; projection makes smart readers cheap.

---

## 9. Assets

### 9.1 Two forms

**Authoring form**: plain `<img src="https://…" alt="…">` — what models
naturally write. External URLs mutate on someone else's schedule; packing
is the fix when it matters. **Packed form**: blobs hoisted once into
`<aim-assets>` as an SVG symbol registry (a 0×0 `aria-hidden` `<svg>`
holding one `<symbol id="asset-…">` per asset), referenced from chunks via
`<svg role="img" aria-label="…"><use href="#asset-…"/></svg>`. Native
rendering, printing, and deduplication; blobs stay out of the body so raw
reads meet document before data.

### 9.2 Content addressing

Asset id = `asset-` + the first 12 hex of sha256 over the asset's bytes:
the raw blob for raster assets; the canonical serialization of the
symbol's children for vector symbols. Replacing an image is a new asset
plus a chunk MODIFY — asset entries need no history events, because *asset
history is chunk history*.

### 9.3 Garbage collection

An asset is live iff referenced by the current body, any retained history
payload, or any pending payload (old versions must still render after time
travel). Pruning history widens what is collectable. `gc` runs as the final
pass of pack/flatten/prune.

---

## 10. Distribution and interop

- The extension is `.aim`; `.aim.html` is a compatibility alias tools MUST
  accept (never the canonical name). Browsers key **local** files off the
  extension, so a bare `.aim` opened via `file://` renders as plain text;
  bridges: a local helper that serves the file with `Content-Type:
  text/html` (the header always wins), OS file-type registration, or the
  alias.
- Served on the web, one line of server configuration (`.aim` →
  `text/html`) suffices. Media-type registration is future work.
- Version control: map `*.aim` to HTML syntax highlighting
  (`.gitattributes`: `*.aim linguist-language=HTML`).
- There is no container format in v0.1: a single text file is the format.

---

## 11. Canonical form and hashing

Byte-deterministic serialization is load-bearing: chain verification and
`doc_hash` both depend on it. Equality is byte equality.

### 11.1 HTML serialization

- Lowercase element and attribute names, with the HTML parser's
  **foreign-content case adjustments** (`viewBox`,
  `preserveAspectRatio`, …) — a naive all-lowercase rule is unimplementable
  because HTML tokenization lowercases and tree construction re-adjusts.
- Attribute order: `data-aim`/`data-aim-container`, `id`, `class`, `style`
  first; remaining attributes alphabetical; `src`/`href` always last.
- `class` tokens sorted alphabetically. Inline style properties in
  whitelist order (§3.3), `; `-separated, no trailing semicolon.
- Double-quoted attributes. Text escapes `& < >` only; attribute values
  escape `& "` only. Raw UTF-8 (no entity-encoding of non-ASCII). LF line
  endings.
- **Constructs never share a line**: one top-level construct per line, with
  constructs containing significant newlines (`pre`) spanning physical
  lines. Typed scripts and the embedded stylesheet are block-laid-out
  (open tag / content lines / close tag); the theme block is a single
  line (it is hashed content). In the asset registry, each symbol sits on
  its own line — one data-URI per line, so one changed image is one
  changed diff line.
- Void elements without `/`; foreign (SVG) empty elements self-close.

### 11.2 JSON serialization

Sorted keys, compact separators, raw UTF-8, `</` written `<\/`; one object
per line in JSONL blocks.

### 11.3 `doc_hash`

`doc_hash` = `sha256:` + hex sha256 over the UTF-8 bytes of the **reduced
projection**: the `<html …>` open tag line, the theme-block line (when
present), and each body content construct line, LF-joined with a trailing
LF. The proposals appendix, the asset registry, both trailers, and all
caches are excluded. Hashing the whole projection (rather than composing
per-chunk hashes) captures attributes, geometry, and order with nothing to
forget by enumeration.

### 11.4 Where hashes live

Hashes exist only where the full text doesn't: checkpoint `doc_hash`, cache
staleness hashes, and content-addressed asset ids. Events carry **no**
hashes — within the file every payload is present, so the chain is verified
by payload byte-equality during replay (§6.7). This is tamper-*evidence*,
not tamper-proofing; a signing layer can sit on the hash-anchored history
later.

### 11.5 Comments

HTML comments are legal only in the head, preserved byte-exact. Body
comments are conformance errors (they would complicate construct-line rules
and hashing for no benefit; provenance belongs in history and
explanations).

---

## 12. Conformance

A document conforms when the verifier reports no errors across:
**structure** (anatomy, section order, coverage, runs, exclusivity, ids),
**vocabulary** (elements, classes, styles, attributes, theme grammar),
**security** (§2.4), **pending lane** (§5.4), **history** (field schemas,
seq contiguity, canonical JSON, full chain verification), and **canonical
form** (§11, byte-exact). Rule codes are listed in Appendix A.6; the
conformance suite (`tests/fixtures/ok_*.aim` / `nok_<CODE>_*.aim`) pins one
rule per file and doubles as a test kit for independent implementations.

The reference verifier is `aim lint` (all findings in one run, `--format
json` for tooling). All ` ```aim ` snippets in this specification are
complete documents validated in CI by that verifier.

---

## 13. Usage examples

A minimal conforming document (empty stylesheet shown for brevity; tools
embed the generated one):

```aim
<!doctype html>
<html data-aim-version="0.1" lang="en">
<head>
<meta charset="utf-8">
<title>Minimal</title>
<style data-aim-css="0.1">
</style>
</head>
<body>
<h1 data-aim="ttl">Minimal</h1>
<p data-aim="body1">One paragraph &amp; nothing else.</p>
<script type="application/aim-history+jsonl">
{"action":"add","after":"<h1 data-aim=\"ttl\">Minimal<\/h1>","anchor":{"after":null,"container":"body"},"author":{"id":"ada","type":"human"},"batch":"b1","kind":"direct_edit","seq":1,"t":"2026-07-07T12:00:00Z","target":"ttl"}
{"action":"add","after":"<p data-aim=\"body1\">One paragraph &amp; nothing else.<\/p>","anchor":{"after":"ttl","container":"body"},"author":{"id":"ada","type":"human"},"batch":"b1","kind":"direct_edit","seq":2,"t":"2026-07-07T12:00:01Z","target":"body1"}
</script>
</body>
</html>
```

A pending agent proposal awaiting review, with one already-decided
resolution and a checkpoint in the log:

```aim
<!doctype html>
<html data-aim-version="0.1" lang="en">
<head>
<meta charset="utf-8">
<title>Pending lane</title>
<style data-aim-css="0.1">
</style>
</head>
<body>
<p data-aim="c1">The accepted wording.</p>
<aim-proposals>
<aim-proposal id="p-1" data-action="modify" data-at="2026-07-07T12:05:00Z" data-author="agent" data-author-model="model-id" data-batch="b3" data-explanation="Lead with the outcome, not the activity." data-for="c1"><template><p data-aim="c1">The sharper wording.</p></template></aim-proposal>
</aim-proposals>
<script type="application/aim-history+jsonl">
{"action":"add","after":"<p data-aim=\"c1\">The first wording.<\/p>","anchor":{"after":null,"container":"body"},"author":{"id":"ada","type":"human"},"batch":"b1","kind":"direct_edit","seq":1,"t":"2026-07-07T12:00:00Z","target":"c1"}
{"action":"modify","applied":"<p data-aim=\"c1\">The accepted wording.<\/p>","batch":"b2","before":"<p data-aim=\"c1\">The first wording.<\/p>","decided_by":{"id":"ada","type":"human"},"decision":"accepted","kind":"resolution","proposal":"p-0","proposed":"<p data-aim=\"c1\">The acceptable wording.<\/p>","proposed_at":"2026-07-07T12:01:00Z","proposed_by":{"model":"model-id","type":"agent"},"seq":2,"t":"2026-07-07T12:02:00Z","target":"c1"}
{"doc_hash":"sha256:cd0352f73c1979db04101ff8d6075a9f3a75103534adb30eecd4dddff179e1bd","kind":"checkpoint","label":"reviewed","seq":3,"t":"2026-07-07T12:03:00Z"}
</script>
</body>
</html>
```

Slides use the same primitives — a fixed canvas container with positioned
chunks:

```aim
<!doctype html>
<html data-aim-version="0.1" lang="en">
<head>
<meta charset="utf-8">
<title>One slide</title>
<style data-aim-css="0.1">
</style>
</head>
<body>
<aim-slide data-aim-container="s1" style="width:1920px; height:1080px"><h2 data-aim="t1" class="font-bold text-5xl" style="left:120px; top:100px; width:1200px; z-index:2">Quarterly review</h2><p data-aim="b1" class="text-2xl text-gray-600" style="left:120px; top:300px; width:1200px">Reading order is DOM order; stacking is explicit z-index.</p></aim-slide>
<script type="application/aim-history+jsonl">
{"action":"add","after":"<aim-slide data-aim-container=\"s1\" style=\"width:1920px; height:1080px\"><h2 data-aim=\"t1\" class=\"font-bold text-5xl\" style=\"left:120px; top:100px; width:1200px; z-index:2\">Quarterly review<\/h2><p data-aim=\"b1\" class=\"text-2xl text-gray-600\" style=\"left:120px; top:300px; width:1200px\">Reading order is DOM order; stacking is explicit z-index.<\/p><\/aim-slide>","anchor":{"after":null,"container":"body"},"author":{"model":"model-id","type":"agent"},"batch":"b1","kind":"direct_edit","seq":1,"t":"2026-07-07T12:00:00Z","target":"s1"}
</script>
</body>
</html>
```

Working with the reference SDK (informative; see the repository README for
the full tour):

```python
import aimformat as aim

doc = aim.new_document(title="Q3 Proposal")
me, bot = aim.human("ada"), aim.agent("model-id")

intro = doc.add_chunk("<p>We propose a three-year engagement.</p>", author=bot)
p = doc.propose_modify(intro.id,
                       f'<p data-aim="{intro.id}">Acme saves €2.1M.</p>',
                       author=bot, explanation="Lead with the outcome.")
doc.accept(p.id, decided_by=me)
doc.checkpoint("sent-to-client")
assert not [f for f in aim.lint(doc) if f.level == "error"]
doc.save("proposal.aim")

# Interop: docling → .aim → DOCX (with real Word revision markup)
# doc = aim.from_docling(converter.convert("in.docx").document)
# aim.to_docx(doc, "out.docx", pending="tracked")
```

---

## Appendix A. Construct reference (generated)

<!-- BEGIN GENERATED REGISTRY REFERENCE (scripts/gen_spec_appendix.py) -->
*This appendix is generated from `src/aimformat/registry.json` — the
machine-readable registry that also drives the linter and the
stylesheet. Do not edit it by hand.*

### A.1 Elements

- **Block chunk carriers** (top level and inside slides): `h1` `h2` `h3` `h4` `h5` `h6` `p` `section` `blockquote` `figure` `pre` `div` `hr` `ul` `ol` `table`
- **Item chunk carriers**: `li` inside `ul` `ol`; `tr` inside `table`
- **Containers** (`data-aim-container`): `ul` `ol` `table` `aim-slide` plus `aim-slide`
- **Table shells** (scaffolding between container and row chunks): `thead` `tbody` `tfoot`
- **Allowed inside chunk subtrees**: `h1` `h2` `h3` `h4` `h5` `h6` `p` `section` `blockquote` `figure` `figcaption` `pre` `div` `hr` `ul` `ol` `li` `table` `thead` `tbody` `tfoot` `tr` `td` `th` `img` `svg` `use` `code` `a` `strong` `em` `b` `i` `u` `s` `sub` `sup` `mark` `br` `span`
- **Asset registry content**: `svg` `symbol` `image` `rect` `circle` `ellipse` `path` `g`
- **Explicitly forbidden** (security, X001): `iframe` `object` `embed` `form` `input` `button` `select` `textarea` `video` `audio` `canvas` `base` `frame` `frameset` `applet` `math`

### A.2 Class vocabulary

- **Type scale** `text-*`: `xs` `sm` `base` `lg` `xl` `2xl` `3xl` `4xl` `5xl` `6xl`
- **Weights** `font-*`: `normal` `medium` `semibold` `bold`
- **Leading** `leading-*`: `tight` `snug` `normal` `relaxed`
- **Alignment** `text-*`: `left` `center` `right`
- **Palette** for `text-` / `bg-` / `border-`: `gray` (50, 100, 200, 300, 400, 500, 600, 700, 800, 900); `red` (50, 600); `green` (50, 600); `amber` (50, 600); plus `white` and theme-backed `brand-1…4`
- **Spacing** `m`, `mt`, `mb`, `ml`, `mr`, `mx`, `my`, `p`, `pt`, `pb`, `pl`, `pr`, `px`, `py` × scale `0` `1` `2` `3` `4` `6` `8` `10` `12` `16`
- **Singles**: `bg-white` `border` `border-b` `border-t` `font-body` `font-heading` `font-mono` `italic` `line-through` `list-decimal` `list-disc` `list-none` `rounded` `rounded-full` `rounded-lg` `rounded-md` `shadow` `text-white` `tracking-tight` `tracking-wide` `underline` `uppercase`

Total registered utilities: **243**.

### A.3 Inline style properties

| property | value grammar |
|---|---|
| `left` | `^-?\d+(\.\d+)?px$` |
| `top` | `^-?\d+(\.\d+)?px$` |
| `width` | `^\d+(\.\d+)?px$` |
| `height` | `^\d+(\.\d+)?px$` |
| `transform` | `^(rotate\(-?\d+(\.\d+)?deg\)|translate\(-?\d+(\.\d+)?px, ?-?\d+(\.\d+)?px\)|scale\(\d+(\.\d+)?\))( (rotate\(-?\d+(\.\d+)?deg\)|translate\(-?\d+(\.\d+)?px, ?-?\d+(\.\d+)?px\)|scale\(\d+(\.\d+)?\)))*$` |
| `z-index` | `^-?\d+$` |

### A.4 Theme slots

| slot | type | default |
|---|---|---|
| `--aim-brand-1` | color | `#1d4ed8` |
| `--aim-brand-2` | color | `#0f766e` |
| `--aim-brand-3` | color | `#b45309` |
| `--aim-brand-4` | color | `#6d28d9` |
| `--aim-font-heading` | font-stack | `system-ui, -apple-system, 'Segoe UI', sans-serif` |
| `--aim-font-body` | font-stack | `system-ui, -apple-system, 'Segoe UI', sans-serif` |
| `--aim-font-mono` | font-stack | `ui-monospace, 'SF Mono', Menlo, Consolas, monospace` |

### A.5 Proposal attributes and event fields

- `aim-proposal` attributes: `id` `data-for` `data-action` `data-batch` `data-author` `data-author-id` `data-author-model` `data-at` `data-anchor-container` `data-anchor-after` `data-anchor-shell` `data-depends-on` `data-explanation`
- `add`: payload; requires `data-anchor-container`
- `modify`: payload; requires `data-for`
- `delete`: payloadless; requires `data-for`
- `move`: payloadless; requires `data-for` `data-anchor-container`

- `direct_edit` events — required: `seq` `kind` `t` `target` `action` `author` `batch`; optional: `before` `after` `anchor` `from` `to` `origin` `explanation` `source`
- `resolution` events — required: `seq` `kind` `t` `proposal` `target` `action` `decision` `proposed_by` `proposed_at` `decided_by` `batch`; optional: `before` `proposed` `applied` `anchor` `from` `to` `superseded_by` `explanation` `source`
- `checkpoint` events — required: `seq` `kind` `t` `label` `doc_hash`

### A.6 Verifier rule codes

| code | level | rule |
|---|---|---|
| S000 | error | document does not parse as HTML |
| S001 | error | <html> missing data-aim-version |
| S002 | warning | document targets a different spec version |
| S003 | error | head missing <meta charset="utf-8"> |
| S004 | error | head missing <title> |
| S005 | warning | no embedded aim.css stylesheet |
| S006 | warning | embedded aim.css targets a different spec version |
| S007 | error | comment in <body> (head-only) |
| S008 | error | stray text as a <body> child |
| S010 | error | unexpected script type in <body> |
| S011 | error | body child is neither chunk nor container |
| S012 | error | element is both chunk and container |
| S013 | error | duplicate body section |
| S014 | error | body section order violated |
| S015 | error | invalid chunk/container id |
| S016 | error | chunk id appears under multiple parents |
| S017 | error | run members not consecutive |
| S018 | error | duplicate container id |
| S019 | error | id used as both chunk and container id |
| S020 | error | uncovered slide child |
| S021 | error | table row without data-aim in a container |
| S022 | error | item chunk inside the wrong container kind |
| S023 | error | uncovered container child |
| S024 | error | chunk or container nested inside a chunk |
| S025 | error | stray text inside a container |
| S026 | error | aim-slide nested inside a slide |
| S027 | error | more than one aim-meta script in the head |
| V001 | error | element not allowed in the asset registry |
| V002 | error | element outside the vocabulary |
| V003 | error | attribute not allowed on this element |
| V004 | error | arbitrary-value class |
| V005 | error | unknown class |
| V006 | error | malformed style declaration |
| V007 | error | style property outside the geometry whitelist |
| V008 | error | style value does not match the property grammar |
| V009 | error | URL scheme not allowed for this attribute |
| V010 | error | theme block is not a single :root rule |
| V011 | error | unregistered theme slot |
| V012 | error | theme slot value does not match its grammar |
| X001 | error | forbidden element |
| X002 | error | event-handler attribute |
| X003 | error | dangerous URL (javascript:/data:text) |
| X004 | error | executable or unknown <script> |
| X005 | error | free <style> block |
| P001 | error | unexpected element inside <aim-proposals> |
| P002 | error | invalid proposal id |
| P003 | error | unknown proposal action |
| P004 | error | proposal missing data-for |
| P005 | error | proposal missing data-anchor-container |
| P006 | error | proposal missing its payload template |
| P007 | error | payloadless action carries a payload |
| P008 | error | proposal targets an unknown chunk |
| P009 | error | second pending modify/delete on one target |
| P010 | error | payload id does not match data-for |
| P011 | error | add anchor is neither a chunk nor a pending proposal |
| P012 | warning | data-depends-on does not reference a pending proposal |
| P013 | error | data-at is not ISO-8601 |
| P014 | error | empty aim-proposals section |
| P015 | error | pending adds anchor on each other in a cycle |
| H001 | warning | no history block (flattened document) |
| H002 | error | unparseable history line |
| H003 | error | event violates its field schema |
| H004 | warning | history starts above seq 1 (pruned) |
| H005 | error | history line is not canonical JSON |
| H006 | error | history chain verification failed |
| M001 | warning | summary cache is stale |
| M002 | warning | embedding is stale or orphaned |
| M003 | error | cache block is not valid JSON of the required shape |
| M004 | error | aim-meta block present but missing its summary |
| C001 | error | file is not in canonical form |
<!-- END GENERATED REGISTRY REFERENCE -->

## Appendix B. Recommendations (informative)

- **Word-level diffs**: viewers SHOULD present word-level diffs computed
  from an event's `before`/`applied` pair (or a card's payload vs the live
  chunk); the algorithm is a tool choice — the format deliberately stores
  whole-target payloads.
- **Deep links**: `data-aim` ids have no native `#fragment` targets;
  viewers and exporters MAY synthesize anchors using the convention
  `#aim:<chunk-id>`.
- **Explanations stand alone**: because payloads are invisible at the raw
  tier, a proposal's `data-explanation` should carry the change's meaning
  by itself.
- **Agent read path**: read the metadata cache first; verify
  `summary.doc_hash` against the document before trusting it.

## Appendix C. Future extensions (informative)

Planned but deliberately outside v0.1: cell-level table addressing and
column operations; pagination for non-slide documents (page breaks,
headers/footers, margins); slide masters/layouts and transitions; an
`.aimx` ZIP container for asset-heavy documents; multi-writer merge
semantics (v0.1 is single-writer; divergence is detectable via payload
equality and checkpoint hashes); `aim:doc` settings; signing on top of the
hash-anchored history; media-type registration; fonts as assets; an
`aim open` reference implementation.
