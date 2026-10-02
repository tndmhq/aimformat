"""Markup-preserving text replacement inside one chunk (decision READS-D13).

An agent that wants to change a few words should not have to resend the
chunk's whole HTML: ``replace_text`` takes the text to find and the text to
put there, and keeps the chunk id, its attributes and every inline element
around the changed span.

The contract:

- ``old_text`` is matched against the chunk's text content (what
  ``Chunk.text`` returns: the text nodes in document order, entities decoded,
  tags removed). A run of whitespace in either side matches any run of
  whitespace in the other, so text copied from a view that collapses
  whitespace still matches (whitespace runs in ``new_text`` are written as
  one space). It must occur exactly once (overlapping
  occurrences count); 0 or 2+ is refused, with the count.
- Only the span that actually changes has to sit inside one text run:
  ``old_text`` and ``new_text`` are trimmed of their common prefix and
  suffix first, so quoting surrounding words for uniqueness is free even
  when they cross ``<strong>`` or ``<a>``. A change whose span still
  crosses an inline-markup boundary is refused: which side of the boundary
  the new text belongs to is a formatting decision the caller must make
  with a full ``modify``.
- A pure insertion at a boundary joins the run before it (the way typing
  continues the preceding formatting); a pure deletion may span runs, since
  deleted text has no formatting to decide.
- An inline formatting element left with no content is removed.

Pure and stdlib-only: parse the fragment, edit one text node, serialize.
"""

from __future__ import annotations

import re

from .canonical import serialize
from .dom import Element, Text, parse_fragment
from .errors import InvalidOperation

__all__ = ["TextReplaceError", "replace_in_markup"]

_WS = re.compile(r"\s+")
#: inline formatting elements that carry nothing once their text is gone
_PRUNABLE = frozenset(
    {"a", "abbr", "b", "code", "em", "i", "mark", "s", "small", "span", "strong", "sub", "sup", "u"}
)


class TextReplaceError(InvalidOperation):
    """``old_text`` is missing or ambiguous, or the change crosses markup."""


def _text_nodes(nodes: list) -> list[tuple[Text, list[Element]]]:
    """Every text node in document order with its element ancestry
    (outermost first). Adjacent text siblings are merged first, so a
    boundary between two runs is always a markup boundary."""
    out: list[tuple[Text, list[Element]]] = []

    def merge(children: list) -> None:
        i = 0
        while i < len(children) - 1:
            a, b = children[i], children[i + 1]
            if isinstance(a, Text) and isinstance(b, Text):
                a.data += b.data
                del children[i + 1]
            else:
                i += 1

    def walk(children: list, chain: list[Element]) -> None:
        merge(children)
        for c in children:
            if isinstance(c, Text):
                out.append((c, chain))
            elif isinstance(c, Element) and c.raw is None:
                walk(c.children, chain + [c])

    walk(nodes, [])
    return out


def _normalized(text: str) -> tuple[str, list[int], list[int]]:
    """*text* with every whitespace run collapsed to one space, plus, per
    normalized character, the [start, end) span it covers in *text*."""
    norm: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    pos = 0
    for m in _WS.finditer(text):
        for i in range(pos, m.start()):
            norm.append(text[i])
            starts.append(i)
            ends.append(i + 1)
        norm.append(" ")
        starts.append(m.start())
        ends.append(m.end())
        pos = m.end()
    for i in range(pos, len(text)):
        norm.append(text[i])
        starts.append(i)
        ends.append(i + 1)
    return "".join(norm), starts, ends


def _occurrences(haystack: str, needle: str) -> list[int]:
    found: list[int] = []
    i = haystack.find(needle)
    while i != -1:
        found.append(i)
        i = haystack.find(needle, i + 1)
    return found


def _describe(chain: list[Element]) -> str:
    return "".join(f"<{e.tag}>" for e in chain) or "plain text"


def replace_in_markup(markup: str, old_text: str, new_text: str) -> str:
    """Return *markup* with the single occurrence of *old_text* in its text
    replaced by *new_text*, every element and attribute kept (see the module
    docstring for the matching and refusal rules)."""
    if not old_text or not old_text.strip():
        raise TextReplaceError("replace_text needs a non-empty old_text")
    nodes = parse_fragment(markup)
    runs = _text_nodes(nodes)
    full = "".join(t.data for t, _ in runs)
    norm_full, starts, ends = _normalized(full)
    old_n = _WS.sub(" ", old_text)
    new_n = _WS.sub(" ", new_text)
    if old_n == new_n:
        raise TextReplaceError("old_text and new_text are the same; nothing to change")
    hits = _occurrences(norm_full, old_n)
    if not hits:
        raise TextReplaceError(
            "old_text not found in the chunk's text (it is matched against the plain text "
            "content: no tags, and none of the text view's **, ~~ or numbering labels)"
        )
    if len(hits) > 1:
        raise TextReplaceError(
            f"old_text occurs {len(hits)} times in the chunk's text; "
            "quote more surrounding words so it occurs once"
        )
    # trim the common prefix and suffix: only the span that really changes
    # has to sit inside one text run
    p = 0
    limit = min(len(old_n), len(new_n))
    while p < limit and old_n[p] == new_n[p]:
        p += 1
    q = 0
    while q < limit - p and old_n[-1 - q] == new_n[-1 - q]:
        q += 1
    replacement = new_n[p : len(new_n) - q]
    cs = hits[0] + p  # changed span in normalized coordinates
    ce = hits[0] + len(old_n) - q
    o_start = starts[cs] if cs < len(starts) else len(full)
    o_end = ends[ce - 1] if ce > cs else o_start
    # the text run holding the change (an insertion joins the run before it)
    offset = 0
    target: tuple[Text, list[Element], int] | None = None
    spanned: list[list[Element]] = []
    for node, chain in runs:
        end = offset + len(node.data)
        if ce > cs:
            if offset < o_end and o_start < end:
                spanned.append(chain)
                if offset <= o_start and o_end <= end:
                    target = (node, chain, offset)
        elif offset <= o_start <= end and node.data and target is None:
            target = (node, chain, offset)
        offset = end
    if target is None and not replacement:
        # a pure deletion has no placement question: cut the span out of
        # every run it covers
        offset = 0
        for node, chain in runs:
            end = offset + len(node.data)
            lo, hi = max(o_start, offset), min(o_end, end)
            if lo < hi:
                node.data = node.data[: lo - offset] + node.data[hi - offset :]
                if not node.data:
                    _prune(nodes, node, chain)
            offset = end
        return "".join(serialize(n) for n in nodes)
    if target is None:
        kinds = ", ".join(dict.fromkeys(_describe(c) for c in spanned)) or "markup"
        raise TextReplaceError(
            f"the changed text crosses inline markup ({kinds}), so where the new text "
            "belongs is a formatting decision: narrow old_text/new_text to text inside "
            "one run, or use action modify with the chunk's HTML"
        )
    node, chain, start = target
    node.data = node.data[: o_start - start] + replacement + node.data[o_end - start :]
    if not node.data:
        _prune(nodes, node, chain)
    return "".join(serialize(n) for n in nodes)


def _prune(nodes: list, node: Text, chain: list[Element]) -> None:
    """Drop the now-empty text node and any formatting element it leaves
    empty, innermost first (never the chunk's own root)."""
    parents: list = [nodes] + [e.children for e in chain]
    parents[-1].remove(node)
    for depth in range(len(chain) - 1, 0, -1):
        el = chain[depth]
        if el.children or el.tag not in _PRUNABLE:
            break
        parents[depth].remove(el)
