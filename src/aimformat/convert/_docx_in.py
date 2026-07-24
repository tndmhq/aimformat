"""DOCX → .aim, natively (extra ``docx``).

The importer walks docx-parser-converter's typed parse of the OOXML (via
the :mod:`._docx_seam` boundary) and emits `.aim` chunks directly — the
docling path (:func:`aimformat.from_docling`) remains for PDFs and any
DoclingDocument, but DOCX no longer flows through a model that cannot hold
styling.

What styling means here (spec §3.3's tiers, applied to ingestion):

- **Document-wide look → theme slots.** ``theme1.xml``'s minor/major latin
  faces become ``--aim-font-body``/``--aim-font-heading``; accents 1–4
  become the brand slots. One diffable head line carries the document's
  typographic identity.
- **The style-driven look is the document's rhythm, not markup.** A run
  whose size/face/colour comes from its *paragraph style* (Heading 2's
  14pt, Normal's Calibri) emits nothing — headings stay clean ``<h2>``
  chunks and the theme carries the look. Only **local intent** — direct
  formatting or a character style deviating from the paragraph's own
  style — becomes literal markup: paint (``color``/``background-color``),
  typography (``font-size:NNpt``/``font-family``), ``<mark>`` highlights,
  and the classic tags (``strong/em/u/s/sub/sup``).
- **Alignment is effective, not direct**: a centered Title style centers
  its ``<h1>`` (``text-center``), because alignment is visible structure.

One heading/paragraph/list-item/table-row per chunk, as everywhere else;
lists and tables containerize through the same step ``from_docling`` uses.
Explicit pagination intent (sectPr page setup, ``w:br type="page"``,
``pageBreakBefore``) lands inline during the walk — no post-hoc text
anchoring. Content dpc's model drops is recovered from the source ``w:p``
element (the seam pairs each with its dpc item): body-level textbox
paragraphs, content-control checkbox state, OMML equations as literal text,
and symbol-font glyphs (a curated Wingdings map). Not yet carried
(deliberately, tracked for the next pass): field codes, footnote refs,
tab-stop geometry, textboxes/equations/checkboxes *inside table cells*, and
everything Word means by floating objects. Table cell shading and widths
survive (§_table_markup); borders do not — the vocabulary has only border
utilities and border-colour paint, not per-side border geometry.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, BinaryIO

from ..canonical import escape_attr, escape_text
from ..document import AimDocument, new_document
from ..events import Actor, external
from ..ingest import _containerize
from ..pagesetup import _fmt_mm
from ..registry import REGISTRY
from ._docx_pages import _match_named_size
from ._docx_seam import (
    ParsedDocx,
    data_uri,
    effective_run_props,
    font_of,
    half_points_to_pt,
    highlight_hex,
    model_dump,
    paragraph_checkbox,
    paragraph_math_text,
    paragraph_run_baseline,
    parse_docx,
    resolve_color,
    shading_hex,
    symbol_char,
    textbox_paragraphs,
    twips_to_mm,
)

__all__ = ["convert_docx"]

_HEADING_STYLE = re.compile(r"^[Hh]eading\s*([1-9])$")
_ALIGN_CLASS = {
    "center": "text-center",
    "right": "text-right",
    "end": "text-right",
    "both": "text-justify",
    "distribute": "text-justify",
}
_PAGE_BREAK = "<aim-page-break></aim-page-break>"
_IMG_TAG = re.compile(r"<img\b[^>]*>")
_IMG_ONLY = re.compile(r"(?:<img\b[^>]*>)+")


def convert_docx(
    source: str | Path | bytes | BinaryIO,
    *,
    title: str | None = None,
    lang: str = "en",
    author: Actor | None = None,
    theme: dict[str, str] | None = None,
) -> AimDocument:
    """Convert a DOCX file (path, bytes, or stream) into an AimDocument."""
    name = Path(source).stem if isinstance(source, (str, Path)) else "document"
    parsed = parse_docx(str(source) if isinstance(source, Path) else source)
    conv = _Converter(parsed)
    blocks = conv.blocks()

    slots = _safe_theme_slots(parsed)
    if theme:
        slots.update(theme)  # the caller's slots win over the derived ones
    doc = new_document(
        title=title or conv.title_text or name or "Imported document",
        lang=lang,
        theme=slots or None,
    )
    who = author or external("docx-import")
    with doc.batch():
        for markup in blocks:
            doc.add_chunk(
                _containerize(markup),
                author=who,
                explanation=f"Imported from {name!r}",
            )
        page = conv.page_setup()
        if page is not None:
            try:
                doc.set_page_setup(
                    page, author=who, explanation="Page setup from the source document"
                )
            except Exception:  # degenerate sectPr: keep the content, skip the page
                pass
    return doc


def _safe_theme_slots(parsed: ParsedDocx) -> dict[str, str]:
    """The derived theme, minus any value the slot grammar cannot hold
    (a non-latin face name must degrade the slot, not fail the ingest)."""
    out: dict[str, str] = {}
    for slot, value in parsed.theme.slots().items():
        kind = REGISTRY.theme_slots.get(slot, {}).get("type")
        pattern = REGISTRY.theme_patterns.get(kind or "")
        if pattern is not None and pattern.fullmatch(value):
            out[slot] = value
    return out


class _Converter:
    """One parsed DOCX → an ordered list of .aim block markups."""

    def __init__(self, parsed: ParsedDocx):
        self.p = parsed
        self.title_text: str | None = None
        self._blocks: list[str] = []
        # consecutive list paragraphs buffer: (num_id, ilvl, markup, li attr)
        self._items: list[tuple[int, int, str, str]] = []

    # -- top level ---------------------------------------------------------

    def blocks(self) -> list[str]:
        for item, elem in self.p.content:
            kind = type(item).__name__
            if kind == "Paragraph":
                self._paragraph(item, elem)
            elif kind == "Table":
                self._flush_items()
                markup = self._table_markup(item)
                if markup:
                    self._blocks.append(markup)
        self._flush_items()
        return self._blocks

    def page_setup(self) -> dict | None:
        sect = getattr(self.p.document.body, "sect_pr", None)
        size = getattr(sect, "pg_sz", None) if sect is not None else None
        w_mm = twips_to_mm(getattr(size, "w", None))
        h_mm = twips_to_mm(getattr(size, "h", None))
        if w_mm is None or h_mm is None:
            return None
        match = _match_named_size(w_mm, h_mm)
        if match is None:
            return None  # odd custom sizes: named sizes only, as before
        named, orientation = match
        margins = getattr(sect, "pg_mar", None)

        def margin(tw: Any) -> str:
            # clamp into the registry bounds up front, and format through
            # the grammar-safe fixed-point formatter (never %g)
            mm = max(0.0, round(twips_to_mm(tw) or 0.0, 1))
            return _fmt_mm(min(mm, float(REGISTRY.margin_max_mm)))

        return {
            "size": named,
            "orientation": orientation,
            "margins": {
                "top": margin(getattr(margins, "top", None)),
                "right": margin(getattr(margins, "right", None)),
                "bottom": margin(getattr(margins, "bottom", None)),
                "left": margin(getattr(margins, "left", None)),
            },
        }

    # -- paragraphs --------------------------------------------------------

    def _paragraph(self, para: Any, elem: Any = None) -> None:
        direct = model_dump(para.p_pr)
        style_id = direct.pop("p_style", None)
        effective = self.p.resolver.resolve_with_direct(style_id, direct)

        if effective.get("page_break_before"):
            self._flush_items()
            self._blocks.append(_PAGE_BREAK)

        inline, trailing_break = self._inline_markup(para, style_id)
        inline = self._with_supplements(inline, elem)

        num_pr = effective.get("num_pr") or {}
        heading = self._heading_level(style_id, effective)
        if inline:
            if heading is not None:
                self._flush_items()
                self._blocks.append(self._block(f"h{heading}", inline, effective))
            elif num_pr.get("num_id") is not None:
                # list items carry their alignment class like any block —
                # a centered bullet is visible structure too
                self._items.append(
                    (
                        int(num_pr["num_id"]),
                        int(num_pr.get("ilvl") or 0),
                        inline,
                        self._class_attr(effective),
                    )
                )
            elif _IMG_ONLY.fullmatch(inline):
                # an image standing alone in its paragraph is a figure — the
                # system idiom (from_docling, the editor's atomic figure
                # nodes, and to_docx's figure exporter all speak <figure>)
                self._flush_items()
                self._blocks.extend(f"<figure>{img}</figure>" for img in _IMG_TAG.findall(inline))
            else:
                self._flush_items()
                self._blocks.append(self._block("p", inline, effective))
        if trailing_break:
            self._flush_items()
            self._blocks.append(_PAGE_BREAK)

        # textbox content (w:txbxContent) has no place in reading order, so it
        # follows its anchor paragraph as ordinary paragraphs; None element →
        # a textbox paragraph itself, which is not re-scanned (one level deep)
        if elem is not None:
            for tb_para in textbox_paragraphs(elem):
                self._paragraph(tb_para, None)

    def _with_supplements(self, inline: str, elem: Any) -> str:
        """Fold a paragraph's XML-only content into its inline markup: a
        content-control checkbox glyph leads, OMML equation text trails."""
        if elem is None:
            return inline
        prefix = ""
        checkbox = paragraph_checkbox(elem)
        if checkbox:
            prefix = escape_text(checkbox) + " "
        suffix = ""
        math = paragraph_math_text(elem)
        if math:
            suffix = " " + escape_text(math)
        return (prefix + inline + suffix).strip()

    def _heading_level(self, style_id: str | None, effective: dict) -> int | None:
        if style_id == "Title":
            return 1
        lvl = effective.get("outline_lvl")
        if lvl is not None and 0 <= int(lvl) <= 8:
            return min(int(lvl) + 1, 6)
        if style_id:
            m = _HEADING_STYLE.match(style_id)
            if m:
                return min(int(m.group(1)), 6)
        return None

    def _block(self, tag: str, inline: str, effective: dict) -> str:
        attr = self._class_attr(effective)
        if tag == "h1" and self.title_text is None:
            self.title_text = _plain_text(inline)
        return f"<{tag}{attr}>{inline}</{tag}>"

    @staticmethod
    def _class_attr(effective: dict) -> str:
        """The block's class attribute ('' when none): alignment classes."""
        align = _ALIGN_CLASS.get(str(effective.get("jc") or ""))
        return f' class="{align}"' if align else ""

    # -- inline content ----------------------------------------------------

    def _inline_markup(self, para: Any, style_id: str | None) -> tuple[str, bool]:
        """(inline markup, trailing-page-break?) for one paragraph.

        A ``w:br type="page"`` finishes the paragraph and breaks *after* it
        — the same reading the pagination side pass used.
        """
        parts: list[str] = []
        page_break = False
        for item in para.content:
            kind = type(item).__name__
            if kind == "Run":
                markup, saw_break = self._run_markup(item, style_id)
                parts.append(markup)
                page_break = page_break or saw_break
            elif kind == "Hyperlink":
                inner_parts: list[str] = []
                for run in item.content:
                    markup, saw_break = self._run_markup(run, style_id, fold_char_style=True)
                    inner_parts.append(markup)
                    page_break = page_break or saw_break
                inner = "".join(inner_parts)
                url = self.p.hyperlinks.get(item.r_id or "")
                if inner and url and REGISTRY.url_allowed("a.href", url):
                    title = getattr(item, "tooltip", None)
                    extra = f' title="{escape_attr(title)}"' if title else ""
                    parts.append(f'<a href="{escape_attr(url)}"{extra}>{inner}</a>')
                else:
                    parts.append(inner)  # unresolvable target keeps its text
        return "".join(parts).strip(), page_break

    def _run_markup(
        self, run: Any, para_style_id: str | None, *, fold_char_style: bool = False
    ) -> tuple[str, bool]:
        direct = model_dump(getattr(run, "r_pr", None))
        run_style = direct.pop("r_style", None)
        props = effective_run_props(self.p.resolver, para_style_id, run_style, direct)
        # the paragraph's own context (docDefaults + its style) is the
        # baseline: what IT sets is rhythm (suppressed); what the run adds
        # on top is local intent. Inside a hyperlink the character style is
        # role, not intent — <a> already carries link-ness, so the
        # Hyperlink style's blue underline folds into the baseline too.
        baseline = paragraph_run_baseline(self.p.resolver, para_style_id)
        if fold_char_style and run_style:
            baseline = self.p.resolver.merge_with_direct(
                baseline, self.p.resolver.resolve_run_properties(run_style)
            )

        text_parts: list[str] = []
        page_break = False
        for piece in getattr(run, "content", []):
            kind = type(piece).__name__
            if kind == "Text":
                text_parts.append(escape_text(piece.value))
            elif kind == "Break":
                if piece.type == "page":
                    page_break = True
                else:
                    text_parts.append("<br>")
            elif kind == "CarriageReturn":
                text_parts.append("<br>")
            elif kind == "TabChar":
                text_parts.append(" ")
            elif kind == "NoBreakHyphen":
                text_parts.append("‑")
            elif kind == "Symbol":
                glyph = symbol_char(getattr(piece, "font", None), getattr(piece, "char", None))
                if glyph:
                    text_parts.append(escape_text(glyph))
            elif kind == "Drawing":
                img = self._drawing_markup(piece)
                if img:
                    text_parts.append(img)
            # SoftHyphen, bookmarks, field plumbing, note references: no text
        out = "".join(text_parts)
        if not out:
            return "", page_break
        return self._decorate(out, props, baseline), page_break

    def _decorate(self, out: str, props: dict, baseline: dict) -> str:
        """Wrap run text per its effective props: span (styles) innermost,
        then mark, then the fixed tag order sub|sup < s < u < em < strong —
        matching the docling mapper's nesting so both importers agree."""
        styles: list[tuple[str, str]] = []
        color = resolve_color(props.get("color"), self.p.theme)
        if color and color != resolve_color(baseline.get("color"), self.p.theme):
            styles.append(("color", color))
        shade = shading_hex(props.get("shd"))
        highlight = highlight_hex(props.get("highlight"))
        if shade and not highlight and shade != shading_hex(baseline.get("shd")):
            styles.append(("background-color", shade))
        size = half_points_to_pt(props.get("sz"))
        if size is not None and size != half_points_to_pt(baseline.get("sz")):
            styles.append(("font-size", f"{size:g}pt"))
        face = font_of(props, self.p.theme)
        if face and face != font_of(baseline, self.p.theme):
            if REGISTRY.style_patterns["font-family"].fullmatch(face):
                styles.append(("font-family", face))

        caps = bool(props.get("caps")) and not baseline.get("caps")
        if styles or caps:
            # one span carries both: caps as the class, literal styling inline
            cls = ' class="uppercase"' if caps else ""
            sty = ""
            if styles:
                order = {p: i for i, p in enumerate(REGISTRY.style_prop_order)}
                styles.sort(key=lambda kv: order.get(kv[0], 99))
                decl = "; ".join(f"{k}:{v}" for k, v in styles)
                sty = f' style="{escape_attr(decl)}"'
            out = f"<span{cls}{sty}>{out}</span>"
        if highlight and highlight != highlight_hex(baseline.get("highlight")):
            paint = "" if highlight == "#ffff00" else f' style="background-color:{highlight}"'
            out = f"<mark{paint}>{out}</mark>"
        # the classic marks are deviations too: a Heading style's own bold
        # is rhythm the stylesheet already renders, not a <strong> to store
        va = str(props.get("vert_align") or "")
        if va in ("subscript", "superscript") and va != str(baseline.get("vert_align") or ""):
            out = f"<sub>{out}</sub>" if va == "subscript" else f"<sup>{out}</sup>"
        if (props.get("strike") or props.get("dstrike")) and not (
            baseline.get("strike") or baseline.get("dstrike")
        ):
            out = f"<s>{out}</s>"
        if _underlined(props) and not _underlined(baseline):
            out = f"<u>{out}</u>"
        if props.get("i") and not baseline.get("i"):
            out = f"<em>{out}</em>"
        if props.get("b") and not baseline.get("b"):
            out = f"<strong>{out}</strong>"
        return out

    def _drawing_markup(self, drawing: Any) -> str:
        container = getattr(drawing, "inline", None) or getattr(drawing, "anchor", None)
        if container is None:
            return ""
        doc_pr = getattr(container, "doc_pr", None)
        alt = getattr(doc_pr, "description", None) or getattr(doc_pr, "name", None) or "image"
        blip = _dig(container, "graphic", "graphic_data", "pic", "blip_fill", "blip")
        rid = getattr(blip, "embed", None) if blip is not None else None
        image = self.p.images.get(rid or "")
        if image is None:
            return f"<em>[picture: {escape_text(str(alt))}]</em>"
        extent = getattr(container, "extent", None)
        width = getattr(extent, "cx", None) if extent is not None else None
        # authored size rides the whitelisted geometry style, not an attribute
        wattr = f' style="width:{round(int(width) / 9525)}px"' if width else ""
        return f'<img alt="{escape_attr(str(alt))}" src="{escape_attr(data_uri(image))}"{wattr}>'

    # -- lists -------------------------------------------------------------

    def _flush_items(self) -> None:
        if not self._items:
            return
        items = self._items
        self._items = []
        for num_id, group in _group_runs(items):
            self._blocks.append(self._list_markup(num_id, group))

    def _list_markup(self, num_id: int, items: list[tuple[int, int, str, str]]) -> str:
        # nest from the group's MINIMUM level, not the first item's: a list
        # that starts indented and later outdents must keep the outdented
        # items (starting deeper would drop everything below the entry
        # level when the walk returns)
        start = min(ilvl for _, ilvl, _, _ in items)
        tag = "ol" if self._ordered(num_id, start) else "ul"
        body, _ = self._nest(items, 0, start)
        return f"<{tag}>{body}</{tag}>"

    def _nest(self, items: list[tuple[int, int, str, str]], i: int, level: int) -> tuple[str, int]:
        parts: list[str] = []
        while i < len(items):
            _, ilvl, markup, attr = items[i]
            if ilvl < level:
                break
            if ilvl > level:
                nested, i = self._nest(items, i, ilvl)
                tag = "ol" if self._ordered(items[i - 1][0], ilvl) else "ul"
                nested_markup = f"<{tag}>{nested}</{tag}>"
                if parts:
                    parts[-1] = parts[-1][: -len("</li>")] + nested_markup + "</li>"
                else:
                    parts.append(f"<li>{nested_markup}</li>")
                continue
            parts.append(f"<li{attr}>{markup}</li>")
            i += 1
        return "".join(parts), i

    def _ordered(self, num_id: int, ilvl: int) -> bool:
        numbering = self.p.numbering
        if numbering is None:
            return False
        abstract_id = next(
            (n.abstract_num_id for n in getattr(numbering, "num", []) if n.num_id == num_id),
            None,
        )
        if abstract_id is None:
            return False
        for ab in getattr(numbering, "abstract_num", []):
            if getattr(ab, "abstract_num_id", None) == abstract_id:
                for lvl in getattr(ab, "lvl", []) or []:
                    if getattr(lvl, "ilvl", None) == ilvl:
                        return getattr(lvl, "num_fmt", None) not in (None, "bullet", "none")
        return False

    # -- tables ------------------------------------------------------------

    def _table_markup(self, table: Any) -> str | None:
        rows = getattr(table, "tr", []) or []
        if not rows:
            return None
        # v_merge continuation cells collapse into the restart cell's rowspan
        spans: dict[tuple[int, int], int] = {}  # (row, col) -> rowspan
        skip: set[tuple[int, int]] = set()
        grid: list[list[tuple[Any, int]]] = []  # (cell, colspan) per row
        for ri, row in enumerate(rows):
            col = 0
            cells: list[tuple[Any, int]] = []
            for cell in getattr(row, "tc", []) or []:
                pr = getattr(cell, "tc_pr", None)
                colspan = int(getattr(pr, "grid_span", None) or 1)
                # dpc models w:vMerge as a plain string: 'restart' opens a
                # vertical span, 'continue' (its default for a bare w:vMerge)
                # extends it
                vmerge = getattr(pr, "v_merge", None)
                if vmerge == "restart":
                    spans[(ri, col)] = 1
                elif vmerge is not None:
                    for above in range(ri - 1, -1, -1):
                        if (above, col) in spans:
                            spans[(above, col)] += 1
                            break
                    skip.add((ri, col))
                cells.append((cell, colspan))
                col += colspan
            grid.append(cells)
        head: list[str] = []
        body: list[str] = []
        for ri, cells in enumerate(grid):
            header_row = bool(getattr(getattr(rows[ri], "tr_pr", None), "tbl_header", None))
            out: list[str] = []
            col = 0
            for cell, colspan in cells:
                if (ri, col) in skip:
                    col += colspan
                    continue
                tag = "th" if header_row else "td"
                attrs = ""
                if colspan > 1:
                    attrs += f' colspan="{colspan}"'
                rowspan = spans.get((ri, col), 1)
                if rowspan > 1:
                    attrs += f' rowspan="{rowspan}"'
                attrs += self._cell_style(cell)
                out.append(f"<{tag}{attrs}>{self._cell_markup(cell)}</{tag}>")
                col += colspan
            row_html = "<tr>" + "".join(out) + "</tr>"
            (head if header_row else body).append(row_html)
        html = "<table>"
        if head:
            html += "<thead>" + "".join(head) + "</thead>"
        if body:
            html += "<tbody>" + "".join(body) + "</tbody>"
        return html + "</table>"

    def _cell_style(self, cell: Any) -> str:
        """A cell's whitelisted geometry+paint style: fixed column width and
        shading fill. Borders are deliberately not carried — the vocabulary
        has border utilities and border-colour paint, not the per-side border
        geometry OOXML cells describe, so recovering them faithfully is not
        possible and a lossy approximation would mislead."""
        pr = getattr(cell, "tc_pr", None)
        if pr is None:
            return ""
        styles: list[tuple[str, str]] = []
        width = _cell_width_px(getattr(pr, "tc_w", None))
        if width:
            styles.append(("width", f"{width}px"))
        fill = shading_hex(model_dump(getattr(pr, "shd", None)))
        if fill:
            styles.append(("background-color", fill))
        if not styles:
            return ""
        order = {p: i for i, p in enumerate(REGISTRY.style_prop_order)}
        styles.sort(key=lambda kv: order.get(kv[0], 99))
        decl = "; ".join(f"{k}:{v}" for k, v in styles)
        return f' style="{escape_attr(decl)}"'

    def _cell_markup(self, cell: Any) -> str:
        paras: list[str] = []
        nested: list[str] = []
        for item in getattr(cell, "content", []) or []:
            kind = type(item).__name__
            if kind == "Paragraph":
                direct = model_dump(item.p_pr)
                style_id = direct.pop("p_style", None)
                inline, _ = self._inline_markup(item, style_id)
                if inline:
                    paras.append(inline)
            elif kind == "Table":
                markup = self._table_markup(item)
                if markup:
                    nested.append(markup)
        if len(paras) == 1 and not nested:
            return paras[0]
        return "".join(f"<p>{p}</p>" for p in paras) + "".join(nested)


def _group_runs(items: list[tuple[int, int, str, str]]):
    """Consecutive items sharing a num_id form one list."""
    start = 0
    for i in range(1, len(items) + 1):
        if i == len(items) or items[i][0] != items[start][0]:
            yield items[start][0], items[start:i]
            start = i


def _underlined(props: dict) -> bool:
    underline = model_dump(props.get("u"))
    return underline.get("val") not in (None, "none")


def _cell_width_px(tc_w: Any) -> int | None:
    """A ``w:tcW`` measurement → integer px, or None. Only fixed ``dxa``
    (twentieths of a point) widths carry over; ``pct``/``auto`` have no
    px equivalent. 1px = 0.75pt, so px = (dxa / 20) / 0.75 = dxa / 15."""
    w = model_dump(tc_w)
    if w.get("type") != "dxa":
        return None
    dxa = w.get("w")
    if not isinstance(dxa, (int, float)) or dxa <= 0:
        return None
    return round(dxa / 15)


def _dig(obj: Any, *names: str) -> Any:
    for name in names:
        if obj is None:
            return None
        obj = getattr(obj, name, None)
    return obj


_TAG = re.compile(r"<[^>]+>")


def _plain_text(markup: str) -> str:
    from ..dom import Element, parse_fragment

    try:
        text = "".join(
            node.text() if isinstance(node, Element) else getattr(node, "data", "")
            for node in parse_fragment(markup)
        )
    except Exception:
        text = _TAG.sub("", markup)
    return text.strip()
