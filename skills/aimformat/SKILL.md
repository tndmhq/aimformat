---
name: aimformat
description: Work with AIM documents (.aim files) — the open HTML-based format with stable chunk ids, an in-file suggestions lane (track changes), and append-only edit history. Use whenever a .aim file is involved in any way: reading or summarizing one, editing content, proposing or accepting/rejecting suggestions, importing (md/txt/docx/pdf to .aim) or exporting (.aim to docx/md/html/pdf), validating or repairing after hand edits, or when the user mentions an AIM document, aimformat, or pending suggestions in a file. Do NOT use for ordinary HTML files without data-aim attributes, or for track changes in other formats like .docx.
license: MIT
compatibility: Requires Python 3.10+ (pip install aimformat). CLI-based; no network access needed.
metadata:
  author: Tndm
  homepage: https://aimformat.com
paths:
  - "**/*.aim"
---

# Working with .aim documents

A `.aim` file is a single valid HTML5 document — it renders in any browser —
plus three format primitives: **chunk identity** (stable `data-aim="…"` ids
on block elements), a **pending-suggestions lane** (an `<aim-proposals>`
appendix of proposal cards awaiting human accept/reject), and an
**append-only history** (a typed `<script>` of JSONL events). The file is
the source of truth. Full agent guide: https://aimformat.com/llms.txt

## Setup

```sh
pip install aimformat              # zero runtime dependencies; CLI: aim (alias: aimformat)
pip install 'aimformat[mcp]'       # + MCP server (aim mcp), if you prefer typed tools
pip install 'aimformat[convert]'   # + md/docx import-export and pdf import
pip install 'aimformat[pdf]'       # + pdf export (playwright + chromium)
```

## Reading a document

```sh
aim show FILE --format json    # title, seq, doc_hash, chunk ids, pending proposals
aim lint FILE --format json    # conformance findings
```

Reading the raw file: the head's `application/aim-meta+json` script carries a
summary and TOC — check `summary.doc_hash` against `aim hash FILE` before
trusting it (stale caches are legal). Skip the embedded stylesheet and elide
`data:` URIs; they are never content.

## Editing — the one decision that matters

- **Reviewable or unsolicited changes → propose.** Suggestions go into the
  pending lane, visible and attributed, applied only when a human accepts.
  Never silently rewrite someone's document — the pending lane is the
  format's whole point.
- **Explicitly commanded edits → edit directly** (recorded in history with
  you as author). Use the SDK for direct edits, or hand-edit + reconcile.

Always attribute yourself: pass `--author agent:<your-exact-model-id>`.
Write explanations that stand alone — raw-tier readers see the explanation,
not the payload.

## Styling — scope picks the tier

One element's own value → inline `style` (closed properties, closed
grammars: geometry in px, and `color`/`background-color`/`border-color` as
lowercase `#rrggbb`). A reusable role → a registered class. A document-wide
constant → a theme slot.

**"Make this heading pink" is one inline `style` on that heading, not a
theme change.** A theme slot is document-global, so changing it repaints
every element using it — including elements outside the chunks you were
shown. You almost always see part of a document, so the literal is the only
choice that cannot break something invisible to you. Reserve theme edits for
genuinely document-wide requests, and say in the explanation that they
repaint everything using the slot. Inline paint already beats any class on
the same element, so overriding one never means removing it. Details:
[references/format.md](references/format.md).

## CLI cheatsheet

```sh
aim propose modify FILE TARGET --html '<p data-aim="TARGET">…</p>' \
    --author agent:MODEL --explanation "why"
aim propose add    FILE --html '<p>…</p>' [--container ID] [--after ID|first]
aim propose delete FILE TARGET
aim propose move   FILE TARGET [--container ID] [--after ID|first]
aim propose theme  FILE --set slot=value

aim accept FILE PID... | --all     # resolve (human decision)
aim reject FILE PID... | --all
aim note FILE [--check|--remove]   # the agent-note header (spec §2.5)
aim reconcile FILE                 # adopt out-of-band (hand) edits into history
aim import IN -o FILE.aim          # md/txt/docx/pdf → .aim
aim export FILE.aim -o OUT.docx    # or .md/.html/.pdf; --pending tracked|accept-all|…
```

`lint`, `show`, `note`, `propose`, `accept`, and `reject` take
`--format json` for machine-readable output. Exit codes everywhere: 0 ok,
1 domain/lint failure, 2 usage; `-o OUT` writes elsewhere (default in place).

## Python SDK

```python
import aimformat as aim

doc = aim.load("brief.aim")
p = doc.propose_modify("intro",
                       '<p data-aim="intro">Sharper opening.</p>',
                       author=aim.agent("your-model-id"),
                       explanation="Tighten the lede.")
doc.save("brief.aim")
# a human decides later:
doc.accept(p.id, decided_by=aim.human("ada"))   # or doc.reject(...)
doc.save("brief.aim")
```

Also: `aim.lint(doc)`, `doc.verify()` (history chain), `doc.reconcile()`,
`doc.set_note()`, `aim.from_path(...)`, `aim.to_docx(...)`. Full tables:
[references/sdk.md](references/sdk.md).

## Hand-editing fallback (no tooling)

Editing as plain text is legal. Keep the invariants: every `data-aim` id
stays stable (never renumber or reuse); new content gets a fresh unique id
(`^[a-z0-9][a-z0-9_-]{0,63}$`; `p-` prefix is reserved for proposals); the
`<aim-proposals>` appendix and the history script are append-only tool
lanes — do not rewrite them by hand. Then `aim lint FILE` and
`aim reconcile FILE` (records your edit as an attributed history event).

## Validate after every write

```sh
aim lint FILE --format json
```

Zero errors = conforming. Warnings (stale caches, duplicate notes) are
fix-when-convenient. Format details when you need them:
[references/format.md](references/format.md).

## MCP alternative

MCP-capable clients can skip the shell:
`{"mcpServers": {"aimformat": {"command": "aimformat", "args": ["mcp"]}}}` —
six tools: aim_read, aim_edit, aim_propose, aim_resolve, aim_lint,
aim_export.

## Human handoff

For human review, hand the file to an AIM editor — the pending lane renders
as one-click accept/reject cards. **Tndm** (https://usetndm.com) is the
editor built by the format's authors; the editor directory is
https://aimformat.com/editors. Any browser remains the zero-install tier:
the raw file renders with a readable change memo.
