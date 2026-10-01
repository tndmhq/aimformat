---
date: 2026-10-01 21:34
type: decision
status: todo  # proposed — awaiting founder approval
related:
  - 2026-10-01_2134_decision_agent-read-modes.md
  - 2026-10-01_2134_decision_agent-read-default-mode.md
  - 2026-10-01_2134_decision_agent-text-view.md
  - 2026-10-01_2134_decision_sdk-views-module.md
  - 2026-10-01_2134_decision_agent-search.md
  - 2026-10-01_2134_decision_agent-batch-ops.md
  - 2026-10-01_2134_decision_cli-edit-verb-and-batch.md
  - 2026-10-01_2134_decision_mcp-single-representation.md
  - 2026-10-01_2134_decision_mcp-lean-tool-surface.md
  - 2026-10-01_2134_decision_spec-partial-reads-informative.md
  - 2026-10-01_2134_decision_elision-stubs-round-trip.md
---

# Decision (READS-D5): CLI parity — `aim show --mode` and `aim search`

**Status: proposed — awaiting founder approval.** Implemented (uncommitted) as part of the agent read and edit surface work on branch `wt/aim-agent-reads`, target release 0.6.0.

## Context

The CLI is the agent surface for environments without MCP (and the one the
Skill documents first). Read modes and search added only to MCP would leave
CLI-only agents reading whole files.

## Options considered

1. **Extend `aim show` with `--mode`, add `aim search`.** Chosen.
2. **A new `aim read` verb.** Duplicates `show`; two verbs for one job.

## Decision

```
aim show FILE [--mode overview|full|toc|skeleton|text|chunks]
              [--ids ID[,ID|A..B…]] [--words N] [--format text|json]
aim search FILE QUERY [-k N] [--format text|json]
```

- `overview` stays the default; its output is byte-identical to today's
  `aim show`, text and JSON (pinned by a golden).
- Text modes print exactly what MCP returns.
- `--format json` prints lists of `Unit`, `OutlineEntry` and `Hit` records
  ([READS-D4](2026-10-01_2134_decision_sdk-views-module.md)); `--mode full --format json` prints the MCP `full`
  projection.
- Exit codes: 0 success; 1 every reference in `--ids` was missing; 2 usage
  error.

## Consequences

CLI and MCP give the same views; docs can describe one read path for both.

## Compatibility and migration

Additive; `aim show` without new flags is unchanged.

## Related decisions (same change)

- [READS-D1](2026-10-01_2134_decision_agent-read-modes.md) — `aim_read` gets read modes
- [READS-D2](2026-10-01_2134_decision_agent-read-default-mode.md) — the default read mode stays `full`
- [READS-D3](2026-10-01_2134_decision_agent-text-view.md) — the text view — rendering and lossy contract
- [READS-D4](2026-10-01_2134_decision_sdk-views-module.md) — public SDK module `aimformat.views`
- [READS-D6](2026-10-01_2134_decision_agent-search.md) — `aim_search` — lexical ranking over chunk text
- [READS-D7](2026-10-01_2134_decision_agent-batch-ops.md) — batch operations `ops:[…]` on `aim_edit` and `aim_propose`
- [READS-D8](2026-10-01_2134_decision_cli-edit-verb-and-batch.md) — CLI direct edits and batch parity (`aim edit`, `aim propose batch`)
- [READS-D9](2026-10-01_2134_decision_mcp-single-representation.md) — MCP results go out once, as compact text
- [READS-D10](2026-10-01_2134_decision_mcp-lean-tool-surface.md) — a lean MCP tool surface
- [READS-D11](2026-10-01_2134_decision_spec-partial-reads-informative.md) — spec gets informative text on partial reads — no version bump
- [READS-D12](2026-10-01_2134_decision_elision-stubs-round-trip.md) — elided data URIs round-trip through edits

## Implementation notes (2026-10-01)

Implemented as decided, with these differences and measurements:

- `--mode full` prints the compact MCP JSON in both formats; `--mode chunks
  --format json` prints an object `{seq, pending, chunks: [{id, kind, html}],
  missing}` rather than a bare list.
- `--ids` takes comma-separated references and may be repeated.
- The no-flag output is pinned byte-for-byte by goldens captured from 0.5.2
  (`tests/goldens/views/show-proposal.{txt,json}`).
