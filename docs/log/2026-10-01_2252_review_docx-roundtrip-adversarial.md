---
date: 2026-10-01 22:52
type: review
status: done
related:
  - 2026-10-01_2210_report_docx-roundtrip-implementation.md
  - 2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md
  - 2026-10-01_2252_decision_docx-roundtrip-edits-refuse-propose-conflicts.md
  - 2026-10-01_2252_decision_docx-roundtrip-already-applied-changes.md
---

# Review: DOCX round trip (G3), adversarial pass

Scope: the whole `wt/aim-docx-roundtrip` diff against `main` (export marks,
manifest, `revision_import.py`, CLI/MCP surface, docs, decision entries).
Every finding below was reproduced with a script first, fixed, and pinned by
a regression test in `tests/test_docx_roundtrip.py` that fails without the
fix.

## Fixed

1. **[major] Cards decided after the export came back as the colleague's.**
   The reconstructed export (X) took the pending lane from the *current*
   document, so a card pending at export but accepted/rejected since was
   missing from the null round trip while the returned file still showed it.
   Its tracked change was then "accepted" as a foreign revision and
   re-proposed under the colleague's name (a rejected agent edit returned;
   an accepted add was duplicated; an accepted move was reverted). Fix:
   `_restore_resolved_cards` rebuilds manifest cards from their resolution
   events onto X; unrebuildable ones warn. Test:
   `test_a_card_resolved_after_the_export_is_not_reproposed` (10 cases over
   the three export modes).
2. **[major] A move made in the document after the export was undone.** The
   order walk ran its LIS against the current order, so an untouched file
   proposed (edits mode: applied) moving the block back. Fix: order is
   compared with the exported order (three-way); a unit already after the
   same predecessor is skipped. Test:
   `test_a_move_made_after_the_export_is_not_undone`.
3. **[major] `changes="edits", conflicts="propose"` overwrote local edits.**
   A conflict was reported and applied directly anyway. Now refused (SDK
   `InvalidOperation`, CLI exit 2). Decision entry
   `…_decision_docx-roundtrip-edits-refuse-propose-conflicts.md`. Test:
   `test_edits_cannot_be_proposed_over_a_conflict`.
4. **[major] A new first body row landed in `<thead>`.** The export draws
   every row in one table, so a row inserted before the first body row was
   anchored "after the last header row". Fix: a row's section comes from the
   document's rows around it; at a section boundary the add/move anchors at
   the start of that section (`after=None` + shell; proposals mode refuses a
   new *first* thead/tfoot row as a conflict, since `propose_add` takes no
   shell). Test: `test_a_new_first_body_row_stays_out_of_the_header`.
5. **[minor] Re-import after accepting was not a no-op.** Modifies became
   "changed since export" conflicts and additions were proposed again. Fix:
   a change the document already contains is counted unchanged. Decision
   entry `…_decision_docx-roundtrip-already-applied-changes.md`; docs now say
   a *rejected* change is proposed again. Test:
   `test_reimport_after_accepting_writes_nothing`.
6. **[minor] Unreadable input crashed the CLI with a traceback**
   (`BadZipFile`, zip-guard `ValueError`, missing `word/document.xml`,
   malformed XML). Now `ParseError("not a readable .docx file: …")`, the
   same error `from_docx` raises. Test: `test_unreadable_files_fail_cleanly`.
7. **[minor] `aim import --onto … -o OTHER` overwrote an existing file
   without `--force`**, unlike plain `aim import`. Test:
   `test_cli_onto_output_needs_force_to_overwrite`.
8. **[minor] `dcterms:modified` offsets were read as UTC**
   (`…T23:30:00+02:00` became 23:30Z). Test:
   `test_edit_time_honours_the_file_offset`.
9. **[minor] Bookmark names could exceed Word's 40 characters** (a 32-char
   id with a 4-digit ordinal); such names now fall back to the hash form.
   Test: `test_long_names_fall_back_to_the_hash`.

Also: the drift check looked up manifest hashes linearly per unit (O(n²));
now a dict. The status wording of the 16 new decision/plan entries now
says "maintainer", the word the repo already uses.

## Checked, no defect

- Default exports (no marks) are byte-identical to `main` for every example
  (all parts compared).
- Manifest parsing of untrusted input (size cap, DTD refusal, type checks),
  author-name sanitising, zip guards on the raw package.
- Null round trip remains zero changes for every example × mode and every
  DOCX fixture, in both proposals and edits mode.

## Not fixed (recorded)

- An *added* paragraph or row carries the importer's own markup (e.g.
  `style="width:340px"` on new table cells): only modifies are rebased onto
  base markup.
- A row moved between `<thead>` and `<tbody>` without changing order is not
  detected; a new first row of `<thead>`/`<tfoot>` is a conflict in proposals
  mode (an SDK `propose_add(shell=)` would lift this — a surface decision).
- `--title`/`--lang` are accepted and ignored with `--onto`.
- Everything already listed under "What is left" in the implementation
  report (Word check, Word-saved fixtures, per-author Track Changes, the
  per-card validation cost on large imports).
