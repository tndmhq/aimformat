---
date: 2026-10-01 21:21
type: decision
status: proposed — awaiting maintainer approval
related: [2026-10-01_2121_decision_auto-accept-resolution-marker.md, 2026-10-01_2121_decision_auto-accept-version-floor.md, 2026-10-01_2121_decision_auto-accept-sdk-batch-close.md, 2026-10-01_2121_decision_auto-accept-guards.md, 2026-10-01_2121_decision_batch-undo-and-version-events.md, 2026-10-01_2121_decision_auto-accept-agent-surfaces.md, spec.md]
---

# Decision: auto-accept review policy lives in the `aim:doc` settings block

**Decision ids:** AA-D1, AA-D2, AA-D3, AA-D13.
**Status:** proposed, awaiting maintainer approval. Nothing here is merged;
the implementation ships as a draft PR on branch `wt/auto-accept`.

## Context

Agents (MCP clients, CLI scripts, SDK users, editor-embedded assistants)
write every change as a pending proposal. A user who trusts the agent for a
given document has no way to say "apply your changes as they come, I will
undo what I don't like". The request has two halves: a standing setting that
travels with the document, and a one-off "accept this change" for a single
call (see [auto-accept-sdk-batch-close](2026-10-01_2121_decision_auto-accept-sdk-batch-close.md) entry). The standing setting must
be recorded (who switched it on, when, at whose request) and undoable.

## Options considered

1. **Tool-local setting** (editor preference, env var, MCP config). Rejected:
   every tool would need its own switch, a document opened elsewhere would
   behave differently, and nothing records who consented.
2. **A new header element or `<meta>`.** Rejected: header changes are not
   evented, so switching would not be in history or undoable.
3. **A field in the `aim:doc` settings block (spec §3.6).** Chosen: the block
   already holds document settings, modifies to it are evented
   `direct_edit`s with whole-block before/after, and undo/redo already work
   on it (page setup uses the same path).

## Decision

- Shape:

  ```json
  {"page": {...}, "review": {"agents": "auto", "by": {"type": "human", "id": "Ada"}}}
  ```

- **Absent means off.** There is no `"off"` value, so each state has exactly
  one spelling. Clearing the policy removes the `review` key.
- `review.agents` is the extension point. Only `"auto"` is registered now.
  `"required"` (agents' direct edits rerouted to proposals) is **reserved**,
  sketched in spec Appendix C, and not built. Any other value is lint D007.
- `review.by` is the human whose standing consent the policy records. It MUST
  have `type: "human"`; `id` is optional (an MCP agent may not know the
  user's name, `{"type":"human"}` reads as "the user"). It is stored in the
  block, not only in the event that wrote it, because history can be pruned
  or flattened and because the event's `author` may be the agent tool that
  switched it on at the user's request.
- Switching is an ordinary `aim:doc` modify (`direct_edit`). Its `author` is
  whoever wrote the change (the person in an editor, `external:aim-mcp`, or
  the agent model). It is never a human by default.
- Unknown keys inside `review` are ignored by parsers and preserved by tools.
- **Scope (AA-D3):** the policy covers proposals whose author type is
  `agent` or `external`. Proposals authored by humans always wait for review.
  No per-model allowlist in v1.
- The agent note (§2.5) never mentions the policy. Agents learn the state
  from `aim_read`, `aim show` or `AimDocument.review_policy`.

## Trust caveat (AA-D13, informative spec text in new §5.6)

A file cannot authenticate actors. `review.by` and every `decided_by` are
claims by the tool that wrote them, exactly as an agent can already call
`aim_resolve` on its own proposals today. The policy adds no write
capability; it adds a **record of human consent**. It binds well-behaved
tools and is not an access control (that belongs to the host: file
permissions, an editor's sharing roles). Auditors who want the subset a human
actually looked at filter out resolutions carrying `auto` (see
[auto-accept-resolution-marker](2026-10-01_2121_decision_auto-accept-resolution-marker.md)). The same caveat will apply to the
reserved `"required"` value.

Also informative in §5.6: the policy travels with the file. A tool that knows
its current user and finds a policy set by someone else SHOULD tell its user
before honouring it.

## Consequences

- Needs spec 0.6 (see [auto-accept-version-floor](2026-10-01_2121_decision_auto-accept-version-floor.md)).
- Spec: §3.6 gains `review`; new §5.6; Appendix C gains the `"required"`
  sketch; generated Appendix A.6 becomes "Document settings" (page + review).
- SDK: `src/aimformat/review.py` (`ReviewPolicy`), `AimDocument.review_policy`,
  `set_review_policy(agents, *, by, author, explanation=None, at=None)`.
- `set_page_setup` and every `aim:doc` write preserve `review` and unknown
  keys.

## Open question for the maintainer

- On a document several people edit, `decided_by` under the policy is
  `review.by` (the person who switched it on), even when someone else's agent
  triggered the acceptance. The alternative is the person who ran the agent.
  Built as `review.by`; flagged for review.
