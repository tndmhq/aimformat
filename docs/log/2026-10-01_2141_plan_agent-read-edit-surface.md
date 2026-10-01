---
date: 2026-10-01 21:41
type: plan
status: active
related:
  - 2026-10-01_2134_decision_agent-read-modes.md
  - 2026-10-01_2134_decision_agent-read-default-mode.md
  - 2026-10-01_2134_decision_agent-text-view.md
  - 2026-10-01_2134_decision_sdk-views-module.md
  - 2026-10-01_2134_decision_cli-show-modes-and-search.md
  - 2026-10-01_2134_decision_agent-search.md
  - 2026-10-01_2134_decision_agent-batch-ops.md
  - 2026-10-01_2134_decision_cli-edit-verb-and-batch.md
  - 2026-10-01_2134_decision_mcp-single-representation.md
  - 2026-10-01_2134_decision_mcp-lean-tool-surface.md
  - 2026-10-01_2134_decision_spec-partial-reads-informative.md
  - 2026-10-01_2134_decision_elision-stubs-round-trip.md
---

# Plan: the agent read and edit surface (MCP + CLI parity)

## Why

A token benchmark of agents working on `.aim` files showed four gaps in what
an agent pays per task:

- **G1** `aim_read` always returns every chunk's HTML (6.5k tokens on a
  81-chunk contract, 67.5k on a 775-chunk report); there is no outline,
  no partial read and no search.
- **G8** there is no cheap whole-document reading view: the HTML markup is
  most of the cost, and Markdown export drops the ids and clause numbers.
- **G7** N edits are N calls, N saves, N lints and N history batches, and an
  add cannot be followed by an edit anchored on it without another read.
- **G9** every MCP result travels twice (`structuredContent` plus an
  `indent=2` text block), and the fixed tool list costs about 1.8k tokens.

An adversarial review of the design added: elided data URIs are destroyed
when an agent edits an image chunk (the stub is accepted as a `src`), and
proposal batches are quadratic in their own size.

## Steps

1. **P1 (no behaviour change):** `AimDocument.chunks` and `chunk()` become
   O(n). Today `chunks` re-walks the tree per chunk and `chunk()` builds the
   whole list; `modify_chunk` returns `chunk()`, so a direct-edit batch is
   quadratic and a proposal batch worse. Pinned by an equivalence property
   test (old vs new on every example, parity and conformance fixture).
2. **`aimformat.views`** (READS-D3, D4, D6): units, numbering labels
   (simulated §3.8 counters, checked against the generated stylesheet),
   outline, reference resolution, the lossy text view, skeleton, exact chunk
   serializations and lexical search.
3. **MCP** (READS-D1, D2, D9, D10): `aim_read(mode=…)`, `aim_search`,
   results sent once as compact text, leaner tool list with a byte budget.
4. **`aimformat._ops`** (READS-D7, D12): shared op executor for MCP and CLI:
   validation, `$N` back-references, caps (100 edits / 25 proposals),
   all-or-nothing, elision stub restoration.
5. **CLI** (READS-D5, D8): `aim show --mode`, `aim search`, `aim edit`,
   `aim edit batch`, `aim propose batch`.
6. **Spec** informative text (READS-D11, no version bump), docs, skill,
   CHANGELOG 0.6.0 (Unreleased).

## Out of scope (deferred, see READS-D2/D6/D7)

`auto` read mode; embedding search; a batch-scoped proposal projection (the
route to a higher proposal cap); atomic `save()`; an `if_seq` write
precondition; `theme`/`set_theme` naming; `to_markdown` dropping outline
numbering; read modes in the TypeScript reader (a `labels.json` golden makes
a port checkable).

## Decisions for review

Twelve entries, READS-D1 … READS-D12, linked in `related:` above. P1 is an
internal speed fix with no behaviour change and is recorded here only.

## Status (2026-10-01)

All six steps implemented on `wt/aim-agent-reads` and opened as a draft PR,
awaiting review of the twelve decisions. Gates green: ruff, ruff format,
mypy, the full suite on Python 3.12 and 3.10 (1,634 passed, 4 skipped, 2
xfailed each), and the converter job's tests with the `[convert,pdf,dev]`
extras (1,638 passed, 2 xfailed). No
TypeScript change: the file format and history shape are unchanged.

Measured (o200k_base tokens; 81-chunk contract / 775-chunk report):

| | before | after |
|---|---:|---:|
| `aim_read` on the wire | 13,842 / 144,116 | 6,389 / 67,620 |
| `aim_read` text block (`full`) | 6,462 / 67,503 | 5,592 / 58,953 |
| `mode=text` | — | 3,021 / 21,172 |
| `mode=toc` | — | 294 / 2,634 |
| `mode=skeleton` (8 words) | — | 1,740 / 15,584 |
| one unit, `mode=chunks` | — | 57 / 37 |
| `tools/list` (seven tools vs six) | 1,815 | 1,321 |
| `doc.chunks` (report) | 0.93 s | 0.008 s |
| 100 direct edits in one batch (report) | — | 0.34 s |

Follow-ups deferred on purpose are recorded in the workspace `TODO.md`.
