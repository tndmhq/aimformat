---
date: 2026-07-17 12:09
type: plan
status: done
related: []
---

# Plan: AF-06 C002 review fixes

Address only the two Codex findings on the new C002 rule; leave the accepted
canonical serializer change unchanged.

1. Add a regression proving authored self-closing non-void markup plus an
   independent canonical defect reports both C002 and C001.
2. Add a regression proving object-only `lint(doc)` does not report the
   source-dependent C002 rule.
3. Make C002 source-backed and additive without suppressing the remaining
   canonical-form checks.
4. Run Ruff formatting/checks, mypy, the full pytest suite, and lint every
   example; then consolidate this entry, make the single requested commit,
   and push `fix/af-06-canonical-self-closing`.
