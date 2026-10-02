---
date: 2026-10-01 21:21
type: decision
status: proposed — awaiting maintainer approval
related: [2026-10-01_2121_decision_auto-accept-policy-in-aim-doc.md, 2026-10-01_2121_decision_auto-accept-version-floor.md, 2026-10-01_2121_decision_auto-accept-sdk-batch-close.md, 2026-10-01_2121_decision_auto-accept-guards.md, 2026-10-01_2121_decision_batch-undo-and-version-events.md, 2026-10-01_2121_decision_auto-accept-agent-surfaces.md, spec.md]
---

# Decision: auto-accepted changes are ordinary `accepted` resolutions with an `auto` marker

**Decision ids:** AA-D4.
**Status:** proposed, awaiting maintainer approval (draft PR, not merged).

## Context

When a change is accepted without a person looking at it, history must say
so, say whose consent it rests on, and keep the agent as the proposer. Every
existing consumer (replay, verify, undo, diff, DOCX tracked-changes export,
review UIs) already understands an `accepted` resolution; the record should
not break them.

## Options considered

1. **New `decision` value `auto_accepted`.** Rejected: breaks every consumer
   that switches on `accepted` (replay, `state_changing`, exporters).
2. **Reuse `origin`.** Rejected: `origin` says how an edit came about
   (`user`, `undo`, `reconcile`), not who decided; mixing the two makes both
   ambiguous.
3. **A `direct_edit` with an `x_auto_accepted_by` field.** Rejected: loses the
   proposal, its `proposed_by`, and its `proposed` payload; `x_` is the vendor
   namespace and the format should not squat in it.
4. **Delegation on the actor (`decided_by.on_behalf_of`, or a marker inside
   `decided_by`).** Rejected: touches the actor schema everywhere, mixes who
   decided with how, and `Actor.from_obj` drops unknown keys, so every SDK
   round trip would lose the marker.
5. **New optional field on the resolution event.** Chosen.

## Decision

```json
{"kind": "resolution", "decision": "accepted", "proposal": "p-7k2",
 "proposed_by": {"type": "agent", "model": "..."}, "proposed_at": "...",
 "decided_by": {"type": "human", "id": "Ada"},
 "auto": "policy", "batch": "b19", "before": "...", "proposed": "..."}
```

- `auto` ∈ `"policy" | "request"`.
  - `"policy"`: accepted because the document's `review.agents` was `"auto"`;
    `decided_by` = `review.by`.
  - `"request"`: accepted because the caller was told to accept this change
    (per-call accept); `decided_by` = the named human, else `review.by` if a
    policy exists, else `{"type": "human"}`.
- Only valid with `decision: "accepted"`. `decided_by.type` MUST be `human`.
  Violations are H003 (event field schema via `Event.validate`); no new H code.
- `proposed_by`, `proposed_at` and `proposed` are kept as for any resolution.
  `applied` is never written under `auto` (no tweaks without a person
  looking), so `applied == proposed` by construction.
- Semantics: under `auto`, `decided_by` records standing or instructed
  consent, not a review. Readers who want "what did a person actually look
  at" filter on the absence of `auto`.
- Replay, verify, invertibility and `state_changing` are untouched: it is an
  `accepted` resolution. Attribution fields are not part of the chain check
  (§6.7), so verification is unaffected.

## Consequences

- Spec §6.2: the `resolution` row gains `auto`; enum line; the accepted-only
  and human-decider rules. Generated Appendix A.5 carries the since-tag.
- `registry.json`: `events.fields.resolution.optional += "auto"`,
  `events.auto_values`, `events.since.auto = "0.6"`.
- Requires spec 0.6 (see [auto-accept-version-floor](2026-10-01_2121_decision_auto-accept-version-floor.md)). Tools
  pinned at 0.5.x reject the unknown field (H003 in lint, `HistoryError` in
  reconcile, drift in diff); that is the reason for the floor.
- Tests: H003 for `auto` on a rejected resolution, for a bad value, and for a
  non-human decider.
