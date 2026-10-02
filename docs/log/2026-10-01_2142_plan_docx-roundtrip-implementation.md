---
date: 2026-10-01 21:42
type: plan
status: active
related:
  - 2026-10-01_2132_decision_docx-roundtrip-architecture.md
  - 2026-10-01_2132_decision_docx-roundtrip-marks-default-on.md
---

# Plan: DOCX round-trip implementation (G3)

Implements the ROUNDTRIP decision set (D1–D9, D12, D13; see the
[architecture entry](2026-10-01_2132_decision_docx-roundtrip-architecture.md)
for the file plan). Everything is reviewable on one branch; nothing merges
before the decisions are approved.

## Scope of this pass

1. **Export side** (`export_docx.py`): `to_docx(..., roundtrip_marks=...)`
   writes one collapsed bookmark per exported paragraph of every unit
   (`_aim_<id>` on the first, `_aim<k>_<id>` on later ones, `_aimh_<hash>`
   for ids that are not bookmark-safe) and the `urn:aimformat:docx-roundtrip:1`
   manifest as a custom XML part. **Default off** in this pass: ROUNDTRIP-D4
   turns it on only after the manual Word check (design §7.4), which has not
   run. CLI `aim export --roundtrip-marks`, MCP `aim_export(roundtrip_marks=)`.
2. **Import side**: the DOCX importer carries markers through as data
   (internal, `convert_docx` unchanged); `revision_import.py` aligns returned
   units to the exported ones (markers, cross-check, exact LCS, budgeted fuzzy
   matching, unique long-text moves), recomputes the null round trip N with the
   running SDK, suppresses conversion noise, rebases text edits onto base
   markup, detects lossy units, checks drift with the manifest hashes, and
   emits one batch of proposals (default) or `origin: "reconcile"` edits via
   the reconcile driver.
3. **Surfaces**: `AimDocument.import_revision()` + `RevisionImportReport`,
   `aim import X.docx --onto BASE.aim`, MCP `aim_import_revision`.
4. **Docs**: `docs/interop/docx-roundtrip.md`, one informative spec §10 bullet
   and one Appendix B line, README, for-agents, skill, CHANGELOG, architecture.
5. **Tests**: red-first null round trip (0 ids survive today), then the
   property "export then import onto the same document = zero changes", edit
   sets with ground truth, refusals, idempotency, untrusted input.

## Deferred (listed in the PR as remaining work)

- D10a (colleague Track Changes as per-author cards) — needs G4.
- D10b (Word accept/reject of exported tracked cards) — phase C.
- Word-saved fixtures (design §7.4) — needs a person with Word.
