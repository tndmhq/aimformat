---
date: 2026-11-07 17:41
type: decision
status: active
related: [2026-11-06_1123_plan_pr40-final-batch.md, 2026-11-05_1816_plan_pr40-rework.md]
---

# The maintainer's answers on every open #40 choice

The maintainer went through every open #40 choice on 2026-11-06. This entry records the
answers, so the earlier entries' "proposed" banners can be read against it (log entries are
not rewritten; their `status` flips instead).

## Approved as built

- IMPORT-D1 to D8: Word tracked changes become pending proposals on the original body, one
  per changed chunk; named Word moves become moves; expressible formatting becomes a
  modify, the rest is reported; Word authors are kept; Word comments are reported, not
  stored, until the format has comments; `import_docx()` and its report; an import that
  cannot verify itself fails loudly.
- IMPORT-D9 to D11: the `baseline` history event, one per import, and `baseline()` in the
  SDK and CLI, never over MCP.
- IMPORT-D13's carry-overs (`toc_doc_hash`, refresh on save, M005, the optional summary)
  and IMPORT-D13b's details P1 to P4, P6 to P8 and P19: the field names `chunk_count`,
  `id`, `level`, `title`, `word_count`; sections include their subsections; the counting
  rules; a table of contents on every save of a 0.6+ document with a heading or slide.
- IMPORT-D14b: one 0.6 release. No 0.5.x release is planned for the TOC-only meta block a
  0.5 tool rejects (M004): users of 0.5 tools upgrade.
- IMPORT-D15b's details P9, P11 to P15 (S036 and S037 stay warnings); point 3 (which
  formats are packed) is replaced by IMPORT-D15c below.
- IMPORT-D16: reconcile adopts a history-less file as a baseline at 0.6+ only.
- The rework plan's P16 to P18, P20 (the export of a chunk both moved and rewritten puts
  the old blocks' deleted text into the one moved paragraph) and P21 (reconcile refuses a
  baseline whose snapshot does not hash to its `doc_hash`).

## New decisions

- [IMPORT-D11b](2026-11-07_1741_decision_baseline-discard-history.md): a one-command repair
  for an unreadable history (`aim baseline FILE --discard-history`).
- [IMPORT-D12b](2026-11-07_1741_decision_flatten-keeps-baseline.md): `flatten()` leaves a
  baseline in 0.6+ documents. Supersedes IMPORT-D12.
- [IMPORT-D15c](2026-11-07_1741_decision_pack-bmp-webp-tiff.md): BMP, WebP and TIFF are
  sized and stored once.
- After #40, a 0.6 format change: `lang` and `dir` on `<html>` become a document setting
  under `aim:doc`, changed by an evented modify, as page setup is. Today they count toward
  `doc_hash` but no event records a change to them, so a hand edit of `lang` passes
  `verify()` on an imported file and fails H006 on a file with a checkpoint.

## Review findings

Codex round 3's two findings are fixed in this PR. Until the 0.6 format work is done,
later review findings in code older than the round under review are deferred to a tracked
list and fixed together afterwards; a regression the PR's own latest change introduces is
still fixed in the PR.

## Still open

The tracked export's layouts (P20 and the move-order fix) are checked in Word itself before
the merge; LibreOffice and the repo's resolver agree with them.
