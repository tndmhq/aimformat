---
date: 2026-07-16 12:59
type: report
status: done
related: []
---

# Report: round-2 review fixes — DOCX tracked-changes lane, Markdown slide adds, MCP scoping

A deep review round (2026-07-13, findings A-01 and A-11..A-16) found one
Critical, three Major, and two Moderate defects in this repo, all in the
exporter/MCP surface. All six were re-verified against `main` with executed
repros before fixing, all six confirmed, all six fixed on
`wt/agent-harness-fixes` (the tndm editor's branch of the same name carries
its side of the round).

## What changed

**`export_docx.py` — one pass over the tracked-changes emitters** (A-11,
A-12, A-13, A-14, A-15):

- A pending **add of a whole `ul`/`ol`/`table`** now exports as an inserted
  list (`_emit_added_list`, one `w:ins` paragraph per item) or a real table
  (`emit_table(force="ins")`) instead of flattening to empty paragraphs —
  the single most common agent-proposal shape was silently losing its
  structure in the default export mode.
- **`<br>` survives** inside tracked ins/del runs (`_make_run` gained the
  `break` branch, mirroring `_apply_runs`).
- A lint-clean table whose **`rowspan`/`colspan` overruns the grid** no
  longer crashes the export with IndexError: `ncols` now comes from a
  first-pass grid simulation that mirrors the emit cursor, and spans are
  clamped — surplus cells become real grid columns, never dropped.
- **Row-adds are emitted after the content loop**, each anchored on its own
  `w:tr` (`anchor_tr.addnext`), chains advancing to the just-inserted row —
  tracked order now equals accept-all order in every shape (was reversed
  for chains, corrupting for mid-table adds).
- A **run chunk** (consecutive same-id `li`s, or multi-row `tr` runs) with a
  pending modify emits its payload **exactly once** (`payload=` flag on
  `emit_tracked_chunk`, grouping in `emit_list`, `done_mods` in
  `emit_table`).

**`convert/_markdown_out.py`** (A-16): the `aim-slide` branch now emits
CriticMarkup for pending adds anchored inside a slide — before children for
container-start anchors, after each child keyed `chunk_id or container_id`
(so adds anchored after a nested list/table inside the slide render too),
mirroring `export_docx.emit_slide`.

**`mcp.py`** (A-01): every path-taking tool now states the local-trusted-
stdio model in its description, and an opt-in `AIMFORMAT_MCP_ROOT` env var
confines every path argument — including `aim_export`'s `out_path`, the
write primitive — to one directory tree (`Path.resolve()` +
`is_relative_to`, so symlink escapes are caught). Unset keeps today's
unscoped behavior byte-identical.

Also pinned in `test_lint.py`: X002 fires on `on*` handlers inside slide
children and figure content — the boundary guarantee editors lean on when
rendering file-derived markup.

## Verification

- Every fix carries regression tests written from the review's executed
  repros: 22 new tests in `test_ingest_export.py`,
  `test_review_regressions.py`, `test_convert.py`, `test_mcp.py`,
  `test_lint.py`. Suite: **646 passed** (was 627 + missing-dep skip);
  ruff/mypy clean.
- Tracked-vs-accept-all parity is asserted for list/table adds and row-add
  ordering — the invariant behind A-11/A-14.

## Left open (deliberate)

- Sibling adds: body-paragraph and list lanes emit proposal-order, table
  lane (and accept-all) last-proposed-closest — pre-existing cross-lane
  inconsistency, out of scope here.
- Table row-adds anchored on a run-chunk row id emit after the first member
  row (lists now group; tables kept the old anchor point).
- 0.2.1 stays unreleased; these fixes ride the same changelog section and
  the eventual PyPI release cuts on the post-merge SHA.
