---
date: 2026-07-25 20:56
type: plan
status: todo
related:
  - 2026-07-25_1953_report_os-filetype-registration.md
---

# Make the browser view of pending changes useful, and make stylesheet changes cheap

Two problems found the same evening, one blocking the other. Written up as a
follow-up; nothing implemented yet.

## What a browser-only reader sees today

Open a `.aim` carrying pending proposals in a browser: the body renders as
the clean accepted document, then a "Pending changes" appendix shows one card
per proposal, like

```
MODIFY ·F7M8CSKV —AGENT ·2026-07-25T19:40:53Z
Tightens payment terms and caps unapproved expenses, per the client call on 24 July.
```

Two things are wrong with that. The card is log output shown to a human: an
opaque chunk id, an ISO timestamp, and `AGENT` in caps, while
`data-author-model` already holds `claude-fable-5` and goes unrendered. And
the proposed wording is absent, because it sits in `<template>`, which CSS
cannot reach by design (§5.1: parser-safe, apply-safe, selector-safe). So a
recipient learns that three changes are proposed and why, but not what any of
them say, and is told nothing about what to do next.

Observed live the same evening: a coding agent handed the file cold, with no
context, identified the format, installed the tooling, and listed the
proposals correctly within seconds. The agent path works. It is the unaided
human opening the file who is left without a next step.

## Why this is not a quick CSS fix (the blocker)

[`css.py`](../../src/aimformat/css.py) exposes one `generate_aim_css()` with
no version parameter, and [`lint.py`](../../src/aimformat/lint.py) requires
every embedded `data-aim-css` block to **byte-equal** that single generated
stylesheet, "whatever version it claims". The byte-lock is deliberate and
worth keeping (review AIM-02): without it a document could ship arbitrary CSS
and render a lie — hiding the pending lane, restyling proposed text to read
as accepted. What a `.aim` shows should be what the format says it means.

The cost of the current shape is that the stylesheet is effectively frozen.
Change one character and **every** `.aim` file in existence fails lint until
regenerated through `loads().dumps()`: conformance fixtures, examples, parity
goldens, the editor's e2e fixtures, and any file already sent to someone.
Not hypothetical — that is the X006 failure of 2026-07-17, where a pin bump
made stale-css e2e fixtures reject on upload.

So the rendering cannot improve without a coordinated cross-repo
regeneration, which makes every future rendering change expensive enough that
it does not happen. That is the real defect, and it should be fixed first.

## Step 1 — version the stylesheet (do this first)

Keep the security property, drop the coupling.

- Freeze each released stylesheet and look it up by version:
  `generate_aim_css(version)` returns the exact bytes for that version, with
  prior versions retained verbatim as data. Lint then compares a file's
  embedded CSS against the stylesheet for **the version that file claims**,
  not against the newest one. Old files stay valid; a new stylesheet
  invalidates nothing.
- Give the stylesheet its own version line, independent of the spec minor.
  A rendering improvement should not require a spec revision, and a spec
  revision should not force a re-render.
- Provide the upgrade path explicitly: `aim normalize` (or an equivalent
  verb) rewrites a document to the current stylesheet. Cheap, because
  aim.css is excluded from `doc_hash` (§10), so restyling does not disturb
  history verification.
- Decide what a file claiming an **unknown future** stylesheet version does.
  Erroring on newer-than-implemented stays right; the message should say
  "regenerate, or upgrade the tooling", not imply the file is malformed.

Once this lands, everything below is an ordinary change instead of a
migration.

## Step 2 — make the card readable (stylesheet only)

No spec change, no new attributes; the data is already there.

- Render `data-author-model` (`claude-fable-5`), not just `AGENT`.
- Flip the visual hierarchy: the explanation is the useful sentence and
  should be the prominent text, with action, target id, and timestamp as
  small secondary metadata. Today the machine line is uppercase monospace
  and visually dominant.
- Consider `<details>`/`<summary>` so a long lane collapses to one-line
  summaries, expandable without script.

## Step 3 — tell the reader what to do next

Add a line to the pending lane: these changes are not applied; this view
shows what each targets and why; to see the proposed wording and accept or
reject, open the file in an AIM editor — `aimformat.com/editors`.

Two constraints on the wording. Name no vendor: the editors page is the
neutral pointer, and the file's own `aim-note` already uses it. And
CSS-generated content cannot be a link, so the URL renders as plain text a
reader must copy; if that proves too weak, promoting it to a real anchor in
the lane is a structural change and belongs with step 4.

## Step 4 (deferred, larger) — show the proposed text itself

Only real DOM can be rendered. CSS cannot see into `<template>`, and
`attr()` yields plain strings, so an attribute-based preview cannot carry
markup or per-word marks. Options considered:

- A rendered preview element beside the template, ids stripped, treated as a
  derived cache like the summary and TOC — rebuildable, lint-checkable for
  drift. The template stays normative.
- Dropping `<template>` and storing the payload as live, id-stripped DOM.
  Viable: `data-for` already names the target, so the applier can stamp the
  id at accept time; chunk vocabulary plus X004 already exclude
  `<style>`/`<script>` payloads; only table fragments need a minimal shell.
  But it converts three structural guarantees into linter-enforced ones,
  which is a downgrade in kind.

Design notes for whoever picks this up. **Render, do not diff:** a proposal
that only changes paint ("make this title pink", §3.3) has no textual delta,
so a rendered preview shows it correctly where a word diff shows nothing.
Word-level `<del>`/`<ins>` then applies as a second layer where wording also
changed, and carries real semantics for screen readers. Theme proposals are
not element payloads at all and want swatches. Slide payloads cannot render
at full size inside a card and need a scaled thumbnail or a described
fallback — that case should decide how much machinery this is worth.

## Sequencing

Step 1, then steps 2 and 3 together in one stylesheet revision. Step 4 is a
spec discussion, not a follow-on. None of this is urgent enough to precede
the work currently in flight.
