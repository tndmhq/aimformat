---
date: 2026-10-01 21:21
type: decision
status: proposed — awaiting maintainer approval
related: [2026-10-01_2121_decision_auto-accept-policy-in-aim-doc.md, 2026-10-01_2121_decision_auto-accept-resolution-marker.md, 2026-10-01_2121_decision_auto-accept-sdk-batch-close.md, 2026-10-01_2121_decision_auto-accept-guards.md, 2026-10-01_2121_decision_batch-undo-and-version-events.md, 2026-10-01_2121_decision_auto-accept-agent-surfaces.md, spec.md]
---

# Decision: review policy and `auto` resolutions require spec 0.6; newer-version fields become warnings

**Decision ids:** AA-D5, AA-D26.
**Status:** proposed, awaiting maintainer approval (draft PR, not merged).

## Context

Two new constructs: `review` in the `aim:doc` settings block
([auto-accept-policy-in-aim-doc](2026-10-01_2121_decision_auto-accept-policy-in-aim-doc.md)) and `auto` on resolution events
([auto-accept-resolution-marker](2026-10-01_2121_decision_auto-accept-resolution-marker.md)). Spec §3.7 already has the
mechanism for gated constructs: a document that uses one must declare the
version that introduced it, and the `aim:version` upgrade event goes in the
same batch as the first edit that needs it (S032/S033 for paint and
typography).

What a tool pinned at 0.5.x does with these files today, measured against
the code (not just the spec's intent):

- `Event.validate` reports any unknown non-`x_` field as H003, so `lint`
  reports an **error**, `reconcile` raises `HistoryError`, and
  `diff_documents` treats the whole history suffix as untrustworthy drift.
- An unknown key in the settings block is tolerated by parsers, so a 0.5
  tool would silently **ignore** the policy (and not honour it).

## Options considered

1. **No floor for `review`** (settings tolerate unknown keys). Rejected: a 0.5
   tool would ignore the policy without any signal.
2. **No floor for `auto`.** Not possible: 0.5 tools hard-fail on it (above).
3. **Floor both at 0.6 and keep strict unknown-field validation.** Rejected
   alone: every future field would repeat the hard failure.
4. **Floor both at 0.6, and make validation version-aware from 0.6 on.**
   Chosen.

## Decision

**AA-D5, the floor.**

- `review` in the live settings block, or in any retained `aim:doc` payload
  (`before`/`after`/`proposed`/`applied` of an `aim:doc` event, or a pending
  `aim:doc` card's template), requires 0.6.
- `auto` on any retained resolution requires 0.6.
- Declaring 0.6 makes a 0.5 tool warn S002 ("targets a version this tool does
  not implement"), the honest signal that it may not honour the policy. It
  also means the first policy acceptance never needs its own upgrade.
- New lint codes: **S035** (error: review policy or auto-accepted resolution
  requires spec 0.6 or newer, but the document declares X) and **D007**
  (error: review policy is malformed: `agents` must be a registered value and
  `by` a human actor). Marker misuse stays H003.
- SDK: `_payload_floors` learns `aim-doc+json` payloads carrying `review`;
  `_retained_floors` adds the live settings block and any event with `auto`;
  `_ensure_feature_version` is generalized into
  `_ensure_version_floor(floor, label, *, author, at)`.
- The upgrade event shares the batch with the first edit that needs it, and
  its author is **the author of the event that needs it** (for
  `set_review_policy`, its `author`; for an auto resolution, its
  `decided_by`). A human-authored upgrade event that no human made is never
  written.
- Reverse direction needs no code: `_apply_data` on `aim:version` already
  refuses to drop below retained floors, so time travel and undo cannot
  downgrade a document whose history holds a 0.6 construct.
- The spec header bump to 0.6 is shared with any other pending 0.6 work: if
  0.6 already exists when this lands, these constructs join that era;
  otherwise this change does the bump. Exactly one named lint code per floor
  value (if another 0.6 construct shares the floor, S035 is renamed to the
  shared 0.6 gate at rebase time).

**AA-D26, version-aware validation (in this change, not deferred).**

- When a document declares a version newer than the tool implements (the
  S002 condition), unknown non-`x_` event fields and unknown settings keys
  are reported as **warnings** ("field from a newer spec, unchecked"), not
  H003 errors. `reconcile` and `diff` apply the same leniency to unknown
  fields only; a missing required field still fails.
- This cannot help already-deployed 0.5.x tools. It makes the 0.6 to 0.7
  step clean and resolves, for the newer-document case, the contradiction
  between H003-on-unknown-fields and the spec preamble's "parsers MUST ignore
  unknown JSON fields". The older-document case stays a backlog item.
- MCP server instructions gain: "Never edit, prune or flatten history to
  clear lint errors. S002 means this tool is older than the document; tell
  the user to upgrade aimformat." (An agent with a cached 0.5.x `uvx
  aimformat` would otherwise see `lint_errors > 0` on every write and be
  invited to "repair" history.)

## Consequences

- **Pinned 0.5.x tools:** a document with the policy on, or with any
  auto-accepted resolution, lints with H003 errors, cannot be reconciled
  (`HistoryError`) and diffs as drift under 0.5.x. Such documents need
  aimformat 0.6 or newer. The CHANGELOG, the skill and the README say so,
  with the upgrade command (`uvx aimformat@latest` / `pip install -U
  aimformat`). Documents that never use the feature are unaffected and stay
  at their declared version.
- Release ordering: dependants must pin a released 0.6 tag before they write
  these constructs, so the package an agent installs and the one a host runs
  agree.
- Spec §3.7 names S035 beside S032/S033 and states that the floor rule covers
  JSON constructs in the settings block and history, not only markup.
  Appendix A.7 regenerated.
- Fixtures: invalid S035 (0.5 declared with `review`; 0.5 declared with
  `auto`), D007 (`agents: "sometimes"`, `by.type: "agent"`); a 0.6 tool
  linting a 0.7-declared document with an unknown resolution field gives a
  warning, not H003.
