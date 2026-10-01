---
date: 2026-10-01 21:34
type: decision
status: todo  # proposed — awaiting founder approval
related:
  - 2026-10-01_2134_decision_agent-read-default-mode.md
  - 2026-10-01_2134_decision_agent-text-view.md
  - 2026-10-01_2134_decision_sdk-views-module.md
  - 2026-10-01_2134_decision_cli-show-modes-and-search.md
  - 2026-10-01_2134_decision_agent-search.md
  - 2026-10-01_2134_decision_agent-batch-ops.md
  - 2026-10-01_2134_decision_cli-edit-verb-and-batch.md
  - 2026-10-01_2134_decision_mcp-single-representation.md
  - 2026-10-01_2134_decision_mcp-lean-tool-surface.md
  - 2026-10-01_2134_decision_spec-partial-reads-informative.md
  - 2026-10-01_2134_decision_elision-stubs-round-trip.md
---

# Decision (READS-D1): `aim_read` gets read modes

**Status: proposed — awaiting founder approval.** Implemented (uncommitted) as part of the agent read and edit surface work on branch `wt/aim-agent-reads`, target release 0.6.0.

## Context

`aim_read` always returns the whole projection: every chunk's HTML, the
pending lane, optional history. Measured on 0.5.2 (tokens, `o200k_base`, the
text block the server sends): 6,462 on a real 81-chunk data-processing
addendum, 67,503 on a 775-chunk report. An agent that needs one clause pays
for the whole file on every read, and nothing lets it address a part of the
document. Documentation and agent guidance already imply partial navigation;
today it does not exist.

## Options considered

1. **Separate tools** (`aim_toc`, `aim_skeleton`, `aim_chunks`, `aim_text`).
   Each tool costs 60-100 fixed tokens in every session's tool list, which
   would cancel the tool-surface savings of READS-D10.
2. **One tool, `aim_read(mode=…)`, with an enum mode.** Chosen.
3. **A query language** (selectors, XPath-like). Over-engineered; models
   misuse free-form grammars.

## Decision

```
aim_read(path, mode="full", ids=None, words=8, include_history=False)
  mode ∈ full | toc | skeleton | text | chunks
```

| mode | returns | format |
|---|---|---|
| `full` | today's projection, same keys (title, lang, spec_version, seq, doc_hash, summary + stale flag, toc cache, every chunk `{id, container, html}`, proposals with payloads, optional history) | compact JSON |
| `toc` | outline built from the body on each call (not from the §8.1 cache) | text |
| `skeleton` | every chunk and container id in document order, indented by container depth: `[id] <tag>[.<class>…][{style}][xN run] <label> <first N words>`; container lines carry no words | text |
| `text` | the lossy text view ([READS-D3](2026-10-01_2134_decision_agent-text-view.md)) | text |
| `chunks` | canonical serialization of each referenced unit, data URIs as round-tripping stubs ([READS-D12](2026-10-01_2134_decision_elision-stubs-round-trip.md)) | text |

**toc entries.**
- Every top-level heading (`h1`-`h6`) and every slide (level 1, titled by its
  first heading). Grouping matches `generate_toc()` (§8.1).
- Every `num-1` and `num-2` block, nested one level below the current
  heading, shown as label + first 8 words. Many imported contracts number
  their sections with outline-numbered paragraphs rather than headings
  (§3.8); a heading-only outline is near-empty on them (the addendum above
  has 3 headings and 30 numbered blocks).
- One line per entry: `[first..last] <#×level> <label> <title> (<n units>)`.
  The range runs to the unit before the next entry of the same or higher
  rank, so it can be pasted into `ids` unchanged.
- Content before the first entry gets a leading untitled range; a document
  with no entries returns one untitled range; an empty document returns
  `(empty document)`.

**Headers.**
- `toc`, `skeleton`, `text`: `<title> | spec <v> | seq <n> | <chunks> chunks | <p> pending | summary: "<text>" [stale]` (summary omitted when absent).
- `chunks` (and `aim_search`): one line, `seq <n> | <p> pending`. These are
  the calls an agent repeats, so they do not repeat the summary.
- `doc_hash` stays out of the text modes (about 40 tokens, rarely used); it
  remains in `full`, in the JSON forms and in `aim hash`.

**`ids` in `mode=chunks`** — a list of references, resolved and printed in
document order:
- a chunk id or a container id (a container returns its whole subtree, which
  is what a container `modify` replaces);
- a reserved singleton: `aim:theme`, `aim:doc`;
- a proposal id `p-…` (§4.4 reserves the prefix): action, target or anchor,
  author, explanation and payload;
- a range `a..b`: inclusive, over the skeleton's unit order (containers and
  items, pre-order). Ids match `[a-z0-9][a-z0-9_-]*`, so `..` cannot occur
  inside one. A reversed range is an error.
- No duplicates: a container wholly inside the selection prints once as its
  subtree; items of a partly covered container print one by one with their
  context line.
- At most 200 references per call.

Unknown references do not fail the call: found units are returned, then a
`[missing] x, y` line. `chunks` without `ids` is an error.

Per unit:

```
[tl3nprt2] 1.1.8
<p data-aim="tl3nprt2" class="num-3 text-justify">…</p>
[r2] in pr; pending p-kg0mplpp add after this
<tr data-aim="r2">…</tr>
```

The context line carries the container (when not `body`), the numbering
label, and every pending card that targets the unit or anchors directly on
it. A new `modify` on a target that already has one replaces the older card
(§5.4); the agent should see that before writing.

**Other rules.** `include_history` is valid with `full` only (error
otherwise). `words` applies to `skeleton` only, range 0-50.

**Why the new modes return text.** They are read by a model; JSON escaping
and key names add 15-25% on HTML-heavy output. Programs get structured data
from the SDK ([READS-D4](2026-10-01_2134_decision_sdk-views-module.md)) and `aim show --format json`
([READS-D5](2026-10-01_2134_decision_cli-show-modes-and-search.md)).

## Consequences

- A targeted lookup (toc or search, then `chunks` for the units to change)
  costs roughly 350 tokens on the 81-chunk addendum and 350-3,000 on the
  775-chunk report, against 6,462 / 67,503 for `full`. That is a best case:
  it holds when the lookup succeeds first time.
- The claim "navigate without loading the whole file" becomes true for the
  agent's context. The server still parses the whole file on every call.
- Partial reads lengthen the time between read and write; a `modify`
  replaces the whole chunk, so a concurrent human edit can be overwritten.
  Every header carries `seq`, so an optional `if_seq` write precondition can
  be added later without changing any output shape (follow-up, not in this
  change).
- A downstream editor that pins this SDK does not use `aimformat.mcp`; it
  sees no change.

## Compatibility and migration

Additive. Callers that omit `mode` get `full` with the same keys and values
(apart from the elision stub text, [READS-D12](2026-10-01_2134_decision_elision-stubs-round-trip.md), and the compact
encoding, [READS-D9](2026-10-01_2134_decision_mcp-single-representation.md)). No spec change, no format change, no
`data-aim-version` change.

## Related decisions (same change)

- [READS-D2](2026-10-01_2134_decision_agent-read-default-mode.md) — the default read mode stays `full`
- [READS-D3](2026-10-01_2134_decision_agent-text-view.md) — the text view — rendering and lossy contract
- [READS-D4](2026-10-01_2134_decision_sdk-views-module.md) — public SDK module `aimformat.views`
- [READS-D5](2026-10-01_2134_decision_cli-show-modes-and-search.md) — CLI parity — `aim show --mode` and `aim search`
- [READS-D6](2026-10-01_2134_decision_agent-search.md) — `aim_search` — lexical ranking over chunk text
- [READS-D7](2026-10-01_2134_decision_agent-batch-ops.md) — batch operations `ops:[…]` on `aim_edit` and `aim_propose`
- [READS-D8](2026-10-01_2134_decision_cli-edit-verb-and-batch.md) — CLI direct edits and batch parity (`aim edit`, `aim propose batch`)
- [READS-D9](2026-10-01_2134_decision_mcp-single-representation.md) — MCP results go out once, as compact text
- [READS-D10](2026-10-01_2134_decision_mcp-lean-tool-surface.md) — a lean MCP tool surface
- [READS-D11](2026-10-01_2134_decision_spec-partial-reads-informative.md) — spec gets informative text on partial reads — no version bump
- [READS-D12](2026-10-01_2134_decision_elision-stubs-round-trip.md) — elided data URIs round-trip through edits

## Implementation notes (2026-10-01)

Implemented as decided, with these differences and measurements:

- `ids` with a mode other than `chunks` is an error (the design did not say),
  and so is `words` with a mode other than `skeleton` (review fix: it was
  ignored silently, unlike the CLI's `--words`); `words` therefore has no
  schema default, and `skeleton` uses 8 when it is omitted.
- `toc` on the 81-chunk contract costs 294 tokens with the `num-1`/`num-2`
  entries (67 heading-only); `chunks` for one unit costs 30-120.
