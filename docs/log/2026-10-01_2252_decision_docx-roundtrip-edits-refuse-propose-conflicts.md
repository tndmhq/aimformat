---
date: 2026-10-01 22:52
type: decision
status: todo  # proposed — awaiting maintainer approval
related:
  - 2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md
  - 2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md
  - 2026-10-01_2252_review_docx-roundtrip-adversarial.md
---

# Decision: edits mode refuses `conflicts="propose"`

**Status:** proposed — awaiting maintainer approval. Implemented on
`wt/aim-docx-roundtrip` during the adversarial review.

## Context

ROUNDTRIP-D9 lets a conflicting colleague change (the unit also changed in
the document since the export, or carries a pending card) be "proposed
anyway, flagged" with `conflicts="propose"`. In proposals mode that is a
card a reviewer can reject. In edits mode the same flag produced a
**direct edit**: the colleague's version silently overwrote the change made
in the document since the export, while the report still listed it as a
conflict. Reproduced: export, edit chunk X locally, colleague edits X,
`import_revision(changes="edits", conflicts="propose")` replaced the local
edit.

## Options considered

1. **Refuse the combination** (`InvalidOperation`; CLI exit 2) — the flag
   only has a meaning where a reviewer decides.
2. Ignore `conflicts` in edits mode (always report) — a silently ignored
   argument.
3. Keep the overwrite — loses a local edit without review (recoverable
   from history only).

## Decision

Option 1. `import_revision(changes="edits", conflicts="propose")` raises
`InvalidOperation("conflicts='propose' needs changes='proposals' …")`;
`aim import --onto … --as edits --conflicts propose` exits 2. MCP surfaces
the same error.

## Consequences

- No conflicting change can be applied directly; conflicts in edits mode
  are always reported and not written.
- The tndm editor, if it adopts edits mode, must not pass the combination.

## Compatibility and migration

None: `import_revision` is new and unreleased. No `.aim` change.
