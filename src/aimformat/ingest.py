"""Ingestion: DoclingDocument → .aim.

`docling <https://github.com/docling-project/docling>`_ converts PDF, DOCX,
PPTX, images, and HTML into a structured ``DoclingDocument``. This module
maps that structure onto .aim chunks, so any format docling reads becomes an
editable, proposal-carrying .aim file::

    from docling.document_converter import DocumentConverter
    import aimformat as aim

    result = DocumentConverter().convert("contract.docx")
    doc = aim.from_docling(result.document, title="Contract")
    doc.save("contract.aim")

Design notes (patterns adapted from a previous project's editor pipeline,
where DOCX/PDF ingestion produced chunk-granular documents that LLMs then
edited by chunk id):

- **No runtime dependency on docling.** The mapper consumes the *exported
  dict* shape (``DoclingDocument.export_to_dict()``); passing the object
  works too (duck-typed). Heavy converters stay out of this package.
- **Ingestion is history.** Every mapped block lands through the ordinary
  document operations in one batch, so the resulting file's event log records
  the import (author defaults to the ``external`` actor ``docling-ingest``)
  and verifies like any other .aim history.
- **Chunk granularity mirrors authorship**: one heading/paragraph/list item/
  table row per chunk — the same unit the previous project found LLMs and
  reviewers work best at.

Mapping (docling label → .aim):

====================  =====================================================
``title``             ``<h1>`` chunk (also the document title by default)
``section_header``    ``<h2>``–``<h6>`` chunk (docling level + 1, capped)
``text``/``paragraph`` ``<p>`` chunk
``code``              ``<pre><code>`` chunk
``formula``           ``<p>`` chunk (TeX source as text)
``list`` group        ``<ul>`` container; items → ``<li>`` chunks
``ordered_list``      ``<ol>`` container; items → ``<li>`` chunks
``table``             ``<table>`` container; grid rows → ``<tr>`` chunks
                      (header rows → ``<thead>``/``<th>``, spans kept)
``picture``           ``<figure>`` chunk (data-URI ``<img>`` when the
                      conversion embedded images, else alt-text only)
``caption``           ``<figcaption>`` inside the owning figure
furniture layer       skipped (page headers/footers are not content)
====================  =====================================================

Page provenance (``prov.page_no``) is dropped: .aim v0.1 has no pagination
model. Nested list groups flatten into their parent item's content.
"""
from __future__ import annotations

from typing import Any, Optional, Union

from .canonical import escape_attr, escape_text
from .document import AimDocument, new_document
from .events import Actor, external

__all__ = ["from_docling"]

_HEADING_CAP = 6


def _as_dict(source: Any) -> dict:
    if isinstance(source, dict):
        return source
    export = getattr(source, "export_to_dict", None)
    if callable(export):
        return export()
    raise TypeError(
        "from_docling() expects a DoclingDocument or its export_to_dict() "
        f"dict, got {type(source).__name__}")


class _Resolver:
    """Follow ``{"$ref": "#/texts/0"}`` pointers into the document dict."""

    def __init__(self, doc: dict):
        self.doc = doc

    def deref(self, ref: Union[dict, str]) -> dict:
        path = ref["$ref"] if isinstance(ref, dict) else ref
        node: Any = self.doc
        for part in path.lstrip("#/").split("/"):
            node = node[int(part)] if part.isdigit() else node[part]
        return node

    def children(self, node: dict) -> list[dict]:
        return [self.deref(ref) for ref in node.get("children", [])]


def _is_body(node: dict) -> bool:
    return node.get("content_layer", "body") == "body"


def _text_of(node: dict) -> str:
    return escape_text(node.get("text", "") or "")


def _list_tag(res: _Resolver, group: dict) -> str:
    """docling-core exports ordered lists with group label ``list`` too; the
    orderedness lives on the items' ``enumerated`` flag."""
    if group.get("label") == "ordered_list":
        return "ol"
    items = [c for c in res.children(group) if c.get("label") == "list_item"]
    return "ol" if items and all(c.get("enumerated") for c in items) else "ul"


def _li_markup(res: _Resolver, item: dict) -> str:
    """One list item, flattening nested list groups into its content."""
    inner = _text_of(item)
    for child in res.children(item):
        if child.get("label") in ("list", "ordered_list"):
            tag = _list_tag(res, child)
            nested = "".join(_li_markup(res, li) for li in res.children(child)
                             if li.get("label") == "list_item")
            inner += f"<{tag}>{nested}</{tag}>"
    return f"<li>{inner}</li>"


def _table_markup(table: dict) -> Optional[str]:
    data = table.get("data") or {}
    grid = data.get("grid") or []
    if not grid:
        return None
    head_rows, body_rows = [], []
    for row in grid:
        header = all(c.get("column_header") for c in row) and bool(row)
        cells = []
        emitted: set[tuple[int, int]] = set()
        for cell in row:
            key = (cell.get("start_row_offset_idx", 0),
                   cell.get("start_col_offset_idx", 0))
            if key in emitted:
                continue  # spanned cells repeat in the grid; emit once
            emitted.add(key)
            if cell.get("start_row_offset_idx") != row[0].get(
                    "start_row_offset_idx"):
                continue  # continuation of a rowspan from an earlier row
            tag = "th" if cell.get("column_header") or cell.get("row_header") \
                else "td"
            attrs = ""
            if cell.get("col_span", 1) > 1:
                attrs += f' colspan="{cell["col_span"]}"'
            if cell.get("row_span", 1) > 1:
                attrs += f' rowspan="{cell["row_span"]}"'
            cells.append(f"<{tag}{attrs}>{escape_text(cell.get('text', ''))}"
                         f"</{tag}>")
        row_html = "<tr>" + "".join(cells) + "</tr>"
        (head_rows if header else body_rows).append(row_html)
    if not head_rows and not body_rows:
        return None
    html = "<table>"
    if head_rows:
        html += "<thead>" + "".join(head_rows) + "</thead>"
    if body_rows:
        html += "<tbody>" + "".join(body_rows) + "</tbody>"
    return html + "</table>"


def _caption_text(res: _Resolver, node: dict) -> str:
    parts = []
    for ref in node.get("captions", []):
        cap = res.deref(ref)
        if cap.get("text"):
            parts.append(cap["text"])
    return " ".join(parts)


def _picture_markup(res: _Resolver, pic: dict) -> str:
    caption = _caption_text(res, pic)
    image = pic.get("image") or {}
    uri = image.get("uri") or ""
    alt = caption or "Imported picture"
    if isinstance(uri, str) and uri.startswith("data:image/"):
        body = f'<img alt="{escape_attr(alt)}" src="{uri}">'
    else:  # no embedded bytes: keep an honest textual placeholder
        body = f"<p><em>[picture: {escape_text(alt)}]</em></p>"
    if caption:
        body += f"<figcaption>{escape_text(caption)}</figcaption>"
    return f"<figure>{body}</figure>"


def from_docling(source: Any, *, title: Optional[str] = None,
                 lang: str = "en", author: Optional[Actor] = None,
                 theme: Optional[dict[str, str]] = None) -> AimDocument:
    """Build an :class:`AimDocument` from a DoclingDocument (or its dict).

    ``author`` attributes the ingestion events; it defaults to the
    ``external`` actor ``docling-ingest``.
    """
    data = _as_dict(source)
    res = _Resolver(data)
    who = author or external("docling-ingest")

    body = data.get("body") or {}
    blocks: list[str] = []
    doc_title = title
    seen_caption_refs: set[str] = set()
    for table in data.get("tables", []) + data.get("pictures", []):
        for ref in table.get("captions", []):
            seen_caption_refs.add(ref["$ref"] if isinstance(ref, dict) else ref)

    def walk(node: dict) -> None:
        nonlocal doc_title
        for child in res.children(node):
            if not _is_body(child):
                continue
            label = child.get("label", "")
            ref = child.get("self_ref", "")
            if label == "title":
                if doc_title is None:
                    doc_title = child.get("text") or None
                blocks.append(f"<h1>{_text_of(child)}</h1>")
            elif label == "section_header":
                level = min(int(child.get("level", 1)) + 1, _HEADING_CAP)
                blocks.append(f"<h{level}>{_text_of(child)}</h{level}>")
            elif label in ("text", "paragraph", "formula", "checkbox_selected",
                           "checkbox_unselected", "footnote"):
                if child.get("text"):
                    blocks.append(f"<p>{_text_of(child)}</p>")
            elif label == "caption":
                if ref not in seen_caption_refs and child.get("text"):
                    blocks.append(f"<p>{_text_of(child)}</p>")
            elif label == "code":
                blocks.append(f"<pre><code>{_text_of(child)}</code></pre>")
            elif label in ("list", "ordered_list"):
                tag = _list_tag(res, child)
                items = "".join(_li_markup(res, li) for li in res.children(child)
                                if li.get("label") == "list_item")
                if items:
                    blocks.append(f"<{tag}>{items}</{tag}>")
            elif label == "list_item":  # stray item outside a group
                blocks.append(f"<ul>{_li_markup(res, child)}</ul>")
            elif label == "table":
                markup = _table_markup(child)
                if markup:
                    blocks.append(markup)
            elif label == "picture":
                blocks.append(_picture_markup(res, child))
            elif label in ("group", "unspecified", "chapter", "section",
                           "inline"):
                walk(child)
            # unknown labels are skipped deliberately: forward compatibility

    walk(body)

    doc = new_document(title=doc_title or data.get("name") or "Imported document",
                       lang=lang, theme=theme)
    source_name = data.get("name") or "document"
    with doc.batch():
        for markup in blocks:
            doc.add_chunk(_containerize(markup), author=who,
                          explanation=f"Imported from {source_name!r} via "
                                      "docling ingestion")
    return doc


def _containerize(markup: str) -> str:
    """Lists/tables arrive as plain markup; mark them as .aim containers with
    item chunks (ids get assigned by the document operations)."""
    if markup.startswith(("<ul>", "<ol>", "<table>")):
        from .dom import parse_fragment
        from .canonical import serialize
        root = parse_fragment(markup)[0]
        root.set("data-aim-container", "")  # placeholder; op assigns real id
        for el in root.iter():
            if el.tag in ("li", "tr") and el is not root:
                parent_ok = el.tag == "li" and root.tag in ("ul", "ol") or \
                    el.tag == "tr" and root.tag == "table"
                if parent_ok and _direct_item(root, el):
                    el.set("data-aim", "")
        return serialize(root)
    return markup


def _direct_item(root, el) -> bool:
    """True for li/tr that are items of *root* itself — li directly under a
    list root, tr under the table root or one of its thead/tbody/tfoot
    shells. Anything reached through li/td/th is nested chunk content."""
    from .dom import Element
    hop = {id(c): p for p in root.iter() for c in p.children
           if isinstance(c, Element)}
    chain = []
    parent = hop.get(id(el))
    while parent is not None and parent is not root:
        chain.append(parent.tag)
        parent = hop.get(id(parent))
    if parent is not root:
        return False
    if el.tag == "li":
        return chain == []
    return all(t in ("thead", "tbody", "tfoot") for t in chain)
