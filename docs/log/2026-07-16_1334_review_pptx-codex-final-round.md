---
date: 2026-07-16 13:34
type: review
status: done
related: ["2026-07-10_1245_plan_fixed-layout-pages.md", "2026-07-16_1259_report_review-round2-exporter-mcp-fixes.md"]
---

# Review: the untriaged final Codex round on PR #5 (fixed-layout pages)

Codex posted one last review round on PR #5 twenty seconds AFTER the squash
merge (review 4673029466, 2026-07-10T16:01:05Z; merge was 16:00:45Z), so it was
never triaged. This entry recovers that round, triages each finding against
today's code (branch base: `wt/agent-harness-fixes` on top of `main`
b9baae3), and doubles as the fix plan. Each verdict below was established by an
independent triage agent and confirmed by an adversarial verifier; every
Python-reachable claim was reproduced live before being accepted.

All five findings here are format-side only; no cross-repo coupling.

## Verdicts

| # | File | Claim (condensed) | Verdict |
|---|------|-------------------|---------|
| A1 | `document.py` (`_resolve`, modify-accept) | Externally authored multi-root modify payloads: only `roots[0]` guarded before `_state.replace()` writes everything; `<p data-aim=X></p><aim-slide data-aim=X>…</aim-slide>` lints clean, accept succeeds, doc then fails S031/S024/H006 | **still present** (reproduced: lint CLEAN before accept, S031+S024+H006 after) |
| A2 | `export_docx.py` (`emit_slide`) | A blank slide (no children) before any flow content emits nothing — `_has_content()` false, empty child loop, and the NEXT slide resets `_break_before_next` — so the page vanishes from DOCX while remaining a real PDF page | **still present** (reproduced) |
| A3 | `document.py` (`accept` with `applied=`) | Tweaking a bare-slide add can stamp `data-aim-container` onto an incompatible root (`<p>`) → S025/V003 | **already fixed** — `_payload_like` gained `_guard_replacement_kind` in the amend_proposal change (b9baae3, PR #6); repro confirms the tweak now raises `InvalidOperation` |
| A4 | `document.py` (`_normalize_payload`, `elif assign:`) | A valid unused caller-supplied `data-aim` on an `aim-slide` root is honored (`_payload_marker` only consulted on the fresh-id path), so `add_chunk('<aim-slide data-aim="sx">…')` immediately violates S031, and `propose_add` mints a card that fails after acceptance | **still present** (reproduced) |
| A5 | `convert/_pdf_out.py` (`_slide_page_css`) | A slide without inline width/height gets a 960×540 named page but the element rule sets only `page`+`zoom`; the slide box collapses (children are absolutely positioned, `overflow:hidden`) → blank/clipped print | **still present** (reproduced) |

## Fix plan (this branch, `wt/pptx-review-fixes`)

- **A1** — in `_resolve`'s modify-accept branch, route the unmodified payload
  through the same full normalization accept-with-tweaks uses
  (`_normalize_payload(payload, expect_id=prop.target, assign=False)`) so a
  malformed run raises `InvalidOperation` instead of being written; tighten
  lint `P010` to validate every root (shared id, multi-root only for item
  carriers), so hand-authored cards fail `aim lint` before acceptance.
- **A2** — in `emit_slide`, when a slide emitted no body content, add a
  placeholder empty paragraph so the page survives and the following slide
  still opens its own page.
- **A4** — in `_normalize_payload`'s honored-id path, enforce the tag-derived
  marker (`_payload_marker`): an `aim-slide` root carrying `data-aim` moves its
  id to `data-aim-container` (and vice-versa for chunk-only tags), keeping the
  caller's id instead of failing lint later.
- **A5** — emit the resolved `width`/`height` on the per-slide print rule
  unconditionally (inline styles still win when present).

Every fix lands with a regression test in `tests/test_review_regressions.py`.

## Outcome (PR #10)

All four fixes landed as designed (A1 additionally grew `skip_payload_of`
plumbing so a plain accept does not remap ids the card itself minted).
Codex's review of the fix PR surfaced three follow-ups, all fixed with
red-first regression tests:

- lint `P010` now enforces marker/kind **parity with accept**: a card keeping
  the target id on the wrong kind of root linted green while accept rejects
  it;
- …including the **double-marker** variant (right marker plus the wrong one on
  the same root), which read as kind-consistent;
- the accept-side wrong-marker guard now scans **every run root** — a
  container marker smuggled onto the second `li` of a run was previously
  written into the body verbatim.

Final: 643 tests passing, ruff/mypy clean; a private-repo reference in the
first version of this entry was generalized per REVIEW.md public-repo hygiene.
