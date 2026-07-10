# Evals

Measurement harnesses for design decisions in the format. Not wired into
CI: each live trial costs a real model call, so a human (or an agent with
budget) runs these deliberately and commits nothing but conclusions.

## `id_preservation.py` — does the agent note earn its bytes?

Every `.aim` file opens with the `aim-note:` head comment (spec §2.5): a
short, human-readable warning to any LLM that opens the file as plain
text, telling it what the file is, that tooling exists, and which
invariants matter if it hand-edits anyway. The design bet is that this
one comment measurably reduces silent corruption by naked models. This
eval produces that number.

### What it does

1. Builds a realistic ~15-construct document through the SDK (headings,
   paragraphs, a `<ul>` container, a table, one pending proposal, a
   summary cache) — deterministic content, `agent:eval-fixture` author.
2. Prepares two byte-identical variants except for the head comment:
   **with-note** and **without-note**. The note text is the canonical
   spec §2.5 template, imported from `aimformat.note` so the A/B always
   measures the note the tools actually emit.
3. Sends each variant through three plain-text edit tasks (fix a typo,
   rewrite the intro, add a bullet) via the `claude` CLI in headless
   mode: *"Here is a file. \<task\>. Return ONLY the complete edited
   file, no commentary."* No tools, no MCP, no skill — worst case on
   purpose.
4. Scores every reply with the `aimformat` library:

   | metric | meaning |
   |---|---|
   | `parses` | `aimformat.loads` accepts the output |
   | `ids_preserved` | every original body `data-aim`/`data-aim-container` id survives (no renumbering, no drops) |
   | `lanes_intact` | history script content byte-unchanged and no `<aim-proposal>` card deleted |
   | `lint_errors` | error-level findings from `aimformat.lint_text` |
   | `note_retained` | (with-note only) an `aim-note:` head comment survives the edit |

5. Prints a markdown results table and writes it to
   `evals/results/<UTC-timestamp>.md` (the directory is gitignored).

### Reading the numbers

- The comparison that matters is **with-note vs without-note** on the
  boolean columns, `ids_preserved` above all. If the note variant
  preserves ids and lanes at a meaningfully higher rate, the header
  design is doing its job.
- Two lint codes have a floor for *any* hand edit and are expected noise:
  `C001` (output not byte-canonical) and `H006` (body diverges from
  history replay — out-of-band edits are exactly what `aim reconcile`
  repairs). Corruption shows up as codes beyond those two, and as the
  boolean columns.

### Cost warning

Each trial is one real `claude -p` call carrying a ~20 KB document —
6 calls per `--trials 1` run (3 tasks x 2 variants), 18 per `--trials 3`.
Budget accordingly; there is no caching.

### Usage

```sh
# from the aimformat repo root; needs the venv (system python may be too old)

# exercise the whole pipeline with a canned fake reply — no API calls
.venv/bin/python evals/id_preservation.py --dry-run

# the real measurement: 3 trials per cell, pinned model
.venv/bin/python evals/id_preservation.py --trials 3 --model claude-fable-5
```

Requires the `claude` CLI on PATH for live runs
(`npm install -g @anthropic-ai/claude-code`, then log in). `--timeout`
bounds each call (default 300 s).
