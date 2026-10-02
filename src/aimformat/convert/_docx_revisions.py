"""Word tracked changes and comments, resolved at the OOXML level.

Part of the DOCX seam (:mod:`._docx_seam` owns the parse layer; this module
owns WordprocessingML revision markup, through lxml only — it never imports
the parse layer). The converter never sees a revision wrapper: it sees two
revision-free *views* of ``document.xml`` and converts each.

- the **reject view** is what Word shows after *Reject All* (the original);
- the **accept view** is what Word shows after *Accept All* (the final).

The rules (ECMA-376 Part 1 §17.13.5):

- run content survives in the accept view iff it has no ``w:del`` /
  ``w:moveFrom`` ancestor, and in the reject view iff it has no ``w:ins`` /
  ``w:moveTo`` ancestor — nesting composes, so ``del(ins(x))`` is gone from
  both;
- in the reject view ``w:delText`` reads as ``w:t`` (and ``w:delInstrText``
  as ``w:instrText``);
- a paragraph MARK carrying ``rPr/w:ins`` (or ``moveTo``) does not exist in
  the reject view, one carrying ``rPr/w:del`` (or ``moveFrom``) does not
  exist in the accept view: that paragraph's content joins the FOLLOWING
  paragraph, whose mark — and so whose ``pPr`` — survives. A mark with
  nothing to join (last in its container, or followed by a table) is kept;
- ``*PrChange`` records hold the OLD properties: the reject view swaps them
  in, the accept view drops the record;
- a row whose ``trPr`` carries ``w:ins`` is absent from the reject view, one
  carrying ``w:del`` from the accept view (``w:cellIns``/``w:cellDel`` the
  same for one cell).

Every ``w:p``, ``w:tr`` and ``w:tbl`` in the body is stamped with a private
source key before the views are resolved, so the two conversions can be
aligned by identity — never by position. A paragraph that absorbs another
one's content carries both keys (its own first), which is how a merged or
split paragraph keeps naming the source elements it came from.

Strings read from the file — author names, comment text — are claims made by
the file: they are stripped of control characters and length-capped here,
before anything else sees them.
"""

from __future__ import annotations

import bisect
import copy
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from ..errors import ParseError

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
_W15_NS = "http://schemas.microsoft.com/office/word/2012/wordml"

#: The private attribute carrying a source key through the views. dpc ignores
#: attributes it does not know, and the namespace can never collide with
#: anything Word writes.
SRC_ATTR = "{urn:aimformat:import}src"

#: Caps on strings the file supplies (author 128, comment 2,000, excerpt 240).
AUTHOR_CAP = 128
COMMENT_CAP = 2000
EXCERPT_CAP = 240


def _q(tag: str) -> str:
    return f"{{{_W_NS}}}{tag}"


_P, _TR, _TBL, _TC, _R = _q("p"), _q("tr"), _q("tbl"), _q("tc"), _q("r")
_INS, _DEL, _MOVE_FROM, _MOVE_TO = _q("ins"), _q("del"), _q("moveFrom"), _q("moveTo")
_RPR, _PPR, _TRPR, _TCPR = _q("rPr"), _q("pPr"), _q("trPr"), _q("tcPr")
_T, _DEL_TEXT = _q("t"), _q("delText")
_INSTR, _DEL_INSTR = _q("instrText"), _q("delInstrText")
_BODY = _q("body")

#: Revision wrappers whose content is gone from each view.
_GONE_IN = {"accept": {_DEL, _MOVE_FROM}, "reject": {_INS, _MOVE_TO}}
#: …and whose wrapper is dropped (content kept) in each view.
_KEEP_IN = {"accept": {_INS, _MOVE_TO}, "reject": {_DEL, _MOVE_FROM}}
_WRAPPERS = {_INS, _DEL, _MOVE_FROM, _MOVE_TO}

_MOVE_RANGES = {
    _q("moveFromRangeStart"): ("moveFrom", "start"),
    _q("moveFromRangeEnd"): ("moveFrom", "end"),
    _q("moveToRangeStart"): ("moveTo", "start"),
    _q("moveToRangeEnd"): ("moveTo", "end"),
}
_CUSTOM_XML_RANGES = {
    _q(f"customXml{kind}Range{edge}")
    for kind in ("Ins", "Del", "MoveFrom", "MoveTo")
    for edge in ("Start", "End")
}
_CELL_CHANGES = {_q("cellIns"), _q("cellDel"), _q("cellMerge")}

#: ``*PrChange`` record → (the properties element it lives in, the children of
#: that element the swap must keep because the record does not describe them).
_PROP_CHANGES: dict[str, set[str]] = {
    _q("rPrChange"): {_INS, _DEL, _MOVE_FROM, _MOVE_TO},
    _q("pPrChange"): {_RPR, _q("sectPr")},
    _q("trPrChange"): {_INS, _DEL},
    _q("tcPrChange"): _CELL_CHANGES,
    _q("tblPrChange"): set(),
    _q("tblPrExChange"): set(),
    _q("tblGridChange"): set(),
    _q("sectPrChange"): {_q("headerReference"), _q("footerReference")},
}
#: rPrChange first: a pPrChange swap copies the (already resolved) mark rPr
_PROP_CHANGE_ORDER = [_q("rPrChange"), _q("pPrChange")] + [
    t for t in _PROP_CHANGES if t not in (_q("rPrChange"), _q("pPrChange"))
]

#: Elements that may sit between two paragraphs without ending the join.
_INVISIBLE_BETWEEN = {
    _q("bookmarkStart"),
    _q("bookmarkEnd"),
    _q("commentRangeStart"),
    _q("commentRangeEnd"),
    _q("proofErr"),
    _q("permStart"),
    _q("permEnd"),
    *_MOVE_RANGES,
    *_CUSTOM_XML_RANGES,
}

#: Everything that makes a document "tracked". Anything here triggers the
#: two-view path; whatever the views do not carry is reported.
_REVISION_TAGS = {
    _INS,
    _DEL,
    _MOVE_FROM,
    _MOVE_TO,
    *_PROP_CHANGES,
    *_CELL_CHANGES,
    _q("numberingChange"),
    _DEL_INSTR,
    *_CUSTOM_XML_RANGES,
}

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def clean(value: str | None, cap: int) -> str:
    """A file-supplied string, safe to carry: control characters stripped,
    whitespace runs collapsed, length capped (with an ellipsis)."""
    if not value:
        return ""
    text = " ".join(_CONTROL.sub("", value).split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


@dataclass
class Revision:
    """One revision record from ``document.xml``, as the file states it."""

    index: int
    kind: str  # ins | del | moveFrom | moveTo | rPrChange | pPrChange | … (local name)
    where: str  # run | mark | row | cell | props | section
    author: str | None
    date: str | None
    text: str  # what the revision inserted or deleted, capped (empty for props)
    srcs: tuple[str, ...]  # stamped ancestors (p / tr / tbl keys), innermost first
    move_name: str | None = None


@dataclass
class Comment:
    """One Word comment, with its anchor resolved against both views."""

    id: str
    author: str
    date: str | None
    text: str
    resolved: bool = False
    parent_id: str | None = None
    #: (view, the chunk key holding the range start, the anchored text)
    anchor_view: str | None = None
    anchor_key: str | None = None
    anchor_text: str = ""


@dataclass
class TrackedDocument:
    """The revision facts of one tracked ``document.xml``."""

    revisions: list[Revision]
    #: paragraph key → ("moveFrom" | "moveTo", range name or None) for paragraphs
    #: whose MARK is a move — the unit a paragraph-level move pairs on
    move_marks: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    #: revisions found where the import never looks (headers, footers,
    #: notes) or inside wrappers the converter skips (body-level ``w:sdt``)
    unreachable: dict[str, int] = field(default_factory=dict)


# -- detection ------------------------------------------------------------------


def revision_count(doc_elem: Any) -> int:
    """How many revision records the document body carries (0 = untracked)."""
    if doc_elem is None:
        return 0
    return sum(1 for el in doc_elem.iter() if el.tag in _REVISION_TAGS)


def stamp_sources(doc_elem: Any) -> None:
    """Give every ``w:p``, ``w:tr`` and ``w:tbl`` in the body its source key
    (recursively: cells, nested tables, textboxes)."""
    body = doc_elem.find(_BODY) if doc_elem is not None else None
    if body is None:
        return
    n = 0
    for el in body.iter(_P, _TR, _TBL):
        n += 1
        prefix = "p" if el.tag == _P else ("r" if el.tag == _TR else "t")
        el.set(SRC_ATTR, f"{prefix}{n}")


def src_key(el: Any) -> str | None:
    """The element's own source key (the first token of its stamp)."""
    value = el.get(SRC_ATTR) if el is not None else None
    return value.split()[0] if value else None


def _ancestor_srcs(el: Any) -> tuple[str, ...]:
    out: list[str] = []
    node = el
    while node is not None:
        value = node.get(SRC_ATTR)
        if value:
            out.extend(value.split()[:1])
        node = node.getparent()
    return tuple(out)


def _capped_text(el: Any) -> str:
    """The text a revision carries, accumulated only up to the cap: a hostile
    1 MB revision costs one linear walk, never more."""
    parts: list[str] = []
    size = 0
    for node in el.iter(_T, _DEL_TEXT, _INSTR, _DEL_INSTR):
        if node.text:
            parts.append(node.text)
            size += len(node.text)
            if size > EXCERPT_CAP * 2:
                break
    return clean("".join(parts), EXCERPT_CAP)


def _where(el: Any) -> str:
    parent = el.getparent()
    tag = el.tag
    if tag in _PROP_CHANGES:
        return "section" if tag == _q("sectPrChange") else "props"
    if tag in _CELL_CHANGES:
        return "cell"
    if parent is not None and parent.tag == _RPR:
        grand = parent.getparent()
        if grand is not None and grand.tag == _PPR:
            return "mark"
        return "historic"  # inside an old rPr held by a change record
    if parent is not None and parent.tag == _TRPR:
        return "row"
    return "run"


def collect_revisions(doc_elem: Any) -> TrackedDocument:
    """Every revision record in the (stamped) body, in document order, with
    move-range names attached and paragraph-mark moves indexed."""
    revisions: list[Revision] = []
    move_marks: dict[str, tuple[str, str | None]] = {}
    unreachable: dict[str, int] = {}
    body = doc_elem.find(_BODY) if doc_elem is not None else None
    if body is None:
        return TrackedDocument(revisions)
    open_ranges: dict[str, dict[str, str]] = {"moveFrom": {}, "moveTo": {}}
    sdt = _q("sdt")
    for el in body.iter():
        if not isinstance(el.tag, str):
            continue
        ranged = _MOVE_RANGES.get(el.tag)
        if ranged is not None:
            kind, edge = ranged
            rid = el.get(_q("id")) or ""
            if edge == "start":
                open_ranges[kind][rid] = clean(el.get(_q("name")), AUTHOR_CAP)
            else:
                open_ranges[kind].pop(rid, None)
            continue
        if el.tag not in _REVISION_TAGS or el.tag in _CUSTOM_XML_RANGES:
            continue
        if el.tag == _DEL_INSTR:
            continue  # field-code text inside a deletion: part of that record
        where = _where(el)
        if where == "historic":
            continue
        kind = etree.QName(el).localname
        name = None
        if kind in ("moveFrom", "moveTo"):
            if where == "mark":
                # the mark lives in pPr, BEFORE the paragraph's own content,
                # where Word usually opens the move range: look inside first
                name = _range_name_in(el.getparent().getparent().getparent(), kind)
            if name is None:
                names = list(open_ranges[kind].values())
                name = names[-1] if names else None
        srcs = _ancestor_srcs(el)
        if _inside(el, sdt, body):
            unreachable["a body-level content control"] = (
                unreachable.get("a body-level content control", 0) + 1
            )
        text = _capped_text(el) if where in ("run",) else ""
        rev = Revision(
            index=len(revisions),
            kind=kind,
            where=where,
            author=clean(el.get(_q("author")), AUTHOR_CAP) or None,
            date=el.get(_q("date")),
            text=text,
            srcs=srcs,
            move_name=name,
        )
        revisions.append(rev)
        if where == "mark" and kind in ("moveFrom", "moveTo") and srcs:
            move_marks[srcs[0]] = (kind, name)
    return TrackedDocument(revisions, move_marks, unreachable)


def _range_name_in(paragraph: Any, kind: str) -> str | None:
    tag = _q(f"{kind}RangeStart")
    for start in paragraph.iter(tag) if paragraph is not None else ():
        return clean(start.get(_q("name")), AUTHOR_CAP) or None
    return None


def _inside(el: Any, tag: str, stop: Any) -> bool:
    node = el.getparent()
    while node is not None and node is not stop:
        if node.tag == tag and node.getparent() is stop:
            return True
        node = node.getparent()
    return False


_PART_REVISION = re.compile(rb"<w:(?:ins|del|moveFrom|moveTo)[\s>/]")
_UNREACHABLE_PARTS = (
    ("header", re.compile(r"^word/header\d*\.xml$")),
    ("footer", re.compile(r"^word/footer\d*\.xml$")),
    ("footnotes", re.compile(r"^word/footnotes\.xml$")),
    ("endnotes", re.compile(r"^word/endnotes\.xml$")),
)


def unreachable_part_revisions(zf: zipfile.ZipFile) -> dict[str, int]:
    """Revisions in parts the importer does not carry (headers, footers,
    footnotes, endnotes), counted so the report can say so."""
    out: dict[str, int] = {}
    for name in zf.namelist():
        for label, pattern in _UNREACHABLE_PARTS:
            if pattern.match(name):
                try:
                    count = len(_PART_REVISION.findall(zf.read(name)))
                except KeyError:
                    count = 0
                if count:
                    shown = f"{label}s" if label in ("header", "footer") else label
                    out[shown] = out.get(shown, 0) + count
    return out


# -- views ------------------------------------------------------------------------


def resolve_view(doc_elem: Any, view: str) -> Any:
    """A revision-free deep copy of *doc_elem*: what Word shows after Accept
    All (``view="accept"``) or Reject All (``view="reject"``)."""
    if view not in _GONE_IN:
        raise ValueError(f"unknown view {view!r}")
    root = copy.deepcopy(doc_elem)
    gone, keep = _GONE_IN[view], _KEEP_IN[view]
    # 1. run-level revision wrappers (marks and rows are handled below)
    for el in [e for e in root.iter() if e.tag in gone]:
        parent = el.getparent()
        if parent is None or parent.tag in (_RPR, _TRPR):
            continue
        parent.remove(el)
    for el in [e for e in root.iter() if e.tag in keep]:
        parent = el.getparent()
        if parent is None or parent.tag in (_RPR, _TRPR):
            continue
        _unwrap(el)
    for el in [e for e in root.iter() if e.tag in _MOVE_RANGES or e.tag in _CUSTOM_XML_RANGES]:
        _detach(el)
    if view == "reject":
        for el in root.iter(_DEL_TEXT):
            el.tag = _T
        for el in root.iter(_DEL_INSTR):
            el.tag = _INSTR
    # 2. property changes hold the OLD properties
    for change_tag in _PROP_CHANGE_ORDER:
        for change in list(root.iter(change_tag)):
            holder = change.getparent()
            if holder is None:
                continue
            if view == "accept":
                holder.remove(change)
                continue
            old = next((c for c in change if isinstance(c.tag, str)), None)
            replacement = copy.deepcopy(old) if old is not None else etree.Element(holder.tag)
            replacement.tag = holder.tag
            for attr, value in holder.attrib.items():
                replacement.set(attr, value)
            for child in holder:
                if child.tag in _PROP_CHANGES[change_tag]:
                    replacement.append(copy.deepcopy(child))
            outer = holder.getparent()
            if outer is not None:
                outer.replace(holder, replacement)
    # 3. rows and cells
    for tr in list(root.iter(_TR)):
        props = tr.find(_TRPR)
        if props is not None and any(c.tag in gone for c in props):
            _detach(tr)
    cell_gone = _q("cellDel") if view == "accept" else _q("cellIns")
    for tc in list(root.iter(_TC)):
        props = tc.find(_TCPR)
        if props is not None and props.find(cell_gone) is not None:
            _detach(tc)
    # 4. paragraph marks: a gone mark joins its paragraph into the next one.
    # A run of consecutive gone marks collapses into the paragraph that ends
    # it in ONE splice: joining pair by pair re-moved the accumulated content
    # at every mark, cubic in the length of the run.
    seen: set[Any] = set()
    for p in list(root.iter(_P)):
        if p in seen or p.getparent() is None or _mark_survives(p, gone):
            continue
        chain = [p]
        nxt = _next_block(p)
        while nxt is not None and nxt.tag == _P and not _mark_survives(nxt, gone):
            chain.append(nxt)
            nxt = _next_block(nxt)
        # nothing to join the last one into (end of container, or a table
        # follows): it stays, and the rest of the run joins it
        target = nxt if nxt is not None and nxt.tag == _P else chain.pop()
        seen.update(chain)
        seen.add(target)
        if not chain:
            continue
        gathered: list[Any] = []
        # source keys, newest holder last: each join puts the absorbed
        # paragraph's keys after the absorbing paragraph's own. An empty
        # prefix (its runs gone with the revision) joins nothing: recording
        # it would make the neighbour cite revisions that never touched its
        # text.
        keys = (chain[0].get(SRC_ATTR) or "").split()[::-1]
        has = False
        recorded = False
        for i, member in enumerate(chain):
            content = [c for c in member if c.tag != _PPR]
            gathered += content
            has = has or any(_has_content(c) for c in content)
            holder = chain[i + 1] if i + 1 < len(chain) else target
            own = (holder.get(SRC_ATTR) or "").split()[::-1]
            recorded = bool(keys) and has
            keys = keys + own if recorded else own
        if recorded:
            target.set(SRC_ATTR, " ".join(keys[::-1]))
        at = 1 if target.find(_PPR) is not None else 0
        target[at:at] = gathered
        for member in chain:
            _detach(member)
    # 5. scrub the remaining markers: the view is revision-free
    for el in [
        e
        for e in root.iter()
        if e.tag in _WRAPPERS or e.tag in _CELL_CHANGES or e.tag == _q("numberingChange")
    ]:
        _detach(el)
    return root


def _next_block(p: Any) -> Any:
    """The sibling after *p*, past markers invisible between paragraphs."""
    nxt = p.getnext()
    while nxt is not None and (not isinstance(nxt.tag, str) or nxt.tag in _INVISIBLE_BETWEEN):
        nxt = nxt.getnext()
    return nxt


def _has_content(el: Any) -> bool:
    """Whether *el* carries anything a reader sees: run content (text, a
    break, a drawing), not only properties and range marks."""
    runs = [el] if el.tag == _R else list(el.iter(_R))
    return any(child.tag != _RPR for run in runs for child in run)


def _mark_survives(p: Any, gone: set[str]) -> bool:
    ppr = p.find(_PPR)
    rpr = ppr.find(_RPR) if ppr is not None else None
    if rpr is None:
        return True
    return not any(child.tag in gone for child in rpr)


def _unwrap(el: Any) -> None:
    parent = el.getparent()
    at = parent.index(el)
    tail = el.tail
    for child in list(el):
        parent.insert(at, child)
        at += 1
    _detach(el, tail=tail)


def _detach(el: Any, tail: str | None = None) -> None:
    parent = el.getparent()
    if parent is None:
        return
    keep_tail = el.tail if tail is None else tail
    prev = el.getprevious()
    if keep_tail:
        if prev is not None:
            prev.tail = (prev.tail or "") + keep_tail
        else:
            parent.text = (parent.text or "") + keep_tail
    parent.remove(el)


# -- comments ---------------------------------------------------------------------

_SAFE_PARSER = etree.XMLParser(
    resolve_entities=False, no_network=True, huge_tree=False, load_dtd=False
)
_MAX_COMMENT_PART = 16 * 1024 * 1024


def _safe_part(zf: zipfile.ZipFile, name: str) -> Any | None:
    """Parse an auxiliary part the way the main parts are guarded: no
    entities, no network, no DTD — and a part that declares a DOCTYPE at
    all is refused (OOXML parts never carry one)."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _MAX_COMMENT_PART:
        raise ParseError(f"{name} exceeds the size limit for an auxiliary part")
    data = zf.read(name)
    if b"<!DOCTYPE" in data[:4096].upper().replace(b"<!doctype", b"<!DOCTYPE"):
        raise ParseError(f"{name} declares a DOCTYPE, which no OOXML part carries")
    try:
        return etree.fromstring(data, _SAFE_PARSER)
    except etree.XMLSyntaxError as exc:
        raise ParseError(f"{name} is not well-formed XML: {exc}") from exc


def read_comments(zf: zipfile.ZipFile) -> list[Comment]:
    """``comments.xml`` (+ ``commentsExtended.xml`` for resolved state and
    reply threading), anchors still unresolved."""
    root = _safe_part(zf, "word/comments.xml")
    if root is None:
        return []
    by_para: dict[str, str] = {}  # w14:paraId of a comment's last paragraph → comment id
    out: list[Comment] = []
    for c in root.iter(_q("comment")):
        cid = c.get(_q("id")) or ""
        paras = list(c.iter(_P))
        text = " ".join("".join(t.text or "" for t in p.iter(_T)).strip() for p in paras).strip()
        out.append(
            Comment(
                id=clean(cid, 32),
                author=clean(c.get(_q("author")), AUTHOR_CAP) or "unknown",
                date=c.get(_q("date")),
                text=clean(text, COMMENT_CAP),
            )
        )
        if paras:
            para_id = paras[-1].get(f"{{{_W14_NS}}}paraId")
            if para_id:
                by_para[para_id] = out[-1].id
    ext = _safe_part(zf, "word/commentsExtended.xml")
    if ext is not None:
        by_id = {c.id: c for c in out}
        for ex in ext.iter(f"{{{_W15_NS}}}commentEx"):
            cid = by_para.get(ex.get(f"{{{_W15_NS}}}paraId") or "")
            comment = by_id.get(cid or "")
            if comment is None:
                continue
            comment.resolved = ex.get(f"{{{_W15_NS}}}done") in ("1", "true")
            parent = by_para.get(ex.get(f"{{{_W15_NS}}}paraIdParent") or "")
            comment.parent_id = parent
    return out


def comment_anchors(view_root: Any) -> dict[str, tuple[str | None, str]]:
    """comment id → (the chunk key holding the range start, the anchored
    text) in one resolved view. A comment with no range marks anchors on
    the paragraph of its reference mark, with no text."""
    start_tag, end_tag = _q("commentRangeStart"), _q("commentRangeEnd")
    ref_tag = _q("commentReference")
    # One shared text stream with running offsets; a range records where in
    # it it starts. Each range's excerpt is cut once, when it closes — the
    # file decides how many ranges are open at once, so per-text-node work
    # over every open range would be quadratic.
    texts: list[str] = []
    ends = [0]  # ends[i]: characters in texts[:i]
    open_: dict[str, int] = {}  # comment id → index of its first text node
    keys: dict[str, str | None] = {}
    done: dict[str, tuple[str | None, str]] = {}

    def excerpt(first: int) -> str:
        # whole text nodes while the excerpt is at most twice the cap long
        stop = bisect.bisect_right(ends, ends[first] + EXCERPT_CAP * 2, lo=first)
        return clean("".join(texts[first:stop]), EXCERPT_CAP)

    for el in view_root.iter():
        tag = el.tag
        if tag == start_tag:
            cid = el.get(_q("id")) or ""
            open_[cid] = len(texts)
            keys[cid] = _paragraph_key(el)
        elif tag == end_tag:
            cid = el.get(_q("id")) or ""
            if cid in open_:
                done[cid] = (keys.get(cid), excerpt(open_.pop(cid)))
        elif tag == ref_tag:
            cid = el.get(_q("id")) or ""
            if cid not in done and cid not in open_:
                done[cid] = (_paragraph_key(el), "")
        elif tag == _T and el.text:
            texts.append(el.text)
            ends.append(ends[-1] + len(el.text))
    for cid, first in open_.items():  # an unterminated range runs to the end
        done.setdefault(cid, (keys.get(cid), excerpt(first)))
    return done


def _paragraph_key(el: Any) -> str | None:
    """The source key of the outermost unit holding *el*: the row for a cell
    paragraph (rows are the chunks), else the paragraph itself."""
    node = el.getparent()
    para: str | None = None
    row: str | None = None
    while node is not None:
        if node.tag == _P and para is None:
            para = src_key(node)
        elif node.tag == _TR:
            row = src_key(node)
        node = node.getparent()
    return row or para
