#!/usr/bin/env python3
"""Id-preservation eval: naked-LLM text edits of a .aim file.

Measures whether an LLM that edits a `.aim` file *as plain text* (no
aimformat tooling, no MCP, no skill — just "here is a file, edit it")
preserves the format's invariants, and whether the `aim-note:` head
comment (spec §2.5) moves that number. Two variants of the same fixture —
WITH the canonical agent note and WITHOUT it — go through the same edit
tasks; per output we score:

- parses          `aimformat.loads` succeeds
- ids_preserved   every original body chunk/container id survives
- lanes_intact    history script content byte-unchanged, no proposal
                  card deleted
- lint_errors     error-level findings from `aimformat.lint_text`
                  (C001 canonical-form drift is expected for hand edits;
                  the distinct codes are reported so it is separable)
- note_retained   (WITH variant only) an `aim-note:` head comment survives

The model is driven through the `claude` CLI in headless mode
(`claude -p … --output-format text`); each trial is one real model call.
`--dry-run` exercises the whole scoring pipeline with a canned fake model
output (deliberately renumbers one id and drops the note) — no API calls.

Usage:
    .venv/bin/python evals/id_preservation.py --dry-run
    .venv/bin/python evals/id_preservation.py --trials 3 --model claude-fable-5

Stdlib + aimformat only. The fixture is built through the SDK's public
API; the note comment (the canonical spec §2.5 template, imported from
aimformat.note so it can never drift) is inserted/stripped *textually*,
so the harness controls both variants regardless of what new_document()
emits.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import shutil
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import aimformat as aim

EVALS_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVALS_DIR / "results"

# --------------------------------------------------------------------------
# The agent note: byte-identical to the canonical template the SDK ships
# (spec §2.5) — the A/B must measure the note tools actually emit. Inserted
# and stripped textually so the harness works on any variant regardless of
# what new_document() emits.
from aimformat.note import _TEMPLATE as AIM_NOTE_TEMPLATE  # noqa: E402

META_CHARSET_LINE = '<meta charset="utf-8">'
_NOTE_COMMENT_RE = re.compile(r"<!--\s*aim-note:.*?-->\n?", re.S)
_ID_ATTR_RE = re.compile(r'\bdata-aim(?:-container)?="([^"]+)"')
_CARD_ID_RE = re.compile(r'<aim-proposal\b[^>]*?\bid="([^"]+)"')
_HISTORY_RE = re.compile(r'<script\s+type="application/aim-history\+jsonl"\s*>(.*?)</script>', re.S)
_FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)\n?```\s*$", re.S)


def strip_note(text: str) -> str:
    """Remove any aim-note head comment(s), textually."""
    return _NOTE_COMMENT_RE.sub("", text)


def insert_note(text: str, version: str) -> str:
    """Insert the canonical note right after the `<meta charset>` line."""
    lines = text.split("\n")
    try:
        at = lines.index(META_CHARSET_LINE)
    except ValueError:
        raise SystemExit(
            f"fixture has no {META_CHARSET_LINE!r} line — cannot place the aim-note comment"
        ) from None
    note = "<!--\n" + AIM_NOTE_TEMPLATE.format(version=version) + "\n-->"
    return "\n".join(lines[: at + 1] + [note] + lines[at + 1 :])


# --------------------------------------------------------------------------
# Fixture: a realistic ~15-chunk document, deterministic content.
FIXTURE_PROPOSAL_ID = "p-eval0001"


def _t(i: int) -> str:
    return f"2026-07-10T09:{i // 60:02d}:{i % 60:02d}Z"


def build_fixture() -> tuple[str, str]:
    """-> (with_note_text, without_note_text), both lint-clean."""
    bot = aim.agent("eval-fixture")
    doc = aim.new_document(title="Search Service Migration Plan")
    with doc.batch():
        doc.add_chunk(
            '<h1 data-aim="ttl">Search Service Migration Plan</h1>',
            author=bot,
            at=_t(0),
            explanation="Initial draft of the migration plan.",
        )
        doc.add_chunk(
            '<p data-aim="intro">This document has been written in order to '
            "describe, at a fairly high level, the plan that we are "
            "intending to follow for the migration of our search service "
            "from the legacy Solr 6 cluster onto the new OpenSearch "
            "deployment, and it also covers the goals, the timeline, and "
            "the risks that are associated with this effort.</p>",
            author=bot,
            at=_t(1),
        )
        doc.add_chunk('<h2 data-aim="bg-h">Background</h2>', author=bot, at=_t(2))
        doc.add_chunk(
            '<p data-aim="bg-p1">The legacy cluster indexes 41M documents '
            "across nine shards and serves roughly 220 queries per second "
            "at peak. It runs on hardware that leaves warranty at the end "
            "of Q4.</p>",
            author=bot,
            at=_t(3),
        )
        doc.add_chunk(
            '<p data-aim="bg-p2">Plugin compatability was the main blocker '
            "in the previous migration attempt: three custom analyzers "
            "depended on APIs removed in Solr 7.</p>",
            author=bot,
            at=_t(4),
        )
        doc.add_chunk('<h2 data-aim="goals-h">Goals</h2>', author=bot, at=_t(5))
        doc.add_chunk(
            '<p data-aim="goals-p">The migration is done when all of the following hold:</p>',
            author=bot,
            at=_t(6),
        )
        doc.add_chunk(
            '<ul data-aim-container="goals">'
            '<li data-aim="g1">Query latency p99 under 120ms at peak load</li>'
            '<li data-aim="g2">All three custom analyzers ported and '
            "covered by golden-query tests</li>"
            '<li data-aim="g3">Index rebuild from source of truth in under '
            "4 hours</li></ul>",
            author=bot,
            at=_t(7),
        )
        doc.add_chunk('<h2 data-aim="tl-h">Timeline</h2>', author=bot, at=_t(8))
        doc.add_chunk(
            '<p data-aim="tl-p">Four phases, each gated on the previous one:</p>',
            author=bot,
            at=_t(9),
        )
        doc.add_chunk(
            '<table data-aim-container="tl"><thead>'
            '<tr data-aim="r0"><th>Phase</th><th>Weeks</th><th>Owner</th>'
            "</tr></thead><tbody>"
            '<tr data-aim="r1"><td>Analyzer port</td><td>3</td>'
            "<td>Search team</td></tr>"
            '<tr data-aim="r2"><td>Dual-write and backfill</td><td>4</td>'
            "<td>Platform team</td></tr>"
            '<tr data-aim="r3"><td>Shadow traffic and cutover</td><td>2</td>'
            "<td>Search team</td></tr></tbody></table>",
            author=bot,
            at=_t(10),
        )
        doc.add_chunk('<h2 data-aim="risks-h">Risks</h2>', author=bot, at=_t(11))
        doc.add_chunk(
            '<p data-aim="risks-p">Scoring parity is the main risk: BM25 '
            "parameter defaults differ between the two engines, so ranking "
            "changes must be caught by the golden-query suite rather than "
            "by users.</p>",
            author=bot,
            at=_t(12),
        )
        doc.add_chunk('<h2 data-aim="next-h">Next steps</h2>', author=bot, at=_t(13))
        doc.add_chunk(
            '<p data-aim="next-p">Staffing is confirmed for the analyzer '
            "port; the dual-write design review is scheduled for the first "
            "week of the project.</p>",
            author=bot,
            at=_t(14),
        )
    prop = doc.propose_add(
        '<tr data-aim="r4"><td>Decommission legacy cluster</td><td>1</td>'
        "<td>Platform team</td></tr>",
        author=bot,
        container="tl",
        after="r3",
        at=_t(20),
        explanation="The plan should end with the legacy hardware gone.",
    )
    doc.set_summary(
        "Plan to migrate search from a legacy Solr 6 cluster to OpenSearch: "
        "four gated phases over nine weeks, gated on analyzer ports and "
        "golden-query parity. One pending timeline row awaits review.",
        model="eval-fixture",
    )
    version = doc.spec_version or aim.SPEC_VERSION
    text = doc.dumps()
    # Pin the (randomly minted) proposal id so the fixture is deterministic.
    text = text.replace(prop.id, FIXTURE_PROPOSAL_ID)
    without = strip_note(text)
    with_ = insert_note(without, version)
    for name, variant_text in (("with-note", with_), ("without-note", without)):
        errors = [f for f in aim.lint_text(variant_text) if f.level == "error"]
        if errors:
            raise SystemExit(
                f"fixture variant {name!r} does not lint clean: "
                + "; ".join(str(e) for e in errors)
            )
    return with_, without


# --------------------------------------------------------------------------
# Edit tasks. Instructions name sections/content, never ids or mechanics —
# the point is what a model does when told only what a user would say.
TASKS = [
    {
        "name": "typo-fix",
        "instruction": "Fix the typo 'compatability' in the Background section "
        "(it should read 'compatibility')",
    },
    {
        "name": "intro-rewrite",
        "instruction": "Rewrite the introduction paragraph (the one right "
        "after the main title) to be punchier: at most two "
        "short sentences, same meaning",
    },
    {
        "name": "bullet-add",
        "instruction": "Add one new bullet to the Goals list: 'Zero-downtime "
        "cutover for all tenants'",
    },
]

PROMPT_TEMPLATE = (
    "Here is a file. {task}. Return ONLY the complete edited file, no commentary.\n\n{file}"
)


# --------------------------------------------------------------------------
# Driver
def strip_fences(reply: str) -> str:
    """Unwrap a ```-fenced reply (models often wrap whole files).

    Normalizes to exactly one trailing newline — canonical .aim text ends
    with one, and the C001 comparison is byte-exact.
    """
    text = reply.strip()
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1)
    elif "```" in text:  # fenced file with prose around it: take the block
        blocks = re.findall(r"```[^\n]*\n(.*?)\n?```", text, re.S)
        docs = [b for b in blocks if b.lstrip().lower().startswith("<!doctype")]
        if docs:
            text = docs[0]
    return text.rstrip("\n") + "\n"


def run_model(prompt: str, *, model: str | None, timeout: float) -> str:
    cmd = ["claude", "-p", prompt, "--output-format", "text"]
    if model:
        cmd += ["--model", model]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:400]}")
    return proc.stdout


def fake_model_output(input_text: str, task_name: str) -> str:
    """Canned 'model output' for --dry-run: performs a plausible edit but
    plants deliberate violations so every scoring path is exercised
    without an API call:

    - renumbers one id (`intro` -> `intro-v2`)   -> ids_preserved fails
    - drops the aim-note comment                 -> note_retained fails
    - typo-fix does a *global* find/replace, which also rewrites the
      typo's copy inside a history event payload (the classic careless
      sed-style edit)                            -> lanes_intact fails
    - wraps the file in markdown fences          -> fence stripping runs
    """
    out = input_text.replace('data-aim="intro"', 'data-aim="intro-v2"')
    out = strip_note(out)
    if task_name == "typo-fix":
        out = out.replace("compatability", "compatibility")
    return "```html\n" + out.rstrip("\n") + "\n```"


# --------------------------------------------------------------------------
# Scoring
@dataclass
class Score:
    parses: bool
    ids_preserved: bool
    lanes_intact: bool
    lint_errors: int
    lint_codes: list[str] = field(default_factory=list)
    note_retained: bool | None = None  # None = not applicable (WITHOUT)
    detail: str = ""


def _body_id_set(doc: aim.AimDocument) -> set[str]:
    return {c.id for c in doc.chunks} | set(doc.containers)


def _head_slice(text: str) -> str:
    idx = text.find("</head>")
    return text[:idx] if idx != -1 else text


def _has_note(text: str) -> bool:
    return any(
        "aim-note:" in m.group(0) for m in re.finditer(r"<!--.*?-->", _head_slice(text), re.S)
    )


def score_output(original: str, edited: str, *, expect_note: bool) -> Score:
    orig_doc = aim.loads(original)
    orig_ids = _body_id_set(orig_doc)

    parses, edited_doc = True, None
    try:
        edited_doc = aim.loads(edited)
    except Exception:
        parses = False

    notes: list[str] = []
    if edited_doc is not None:
        edited_ids = _body_id_set(edited_doc)
    else:  # unparseable: fall back to a textual id sweep so we still score
        edited_ids = set(_ID_ATTR_RE.findall(edited))
        notes.append("ids checked textually (no parse)")
    missing = sorted(orig_ids - edited_ids)
    ids_preserved = not missing
    if missing:
        notes.append("missing ids: " + ", ".join(missing[:6]) + ("…" if len(missing) > 6 else ""))

    # lanes: history block content byte-unchanged + every card still there
    orig_hist = _HISTORY_RE.findall(original)
    edited_hist = _HISTORY_RE.findall(edited)
    history_ok = edited_hist == orig_hist
    if not history_ok:
        notes.append("history block rewritten or missing")
    orig_cards = set(_CARD_ID_RE.findall(original))
    cards_ok = orig_cards <= set(_CARD_ID_RE.findall(edited))
    if not cards_ok:
        notes.append("proposal card(s) deleted")
    lanes_intact = history_ok and cards_ok

    findings = aim.lint_text(edited)
    errors = [f for f in findings if f.level == "error"]
    codes = sorted({f.code for f in errors})

    note_retained = _has_note(edited) if expect_note else None

    return Score(
        parses=parses,
        ids_preserved=ids_preserved,
        lanes_intact=lanes_intact,
        lint_errors=len(errors),
        lint_codes=codes,
        note_retained=note_retained,
        detail="; ".join(notes),
    )


# --------------------------------------------------------------------------
# Reporting
def _frac(values: list[bool]) -> str:
    return f"{sum(values)}/{len(values)}"


def render_report(rows: list[dict], *, args: argparse.Namespace, fixture_stats: str) -> str:
    """rows: one dict per (task, variant) with 'scores': list[Score]."""
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    lines = [
        "# Id-preservation eval — naked LLM edits of a .aim file",
        "",
        f"- run: {stamp}  ·  mode: "
        f"{'dry-run (canned output, no API calls)' if args.dry_run else 'live'}",
        f"- model: {args.model or 'claude CLI default'}  ·  trials per cell: {args.trials}",
        f"- aimformat {aim.__version__} (spec {aim.SPEC_VERSION})  ·  fixture: {fixture_stats}",
        "",
        "Metrics per cell are `passes/trials`; lint errors are the mean "
        "error-level finding count. Two codes have a floor for *any* hand "
        "edit and are expected: C001 (not byte-canonical) and H006 (body "
        "diverges from history replay — hand edits are out-of-band until "
        "`aim reconcile`). The signal is the boolean columns plus any "
        "error codes beyond those two.",
        "",
        "| task | variant | parses | ids_preserved | lanes_intact | "
        "note_retained | lint_errors (mean) | error codes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        s: list[Score] = row["scores"]
        note_vals = [x.note_retained for x in s if x.note_retained is not None]
        note_cell = _frac(note_vals) if note_vals else "—"
        codes = sorted({c for x in s for c in x.lint_codes})
        lines.append(
            f"| {row['task']} | {row['variant']} "
            f"| {_frac([x.parses for x in s])} "
            f"| {_frac([x.ids_preserved for x in s])} "
            f"| {_frac([x.lanes_intact for x in s])} "
            f"| {note_cell} "
            f"| {statistics.mean(x.lint_errors for x in s):.1f} "
            f"| {', '.join(codes) or '—'} |"
        )
    details = [(row, i, sc) for row in rows for i, sc in enumerate(row["scores"], 1) if sc.detail]
    if details:
        lines += ["", "## Trial details", ""]
        lines += [
            f"- {row['task']} / {row['variant']} / trial {i}: {sc.detail}" for row, i, sc in details
        ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Measure invariant preservation of naked-LLM .aim edits, "
        "with vs without the aim-note head comment."
    )
    ap.add_argument(
        "--trials", type=int, default=1, help="model calls per task per variant (default 1)"
    )
    ap.add_argument("--model", default=None, help="passed through to `claude --model`")
    ap.add_argument(
        "--timeout", type=float, default=300.0, help="seconds per model call (default 300)"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="use a canned fake model output; no API calls"
    )
    args = ap.parse_args()

    if not args.dry_run and shutil.which("claude") is None:
        print(
            "error: the `claude` CLI is not on PATH.\n"
            "Install it (https://claude.com/claude-code, e.g. "
            "`npm install -g @anthropic-ai/claude-code`) and log in, "
            "or use --dry-run to exercise the pipeline without it.",
            file=sys.stderr,
        )
        return 1

    with_note, without_note = build_fixture()
    fixture_stats = (
        f"{len(_body_id_set(aim.loads(with_note)))} body ids, {len(with_note) / 1024:.0f} KB"
    )
    variants = [("with-note", with_note, True), ("without-note", without_note, False)]

    rows: list[dict] = []
    total = len(TASKS) * len(variants) * args.trials
    done = 0
    for task in TASKS:
        for vname, vtext, has_note in variants:
            prompt = PROMPT_TEMPLATE.format(task=task["instruction"], file=vtext)
            scores: list[Score] = []
            for trial in range(args.trials):
                done += 1
                print(
                    f"[{done}/{total}] {task['name']} · {vname} · trial {trial + 1}",
                    file=sys.stderr,
                )
                if args.dry_run:
                    reply = fake_model_output(vtext, task["name"])
                else:
                    try:
                        reply = run_model(prompt, model=args.model, timeout=args.timeout)
                    except (RuntimeError, subprocess.TimeoutExpired) as exc:
                        scores.append(
                            Score(
                                parses=False,
                                ids_preserved=False,
                                lanes_intact=False,
                                lint_errors=0,
                                detail=f"model call failed: {exc}",
                            )
                        )
                        continue
                sc = score_output(vtext, strip_fences(reply), expect_note=has_note)
                scores.append(sc)
            rows.append({"task": task["name"], "variant": vname, "scores": scores})

    report = render_report(rows, args=args, fixture_stats=fixture_stats)
    print(report)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS_DIR / f"{stamp}{'-dry-run' if args.dry_run else ''}.md"
    out.write_text(report, "utf-8")
    print(f"written: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
