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
  - 2026-10-01_2134_decision_elision-stubs-round-trip.md
---

# Decision (READS-D11): spec gets informative text on partial reads — no version bump

**Status: proposed — awaiting founder approval.** Implemented (uncommitted) as part of the agent read and edit surface work on branch `wt/aim-agent-reads`, target release 0.6.0.

## Context

§8.3 (LLM projection, informative) describes what an agent-facing read
returns, but not partial reads, and says nothing about the hazards found
here: a lossy reading view used as an edit payload, and elided data URIs
written back into a document. Other implementations of agent tooling should
not have to rediscover them.

## Options considered

1. **Informative text in §8.3 and Appendix B.** Chosen.
2. **A normative definition of the text view.** It is a presentation, does
   not round-trip, and freezing it gains nothing for interoperability.
3. **No spec change.** The hazards would stay implementation lore.

## Decision

Append to §8.3:

> Agent tooling should also offer partial reads, so that what a read costs
> follows what the agent needs rather than the size of the file:
> - an outline derived from heading chunks and outline-numbered blocks, with
>   the range of units each entry covers;
> - an id skeleton: every chunk and container id with its tag and a short
>   text prefix;
> - the exact serialization of a chosen set or range of units;
> - a ranked search over chunk text.
>
> Rendering outline numbering (§3.8) into these views lets references like
> "clause 1.1.8" be resolved. A plain-text rendering keyed by chunk id is a
> useful reading view, but it is lossy: it drops markup, classes and
> styles. It is never an edit payload. A `modify` replaces the target's
> whole serialization (§6.6), so an edit must start from the exact
> serialization of that chunk. A tool that elides data URIs on read should
> restore its own stubs on write, or refuse a payload that contains one: a
> stub written into a document destroys the asset it stood for.

Add to Appendix B, "Agent read path":

> …then, for a long document, read the outline or search, and fetch exact
> serializations only for the units you will change.

## Consequences

The spec documents the partial-read pattern and the two hazards; the
reference tooling ([READS-D1](2026-10-01_2134_decision_agent-read-modes.md), [READS-D3](2026-10-01_2134_decision_agent-text-view.md),
[READS-D12](2026-10-01_2134_decision_elision-stubs-round-trip.md)) follows it.

## Compatibility and migration

- Informative only: conformance and `data-aim-version` do not change, so the
  §3.7 versioning rules are not triggered and no spec version bump is made.
- The new text contains no `aim` code blocks (the spec block linter is
  unaffected).

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
- [READS-D12](2026-10-01_2134_decision_elision-stubs-round-trip.md) — elided data URIs round-trip through edits

## Implementation notes (2026-10-01)

Implemented as decided, no deviations.
