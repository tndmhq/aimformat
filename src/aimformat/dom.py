"""Minimal DOM for .aim documents.

A deliberately small tree — ``Element`` / ``Text`` / ``Comment`` — built by an
``html.parser``-based reader. It is *not* a general HTML5 tree builder: .aim
canonical form (spec §11) writes explicit markup (no implied tags, no tag
soup), so a transparent parser that reports the file exactly as written is
both sufficient and desirable — the canonical serializer must be able to
reproduce the input byte-for-byte.

Raw-text elements (``script``/``style``) keep their content in ``raw``.
The HTML tokenizer lowercases attribute names; foreign (SVG) attribute case
is re-adjusted at serialization time from the registry table, mirroring what
browser tree-construction does.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from html.parser import HTMLParser
from typing import Union

from .errors import ParseError
from .registry import REGISTRY

Nodeish = Union["Element", "Text", "Comment"]


class Text:
    __slots__ = ("data",)

    def __init__(self, data: str):
        self.data = data

    def __repr__(self) -> str:
        return f"Text({self.data!r})"


class Comment:
    __slots__ = ("data",)

    def __init__(self, data: str):
        self.data = data

    def __repr__(self) -> str:
        return f"Comment({self.data!r})"


class Element:
    __slots__ = ("tag", "attrs", "children", "self_closing", "raw")

    def __init__(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]] | None = None,
        *,
        self_closing: bool = False,
    ):
        self.tag = tag
        self.attrs: list[tuple[str, str | None]] = list(attrs or [])
        self.children: list[Nodeish] = []
        self.self_closing = self_closing
        self.raw: str | None = None  # script/style raw content

    # -- attribute helpers ---------------------------------------------------
    def get(self, name: str, default: str | None = None) -> str | None:
        for k, v in self.attrs:
            if k == name:
                return v if v is not None else ""
        return default

    def has(self, name: str) -> bool:
        return any(k == name for k, _ in self.attrs)

    def set(self, name: str, value: str | None) -> None:
        for i, (k, _) in enumerate(self.attrs):
            if k == name:
                self.attrs[i] = (name, value)
                return
        self.attrs.append((name, value))

    def remove_attr(self, name: str) -> None:
        self.attrs = [(k, v) for k, v in self.attrs if k != name]

    # -- convenience ---------------------------------------------------------
    @property
    def chunk_id(self) -> str | None:
        return self.get("data-aim")

    @property
    def container_id(self) -> str | None:
        return self.get("data-aim-container")

    def elements(self) -> list[Element]:
        return [c for c in self.children if isinstance(c, Element)]

    def iter(self) -> Iterator[Element]:
        """Depth-first over this element and all element descendants."""
        yield self
        for c in self.elements():
            yield from c.iter()

    def find(self, pred: Callable[[Element], bool]) -> Element | None:
        return next((e for e in self.iter() if pred(e)), None)

    def find_all(self, pred: Callable[[Element], bool]) -> list[Element]:
        return [e for e in self.iter() if pred(e)]

    def text(self) -> str:
        """Concatenated text content (template/raw content excluded)."""
        parts: list[str] = []
        for c in self.children:
            if isinstance(c, Text):
                parts.append(c.data)
            elif isinstance(c, Element):
                parts.append(c.text())
        return "".join(parts)

    def __repr__(self) -> str:
        ident = self.chunk_id or self.container_id or self.get("id") or ""
        return f"<{self.tag}{' ' + ident if ident else ''}>"


class Fragment:
    """Root holder for parsed content: a doctype plus top-level nodes."""

    def __init__(self) -> None:
        self.doctype: str | None = None
        self.children: list[Nodeish] = []

    def elements(self) -> list[Element]:
        return [c for c in self.children if isinstance(c, Element)]


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fragment = Fragment()
        self._stack: list[Element] = []
        self._raw: Element | None = None

    # ----------------------------------------------------------------------
    def _append(self, node: Nodeish) -> None:
        if self._stack:
            self._stack[-1].children.append(node)
        else:
            self.fragment.children.append(node)

    def handle_decl(self, decl: str) -> None:
        self.fragment.doctype = decl

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self._raw is not None:
            return
        el = Element(tag, attrs)
        self._append(el)
        if tag in ("script", "style"):
            el.raw = ""
            self._raw = el
            self._stack.append(el)
        elif tag not in REGISTRY.void_elements:
            self._stack.append(el)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        if self._raw is not None:
            return
        self._append(Element(tag, attrs, self_closing=True))

    def handle_endtag(self, tag: str) -> None:
        if self._raw is not None:
            if tag == self._raw.tag:
                self._raw = None
                self._stack.pop()
            return
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return
        raise ParseError(f"unmatched closing tag </{tag}>")

    def handle_data(self, data: str) -> None:
        if self._raw is not None:
            self._raw.raw = (self._raw.raw or "") + data
        else:
            self._append(Text(data))

    def handle_comment(self, data: str) -> None:
        self._append(Comment(data))


def parse_html(text: str) -> Fragment:
    """Parse *text* into a :class:`Fragment` (document or fragment)."""
    reader = _Reader()
    try:
        reader.feed(text)
        reader.close()
    except ParseError:
        raise
    except Exception as exc:  # html.parser raises assorted ValueErrors
        raise ParseError(str(exc)) from exc
    if reader._raw is not None:
        raise ParseError(f"unterminated <{reader._raw.tag}> block")
    if reader._stack:
        raise ParseError(f"unclosed <{reader._stack[-1].tag}>")
    return reader.fragment


def parse_fragment(markup: str) -> list[Nodeish]:
    """Parse a body-context fragment; returns its top-level nodes."""
    return parse_html(markup).children


def deep_copy(node: Nodeish) -> Nodeish:
    if isinstance(node, Text):
        return Text(node.data)
    if isinstance(node, Comment):
        return Comment(node.data)
    el = Element(node.tag, list(node.attrs), self_closing=node.self_closing)
    el.raw = node.raw
    el.children = [deep_copy(c) for c in node.children]
    return el
