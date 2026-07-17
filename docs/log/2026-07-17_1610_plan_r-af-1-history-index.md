---
date: 2026-07-17 16:10
type: plan
status: done
related: []
---

# Plan: incremental history and id bookkeeping

Owner-approved refactor R-AF-1 (2026-07-17), closing AF-16. The tree and
history JSONL remain authoritative; this work adds no mutable tree index.

1. Add a permanent bulk-add scaling regression with absolute and growth-ratio
   bounds. Demonstrate it fails against the `origin/main` implementation, then
   keep the benchmark-only commit green with a strict expected failure.
2. Add a lazy, derived `_HistoryIndex` owned by each `AimDocument`. Build it
   once from history plus the instance-lifetime burned-id seed, route history
   readers through it, and update it from every history writer.
3. Replace the existing AF-05 burned-id ledger with the index's single ledger;
   preserve prune/flatten lifetime semantics and ensure verify/reconcile clones
   rebuild their own index from authoritative state.
4. Add optional-Hypothesis recompute-parity coverage for arbitrary mutation,
   proposal-resolution, prune, and flatten interleavings. Compare the live
   index exactly with a scratch rebuild from JSONL plus the lifetime seed.
5. Between stages, honor the review `HOLD` file if present. Run focused tests
   and the full suite after each logical commit; finish with ruff, format,
   mypy, full pytest, example lint, fresh benchmarks, and log consolidation.
