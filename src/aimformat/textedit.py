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
- A pure insertion at an inline-formatting boundary joins the run before
  it (the way typing continues the preceding formatting). At a block or
  line boundary inside the chunk (two cells, two list items, a ``<br>``) it
  goes to the side ``old_text`` was quoted from: before the match's first
  word it starts the next cell, after its last word it ends the previous
  one, and between quoted words on both sides it is refused.
- A pure deletion may span runs, since deleted text has no formatting to
  decide. When the deleted characters could be read at more than one place
  ("Note: Notice" -> "Notice" deletes "Note: " or "e: Not") and the
  readings cut different runs, the one on word edges wins; with no single
  such reading the deletion is refused.
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


#: elements that flow inline with the text around them: crossing into or out
#: of one does not move text to another cell, item, paragraph or line
_INLINE = _PRUNABLE | frozenset(
    {"bdi", "bdo", "cite", "data", "del", "dfn", "ins", "kbd", "q", "samp", "time", "var"}
)
#: a pure deletion is tried at every reading; past this many it is refused
_MAX_READINGS = 256


class _Run:
    __slots__ = ("node", "chain", "flow")

    def __init__(self, node: Text, chain: list[Element], flow: int):
        self.node, self.chain, self.flow = node, chain, flow


def _text_nodes(nodes: list) -> list[_Run]:
    """Every text node in document order with its element ancestry
    (outermost first) and its *flow*: two runs share a flow when only
    inline formatting separates them, and differ when a block or line
    boundary (``<td>``, ``<li>``, ``<p>``, ``<br>``, an image…) sits between
    them. Adjacent text siblings are merged first, so a boundary between two
    runs is always a markup boundary."""
    out: list[_Run] = []
    flow = 0

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
        nonlocal flow
        merge(children)
        for c in children:
            if isinstance(c, Text):
                out.append(_Run(c, chain, flow))
            elif isinstance(c, Element):
                inline = c.tag in _INLINE and c.raw is None
                if not inline:
                    flow += 1
                if c.raw is None:
                    walk(c.children, chain + [c])
                if not inline:
                    flow += 1

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


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


def _word_edge(text: str, i: int) -> bool:
    """True when position *i* of *text* does not split a word."""
    return i <= 0 or i >= len(text) or not (text[i - 1].isalnum() and text[i].isalnum())


def replace_in_markup(markup: str, old_text: str, new_text: str) -> str:
    """Return *markup* with the single occurrence of *old_text* in its text
    replaced by *new_text*, every element and attribute kept (see the module
    docstring for the matching and refusal rules)."""
    if not old_text or not old_text.strip():
        raise TextReplaceError("replace_text needs a non-empty old_text")
    runs = _text_nodes(parse_fragment(markup))
    norm_full = _normalized("".join(r.node.data for r in runs))[0]
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
    hit = hits[0]
    pre = _common_prefix(old_n, new_n)
    suf = _common_prefix(old_n[::-1], new_n[::-1])
    cut = len(old_n) - len(new_n)
    if cut > 0 and pre + suf >= len(new_n):
        return _delete(markup, norm_full, hit, cut, range(len(new_n) - suf, pre + 1))
    # trim the common prefix and suffix: only the span that really changes
    # has to sit inside one text run
    q = min(suf, min(len(old_n), len(new_n)) - pre)
    replacement = new_n[pre : len(new_n) - q]
    return _apply(markup, hit + pre, hit + len(old_n) - q, replacement, hit, hit + len(old_n))


def _delete(markup: str, norm_full: str, hit: int, cut: int, splits: range) -> str:
    """A pure deletion of *cut* characters, which can often be read at more
    than one place: deleting "Note: " from "Note: Notice" leaves "Notice",
    and so does deleting "e: Not". The readings give the same plain text but
    may cut different runs; when they disagree, the reading on word edges
    wins, and with no single such reading the deletion is refused."""
    last = splits[-1]
    try:
        # every reading inside one run (the common case) cuts that run
        # alike: probing the union of their spans refuses iff it crosses markup
        _apply(markup, hit + splits[0], hit + last + cut, "x", hit, hit + cut)
    except TextReplaceError:
        pass
    else:
        return _apply(markup, hit + last, hit + last + cut, "", hit, hit + cut)
    if len(splits) > _MAX_READINGS:
        raise TextReplaceError(
            "the deleted text can be read at too many places; quote exactly the text "
            "to delete as old_text with new_text ''"
        )
    outs = {k: _apply(markup, hit + k, hit + k + cut, "", hit, hit + cut) for k in splits}
    if len(set(outs.values())) == 1:
        return next(iter(outs.values()))
    edged = {
        out
        for k, out in outs.items()
        if _word_edge(norm_full, hit + k) and _word_edge(norm_full, hit + k + cut)
    }
    if len(edged) == 1:
        return edged.pop()
    raise TextReplaceError(
        "the deleted text could sit at more than one place across inline markup, and "
        "each reading removes different formatting: quote exactly the text to delete as "
        "old_text with new_text '', or use action modify with the chunk's HTML"
    )


def _apply(markup: str, cs: int, ce: int, replacement: str, hit_start: int, hit_end: int) -> str:
    """Replace the normalized span [cs, ce) of *markup*'s text with
    *replacement*; *hit_start*/*hit_end* bound the matched old_text."""
    nodes = parse_fragment(markup)
    runs = _text_nodes(nodes)
    full = "".join(r.node.data for r in runs)
    _, starts, ends = _normalized(full)
    o_start = starts[cs] if cs < len(starts) else len(full)
    o_end = ends[ce - 1] if ce > cs else o_start
    target: tuple[_Run, int] | None = None
    spanned: list[list[Element]] = []
    if ce > cs:
        offset = 0
        for run in runs:
            end = offset + len(run.node.data)
            if offset < o_end and o_start < end:
                spanned.append(run.chain)
                if offset <= o_start and o_end <= end:
                    target = (run, offset)
            offset = end
    else:
        target = _insertion_run(runs, o_start, at_start=cs == hit_start, at_end=cs == hit_end)
    if target is None and not replacement:
        # a pure deletion has no placement question: cut the span out of
        # every run it covers
        offset = 0
        for run in runs:
            end = offset + len(run.node.data)
            lo, hi = max(o_start, offset), min(o_end, end)
            if lo < hi:
                run.node.data = run.node.data[: lo - offset] + run.node.data[hi - offset :]
                if not run.node.data:
                    _prune(nodes, run.node, run.chain)
            offset = end
        return "".join(serialize(n) for n in nodes)
    if target is None:
        kinds = ", ".join(dict.fromkeys(_describe(c) for c in spanned)) or "markup"
        raise TextReplaceError(
            f"the changed text crosses inline markup ({kinds}), so where the new text "
            "belongs is a formatting decision: narrow old_text/new_text to text inside "
            "one run, or use action modify with the chunk's HTML"
        )
    run, start = target
    node = run.node
    node.data = node.data[: o_start - start] + replacement + node.data[o_end - start :]
    if not node.data:
        _prune(nodes, node, run.chain)
    return "".join(serialize(n) for n in nodes)


def _insertion_run(
    runs: list[_Run], at: int, *, at_start: bool, at_end: bool
) -> tuple[_Run, int] | None:
    """The run that receives text inserted at offset *at*. Inside a run,
    that run. At a boundary between runs of one flow (inline formatting),
    the run before it, the way typing continues the preceding formatting.
    At a block or line boundary (two cells, two list items, a ``<br>``) the
    side the quoted old_text sits on: an insertion at the start of the
    match belongs to the run after the boundary, at its end to the run
    before; one between quoted words on both sides is refused."""
    before: tuple[_Run, int] | None = None
    after: tuple[_Run, int] | None = None
    offset = 0
    for run in runs:
        end = offset + len(run.node.data)
        if run.node.data:
            if offset < at < end:
                return (run, offset)
            if end == at:
                before = (run, offset)
            elif offset == at and after is None:
                after = (run, offset)
        offset = end
    if before is None or after is None:
        return before or after
    if before[0].flow == after[0].flow or at_end:
        return before
    if at_start:
        return after
    raise TextReplaceError(
        "the inserted text sits where one cell, list item or line ends and the next begins, "
        "so which one it belongs to is ambiguous: quote old_text only from the side it "
        "belongs to (insert at the start or end of the quoted text)"
    )


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
