---
date: 2026-10-01 21:21
type: decision
status: proposed — awaiting maintainer approval
related: [2026-10-01_2121_decision_auto-accept-policy-in-aim-doc.md, 2026-10-01_2121_decision_auto-accept-resolution-marker.md, 2026-10-01_2121_decision_auto-accept-version-floor.md, 2026-10-01_2121_decision_auto-accept-guards.md, 2026-10-01_2121_decision_batch-undo-and-version-events.md, 2026-10-01_2121_decision_auto-accept-agent-surfaces.md, spec.md]
---

# Decision: the SDK auto-accepts at the close of the outermost batch, all or nothing

**Decision ids:** AA-D6, AA-D7, AA-D8.
**Status:** proposed, awaiting maintainer approval (draft PR, not merged).

## Context

With the policy on ([auto-accept-policy-in-aim-doc](2026-10-01_2121_decision_auto-accept-policy-in-aim-doc.md)), or on a
per-call request, `propose_*` must turn the new card into an accepted change.
Agent turns usually create many cards inside one `with doc.batch():`, and
later cards may chain on earlier ones (`after=p.id`) or amend them
(`amend_proposal`). Accepting each card the moment it is created would break
both, and would make "one agent turn = one undoable unit" an accident.

## Options considered

1. **Accept inside each `propose_*` call.** Rejected: breaks chained adds and
   amends within a batch; a mid-batch failure leaves a half-accepted turn.
2. **Accept per card at batch close, keeping whatever succeeds.** Rejected:
   a turn that lands half accepted and half pending is harder to read and to
   undo than one that is fully pending with a reason.
3. **Accept every in-scope card at the close of the outermost batch, in
   creation order, all or nothing after a dry run on a clone.** Chosen.

## Decision

**AA-D6, when and how.**

- Every `propose_*` registers its card with its per-call `accept` value. When
  the **outermost** batch exits normally (never on exception), the SDK
  computes the in-scope cards and accepts them inside the same batch id: the
  version upgrade first if needed, then resolutions in creation order (the
  order `accept_all` already proves safe, §5.4).
- Dry run on a clone first. On any failure nothing changes, the cards stay
  pending, and the outcome is reported through `doc.last_auto_accept`
  (`AutoAcceptOutcome{batch, via, decided_by, accepted, deferred, reason}`).
  It **never raises**.
- A `propose_*` called outside any batch owns its own batch, so a single
  MCP/CLI/SDK call is accepted before it returns and `Proposal.resolution` is
  set. Inside a caller's batch, `Proposal.resolution` is `None` until the
  block closes; afterwards `doc.resolution_of(pid)` returns the event.
- In scope: still pending (not superseded later in the batch); and either
  `accept is True` (via `request`) or `accept is None`, batch knob not
  `False`, policy `auto`, author `agent` or `external` (via `policy`). Plus
  the guards in [auto-accept-guards](2026-10-01_2121_decision_auto-accept-guards.md).
- Mixed `via` in one batch: the request group first, then the policy group,
  each with its own `decided_by`, both in the same batch.
- Switching the policy on does **not** sweep cards that are already pending.
- An interrupted producer (a host that stops an agent turn midway) should
  persist under `batch(auto_accept=False)`: stopping means "not this". This
  is documented guidance for SDK users.

**AA-D7, per-call API.**

- `propose_modify/add/delete/move/theme/page_setup(..., accept=None|True|False,
  accept_by=None)`: `None` honours the policy, `True` requests acceptance of
  this card (with or without a policy, any author), `False` keeps it pending
  even under the policy.
- `batch(*, auto_accept=None|True|False)` sets the same for every card in the
  batch; nested batches inherit the outermost setting.
- `False` is a **tool-level** override (preview copies, fallback replays). It
  is not exposed on MCP or the CLI: agents cannot opt out of the document
  owner's policy. "Agent asks for review of one change" is a backlog item.

**AA-D8, applying the policy to existing cards.**

- `auto_accept(pids, *, via="policy", decided_by=None, at=None)` accepts
  already-existing pending cards with the same all-or-nothing rule. It exists
  for hosts that enforce the policy on behalf of writers that do not honour
  it (for example a file watcher seeing a write from an older tool). The
  batch is the open batch, else the cards' shared `data-batch`, else new.
  `via="policy"` without a policy raises `InvalidOperation`.

## Consequences

- New: `review.py` (`ReviewPolicy`, `AutoAcceptOutcome`), `review_policy`,
  `set_review_policy`, `auto_accept`, `resolution_of`, `last_auto_accept`,
  `auto_accepted_batches(scan_limit=400, max_batches=5)` (display helper over
  the trailing history; never orders by `t`).
- `accept()` public signature unchanged; `_resolve(..., auto=...)` writes the
  field through `_resolve_retaining_paint`, so paint/typography upgrades still
  happen.
- Spec §5.6: a conforming writer SHOULD honour `auto` by accepting in the
  creating batch, in creation order, with `decided_by = review.by` and
  `auto: "policy"`; MAY accept on instruction with `auto: "request"`; a failed
  acceptance leaves the batch's cards pending.
- Tests: each change kind under the policy; human author stays pending;
  `accept=True` on a 0.5 document without policy writes the upgrade and
  `auto: "request"` in one batch; chained add + amend in one batch accepted at
  exit; dry-run refusal leaves the document byte-identical to the no-auto
  result; property test: an auto turn equals `accept_all` on the same lane;
  `verify()` clean throughout.
