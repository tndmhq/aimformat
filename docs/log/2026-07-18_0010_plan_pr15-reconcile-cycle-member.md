---
date: 2026-07-18 00:10
type: plan
status: done
related: []
---

# Plan: pr15 reconcile cycle member

Fix the fail-closed reconciliation path for a foreign-authored pending lane
whose chained adds form a cycle after unrelated valid cards:

1. Add a red-first regression with a valid modify followed by two mutually
   anchored adds, proving reconcile rejects a cycle member and preserves and
   accepts the modify with clean verification.
2. Make chained-add ordering failures identify their cycle participants and
   have reconcile reject one of those participants, preserving creation order
   for every non-cycle card and normal rejection rebinding for survivors.
3. Run the focused regression and the complete requested gate suite, then
   mark this plan done and commit the single logical change locally.
