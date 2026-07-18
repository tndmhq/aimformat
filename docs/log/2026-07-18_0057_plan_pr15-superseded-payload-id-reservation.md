---
date: 2026-07-18 00:57
type: plan
status: done
related: []
---

# Plan: pr15 superseded payload id reservation

Fix the replacement-proposal normalization path without reintroducing the
superseded card's structural effects into the pending projection.

1. Read commit `8cf1556` and trace `_taken_ids()`, `_supersede_if_pending()`,
   projection construction, proposal normalization, and acceptance.
2. Add a red-first regression that supersedes a pending proposal, explicitly
   reuses one of its payload IDs, and proves the displayed replacement ID is
   exactly the ID later applied by `accept()` while `verify()` remains clean.
3. Feed the structurally excluded card's payload IDs into the burned/taken ID
   set used to normalize its replacement, preserving existing remint semantics.
4. Run the focused regression, then ruff check, ruff format check, mypy, full
   pytest, and lint every example before making one local commit.
