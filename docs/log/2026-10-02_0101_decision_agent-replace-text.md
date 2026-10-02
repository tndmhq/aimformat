---
date: 2026-10-02 01:01
type: decision
status: todo  # proposed — awaiting founder approval
related:
  - 2026-10-01_2134_decision_agent-batch-ops.md
  - 2026-10-01_2134_decision_cli-edit-verb-and-batch.md
  - 2026-10-01_2134_decision_mcp-lean-tool-surface.md
  - 2026-10-01_2134_decision_agent-text-view.md
---

# Decision (READS-D13): markup-preserving text replacement (`replace_text`)

**Status: proposed — awaiting founder approval.** Implemented on branch
`wt/aim-agent-reads` (aimformat#42), target release 0.6.0. No format,
conformance or history change: the edit is recorded as an ordinary `modify`
event (or proposed as an ordinary `modify` card).

## Context

The token benchmark's hostile review (2026-10-01) found the biggest remaining gap against Markdown working copies: a single
edit through the MCP tools costs 2.5 to 4 times more end to end, because
`aim_edit`/`aim_propose` `modify` takes the chunk's whole HTML. In
formatting-heavy documents that HTML is mostly markup (per-run fonts,
colours, spans), and the agent must reproduce it byte for byte to avoid
changing formatting it did not mean to touch. Every text-editing agent tool
of note has a `str_replace` primitive for this reason.

## Options considered

1. **Keep `modify` only; make reads cheaper.** Reads are already cheaper
   (READS-D1 to D6); the write side still carries the whole chunk.
2. **Text diff/patch payloads (unified diff, JSON patch on the DOM).**
   Models produce these unreliably, and a DOM patch leaks the tree shape.
3. **`str_replace` on the raw HTML.** Cheap, but the agent has to quote
   markup exactly, and an edit can break tags or ids.
4. **`str_replace` on the chunk's text, markup kept.** Chosen.

## Decision

A new action `replace_text` wherever `modify` is accepted:

```
aim_edit(path, action="replace_text", target, old_text, new_text, explanation?, author?)
aim_propose(…same…)
ops: [{"action": "replace_text", "target", "old_text", "new_text", "explanation"?}, …]
aim edit|propose replace-text FILE TARGET --old STR --new STR
doc.replace_text(id, old_text, new_text, author=…, explanation=…)        # recorded modify
doc.propose_replace_text(id, old_text, new_text, author=…, explanation=…)  # modify card
aimformat.textedit.replace_in_markup(markup, old_text, new_text) -> str   # the pure core
```

**Matching.** `old_text` is matched against the chunk's text content
(`Chunk.text`: text nodes in document order, entities decoded, no tags; not
the text view's `**`/`~~` marks or numbering labels). A whitespace run on
either side matches any whitespace run on the other (whitespace runs in
`new_text` are written as one space). It must occur **exactly once**;
overlapping occurrences count. 0 is refused as "not found", 2+ as
"occurs N times; quote more surrounding words".

**Placement.** `old_text` and `new_text` are trimmed of their common prefix
and suffix first; only the span that actually changes has to sit inside
one text run. Quoting context across `<strong>` or `<a>` for uniqueness is
therefore free. A changed span that still crosses inline markup is
**refused** with the elements named, because where the new text belongs
(bold or not, inside the link or not) is a formatting decision the caller
has to make with a full `modify`. Two exceptions with no such decision:
a pure deletion may span runs, and a pure insertion exactly at a boundary
joins the run before it (the way typing continues the preceding
formatting). A formatting element left empty is removed.

**Identity and recording.** The chunk keeps its id, attributes and every
untouched inline element. The direct edit is a `modify` event; the
proposal is a `modify` card computed against the live chunk (so it counts
as the chunk's modify-or-delete card in a batch, READS-D7). In a batch,
`replace_text` sees the result of earlier edit ops on the same chunk.
`html` with `replace_text`, or `old_text`/`new_text` with any other action,
is refused.

## Measurements (o200k_base, bare call arguments as in the benchmark harness)

Corpus: the token benchmark's 10 converted documents. For every chunk with
20 or more words, one word near the middle is changed, quoting the
shortest unique span around it; `modify` carries the chunk's new HTML.

| doc | chunks | refused | modify median | replace_text median | ratio | modify p90 | replace_text p90 |
|---|---:|---:|---:|---:|---:|---:|---:|
| arxiv-2310.06825 | 62 | 3 | 116.5 | 41 | 2.8× | 186 | 43 |
| bonterms-cloud-terms | 81 | 0 | 105 | 41 | 2.6× | 160 | 46 |
| common-paper-mnda | 12 | 3 | 241 | 40.5 | 6.0× | 572 | 42 |
| legal-addendum | 28 | 0 | 110 | 42 | 2.6× | 165 | 47 |
| long-report | 117 | 2 | 87 | 41 | 2.1× | 104 | 44 |
| memo-policy | 7 | 1 | 84 | 41 | 2.0× | 4,497 | 44 |
| multi-column | 17 | 0 | 79 | 40 | 2.0× | 89 | 42 |
| nist-ai-100-1 | 206 | 2 | 122.5 | 41 | 3.0× | 206 | 45 |
| proposal-aster-labs | 4 | 0 | 88.5 | 40.5 | 2.2× | 94 | 41 |
| wikipedia-lighthouse | 79 | 12 | 137 | 41 | 3.3× | 197 | 44 |

Over all 613 applied chunks: 87,888 → 25,497 tokens (3.45× smaller). The
`replace_text` call is flat at about 40 to 47 tokens whatever the chunk's
markup; `modify` grows with it (memo-policy p90: a 4,497-token chunk). The
23 refusals (3.6%) are spans whose chosen "word" crosses markup, for
example `Purpose,` with `Purpose` bold and the comma plain, or text glued
across two paragraphs of one table cell; quoting the word alone applies.

The benchmark's scripted scenarios (old/new as written in the scenario
files, whole sentences; "minimal" = the word-aligned changed span, widened
until unique):

| scenario | modify | replace_text (as written) | replace_text (minimal) |
|---|---:|---:|---:|
| D3 common-paper-mnda (heavily formatted list item) | 314 | 81 | 46 |
| D3 legal-addendum (most of a plain sentence rewritten) | 91 | 107 | 107 |
| D3 proposal-aster-labs | 91 | 97 | 54 |
| D4 long-report, ten edits in one `ops` batch | 817 | 674 | 468 |

So `replace_text` wins wherever markup is heavy or the change is small
relative to the chunk, and loses slightly when most of a lightly formatted
chunk is rewritten (the call then carries the old and the new text, where
`modify` carries only the new): in that case `modify` stays the right tool.
The cost: the tools/list plus instructions grow from 5,392 to 5,881 bytes
(1,302 → 1,424 o200k tokens, +122, sent with every request that offers the
tools, usually from the prompt cache). The byte-budget test moves to 6,470.
Measured with a one-off script over the token benchmark's converted corpus
(the `benchmarks/` harness, landing in its own PR), using its counting
rules: `json.dumps` of the call arguments, `o200k_base`.

## Consequences

- Agents are steered to it by one line in the server instructions and in
  the `aim_edit` description; `docs/for-agents.md` and the skill document
  the rules.
- The text view (READS-D3) stays a reading view: `old_text` is a locator
  matched against the plain text, never a payload written into the file.
- Open: text glued across block boundaries inside one chunk (two `<p>` in
  a table cell) matches without a separator; quoting across such a
  boundary is refused anyway because the change crosses markup.

## Test plan

Red first (all failed before the implementation): `tests/test_textedit.py`
(the pure function: plain run, inside an inline element, context across
markup, link text, crossing refused, not found, 2 and overlapping
occurrences, empty/no-op, entities, emptied element pruned, deletion across
runs, whitespace runs, insertion at a boundary, multi-member run chunk; SDK
direct edit and proposal, refusals leave the document unchanged),
`tests/test_mcp.py` (single op keeps markup and id, proposal and `ops`
batch, refusals write nothing), `tests/test_cli.py` (`aim edit|propose
replace-text`, refusal exits 1 and writes nothing).
