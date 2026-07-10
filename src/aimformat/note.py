"""The agent note — the format's in-file self-description (spec §2.5).

One head comment, sigil ``aim-note:``, addressed to LLM agents and generic
tooling that open a ``.aim`` file outside AIM-aware software. Declarative
only: per §2.5 nothing may execute, install, or fetch anything because of
note content — the note informs, the reader decides.
"""

from __future__ import annotations

from .dom import Comment, Element
from .registry import REGISTRY

#: A head comment is an agent note iff its text starts with this (after
#: optional leading whitespace). The linter (S030) uses the same test.
SIGIL = "aim-note:"

_TEMPLATE = """\
aim-note: This file is an AIM document (open format, v{version}) — valid HTML plus
chunk identity, a pending-suggestions lane, and an edit history.
Agent docs: https://aimformat.com/llms.txt
The reliable way to edit this file is the `aimformat` tooling, which manages
ids, suggestions, and history for you: `pip install aimformat` for the `aim`
CLI (`aim --help`); `pip install 'aimformat[mcp]'` adds its MCP server
(`aim mcp`). An Agent Skill exists: `npx skills add tndmhq/aimformat`.
Hand-editing as plain text is the fallback; if you do: keep every data-aim id
stable (never renumber or reuse; give new content a fresh id), treat the
aim-proposals appendix and the history script as append-only tool lanes, and
validate with `aim lint`. Humans review in AIM editors:
https://aimformat.com/editors"""


def render_note(version: str | None = None) -> str:
    """The canonical comment data for *version* (default: current spec).

    Newline-framed so the serialized form is ``<!--\\naim-note: …\\n-->``,
    byte-stable through parse/serialize round-trips.
    """
    return "\n" + _TEMPLATE.format(version=version or REGISTRY.spec_version) + "\n"


def find_note(head: Element) -> Comment | None:
    """The first agent-note comment among *head*'s children, if any."""
    for node in head.children:
        if isinstance(node, Comment) and node.data.lstrip().startswith(SIGIL):
            return node
    return None


def is_canonical(data: str, version: str | None = None) -> bool:
    """Whether *data* is byte-exactly the canonical note for *version*."""
    return data == render_note(version)
