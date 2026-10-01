"""Agent read views: outline, skeleton, lossy text, exact chunks, search.

What a read costs should follow what the agent needs, not the size of the
file. These pure functions take an :class:`~aimformat.document.AimDocument`
and return either records (for programs) or compact text (for models):

- :func:`units` — every addressable unit (chunk or container) in document
  order, each with its depth, tags, classes and lossy text;
- :func:`numbering_labels` — the outline numbers (§3.8) the stylesheet
  draws, which exist only as CSS counters in the file;
- :func:`outline` / :func:`render_toc` — headings, slides and
  ``num-1``/``num-2`` blocks with the id range each covers;
- :func:`render_skeleton` — every id with its tag, classes and first words;
- :func:`render_text` — the **lossy** reading view keyed by id;
- :func:`render_chunks` — the exact serialization of chosen units;
- :func:`search` — lexical (BM25) ranking over unit text.

The text view is for reading only: it drops classes, styles and attributes,
and a ``modify`` replaces a chunk's whole serialization (§6.6), so an edit
must start from :func:`render_chunks`, never from the text view.

Long ``data:`` URIs are elided to ``[elided: <size>, sha256:<16 hex>]``
stubs (§8.3); the write paths that elide (MCP, CLI) restore them on write.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .canonical import serialize, serialize_run
from .document import AimDocument, Proposal
from .dom import Element, Text
from .errors import AimError
from .events import Actor

__all__ = [
    "Hit",
    "OutlineEntry",
    "Unit",
    "elide",
    "full_projection",
    "numbering_labels",
    "outline",
    "render_chunks",
    "render_search",
    "render_skeleton",
    "render_text",
    "render_toc",
    "resolve_refs",
    "search",
    "short_header",
    "stub_for",
    "units",
]

HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
MAX_REFS = 200
MAX_WORDS = 50
MAX_QUERY_CHARS = 512
MAX_QUERY_TERMS = 32
MAX_K = 50
LOSSY_NOTICE = (
    "(text view: lossy, for reading only. Classes, styles and attributes are "
    "omitted. Fetch exact HTML with mode=chunks before you edit a chunk.)"
)

# any long data: URI — the payload alphabet varies (base64 commas, percent
# escapes), so match every non-delimiter run rather than a fixed alphabet
DATA_URI = re.compile(r"data:[^\"'\s]{64,}")
STUB = re.compile(r"\[elided: [^,\]]{1,16}, sha256:([0-9a-f]{16})\]")

_NUM = re.compile(r"^num-([1-9])$")
_WS = re.compile(r"\s+")
_BLOCKS = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "section",
        "div",
        "blockquote",
        "figure",
        "figcaption",
        "pre",
        "hr",
        "aim-page-break",
        "ul",
        "ol",
        "li",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "aim-slide",
    }
)
# placeholders for view syntax inside content lines, so escaping a content
# line that starts with "[" never touches the view's own "[image: …]"
_OPEN, _CLOSE = "\x01", "\x02"
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


# --------------------------------------------------------------------------- records
@dataclass(frozen=True)
class Unit:
    """One addressable unit — a chunk (possibly a run) or a container — in
    document order (containers before their items)."""

    id: str
    kind: Literal["chunk", "container"]
    container: str  # "body", a container id, or a slide id
    depth: int  # container nesting depth (0 = body)
    tags: tuple[str, ...]  # member tags (a run has several)
    classes: tuple[str, ...]  # class tokens of the first member
    styled: bool  # the first member carries an inline style
    label: str | None  # outline numbering label (§3.8), if any
    text: str  # lossy text view lines, newline-joined


@dataclass(frozen=True)
class OutlineEntry:
    """One outline entry: a heading, a slide, a ``num-1``/``num-2`` block, or
    the untitled range before the first entry. ``first..last`` is inclusive
    over unit order and can be passed to :func:`render_chunks` as is."""

    first: str
    last: str
    level: int
    kind: Literal["heading", "slide", "numbered", "untitled"]
    title: str
    label: str | None
    units: int


@dataclass(frozen=True)
class Hit:
    """One search hit: a chunk id, its score, the nearest preceding section
    title, a snippet around the first match, and pending cards on it."""

    id: str
    score: float
    section: str | None
    snippet: str
    pending: tuple[str, ...]


# --------------------------------------------------------------------------- elision
def _size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{round(n / 1024)}KB"
    return f"{n / (1024 * 1024):.1f}MB"


def stub_for(uri: str) -> str:
    """The §8.3 elision stub for one data URI (size + content-hash prefix)."""
    digest = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:16]
    return f"[elided: {_size(len(uri.encode('utf-8')))}, sha256:{digest}]"


def elide(html: str) -> str:
    """Replace every long ``data:`` URI with its §8.3 stub. Tools that elide
    on read must restore their stubs on write (or refuse them): a stub
    written into a document destroys the asset it stood for."""
    return DATA_URI.sub(lambda m: stub_for(m.group(0)), html)


def _elide_value(value: Any) -> Any:
    """Elide data URIs anywhere in an event field — a baseline snapshot is a
    nested object whose body lines carry the same blobs a payload would."""
    if isinstance(value, str):
        return elide(value)
    if isinstance(value, list):
        return [_elide_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _elide_value(v) for k, v in value.items()}
    return value


def _actor_obj_str(obj: Any) -> str | None:
    """:func:`_actor` for an actor object read from history JSON."""
    if not isinstance(obj, dict) or "type" not in obj:
        return None
    value = obj.get("model") or obj.get("id")
    return f"{obj['type']}:{value}" if value else str(obj["type"])


def review_view(doc: AimDocument) -> dict[str, Any] | None:
    """The review policy as data for an agent (spec §5.6): None when off,
    ``{"agents": "auto", "by": "human:…"}`` when proposals from agents and
    tools are accepted as they arrive, ``{"malformed": …}`` when unreadable."""
    try:
        policy = doc.review_policy
    except AimError as exc:
        return {"malformed": str(exc)}
    if policy is None:
        return None
    return {"agents": policy.agents, "by": _actor(policy.by)}


def recent_auto_batches(doc: AimDocument, limit: int = 3) -> list[dict[str, Any]]:
    """The newest auto-accepted batches (the ids ``aim_undo`` takes)."""
    try:
        batches = doc.auto_accepted_batches(max_batches=limit)
    except AimError:
        return []
    return [
        {
            "batch": b["batch"],
            "via": b["via"],
            "decided_by": _actor_obj_str(b["decided_by"]),
            "t": b["t"],
            "targets": b["targets"],
            "undone": b["undone"],
            "revertable": b["revertable"],
        }
        for b in batches
    ]


def full_projection(doc: AimDocument, *, include_history: bool = False) -> dict:
    """The whole-document projection (``aim_read`` mode ``full``): title,
    language, spec version, seq, doc_hash, summary with its staleness flag,
    the table of contents, every chunk's HTML, the pending lane, the review
    policy and the recent auto-accepted batches — data URIs elided, the
    stylesheet never included. The table of contents is
    never stale: the stored cache when it matches the document, else derived
    from the headings (``toc_source`` says which; both are None when the
    document has no heading or slide to outline)."""
    summary = None
    meta = doc.meta
    if meta and isinstance(meta.get("summary"), dict):
        summary = {
            "text": meta["summary"].get("text"),
            "stale": meta["summary"].get("doc_hash") != doc.doc_hash,
        }
    # A stale or missing cache is never served: derive the outline live
    # (O(n)) — but only when the body HAS one. Without a heading or a
    # slide the outline is one untitled entry repeating every chunk id
    # the "chunks" list already carries: tokens for nothing.
    toc: Any = None
    toc_source: str | None = None
    if doc.toc_is_fresh():
        toc, toc_source = (meta or {}).get("toc"), "cache"
    elif doc._has_outline():
        toc, toc_source = doc.outline(), "derived"
    out: dict = {
        "title": doc.title,
        "lang": doc.lang,
        "spec_version": doc.spec_version,
        "seq": doc.seq,
        "doc_hash": doc.doc_hash,
        "summary": summary,
        "toc": toc,
        "toc_source": toc_source,
        "chunks": [
            {"id": c.id, "container": c.container, "html": elide(c.html)} for c in doc.chunks
        ],
        "proposals": [
            {
                "id": p.id,
                "action": p.action,
                "target": p.target,
                "author": _actor(p.author),
                "explanation": p.explanation,
                "payload_html": elide(p.payload_html) if p.payload_html else None,
                "batch": p.batch,
            }
            for p in doc.proposals
        ],
        "review": review_view(doc),
        "recent_auto_batches": recent_auto_batches(doc),
    }
    if include_history:
        # elided like every other projection: raw add/modify payloads would
        # dump full base64 data URIs into model context
        out["history"] = [{k: _elide_value(v) for k, v in ev.data.items()} for ev in doc.history]
    return out


# --------------------------------------------------------------------------- walk
@dataclass
class _U:
    id: str
    kind: str
    container: str
    depth: int
    els: list[Element]


def _walk(doc: AimDocument) -> list[_U]:
    out: list[_U] = []

    def visit(kids: list[Element], container: str, depth: int) -> None:
        i = 0
        while i < len(kids):
            el = kids[i]
            if el.tag == "template":
                i += 1
                continue
            if el.chunk_id:
                run = [el]
                while i + 1 < len(kids) and kids[i + 1].chunk_id == el.chunk_id:
                    i += 1
                    run.append(kids[i])
                out.append(_U(el.chunk_id, "chunk", container, depth, run))
            elif el.container_id:
                out.append(_U(el.container_id, "container", container, depth, [el]))
                visit(el.elements(), el.container_id, depth + 1)
            else:  # table shells, unmarked wrappers: transparent
                visit(el.elements(), container, depth)
            i += 1

    visit(doc._state.constructs(), "body", 0)
    return out


def _classes(el: Element) -> list[str]:
    return (el.get("class") or "").split()


def _num_level(el: Element) -> int | None:
    # several num-k classes on one block: the generated stylesheet emits the
    # levels in order, so the deepest one wins the cascade
    levels = [int(m.group(1)) for tok in _classes(el) if (m := _NUM.match(tok))]
    return max(levels) if levels else None


def _label_map(doc: AimDocument) -> dict[int, str]:
    """``id(element) -> label`` for every outline-numbered block, simulating
    the generated stylesheet: counters instantiated once on ``body``;
    ``num-k`` increments level k and zeroes deeper levels; ``num-restart``
    sets level k to 1 (no increment); ``data-aim-num-prefix`` replaces the
    chain with prefix + counter(k); level 1 alone gets a ``.`` suffix."""
    counters = [0] * 10
    out: dict[int, str] = {}

    def visit(el: Element) -> None:
        if el.tag == "template":
            return
        k = _num_level(el)
        if k is not None:
            if "num-restart" in _classes(el):
                counters[k] = 1
            else:
                counters[k] += 1
            for j in range(k + 1, 10):
                counters[j] = 0
            prefix = el.get("data-aim-num-prefix")
            if prefix is not None:
                out[id(el)] = f"{prefix}{counters[k]}"
            elif k == 1:
                out[id(el)] = f"{counters[1]}."
            else:
                out[id(el)] = ".".join(str(counters[j]) for j in range(1, k + 1))
        for child in el.elements():
            visit(child)

    for top in doc._state.constructs():
        visit(top)
    return out


def _roman(n: int) -> str:
    table = (
        (1000, "m"),
        (900, "cm"),
        (500, "d"),
        (400, "cd"),
        (100, "c"),
        (90, "xc"),
        (50, "l"),
        (40, "xl"),
        (10, "x"),
        (9, "ix"),
        (5, "v"),
        (4, "iv"),
        (1, "i"),
    )
    if n <= 0:
        return str(n)
    s = ""
    for value, sym in table:
        while n >= value:
            s, n = s + sym, n - value
    return s


def _alpha(n: int) -> str:
    if n <= 0:
        return str(n)
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(97 + r) + s
    return s


def _marker_map(doc: AimDocument) -> dict[int, str]:
    """``id(li) -> marker`` for every list item: ``ol start`` honoured; the
    alpha/roman classes set the counter style; ``list-paren`` / ``list-bare``
    the suffix; ``list-multilevel`` the dotted chain of ancestor items;
    ``ul`` items get ``-``; ``list-none`` no marker."""
    out: dict[int, str] = {}

    def item_marker(lst: Element, chain: list[int], ordinal: int) -> str:
        cls = set(_classes(lst))
        suffix = ")" if "list-paren" in cls else None
        if "list-bare" in cls:
            suffix = ""
        if "list-multilevel" in cls:
            return ".".join(str(n) for n in [*chain, ordinal]) + (suffix or "")
        if "list-none" in cls:
            return ""
        if "list-lower-alpha" in cls:
            value = _alpha(ordinal)
        elif "list-upper-alpha" in cls:
            value = _alpha(ordinal).upper()
        elif "list-lower-roman" in cls:
            value = _roman(ordinal)
        elif "list-upper-roman" in cls:
            value = _roman(ordinal).upper()
        elif (
            lst.tag == "ol" or "list-decimal" in cls or "list-paren" in cls or "list-bare" in cls
        ) and "list-disc" not in cls:
            value = str(ordinal)
        else:
            return "-"
        return value + (suffix if suffix is not None else ".")

    def visit(el: Element, chain: list[int]) -> None:
        if el.tag == "template":
            return
        if el.tag in ("ul", "ol"):
            try:
                ordinal = int(el.get("start") or 1) if el.tag == "ol" else 1
            except ValueError:
                ordinal = 1
            for child in el.elements():
                if child.tag == "li":
                    out[id(child)] = item_marker(el, chain, ordinal)
                    for sub in child.elements():
                        visit(sub, [*chain, ordinal])
                    ordinal += 1
                else:
                    visit(child, chain)
            return
        for child in el.elements():
            visit(child, chain)

    for top in doc._state.constructs():
        visit(top, [])
    return out


# --------------------------------------------------------------------------- rendering
@dataclass
class _Ctx:
    labels: dict[int, str]
    markers: dict[int, str]
    marks: bool = True  # **bold**, *italic*, ~~struck~~


def _image(label: str | None) -> str:
    return f"{_OPEN}image: {_clean(label or '')}{_CLOSE}"


def _clean(s: str) -> str:
    return _WS.sub(" ", _CONTROL.sub("", s)).strip()


def _wrap(inner: str, mark: str) -> str:
    core = inner.strip()
    if not core:
        return inner
    lead = " " if inner[:1].isspace() else ""
    trail = " " if inner[-1:].isspace() else ""
    return f"{lead}{mark}{core}{mark}{trail}"


def _inline_raw(el: Element, ctx: _Ctx) -> str:
    parts: list[str] = []
    for ch in el.children:
        if isinstance(ch, Text):
            parts.append(_CONTROL.sub("", ch.data))
            continue
        if not isinstance(ch, Element):
            continue
        tag = ch.tag
        if tag == "br":
            parts.append(" / ")
        elif tag == "img":
            parts.append(_image(ch.get("alt")))
        elif tag == "svg":
            if ch.get("role") == "img":
                parts.append(_image(ch.get("aria-label")))
        elif tag in ("template", "script", "style"):
            continue
        else:
            inner = _inline_raw(ch, ctx)
            if ctx.marks and tag in ("strong", "b"):
                inner = _wrap(inner, "**")
            elif ctx.marks and tag in ("em", "i"):
                inner = _wrap(inner, "*")
            if ctx.marks and (tag == "s" or "line-through" in _classes(ch)):
                inner = _wrap(inner, "~~")
            if tag in _BLOCKS:  # a block inside inline context: keep it apart
                inner = f" {inner} "
            parts.append(inner)
    return "".join(parts)


def _inline(el: Element, ctx: _Ctx) -> str:
    return _WS.sub(" ", _inline_raw(el, ctx)).strip()


def _lead(el: Element, ctx: _Ctx) -> str:
    label = ctx.labels.get(id(el))
    return f"{label} " if label else ""


def _flow(el: Element, ctx: _Ctx) -> list[str]:
    """Lines for mixed content: inline runs become lines, block children
    render by their own rules."""
    lines: list[str] = []
    buf = Element("span")

    def flush() -> None:
        text = _inline(buf, ctx)
        if text:
            lines.append(text)
        buf.children = []

    for ch in el.children:
        if isinstance(ch, Element) and ch.tag in ("ul", "ol"):
            flush()  # nested list: its items, one level deeper
            lines.extend("  " + line for line in _list_lines(ch, ctx))
        elif isinstance(ch, Element) and ch.tag == "table":
            flush()
            lines.extend(_block(ch, ctx)[1:])
        elif isinstance(ch, Element) and ch.tag in _BLOCKS:
            flush()
            lines.extend(_block(ch, ctx))
        elif isinstance(ch, Element) and ch.tag == "img" and el.tag == "figure":
            flush()
            lines.append(_image(ch.get("alt")))
        else:
            buf.children.append(ch)
    flush()
    return lines


def _list_lines(lst: Element, ctx: _Ctx) -> list[str]:
    out: list[str] = []
    for item in lst.elements():
        if item.tag == "li":
            out.extend(_block(item, ctx))
    return out


def _cell(td: Element, ctx: _Ctx) -> str:
    text = " / ".join(line.strip() for line in _flow(td, ctx) if line.strip())
    for attr, unit in (("colspan", "cols"), ("rowspan", "rows")):
        try:
            n = int(td.get(attr) or 1)
        except ValueError:
            n = 1
        if n > 1:
            text = f"{text} {{{n} {unit}}}".strip()
    return text


def _block(el: Element, ctx: _Ctx) -> list[str]:
    """Lines for one block element; the first is the unit's own line when
    *el* is a unit root, the rest are continuation lines (relative indent
    already applied for nested structure)."""
    tag = el.tag
    if tag in HEADINGS:
        return ["#" * int(tag[1]) + " " + _lead(el, ctx) + _inline(el, ctx)]
    if tag == "p":
        return [(_lead(el, ctx) + _inline(el, ctx)).strip()]
    if tag == "li":
        marker = ctx.markers.get(id(el), "-")
        body = _flow(el, ctx)
        if body and not body[0].startswith("  "):
            return [f"{marker} {body[0]}".strip()] + body[1:]
        return [marker] + body
    if tag in ("ul", "ol"):
        return ["<list>"] + _list_lines(el, ctx)
    if tag == "tr":
        cells = [_cell(td, ctx) for td in el.elements() if td.tag in ("td", "th")]
        return ["| " + " | ".join(cells) + " |"]
    if tag in ("thead", "tbody", "tfoot"):
        return [line for row in el.elements() for line in _block(row, ctx)]
    if tag == "table":
        rows = [line for child in el.elements() for line in _block(child, ctx)]
        return ["<table>"] + rows
    if tag == "aim-page-break":
        return ["--- page break ---"]
    if tag == "hr":
        return ["---"]
    if tag == "pre":
        # every line break a reader may honour ends a view line (\r, \x85,
        # \u2028…), so none can start a forged "[id]" line mid-line
        text = _CONTROL.sub("", el.text()).rstrip("\n")
        return text.splitlines() or [""]
    if tag == "blockquote":
        return ["> " + line for line in (_flow(el, ctx) or [""])]
    if tag == "figcaption":
        return ["caption: " + _inline(el, ctx)]
    if tag in ("section", "div", "figure", "aim-slide"):
        return _flow(el, ctx) or [""]
    return [_inline(el, ctx)]


def _unit_lines(u: _U, ctx: _Ctx) -> list[str]:
    if u.kind == "container":
        root = u.els[0]
        name = {"ul": "list", "ol": "list", "table": "table", "aim-slide": "slide"}.get(
            root.tag, root.tag
        )
        return [f"<{name}>"]
    lines: list[str] = []
    for member in u.els:
        lines.extend(_block(member, ctx))
    return lines or [""]


def _escape(line: str) -> str:
    # document text must never read as the view's own "[id]" syntax, even
    # behind leading spaces (which would mimic a deeper unit's indentation)
    stripped = line.lstrip(" ")
    if stripped.startswith("["):
        line = line[: len(line) - len(stripped)] + "\\" + stripped
    return line.replace(_OPEN, "[").replace(_CLOSE, "]")


def _emit(u: _U, lines: list[str]) -> list[str]:
    indent = "  " * u.depth
    first, *rest = lines
    out = [f"{indent}[{u.id}] {_escape(first)}".rstrip()]
    out += [f"{indent}  {_escape(line)}".rstrip() for line in rest]
    return out


def _plain(lines: list[str]) -> str:
    return " ".join(line.replace(_OPEN, "[").replace(_CLOSE, "]") for line in lines).strip()


class _View:
    """One pass over a document, shared by every renderer."""

    def __init__(self, doc: AimDocument, *, marks: bool = True):
        self.doc = doc
        self.walk = _walk(doc)
        self.ctx = _Ctx(_label_map(doc), _marker_map(doc), marks)
        self._lines: dict[int, list[str]] = {}

    def lines(self, i: int) -> list[str]:
        if i not in self._lines:
            self._lines[i] = _unit_lines(self.walk[i], self.ctx)
        return self._lines[i]

    def label(self, u: _U) -> str | None:
        return self.ctx.labels.get(id(u.els[0])) if u.kind == "chunk" else None

    def descendants(self, i: int) -> range:
        """Indices of the units nested under container unit *i*."""
        depth = self.walk[i].depth
        j = i + 1
        while j < len(self.walk) and self.walk[j].depth > depth:
            j += 1
        return range(i + 1, j)


# --------------------------------------------------------------------------- public
def units(doc: AimDocument) -> list[Unit]:
    """Every chunk and container in document order (pre-order)."""
    view = _View(doc)
    out = []
    for i, u in enumerate(view.walk):
        first = u.els[0]
        out.append(
            Unit(
                id=u.id,
                kind="chunk" if u.kind == "chunk" else "container",
                container=u.container,
                depth=u.depth,
                tags=tuple(e.tag for e in u.els),
                classes=tuple(_classes(first)),
                styled=first.has("style"),
                label=view.label(u),
                text="\n".join(_escape(line) for line in view.lines(i)),
            )
        )
    return out


def numbering_labels(doc: AimDocument) -> dict[str, str]:
    """``chunk id -> rendered outline label`` (§3.8) for every chunk whose
    root carries ``num-k`` — e.g. ``{"tl3nprt2": "1.1.8"}``."""
    labels = _label_map(doc)
    out: dict[str, str] = {}
    for u in _walk(doc):
        if u.kind == "chunk" and id(u.els[0]) in labels:
            out[u.id] = labels[id(u.els[0])]
    return out


def _title(view: _View, i: int) -> str:
    u = view.walk[i]
    if u.kind == "container":  # a slide: titled by its first heading
        heading = u.els[0].find(lambda e: e.tag in HEADINGS)
        plain = _Ctx(view.ctx.labels, view.ctx.markers, marks=False)
        return _clean(_plain([_inline(heading, plain)])) if heading is not None else ""
    plain = _Ctx(view.ctx.labels, view.ctx.markers, marks=False)
    return _clean(_plain([_inline(u.els[0], plain)]))


def _outline(view: _View) -> list[tuple[int, OutlineEntry]]:
    starts: list[tuple[int, int, str, str, str | None]] = []  # (index, level, kind, title, label)
    base, base_num = 0, 0
    for i, u in enumerate(view.walk):
        if u.depth != 0:
            continue
        root = u.els[0]
        if u.kind == "chunk" and root.tag in HEADINGS:
            level = int(root.tag[1])
            base, base_num = level, (_num_level(root) or 0)
            starts.append((i, level, "heading", _title(view, i), view.label(u)))
        elif u.kind == "container" and root.tag == "aim-slide":
            base, base_num = 1, 0
            starts.append((i, 1, "slide", _title(view, i), None))
        elif u.kind == "chunk" and _num_level(root) in (1, 2):
            k = _num_level(root) or 1
            level = base + max(1, k - base_num)
            words = _title(view, i).split()
            label = view.label(u)
            title = " ".join(words[:8]) + (" …" if len(words) > 8 else "")
            starts.append((i, level, "numbered", title, label))
    n = len(view.walk)
    entries: list[tuple[int, OutlineEntry]] = []
    if not n:
        return entries
    if not starts or starts[0][0] > 0:
        end = (starts[0][0] if starts else n) - 1
        entries.append(
            (
                0,
                OutlineEntry(view.walk[0].id, view.walk[end].id, 1, "untitled", "", None, end + 1),
            )
        )
    for j, (i, level, kind, title, label) in enumerate(starts):
        end = n - 1
        for i2, level2, *_ in starts[j + 1 :]:
            if level2 <= level:
                end = i2 - 1
                break
        entries.append(
            (
                i,
                OutlineEntry(
                    view.walk[i].id,
                    view.walk[end].id,
                    level,
                    kind,  # type: ignore[arg-type]
                    title,
                    label,
                    end - i + 1,
                ),
            )
        )
    return entries


def outline(doc: AimDocument) -> list[OutlineEntry]:
    """The outline, derived from the body on each call (not from the cache):
    top-level headings and slides, plus ``num-1``/``num-2`` blocks nested
    below the current heading. Each entry's range runs to the unit before
    the next entry of the same or a higher rank."""
    return [e for _, e in _outline(_View(doc, marks=False))]


def _summary(doc: AimDocument) -> str:
    meta = doc.meta or {}
    s = meta.get("summary")
    if not isinstance(s, dict) or not s.get("text"):
        return ""
    stale = " [stale]" if s.get("doc_hash") != doc.doc_hash else ""
    return f' | summary: "{_clean(str(s["text"]))}"{stale}'


def _header(doc: AimDocument, view: _View) -> str:
    chunks = sum(1 for u in view.walk if u.kind == "chunk")
    return (
        f"{_clean(doc.title) or '(untitled)'} | spec {doc.spec_version} | seq {doc.seq} | "
        f"{chunks} chunks | {len(doc.proposals)} pending{_review(doc)}{_summary(doc)}"
    )


def _review(doc: AimDocument) -> str:
    """`` | review: auto`` when proposals from agents are applied as they
    arrive (§5.6) — a reader of any view must know its proposals will not wait."""
    view = review_view(doc)
    if view is None:
        return ""
    return " | review: malformed" if "malformed" in view else f" | review: {view['agents']}"


def short_header(doc: AimDocument) -> str:
    return f"seq {doc.seq} | {len(doc.proposals)} pending"


def render_toc(doc: AimDocument) -> str:
    view = _View(doc, marks=False)
    lines = [_header(doc, view)]
    entries = _outline(view)
    if not entries:
        lines.append("(empty document)")
    for _, e in entries:
        title = e.title or "(untitled)"
        label = f"{e.label} " if e.label else ""
        lines.append(f"[{e.first}..{e.last}] {'#' * e.level} {label}{title} ({e.units})")
    return "\n".join(lines)


def render_skeleton(doc: AimDocument, words: int = 8) -> str:
    """Every unit id with ``tag.classes{style} xN`` and its first *words*
    words, indented by container depth."""
    if not 0 <= words <= MAX_WORDS:
        raise ValueError(f"words must be between 0 and {MAX_WORDS}")
    view = _View(doc, marks=False)
    out = [_header(doc, view)]
    if not view.walk:
        out.append("(empty document)")
    for i, u in enumerate(view.walk):
        root = u.els[0]
        shape = root.tag + "".join(f".{c}" for c in _classes(root))
        if root.has("style"):
            shape += "{style}"
        if len(u.els) > 1:
            shape += f" x{len(u.els)}"
        indent = "  " * u.depth
        if u.kind == "container":
            n = len(view.descendants(i))
            out.append(f"{indent}[{u.id}] {shape} ({n} units)")
            continue
        text = _plain(view.lines(i))
        if root.tag in HEADINGS:
            text = text.lstrip("#").lstrip()
        ws = text.split()
        snippet = " ".join(ws[:words]) + (" …" if words and len(ws) > words else "")
        if snippet.startswith("["):
            snippet = "\\" + snippet
        out.append(f"{indent}[{u.id}] {shape} {snippet}".rstrip())
    return "\n".join(out)


def _actor(actor: Actor) -> str:
    # the author string comes from the file (or a tool argument) unchecked:
    # one line, so it cannot forge a view line of its own
    value = actor.model or actor.id
    return _clean(f"{actor.type}:{value}" if value else actor.type)


def _payload_text(p: Proposal, ctx: _Ctx) -> str:
    from .dom import parse_fragment

    if not p.payload_html:
        return ""
    lines: list[str] = []
    for node in parse_fragment(p.payload_html):
        if not isinstance(node, Element):
            continue
        if node.tag in ("style", "script"):
            lines.append(_clean(node.raw or ""))
        else:
            lines.extend(line.strip() for line in _block(node, ctx))
    words = _plain([" / ".join(line for line in lines if line)]).split()
    return " ".join(words[:40]) + (" …" if len(words) > 40 else "")


def _where(p: Proposal) -> str:
    anchor = f"after {p.anchor_after}" if p.anchor_after else "first"
    if p.action == "add":
        return f"into {p.anchor_container} {anchor}"
    if p.action == "move":
        return f"{p.target} to {p.anchor_container} {anchor}"
    return p.target or ""


def _card_line(p: Proposal, ctx: _Ctx, *, payload: bool) -> str:
    line = f"[{p.id}] {p.action} {_where(p)} by {_actor(p.author)}"
    if p.explanation:
        line += f": {_clean(p.explanation)}"
    if payload and p.action in ("add", "modify"):
        text = _payload_text(p, ctx)
        if text:
            line += f" → {text}"
    return line


def render_text(doc: AimDocument) -> str:
    """The lossy reading view: every unit as ``[id] text`` (indented by
    container depth), then the pending lane. Never an edit payload."""
    view = _View(doc)
    out = [_header(doc, view), LOSSY_NOTICE]
    if not view.walk:
        out.append("(empty document)")
    for i, u in enumerate(view.walk):
        out.extend(_emit(u, view.lines(i)))
    props = doc.proposals
    if props:
        out.append(f"## pending ({len(props)})")
        out.extend(_card_line(p, view.ctx, payload=True) for p in props)
    return "\n".join(out)


# --------------------------------------------------------------------------- refs
def _parse_refs(view: _View, doc: AimDocument, refs: Sequence[str]):
    if len(refs) > MAX_REFS:
        raise ValueError(f"at most {MAX_REFS} references per call (got {len(refs)})")
    index = {u.id: i for i, u in enumerate(view.walk)}
    explicit: set[int] = set()
    ranged: set[int] = set()
    singles: list[str] = []  # aim:theme / aim:doc
    cards: list[str] = []
    missing: list[str] = []
    pending_ids = {p.id for p in doc.proposals}
    for raw in refs:
        ref = raw.strip()
        if ".." in ref:
            a, _, b = ref.partition("..")
            a, b = a.strip(), b.strip()
            if a not in index or b not in index:
                missing.append(ref)
                continue
            ia, ib = index[a], index[b]
            if ia > ib:
                raise ValueError(f"reversed range {ref!r}: {a!r} comes after {b!r}")
            ranged.update(range(ia, ib + 1))
        elif ref in ("aim:theme", "aim:doc"):
            if doc._state.serial(ref) is None:
                missing.append(ref)
            elif ref not in singles:
                singles.append(ref)
        elif ref.startswith("p-"):
            if ref in pending_ids:
                if ref not in cards:
                    cards.append(ref)
            else:
                missing.append(ref)
        elif ref in index:
            explicit.add(index[ref])
        else:
            missing.append(ref)
    selected = explicit | ranged
    whole: set[int] = set()
    for i in sorted(selected):
        u = view.walk[i]
        if u.kind != "container":
            continue
        desc = view.descendants(i)
        if i in explicit or all(j in ranged for j in desc):
            whole.add(i)
    suppressed: set[int] = set()
    for i in whole:
        suppressed.update(view.descendants(i))
    order: list[int] = []
    for i in sorted(selected):
        if i in suppressed:
            continue
        if view.walk[i].kind == "container" and i not in whole:
            continue  # partly covered: its covered items print one by one
        order.append(i)
    return singles, order, cards, missing


def resolve_refs(doc: AimDocument, refs: Sequence[str]) -> tuple[list[str], list[str]]:
    """Resolve chunk / container / proposal ids, ``aim:theme`` / ``aim:doc``
    and inclusive ``a..b`` ranges (over unit order) to ``(ids, missing)``,
    deduplicated: a container selected whole stands for its subtree.
    Raises ``ValueError`` on a reversed range or more than 200 references."""
    view = _View(doc)
    singles, order, cards, missing = _parse_refs(view, doc, refs)
    return singles + [view.walk[i].id for i in order] + cards, missing


def _context(view: _View, u: _U, by_target: dict[str, list[Proposal]]) -> str:
    parts: list[str] = []
    if u.container != "body":
        parts.append(f"in {u.container}")
    label = view.label(u)
    if label:
        parts.append(label)
    for p in by_target.get(u.id, []):
        if p.target == u.id:
            parts.append(f"pending {p.id} {p.action}")
        else:
            parts.append(f"pending {p.id} {p.action} after this")
    return f"[{u.id}]" + (" " + "; ".join(parts) if parts else "")


def _pending_by_unit(doc: AimDocument) -> dict[str, list[Proposal]]:
    out: dict[str, list[Proposal]] = {}
    for p in doc.proposals:
        if p.target:
            out.setdefault(p.target, []).append(p)
        if p.anchor_after and p.anchor_after != p.target:
            out.setdefault(p.anchor_after, []).append(p)
    return out


def render_chunks(doc: AimDocument, refs: Sequence[str]) -> str:
    """The exact canonical serialization of each referenced unit, data URIs
    elided to round-tripping stubs, each under a context line naming its
    container, numbering label and the pending cards that target or anchor
    on it. Unknown references are listed on a final ``[missing]`` line."""
    if not refs:
        raise ValueError("chunks mode needs ids (chunk, container or proposal ids, or 'a..b')")
    view = _View(doc)
    singles, order, cards, missing = _parse_refs(view, doc, refs)
    by_target = _pending_by_unit(doc)
    out = [short_header(doc)]
    state = doc._state
    for ref in singles:
        out.append(f"[{ref}]")
        out.append(elide(state.serial(ref) or ""))
    for i in order:
        u = view.walk[i]
        out.append(_context(view, u, by_target))
        html = serialize(u.els[0]) if u.kind == "container" else serialize_run(u.els)
        out.append(elide(html))
    proposals = {p.id: p for p in doc.proposals}
    for pid in cards:
        p = proposals[pid]
        out.append(_card_line(p, view.ctx, payload=False))
        if p.payload_html:
            out.append(elide(p.payload_html))
    if missing:
        out.append("[missing] " + ", ".join(missing))
    return "\n".join(out)


# --------------------------------------------------------------------------- search
_CHAIN = re.compile(r"\d+(?:\.\d+)+")
_WORD = re.compile(r"\w+")
_CJK = re.compile("([ᄀ-ᇿ⺀-⿟぀-ヿ㄀-ㇿ㐀-䶿一-鿿가-힯豈-﫿\U00020000-\U0002fa1f]+)")
_PHRASE = re.compile(r'"([^"]*)"')


def _tokens(text: str) -> list[str]:
    """Case-folded terms: dotted number chains whole (plus their parts),
    Unicode word runs, and CJK runs as overlapping character bigrams."""
    folded = text.casefold()
    out = _CHAIN.findall(folded)
    for word in _WORD.findall(folded):
        for piece in _CJK.split(word):
            if not piece:
                continue
            if _CJK.fullmatch(piece):
                if len(piece) == 1:
                    out.append(piece)
                else:
                    out.extend(piece[i : i + 2] for i in range(len(piece) - 1))
            else:
                out.append(piece)
    return out


def _prefix_match(q: str, term: str) -> bool:
    short, long_ = (q, term) if len(q) <= len(term) else (term, q)
    return len(short) >= 4 and len(long_) - len(short) <= 3 and long_.startswith(short)


def _norm(text: str) -> str:
    return _WS.sub(" ", text.casefold()).strip()


def search(doc: AimDocument, query: str, k: int = 8) -> list[Hit]:
    """Rank body chunks by lexical relevance to *query* (BM25, k1=1.2,
    b=0.75, over each chunk's text-view rendering, numbering labels
    included). Chunks matching more distinct query terms rank first; a term
    with no exact match matches longer/shorter forms (shared prefix of 4+
    characters, at most 3 apart) at half weight; ``"quoted phrases"``
    filter. Ties keep document order. Never compiles the query as a regex."""
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"query longer than {MAX_QUERY_CHARS} characters")
    if not 1 <= k <= MAX_K:
        raise ValueError(f"k must be between 1 and {MAX_K}")
    terms = list(dict.fromkeys(_tokens(query)))
    if len(terms) > MAX_QUERY_TERMS:
        raise ValueError(f"query has more than {MAX_QUERY_TERMS} terms")
    phrases = [_norm(p) for p in _PHRASE.findall(query) if _norm(p)]
    if not terms:
        return []

    view = _View(doc, marks=False)
    docs: list[tuple[int, str, Counter[str]]] = []
    sections: dict[int, str | None] = {}
    section: str | None = None
    for i, u in enumerate(view.walk):
        if u.kind != "chunk":
            continue
        text = _plain(view.lines(i))
        root = u.els[0]
        is_section = u.depth == 0 and (root.tag in HEADINGS or _num_level(root) == 1)
        sections[i] = None if is_section else section
        if is_section:
            title = text.lstrip("#").strip()
            section = title[:40].rstrip() + ("…" if len(title) > 40 else "")
        docs.append((i, text, Counter(_tokens(text))))
    if not docs:
        return []
    n = len(docs)
    avg = sum(sum(c.values()) for _, _, c in docs) / n
    df: Counter[str] = Counter()
    for _, _, counts in docs:
        df.update(counts.keys())
    vocab = list(df)

    # each query term -> (variants, weight)
    expanded: list[tuple[tuple[str, ...], float]] = []
    for t in terms:
        if df[t]:
            expanded.append(((t,), 1.0))
        else:
            variants = tuple(v for v in vocab if _prefix_match(t, v))
            if variants:
                expanded.append((variants, 0.5))

    weights: list[tuple[tuple[str, ...], float, float]] = []  # variants, weight, idf
    for variants, weight in expanded:
        dfv = sum(1 for _, _, c in docs if any(c[v] for v in variants))
        weights.append((variants, weight, math.log(1 + (n - dfv + 0.5) / (dfv + 0.5))))

    by_target = _pending_by_unit(doc)
    scored: list[tuple[int, float, int, str, str]] = []
    for i, text, counts in docs:
        if phrases and not all(p in _norm(text) for p in phrases):
            continue
        length = sum(counts.values())
        score, matched = 0.0, 0
        matched_terms: list[str] = []
        for variants, weight, idf in weights:
            tf = sum(counts[v] for v in variants)
            if not tf:
                continue
            score += weight * idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / avg))
            matched += 1
            matched_terms.extend(variants)
        if matched:
            scored.append((matched, score, i, text, " ".join(matched_terms)))
    scored.sort(key=lambda s: (-s[0], -s[1], s[2]))

    hits: list[Hit] = []
    for _matched, score, i, text, joined in scored[:k]:
        wanted = set(joined.split())
        words = text.split()
        first = next((j for j, w in enumerate(words) if wanted & set(_tokens(w))), 0)
        lo = max(0, first - 8)
        snippet = " ".join(words[lo : lo + 24])
        snippet = ("… " if lo else "") + snippet + (" …" if lo + 24 < len(words) else "")
        uid = view.walk[i].id
        hits.append(
            Hit(
                id=uid,
                score=round(score, 4),
                section=sections[i],
                snippet=snippet,
                pending=tuple(p.id for p in by_target.get(uid, [])),
            )
        )
    return hits


def render_search(doc: AimDocument, query: str, k: int = 8) -> str:
    hits = search(doc, query, k)
    out = [short_header(doc)]
    if not hits:
        out.append(f'no matches for "{_clean(query)}"')
    for h in hits:
        where = f"§ {h.section} | " if h.section else ""
        pending = f" [pending {', '.join(h.pending)}]" if h.pending else ""
        out.append(f"[{h.id}] {where}{h.snippet}{pending}")
    return "\n".join(out)
