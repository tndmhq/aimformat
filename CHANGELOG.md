# Changelog

All notable changes to the spec and the reference toolkit. The package
version tracks the spec version it implements (0.x minors may break).

## Unreleased

- **The agent note** (spec §2.5): every new/imported document opens with a
  declarative head comment (`aim-note:`) telling LLM agents what the file
  is, where the docs live (aimformat.com/llms.txt), and the hand-editing
  invariants. Informative-only by spec — tools never execute anything
  because of it. SDK: `doc.note` / `set_note()` / `remove_note()` /
  `has_canonical_note()`; CLI: `aim note FILE... [--check|--remove]`;
  linter: S030 (warning) flags duplicate notes. The note text contains no
  markup, so structural substring checks never false-positive on it.
- **Pending-lane CLI verbs**: `aim propose {modify,add,delete,move,theme}`,
  `aim accept` / `aim reject` (by id or `--all`), with `--author human:ID |
  agent:MODEL | external:ID` attribution (`aim.parse_actor`), and
  `aim show --format json` for machine reads.
- **MCP server**: `pip install 'aimformat[mcp]'` (pinned `mcp==1.28.1`)
  then `aim mcp` — local stdio, six workflow tools: `aim_read` (projected
  view), `aim_edit`, `aim_propose`, `aim_resolve`, `aim_lint`,
  `aim_export`.
- **Agent Skill** under `skills/aimformat/` (open Agent Skills standard):
  `npx skills add tndmhq/aimformat`, or in Claude Code
  `/plugin marketplace add tndmhq/aimformat` (`.claude-plugin/` manifest).
- **docs/for-agents.md** — the canonical LLM-facing guide, served as
  https://aimformat.com/llms.txt.
- **evals/** — id-preservation harness measuring invariant compliance of
  naked LLM edits with vs without the agent note.
- **Packaging**: second console script `aimformat` (AimStack `aim`
  collision), version single-sourced from `__init__.py`, PyPI
  trusted-publishing workflow (`.github/workflows/publish.yml`).
- **Reconcile** (spec §6.8): `AimDocument.reconcile()` and `aim reconcile
  FILE [-o OUT] [--check]` detect out-of-band edits — hand edits,
  corruption, files that never had history — and repair the document by
  appending `origin:"reconcile"` events (external author) that declare the
  current body truth, so `verify()` passes again. Assigns ids where missing
  or conflicting, rejects pending proposals whose target vanished, reports
  unrepairable log damage as `residual`. Also the adoption path for
  hand-written `.aim` files. Refuses pruned or damaged logs
  (`HistoryError`) rather than guessing at an unrecoverable baseline.

## 0.1.0 — 2026-07-07

First published draft of the specification and the reference toolkit.

- **Spec** (`spec.md`): document anatomy, closed HTML/Tailwind-subset
  vocabulary, semantic chunks with runs and containers, the pending lane
  (template-inert proposals with attribution and deterministic
  supersede/chain semantics), append-only invertible history with
  checkpoint hashing, versioned-state-vs-caches split, retrieval layer,
  content-addressed assets, canonical form + `doc_hash`, security
  constraints, conformance rules. Generated construct-reference appendix;
  every snippet validated in CI.
- **SDK** (`aimformat`, stdlib-only): `AimDocument` with direct edits,
  batches, proposals (accept / accept-with-tweaks / reject / supersede),
  undo/redo, checkpoints, `verify()`, `state_at()`, flatten/prune, asset
  pack/gc, summary/TOC/embedding caches.
- **Verifier**: `aim lint` — structure, vocabulary, security, pending-lane,
  history-chain, and canonical-form rules with stable codes; JSON output.
- **CLI**: `aim lint | hash | new | show | flatten | css`.
- **Interop**: `from_docling()` (DoclingDocument → .aim, dependency-free)
  and `to_docx()` (.aim → Word, pending lane as real `w:ins`/`w:del`
  tracked changes or resolved on a copy; `[docx]` extra).
- **Conformance suite**: `tests/fixtures/ok_*` / `nok_<CODE>_*`, one rule
  per file; 260+ tests.
