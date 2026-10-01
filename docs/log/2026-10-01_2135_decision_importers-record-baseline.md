---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_history-baseline-event.md, 2026-10-01_2135_decision_toc-on-import-and-freshness.md, 2026-10-01_2135_decision_import-versioning-and-sequencing.md, 2026-10-01_2135_decision_pack-imported-images-deferred.md]
---

# Decision IMPORT-D10: importers record one baseline instead of N `add` events

**Status: proposed — awaiting founder approval.** Implemented, uncommitted, on `wt/aim-import-tracked`; deviations are listed in the [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D10**. Related decisions: [IMPORT-D9](2026-10-01_2135_decision_history-baseline-event.md), [IMPORT-D13](2026-10-01_2135_decision_toc-on-import-and-freshness.md), [IMPORT-D14](2026-10-01_2135_decision_import-versioning-and-sequencing.md), [IMPORT-D15](2026-10-01_2135_decision_pack-imported-images-deferred.md).

## Context

See IMPORT-D9 for the history-shape problem. Fixture sizes (o200k tokens),
baseline alone, TOC excluded:

| Fixture (chunks, events) | Before | After | Change | Note |
|---|---|---|---|---|
| legal-addendum (81, 82) | 28,147 | 21,103 | −25.0% | |
| long-report (775, 379) | 130,424 | 98,025 | −24.8% | |
| multi-column (37, 31) | 13,044 | 10,420 | −20.1% | |
| sample3 (42, 27) | 35,584 | 33,320 | −6.4% | inline images dominate (IMPORT-D15) |
| tables-merged (56, 19) | 18,193 | 16,595 | −8.8% | tables are already one event each |

After baselining, history is 1.01–1.07× the body. `aim_read` omits history by
default (`mcp.py`), so the projected read an agent pays is unchanged; the case
rests on history semantics first (undo, reconcile, save-path replay) and raw size
second.

## Options considered

- Keep N `add` events (status quo) — see IMPORT-D9 option A.
- Flatten after import — the smallest file but loses reconcile adoption (option B).
- **One baseline per import** — chosen; follows §6.9's SHOULD.

## Decision

- Scope: `from_docx`, `from_markdown`, `from_text`, `from_docling` / `from_pdf`.
  The page-setup and page-break events the DOCX importer writes today also
  collapse into the snapshot.
- Event fields: `label: "import"`; `author`: the importer's actor, as today;
  `explanation: "Imported from '<base name>'"` (base name only, never a directory
  path); optional `source: ["sha256:<input bytes>"]` — provenance for a later
  re-import alignment. It carries no content, but it lets someone holding the
  original file confirm the match, so writers may omit it.
- Pending proposals written by the DOCX tracked-change import (IMPORT-D1) are
  unaffected: they live in the lane, not in the snapshot.

## Consequences

- `undo()` after an import has nothing to undo.
- Save-path history replay starts from 1 event for a fresh import.
- Import provenance moves to one event.
- Imported documents declare 0.6.
- Tests that pin "ingestion is history" are rewritten:
  `test_ingest_export.py::test_ingestion_is_recorded_history`,
  `test_convert.py::test_import_is_history`, and `test_docx_import.py` around
  line 141; the `ingest.py` design note is replaced. (`ingest.py` also uses
  "baseline" for vertical alignment, `script: "baseline"`; rename the local
  variable if it causes confusion.)
- **tndm editor:** its DOCX upload conversion produces the new shape after its pin
  bump; undo right after an upload must return the same "nothing to undo"
  response its `InvalidOperation` path already gives for empty histories. Its
  test goldens built from imports regenerate.

## Compatibility and migration

Default output changes; produced documents are v0.6 (IMPORT-D14). Existing files
are not touched; nothing is migrated. Users who want the old shape can still
build documents event by event with the SDK.

## Tests

Structural, not a ratio: each importer's output has exactly one history line and
its snapshot equals the reduced projection. A benchmark script records the
before/after table separately.

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
