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
  - 2026-10-01_2134_decision_cli-edit-verb-and-batch.md
  - 2026-10-01_2134_decision_mcp-single-representation.md
  - 2026-10-01_2134_decision_mcp-lean-tool-surface.md
  - 2026-10-01_2134_decision_spec-partial-reads-informative.md
  - 2026-10-01_2134_decision_elision-stubs-round-trip.md
---

# Decision (READS-D7): batch operations `ops:[…]` on `aim_edit` and `aim_propose`

**Status: proposed — awaiting founder approval.** Implemented (uncommitted) as part of the agent read and edit surface work on branch `wt/aim-agent-reads`, target release 0.6.0.

## Context

- N edits cost N round trips; each loads, saves and re-lints the file.
- N calls produce N history batches, against §6.3's "one editing intention
  per batch".
- An agent adding a heading and then a paragraph after it cannot know the
  heading's id without reading the document again (a single `aim_edit` add
  does not even return the id it created today).

## Options considered

1. **Partial success.** Leaves half an intention applied.
2. **All-or-nothing.** Chosen.
3. **Split multi-block `html` automatically.** Hides structure decisions in
   the tool; `$N` covers the need explicitly.

## Decision

```
aim_edit(path, action?, target?, html?, container="body", after?, theme_slots?,
         explanation?, ops?: list[EditOp], author?)
aim_propose(…same…, ops?: list[ProposeOp])     # action set: add|modify|delete|move|theme
EditOp / ProposeOp = {action (required, enum), target?, html?, container?, after?, theme_slots?, explanation?}
```

**Arguments.**
- Exactly one of `action` or `ops`.
- `ops`: 1-100 entries for `aim_edit`, 1-25 for `aim_propose`.
- The single-op form runs as a one-element batch: one code path.
- A top-level `explanation` is the default for ops without one; `author`
  applies to the whole call.

**Behaviour.**
1. **Load once, apply in order** inside `with doc.batch():`, so every event
   or card shares one batch id. A version-upgrade event triggered by an op
   joins that batch (§3.7).
2. **Any op error aborts before anything is written.** The error names the
   op: `ops[3] (modify c42a): <message>; nothing was written`. The in-memory
   document is discarded. This covers op errors, not a process crash during
   the save (`save()` is a plain write; making it atomic is a separate
   follow-up, because replacing the file changes its inode, which can break
   inode-based watchers in consumers).
3. **Back-references `$N`** in `target` and `after`, N lower than the current
   op's index. Forward and out-of-range references are errors. `$` cannot
   start an id, so there is no ambiguity.

   | ops[N] was | `$N` resolves to | usable as |
   |---|---|---|
   | an `aim_edit` add | the new chunk id | `target` or `after` of any later op |
   | an `aim_propose` add | its `p-` id | `after` of a later **add** into the **same container** only (the §5.2 chain the SDK supports) |
   | any other op | its target | `target` or `after`, with the usual checks (a deleted target fails as an anchor) |
   | `set_theme` / `theme` | `aim:theme` | nothing; using it is an error |

4. **Agent-chosen ids** in a payload are kept only if valid and unused, as
   today. Reusing an id that an earlier op in the same batch created is an
   error; the tool never silently re-mints an id that a later `$N` or
   `after` might point at.
5. **Proposals: one card per target per batch** (at most one modify-or-delete
   card and one move card). A second would supersede the first inside the
   same call and leave a junk `superseded` resolution in history. Direct
   edits have no such limit.
6. **Save, then lint**, as today. Lint findings do not roll the file back.

**Result (compact JSON).** Today's keys plus:

```json
{"ok":true,"seq":91,"doc_hash":"sha256:…","lint_errors":0,"batch":"b7",
 "results":[{"op":0,"id":"x8k2m1q0","target":"x8k2m1q0"},{"op":1,"id":"p-3f9a","target":"c42a"}]}
```

- `id`: what the op created — new chunk id for an edit add, the proposal id
  for every propose op, otherwise the target.
- `target`: the document unit addressed; absent for a proposed add.
- Single-op calls keep their current keys (`proposal` for `aim_propose`);
  `aim_edit` gains `id`.
- Propose results add `superseded`: ids of existing cards the batch replaced.

**Why the caps.** With the O(n) chunk lookup ([READS-D4](2026-10-01_2134_decision_sdk-views-module.md)), direct
edits are linear: 100 ops take well under a second on the 775-chunk report.
Proposals stay quadratic in batch size: op *i* clones the document and
replays every card created before it in the batch plus those already
pending. Measured on that report: 20 proposals 8.5 s, 30 16.7 s, 50 45.5 s;
100 would take about 3 minutes. The MCP server runs sync tools inline, so a
long batch blocks the stdio server, and a client timeout plus retry would
land the batch twice. 25 keeps the worst case near 12 s. A batch-scoped
projection (validate op *i* against one cached projection with all earlier
cards applied; run the full projection only to build an error or when an op
replaces a card) is the route to raising the cap; it is a follow-up, not
part of this change.

**Timeouts.** The docs tell agents to check `seq` (via `aim_read` `toc` or
`chunks`) after a timeout before retrying.

## Consequences

- **History shape: unchanged.** A batch is N ordinary events or N cards
  sharing one batch id, exactly what `doc.batch()` produces today. Undo and
  verification work per event as before.
- A downstream editor that pins this SDK and reloads files written by agents
  sees, via `classify_divergence`, N new events in one batch or N new
  proposals sharing `data-batch` — both already handled.
- Implemented once in an internal module `aimformat/_ops.py` (validation,
  `$N`, dispatch, stub restoration of [READS-D12](2026-10-01_2134_decision_elision-stubs-round-trip.md)), shared by MCP
  and the CLI ([READS-D8](2026-10-01_2134_decision_cli-edit-verb-and-batch.md)).

## Compatibility and migration

Additive. Single-op calls keep their arguments, keys and exact error texts;
the `ops[i]` prefix appears only when `ops` is used. No spec or history
change.

## Related decisions (same change)

- [READS-D1](2026-10-01_2134_decision_agent-read-modes.md) — `aim_read` gets read modes
- [READS-D2](2026-10-01_2134_decision_agent-read-default-mode.md) — the default read mode stays `full`
- [READS-D3](2026-10-01_2134_decision_agent-text-view.md) — the text view — rendering and lossy contract
- [READS-D4](2026-10-01_2134_decision_sdk-views-module.md) — public SDK module `aimformat.views`
- [READS-D5](2026-10-01_2134_decision_cli-show-modes-and-search.md) — CLI parity — `aim show --mode` and `aim search`
- [READS-D6](2026-10-01_2134_decision_agent-search.md) — `aim_search` — lexical ranking over chunk text
- [READS-D8](2026-10-01_2134_decision_cli-edit-verb-and-batch.md) — CLI direct edits and batch parity (`aim edit`, `aim propose batch`)
- [READS-D9](2026-10-01_2134_decision_mcp-single-representation.md) — MCP results go out once, as compact text
- [READS-D10](2026-10-01_2134_decision_mcp-lean-tool-surface.md) — a lean MCP tool surface
- [READS-D11](2026-10-01_2134_decision_spec-partial-reads-informative.md) — spec gets informative text on partial reads — no version bump
- [READS-D12](2026-10-01_2134_decision_elision-stubs-round-trip.md) — elided data URIs round-trip through edits

## Implementation notes (2026-10-01)

Implemented as decided, with these differences and measurements:

- `container` also accepts `$N` (an `aim_edit` add's id), so a batch can add
  a container and then fill it.
- With `ops`, a top-level `target`, `html`, `container`, `after` or
  `theme_slots` is an error (it would otherwise be ignored silently; review
  fix for `container`, which had a `"body"` default and so slipped past the
  check: adds meant for a list landed in `body`). `container` therefore has
  no schema default; an op without one still means `body`.
- Unknown op fields are refused (MCP: `extra="forbid"` on the op TypedDicts;
  CLI: the executor's own check), so a misspelt `anchor` cannot land an add
  at the end of the document.
- An agent-chosen id that is taken gets a fresh id, as before; a later op in
  the same batch that names the chosen id literally is refused with "refer to
  it as $N". When the literal id also names live content (the payload copied
  an existing chunk's id), the error says the reference is ambiguous and
  names both ways out: `$N` for the new content, or drop `data-aim` from the
  payload to mean the existing one.
- Single-op `aim_propose` results gain `superseded` as well.
- Measured on the 775-chunk report: 100 direct edits 0.34 s; 10 proposals
  2.65 s; 25 proposals 14.7 s (the cap's worst case).
