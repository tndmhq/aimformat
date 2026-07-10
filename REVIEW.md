# REVIEW.md — review guidance for AI reviewers

Guidance for every AI reviewer on this repo (Claude via `@claude` mention,
Codex auto-review, or any future bot). Reviews are **advisory** — a human
merges everything.

## The contract

- **At most 5 findings per review**, ranked by severity. If you found more,
  report the worst five and say so.
- **Severity-tag every finding**: `[critical]` (data loss, spec violation,
  security) → `[major]` (wrong behavior, broken invariant) → `[minor]`
  (real but low-impact defect).
- **No nits.** Style, formatting, import order, and naming are ruff's and
  mypy's job (CI `lint`); never comment on anything a linter enforces or a
  reasonable maintainer could shrug at.
- **Silence when unsure.** Only report findings you are confident are real
  defects, with the failure scenario stated concretely. No speculative
  refactors, no "consider…", no questions dressed as findings.
- **Read the existing PR comments first** and skip anything another reviewer
  (e.g. Codex) already flagged — agreement is signal, repetition is noise.
- **One sticky review comment, updated in place** — never stack a new
  comment per push.

## What to check (aimformat-specific)

1. **Spec conformance.** `spec.md` is normative. Behavior changes must match
   it (or change it in the same PR — never let SDK and spec diverge).
   Appendix A is generated from `src/aimformat/registry.json`
   (`scripts/gen_spec_appendix.py`); flag any hand-edit.
2. **Zero-runtime-dependency rule.** `[project.dependencies]` stays `[]`;
   the core SDK imports stdlib only. Converter/ingest/export imports live in
   optional extras and must stay lazy (inside functions). Flag any new
   top-level third-party import.
3. **Public-API stability.** Anything exported from `aimformat/__init__.py`
   is public. Flag signature changes, renames, or removals; new API should
   be deliberate, documented, and tested.
4. **Core invariants.** Round-trip identity (`loads(dumps(d)).dumps()`
   byte-identical), history replay (`verify() == []`), lint-clean SDK
   output, deterministic ops via `at=`. `tests/test_properties.py` encodes
   these — flag changes that weaken a property instead of fixing the bug.
5. **Python 3.10–3.13 compatibility.** `requires-python = ">=3.10"`; CI
   tests all four. Flag 3.11+ syntax/stdlib (e.g. `tomllib`,
   `except*`, `typing.Self`) outside version guards.
6. **Public repo hygiene.** No business/strategy content, no secrets, no
   references to private tndmhq repos beyond what `AGENTS.md` already says.
