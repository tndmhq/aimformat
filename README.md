# `.aim` — an open document format for human + AI co-authoring

**Status: pre-v0.1 — the spec is in progress and nothing here is normative yet.**

Documents are increasingly written *with* AI, but the formats we write them in
were designed for a single human at a single cursor. PDF is hostile to
programmatic editing. DOCX and PPTX are zipped XML that language models handle
unreliably. Markdown is excellent for plain text and has no real layout, which
rules it out wherever formatting is part of the deliverable. Anyone building
document workflows with AI today ends up reinventing the same primitives:
how to address a region of a document, how to track which edits came from the
AI, how to let a human accept or reject them.

`.aim` is an attempt to make those primitives part of the file format itself —
open, editor-agnostic, MIT-licensed.

## Design goals

These are goals, not a spec. The concrete representation is being worked out;
expect breaking changes until v0.1.

- **HTML plus a strict Tailwind-class subset** as the canonical serialization.
  Models read and write HTML/Tailwind far more accurately than custom ASTs or
  DOCX XML, and every browser renders it for free.
- **Custom elements carry the semantics** (chunks, slides, proposals), with
  stable per-chunk UUIDs so identity survives edits and cross-references stay
  coherent.
- **Propose / accept as file primitives.** A `.aim` file can carry the accepted
  document, proposed changes (from AI or humans), and the acceptance state of
  each proposal — track changes as a property of the format, not of one
  editor. (How the lanes are physically represented is an open design
  question.)
- **Documents and slides in one format**, with explicit slide dimensions and
  positioned children so slide layout is deterministic.
- **A strict subset with a linter**, so different tools and models converge on
  the same output instead of drifting.
- **Deterministic exports** to PDF, DOCX, and PPTX.
- **A Python SDK** with a typed AST view alongside the canonical HTML.
- **Agent-native distribution**: an MCP server and agent instructions ship with
  the SDK, so any MCP-speaking tool can read, propose, and accept `.aim`
  edits out of the box.

## What lives in this repo

The format specification, the Python SDK, the MCP server, the linter, the
reference viewer, and the developer docs — as they land. Right now the repo
holds the contributor and agent scaffolding only; spec and code arrive with
v0.1.

This repo is deliberately structured so that AI agents can contribute well:
start at [`AGENTS.md`](AGENTS.md), which also hosts the canonical working
conventions shared across the `tndmhq` repos. The committed agent memory lives
in [`docs/`](docs/README.md).

## Relationship to Tndm

The format is maintained by [Tndm](https://github.com/tndmhq), which is
building a commercial editor on top of it — the same model as ProseMirror/
Tiptap or Git/GitHub. The format itself is neutral ground: open, MIT-licensed,
and designed to be adopted (and extended) by anyone, including other editors.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Pre-v0.1 the most useful
contributions are design discussion via issues rather than spec PRs.

## License

[MIT](LICENSE) © Luca Campanella
