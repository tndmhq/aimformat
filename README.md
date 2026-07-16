# `.aim`: an open document format for human + AI co-authoring

**Status: v0.2 draft.** Spec and reference tooling are published; breaking
changes are possible until 1.0. v0.2 added pagination: page setup
(`aim:doc`) and hard page breaks.
[Specification](https://github.com/tndmhq/aimformat/blob/main/spec.md) ·
[Getting started](https://github.com/tndmhq/aimformat/blob/main/docs/guide/getting-started.md) ·
[Examples](https://github.com/tndmhq/aimformat/blob/main/examples/)

A `.aim` file is a single HTML document that is, at the same time:

- the rendered artifact: open it in any browser, styled, no tooling;
- the accepted current version: every block covered by a stable,
  uniquely-identified *chunk* that AI and tools can address;
- the pending-change lane: AI/human proposals carried *in the file*,
  visible to any reader, applied only on explicit accept;
- the full edit history: an append-only, invertible event log; any past
  version is reconstructible and verifiable against checkpoint hashes;
- derived caches (summary, TOC, embeddings, packed assets) that help
  agents but are never load-bearing.

Documents are increasingly written *with* AI, in formats designed for a
single human at a single cursor. Everyone building document workflows with
AI reinvents the same primitives: addressing a region of a document,
tracking which edits came from the model, letting a human accept or reject
them, proving what the document said before. `.aim` makes those primitives
part of the file format itself: open, editor-agnostic, MIT.

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
pip install 'aimformat[mcp]'     # + MCP server: aim mcp
pip install 'aimformat[convert]' # + md/docx import-export, pdf import
pip install 'aimformat[pdf]'     # + PDF export (playwright + chromium)
```

The CLI installs as `aim` and as `aimformat` (same tool; the alias avoids
the console-script collision with AimStack's `aim` experiment tracker and
makes `uvx aimformat` work).

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

- Identity is part of the format. Chunk ids live in the file, edits
  target ids (never character offsets), and identity survives any tool.
- Byte-canonical serialization. Attribute order, class order, escaping,
  line structure: all specified. Equality is byte equality, diffs are
  string compares, and no editor's parser is the arbiter of truth.
- The history verifies. Every state-changing event carries enough to undo
  it. `verify()` replays the log backwards over a copy, byte-compares
  every payload (which catches out-of-band edits), and checks every
  checkpoint hash.
- Provenance is first-class. Every event and proposal records its actor
  (`human` / `agent` + exact model id / `external`), timestamp, batch, and
  a one-line explanation.

## Interop: read almost anything, write Word

Ingest whatever [docling](https://github.com/docling-project/docling) can
read (PDF, DOCX, PPTX, images, HTML), without adding docling as a
dependency of this package:

```python
from docling.document_converter import DocumentConverter
import aimformat as aim

result = DocumentConverter().convert("contract.pdf")
doc = aim.from_docling(result.document)      # chunks, lists, tables, figures
doc.save("contract.aim")                     # ingestion itself is history
```

Export back to Word with the pending lane as real tracked changes
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
| assets | `pack_assets` (data-URIs into the content-addressed registry), `gc_assets` |
| interop | `from_docling`, `to_docx` |
| verifier | `lint`, `lint_text`, `lint_path`, each returning `Finding(code, level, message, where)` |
| agent note | `doc.note`, `doc.set_note()`, `doc.remove_note()`, `doc.has_canonical_note()` (spec §2.5) |

Actors: `aim.human("ada")`, `aim.agent("model-id")`, `aim.external("tool")`,
parsed from strings with `aim.parse_actor("agent:model-id")`.

## For agents & LLMs

Every `.aim` file opens with a short *agent note* (spec §2.5): a
declarative head comment that tells whichever LLM opens the file what it
is, where the docs live, and which invariants to keep. It is informative
only, by spec; nothing installs or executes because of it. The note points
to <https://aimformat.com/llms.txt>, which condenses
[`docs/for-agents.md`](https://github.com/tndmhq/aimformat/blob/main/docs/for-agents.md),
the canonical guide for agents.

The tooling on-ramps, in order of preference:

```sh
# CLI: coding agents with a shell need nothing else
aim show FILE --format json
aim propose modify FILE CHUNK_ID --html '…' --author agent:MODEL_ID
aim accept FILE PID --author human:ada
aim note FILE --check                    # CI gate for the agent note

# Agent Skill: any harness supporting the open Agent Skills standard
npx skills add tndmhq/aimformat
#   Claude Code: /plugin marketplace add tndmhq/aimformat

# MCP server: for shell-less clients (six tools, local stdio)
pip install 'aimformat[mcp]'
```

```json
{ "mcpServers": { "aimformat": { "command": "aimformat", "args": ["mcp"] } } }
```

An id-preservation eval harness under
[`evals/`](https://github.com/tndmhq/aimformat/tree/main/evals) measures
how well naked LLMs respect the format's invariants with and without the
agent note.

## Format at a glance

The [specification](https://github.com/tndmhq/aimformat/blob/main/spec.md) is one file, and it is executable: every
` ```aim ` snippet in it is linted in CI, the construct reference appendix
is generated from the same [registry](https://github.com/tndmhq/aimformat/blob/main/src/aimformat/registry.json) that
drives the linter and the stylesheet, and the conformance suite
([`tests/fixtures/`](https://github.com/tndmhq/aimformat/blob/main/tests/fixtures/)) pins every rule with an `ok_*` /
`nok_<CODE>_*` file pair that third-party implementations can reuse.

Design pillars (details and rationale in the spec):

- HTML plus a closed Tailwind-vocabulary subset. Models read and write it
  accurately, a finite vocabulary with one versioned stylesheet kills
  cross-model drift, and every browser renders it for free.
- Semantic chunking. Chunk boundaries are authorial; the chunk is the unit
  of meaning, retrieval, edit targeting, and explanation.
- Propose/accept as file primitives, with persisted attribution,
  explanations, accept-with-tweaks (`applied` vs `proposed`), and
  deterministic supersede/chain semantics.
- Docs and slides in one format. Slides are fixed-canvas containers of
  positioned chunks, with the same proposals and the same history.
- Security as conformance. No script, no event handlers, no dangerous URL
  schemes, all enforced by the linter (`aim lint` is the gate).

## Repository map

| | |
|---|---|
| [`spec.md`](https://github.com/tndmhq/aimformat/blob/main/spec.md) | the normative specification (single file, executable snippets) |
| [`src/aimformat/`](https://github.com/tndmhq/aimformat/blob/main/src/aimformat/) | reference SDK: document ops, verifier, canonical form, CSS generator, ingest/export |
| [`src/aimformat/registry.json`](https://github.com/tndmhq/aimformat/blob/main/src/aimformat/registry.json) | the machine-readable vocabulary; single source for the linter, stylesheet, and spec appendix |
| [`tests/`](https://github.com/tndmhq/aimformat/blob/main/tests/) | 310+ tests; [`tests/fixtures/`](https://github.com/tndmhq/aimformat/blob/main/tests/fixtures/) is the conformance suite |
| [`examples/`](https://github.com/tndmhq/aimformat/blob/main/examples/) | worked documents, generated by the SDK ([readme](https://github.com/tndmhq/aimformat/blob/main/examples/README.md)) |
| [`src/aimformat/mcp.py`](https://github.com/tndmhq/aimformat/blob/main/src/aimformat/mcp.py) | the MCP server (`aim mcp`, stdio, six workflow tools) |
| [`skills/aimformat/`](https://github.com/tndmhq/aimformat/blob/main/skills/aimformat/) | the Agent Skill (`npx skills add tndmhq/aimformat`) |
| [`docs/for-agents.md`](https://github.com/tndmhq/aimformat/blob/main/docs/for-agents.md) | the canonical LLM-facing guide (served as aimformat.com/llms.txt) |
| [`evals/`](https://github.com/tndmhq/aimformat/blob/main/evals/) | id-preservation eval harness (agent-note A/B) |
| [`scripts/`](https://github.com/tndmhq/aimformat/blob/main/scripts/) | appendix/fixture/example generators |
| [`docs/`](https://github.com/tndmhq/aimformat/blob/main/docs/README.md) | contributor + agent memory (knowledge base and decision log) |

Planned next (tracked in the spec's Future Extensions): reference viewer
and PPTX export.

## Relationship to Tndm

The format is maintained by [Tndm](https://github.com/tndmhq), which builds
a commercial editor on top of it, the same model as ProseMirror/Tiptap or
Git/GitHub. The format itself is neutral ground: open, MIT-licensed, and
designed to be adopted (and extended) by anyone, including other editors.

## Contributing

See [`CONTRIBUTING.md`](https://github.com/tndmhq/aimformat/blob/main/CONTRIBUTING.md), which covers how the
registry, generated appendix, fixtures, and executable spec snippets fit
together. AI agents: start at [`AGENTS.md`](https://github.com/tndmhq/aimformat/blob/main/AGENTS.md).

## License

[MIT](https://github.com/tndmhq/aimformat/blob/main/LICENSE) © Luca Campanella
