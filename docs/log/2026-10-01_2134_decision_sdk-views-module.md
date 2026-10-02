---
date: 2026-10-01 21:34
type: decision
status: todo  # proposed — awaiting founder approval
related:
  - 2026-10-01_2134_decision_agent-read-modes.md
  - 2026-10-01_2134_decision_agent-read-default-mode.md
  - 2026-10-01_2134_decision_agent-text-view.md
  - 2026-10-01_2134_decision_cli-show-modes-and-search.md
  - 2026-10-01_2134_decision_agent-search.md
  - 2026-10-01_2134_decision_agent-batch-ops.md
  - 2026-10-01_2134_decision_cli-edit-verb-and-batch.md
  - 2026-10-01_2134_decision_mcp-single-representation.md
  - 2026-10-01_2134_decision_mcp-lean-tool-surface.md
  - 2026-10-01_2134_decision_spec-partial-reads-informative.md
  - 2026-10-01_2134_decision_elision-stubs-round-trip.md
---

# Decision (READS-D4): public SDK module `aimformat.views`

**Status: proposed — awaiting founder approval.** Implemented (uncommitted) as part of the agent read and edit surface work on branch `wt/aim-agent-reads`, target release 0.6.0.

## Context

The read modes ([READS-D1](2026-10-01_2134_decision_agent-read-modes.md)), text view ([READS-D3](2026-10-01_2134_decision_agent-text-view.md)) and
search ([READS-D6](2026-10-01_2134_decision_agent-search.md)) need one implementation shared by the MCP
server and the CLI, and SDK users should get the same data structurally.

## Options considered

1. **Methods on `AimDocument`.** The class is already over 4,000 lines.
2. **A module of pure functions that take a document**, mirroring
   `aimformat.diff`. Chosen.
3. **Private helpers in the MCP server.** SDK users and the CLI could not
   reach them.

## Decision

Option 2. Each function walks the tree once.

```python
# aimformat/views.py — public; re-exported in aimformat.__all__
@dataclass(frozen=True)
class Unit:            # one addressable unit in document order
    id: str; kind: Literal["chunk", "container"]; container: str
    depth: int; tags: tuple[str, ...]; classes: tuple[str, ...]
    styled: bool; label: str | None; text: str

@dataclass(frozen=True)
class OutlineEntry:
    first: str; last: str; level: int
    kind: Literal["heading", "slide", "numbered", "untitled"]
    title: str; label: str | None; units: int

@dataclass(frozen=True)
class Hit:
    id: str; score: float; section: str | None; snippet: str; pending: tuple[str, ...]

def units(doc) -> list[Unit]
def numbering_labels(doc) -> dict[str, str]
def outline(doc) -> list[OutlineEntry]
def resolve_refs(doc, refs) -> tuple[list[str], list[str]]   # (ids, missing)
def search(doc, query: str, k: int = 8) -> list[Hit]
def render_toc(doc) -> str
def render_skeleton(doc, words: int = 8) -> str
def render_text(doc) -> str
def render_chunks(doc, refs) -> str
```

**`generate_toc()` is not touched.** Its output is the §8.1 cache that
consumers read; rebasing it on `outline()` would risk those bytes for no
user gain. A test pins that `outline()`'s heading and slide entries cover
exactly the top-level ids `generate_toc()` lists.

**Prerequisite (internal, no behaviour change): `AimDocument.chunks` and
`chunk()` become O(n).** Today `chunks` calls `find_chunk` and
`container_of_chunk` once per chunk, each walking the whole tree, and
`modify_chunk` returns `self.chunk(cid)`, which rebuilds the whole list.
Measured on the 775-chunk report: `doc.chunks` 0.9 s (one tree walk is
0.004 s); 10 direct edits 11.6 s; 10 proposals 119 s. After the fix: 10
edits 0.04 s, 10 proposals 3.0 s. `chunks` becomes one walk that groups run
members and tracks the container; `chunk()` looks the id up directly and
raises the same `TargetNotFound`. Objects and order are unchanged; an
equivalence property test over every fixture and the parity goldens pin it.

## Consequences

- New public API; `views` joins `__all__`.
- A downstream editor that pins this SDK gets faster `doc.chunks` and
  `modify_chunk` and sees no other change.
- The TS reader does not get these views in this change; `labels.json`
  makes a later port checkable.

## Compatibility and migration

New public API only. The O(n) rewrite preserves results exactly.

## Related decisions (same change)

- [READS-D1](2026-10-01_2134_decision_agent-read-modes.md) — `aim_read` gets read modes
- [READS-D2](2026-10-01_2134_decision_agent-read-default-mode.md) — the default read mode stays `full`
- [READS-D3](2026-10-01_2134_decision_agent-text-view.md) — the text view — rendering and lossy contract
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

- Beyond the listed functions the module also exports `render_search`,
  `short_header`, `stub_for`, `elide` and `full_projection` (the `full`
  projection moved here so the CLI can print it without the `[mcp]` extra),
  and `aimformat.views` is listed in `aimformat.__all__`.
- P1 measured: `doc.chunks` on the 775-chunk report 0.93 s -> 0.008 s;
  `chunk()` 0.001 s. Equivalence pinned in `tests/test_chunk_lookup.py`
  (every example, parity and conformance fixture) and a hypothesis property.
