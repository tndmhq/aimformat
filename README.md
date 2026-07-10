# `.aim` — an open document format for human + AI co-authoring

**Status: v0.2 draft — spec and reference tooling published, breaking changes
possible until 1.0. v0.2 adds pagination: page setup (`aim:doc`) and hard
page breaks.** [Specification](spec.md) ·
[Getting started](docs/guide/getting-started.md) ·
[Examples](examples/)

A `.aim` file is a single HTML document that is, at the same time:

- **the rendered artifact** — open it in any browser, styled, no tooling;
- **the accepted current version** — every block covered by a stable,
  uniquely-identified *chunk* that AI and tools can address;
- **the pending-change lane** — AI/human proposals carried *in the file*,
  visible to any reader, applied only on explicit accept;
- **the full edit history** — an append-only, invertible event log; any past
  version is reconstructible and verifiable against checkpoint hashes;
- **derived caches** — summary, TOC, embeddings, packed assets — that help
  agents but are never load-bearing.

Documents are increasingly written *with* AI, in formats designed for a
single human at a single cursor. Everyone building document workflows with
AI reinvents the same primitives: addressing a region of a document,
tracking which edits came from the model, letting a human accept or reject
them, proving what the document said before. `.aim` makes those primitives
part of the file format — open, editor-agnostic, MIT.

```
┌──────────────────────────── one .aim file ────────────────────────────┐
│ <head>   summary + TOC cache · versioned stylesheet · theme           │
│ <body>   the accepted document (chunks + containers, renderable)      │
│          <aim-proposals>  pending AI/human changes + explanations     │
│          <aim-assets>     content-addressed packed images             │
│          history          append-only invertible event log (JSONL)    │
│          embeddings       per-chunk vectors w/ staleness hashes       │
└────────────────────────────────────────────────────────────────────────┘
```

## Install

```sh
pip install aimformat            # zero runtime dependencies (stdlib only)
pip install 'aimformat[docx]'    # + DOCX export (python-docx)
```

## Sixty seconds

```python
import aimformat as aim

doc = aim.new_document(title="Q3 Proposal")
me, bot = aim.human("ada"), aim.agent("model-id")

# direct edits append invertible history events
intro = doc.add_chunk("<p>We propose a three-year engagement.</p>", author=bot)

# the pending lane: propose → human decides
p = doc.propose_modify(intro.id,
                       f'<p data-aim="{intro.id}">Acme saves €2.1M over three years.</p>',
                       author=bot, explanation="Lead with the outcome.")
doc.accept(p.id, decided_by=me)          # or .reject(...), or accept with tweaks
doc.checkpoint("sent-to-client")         # pins a verifiable doc_hash

assert doc.verify() == []                # replay the log, check every hash
doc.save("proposal.aim")                 # canonical bytes; renders in a browser
```

```sh
aim lint proposal.aim     # structure + vocabulary + security + history chain
aim show proposal.aim     # chunks, pending lane, history at a glance
aim hash proposal.aim     # current doc_hash
aim normalize other.aim   # re-spell to canonical form (lossless, idempotent)
```

What makes this different from "HTML with extra attributes":

- **Identity is part of the format.** Chunk ids live in the file, edits
  target ids (never character offsets), and identity survives any tool.
- **Byte-canonical serialization.** Attribute order, class order, escaping,
  line structure — all specified. Equality is byte equality; diffs are
  string compares; no editor's parser is the arbiter of truth.
- **The history verifies.** Every state-changing event carries enough to
  undo it; `verify()` replays the log backwards and byte-compares every
  payload, catching out-of-band edits, and checks every checkpoint hash.
- **Provenance is first-class.** Every event and proposal records its actor
  (`human` / `agent` + exact model id / `external`), timestamp, batch, and a
  one-line explanation.

## Interop: anything → .aim → DOCX

Ingest whatever [docling](https://github.com/docling-project/docling) can
read (PDF, DOCX, PPTX, images, HTML) — without adding docling as a
dependency of this package:

```python
from docling.document_converter import DocumentConverter
import aimformat as aim

result = DocumentConverter().convert("contract.pdf")
doc = aim.from_docling(result.document)      # chunks, lists, tables, figures
doc.save("contract.aim")                     # ingestion itself is history
```

Export back to Word — with the pending lane as **real tracked changes**
(`w:ins`/`w:del`, attributed to the proposing human or model), or resolved
with a caller-chosen default:

```python
aim.to_docx(doc, "out.docx", pending="tracked")      # reviewable in Word
aim.to_docx(doc, "out.docx", pending="accept-all")   # resolved on a copy
aim.to_docx(doc, "out.docx", pending="reject-all")
```

## The API in one table

| | |
|---|---|
| load / create | `load`, `loads`, `new_document`, `doc.save`, `doc.dumps` |
| read | `doc.chunks`, `doc.chunk(id)`, `doc.containers`, `doc.proposals`, `doc.history`, `doc.meta`, `doc.theme`, `doc.doc_hash`, `doc.seq` |
| direct edits | `add_chunk`, `modify_chunk`, `delete_chunk`, `move_chunk`, `set_theme`, `doc.batch()` |
| pending lane | `propose_modify/add/delete/move/theme`, `accept` (with optional `applied=` tweaks), `reject`; supersede and chain rebinding are automatic |
| history | `verify`, `state_at(seq)`, `checkpoint`, `undo`, `redo`, `flatten`, `prune`, `reconcile` (repair out-of-band edits / adopt hand-written files) |
| caches | `set_summary`, `generate_toc`, `set_embedding`, `stale_embeddings` |
| assets | `pack_assets` (data-URIs → content-addressed registry), `gc_assets` |
| interop | `from_docling`, `to_docx` |
| verifier | `lint`, `lint_text`, `lint_path` → `Finding(code, level, message, where)` |

Actors: `aim.human("ada")`, `aim.agent("model-id")`, `aim.external("tool")`.

## Format at a glance

The [specification](spec.md) is one file, and it is executable: every
` ```aim ` snippet in it is linted in CI, the construct reference appendix
is generated from the same [registry](src/aimformat/registry.json) that
drives the linter and the stylesheet, and the conformance suite
([`tests/fixtures/`](tests/fixtures/)) pins every rule with an `ok_*` /
`nok_<CODE>_*` file pair that third-party implementations can reuse.

Design pillars (details and rationale in the spec):

- **HTML + a closed Tailwind-vocabulary subset** — models read and write it
  accurately; a finite vocabulary plus one versioned stylesheet kills
  cross-model drift; every browser renders it for free.
- **Semantic chunking** — chunk boundaries are authorial; the chunk is the
  unit of meaning, retrieval, edit targeting, and explanation.
- **Propose/accept as file primitives** — with persisted attribution,
  explanations, accept-with-tweaks (`applied` vs `proposed`), and
  deterministic supersede/chain semantics.
- **Docs and slides in one format** — slides are fixed-canvas containers of
  positioned chunks; same proposals, same history.
- **Security as conformance** — no script, no event handlers, no dangerous
  URL schemes, enforced by the linter (`aim lint` is the gate).

## Repository map

| | |
|---|---|
| [`spec.md`](spec.md) | the normative specification (single file, executable snippets) |
| [`src/aimformat/`](src/aimformat/) | reference SDK: document ops, verifier, canonical form, CSS generator, ingest/export |
| [`src/aimformat/registry.json`](src/aimformat/registry.json) | the machine-readable vocabulary — single source for linter, stylesheet, spec appendix |
| [`tests/`](tests/) | 310+ tests; [`tests/fixtures/`](tests/fixtures/) is the conformance suite |
| [`examples/`](examples/) | worked documents, generated by the SDK ([readme](examples/README.md)) |
| [`scripts/`](scripts/) | appendix/fixture/example generators |
| [`docs/`](docs/README.md) | contributor + agent memory (knowledge base and decision log) |

Planned next (tracked in the spec's Future Extensions): MCP server,
reference viewer, PPTX export, pagination.

## Relationship to Tndm

The format is maintained by [Tndm](https://github.com/tndmhq), which builds
a commercial editor on top of it — the same model as ProseMirror/Tiptap or
Git/GitHub. The format itself is neutral ground: open, MIT-licensed, and
designed to be adopted (and extended) by anyone, including other editors.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) — including how the registry,
generated appendix, fixtures, and executable spec snippets fit together.
AI agents: start at [`AGENTS.md`](AGENTS.md).

## License

[MIT](LICENSE) © Luca Campanella
