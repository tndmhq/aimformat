---
date: 2026-10-01 21:32
type: decision
status: todo  # proposed — awaiting maintainer approval
related:
  - 2026-10-01_2132_decision_docx-roundtrip-identity-carrier.md
  - 2026-10-01_2132_decision_docx-roundtrip-marker-placement.md
  - 2026-10-01_2132_decision_docx-roundtrip-manifest.md
  - 2026-10-01_2132_decision_docx-roundtrip-marks-default-on.md
  - 2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md
  - 2026-10-01_2132_decision_docx-roundtrip-id-alignment.md
  - 2026-10-01_2132_decision_docx-roundtrip-conversion-noise.md
  - 2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md
  - 2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md
  - 2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md
  - 2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md
  - 2026-10-01_2132_decision_docx-roundtrip-informative-convention.md
  - 2026-10-01_2132_decision_docx-roundtrip-architecture.md
---

# Decision: ROUNDTRIP-D5 — entry point `AimDocument.import_revision()` and `aim import --onto`

**Status:** proposed — awaiting maintainer approval. Nothing is implemented yet. This entry is one of the
DOCX round-trip decision set (gap G3: chunk identity lost on
`.aim` → DOCX → edited in Word → DOCX → `.aim`). Sibling decisions:
[ROUNDTRIP-D1](2026-10-01_2132_decision_docx-roundtrip-identity-carrier.md), [ROUNDTRIP-D2](2026-10-01_2132_decision_docx-roundtrip-marker-placement.md), [ROUNDTRIP-D3](2026-10-01_2132_decision_docx-roundtrip-manifest.md), [ROUNDTRIP-D4](2026-10-01_2132_decision_docx-roundtrip-marks-default-on.md), [ROUNDTRIP-D6](2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md), [ROUNDTRIP-D7](2026-10-01_2132_decision_docx-roundtrip-id-alignment.md), [ROUNDTRIP-D8](2026-10-01_2132_decision_docx-roundtrip-conversion-noise.md), [ROUNDTRIP-D9](2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md), [ROUNDTRIP-D10a](2026-10-01_2132_decision_docx-roundtrip-colleague-track-changes.md), [ROUNDTRIP-D10b](2026-10-01_2132_decision_docx-roundtrip-word-resolutions.md), [ROUNDTRIP-D11](2026-10-01_2132_decision_docx-roundtrip-no-diff-resolve.md), [ROUNDTRIP-D12](2026-10-01_2132_decision_docx-roundtrip-informative-convention.md), [ROUNDTRIP-D13](2026-10-01_2132_decision_docx-roundtrip-architecture.md). Baseline under test: aimformat 0.5.2 (`79a006b`).

## Shared context (G3)

Measured on `tests/fixtures/docxs/legal-addendum.docx` (81 chunks) with
**no** edits in between: 0 of 81 chunk ids survive `to_docx` → `from_docx`,
and `diff_documents(base, returned)` reports 81 added + 81 deleted, so an
agent has to re-read the whole document (5,804 `o200k_base` tokens for an
`aim_read`-shaped projection) to learn what a colleague changed. Text
survives 81/81 positionally; markup survives 69/81 (bold dropped on three
`num-1` headings, eight `<mark>` highlight colours dropped, a three-image
figure collapsed to one image). Two defects: identity loss, and conversion
noise that is not a colleague edit.

A scratch prototype (outside the repo) measured: bookmarks survive a
LibreOffice 25.8 re-save 81/81; a null round trip aligns 77 by marker +
4 by content with 0 spurious ops (12 noise units suppressed); a synthetic
10-edit set aligns exactly. **Not yet measured:** any Microsoft Word
behaviour, point bookmarks, the custom-XML manifest, the three-way merge,
`tracked`/`reject-all` exports with a non-empty lane, idempotent re-import,
split/merge linking, the fuzzy budget. A manual Word protocol (open a marked
export, perform a fixed list of edits with and without Track Changes, after
Document Inspector, and through a Google Docs round trip; record marker and
manifest survival; commit the saved files as fixtures) gates the
convention-freezing decisions.

## Context

Importing a returned DOCX *onto* an existing document is a new operation: it
must mutate a document, return a structured report, and support a dry run.
The original task sketch suggested `from_docx(..., base=doc)`.

## Options considered

- `from_docx(path, base=doc)`: overloads a constructor, has nowhere to
  return a report.
- A free function: a second, parallel entry point.
- **A method mirroring `reconcile()`** (mutates in place, returns a report,
  supports `dry_run`): chosen.

## Decision

```python
@dataclass
class RevisionImportReport:
    changes: str                    # "proposals" | "edits"
    author: Actor
    base_match: str                 # "exact" | "advanced" | "unknown" | "mismatch"
    aligned_by_marker: int; aligned_by_content: int
    unchanged: int; noise_suppressed: int; rebased: int
    added: list[str]; deleted: list[str]; modified: list[str]; moved: list[str]
    proposals: list[str]            # created card ids (proposals mode)
    events: list[Event]             # appended events (edits mode)
    already_pending: list[str]      # idempotency skips
    superseded: list[str]
    resolved: list[tuple[str, str]] # phase C
    conflicts: list[Conflict]       # (unit id, reason, base text, colleague text)
    warnings: list[str]
    def summary(self) -> str: ...
    def to_obj(self) -> dict: ...

def import_revision(
    self, source: str | Path | bytes | BinaryIO, *,
    author: Actor | None = None,
    changes: Literal["proposals", "edits"] = "proposals",
    conflicts: Literal["report", "propose"] = "report",
    base_seq: int | None = None,
    at: str | None = None,
    dry_run: bool = False,
) -> RevisionImportReport: ...
```

- The source format is sniffed; only DOCX is accepted in this PR. The
  aligner is format-agnostic so Markdown can follow.
- The whole import is computed on a clone and written atomically. A single
  card refused by projected-lane validation becomes a `Conflict`; it does not
  abort the import.
- **Refusals:** the base does not `verify()` (chain errors — point to
  `aim reconcile`); document mismatch ([ROUNDTRIP-D7](2026-10-01_2132_decision_docx-roundtrip-id-alignment.md)); a repeated import in edits mode
  (same returned-file hash already in the retained log).
- **Idempotency:** before emitting, each candidate card is compared with the
  pending lane; an identical pending card (same action, target, anchor and
  payload, ids ignored) is skipped and listed in `already_pending`.

CLI:

```
aim import RETURNED.docx --onto BASE.aim [-o OUT] [--as proposals|edits]
    [--author human:NAME] [--conflicts report|propose] [--base-seq N] [--dry-run] [--format text|json]
```

`-o` defaults to BASE, written in place like `aim reconcile`. Exit 0 on
success (including reported conflicts), 1 on refusal, 2 on usage errors.

MCP: `aim_import_revision(path, docx_path, changes="proposals", dry_run=False)`
returns `report.to_obj()`; both paths go through the existing
`AIMFORMAT_MCP_ROOT` guard. If a separate `aim_import` MCP tool lands first
(gap G2), this becomes its `onto` parameter instead.

## Consequences

- New public API: `AimDocument.import_revision`, `RevisionImportReport`,
  `Conflict`, CLI `import --onto …`, MCP `aim_import_revision`. The report
  shape becomes a JSON contract (`--format json`, MCP).
- `from_docx` and `aim import` without `--onto` are unchanged.
- The tndm editor may adopt the method for "upload a returned DOCX onto this
  document"; nothing it calls today changes.

## Compatibility and migration

Purely additive. Thresholds and internal helpers are not public API.
