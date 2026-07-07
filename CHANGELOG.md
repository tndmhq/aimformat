# Changelog

All notable changes to the spec and the reference toolkit. The package
version tracks the spec version it implements (0.x minors may break).

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
