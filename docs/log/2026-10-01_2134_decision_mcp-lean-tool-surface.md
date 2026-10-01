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
  - 2026-10-01_2134_decision_spec-partial-reads-informative.md
  - 2026-10-01_2134_decision_elision-stubs-round-trip.md
---

# Decision (READS-D10): a lean MCP tool surface

**Status: proposed — awaiting founder approval.** Implemented (uncommitted) as part of the agent read and edit surface work on branch `wt/aim-agent-reads`, target release 0.6.0.

## Context

Every session pays for the tool list before any work. Today `tools/list`
costs 1,815 tokens plus 206 for the server instructions. Where they go: an
auto-generated `title` on every property; `anyOf:[T,null]` for every
optional argument; an empty `outputSchema` per tool; one 20-token sentence
repeated in all six descriptions; docstring indentation (`\n        `) kept
inside descriptions. This change adds a seventh tool and several arguments,
so without trimming the fixed cost would grow.

## Decision

1. **Leaner schemas.** A small `FastMCP` subclass overrides the public
   `list_tools()` and rewrites each `inputSchema`: drop auto-generated
   `title`s; collapse `anyOf:[T,null]` with `default:null` into `T`; inline
   every `$ref` and drop `$defs` (several tool-calling clients do not
   resolve references). Call validation is unchanged: FastMCP validates
   against its own pydantic model, so an explicit `null` is still accepted.
2. **Descriptions.** Whitespace collapsed (`inspect.cleandoc`, lines
   joined); text rewritten to what a caller needs to call correctly.
3. **Enums via `Literal`**, including inside the op items:
   `EditOp` / `ProposeOp` are `typing_extensions.TypedDict`s with
   `action: Required[Literal[…]]`, so batch ops keep the enum the
   top-level `action` has.
4. **Server instructions rewritten**: the trusted-stdio sentence and the
   `AIMFORMAT_MCP_ROOT` pointer appear there once instead of in every tool.
   The stderr warning when no root is set stays.
5. **Budget test.** The compact JSON of `tools/list` plus the instructions
   must stay under a byte budget, set at implementation to the measured size
   plus 10% (about 5,800 bytes expected). Bytes, not a token estimate: CI
   needs no tokenizer, and the guard is not calibrated on a ratio derived
   from the surface it guards.

**What the shorter text keeps:** propose versus edit; author attribution;
explanations that stand alone; the lossy text view; `$N`; the op caps. Style
and colour guidance stays in the Skill and `docs/for-agents.md`.

## Consequences

- Prototype measurement: seven tools ≈ 1,300 tokens + 176 for instructions,
  against 1,815 + 206 for six today (about −27%). Re-measured after
  implementation; the PR reports the final numbers.
- Tests assert: exactly seven tools; no `outputSchema`; no `title`, `$ref`,
  `$defs` anywhere; enums present including inside `ops` items; no
  newline-plus-indent runs in descriptions; the byte budget.

## Compatibility and migration

Tool names and argument names do not change; schemas describe the same
accepted inputs. `typing_extensions` is already a dependency of `mcp`; the
core package stays standard-library only. (`aim_edit` says `set_theme` while
`aim_propose` says `theme`; renaming would break callers, so it is left as a
recorded follow-up.)

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
- [READS-D11](2026-10-01_2134_decision_spec-partial-reads-informative.md) — spec gets informative text on partial reads — no version bump
- [READS-D12](2026-10-01_2134_decision_elision-stubs-round-trip.md) — elided data URIs round-trip through edits

## Implementation notes (2026-10-01)

Implemented as decided, with these differences and measurements:

- An unknown `action` is now rejected by argument validation (the `Literal`
  enum); the error lists the allowed values instead of the old "unknown edit
  action" text.
- The op TypedDicts carry `extra="forbid"` (see READS-D7), which adds
  `additionalProperties: false` to the `ops` item schemas.
- Measured: `tools/list` 1,815 -> 1,321 tokens (seven tools instead of six);
  compact JSON 6,402 -> 4,519 bytes; instructions 206 tokens. Budget set to
  5,950 bytes (5,412 measured + 10%; 5,392 after review fixes).
