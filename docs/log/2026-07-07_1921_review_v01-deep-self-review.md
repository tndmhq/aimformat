---
date: 2026-07-07 19:21
type: review
status: active
related:
  - 2026-07-07_1659_report_v01-shipped.md
---

# Review: v0.1 deep self-review (post-ship)

Adversarial review of the freshly shipped v0.1 toolkit, at the maintainer's
request: my own targeted pass over the modules I distrusted most, plus three
independent review agents (document/events state machine; linter + canonical
form + fixtures; ingest/export/CLI/docs), each required to *reproduce* every
finding before reporting it. All fixes land with regression tests in
`tests/test_review_regressions.py`.

## Confirmed findings and fixes

### F1 — critical: table-row delete/undo corrupted documents across shells

Deleting the first `tbody` row of a table with a `thead` recorded anchor
`{container, after: null}`; undo then reinserted the row **into the
`thead`** (first position = before the first row chunk, wherever it sits).
`verify()` flagged the resulting chain break — the invariant machinery
caught its own SDK. *Fix*: anchors for rows in table containers now carry
`shell` (`thead|tbody|tfoot`); `_anchor_of` records it, `DocState.insert`
honors it, spec §6.4 specifies it. Round-trips and `state_at` now
byte-exact across shells.

### F2 — major: `redo()` broken for stacked undos

`undo, undo, redo, redo` raised "nothing to redo" on the second redo — the
zone walk decremented a counter and bailed instead of treating redos as
stack pops. *Fix*: rewrote `redo()` (each redo cancels the nearest earlier
undo; the first uncancelled undo is the target) and documented
`_undo_candidate`'s negative-dip invariant. Full-cycle and interleaved
sequences are now pinned by tests.

### F3 — major: theme payloads bypassed validation on two paths

`accept(pid, applied=…)` on an `aim:theme` proposal took the applied markup
verbatim, and `propose_modify("aim:theme", raw_markup)` skipped grammar
checks — a hostile `body{background:…}` rule could enter the *versioned
theme block* through accept-with-tweaks even though lint would flag it
afterwards. *Fix*: `_validated_theme_markup()` (single
`<style data-aim-theme>`, one `:root` rule, registered slots only) now
guards both paths.

### F4 — design smell: `superseded_by` was patched by string replacement

`_fix_superseded_by` rewrote `"(new)"` placeholders in the raw history
after the fact — fragile by construction. *Fix*: proposal ids are allocated
before superseding, so the resolution event is written correctly the first
time; the helper is gone. A test asserts no placeholder can ever appear.

### F5 — minor: supersede + new proposal split across two batches

The superseded-resolution event and the proposal that triggered it got
different auto-batches despite being one editing intention. *Fix*: the
propose operations wrap both in one batch.

### F6 — trivial: dead loop in `lint.py` (`for … : pass`). Removed.

<!-- AGENT FINDINGS PENDING -->

## Overall opinion

<!-- PENDING -->
