---
date: 2026-07-10 16:46
type: decision
status: done
related: []
---

# Decision: `amend_proposal` — in-place amend of a pending proposal

**What:** `AimDocument.amend_proposal(pid, markup=None, *, explanation=None,
at=None)` replaces a pending proposal's payload and/or explanation **in
place**, preserving its id, action, target, anchor, author, batch, and
dependencies. Shipped in 0.2.1 with tests (`tests/test_proposals.py::TestAmend`).

**Why:** the tndm editor's agent loop needs follow-up turns to be able to
change content the user hasn't accepted yet ("make the section you just
added shorter") without churning proposal ids or spamming the history with
supersede events. Decided in the editor's agentic-architecture plan
(tndm `docs/log/2026-07-10_1338_plan_agentic-architecture-v1.md`, amendment
A15); logged here because proposal semantics are format-owned.

**Design points:**

- **No spec change needed.** Spec §5.4 already sanctions it: *"Editing a
  pending payload in place is allowed and unrecorded; provenance is
  preserved at resolution via `proposed` vs `applied` (§6.2)."* The method
  appends **no history event** — the amended payload simply becomes the
  card's payload, and the eventual resolution event records it as
  `proposed`.
- **Validation = the original propose path.** modify payloads re-validate
  against the live target (`_normalize_payload(expect_id=target)`, incl.
  the `aim:theme` / `aim:doc` special payloads); add payloads keep the
  proposed root id and marker kind (`_payload_like`, the accept-with-tweaks
  helper), so chained adds anchored on the proposal stay stable.
- **Scope guardrails:** delete/move proposals carry no payload — only their
  explanation is amendable; re-anchoring or re-targeting is deliberately
  NOT an amend (that is a reject + new propose, or the existing supersede
  path). `explanation=""` clears the explanation; `data-at` changes only
  when `at` is passed (an amend is not a new proposal).
- **Contrast with supersede:** `propose_modify` on an already-pending
  target logs a `superseded` resolution and mints a new card (new id);
  `amend_proposal` is the id-stable, log-silent alternative for iterating
  on one intention.

**Surface scope:** SDK-only for now. The MCP server (`aim mcp`) and CLI do
not expose amend yet — follow-up when an external-agent use case shows up.
