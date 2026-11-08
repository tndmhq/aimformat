---
date: 2026-11-07 17:41
type: decision
status: active
related: [2026-10-01_2135_decision_baseline-lifecycle-operation.md, 2026-11-06_1123_plan_pr40-final-batch.md]
---

# Decision IMPORT-D11b: baseline(discard_history=True) repairs an unreadable history

Id: **IMPORT-D11b**. Decided by the maintainer on 2026-11-06. Extends
[IMPORT-D11](2026-10-01_2135_decision_baseline-lifecycle-operation.md), which left open
that `baseline()` "refuses a log it cannot parse (H002) — not yet a recovery for a corrupt
log".

## Context

Probed on 2026-11-06: with one unreadable history line (a damaged save, a bad hand edit),
lint fails H002 and `verify()`, `baseline()`, `flatten()`, `reconcile()` and `prune()` all
refuse. The only way out was to empty the history block by hand and run `reconcile()`.

## Decision

- `AimDocument.baseline(label, ..., discard_history=False)`. With `discard_history=True` the
  stored history is dropped without being parsed, readable or not, and the baseline is
  recorded as on a readable log: pending proposals, caches and assets stay. Its seq is
  `max(0, h) + 1`, where `h` is the highest integer seq among the dropped lines that parse,
  so it stays positive and never goes backwards as far as the file can tell. Units without
  a usable id get fresh ids; ids that are already usable keep them, including ids a pending
  payload names, so pending proposals keep their targets. A construct that cannot be a
  snapshot entry (H008) is still refused.
- `AimDocument.history_text()` returns the stored history block's text, unparsed, so a
  caller can keep what it is about to drop.
- `aim baseline FILE --discard-history` reads the file without newline translation, writes
  the dropped text byte for byte to `<OUT>.discarded-history.jsonl` (created exclusively,
  mode 0600, numbered rather than overwritten, fsynced), then writes the document to a
  temporary file and moves it over `OUT` atomically. A failure at any step leaves `OUT` as
  it was.
- Still no MCP tool (IMPORT-D11).

## Consequences

One command repairs a file whose history cannot be read. Ids that only the dropped text
named are not burned on the instance (the text cannot be trusted to parse); the side file
keeps them.
