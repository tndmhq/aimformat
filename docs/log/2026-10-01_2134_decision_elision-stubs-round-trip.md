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
  - 2026-10-01_2134_decision_mcp-single-representation.md
  - 2026-10-01_2134_decision_mcp-lean-tool-surface.md
  - 2026-10-01_2134_decision_spec-partial-reads-informative.md
---

# Decision (READS-D12): elided data URIs round-trip through edits

**Status: proposed — awaiting founder approval.** Implemented (uncommitted) as part of the agent read and edit surface work on branch `wt/aim-agent-reads`, target release 0.6.0.

## Context

Every MCP read replaces long `data:` URIs with the fixed string
`[data-uri elided]`, and the write path accepts that string. Reproduced on
0.5.2: an agent that reads a `figure` with an inline image and changes only
its caption writes back `src="[data-uri elided]"`; the image is gone from the
live document (only the history `before` keeps it). `mode=chunks`
([READS-D1](2026-10-01_2134_decision_agent-read-modes.md)) is advertised as "the HTML you edit from", which makes
this flow the normal one. The stub also differs from the form §8.3 shows
(`…[elided: 480KB, sha256:ab12…]`) and carries nothing to restore from.

## Options considered

1. **Refuse payloads containing a stub.** Safe, but an agent can then never
   edit a chunk that holds an inline image (a caption typo, a table with a
   logo).
2. **Stubs carry a content hash; the MCP/CLI write path restores them by
   hash from the current document and refuses a stub that matches nothing.**
   Chosen.
3. **Stop eliding in `chunks` mode.** Exact, but one inline photo can cost
   hundreds of thousands of tokens.

## Decision

- **Stub form follows §8.3:** `[elided: <size>, sha256:<first 16 hex of the
  URI's sha256>]`, replacing the whole URI inside the attribute value. Used
  everywhere the read surface elides: `full`, `chunks`, payloads, history,
  the CLI.
- **Restore on write.** Before an op runs, `_ops.py`
  ([READS-D7](2026-10-01_2134_decision_agent-batch-ops.md)) replaces every stub in its `html` with the data URI
  whose hash prefix matches, searching the live chunks, the theme block and
  pending payloads.
- **Unmatched stub fails the op:** `payload contains an elided data URI that
  matches nothing in this document; fetch the chunk again with mode=chunks`.
  Nothing is written.
- **The SDK's `modify_chunk` / `add_chunk` know nothing about stubs.**
  Restoration is the job of the tools that elide, as the spec text of
  [READS-D11](2026-10-01_2134_decision_spec-partial-reads-informative.md) says.
- Packed assets (`<use href="#asset-…">`, §9.1) are unaffected: the chunk
  carries no data URI.

## Consequences

- Image chunks can be edited through MCP and the CLI without losing the
  image.
- The stub in `full` grows from about 4 to about 15 tokens per elided URI.
- A downstream editor that pins this SDK does not use the MCP read surface;
  no change for it.
- Tests: read an image chunk with `chunks`, change the caption leaving the
  stub, the image bytes survive; an unknown-hash stub is refused and the file
  is byte-identical; `include_history` shows stubs.

## Compatibility and migration

- The stub text is a visible output change: CHANGELOG "Changed" (and the fix
  under "Fixed") in 0.6.0.
- Files older tools already wrote with `[data-uri elided]` are not repaired;
  lint flags their `src` scheme, as it does today.
- No spec conformance change; the SDK write API is unchanged.

## Related decisions (same change)

- [READS-D1](2026-10-01_2134_decision_agent-read-modes.md) — `aim_read` gets read modes
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

## Implementation notes (2026-10-01)

Implemented as decided, with these differences and measurements:

- `<size>` is the length of the elided URI itself (as serialized), formatted
  `NB` / `NKB` / `N.NMB`.
- Review fix: stubs also restore from history payloads, not only from live
  chunks, pending payloads and the theme. `aim_read(include_history=True)`
  elides history the same way, and the history is the only place a deleted
  chunk's data survives, so re-adding deleted content from it used to be
  refused.
