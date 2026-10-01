---
date: 2026-10-01 21:34
type: decision
status: todo  # proposed — awaiting founder approval
related:
  - 2026-10-01_2134_decision_agent-read-modes.md
  - 2026-10-01_2134_decision_agent-read-default-mode.md
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

# Decision (READS-D3): the text view — rendering and lossy contract

**Status: proposed — awaiting founder approval.** Implemented (uncommitted) as part of the agent read and edit surface work on branch `wt/aim-agent-reads`, target release 0.6.0.

## Context

Whole-document work (review, summarise, plan a rewrite) needs every chunk's
content but not its markup. HTML markup is most of the read cost: on the
775-chunk report, every chunk as `[id] <html>` is 53k tokens, the same content
as plain text about 21k. `to_markdown()` is close in cost but drops chunk ids
(the document becomes unaddressable) and drops outline-numbering labels
(§3.8), which exist only as CSS counters, so "clause 1.1.8" cannot be found.

## Options considered

1. **`to_markdown()` per chunk.** Markdown escaping costs tokens and buys
   nothing here; numbering labels dropped; container items lose their ids.
2. **A dedicated, deliberately lossy rendering keyed by id.** Chosen.
3. **Plain text with no markers.** Headings, list items and table rows can
   no longer be told apart.

## Decision

**Lines.**
- Every unit starts a line with `[id] `, indented two spaces per level of
  container depth; a unit's continuation lines are indented two more.
- Document text never produces a line at the unit's own indentation; a
  content line that would begin with `[` is written `\[`. Document text can
  therefore not spoof the view's structure (a `pre` line `[abc12345] …`, a
  fake lossy notice).

**Blocks.**

| Element | Rendered as |
|---|---|
| `h1`-`h6` | `#` × level, space, text |
| `p` | its text |
| `num-1`…`num-9` blocks | prefixed with the rendered label: `1.`, `1.1`, `1.1.1`, or `<data-aim-num-prefix><n>` |
| `li` | computed marker, then text; nested lists as continuation lines |
| `tr` | `\| cell \| cell \|`; a cell with `colspan`/`rowspan` > 1 gets `{2 cols}` / `{3 rows}`; a cell's inner blocks joined with ` / ` |
| atomic `table`/`ul`/`ol` chunk | rows or items as continuation lines |
| container (`ul`, `ol`, `table`, `aim-slide`) | its own line (`[id] <list>`, `<table>`, `<slide>`), items indented below |
| `section`, `div`, `blockquote`, `figure` (block carriers: one chunk each) | one unit, inner blocks as continuation lines; `blockquote` lines get `> ` |
| `aim-page-break` | `--- page break ---` |
| `hr` | `---` |
| `pre` | its text, line breaks kept, as continuation lines |
| `img`, `svg role="img"` (packed assets, §9.1) | `[image: <alt or aria-label>]`; `figcaption` → `caption: <text>`; data URIs never appear |

`li` markers: `ol` honours `start`; `list-lower-alpha`, `list-upper-alpha`,
`list-lower-roman`, `list-upper-roman` set the counter style; `list-paren`
and `list-bare` set the suffix; `list-multilevel` gives the dotted chain of
ancestor items (`1.2.1`); `ul` uses `-`.

**Inline.** `strong`/`b` → `**…**`, `em`/`i` → `*…*` (1-3% more tokens; keeps
defined terms visible). `s` → `~~…~~`, so struck text never reads as live
text. Other marks (`u`, `sub`, `sup`, `mark`, `code`, `span`) dropped, text
kept. Links keep their text only. `br` → ` / `. Whitespace collapsed.

**Pending section.** The view ends with `## pending (<n>)`, one line per card:
`[p-…] <action> <target | into C after X> by <author>: <explanation> → <payload as text>`.
Payload only for add and modify, cut at 40 words; explanation and payload
collapsed to one line.

**Second line, always:**

```
(text view: lossy, for reading only. Classes, styles and attributes are omitted. Fetch exact HTML with mode=chunks before you edit a chunk.)
```

**Lossy contract** (stated in the tool description, the agent docs and the
spec note of [READS-D11](2026-10-01_2134_decision_spec-partial-reads-informative.md)): the text view is never an edit payload.
A `modify` replaces the whole chunk (§6.6); a payload rebuilt from the text
view would silently drop classes, inline paint and attributes. Safeguards: a
text-view line pasted as `html` fails with "payload contains no element"; the
header and the tool description warn; exact HTML is one `chunks` call away.
The server is stateless and does not track what the agent has read.

**Numbering labels** come from one SDK function, `numbering_labels(doc)`,
which simulates the §3.8 counters exactly as the generated stylesheet defines
them: counters instantiated once on `body`; `num-k` increments level k and
zeroes deeper levels; `num-restart` sets level k to 1; `data-aim-num-prefix`
replaces the chain with `prefix + counter(k)`; level 1 alone gets a `.`
suffix. A table-driven test parses the rules out of `generate_aim_css()` so
labels and stylesheet cannot drift. `tests/goldens/views/labels.json`
(fixture → id → label) is committed so other implementations can check a
port against it.

## Consequences

- Whole-document reads cost 2.2-3.2x less than `full` on the measured corpus
  (2,921 vs 6,462 tokens on the 81-chunk addendum; 21,052 vs 67,503 on the
  775-chunk report), close to a Markdown export, while keeping ids and
  rendered clause numbers.
- Labels make clause references findable by `aim_search`
  ([READS-D6](2026-10-01_2134_decision_agent-search.md)).
- `to_markdown()` still drops outline numbering; that is a separate bug,
  recorded as a follow-up.

## Compatibility and migration

Additive; a new read surface. The rendering is a presentation and is **not**
part of the spec (see [READS-D11](2026-10-01_2134_decision_spec-partial-reads-informative.md)): it may change between package
versions; golden tests pin it within a version.

## Related decisions (same change)

- [READS-D1](2026-10-01_2134_decision_agent-read-modes.md) — `aim_read` gets read modes
- [READS-D2](2026-10-01_2134_decision_agent-read-default-mode.md) — the default read mode stays `full`
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

- The `line-through` class renders as `~~…~~` too (same meaning as `s`).
- The `\[` escape also applies when the `[` follows leading spaces (a `pre`
  line indented to look like a deeper unit's `[id]`).
- Measured: 3,021 tokens on the 81-chunk contract (full JSON 5,592; the old
  indented text block 6,462), 21,172 on the 775-chunk report (58,953 /
  67,503).
