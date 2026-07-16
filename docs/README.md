# Agent memory: map & rules

Committed, cross-agent project memory. Any AI agent (Claude, Cursor, Codex,
Aider, Gemini CLI, …) and any human working in this repo reads and writes
these notes. The root [`AGENTS.md`](../AGENTS.md) links here; this folder holds
the depth. It is the shared, version-controlled layer, distinct from any single
tool's private/local memory.

Because this repo is public, the memory carries two extra rules (from the
Governance section of `AGENTS.md`): `knowledge/` is maintainer-authoritative
(changes reviewed like code), and everything in here is technical only, with
no business or strategy reasoning.

Two tiers:

## [`knowledge/`](knowledge/): semantic memory (curated, always fresh)

Stable facts and rules about the format and this repo. Edit in place and
keep it current: stale knowledge is worse than none. Currently sparse; it
grows as the spec and SDK land. The generic working conventions are not
duplicated here; they live in [`AGENTS.md`](../AGENTS.md) (canonical).

## [`log/`](log/): episodic memory (append-only, timestamped, light)

One file per episode: durable decisions, major implementation plans, and
substantial reviews/reports. Kept deliberately light, because this is a
fast-moving pre-product repo; day-to-day work does not need an entry, and
external contributors are never required to write one. Skim
[`log/index.md`](log/index.md) for recent context instead of opening every
file.

**Naming:** `YYYY-MM-DD_HHMM_<type>_<slug>.md`, type ∈
`plan | review | report | status | decision | event`, slug in kebab-case.
`HHMM` is the 24h time of day in UTC (one fixed, DST-free zone for everyone,
regardless of where the agent or contributor runs), so entries sort
chronologically even within the same day.

**Scaffolder** (creates the file + index row with the correct timestamp):

```bash
python3 scripts/new_log_entry.py <type> <slug> --what "one-line index summary"
```

**Frontmatter** (every entry):

```yaml
---
date: 2026-07-06 18:30
type: plan
status: active        # active | done | superseded
related: []
---
```

**Rules:**

1. **Append-only.** Never rewrite a log entry. To change course, write a new
   entry and flip the old one's `status` to `superseded` (frontmatter only).
2. **Index every entry.** Add a one-line row to [`log/index.md`](log/index.md)
   (newest first) when you create it; update the row's status when it changes.
3. **Plan before implementing.** For any non-trivial task, the finalized plan
   goes in as `…_plan_…` before the implementation starts.
4. **Consolidate on completion** (part of the definition of done): flip the
   entry's `status` and promote durable non-discoverable lessons into the
   right `knowledge/` file.
