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
``text``/``paragraph`` ``<p>`` chunk; ``formatting`` flags become
                      ``strong/em/u/s/sub/sup``, ``hyperlink`` becomes
                      ``<a>`` when its scheme is registry-safe
``inline`` group      one ``<p>`` chunk joining the per-run children
                      (docling's shape for mixed-formatting paragraphs);
                      runs join with a space except at punctuation and
                      around sub/superscript
``code``              ``<pre><code>`` chunk
``formula``           ``<p>`` chunk (TeX source as text)
``list`` group        ``<ul>``/``<ol>`` container (orderedness read from the
                      items' ``enumerated`` flags); items → ``<li>`` chunks;
                      groups nested directly under a group become a nested
                      list inside the preceding item
``table``             ``<table>`` container; grid rows → ``<tr>`` chunks
                      (header rows → ``<thead>``/``<th>``, row/col spans
                      kept — cells belong to the row where they *start*);
                      a table caption follows as an ``<em>`` paragraph
``picture``           ``<figure>`` chunk (attribute-escaped data-URI
                      ``<img>`` when the conversion embedded images, else an
                      alt-text placeholder; attached text becomes ``<p>``)
``caption``           ``<figcaption>`` inside the owning figure
other grouping nodes  descended (``sheet``, ``form_area``, chapters, …) —
                      a subtree with content is never silently dropped
furniture layer       skipped (page headers/footers are not content)
====================  =====================================================

Page provenance (``prov.page_no``) is dropped — deliberately, still, in the
pagination-aware format: where content *landed* when some renderer flowed it
is a layout artifact, and fossilizing a PDF's soft breaks as hard
``<aim-page-break>`` chunks would be wrong. Explicit author intent (DOCX
``sectPr`` page setup, hard page breaks) is a different matter and is carried
over by the python-docx side pass in :mod:`.convert._docx_pages`. Tables and
lists nested inside a list item become that item's inline content.
"""

from __future__ import annotations

import re
from typing import Any

from .canonical import escape_attr, escape_text
from .document import AimDocument, new_document
from .events import Actor, external
from .registry import REGISTRY

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
        f"dict, got {type(source).__name__}"
    )


class _Resolver:
    """Follow ``{"$ref": "#/texts/0"}`` pointers into the document dict."""

    def __init__(self, doc: dict):
        self.doc = doc

    def deref(self, ref: dict | str) -> dict:
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


def _fmt_markup(node: dict) -> str:
    """A text item's inline markup: escaped text wrapped per the docling
    ``formatting`` flags and ``hyperlink`` — fixed nesting order
    ``a > strong > em > u > s > sub|sup`` (outermost first)."""
    out = _text_of(node)
    if not out:
        return out
    fmt = node.get("formatting") or {}
    script = fmt.get("script", "baseline")
    if script == "sub":
        out = f"<sub>{out}</sub>"
    elif script == "super":
        out = f"<sup>{out}</sup>"
    if fmt.get("strikethrough"):
        out = f"<s>{out}</s>"
    if fmt.get("underline"):
        out = f"<u>{out}</u>"
    if fmt.get("italic"):
        out = f"<em>{out}</em>"
    if fmt.get("bold"):
        out = f"<strong>{out}</strong>"
    link = node.get("hyperlink")
    # emit only registry-allowed schemes; anything else (file://, ftp://, …)
    # keeps its text — a converter must not produce documents the linter
    # then rejects (V009). Same predicate the linter itself uses.
    if link and REGISTRY.url_allowed("a.href", str(link)):
        out = f'<a href="{escape_attr(str(link))}">{out}</a>'
    return out


def _script_of(node: dict) -> str:
    return (node.get("formatting") or {}).get("script", "baseline")


_NO_SPACE_BEFORE = tuple(",.;:!?)]}»")
_NO_SPACE_AFTER = tuple("([{«")


def _hugs(prev: dict, cur: dict) -> bool:
    """Whether two adjacent inline runs join without a space. docling strips
    run-boundary whitespace (``text`` and ``orig`` alike), so the join is a
    heuristic: closing punctuation hugs left, opening brackets hug right,
    and script runs hug their base — strictly better than docling's own
    serializers, which space-join unconditionally ("H 2 O", "bold , then").

    Direction matters after a script run: a SUBSCRIPT is usually mid-word
    (H2O — glue both sides), while a SUPERSCRIPT usually ends its word
    (x², footnote markers — glue left only, keep the following space)."""
    if _script_of(cur) != "baseline":
        return True  # scripts always attach to the base on their left
    if _script_of(prev) == "sub":
        return True  # …O in H2O
    prev_text = prev.get("text") or ""
    cur_text = cur.get("text") or ""
    # some backends keep run-boundary whitespace (DOCX strips it, HTML/
    # programmatic documents may not): an existing boundary space must not
    # be doubled by the separator (Codex review, aimformat#2)
    if prev_text[-1:].isspace() or cur_text[:1].isspace():
        return True
    return cur_text.startswith(_NO_SPACE_BEFORE) or prev_text.endswith(_NO_SPACE_AFTER)


def _inline_group_markup(res: _Resolver, group: dict, _visited: set | None = None) -> str:
    """One ``inline`` group — the per-run shape docling emits for a
    paragraph with mixed formatting — joined back into a single block's
    inline content. Guards against ``$ref`` cycles in hostile dicts (the
    same case ``walk()`` handles) and skips furniture runs, mirroring the
    body walker's layer filter."""
    if _visited is None:
        _visited = set()
    ref = group.get("self_ref", "")
    if ref:
        if ref in _visited:
            return ""
        _visited.add(ref)
    parts: list[str] = []
    prev: dict | None = None
    for child in res.children(group):
        if not _is_body(child):
            continue  # page headers/footers never belong in a paragraph
        if child.get("text"):
            markup = _fmt_markup(child)
        elif child.get("children"):
            # nested grouping of any label: descend rather than silently
            # dropping a subtree (mirrors the body walker's philosophy)
            markup = _inline_group_markup(res, child, _visited)
        else:
            continue
        if not markup:
            continue
        if prev is not None and not _hugs(prev, child):
            parts.append(" ")
        parts.append(markup)
        prev = child
    return "".join(parts)


def _list_tag(res: _Resolver, group: dict) -> str:
    """docling-core exports ordered lists with group label ``list`` too; the
    orderedness lives on the items' ``enumerated`` flag."""
    if group.get("label") == "ordered_list":
        return "ol"
    items = [c for c in res.children(group) if c.get("label") == "list_item"]
    return "ol" if items and all(c.get("enumerated") for c in items) else "ul"


def _li_markup(res: _Resolver, item: dict) -> str:
    """One list item; nested lists and tables become its inline content."""
    inner = _fmt_markup(item)
    for child in res.children(item):
        label = child.get("label")
        if label in ("list", "ordered_list"):
            tag = _list_tag(res, child)
            nested = _list_items_markup(res, child)
            inner += f"<{tag}>{nested}</{tag}>"
        elif label == "table":
            nested_table = _table_markup(child)
            if nested_table:
                inner += nested_table
        elif label == "inline":
            # DOCX items with mixed run formatting carry their content in an
            # inline group (the item's own text is empty)
            inner += _inline_group_markup(res, child)
        elif label in ("text", "paragraph") and child.get("text"):
            inner += f"<p>{_fmt_markup(child)}</p>"
    return f"<li>{inner}</li>"


def _list_items_markup(res: _Resolver, group: dict) -> str:
    """A list group's items — including sub-groups docling parents directly
    on the group (not on an item), which become a nested list inside the
    preceding item (or a wrapper item when they lead)."""
    parts: list[str] = []
    for child in res.children(group):
        label = child.get("label")
        if label == "list_item":
            parts.append(_li_markup(res, child))
        elif label in ("list", "ordered_list"):
            tag = _list_tag(res, child)
            nested = f"<{tag}>{_list_items_markup(res, child)}</{tag}>"
            if parts:
                parts[-1] = parts[-1][: -len("</li>")] + nested + "</li>"
            else:
                parts.append(f"<li>{nested}</li>")
    return "".join(parts)


def _table_markup(table: dict) -> str | None:
    data = table.get("data") or {}
    grid = data.get("grid") or []
    if not grid:
        return None
    head_rows: list[str] = []
    body_rows: list[str] = []
    for ri, row in enumerate(grid):
        # a cell belongs to the row where it STARTS; the grid repeats
        # spanning cells in every row/column they cover
        own: list[dict] = []
        seen_cols: set[int] = set()
        for cell in row:
            if cell.get("start_row_offset_idx", ri) != ri:
                continue  # rowspan continuation from an earlier row
            col = cell.get("start_col_offset_idx", 0)
            if col in seen_cols:
                continue  # colspan repeat within this row
            seen_cols.add(col)
            own.append(cell)
        if not own:
            continue  # row consists entirely of continuations
        header = all(c.get("column_header") for c in own)
        cells = []
        for cell in own:
            tag = "th" if cell.get("column_header") or cell.get("row_header") else "td"
            attrs = ""
            if cell.get("col_span", 1) > 1:
                attrs += f' colspan="{cell["col_span"]}"'
            if cell.get("row_span", 1) > 1:
                attrs += f' rowspan="{cell["row_span"]}"'
            cells.append(f"<{tag}{attrs}>{escape_text(cell.get('text', ''))}</{tag}>")
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


_DATA_IMAGE_RE = re.compile(r"^data:image/[a-z+.-]+;base64,[A-Za-z0-9+/=\s]+$")


def _picture_markup(res: _Resolver, pic: dict) -> str:
    caption = _caption_text(res, pic)
    image = pic.get("image") or {}
    uri = image.get("uri") or ""
    alt = caption or "Imported picture"
    if isinstance(uri, str) and _DATA_IMAGE_RE.match(uri):
        # attribute-escaped AND grammar-checked: a quote or markup smuggled
        # into the URI must not become attributes or elements
        body = f'<img alt="{escape_attr(alt)}" src="{escape_attr(uri)}">'
    else:  # no (usable) embedded bytes: keep an honest textual placeholder
        body = f"<p><em>[picture: {escape_text(alt)}]</em></p>"
    for child in res.children(pic):  # picture-attached text (not captions)
        if child.get("label") in ("text", "paragraph") and child.get("text") and _is_body(child):
            body += f"<p>{_fmt_markup(child)}</p>"
    if caption:
        body += f"<figcaption>{escape_text(caption)}</figcaption>"
    return f"<figure>{body}</figure>"


def from_docling(
    source: Any,
    *,
    title: str | None = None,
    lang: str = "en",
    author: Actor | None = None,
    theme: dict[str, str] | None = None,
) -> AimDocument:
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

    visited: set[str] = set()  # $ref graphs from hostile input may cycle

    def walk(node: dict) -> None:
        nonlocal doc_title
        node_ref = node.get("self_ref", "")
        if node_ref:
            if node_ref in visited:
                return
            visited.add(node_ref)
        for child in res.children(node):
            if not _is_body(child):
                continue
            label = child.get("label", "")
            ref = child.get("self_ref", "")
            if label == "title":
                if child.get("text"):
                    if doc_title is None:
                        doc_title = child.get("text")
                    blocks.append(f"<h1>{_text_of(child)}</h1>")
                # DOCX-style hierarchical bodies parent section content
                # under the heading node — the section must still walk
                walk(child)
            elif label == "section_header":
                if child.get("text"):
                    level = min(int(child.get("level", 1)) + 1, _HEADING_CAP)
                    blocks.append(f"<h{level}>{_text_of(child)}</h{level}>")
                walk(child)
            elif label in (
                "text",
                "paragraph",
                "formula",
                "checkbox_selected",
                "checkbox_unselected",
                "footnote",
                "reference",
                "document_index",
            ):
                if child.get("text"):
                    blocks.append(f"<p>{_fmt_markup(child)}</p>")
            elif label == "inline":
                # a paragraph with mixed run formatting: one block, not one
                # chunk per run (descending would shatter it)
                markup = _inline_group_markup(res, child)
                if markup:
                    blocks.append(f"<p>{markup}</p>")
            elif label == "caption":
                if ref not in seen_caption_refs and child.get("text"):
                    blocks.append(f"<p>{_text_of(child)}</p>")
            elif label == "code":
                blocks.append(f"<pre><code>{_text_of(child)}</code></pre>")
            elif label in ("list", "ordered_list"):
                tag = _list_tag(res, child)
                items = _list_items_markup(res, child)
                if items:
                    blocks.append(f"<{tag}>{items}</{tag}>")
            elif label == "list_item":  # stray item outside a group
                blocks.append(f"<ul>{_li_markup(res, child)}</ul>")
            elif label == "table":
                markup = _table_markup(child)
                if markup:
                    blocks.append(markup)
                caption = _caption_text(res, child)
                if caption:  # tables cannot wrap a figcaption; keep the
                    blocks.append(f"<p><em>{escape_text(caption)}</em></p>")
            elif label == "picture":
                blocks.append(_picture_markup(res, child))
            elif child.get("children"):
                # any grouping construct (group/chapter/section/inline, but
                # also sheet/form_area/key_value_area/…) — descend rather
                # than silently dropping a subtree of real content
                walk(child)
            # childless unknown labels are skipped: forward compatibility

    walk(body)

    doc = new_document(
        title=doc_title or data.get("name") or "Imported document", lang=lang, theme=theme
    )
    source_name = data.get("name") or "document"
    with doc.batch():
        for markup in blocks:
            doc.add_chunk(
                _containerize(markup),
                author=who,
                explanation=f"Imported from {source_name!r} via docling ingestion",
            )
    return doc


def _containerize(markup: str) -> str:
    """Lists/tables arrive as plain markup; mark them as .aim containers with
    item chunks (ids get assigned by the document operations)."""
    if markup.startswith(("<ul>", "<ol>", "<table>")):
        from .canonical import serialize
        from .dom import parse_fragment

        root = parse_fragment(markup)[0]
        root.set("data-aim-container", "")  # placeholder; op assigns real id
        for el in root.iter():
            if el.tag in ("li", "tr") and el is not root:
                parent_ok = (
                    el.tag == "li"
                    and root.tag in ("ul", "ol")
                    or el.tag == "tr"
                    and root.tag == "table"
                )
                if parent_ok and _direct_item(root, el):
                    el.set("data-aim", "")
        return serialize(root)
    return markup


def _direct_item(root, el) -> bool:
    """True for li/tr that are items of *root* itself — li directly under a
    list root, tr under the table root or one of its thead/tbody/tfoot
    shells. Anything reached through li/td/th is nested chunk content."""
    from .dom import Element

    hop = {id(c): p for p in root.iter() for c in p.children if isinstance(c, Element)}
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
