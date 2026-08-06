---
date: 2026-08-06 20:22
type: decision
status: done
related: []
---

# Decision: the editor-reload SDK surface — diff + divergence become public API

## Problem

Any live `.aim` consumer that follows a file on disk (an editor pane, a
watcher, a CI bot) hits the same two questions when the file changes under
it:

1. **What changed?** — at the format's own granularity (addressable units:
   chunks, container items, containers), not text lines.
2. **Does the file's own history explain the change?** — the answer decides
   what "undo" means after a reload:
   - new proposal cards appeared → the pending lane changed, body untouched;
   - new history events appeared and replaying them from the previously-seen
     state reproduces the new body → an SDK-driven edit, `doc.undo()` is
     correct;
   - the body changed with **no** (or insufficient) new events → a raw text
     write. `doc.undo()` would now revert the wrong (older) event; the
     change must first be adopted into history.

Raw text writes are an advertised way to edit `.aim` (spec §6.8, "editing as
plain text is legal"), so case 3 is a first-class path, not corruption.

## Decision

- **`aimformat.diff` becomes a public module** with two entry points:
  - `diff_documents(old, new) -> DocumentDiff` — unit-level diff between two
    parsed versions: `added` / `deleted` / `modified` / `moved` unit ids plus
    `theme_changed` / `doc_settings_changed` / `version_changed`. Unit
    identity and serialization reuse the reconcile machinery (`_units`), so
    diff and reconcile can never disagree about what a unit is. Containers
    compare by *skeleton* (open tag + shells), so a member edit marks the
    member, not every ancestor container.
  - `classify_divergence(old, new) -> Divergence` — the reload-tier
    classifier: `new_events` (the appended suffix when old's log is a prefix
    of new's), `history_rewritten` (it isn't — flatten/prune/hand-rewrite),
    `new_proposals` / `removed_proposals`, and `content_drift` (new's body is
    not explained by its own history relative to old: no new events but the
    doc_hash moved, or replaying the new events back to old's seq does not
    reproduce old's hash).
- **`aim diff OLD NEW`** joins the CLI (text + `--format json`) over the same
  engine.
- **External-edit synthesis is NOT new API** — it already exists as
  `doc.reconcile()` (spec §6.8): expected-vs-actual edit script appended as
  `direct_edit` events, `origin: "reconcile"`, external author, dry-run
  supported. Consumers handle case 3 by calling reconcile after classifying.
  Nothing is added or changed there.
- **`aim normalize` already ships** (§11 canonical re-spell); no work needed.
- **Skill guidance tightens** (`skills/aimformat/SKILL.md`): for edits to
  *existing* documents, prefer the tooling paths (CLI/SDK/MCP propose or
  direct edit) over hand-editing, because each edit lands attributed and
  invertible in history at write time; hand-editing stays fully legal (then
  `aim reconcile`) and raw-text authoring of *new* documents stays
  first-class.

## Why public (and why now)

The immediate consumer is an editor reloading files an agent writes, but
neither question is editor-specific: the answers are defined entirely by
format concepts (units, the history contract, the pending lane). Keeping
diff/divergence private to one consumer would invite parallel
reimplementations that drift from reconcile's unit semantics — the exact
failure the reconcile machinery centralizes against.

## Non-goals

- No new event kinds, origins, or spec changes — this is SDK surface over
  existing spec concepts.
- No line/word-level text diffing; the unit is the format's granularity.
- `classify_divergence` assumes `old` is a version the caller held in a
  consistent state (its precondition is documented on the function).
