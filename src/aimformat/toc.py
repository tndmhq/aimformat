"""The table of contents: one compact entry per section, and section reads.

Spec §8.1. A section opens at a top-level heading chunk (``h1``–``h6``, its
level is the heading's rank) or at a slide (level 1, named by the slide's
container id and titled by its first heading), and runs to the next opener of
the same or a higher level: it holds its subsections, and its counts include
theirs. Content before the first opener is an untitled level-1 section named
by its first construct's id; it ends at the first opener, whatever that one's
level. Headings inside a chunk or a slide open nothing.

The counts are informative — a verifier checks the entry shape and
``toc_doc_hash``, never a count:

- ``chunk_count``: the distinct chunk ids in the section. A list item or a
  table row counts one, a run counts once, a container counts nothing.
- ``word_count``: the whitespace-separated words of the chunks' visible text.
  Block, item, row and cell boundaries and ``<br>`` separate words; inline
  markup does not; images, ``svg``, ``template``, ``script`` and ``style``
  contribute nothing.

Pure functions over a body's top-level constructs (pending proposals are not
constructs), so every read path shares one definition of a section.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .canonical import serialize
from .dom import Element, Text

__all__ = [
    "SINCE",
    "Section",
    "is_entry",
    "is_legacy_entry",
    "outline",
    "section",
    "section_level",
    "visible_text",
]

#: The spec version that introduced the compact entry and the TOC-only meta
#: block. Writers build and refresh the TOC only in documents declaring it.
SINCE = "0.6"
HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

# Phrasing markup: words run across it ("Ac<strong>me</strong>" is one word).
# Every other element is a block, item, row or cell boundary, or a <br>.
_INLINE = frozenset({"a", "b", "code", "em", "i", "mark", "s", "span", "strong", "sub", "sup", "u"})
# Images (raster or packed) carry no words; template, script and style are
# never content.
_SILENT = frozenset({"img", "svg", "template", "script", "style"})


@dataclass(frozen=True)
class Section:
    """One section of the outline, read whole (spec §8.1).

    ``id`` names the construct that opens it: a heading chunk, a slide, or,
    for the untitled section before the first heading, the first construct.
    ``ids`` lists every chunk and container id it holds in document order, a
    container before its items. ``text`` is the visible text, one line per
    chunk (an empty chunk is an empty line): the words ``word_count`` counts.
    ``html`` is the section's markup exactly as the file writes it: its
    top-level constructs, each starting on its own line. Pending proposals
    are not part of a section.
    """

    id: str
    level: int
    title: str
    word_count: int
    chunk_count: int
    ids: tuple[str, ...]
    text: str
    html: str

    def entry(self) -> dict:
        """The section's TOC entry, as :meth:`AimDocument.outline` lists it."""
        return {
            "chunk_count": self.chunk_count,
            "id": self.id,
            "level": self.level,
            "title": self.title,
            "word_count": self.word_count,
        }


def visible_text(el: Element) -> str:
    """The words a reader sees in *el*, whitespace-collapsed."""
    if el.tag in _SILENT:
        return ""
    parts: list[str] = []
    _walk(el, parts)
    return " ".join("".join(parts).split())


def _walk(el: Element, parts: list[str]) -> None:
    for child in el.children:
        if isinstance(child, Text):
            parts.append(child.data)
        elif isinstance(child, Element):
            if child.tag in _INLINE:
                _walk(child, parts)
                continue
            parts.append(" ")
            if child.tag not in _SILENT:
                _walk(child, parts)
                parts.append(" ")


def section_level(top: Element) -> int | None:
    """The level of the section the top-level construct *top* opens, or
    None: an addressable heading chunk opens one at its rank, a slide (a
    container) at level 1."""
    if top.tag in HEADINGS and top.chunk_id:
        return int(top.tag[1])
    if top.tag == "aim-slide" and top.container_id:
        return 1
    return None


@dataclass(frozen=True)
class _Unit:
    """One top-level construct: its ids in document order (a container before
    its items, a run's id once per member) and the visible text of every
    chunk member element it holds."""

    ids: tuple[str, ...]
    texts: tuple[tuple[str, str], ...]  # (chunk id, visible text)


def _unit(top: Element) -> _Unit:
    ids: list[str] = []
    texts: list[tuple[str, str]] = []

    def visit(el: Element) -> None:
        if el.tag == "template":
            return
        cid = el.chunk_id
        if cid:
            ids.append(cid)
            texts.append((cid, visible_text(el)))
            return  # chunks never nest (§4.3)
        if el.container_id:
            ids.append(el.container_id)
        for child in el.elements():
            visit(child)

    visit(top)
    return _Unit(tuple(ids), tuple(texts))


def _lines(units: Sequence[_Unit]) -> list[str]:
    """One line of visible text per distinct chunk, in document order; the
    members of a run share their chunk's line."""
    lines: dict[str, list[str]] = {}
    for unit in units:
        for cid, text in unit.texts:
            lines.setdefault(cid, []).append(text)
    return [" ".join(t for t in parts if t) for parts in lines.values()]


@dataclass(frozen=True)
class _Span:
    """Where one section sits among the top-level constructs."""

    id: str
    level: int
    title: str
    start: int
    end: int  # exclusive


def _spans(constructs: Sequence[Element]) -> list[_Span]:
    """Every section, in document order. An id names at most one section: a
    heading that repeats an earlier section's id (a duplicate id, which lint
    reports) opens none."""
    starts: list[tuple[int, int, str]] = []  # (index, level, id)
    preamble: str | None = None
    taken: set[str] = set()
    for i, top in enumerate(constructs):
        level = section_level(top)
        cid = top.chunk_id or top.container_id
        if level is not None and cid and cid not in taken:
            starts.append((i, level, cid))
            taken.add(cid)
        elif not starts and preamble is None and cid:
            preamble = cid  # the first construct with an id names the preamble
            taken.add(cid)
    n = len(constructs)
    # one pass: an opener closes every open section of its level or deeper
    # (the open levels only ever rise, so those sit on top of the stack)
    ends = [n] * len(starts)
    open_: list[int] = []
    for j, (i, level, _) in enumerate(starts):
        while open_ and starts[open_[-1]][1] >= level:
            ends[open_.pop()] = i
        open_.append(j)
    spans: list[_Span] = []
    if preamble is not None:
        spans.append(_Span(preamble, 1, "", 0, starts[0][0] if starts else n))
    for (i, level, cid), end in zip(starts, ends, strict=True):
        spans.append(_Span(cid, level, _title(constructs[i]), i, end))
    return spans


def _title(top: Element) -> str:
    if top.tag == "aim-slide":
        heading = top.find(lambda e: e.tag in HEADINGS)
        return visible_text(heading) if heading is not None else ""
    return visible_text(top)


def _entry(span: _Span, lines: Sequence[str]) -> dict:
    return {
        "chunk_count": len(lines),
        "id": span.id,
        "level": span.level,
        "title": span.title,
        "word_count": sum(len(line.split()) for line in lines),
    }


def outline(constructs: Sequence[Element]) -> list[dict]:
    """The TOC entries of a body, in document order (§8.1)."""
    units = [_unit(top) for top in constructs]
    return [_entry(s, _lines(units[s.start : s.end])) for s in _spans(constructs)]


def section(constructs: Sequence[Element], section_id: str) -> Section | None:
    """The section *section_id* opens, or None when it opens none."""
    span = next((s for s in _spans(constructs) if s.id == section_id), None)
    if span is None:
        return None
    tops = constructs[span.start : span.end]
    units = [_unit(top) for top in tops]
    lines = _lines(units)
    entry = _entry(span, lines)
    return Section(
        id=span.id,
        level=span.level,
        title=span.title,
        word_count=entry["word_count"],
        chunk_count=entry["chunk_count"],
        ids=tuple(dict.fromkeys(i for unit in units for i in unit.ids)),
        text="\n".join(lines),
        html="\n".join(serialize(top) for top in tops),
    )


def _count(value: object) -> bool:
    return type(value) is int and value >= 0


def is_entry(obj: object) -> bool:
    """Whether *obj* is a TOC entry of the v0.6 shape. Unknown keys are
    ignored, as everywhere in the format's JSON, except that an entry
    carrying a ``chunks`` list is read as the pre-0.6 shape (§8.1), so it is
    not one; a ``chunks`` value of any other type is an unknown field."""
    if not isinstance(obj, dict) or isinstance(obj.get("chunks"), list):
        return False
    sid, level = obj.get("id"), obj.get("level")
    return (
        isinstance(sid, str)
        and bool(sid)
        and isinstance(obj.get("title"), str)
        and type(level) is int
        and 1 <= level <= 6
        and _count(obj.get("chunk_count"))
        and _count(obj.get("word_count"))
    )


def is_legacy_entry(obj: object) -> bool:
    """Whether *obj* is a TOC entry of the pre-0.6 shape
    ``{title, level, chunks}``: valid below v0.6, stale from v0.6 on."""
    return (
        isinstance(obj, dict)
        and isinstance(obj.get("title"), str)
        and type(obj.get("level")) is int
        and isinstance(obj.get("chunks"), list)
    )
