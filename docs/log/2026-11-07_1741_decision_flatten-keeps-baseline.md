---
date: 2026-11-07 17:41
type: decision
status: active
related: [2026-10-01_2135_decision_flatten-collapses-to-checkpoint.md, 2026-10-01_2135_decision_history-baseline-event.md, 2026-10-01_2135_decision_reconcile-adopts-as-baseline.md]
---

# Decision IMPORT-D12b: flatten() leaves a baseline in 0.6+ documents

Id: **IMPORT-D12b**. Decided by the maintainer on 2026-11-06. Supersedes
[IMPORT-D12](2026-10-01_2135_decision_flatten-collapses-to-checkpoint.md).

## Context

IMPORT-D12 made `flatten()` leave one checkpoint, a hash with no copy of the text. Probed on
2026-11-06: after a flatten, a hand edit fails lint (H006), `verify()` reports it, and
`reconcile()` refuses and names `baseline()`, because nothing in the file says what the text
was. Every other way a history starts (an import, reconcile adopting a file under
IMPORT-D16, `baseline()`) keeps a full copy, which is what lets reconcile explain a later
hand edit.

## Decision

In a document declared 0.6 or later, `flatten()` replaces the log with one `baseline`
event at the next seq (label as passed, default `"flatten"`), through the same code as
`baseline()`. Below 0.6 it leaves one checkpoint, as IMPORT-D12 did, so a flatten never
raises a 0.5 file to 0.6 (§3.7). A document without history stays without it. A 0.6+ body
that cannot be a snapshot (H008, or units without a usable id) is refused with
`baseline()`'s messages before anything changes; the 0.6 line is unreleased, and a silent
fallback to a checkpoint would lose what this decision is for. Spec §6.8 and §6.9 say so.

## Consequences

A hand edit after a flatten reconciles with its true `before`. A flattened file is larger:
11% to 85% more tokens on the five DOCX fixtures measured on 2026-11-06. Stacked PRs that
test `flatten()` on 0.6 documents meet the change when they rebase.
