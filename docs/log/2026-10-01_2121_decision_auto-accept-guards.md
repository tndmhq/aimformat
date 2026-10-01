---
date: 2026-10-01 21:21
type: decision
status: proposed — awaiting maintainer approval
related: [2026-10-01_2121_decision_auto-accept-policy-in-aim-doc.md, 2026-10-01_2121_decision_auto-accept-resolution-marker.md, 2026-10-01_2121_decision_auto-accept-version-floor.md, 2026-10-01_2121_decision_auto-accept-sdk-batch-close.md, 2026-10-01_2121_decision_batch-undo-and-version-events.md, 2026-10-01_2121_decision_auto-accept-agent-surfaces.md, spec.md]
---

# Decision: guards on auto-accept (the policy is never changed by a proposal; a person's suggestion is never replaced unseen)

**Decision ids:** AA-D9 (as amended), AA-D27, AA-D28.
**Status:** proposed, awaiting maintainer approval (draft PR, not merged).

## Context

Two paths would let auto-accept do something no person agreed to.

1. **A proposal that flips the policy.** `aim:doc` proposals carry the whole
   settings block. A page-setup card could quietly carry
   `"review": {"agents": "auto"}`; a person accepting it sees margins, not the
   policy switch. A stale page-setup card created before the policy changed
   would, on acceptance, flip the policy back.
2. **Supersede.** `_supersede_if_pending` resolves any pending modify or
   delete on the same target as `superseded`, with `decided_by` set to the new
   card's author. Under auto, an agent card on a chunk where a person has a
   pending suggestion would throw the person's card away and land with nobody
   looking, contradicting "proposals authored by humans still wait".

## Options considered

- For (1): only refusing to *auto*-accept a card that changes `review`
  (original AA-D9). Rejected as too weak: a person could still accept it
  manually without seeing the switch. Chosen instead: a proposal can never
  change `review` at all.
- For (2): forbid agents from superseding human cards. Rejected: changes
  existing non-auto behaviour. Chosen: such a card is simply out of auto scope
  and waits for review like the card it replaced.

## Decision

**AA-D27 (supersedes the narrower part of AA-D9).**

- The SDK refuses to create an `aim:doc` card whose `review` differs from the
  live block. `propose_page_setup` already copies unknown keys from the live
  block, so honest callers never hit this.
- When a 0.6 tool accepts any `aim:doc` card, it keeps the live `review`. If
  the payload's `review` differs, the resolution records `applied` with the
  live value (the existing accept-with-tweaks record). Such a card is out of
  auto scope anyway, so "`applied` is never written under auto" still holds.
- One helper, `_sync_pending_doc_cards_review()`, amends pending `aim:doc`
  cards in place (an unrecorded payload amendment, allowed by §5.4) after
  every live `review` change: `set_review_policy`, and undo/redo of an
  `aim:doc` event. This protects 0.5 acceptors, which do not know the rule.
- Spec §5.6: "`review` is changed only by a direct edit. Resolving a proposal
  never changes it." No new lint code: a stale card is harmless to 0.6 tools.

**AA-D28.**

- A card whose creation superseded a human-authored card is out of auto
  scope. It stays pending with the deferred reason "replaces a suggestion from
  a person". The rest of the batch is judged as usual; if a later card depends
  on the excluded one (`depends_on` or chained `after`), the dry run refuses
  and the whole batch stays pending (the all-or-nothing rule in
  [auto-accept-sdk-batch-close](2026-10-01_2121_decision_auto-accept-sdk-batch-close.md)).
- Spec §5.6 gets one sentence.

**Kept from AA-D9:** switching the policy on does not sweep cards already
pending; a policy change is never auto-accepted (now moot, since no proposal
can carry one).

## Consequences

- Tests: an `aim:doc` card with a differing `review` is refused at creation;
  accepting a stale card keeps the live `review` and writes `applied`; undoing
  the policy event re-syncs pending `aim:doc` cards; an agent card superseding
  a human card stays pending; `set_page_setup` preserves `review`.
