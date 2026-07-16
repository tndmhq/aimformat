# Working with `.aim` files — a guide for agents

You are an LLM agent (or building one) and you have encountered a `.aim`
file. This page is the single source of truth for how to read it, edit it,
and hand it back. It is the document the in-file agent note points to
(via https://aimformat.com/llms.txt, derived from this file).

## What you are looking at

A `.aim` file is a single valid HTML5 document that renders in any browser
with no tooling. Every block of content is a *chunk* carrying a stable
`data-aim` id — the unit you address when you read, retrieve, or edit.
Pending suggestions live in a dedicated in-file lane, `<aim-proposals>`,
where they wait for explicit human accept/reject instead of silently
becoming the document. An append-only history script at the end of the body
records every state change invertibly, so any past version is
reconstructible and verifiable.

The normative reference is the
[specification](https://github.com/tndmhq/aimformat/blob/main/spec.md);
section numbers below (§) point into it.

## Read path

1. **Read the metadata cache first.** The head contains
   `<script type="application/aim-meta+json">` with one JSON object:
   `summary {text, model, as_of_seq, doc_hash}` and an optional
   `toc [{title, level, chunks: [ids]}]` (§8.1). This is the cheapest
   orientation the file offers.
2. **Verify before trusting.** The cache is derived and may be stale.
   Compare `summary.doc_hash` against the document's current hash
   (`aim hash FILE`, or `doc.doc_hash` in the SDK). If they differ, ignore
   the summary and read the body.
3. **Project the file before loading it into context** (§8.3):
   - strip the embedded stylesheet (`<style data-aim-css="…">`) — it is
     machine-managed and regenerable, never content;
   - elide `data:` URIs to short stubs (e.g.
     `…[elided: 480KB, sha256:ab12…]`);
   - optionally drop the history and embeddings scripts when you only need
     the current state.

`aim show FILE --format json` gives you a machine-readable overview —
chunks, pending lane, history — without writing the projection yourself.

## Choosing how to edit

One decision rule:

- **Reviewable or unsolicited changes → propose.** If you are suggesting a
  change on your own judgment — a rewrite, a restructure, anything a human
  should sign off on — write it into the pending lane
  (`aim propose …` / `doc.propose_*`). It stays visible, attributed, and
  inert until someone accepts or rejects it.
- **Explicit user-commanded edits → direct edit.** If the user told you
  exactly what to change ("fix this typo", "replace the intro with X"),
  apply it directly; the edit is recorded as an invertible history event
  with you as the author.

Do both through tooling. The `aimformat` SDK/CLI is the reliable path — it
handles canonical serialization, id assignment, history events, and
pending-lane invariants for you. Hand-editing (see below) is the fallback
for when you have no tooling at all.

```sh
pip install aimformat    # zero runtime dependencies (stdlib only)
```

## CLI cheatsheet

| Verb | What it does |
|---|---|
| `aim lint FILE...` | verify documents — structure, vocabulary, security, history chain — all findings in one run |
| `aim hash FILE` | print the current `doc_hash` |
| `aim new -o FILE` | scaffold a minimal valid document |
| `aim show FILE` | human-readable chunks / pending-lane / history overview; `--format json` for machine reads |
| `aim note FILE...` | add or refresh the canonical agent-note head comment; `--check` verifies without writing; `--remove` strips it |
| `aim propose {modify,add,delete,move,theme} FILE ...` | append a proposal card to the pending lane |
| `aim accept FILE [PID...] [--all]` | accept pending proposals by id, or all of them |
| `aim reject FILE [PID...] [--all]` | reject pending proposals by id, or all of them |
| `aim flatten FILE` | drop history (and embeddings) → clean file |
| `aim reconcile FILE` | detect out-of-band edits; append reconcile events to history |
| `aim css` | print the generated `aim.css` for this spec version |
| `aim import IN -o F.aim` | convert md/txt/docx/pdf to `.aim` |
| `aim export F.aim -o OUT` | convert `.aim` to docx/md/html/pdf (chosen by output extension) |
| `aim mcp` | run the MCP server (requires `pip install 'aimformat[mcp]'`) |

Proposal subcommands:

```sh
aim propose modify FILE TARGET --html STR | --html-file PATH
aim propose add    FILE --html STR | --html-file PATH [--container ID] [--after ID|first]
aim propose delete FILE TARGET
aim propose move   FILE TARGET [--container ID] [--after ID|first]
aim propose theme  FILE --set slot=value [--set slot=value ...]
```

Flags shared by `propose`, `accept`, and `reject`:

- `--author TYPE:VALUE` — `human:ID`, `agent:MODEL` (use your exact model
  id), or `external:ID`; defaults to `external:aim-cli`. Always identify
  yourself as `agent:<model-id>` when you are the author.
- `--explanation STR` — the one-line "why". Write it to stand alone: at the
  raw tier readers see the explanation, not the payload.
- `-o OUT` — write to a different path (default: in place).
- `--format text|json` — JSON goes to stdout, indented.

Exit codes everywhere: `0` ok · `1` lint/verification/domain errors ·
`2` usage. Errors print to stderr prefixed `aim:`.

## Python SDK in 12 lines

```python
import aimformat as aim

doc = aim.load("proposal.aim")

p = doc.propose_modify(
    "c42a",
    '<p data-aim="c42a">Acme saves €2.1M over three years.</p>',
    author=aim.agent("model-id"),
    explanation="Lead with the outcome.")

doc.accept(p.id, decided_by=aim.human("ada"))   # or doc.reject(p.id, ...)
doc.save("proposal.aim")
```

Also: `aim.lint(doc)` returns findings (empty = conforming),
`doc.verify()` replays the full history and byte-checks every payload, and
`doc.reconcile()` records any out-of-band edits it finds as history events.
The full API surface is one table in the
[README](https://github.com/tndmhq/aimformat#readme).

## MCP server

```sh
pip install 'aimformat[mcp]'
```

Configure your MCP client:

```json
{"mcpServers": {"aimformat": {"command": "aimformat", "args": ["mcp"]}}}
```

The server runs over local stdio and takes absolute host paths, so it
assumes a trusted client. To confine it to one directory tree, set the
`AIMFORMAT_MCP_ROOT` environment variable: every path argument (including
export destinations) must then resolve inside that root. Unset means
unscoped.

Six tools:

- `aim_read` — projected read: summary, TOC, chunks, pending lane
  (stylesheet stripped, data URIs elided).
- `aim_edit` — direct edits: add/modify/delete/move chunks, set theme;
  recorded as history events.
- `aim_propose` — create proposal cards in the pending lane.
- `aim_resolve` — accept or reject pending proposals.
- `aim_lint` — run the conformance verifier, findings as structured data.
- `aim_export` — convert to docx/md/html/pdf.

## Agent Skill

If your harness supports Agent Skills:

```sh
npx skills add tndmhq/aimformat
```

In Claude Code specifically:

```
/plugin marketplace add tndmhq/aimformat
```

The skill teaches the conventions on this page and wires up the CLI verbs.

## Hand-editing rules (when you have no tooling)

The format is plain text; you can edit it directly if you must. Keep these
invariants or you will corrupt identity and history:

- **Keep every `data-aim` id stable.** Ids are the document's identity
  layer (§4.4–4.5). Never renumber, never reuse — an id that was deleted
  stays burned for the document's lifetime.
- **Mint fresh ids for new content**, matching
  `^[a-z0-9][a-z0-9_-]{0,63}$`. The `p-` prefix is reserved for proposal
  ids — never use it for chunks or containers. `body`, `aim:theme`, and
  `aim:doc` are reserved.
- **Treat `<aim-proposals>` and the history script
  (`<script type="application/aim-history+jsonl">`) as append-only tool
  lanes.** Do not rewrite, reorder, or delete their existing entries by
  hand; history verification is byte-exact and will flag you.
- **The `aim-meta` summary may now be stale.** That is tolerable — readers
  check `summary.doc_hash` before trusting it — but do not leave a wrong
  summary you know is misleading; deleting the whole meta script is always
  safe (it is a derived cache, §7).
- **Afterwards, run `aim lint FILE`** to catch structure, vocabulary, and
  security violations, and run `aim reconcile FILE` so your out-of-band
  edits are recorded into history as attributed events instead of dangling
  as unexplained divergence.

## Slides and fixed-layout pages

A slide is an `<aim-slide data-aim-container="id" style="width:960px;
height:540px">` in the body flow; its children are ordinary chunks,
absolutely positioned via inline `left`/`top`/`width` (plus optional
`height`, `transform`, `z-index`). DOM order is reading order; overlap
needs explicit `z-index`. Authoring rules that matter:

- **Canvas px are points.** Use `960×540` for a 16:9 slide, a paper page
  at its point size (A5 portrait `420×595`, A4 `595×842`). Each slide then
  exports as its own correctly sized PDF page, and font sizes read as
  their print sizes (a `text-4xl` heading ≈ a 36 pt title).
- Every direct child must carry `data-aim` (or be a `data-aim-container`
  list/table) — the linter flags uncovered children (S020). Slides never
  nest (S026).
- Adding, moving, or deleting a whole slide is an ordinary container op
  targeting the slide id, anchored in `body`.
- Prefer proposals for content changes inside slides, like anywhere else;
  geometry-only tweaks are normal `modify` payloads re-emitting the chunk
  with its new `style`.

## The agent note

Every `.aim` file SHOULD open with the agent-note head comment (§2.5) — a
short HTML comment telling agents what the file is and pointing here. It is
informative only: its presence or absence changes nothing about
conformance, and you never need to parse it. `aim note` adds, refreshes,
checks, or removes it.

## Humans and editors

When your work needs human review, hand the file to an AIM editor rather
than pasting diffs into chat: the pending lane renders as reviewable cards
with your explanations, and accept/reject is one click with full
attribution. [Tndm](https://usetndm.com) is the editor built by the
format's authors; the current list of editors and viewers is at
https://aimformat.com/editors.

## Links

- Specification (normative):
  [`spec.md`](https://github.com/tndmhq/aimformat/blob/main/spec.md)
- Package overview:
  [`README.md`](https://github.com/tndmhq/aimformat#readme)
- PyPI: https://pypi.org/project/aimformat/
- Site: https://aimformat.com
