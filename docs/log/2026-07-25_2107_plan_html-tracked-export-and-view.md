---
date: 2026-07-25 21:07
type: plan
status: todo
related:
  - 2026-07-25_2056_plan_browser-view-and-css-versioning.md
  - 2026-07-25_1953_report_os-filetype-registration.md
---

# Render proposals as a reviewable HTML export, and wrap it as aim view

A cheaper answer to "a reader cannot see what a proposal actually says" than
anything in the earlier plan. That entry's steps 1–3 (stylesheet versioning,
card readability, editor pointer) still stand; **this supersedes its step 4**,
which proposed rendering payloads inside the `.aim` itself.

## The gap in the exporters

The two exporters are asymmetric, and nobody noticed until a reader opened a
file cold:

| target | pending fates |
|---|---|
| DOCX | `tracked` (default), `accept-all`, `reject-all` |
| HTML | `keep` (default), `accept-all`, `reject-all` |

`tracked` turns each proposal into real Word revision markup, so a
counterparty sees a reviewable redline. HTML has no equivalent: `keep`
retains the proposals appendix, whose payloads sit in `<template>` and
therefore never render, and the other two resolve the proposals away. So the
one format that opens everywhere is the one that cannot show a proposal.

The fix is the missing fate, not a new subsystem: `--pending tracked` for
HTML, emitting `<del>`/`<ins>` where DOCX emits `w:ins`/`w:del`.

## Why a static artifact, not a local server

Spec §10 names "a local helper that serves the file with `Content-Type:
text/html`" as one bridge for local rendering. A server is the wrong shape
here:

- **Reachability.** A server inside a container or sandbox is invisible to
  the user's browser. Observed on 2026-07-25: an agent asked to show a
  document could not, and fell back to screenshotting a headless browser. A
  file can be handed over through whatever channel exists.
- **Shareability.** The rendered review is itself an artifact — mail it,
  attach it to a PR, archive it. A localhost URL is worth nothing to anyone
  else.
- **Nothing to manage.** No port, no process lifetime, no listening socket
  to scope or secure.
- **Testability.** Deterministic output takes golden tests like every other
  exporter.

What is given up: live reload and in-page accept/reject. Both are covered —
accepting is `aim accept`, and interactive review is what an editor is for.

## Scope

1. `_html_out.py` gains `pending="tracked"`: per proposal, wrap the current
   chunk in `<del>` and emit the payload in `<ins>`; `add` inserts at the
   anchor, `delete` wraps in `<del>`, `move` marks both ends. Carry author,
   model, timestamp, and explanation as attributes. Decide what a `theme`
   proposal renders as (swatches, or a stated note).
2. Granularity mirrors DOCX: **whole-block delete plus whole-block insert**,
   not a word-level diff. There is no diff algorithm in this repo today and
   none is needed here — block-level strike-and-insert is what a replaced
   sentence looks like in Word already. Word-level marks belong in an
   editing surface, and diffing HTML without breaking markup is a genuinely
   fiddly problem better not taken on for a preview.
3. CLI: accept `tracked` for html, keep the per-format default table honest
   in `--help`.
4. `aim view <file>`: export with `pending="tracked"` to a temp path and
   `webbrowser.open()` it. Stdlib, roughly ten lines, and it gives an agent
   a one-command way to show a human the real document.
5. Golden tests for each action kind; docs for the new fate.

## Useful freedom

The export is a derived artifact, **not** a `.aim`, so it is outside the
byte-locked stylesheet: the review view can carry whatever CSS it needs, with
no version bump and no regeneration of existing documents. That is what makes
this cheap where the in-file approaches are not.

## Estimate

Half a day to a day including tests and one review round; a few hours if an
agent does the typing. `aim view` is a rounding error on top.

## Consequences if this lands

- The `.aim.html` alias and OS file-type registration stop mattering for
  "let me look at this": `aim view` covers it.
- Steps 2–3 of the earlier plan get less urgent (the in-file lane only has
  to be honest and point somewhere); step 1 still stands on its own merits.
- The demo no longer needs to explain why the proposed text is missing.
