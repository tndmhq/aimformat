---
date: 2026-10-01 21:35
type: decision
status: todo
related: [2026-10-01_2135_decision_importers-record-baseline.md, 2026-10-01_2135_decision_baseline-lifecycle-operation.md, 2026-10-01_2135_decision_flatten-collapses-to-checkpoint.md, 2026-10-01_2135_decision_toc-on-import-and-freshness.md, 2026-10-01_2135_decision_import-versioning-and-sequencing.md, 2026-10-01_2135_decision_reconcile-adopts-as-baseline.md]
---

# Decision IMPORT-D9: a `baseline` history event kind with a required snapshot (spec v0.6)

**Status: proposed — awaiting founder approval.** Implemented, uncommitted, on `wt/aim-import-tracked`; deviations are listed in the [implementation report](2026-10-01_2237_report_import-fidelity-implementation.md). Frontmatter `status: todo` per the log convention (`docs/README.md`); flip to `done` when the approved decision ships, or `superseded` if review changes it.

Id: **IMPORT-D9**. Related decisions: [IMPORT-D10](2026-10-01_2135_decision_importers-record-baseline.md), [IMPORT-D11](2026-10-01_2135_decision_baseline-lifecycle-operation.md), [IMPORT-D12](2026-10-01_2135_decision_flatten-collapses-to-checkpoint.md), [IMPORT-D13](2026-10-01_2135_decision_toc-on-import-and-freshness.md), [IMPORT-D14](2026-10-01_2135_decision_import-versioning-and-sequencing.md), [IMPORT-D16](2026-10-01_2135_decision_reconcile-adopts-as-baseline.md).

## Context

Importers write one `add` event per top-level construct, each with its full
payload plus about 85 tokens of envelope ("Ingestion is history", `ingest.py`).
History is 1.3–2.5× the body; on legal-addendum 6,978 of 14,373 history tokens
are envelope. Measured side effects:

- `undo()` right after an import deletes the **last imported block**.
- Save-path lint in a consuming editor replays the whole history on every save
  (an imported 775-chunk report starts at 379 events).

Why the origin must carry content, not only a hash: reconcile builds the expected
state by forward replay. A hand edit to imported clause 12 needs clause 12's
original text as `before`, or the adoption is not invertible (§6.6). A hash-only
origin already exists in v0.5 — a pruned log whose first retained event is a
`checkpoint` at seq > 1 (verify checks its `doc_hash`, reconcile refuses it as
pruned, lint warns H004) — and it can **detect** such an edit but not localize or
adopt it. A self-sufficient history therefore needs one copy of the origin state
(≈ 1× body); what can go is the per-event envelope and the multi-event noise.

## Options considered

| Option | File (long-report, o200k) | Reconcile | Spec | Verdict |
|---|---|---|---|---|
| A. Status quo | 130k | works | none | rejected: 2–3× body; undo deletes blocks |
| B. Flatten after import | 52k | refuses (with IMPORT-D12) or crashes (today) | none | not a default; stays a user choice |
| C. Trim the envelope | ~120k | works | none | rejected: ~10%, shape stays wrong |
| D. Checkpoint at seq 1 as origin | 52k | broken: v0.5 reconcile assumes an empty origin at seq 1 | none | rejected |
| E. Checkpoint + `x_snapshot` vendor field | 98k | v0.5 tools see a pruned log | none in name | rejected: `x_*` fields are ignorable by definition, yet this one would change what verify and reconcile do — load-bearing semantics in an ignorable field |
| **F. New `baseline` kind, snapshot required** | **98k** | works from the file alone | v0.6 | **chosen** |
| G. Snapshot as gzip + base64 | ~40k (est.) | works | v0.6 | rejected: opaque blob in a history meant to be readable JSON (§6.1, principle 5); TS reader would need decompression |
| F′. Baseline with optional snapshot | — | — | v0.6 | rejected: the snapshot-less variant duplicates the existing checkpoint-first pruned log |

## Decision

F. Event shape:

```json
{"seq":1,"kind":"baseline","t":"2026-10-01T12:00:00Z","label":"import","doc_hash":"sha256:…",
 "author":{"id":"docx-import","type":"external"},"explanation":"Imported from 'contract.docx'",
 "snapshot":{"html":"<html data-aim-version=\"0.6\" lang=\"en\">","doc":"<script type=\"application/aim-doc+json\">…<\/script>",
             "theme":"<style data-aim-theme>…<\/style>","body":["<h1 data-aim=\"…\">…<\/h1>","…"]}}
```

Semantics:

- `snapshot` is the reduced projection (§11.3) written out: exactly the lines
  `doc_hash` hashes, as `{html, doc?, theme?, body: [construct serializations]}`.
  Body is a list because a construct line may contain newlines (`pre`).
  `doc_hash` MUST equal the hash recomputed from the snapshot (a reader can compare
  hashes without parsing markup; the event checks itself).
- Every snapshot entry MUST parse as exactly one construct and pass the element,
  URL, handler and style rules applied to pending payloads (the snapshot feeds
  reconcile's expected state, so it must not be an uninspected carrier of inert
  `<script>` / `on*` / forbidden URLs).
- It is the first retained event; a log carries at most one. Its `seq` continues
  the document's numbering (1 for an import, `seq+1` when collapsing an existing
  log); seq never goes backwards.
- Not state-changing: after a fresh import `undo()` has nothing to undo (no new
  undo logic — non-state-changing events are already skipped).
- `state_at(n)` raises for every `n < baseline.seq` (fixes an off-by-one: today
  `state_at` refuses only `seq < first_seq − 1`, which would return the imported
  state for `state_at(0)`). A checkpoint-first pruned log keeps today's rule.
- verify: replay backwards to the baseline; compare with `snapshot` entry by entry,
  byte for byte (localizes the mismatching construct); compare with `doc_hash`.
- reconcile: expected state = snapshot + forward replay of later events, so
  out-of-band edits are adopted exactly as today. Theme and settings set at import
  become tracked origin state, so hand edits to them become adoptable too.
- prune: `prune(before=k)` with `k` above the baseline drops it like any other
  event (leaving an H004 pruned log); `prune(before=<label>)` matches a
  baseline's label.
- The history index does **not** parse the snapshot (every snapshot id is live in
  the body or recorded by a later `delete`; the only exception, an unreconciled
  hand delete, is recorded by reconcile). Only verify, reconcile and lint parse it.
- `classify_divergence` unchanged: a re-baselined file reads as
  `history_rewritten`.

Spec text:

- **§6.2 table**, new row: `baseline` | required `seq, kind, t, label, doc_hash,
  snapshot` | optional `author, explanation, source` | state-changing: no (it is
  the origin).
- **§6.3:** "strictly contiguous within the retained log (a documented gap at the
  start after pruning; a log that begins with a `baseline` starts at that
  baseline's seq)".
- **New §6.9 "Baselines (since v0.6)":**

  > A `baseline` event declares the origin of the retained log: the document state
  > at its `seq` is where the log begins, and no earlier state is recorded in the
  > file. When present it MUST be the first retained event, and a log carries at
  > most one. `snapshot` writes the origin state out as `{html, doc?, theme?,
  > body: [construct serializations in order]}`: exactly the lines §11.3 hashes.
  > `doc_hash` MUST equal the hash recomputed from it, and every snapshot entry
  > MUST conform as a payload does. Time travel to the baseline's seq,
  > verification and reconciliation work from the file alone. A baseline is not
  > state-changing and is never undone. Recording a baseline raises the declared
  > version to at least 0.6 inside the snapshot; no `aim:version` event is
  > recorded, because no earlier state is retained to record it against (§3.7).
  > Writers that create a document from content that did not arrive by editing
  > (importers) SHOULD record a baseline rather than one `add` event per
  > construct. A document that needs only a hash-anchored origin uses a checkpoint
  > as its first retained event (§6.8, prune).

- **§6.7:** "State at seq N is defined for N ≥ the baseline's seq. Verifiers MUST
  compare the reconstruction at a baseline with its snapshot byte for byte and
  with its `doc_hash`."
- **§6.8:** add `baseline` to the lifecycle operations (IMPORT-D11); reconcile
  "requires the full retained log, or a log that begins with a baseline"; flatten
  redefined per IMPORT-D12.
- **§3.7:** the version raise is sanctioned explicitly by §6.9; a baseline under a
  declared version below 0.6 is **S034** ("markup from a spec era newer than the
  declared version") — no new code.

Lint (registry + Appendix A.7):

- **H007** (error): baseline not first, or more than one.
- **H008** (error): snapshot does not hash to `doc_hash`, or an entry is not a
  single conforming construct (message names the construct index and points at
  the likely cause, e.g. a global find-and-replace that also hit the snapshot).
- **H004** does not fire when the log begins with a baseline.
- **H009** (warning): history starts at seq 1 but backward replay does not reach
  an empty body — an unrecorded origin. Compares body constructs and `aim:doc`
  only and tolerates an untracked theme (today's importers set the theme without
  an event; `reconcile._align_theme_baseline` exists for this), so existing
  imported files do not warn. A warning, so files that pass today stay valid.

## Consequences

- `Event.kind` gains a value; `verify`, `state_at`, `reconcile`, `prune`, lint,
  `llms.txt`, the skill and the hand-editing guidance learn it (the history may
  begin with a `baseline` line, which is never edited by hand).
- A v0.5 tool reading a v0.6 file reports S002 and H003, as the versioning rules
  intend.
- **TS reader** (`ts/`): history stays opaque JSONL (`historyJsonl`); only
  `registry.data.ts` regenerates.
- **tndm editor:** its backend reads event kinds in one place
  (it looks only for `resolution` events), so an unknown kind is
  harmless there; its document ceiling already excludes the history block, so the
  snapshot does not count against it. It must move to a 0.6-aware pin before it
  can accept 0.6 files (IMPORT-D14).
- The same payload-security gap for *ordinary* history payloads predates this
  work and is recorded in `TODO.md`.

## Compatibility and migration

**Format change: spec v0.6** (registry `spec_version`, `data-aim-css`, status
line, aim-note version, Appendix A, examples, conformance fixtures,
`ts/src/registry.data.ts`, parity goldens). Every existing v0.5 file stays valid
and unchanged; nothing is migrated. Files containing a baseline declare ≥ 0.6 and
need 0.6-aware tools.

## Tests

Conformance kit (`scripts/gen_fixtures.py`) — ok: baseline at seq 1; baseline at
seq 58 followed by edits; baseline followed by resolution of a pre-baseline
proposal. nok: H007 (not first; two baselines); H008 (hash mismatch; `<script>`
entry; `onclick` entry; two constructs in one entry); hand-edited body under a
baseline (H006 naming the construct); S034 (baseline under a 0.5 declaration);
H009 (flattened-then-edited 0.5 file, warning only). SDK: verify on a baselined
document; `state_at(b)` returns the snapshot state and `state_at(b-1)` raises;
reconcile after a hand edit of an untouched imported chunk adopts it with the
correct `before`; `classify_divergence` across a re-baseline returns
`history_rewritten`; `prune(before="import")`.

## Evidence

Measurements and probes were taken against `79a006b` (release 0.5.2), the base of branch `wt/aim-import-tracked`, on `tests/fixtures/docxs/*` and on purpose-built tracked-change probes (a Word-shaped revision document and the output of our own `to_docx(pending="tracked")`). Token counts use tiktoken `o200k_base`; they move by about ±0.1% between runs because ids are random.
