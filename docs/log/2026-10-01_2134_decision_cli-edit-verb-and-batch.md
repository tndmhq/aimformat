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
  - 2026-10-01_2134_decision_mcp-single-representation.md
  - 2026-10-01_2134_decision_mcp-lean-tool-surface.md
  - 2026-10-01_2134_decision_spec-partial-reads-informative.md
  - 2026-10-01_2134_decision_elision-stubs-round-trip.md
---

# Decision (READS-D8): CLI direct edits and batch parity (`aim edit`, `aim propose batch`)

**Status: proposed — awaiting founder approval.** Implemented (uncommitted) as part of the agent read and edit surface work on branch `wt/aim-agent-reads`, target release 0.6.0.

## Context

The agent docs and the Skill say commanded edits can be made "via the CLI".
They cannot: the CLI can only propose. A CLI-only agent asked to make a
commanded change either hand-edits the file (then must reconcile) or fills
the pending lane with cards the user must accept one by one. Batch ops
([READS-D7](2026-10-01_2134_decision_agent-batch-ops.md)) also need a CLI form.

## Options considered

1. **Add `aim edit {modify,add,delete,move,theme,batch}` and
   `aim propose batch`.** Chosen.
2. **Fix the docs only.** Leaves CLI-only agents without direct edits.
3. **A single `aim apply --mode`.** A verb nobody would look for.

## Decision

```
aim edit {modify,add,delete,move,theme} FILE …   # same args/flags as aim propose
aim edit batch    FILE OPS.json|-                 # JSON array of ops (READS-D7), '-' = stdin
aim propose batch FILE OPS.json|-
```

- Shared flags: `--author`, `--explanation`, `-o`, `--format`.
- Text output: one line per result, then `wrote FILE`. JSON output: the
  READS-D7 result object.
- Default author stays `external:aim-cli`.
- Same caps (100 / 25), same `$N` rules, same stub restoration
  ([READS-D12](2026-10-01_2134_decision_elision-stubs-round-trip.md)) as MCP, via the shared `_ops.py`.

## Consequences

The documented "direct edit via the CLI" path becomes true; the docs and the
Skill are updated to show `aim edit`.

## Compatibility and migration

Additive; existing `aim propose` subcommands are unchanged.

## Related decisions (same change)

- [READS-D1](2026-10-01_2134_decision_agent-read-modes.md) — `aim_read` gets read modes
- [READS-D2](2026-10-01_2134_decision_agent-read-default-mode.md) — the default read mode stays `full`
- [READS-D3](2026-10-01_2134_decision_agent-text-view.md) — the text view — rendering and lossy contract
- [READS-D4](2026-10-01_2134_decision_sdk-views-module.md) — public SDK module `aimformat.views`
- [READS-D5](2026-10-01_2134_decision_cli-show-modes-and-search.md) — CLI parity — `aim show --mode` and `aim search`
- [READS-D6](2026-10-01_2134_decision_agent-search.md) — `aim_search` — lexical ranking over chunk text
- [READS-D7](2026-10-01_2134_decision_agent-batch-ops.md) — batch operations `ops:[…]` on `aim_edit` and `aim_propose`
- [READS-D9](2026-10-01_2134_decision_mcp-single-representation.md) — MCP results go out once, as compact text
- [READS-D10](2026-10-01_2134_decision_mcp-lean-tool-surface.md) — a lean MCP tool surface
- [READS-D11](2026-10-01_2134_decision_spec-partial-reads-informative.md) — spec gets informative text on partial reads — no version bump
- [READS-D12](2026-10-01_2134_decision_elision-stubs-round-trip.md) — elided data URIs round-trip through edits

## Implementation notes (2026-10-01)

Implemented as decided, with these differences and measurements:

- A single `aim propose <action>` keeps its pre-0.6 output exactly (and, as
  before, does not lint); `aim edit` and both batch verbs lint after the
  write and report `lint_errors` in JSON (stderr line in text mode).
