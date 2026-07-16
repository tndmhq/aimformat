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
    what the format stores; word-level diffs are a viewer concern. This
    covers body chunks, chunks inside slides, list items and whole list
    containers, table rows and whole table containers, and anchored adds.
``pending="accept-all"`` / ``pending="reject-all"``
    The pending lane is resolved on a throwaway copy of the document —
    through the ordinary accept/reject machinery, decided by the ``external``
    actor ``docx-export`` — and the resolved body is exported clean.

Slides linearize (Word has no fixed canvas): a page break, then the slide's
chunks as flowing blocks in reading order — geometry and classes drop; text,
structure, marks, and the per-chunk pending lane survive. The same
degradation contract as the Markdown exporter, on pages instead of ``---``.
A faithful canvas export is the PDF's job (and a future PPTX exporter's).

Not represented in v0.1: ``move`` proposals, ``aim:theme``/``aim:doc``
proposals, and proposals targeting a *whole slide* (all export as unchanged
current content), plus hyperlink relationships (links render as text with
the URL in parentheses). These are deliberate scope cuts, not oversights.
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .document import AimDocument, Proposal, resolution_order
from .dom import Element, Text, parse_fragment
from .errors import InvalidOperation
from .events import external

if TYPE_CHECKING:  # pragma: no cover
    pass

__all__ = ["to_docx"]

_PENDING_MODES = ("tracked", "accept-all", "reject-all")
_MONO = "Consolas"

_BOLD_TAGS = {"strong", "b"}
_ITALIC_TAGS = {"em", "i"}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _require_docx():
    try:
        import docx  # noqa: F401

        return docx
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ImportError(
            "DOCX export needs python-docx. Install the extra:\n    pip install 'aimformat[docx]'"
        ) from exc


# --------------------------------------------------------------------------
# inline content -> (text, formatting) run specs
def _runs_of(el: Element, fmt: dict | None = None) -> list[dict]:
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


def _block_runs(el: Element) -> list[dict]:
    """Run specs for one block: a page-break chunk is a single page-break
    run (it has no text runs — without this, the tracked lane would emit a
    pending break as an empty revision paragraph)."""
    if el.tag == "aim-page-break":
        return [{"page_break": True}]
    return _runs_of(el)


def _apply_runs(paragraph, runs: list[dict]) -> None:
    for spec in runs:
        run = paragraph.add_run()
        if spec.get("page_break"):
            from docx.enum.text import WD_BREAK

            run.add_break(WD_BREAK.PAGE)
            continue
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
        if spec.get("page_break"):
            from docx.enum.text import WD_BREAK

            run.add_break(WD_BREAK.PAGE)
        elif spec.get("break"):
            run.add_break()
        else:
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


def _style_for(tag: str) -> str | None:
    if tag in _HEADINGS:
        return f"Heading {tag[1]}"
    if tag == "blockquote":
        return "Quote"
    if tag == "li":
        return "List Bullet"
    return None


def _block_children(el: Element) -> list[Element]:
    """The block-level pieces of one chunk (a section's children; a slide's
    children recursively, so a pending whole-slide add linearizes per block
    like an accepted slide; the element itself otherwise)."""
    if el.tag == "section":
        return el.elements()
    if el.tag == "aim-slide":
        out: list[Element] = []
        for child in el.elements():
            out.extend(_block_children(child))
        return out
    return [el]


_PX_VALUE_RE = re.compile(r"^(\d+(?:\.\d+)?)px$")


def _style_px(el: Element, prop: str) -> float | None:
    """A px-valued inline-style property of *el*, if declared."""
    for piece in (el.get("style") or "").split(";"):
        if ":" not in piece:
            continue
        name, val = (s.strip() for s in piece.split(":", 1))
        if name == prop:
            m = _PX_VALUE_RE.match(val)
            return float(m.group(1)) if m else None
    return None


# --------------------------------------------------------------------------
class _Exporter:
    def __init__(self, doc: AimDocument, docx_mod):
        self.aim = doc
        self.docx = docx_mod
        self.out = docx_mod.Document()
        self.rev = _Revisions()
        self._break_before_next = False  # set after a slide: next content opens a page
        self.pending_mod: dict[str, Proposal] = {}
        self.pending_del: dict[str, Proposal] = {}
        # adds keyed by (container, after) — every container, not just body
        self.adds_by_anchor: dict[tuple[str, str | None], list[Proposal]] = {}
        for p in doc.proposals:
            if p.action == "modify" and p.target and p.target not in ("aim:theme", "aim:doc"):
                self.pending_mod[p.target] = p
            elif p.action == "delete" and p.target:
                self.pending_del[p.target] = p
            elif p.action == "add":
                key = (p.anchor_container or "body", p.anchor_after)
                self.adds_by_anchor.setdefault(key, []).append(p)

    # -- top level -----------------------------------------------------------
    def run(self) -> None:
        title = self.aim.title
        if title:
            self.out.core_properties.title = title
        self._apply_page_setup()
        self._emit_anchored_adds("body", None)
        for construct in self.aim._state.constructs():
            self.emit_construct(construct)
            cid = construct.chunk_id or construct.container_id
            self._emit_anchored_adds("body", cid)
        # anything still unemitted (adds into containers that are themselves
        # pending-deleted) surfaces at the end rather than being silently
        # dropped
        for props in list(self.adds_by_anchor.values()):
            for prop in props:
                self._emit_add_paragraphs(prop)
        self.adds_by_anchor.clear()

    def _apply_page_setup(self) -> None:
        """The document's page setup → Word section properties, from the
        same resolution the PDF's @page rule uses (aimformat.pagesetup)."""
        from docx.enum.section import WD_ORIENT
        from docx.shared import Mm

        setup = self.aim.page_setup
        section = self.out.sections[0]
        section.orientation = (
            WD_ORIENT.LANDSCAPE if setup.orientation == "landscape" else WD_ORIENT.PORTRAIT
        )
        section.page_width = Mm(setup.page_width_mm)
        section.page_height = Mm(setup.page_height_mm)
        m = setup.margins_mm
        section.top_margin = Mm(m["top"])
        section.right_margin = Mm(m["right"])
        section.bottom_margin = Mm(m["bottom"])
        section.left_margin = Mm(m["left"])

    def _pop_adds(self, container: str, after: str | None) -> list[Proposal]:
        return self.adds_by_anchor.pop((container, after), [])

    def _emit_anchored_adds(self, container: str, anchor: str | None) -> None:
        for prop in self._pop_adds(container, anchor):
            self._emit_add_paragraphs(prop)
            self._emit_anchored_adds(container, prop.id)  # chained adds anchor on this one

    def _emit_add_paragraphs(self, prop: Proposal, style: str | None = None) -> None:
        els = self._payload_elements(prop)
        slide_payload = any(el.tag == "aim-slide" for el in els)
        # a pending add anchored after a slide (or adding a slide) belongs to
        # the next page, exactly like accepted content; the plain break
        # survives a rejection, which is the accepted-state layout
        if self._break_before_next or (slide_payload and self._has_content()):
            self._break_before_next = False
            if not self._ends_with_page_break():
                self._page_break()
        for el in els:
            for block in _block_children(el):
                if block.tag in ("ul", "ol"):
                    self._emit_added_list(block, prop)
                    continue
                if block.tag == "table":
                    self.emit_table(block, force="ins", prop=prop)
                    continue
                para = self.out.add_paragraph(
                    style=style or self._safe_style(_style_for(block.tag))
                )
                self.rev.ins(para, _block_runs(block), _actor_label(prop.author), prop.at)
        if slide_payload:
            self._break_before_next = True

    def _emit_added_list(self, el: Element, prop: Proposal) -> None:
        """A pending add of a whole list: one inserted paragraph per item —
        the ins half of ``emit_tracked_list_container``. Nested lists inside
        payload items flatten via ``_runs_of``, the same granularity the
        container-modify path has."""
        style = self._safe_style("List Bullet" if el.tag == "ul" else "List Number")
        label, date = _actor_label(prop.author), prop.at
        for li in el.elements():
            para = self.out.add_paragraph(style=style)
            self.rev.ins(para, _runs_of(li), label, date)

    def _payload_elements(self, prop: Proposal) -> list[Element]:
        return [n for n in parse_fragment(prop.payload_html or "") if isinstance(n, Element)]

    def _safe_style(self, name: str | None) -> str | None:
        if name is None:
            return None
        return name if self._has_style(name) else None

    def _has_style(self, name: str) -> bool:
        try:
            self.out.styles[name]
            return True
        except KeyError:
            return False

    # -- constructs ------------------------------------------------------------
    def emit_construct(self, el: Element) -> None:
        if el.tag == "aim-slide":
            self.emit_slide(el)
            return
        if self._break_before_next:
            self._break_before_next = False
            self._page_break()
        cid = el.chunk_id or el.container_id or ""
        prop = self.pending_del.get(cid) or self.pending_mod.get(cid)
        if el.container_id and el.tag in ("ul", "ol"):
            if prop is not None:
                self.emit_tracked_list_container(el, prop)
            else:
                self.emit_list(el)
        elif el.container_id and el.tag == "table":
            if prop is not None:
                self.emit_table(el, force="del")
                if prop.action == "modify":
                    for new_el in self._payload_elements(prop):
                        self.emit_table(new_el, force="ins", prop=prop)
            else:
                self.emit_table(el)
        elif prop is not None:
            self.emit_tracked_chunk(el, prop)
        else:
            for block in _block_children(el):
                self.emit_block(block, cid)

    # -- tracked replacements (exactly once per chunk, never per child) -------
    def emit_tracked_chunk(
        self, el: Element, prop: Proposal, style: str | None = None, *, payload: bool = True
    ) -> None:
        """``payload=False`` deletes *el* without re-emitting the modify
        payload — used for all but the last member of a run chunk, so the
        replacement lands exactly once per chunk id."""
        label, date = _actor_label(prop.author), prop.at
        for block in _block_children(el):
            para = self.out.add_paragraph(style=style or self._safe_style(_style_for(block.tag)))
            self.rev.dele(para, _block_runs(block), label, date)
        if payload and prop.action == "modify":
            for new_el in self._payload_elements(prop):
                for block in _block_children(new_el):
                    para = self.out.add_paragraph(
                        style=style or self._safe_style(_style_for(block.tag))
                    )
                    self.rev.ins(para, _block_runs(block), label, date)

    def emit_tracked_list_container(self, el: Element, prop: Proposal) -> None:
        label, date = _actor_label(prop.author), prop.at
        style = self._safe_style("List Bullet" if el.tag == "ul" else "List Number")
        for li in el.elements():
            para = self.out.add_paragraph(style=style)
            self.rev.dele(para, _runs_of(li), label, date)
        if prop.action == "modify":
            for new_el in self._payload_elements(prop):
                for li in new_el.elements():
                    para = self.out.add_paragraph(style=style)
                    self.rev.ins(para, _runs_of(li), label, date)

    # -- plain blocks -----------------------------------------------------------
    def emit_block(self, el: Element, cid: str, style: str | None = None) -> None:
        if el.tag == "figure":
            self.emit_figure(el)
            return
        if el.tag == "pre":
            self.emit_pre(el)
            return
        if el.tag == "table":  # atomic table chunk
            self.emit_table(el)
            return
        if el.tag == "hr":
            self.out.add_paragraph("—" * 12)
            return
        if el.tag == "aim-page-break":
            from docx.enum.text import WD_BREAK

            para = self.out.add_paragraph()
            para.add_run().add_break(WD_BREAK.PAGE)
            return
        if el.tag in ("ul", "ol"):  # atomic list chunk / nested list content
            self.emit_list(el)
            return
        para = self.out.add_paragraph(style=style or self._safe_style(_style_for(el.tag)))
        _apply_runs(para, _runs_of(el))

    def emit_pre(self, el: Element) -> None:
        text = el.text()
        para = self.out.add_paragraph()
        for i, line in enumerate(text.split("\n")):
            if i:
                para.add_run().add_break()
            run = para.add_run(line)
            run.font.name = _MONO

    def emit_slide(self, el: Element) -> None:
        """Linearize a fixed-canvas page: a page break, then the slide's
        chunks as flowing blocks in reading order (see the module
        docstring). Chunk-level proposals inside the slide ride the same
        tracked/resolve machinery as body chunks; a proposal targeting the
        slide container itself exports as unchanged current content.
        """
        sid = el.container_id or ""
        self._break_before_next = False
        if self._has_content() and not self._ends_with_page_break():
            self._page_break()
        opened_at = len(self.out.element.body)
        if sid:
            self._emit_anchored_adds(sid, None)
        for child in el.elements():
            self.emit_construct(child)
            if sid:
                self._emit_anchored_adds(sid, child.chunk_id or child.container_id)
        if len(self.out.element.body) == opened_at:
            # a blank canvas is still a page (PDF prints it): without at
            # least one paragraph the slide leaves no mark for
            # _has_content(), the next slide resets _break_before_next,
            # and the page silently vanishes from the DOCX
            self.out.add_paragraph()
        # content following the slide belongs to the next page — mirror of
        # the print layer's page-break-after; nothing is emitted when the
        # slide is last, so the document gains no trailing blank page
        self._break_before_next = True

    def _has_content(self) -> bool:
        body = self.out.element.body
        return any(child.tag.endswith("}p") or child.tag.endswith("}tbl") for child in body)

    def _ends_with_page_break(self) -> bool:
        """True when the last emitted paragraph is nothing but a page break —
        an explicit ``aim-page-break`` right before a slide already paged, and
        a second break would print a blank page."""
        from docx.oxml.ns import qn

        paras = [c for c in self.out.element.body if c.tag.endswith("}p")]
        if not paras:
            return False
        last = paras[-1]
        breaks = [b for b in last.findall(".//" + qn("w:br")) if b.get(qn("w:type")) == "page"]
        text = "".join(t.text or "" for t in last.findall(".//" + qn("w:t")))
        return bool(breaks) and not text

    def _page_break(self) -> None:
        from docx.enum.text import WD_BREAK

        para = self.out.add_paragraph()
        para.add_run().add_break(WD_BREAK.PAGE)

    def emit_figure(self, el: Element) -> None:
        img = el.find(lambda e: e.tag == "img")
        emitted = False
        if img is not None:
            src = img.get("src") or ""
            m = re.match(r"^data:image/[a-z+.-]+;base64,(.*)$", src, re.S)
            if m:
                try:
                    blob = base64.b64decode(m.group(1))
                    self.out.add_picture(io.BytesIO(blob), width=self._figure_width(el, img))
                    emitted = True
                except Exception:
                    emitted = False
            if not emitted:
                alt = img.get("alt") or "image"
                para = self.out.add_paragraph()
                run = para.add_run(f"[image: {alt}]")
                run.italic = True
                emitted = True
        for child in el.elements():  # non-caption content first, then captions
            if child.tag not in ("figcaption", "img", "svg"):
                self.emit_block(child, el.chunk_id or "")
        for cap in el.elements():
            if cap.tag == "figcaption":
                self.out.add_paragraph(cap.text(), style=self._safe_style("Caption"))

    def _figure_width(self, fig: Element, img: Element):
        """The authored image width (inline style, CSS px at 96 dpi — the
        img's own, else the figure's), capped to the page content box; the
        historical 4.5 in when nothing is declared."""
        from docx.shared import Inches

        px = _style_px(img, "width")
        if px is None:
            px = _style_px(fig, "width")
        inches = px / 96.0 if px is not None else 4.5
        setup = self.aim.page_setup
        margins = setup.margins_mm
        avail = (setup.page_width_mm - margins["left"] - margins["right"]) / 25.4
        return Inches(max(0.25, min(inches, avail)))

    # -- lists ---------------------------------------------------------------------
    def emit_list(self, el: Element, level: int = 0) -> None:
        base = "List Bullet" if el.tag == "ul" else "List Number"
        style = base if level == 0 else f"{base} {min(level + 1, 3)}"
        if not self._has_style(style):
            style = base if self._has_style(base) else None
        container_id = el.container_id
        if container_id:
            self._emit_list_adds(container_id, None, style)
        items = el.elements()
        i = 0
        while i < len(items):
            cid = items[i].chunk_id or ""
            group = [items[i]]  # a run chunk = consecutive same-id items
            while cid and i + 1 < len(items) and items[i + 1].chunk_id == cid:
                i += 1
                group.append(items[i])
            i += 1
            prop = (self.pending_del.get(cid) or self.pending_mod.get(cid)) if cid else None
            for li in group:
                nested = [c for c in li.elements() if c.tag in ("ul", "ol")]
                content = Element("li")
                content.children = [
                    c for c in li.children if not (isinstance(c, Element) and c.tag in ("ul", "ol"))
                ]
                if prop is not None:
                    self.emit_tracked_chunk(content, prop, style=style, payload=li is group[-1])
                else:
                    self.emit_block(content, cid, style=style)
                for sub in nested:
                    self.emit_list(sub, level + 1)
            if container_id and cid:
                self._emit_list_adds(container_id, cid, style)

    def _emit_list_adds(self, container: str, after: str | None, style: str | None) -> None:
        for prop in self._pop_adds(container, after):
            self._emit_add_paragraphs(prop, style=style)
            self._emit_list_adds(container, prop.id, style)

    # -- tables ----------------------------------------------------------------------
    def emit_table(
        self, el: Element, *, force: str | None = None, prop: Proposal | None = None
    ) -> None:
        rows = el.find_all(lambda e: e.tag == "tr")
        if not rows:
            return
        # true grid width: simulate the emit loop's cursor (spans shift later
        # cells right; a rowspan reaching below the last row is clamped), so
        # every (ri, ci) the loop touches exists in the table
        ncols = 0
        spanned: set[tuple[int, int]] = set()
        for ri, row in enumerate(rows):
            ci = 0
            for c in row.elements():
                if c.tag not in ("td", "th"):
                    continue
                while (ri, ci) in spanned:
                    ci += 1
                cs = int(c.get("colspan") or 1)
                rs = min(int(c.get("rowspan") or 1), len(rows) - ri)
                if cs > 1 or rs > 1:
                    for dr in range(rs):
                        for dc in range(cs):
                            spanned.add((ri + dr, ci + dc))
                ci += cs
            ncols = max(ncols, ci)
        table = self.out.add_table(rows=len(rows), cols=ncols)
        table.style = "Table Grid" if self._has_style("Table Grid") else None
        occupied: set[tuple[int, int]] = set()
        done_mods: set[str] = set()  # payload once per run chunk; later rows dele-only
        container_id = el.container_id
        for ri, row in enumerate(rows):
            rid = row.chunk_id or ""
            prop_mod = self.pending_mod.get(rid) if not force else None
            prop_del = self.pending_del.get(rid) if not force else None
            if force == "del":
                prop_del = prop  # container-level: everything deleted
            new_cells = None
            if prop_mod is not None and rid not in done_mods:
                done_mods.add(rid)
                payload = self._payload_elements(prop_mod)
                new_cells = [c for p in payload for c in p.elements() if c.tag in ("td", "th")]
            cells = [c for c in row.elements() if c.tag in ("td", "th")]
            ci = 0
            for orig_idx, cell_el in enumerate(cells):
                while (ri, ci) in occupied:
                    ci += 1
                colspan = min(int(cell_el.get("colspan") or 1), ncols - ci)
                rowspan = min(int(cell_el.get("rowspan") or 1), len(rows) - ri)
                cell = table.cell(ri, ci)
                if colspan > 1 or rowspan > 1:
                    cell = cell.merge(table.cell(ri + rowspan - 1, ci + colspan - 1))
                    for dr in range(rowspan):
                        for dc in range(colspan):
                            occupied.add((ri + dr, ci + dc))
                para = cell.paragraphs[0]
                runs = _runs_of(cell_el)
                if cell_el.tag == "th":
                    runs = [{**r, "bold": True} for r in runs]
                if force == "ins" and prop is not None:
                    self.rev.ins(para, runs, _actor_label(prop.author), prop.at)
                elif prop_del is not None:
                    self.rev.dele(para, runs, _actor_label(prop_del.author), prop_del.at)
                elif prop_mod is not None:
                    self.rev.dele(para, runs, _actor_label(prop_mod.author), prop_mod.at)
                    if new_cells and orig_idx < len(new_cells):
                        take = (
                            new_cells[orig_idx:]
                            if orig_idx == len(cells) - 1
                            else [new_cells[orig_idx]]
                        )
                        for nc in take:  # surplus new cells join the last one
                            self.rev.ins(
                                para, _runs_of(nc), _actor_label(prop_mod.author), prop_mod.at
                            )
                else:
                    _apply_runs(para, runs)
                ci += colspan
        # row-adds only after the content loop: inserting rows mid-loop would
        # shift the (ri, ci) coordinates the loop and the merges rely on
        if container_id and not force:
            orig_trs = [r._tr for r in table.rows]  # 1:1 with the AIM rows
            self._emit_row_adds(table, None, container_id, None, ncols, first=True)
            for ri, row in enumerate(rows):
                rid = row.chunk_id
                if rid:
                    self._emit_row_adds(table, orig_trs[ri], container_id, rid, ncols)

    def _emit_row_adds(
        self,
        table,
        anchor_tr,
        container: str,
        after: str | None,
        ncols: int,
        first: bool = False,
    ) -> None:
        """Insert pending row-adds as fully-inserted (w:ins) table rows,
        each positioned right after its anchor ``w:tr`` (the container start
        when ``first``)."""
        props = self._pop_adds(container, after)
        for prop in props:
            new_row = table.add_row()  # appended; repositioned below
            payload_cells = [
                c
                for p in self._payload_elements(prop)
                for c in p.elements()
                if c.tag in ("td", "th")
            ]
            for idx in range(min(len(payload_cells), ncols)):
                para = new_row.cells[idx].paragraphs[0]
                self.rev.ins(para, _runs_of(payload_cells[idx]), _actor_label(prop.author), prop.at)
            if first:
                table.rows[0]._tr.addprevious(new_row._tr)
            else:
                anchor_tr.addnext(new_row._tr)
            # chained adds anchor on the row just inserted
            self._emit_row_adds(table, new_row._tr, container, prop.id, ncols)


# --------------------------------------------------------------------------
def _resolve_copy(doc: AimDocument, decision: str) -> AimDocument:
    clone = AimDocument.loads(doc.dumps())
    decider = external("docx-export")
    # dependency-safe order (chained adds after their anchor, deletes last
    # per round) — shared with `aim accept/reject --all`
    for p in resolution_order(clone.proposals):
        if decision == "accept-all":
            clone.accept(p.id, decided_by=decider)
        else:
            clone.reject(p.id, decided_by=decider)
    return clone


def to_docx(doc: AimDocument, path: str | Path, *, pending: str = "tracked") -> Path:
    """Write *doc* to *path* as a .docx file.

    ``pending`` — what to do with the pending lane: ``"tracked"`` emits Word
    revision markup, ``"accept-all"``/``"reject-all"`` resolve a throwaway
    copy first (the original document is never mutated).
    """
    if pending not in _PENDING_MODES:
        raise InvalidOperation(f"pending must be one of {_PENDING_MODES}, got {pending!r}")
    docx_mod = _require_docx()
    source = doc if pending == "tracked" else _resolve_copy(doc, pending)
    exporter = _Exporter(source, docx_mod)
    exporter.run()
    out = Path(path)
    exporter.out.save(str(out))
    return out
