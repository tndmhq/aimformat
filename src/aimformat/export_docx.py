"""Export: .aim → DOCX (``python-docx``, behind the ``aimformat[docx]`` extra).

The exporter walks the accepted body and maps chunks onto Word constructs.
Its distinguishing feature — a pattern adapted from a previous project's
editor, where the DOCX download replayed the change log with a caller-chosen
default — is how the **pending lane** is handled:

``pending="tracked"`` (default)
    Pending proposals are emitted as *real Word revision markup*
    (``w:ins``/``w:del``), attributed to the proposing actor and timestamped,
    so a counterparty opening the file in Word sees reviewable tracked
    changes. Granularity is the chunk (whole-block delete + insert), matching
    what the format stores; word-level diffs are a viewer concern.
``pending="accept-all"`` / ``pending="reject-all"``
    The pending lane is resolved on a throwaway copy of the document —
    through the ordinary accept/reject machinery, decided by the ``external``
    actor ``docx-export`` — and the resolved body is exported clean.

Not represented in v0.1: ``move`` proposals and ``aim:theme`` proposals
(exported as unchanged content), slides (``aim-slide`` targets a future PPTX
exporter), and hyperlink relationships (links render as text with the URL in
parentheses). These are deliberate scope cuts, not oversights.
"""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from .document import AimDocument
from .dom import Element, Text
from .errors import InvalidOperation
from .events import external

if TYPE_CHECKING:  # pragma: no cover
    from docx.text.paragraph import Paragraph

__all__ = ["to_docx"]

_PENDING_MODES = ("tracked", "accept-all", "reject-all")
_MONO = "Consolas"

_BOLD_TAGS = {"strong", "b"}
_ITALIC_TAGS = {"em", "i"}


def _require_docx():
    try:
        import docx  # noqa: F401
        return docx
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ImportError(
            "DOCX export needs python-docx. Install the extra:\n"
            "    pip install 'aimformat[docx]'") from exc


# --------------------------------------------------------------------------
# inline content -> (text, formatting) run specs
def _runs_of(el: Element, fmt: Optional[dict] = None) -> list[dict]:
    fmt = dict(fmt or {})
    tag = el.tag
    if tag in _BOLD_TAGS:
        fmt["bold"] = True
    elif tag in _ITALIC_TAGS:
        fmt["italic"] = True
    elif tag == "u":
        fmt["underline"] = True
    elif tag == "s":
        fmt["strike"] = True
    elif tag == "mark":
        fmt["highlight"] = True
    elif tag == "code":
        fmt["mono"] = True
    elif tag == "sub":
        fmt["subscript"] = True
    elif tag == "sup":
        fmt["superscript"] = True

    runs: list[dict] = []
    for child in el.children:
        if isinstance(child, Text):
            if child.data:
                runs.append({"text": child.data, **fmt})
        elif isinstance(child, Element):
            if child.tag == "br":
                runs.append({"break": True, **fmt})
            elif child.tag == "a":
                runs += _runs_of(child, {**fmt, "underline": True})
                href = child.get("href") or ""
                if href and not href.startswith("#"):
                    runs.append({"text": f" ({href})", **fmt})
            else:
                runs += _runs_of(child, fmt)
    return runs


def _apply_runs(paragraph, runs: list[dict]) -> None:
    for spec in runs:
        run = paragraph.add_run()
        if spec.get("break"):
            run.add_break()
            continue
        run.text = spec.get("text", "")
        _format_run(run, spec)


def _format_run(run, spec: dict) -> None:
    run.bold = spec.get("bold") or None
    run.italic = spec.get("italic") or None
    run.underline = True if spec.get("underline") else None
    if spec.get("strike"):
        run.font.strike = True
    if spec.get("mono"):
        run.font.name = _MONO
    if spec.get("subscript"):
        run.font.subscript = True
    if spec.get("superscript"):
        run.font.superscript = True
    if spec.get("highlight"):
        from docx.enum.text import WD_COLOR_INDEX
        run.font.highlight_color = WD_COLOR_INDEX.YELLOW


# --------------------------------------------------------------------------
# tracked-changes OXML
class _Revisions:
    """Builds w:ins / w:del wrappers with stable ids and attribution."""

    def __init__(self):
        self._next = 1

    def _attrs(self, el, author: str, date: str) -> None:
        from docx.oxml.ns import qn
        el.set(qn("w:id"), str(self._next))
        el.set(qn("w:author"), author)
        el.set(qn("w:date"), date or "2026-01-01T00:00:00Z")
        self._next += 1

    def ins(self, paragraph, runs: list[dict], author: str, date: str) -> None:
        from docx.oxml import OxmlElement
        wrap = OxmlElement("w:ins")
        self._attrs(wrap, author, date)
        for spec in runs:
            wrap.append(self._make_run(paragraph, spec, deleted=False))
        paragraph._p.append(wrap)

    def dele(self, paragraph, runs: list[dict], author: str, date: str) -> None:
        from docx.oxml import OxmlElement
        wrap = OxmlElement("w:del")
        self._attrs(wrap, author, date)
        for spec in runs:
            wrap.append(self._make_run(paragraph, spec, deleted=True))
        paragraph._p.append(wrap)

    @staticmethod
    def _make_run(paragraph, spec: dict, *, deleted: bool):
        # build a real run for formatting, then detach and retag its text
        from docx.oxml.ns import qn
        run = paragraph.add_run(spec.get("text", ""))
        _format_run(run, spec)
        r = run._r
        r.getparent().remove(r)
        if deleted:
            for t in r.findall(qn("w:t")):
                t.tag = qn("w:delText")
                t.set(qn("xml:space"), "preserve")
        return r


def _actor_label(author) -> str:
    if author.type == "agent":
        return f"agent:{author.model or author.id or 'unknown'}"
    if author.type == "human":
        return author.id or "human"
    return author.id or "external"


# --------------------------------------------------------------------------
class _Exporter:
    def __init__(self, doc: AimDocument, docx_mod):
        self.aim = doc
        self.docx = docx_mod
        self.out = docx_mod.Document()
        self.rev = _Revisions()
        self.pending_mod: dict[str, object] = {}
        self.pending_del: dict[str, object] = {}
        self.adds_by_anchor: dict[Optional[str], list] = {}
        for p in doc.proposals:
            if p.action == "modify" and p.target and p.target != "aim:theme":
                self.pending_mod[p.target] = p
            elif p.action == "delete" and p.target:
                self.pending_del[p.target] = p
            elif p.action == "add" and (p.anchor_container or "body") == "body":
                self.adds_by_anchor.setdefault(p.anchor_after, []).append(p)

    # -- top level -----------------------------------------------------------
    def run(self) -> None:
        title = self.aim.title
        if title:
            self.out.core_properties.title = title
        self._emit_adds(None)  # pending adds at the very front
        for construct in self.aim._state.constructs():
            self.emit_construct(construct)
            cid = construct.chunk_id or construct.container_id
            self._emit_adds(cid)

    def _emit_adds(self, anchor: Optional[str]) -> None:
        for prop in self.adds_by_anchor.pop(anchor, []):
            for el in self._payload_elements(prop):
                runs = _runs_of(el)
                para = self.out.add_paragraph(
                    style=self._style_for(el.tag))
                self.rev.ins(para, runs, _actor_label(prop.author), prop.at)
            self._emit_adds(prop.id)  # chained adds anchor on this proposal

    def _payload_elements(self, prop) -> list[Element]:
        from .dom import parse_fragment
        return [n for n in parse_fragment(prop.payload_html or "")
                if isinstance(n, Element)]

    def _style_for(self, tag: str) -> Optional[str]:
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            return f"Heading {tag[1]}"
        if tag == "blockquote":
            return "Quote"
        if tag == "li":
            return "List Bullet"
        return None

    # -- constructs ------------------------------------------------------------
    def emit_construct(self, el: Element) -> None:
        if el.tag == "aim-slide":
            return  # slides target a future PPTX exporter
        cid = el.chunk_id or el.container_id or ""
        if el.container_id and el.tag in ("ul", "ol"):
            self.emit_list(el)
        elif el.container_id and el.tag == "table":
            self.emit_table(el)
        elif el.tag == "section":
            for child in el.elements():
                self.emit_block(child, cid)
        else:
            self.emit_block(el, cid)

    def emit_block(self, el: Element, cid: str,
                   style: Optional[str] = None) -> None:
        prop_mod = self.pending_mod.get(cid)
        prop_del = self.pending_del.get(cid)
        style = style or self._style_for(el.tag)

        if el.tag == "figure":
            self.emit_figure(el)
            return
        if el.tag == "pre":
            self.emit_pre(el, cid)
            return
        if el.tag == "hr":
            self.out.add_paragraph("—" * 12)
            return
        if el.tag in ("ul", "ol"):  # atomic list chunk / nested content
            for li in el.elements():
                self.emit_block(li, cid, style="List Bullet" if el.tag == "ul"
                                else "List Number")
            return

        para = self.out.add_paragraph(style=style)
        runs = _runs_of(el)
        if prop_del is not None:
            self.rev.dele(para, runs, _actor_label(prop_del.author),
                          prop_del.at)
        elif prop_mod is not None:
            self.rev.dele(para, runs, _actor_label(prop_mod.author),
                          prop_mod.at)
            for new_el in self._payload_elements(prop_mod):
                self.rev.ins(para, _runs_of(new_el),
                             _actor_label(prop_mod.author), prop_mod.at)
        else:
            _apply_runs(para, runs)

    def emit_pre(self, el: Element, cid: str) -> None:
        text = el.text()
        para = self.out.add_paragraph()
        for i, line in enumerate(text.split("\n")):
            if i:
                para.add_run().add_break()
            run = para.add_run(line)
            run.font.name = _MONO

    def emit_figure(self, el: Element) -> None:
        img = el.find(lambda e: e.tag == "img")
        emitted = False
        if img is not None:
            src = img.get("src") or ""
            m = re.match(r"^data:image/[a-z+]+;base64,(.*)$", src, re.S)
            if m:
                try:
                    from docx.shared import Inches
                    blob = base64.b64decode(m.group(1))
                    self.out.add_picture(io.BytesIO(blob), width=Inches(4.5))
                    emitted = True
                except Exception:
                    emitted = False
            if not emitted:
                alt = img.get("alt") or "image"
                para = self.out.add_paragraph()
                run = para.add_run(f"[image: {alt}]")
                run.italic = True
                emitted = True
        for cap in el.elements():
            if cap.tag == "figcaption":
                self.out.add_paragraph(cap.text(), style="Caption" if
                                       self._has_style("Caption") else None)
        if not emitted and img is None:
            for child in el.elements():
                if child.tag != "figcaption":
                    self.emit_block(child, el.chunk_id or "")

    def _has_style(self, name: str) -> bool:
        try:
            self.out.styles[name]
            return True
        except KeyError:
            return False

    # -- lists ---------------------------------------------------------------------
    def emit_list(self, el: Element, level: int = 0) -> None:
        base = "List Bullet" if el.tag == "ul" else "List Number"
        style = base if level == 0 else f"{base} {min(level + 1, 3)}"
        if not self._has_style(style):
            style = base
        for li in el.elements():
            cid = li.chunk_id or ""
            nested = [c for c in li.elements() if c.tag in ("ul", "ol")]
            # the item's own inline content (nested lists rendered after)
            content = Element("li")
            content.children = [c for c in li.children
                                if not (isinstance(c, Element)
                                        and c.tag in ("ul", "ol"))]
            self.emit_block(content, cid, style=style)
            for sub in nested:
                self.emit_list(sub, level + 1)

    # -- tables ----------------------------------------------------------------------
    def emit_table(self, el: Element) -> None:
        rows = el.find_all(lambda e: e.tag == "tr")
        if not rows:
            return
        ncols = max(sum(int(c.get("colspan") or 1)
                        for c in r.elements() if c.tag in ("td", "th"))
                    for r in rows)
        table = self.out.add_table(rows=len(rows), cols=ncols)
        table.style = "Table Grid" if self._has_style("Table Grid") else None
        occupied: set[tuple[int, int]] = set()
        for ri, row in enumerate(rows):
            rid = row.chunk_id or ""
            prop_mod = self.pending_mod.get(rid)
            prop_del = self.pending_del.get(rid)
            new_cells = None
            if prop_mod is not None:
                payload = self._payload_elements(prop_mod)
                new_cells = [c for p in payload for c in p.elements()
                             if c.tag in ("td", "th")]
            ci = 0
            for cell_el in [c for c in row.elements() if c.tag in ("td", "th")]:
                while (ri, ci) in occupied:
                    ci += 1
                colspan = int(cell_el.get("colspan") or 1)
                rowspan = int(cell_el.get("rowspan") or 1)
                cell = table.cell(ri, ci)
                if colspan > 1 or rowspan > 1:
                    cell = cell.merge(table.cell(ri + rowspan - 1,
                                                 ci + colspan - 1))
                    for dr in range(rowspan):
                        for dc in range(colspan):
                            occupied.add((ri + dr, ci + dc))
                para = cell.paragraphs[0]
                runs = _runs_of(cell_el)
                if cell_el.tag == "th":
                    runs = [{**r, "bold": True} for r in runs]
                if prop_del is not None:
                    self.rev.dele(para, runs, _actor_label(prop_del.author),
                                  prop_del.at)
                elif prop_mod is not None:
                    self.rev.dele(para, runs, _actor_label(prop_mod.author),
                                  prop_mod.at)
                    idx = len([c for c in row.elements()
                               if c.tag in ("td", "th")][:ci + 1]) - 1
                    if new_cells and idx < len(new_cells):
                        self.rev.ins(para, _runs_of(new_cells[idx]),
                                     _actor_label(prop_mod.author),
                                     prop_mod.at)
                else:
                    _apply_runs(para, runs)
                ci += colspan


# --------------------------------------------------------------------------
def _resolve_copy(doc: AimDocument, decision: str) -> AimDocument:
    clone = AimDocument.loads(doc.dumps())
    decider = external("docx-export")
    guard = 0
    while clone.proposals:
        guard += 1
        if guard > 1000:  # pragma: no cover - defensive
            raise InvalidOperation("pending lane did not converge")
        pending_ids = {p.id for p in clone.proposals}
        # resolve proposals whose anchors are settled first (chains)
        ready = [p for p in clone.proposals
                 if not (p.action == "add" and p.anchor_after in pending_ids)]
        for p in ready or clone.proposals:
            if decision == "accept-all":
                clone.accept(p.id, decided_by=decider)
            else:
                clone.reject(p.id, decided_by=decider)
    return clone


def to_docx(doc: AimDocument, path: Union[str, Path], *,
            pending: str = "tracked") -> Path:
    """Write *doc* to *path* as a .docx file.

    ``pending`` — what to do with the pending lane: ``"tracked"`` emits Word
    revision markup, ``"accept-all"``/``"reject-all"`` resolve a throwaway
    copy first (the original document is never mutated).
    """
    if pending not in _PENDING_MODES:
        raise InvalidOperation(
            f"pending must be one of {_PENDING_MODES}, got {pending!r}")
    docx_mod = _require_docx()
    source = doc if pending == "tracked" else _resolve_copy(doc, pending)
    exporter = _Exporter(source, docx_mod)
    exporter.run()
    out = Path(path)
    exporter.out.save(str(out))
    return out
