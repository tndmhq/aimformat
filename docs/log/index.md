# Log index

One line per episodic entry, newest first. Add a row when you create an entry;
update its status when it changes. Scaffold entries with
`python3 scripts/new_log_entry.py <type> <slug> --what "…"`.
(Format rules → [`docs/README.md`](../README.md). Kept light: durable
decisions and major plans/reviews only; external contributors are not
required to add entries.)

| Date | Type | Entry | Status | What |
|---|---|---|---|---|
| 2026-07-08 06:26 | plan | [reconcile](2026-07-08_0626_plan_reconcile.md) | done | Implement AimDocument.reconcile() + aim reconcile CLI verb (spec §6.8): detect out-of-band body edits against the full history and synthesize origin:reconcile events so the file verifies again — shipped same day with 30 tests |
| 2026-07-07 22:43 | plan | [converters](2026-07-07_2243_plan_converters.md) | done | Format converters: md/txt importers, docling wrappers, md/html/pdf exporters, aim import/export CLI — deps as optional extras |
| 2026-07-07 19:21 | review | [v01-deep-self-review](2026-07-07_1921_review_v01-deep-self-review.md) | done | Post-ship adversarial review of the v0.1 toolkit (own pass + three independent reproduce-before-report agents): 38 findings (5 critical, 16 major), all fixed with regression tests; anchor resolution rebuilt around context-validated primitives, serializer made a true normal form, verifier crash-proofed, tracked DOCX export completed for containers; 313 tests green. |
| 2026-07-07 16:59 | report | [v01-shipped](2026-07-07_1659_report_v01-shipped.md) | done | spec.md v0.1 + reference toolkit shipped: registry-driven spec with executable snippets, stdlib-only SDK (ops/proposals/history/verify/time-travel/assets/caches), aim CLI, aim.css generator, docling ingestor, DOCX exporter with tracked changes, 270+ tests incl. ok/nok conformance suite, README/guide/CONTRIBUTING/CHANGELOG/CI. |
| 2026-07-07 16:59 | decision | [v01-open-points-resolved](2026-07-07_1659_decision_v01-open-points-resolved.md) | done | All open points resolved by the implementing agent for v0.1 under maintainer delegation: id scheme (short opaque ids, UUIDs valid), vector-asset addressing, TOC id kinds, comments head-only, embeddings (no primary, plain floats), aim:doc reserved-unusable, stepped slide scales, chain-rebind rule, DOCX/docling interop scope, zero-dep + extras packaging, direct-to-main release. Each with rationale; flagged for maintainer review. |
| 2026-07-07 16:19 | plan | [spec-v01-and-reference-toolkit](2026-07-07_1619_plan_spec-v01-and-reference-toolkit.md) | done | Promote the format definition into spec.md v0.1 (registry-driven appendix, executable snippets), decide remaining open points, and implement the reference toolkit: zero-dep Python SDK (document ops, proposals, history, verify), aim CLI, aim.css generator, conformance fixtures, 100+ tests, docs. |
| 2026-07-06 20:13 | decision | [utc-log-timestamps](2026-07-06_2013_decision_utc-log-timestamps.md) | active | All docs/log timestamps across tndmhq repos switch from CET/CEST to UTC — contributor-neutral and DST-free; scaffolders + conventions updated, the one pre-existing workspace entry renamed. |
