---
date: 2026-10-01 22:37
type: report
status: active
related: [2026-10-01_2135_decision_import-tracked-changes-as-proposals.md, 2026-10-01_2135_decision_history-baseline-event.md, 2026-10-01_2135_decision_import-versioning-and-sequencing.md]
---

# Report: import fidelity implementation (IMPORT-D1 … IMPORT-D16)

Everything below is **uncommitted** on `wt/aim-import-tracked` and awaits
founder approval of the decisions; nothing merges before that. Both planned
PRs live in one working tree:

- **PR 1 — tracked changes (SDK 0.5.3, no format change):** IMPORT-D1 … D8.
  A G4-only copy of the tree (diff plus new files) was kept outside the repo
  before the spec work started, so the two PRs can be split cleanly.
- **PR 2 — spec and SDK 0.6:** IMPORT-D9 … D13, D16 (D15 deferred, D14 is
  the sequencing itself).

## What was built

Tracked changes (D1–D8):

- `convert/_docx_revisions.py` — revision detection, source-key stamping,
  reject/accept view resolution, revision records with move-range names,
  comments (+ `commentsExtended.xml`), revisions in parts the importer does
  not read. Auxiliary parts are parsed with entities, DTDs and network off,
  and a part declaring a DOCTYPE is refused.
- `_docx_in.py` provenance mode (`data-aim-src` on every block, item and row;
  stripped before anything is written) and `import_docx_source`.
- `convert/_docx_tracked.py` — alignment by key, the no-card-without-revision
  rule, the list-split rule, move pairing, replacement pairing, attribution,
  explanations, validation, the coarse fallback and the refusal.
- `AimDocument._propose_lane` — the batch-propose primitive (one projection;
  same id minting, card serialization and P-rules as `propose_*`).
- `ImportResult` / `ImportReport` / `RevisionNote` / `CommentNote` /
  `AimImportWarning`, `import_docx`, `tracked=` / `max_revisions=` on
  `from_docx` / `from_path`, `aim import --tracked`.
- `to_docx(pending="tracked")` exports pending paragraph moves as
  `w:moveFrom`/`w:moveTo` (named ranges), moved containers and non-text blocks
  as delete + insert, moved-and-modified as `moveTo(del(old)) + ins(new)`.

Baseline and TOC (D9–D13, D16): the `baseline` event (registry, `Event`
validation, `verify`, `state_at`, `prune`, reconcile origin, floors/S034),
`AimDocument.baseline()` + `aim baseline`, `flatten()` → one checkpoint,
`ingest.finish_import` used by every importer, `toc_doc_hash` + `dumps()`
refresh + `outline()` / `toc_is_fresh()`, MCP `aim_read` `toc_source`, lint
H007/H008/H009/M005 and the retargeted M004, spec v0.6 text (§3.7, §6.2–§6.9,
§7, §8.1, a CI-linted baseline example), regenerated Appendix A, TS registry,
conformance kit (+8 fixtures), examples and parity goldens.

## Measured (DOCX fixtures, o200k tokens, `scripts/measure_import_sizes.py`)

| fixture | history events | file bytes | file tokens | history tokens | TOC (meta) tokens |
|---|---|---|---|---|---|
| legal-addendum | 82 → 1 | 82,651 → 63,253 B | 28,153 → 22,102 (-21.5%) | 14,372 → 7,711 | 612 |
| long-report | 379 → 1 | 418,799 → 332,767 B | 130,278 → 103,180 (-20.8%) | 78,388 → 47,062 | 4,202 |
| multi-column | 31 → 1 | 41,698 → 35,417 B | 13,020 → 11,111 (-14.7%) | 4,535 → 2,228 | 395 |
| sample3 | 27 → 1 | 72,966 → 67,616 B | 35,590 → 33,934 (-4.7%) | 15,660 → 13,676 | 328 |
| tables-merged | 19 → 1 | 52,232 → 48,688 B | 18,180 → 17,127 (-5.8%) | 6,626 → 5,298 | 271 |

The TOC adds 269–4,208 tokens; without it the reduction is 2–3 points larger.
`aim_read` omits history by default, so the MCP read cost is unchanged.
Import of 800 chunks with 300 changed paragraphs: about 5 s (guard: 15 s).
Lint of the imported long-report: 0.08 s.

## Deviations from the design

1. `RevisionNote.cards` is a tuple of every card a revision feeds (a split
   paragraph's mark feeds the insertion and the shortened original), not one
   `card` id.
2. **List-split rule** (extends C8): a list split in the accept view by an
   inserted block is carried as item deletes plus a new container, citing the
   splitting block's revisions; without such a revision it stays converter
   noise. Without the rule the inserted paragraph would land after the whole
   list.
3. The moved-and-modified export shape is implemented but **not checked
   against Word or LibreOffice** (no oracle runs on this machine); no
   fallback switch was built. Item-level (list item / row) moves still export
   as unchanged content.
4. Added beyond the design: pending row adds and row deletes now carry the
   `w:trPr` row marker (Word's Reject All / Accept All left empty rows);
   `modify_chunk` returns its view without rebuilding every chunk view (it made
   bulk writers quadratic).
5. The shape fixtures are built in memory from `tests/tracked_docx_kit.py`;
   `scripts/gen_docx_tracked.py` writes them as files for the one-time manual
   check (binaries are not committed — python-docx timestamps them). The
   LibreOffice oracle job (`tracked-oracle`, non-gating) and its script were
   written but never run here. The Word-authored redline of
   `legal-addendum.docx` needs to be made by hand.
6. `flatten()` gained `label=` / `at=`; `outline()` and `toc_is_fresh()` are
   new public methods (the never-stale `aim_read` needed them); `to_html()`
   drops history through an internal helper instead of `flatten()`.
7. Lint computes H008 itself and calls `_verify(check_snapshot=False)`, so a
   tampered snapshot hash is one H008, not H008 + H006; `verify()` still
   reports it.
8. Importers drop their scaffolding `add` events before recording the
   baseline, so it is seq 1.
9. The package version is 0.6.0 in this tree (the spec test ties
   `__version__` to the spec version); CHANGELOG carries `0.6.0 — unreleased`
   and `0.5.3 — unreleased` sections.
10. `max_revisions` counts revision records, marks and property changes
    included.

## Gates (local, CI-equivalent)

ruff check + format check, mypy (30 files), full pytest on Python 3.12
(1,719 passed, 3 skipped for missing Playwright, 2 xfailed), the converters
job with Playwright Chromium (243 passed, 0 skipped), `aim lint
examples/*.aim`, TS prettier + typecheck + vitest (86 passed). Three mutation
checks (mark-join rule, move pairing, no-card-without-revision) each fail the
shape tests.
