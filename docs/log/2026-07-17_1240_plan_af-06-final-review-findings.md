---
date: 2026-07-17 12:40
type: plan
status: active
related: []
---

# Plan: AF-06 final review findings

Address only the three selected Codex findings on PR #13, preserving the
maintainer's explicit decision not to version or migrate the pre-release
canonical-form break.

1. Document in spec §11.1 and `CHANGELOG.md` that explicit end tags for empty
   non-void HTML elements are an intentionally incompatible, intentionally
   unversioned pre-release change; do not alter any version constant.
2. Add a red regression with a self-closing non-void element in a rejected
   historical proposal payload, then minimally extend C002 across the
   markup-bearing history fields and commit the fix independently.
3. Add a red regression for a self-closing block container that should report
   C002 without the C001 alias, then preserve the canonical block layout in
   the C002-only comparison and commit the fix independently.
4. Run Ruff checks and formatting verification, mypy, the full pytest suite,
   and `aim lint` across all examples; consolidate this entry, push
   `fix/af-06-canonical-self-closing`, and verify the remote branch head.
