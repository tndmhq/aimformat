---
date: 2026-10-01 21:34
type: decision
status: todo  # proposed — awaiting founder approval
related:
  - 2026-10-01_2134_decision_agent-read-modes.md
  - 2026-10-01_2134_decision_agent-read-default-mode.md
  - 2026-10-01_2134_decision_agent-text-view.md
  - 2026-10-01_2134_decision_sdk-views-module.md
  - 2026-10-01_2134_decision_cli-show-modes-and-search.md
  - 2026-10-01_2134_decision_agent-batch-ops.md
  - 2026-10-01_2134_decision_cli-edit-verb-and-batch.md
  - 2026-10-01_2134_decision_mcp-single-representation.md
  - 2026-10-01_2134_decision_mcp-lean-tool-surface.md
  - 2026-10-01_2134_decision_spec-partial-reads-informative.md
  - 2026-10-01_2134_decision_elision-stubs-round-trip.md
---

# Decision (READS-D6): `aim_search` — lexical ranking over chunk text

**Status: proposed — awaiting founder approval.** Implemented (uncommitted) as part of the agent read and edit surface work on branch `wt/aim-agent-reads`, target release 0.6.0.

## Context

An outline helps only when the target sits under a meaningful heading. An
agent told "fix the subprocessor notice period" or "change clause 1.1.8"
needs to locate a chunk by content. Today the only option is reading
everything.

## Options considered

1. **BM25 over each body chunk's text-view rendering, labels included.**
   Standard library only, deterministic, offline. Chosen.
2. **Hybrid with stored embeddings (§8.2).** The query must be embedded by
   the same model as the stored vectors, which needs a network provider and
   breaks the offline, dependency-free core.
3. **Substring grep.** No relevance ordering for multi-term queries.

## Decision

Option 1. Embedding search stays a documented future extension
(`query_vector=`, `model=`, rank fusion), not part of this change.

| Aspect | Rule |
|---|---|
| Tokens | Case-folded, no stemming. Three kinds: dotted number chains (`\d+(?:\.\d+)+`, so `1.1.8` is one token, also emitted with its parts); Unicode word runs (`\w+`); runs of Han, Hiragana, Katakana or Hangul split into overlapping character bigrams (a single character stays as is) |
| Prefix match | Only when a query term has no exact match. A document term then matches at weight 0.5 if one is a prefix of the other, the shorter is ≥ 4 characters, and they differ by ≤ 3 characters (`subprocessors` ↔ `subprocessor`) |
| Ranking | BM25, k1 = 1.2, b = 0.75, over body chunks; containers are not indexed, their items are |
| Quoted phrases | `"…"` filters: chunks without the phrase (case-folded, whitespace-normalized substring) are dropped. Unquoted queries never filter |
| Ties | Document order |
| Limits | Query ≤ 512 characters and ≤ 32 terms; `k` 1-50, default 8; the query is never compiled as a regular expression |

Output: the short header (`seq <n> | <p> pending`), then one line per hit:

```
[id] § <nearest preceding heading or level-1 numbered block, ≤40 chars> | <about 24 words around the first match> [pending p-…]
```

No hits → `no matches for "<query>"`. Scores appear in the JSON forms only.
Pending payloads are not searched (a hit id would then be ambiguous between
a chunk and a card); cards on a hit are flagged instead.

**Why these tokenizer rules.** A prototype with plain `\w+` tokens and
no prefix rule failed in exactly the cases agents hit: `subprocessors`
returned nothing on a document that says "Subprocessor"; `1.1.8` split into
`1`, `1`, `8`; CJK text became one token per run, so search failed on
Chinese and Japanese. Each rule above answers one of those.

**Tests assert properties, not tuned ranks:** a chunk with all query terms
outranks one with some; a phrase filter drops chunks without it; `1.1.8`
returns the chunk labelled 1.1.8 first; a plural query finds the singular; a
CJK query finds a CJK chunk; empty and punctuation-only queries return no
matches without error; the caps hold. No precision claims are published
until measured on the final implementation.

## Consequences

- A seventh MCP tool (fixed cost about 100 tokens, offset by
  [READS-D10](2026-10-01_2134_decision_mcp-lean-tool-surface.md)).
- Search hits feed directly into `aim_read mode=chunks`.

## Compatibility and migration

Additive. No spec change; search is tooling over existing content.

## Related decisions (same change)

- [READS-D1](2026-10-01_2134_decision_agent-read-modes.md) — `aim_read` gets read modes
- [READS-D2](2026-10-01_2134_decision_agent-read-default-mode.md) — the default read mode stays `full`
- [READS-D3](2026-10-01_2134_decision_agent-text-view.md) — the text view — rendering and lossy contract
- [READS-D4](2026-10-01_2134_decision_sdk-views-module.md) — public SDK module `aimformat.views`
- [READS-D5](2026-10-01_2134_decision_cli-show-modes-and-search.md) — CLI parity — `aim show --mode` and `aim search`
- [READS-D7](2026-10-01_2134_decision_agent-batch-ops.md) — batch operations `ops:[…]` on `aim_edit` and `aim_propose`
- [READS-D8](2026-10-01_2134_decision_cli-edit-verb-and-batch.md) — CLI direct edits and batch parity (`aim edit`, `aim propose batch`)
- [READS-D9](2026-10-01_2134_decision_mcp-single-representation.md) — MCP results go out once, as compact text
- [READS-D10](2026-10-01_2134_decision_mcp-lean-tool-surface.md) — a lean MCP tool surface
- [READS-D11](2026-10-01_2134_decision_spec-partial-reads-informative.md) — spec gets informative text on partial reads — no version bump
- [READS-D12](2026-10-01_2134_decision_elision-stubs-round-trip.md) — elided data URIs round-trip through edits

## Implementation notes (2026-10-01)

Implemented as decided, with these differences and measurements:

- Ranking is coordination-first: chunks matching more distinct query terms
  rank above chunks matching fewer, then BM25 orders within a level. This
  makes the "all terms above some terms" property hold by construction.
- Near forms found by the prefix rule are merged into one pseudo-term per
  query term (summed tf, union df), weighted 0.5.
- Measured on the 81-chunk contract: `1.1.8` -> 1.1.8 first;
  `subprocessors` -> three chunks containing "Subprocessor" (the definition
  1.1.13 ranks third, behind shorter chunks); `"EU Data Protection Laws"`
  -> 1.1.6, 1.1.8 (both contain the phrase). Search output costs 40-160
  tokens for k=3.
