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
  - 2026-10-01_2134_decision_agent-search.md
  - 2026-10-01_2134_decision_agent-batch-ops.md
  - 2026-10-01_2134_decision_cli-edit-verb-and-batch.md
  - 2026-10-01_2134_decision_mcp-lean-tool-surface.md
  - 2026-10-01_2134_decision_spec-partial-reads-informative.md
  - 2026-10-01_2134_decision_elision-stubs-round-trip.md
---

# Decision (READS-D9): MCP results go out once, as compact text

**Status: proposed — awaiting founder approval.** Implemented (uncommitted) as part of the agent read and edit surface work on branch `wt/aim-agent-reads`, target release 0.6.0.

## Context

Measured over real stdio (`mcp==1.28.1`): FastMCP sends every tool result
twice — as `structuredContent` (because it derives an `outputSchema` from the
`-> dict[str, Any]` return annotation) and as a `TextContent` holding
`to_json(result, indent=2)`. For `aim_read` on the 81-chunk addendum the
whole result is 13,842 tokens on the wire (text block 6,462,
structuredContent 6,079); compact JSON of the same content is 5,546. How many
copies reach the model depends on the client.

## Options considered

1. **Keep both copies** (status quo).
2. **Text only:** `@server.tool(structured_output=False)`, tools return `str`.
   Chosen.
3. **`structuredContent` with a stub text block.** Clients that read only
   `content` (the backward-compatible path in the MCP spec) would see
   nothing.
4. **Both copies, text block compacted.** Keeps the duplication.

## Decision

Option 2. JSON results are serialized with
`json.dumps(…, separators=(",", ":"), ensure_ascii=False)`. The new read
modes ([READS-D1](2026-10-01_2134_decision_agent-read-modes.md)) return plain text.

## Consequences

- Wire size drops about 60% (13,842 → 5,546 tokens for `aim_read` on the
  addendum); what the model sees drops about 14% for clients that read the
  text block.
- No tool advertises an `outputSchema` any more (about 130 fixed tokens
  saved in `tools/list`).
- Non-ASCII text travels as characters, not `\uXXXX` escapes.

## Compatibility and migration

- **Wire-shape change.** Programs that read `structuredContent` must parse
  the text block instead (the test suite already does). Keys and values of
  the JSON results are unchanged.
- Released as 0.6.0 with a CHANGELOG entry under "Changed".
- A downstream editor that pins this SDK does not use the MCP server.
- No spec or file-format change.

## Related decisions (same change)

- [READS-D1](2026-10-01_2134_decision_agent-read-modes.md) — `aim_read` gets read modes
- [READS-D2](2026-10-01_2134_decision_agent-read-default-mode.md) — the default read mode stays `full`
- [READS-D3](2026-10-01_2134_decision_agent-text-view.md) — the text view — rendering and lossy contract
- [READS-D4](2026-10-01_2134_decision_sdk-views-module.md) — public SDK module `aimformat.views`
- [READS-D5](2026-10-01_2134_decision_cli-show-modes-and-search.md) — CLI parity — `aim show --mode` and `aim search`
- [READS-D6](2026-10-01_2134_decision_agent-search.md) — `aim_search` — lexical ranking over chunk text
- [READS-D7](2026-10-01_2134_decision_agent-batch-ops.md) — batch operations `ops:[…]` on `aim_edit` and `aim_propose`
- [READS-D8](2026-10-01_2134_decision_cli-edit-verb-and-batch.md) — CLI direct edits and batch parity (`aim edit`, `aim propose batch`)
- [READS-D10](2026-10-01_2134_decision_mcp-lean-tool-surface.md) — a lean MCP tool surface
- [READS-D11](2026-10-01_2134_decision_spec-partial-reads-informative.md) — spec gets informative text on partial reads — no version bump
- [READS-D12](2026-10-01_2134_decision_elision-stubs-round-trip.md) — elided data URIs round-trip through edits

## Implementation notes (2026-10-01)

Implemented as decided, with these differences and measurements:

- Measured over real stdio: `aim_read` on the 81-chunk contract 13,842 ->
  6,389 tokens on the wire (text block 6,462 -> 5,592); on the 775-chunk
  report 144,116 -> 67,620 (67,503 -> 58,953).
