---
date: 2026-07-17 23:04
type: plan
status: active
related: []
---

# Plan: pending lane creation order

Replace the branch's constraint-solving acceptance machinery with the
owner-approved pending-lane model:

1. Validate every newly proposed operation against a clone containing the
   existing pending lane projected in creation order, and identify the first
   conflicting pending proposal in errors.
2. Resolve whole lanes in creation order (retaining only chained-add rebinding),
   with an atomic accept-all dry-run on a clone before any live mutation.
3. Make reconcile revalidate the pending lane as a creation-order projection
   and reject invalid cards to a fixpoint; remove candidate/rescue reasoning.
4. Stop writing and replaying non-spec `x_source_*` move metadata.
5. Replace the permutation-ordering property model with a creation-order replay
   oracle, update affected regressions, and keep the full quality gates green.

Deliver the redesign as logical local commits only. Do not change
[`spec.md`](../../spec.md); report any tension between its proposal/history
language and the stricter SDK behavior.
