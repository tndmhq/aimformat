---
date: 2026-07-25 21:21
type: plan
status: active
related:
  - 2026-07-10_0414_decision_agent-native-surfaces.md
  - 2026-07-10_1646_decision_amend-proposal.md
  - 2026-07-25_2107_plan_html-tracked-export-and-view.md
---

# Agent-interface gaps found by watching an agent meet a .aim file cold

On 2026-07-25 a `.aim` with three pending proposals was handed to a coding
agent in a sandboxed session with no context beyond the file. Reviewing that
transcript against the surfaces shipped in
[agent-native-surfaces](2026-07-10_0414_decision_agent-native-surfaces.md)
found four gaps. All are deferred; the fifth finding (the editor listed as
"coming soon") was fixed the same evening in the landing repo.

## What worked, and should not be disturbed

The layered discovery did its job. Within seconds, unprompted, the agent
identified the format from the in-file note, fetched
`aimformat.com/llms.txt`, installed the package, linted the document, and
listed the three pending proposals with their explanations and targets. The
declarative (non-imperative) note phrasing drew no prompt-injection
suspicion. The CLI verbs were what it reached for, and it used
`--author human:luca` correctly without being told.

## 1. No recipe for showing a document to a human

`docs/for-agents.md` §"Humans and editors" says to hand the file to an
editor rather than pasting diffs into chat. In this session the human *was*
in a chat, the agent had no sanctioned way to show them the document, and
improvised for roughly a quarter of the session: a hand-rolled BeautifulSoup
review page, then a DOCX export, then a screenshot of headless Chromium,
before finally handing over the file itself to be rendered.

Add an explicit recipe to for-agents.md and the Skill: produce an artifact
and hand it over. Say plainly that a server started inside a sandbox is
unreachable from the user's browser, so opening or serving is not the move.
The real fix is [`aim view` and the HTML tracked
export](2026-07-25_2107_plan_html-tracked-export-and-view.md); this is the
guidance that should exist either way.

## 2. The HTML export default hides exactly what a reader wants

`aim export -o out.docx` defaults to `pending="tracked"` and produces a
reviewable redline. `aim export -o out.html` defaults to `"keep"`, which
retains the proposals appendix whose payloads sit in `<template>` and never
render. So an agent exporting HTML *to show pending changes* produces a file
that does not show them, while the same command aimed at DOCX works.

Until the `tracked` fate exists for HTML, document the asymmetry: to show
pending changes today, export DOCX. When it lands, reconsider the default.

## 3. `amend_proposal` reaches no agent surface

Shipped 2026-07-10 explicitly for agent loops that iterate on a pending
suggestion across turns without churning its id or spamming the history with
supersede events. It exists on `AimDocument` and nowhere else: not in the
CLI, not among the six MCP tools, not in `for-agents.md`, not in the Skill.
The agents it was designed for cannot reach it, so a follow-up instruction
("make that suggestion shorter") forces a supersede — the exact noise the
method was built to avoid.

Expose it: an `aim amend` verb and a seventh MCP tool, plus a line in the
agent docs about when to amend rather than supersede.

## 4. No install guidance for PEP 668 sandboxes

The session needed `pip install aimformat --break-system-packages`. Most
agent sandboxes are Debian-based images that mark system Python
externally-managed, so the bare `pip install aimformat` printed in every
file's own note fails there. This agent knew the override; one that does not
falls back to the other option the note offers — hand-editing the HTML —
which risks reused ids and a rewritten history lane. A failed install
degrades to file corruption, not to slowness.

Document the three environments in for-agents.md and the Skill: `uvx
aimformat …` for a zero-install isolated run, `pip install aimformat` inside
a virtualenv, `--break-system-packages` in a managed-Python sandbox. Note
that decision 6 of the agent-native-surfaces entry shipped the `aimformat`
console-script alias *specifically* so `uvx aimformat` resolves correctly
(`uvx aim` would fetch an unrelated PyPI package), so the capability already
exists and is simply undocumented. Verify `uvx aimformat show FILE` once
before publishing the line. Consider whether the in-file note should carry
it too: unlike the stylesheet, note text is free to change, since existing
files keep their own copy and nothing fails lint.

## Sequencing

One small PR covers all four: docs for 1, 2 and 4, plus the CLI verb and MCP
tool for 3. None of it is urgent.
