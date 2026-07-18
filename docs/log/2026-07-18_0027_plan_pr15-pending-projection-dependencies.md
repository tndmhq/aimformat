---
date: 2026-07-18 00:27
type: plan
status: done
related: []
---

# Plan: pr15 pending projection dependencies

Close the remaining class of proposal positions that resolve only through the
pending projection. The two concrete regressions are a first-position add into
a pending-add-introduced container and a move whose `LAST` destination is a
pending add's payload root.

1. Read the creation-order redesign and local-review commits from `442d6ae`
   through `b1423de`, then audit anchor and container resolution for every
   `propose_*` path.
2. Add red-first regressions for both reported failures and extend the
   creation-order property/oracle generator with pending-introduced containers
   and moves to pending tails.
3. Make projected-only dependencies consistent: encode the sanctioned
   pending-add proposal-id chain where the operation supports rebinding, and
   otherwise reject atomically at proposal creation.
4. Run focused tests and the complete gate (`ruff check`, `ruff format
   --check`, `mypy`, full `pytest`, and `aim lint examples/*.aim`) before each
   logical local commit. Do not push.
