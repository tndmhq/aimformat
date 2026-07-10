# Changelog

All notable changes to the spec and the reference toolkit. The package
version tracks the spec version it implements (0.x minors may break).

## 0.2.1 — unreleased

- **`AimDocument.amend_proposal(pid, markup=None, *, explanation=None,
  at=None)`** — in-place amend of a pending proposal's payload and/or
  explanation, preserving id, anchor, author, batch, and dependencies.
  Implements what spec §5.4 already sanctions ("editing a pending payload
  in place is allowed and unrecorded"): no history event is appended;
  payload validation matches the original propose path (add payloads keep
  the proposed root id, so chained anchors stay stable). delete/move
  proposals are explanation-only. No spec change.

Fixed-layout pages: slides become correct pages end to end.

- **PDF**: each `aim-slide` prints as its own page **at its own canvas
  size** via per-slide CSS named pages (previously slides landed clipped
  on the document's global page). Flowing content keeps the `aim:doc`
  page setup; mixed documents interleave both.
- **Canvas-pt convention** (spec §3.3, informative): canvas px are
  point-equivalent at print — `960×540` is the native 16:9 slide,
  paper pages are their point size (A5 portrait `420×595`). Examples,
  fixtures, and spec snippets regenerated; new `examples/booklet.aim`
  shows fixed-layout A5 paper pages with a positioned image figure.
- **DOCX**: slides now **linearize** (page break + chunks in reading
  order, in-slide proposals ride the tracked-changes lane) instead of
  being silently dropped — a deck previously exported as an empty
  document. Figures honor an authored inline-style width (CSS px at
  96 dpi, clamped to the content box) instead of a hardcoded 4.5 in.
  In tracked mode, a pending add anchored after a slide starts on the
  following page (like accepted content), and a pending whole-slide add
  linearizes per block instead of collapsing into one inserted paragraph.
  An explicit `aim-page-break` immediately before a slide no longer
  doubles into a blank Word page.
- **SDK/linter**: a payload whose root is a bare `aim-slide` (no identity
  markers) now always takes the container path — `add_chunk`/proposals
  previously demoted it to an opaque *chunk* with unaddressable children,
  and the linter accepted the result. New rule **S031** (error):
  `aim-slide` marked as a chunk. `to_markdown` gains
  `pending="accept-all"/"reject-all"` (resolve-on-a-copy, like DOCX/PDF),
  and `aim export --pending` accepts the two modes for `.md` as well.
  Spec §3.3 now credits the canvas-pt print scale to the PDF exporter
  explicitly (the frozen v0.2 embedded print layer stays CSS-native;
  folding the scale in is deferred to a future stylesheet revision).
  Replacements now keep the target's kind: an `aim-slide` payload can
  never replace a chunk (and a container never becomes a flat block) —
  `modify_chunk`, `propose_modify`, and the accept path all reject what
  would fail S030/S031 on the next lint, including proposals authored
  by external tools.

## 0.2.0 — 2026-07-10

First release published to PyPI: `pip install aimformat`.

- **Pagination** (spec §3.6): `<aim-page-break></aim-page-break>` — the
  hard page break as an ordinary empty top-level chunk: addressable,
  movable, proposable, undoable (explicit open+close tags required;
  placement enforced top-level). And the `aim:doc` settings block (head
  script, `application/aim-doc+json`) defining page setup: registered
  named size, orientation, per-side mm margins (defaults = A4 portrait
  15 mm — the previous hardcoded PDF geometry). Whole-block modify
  semantics exactly like `aim:theme`: events, undo/redo, proposals,
  accept-with-tweaks. `doc_hash` covers the settings line when present;
  documents without it hash byte-identically to v0.1.
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
