"""Markdown → .aim.

CommonMark (plus tables and strikethrough) mapped into the closed .aim
vocabulary. Parsing is done by ``markdown-it-py`` (extra ``markdown``);
this module only walks its token stream — no runtime dependency lands on
the core package.

Mapping notes:

- Inline marks map to registry elements (``strong``/``em``/``s``/``code``/
  ``a``/``br``/``img``). Raw HTML in the source (blocks or inline) is
  **escaped and kept as visible text** — a converter must never inject
  markup the linter didn't vet.
- Links keep ``href`` only for registry-allowed schemes (http/https/
  mailto/#); anything else degrades to the link text.
- Images keep ``src`` for http/https/``data:image/``; anything else
  degrades to the alt text.
- Fence info strings (language tags) are dropped: the class vocabulary has
  no per-language classes in v0.1.
- Top-level lists and tables become .aim containers with item chunks
  (same rule as docling ingestion); nested lists/tables stay inline content
  of their list item; everything inside a blockquote is one atomic chunk.
"""

from __future__ import annotations

from typing import Any

from ..canonical import escape_attr, escape_text
from ..document import AimDocument, new_document
from ..events import Actor, external
from ..ingest import _containerize
from ..registry import REGISTRY

__all__ = ["from_markdown"]


def _md():
    try:
        from markdown_it import MarkdownIt
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise ImportError(
            "markdown support requires the 'markdown' extra: pip install 'aimformat[markdown]'"
        ) from exc
    return MarkdownIt("commonmark").enable("table").enable("strikethrough")


def _href_ok(href: str) -> bool:
    return REGISTRY.url_allowed("a.href", href)


def _src_ok(src: str) -> bool:
    return REGISTRY.url_allowed("img.src", src)


def _inline(children: list[Any] | None) -> str:
    """Render an inline token list to .aim inline markup."""
    out: list[str] = []
    suppressed_links = 0  # links whose scheme we refuse: keep text only
    for tok in children or []:
        t = tok.type
        if t == "text":
            out.append(escape_text(tok.content))
        elif t == "code_inline":
            out.append(f"<code>{escape_text(tok.content)}</code>")
        elif t == "strong_open":
            out.append("<strong>")
        elif t == "strong_close":
            out.append("</strong>")
        elif t == "em_open":
            out.append("<em>")
        elif t == "em_close":
            out.append("</em>")
        elif t == "s_open":
            out.append("<s>")
        elif t == "s_close":
            out.append("</s>")
        elif t == "link_open":
            href = tok.attrGet("href") or ""
            if _href_ok(href):
                out.append(f'<a href="{escape_attr(href)}">')
            else:
                suppressed_links += 1
        elif t == "link_close":
            if suppressed_links:
                suppressed_links -= 1
            else:
                out.append("</a>")
        elif t == "image":
            src = tok.attrGet("src") or ""
            alt = tok.content or ""
            if _src_ok(src):
                out.append(f'<img alt="{escape_attr(alt)}" src="{escape_attr(src)}">')
            elif alt:
                out.append(escape_text(f"[{alt}]"))
        elif t == "softbreak":
            out.append(" ")
        elif t == "hardbreak":
            out.append("<br>")
        elif t == "html_inline":
            out.append(escape_text(tok.content))
        elif tok.children:  # unknown container-ish inline: keep its text
            out.append(_inline(tok.children))
        elif tok.content:
            out.append(escape_text(tok.content))
    return "".join(out)


def _inline_text(children: list[Any] | None) -> str:
    """Plain text represented by an inline token list."""
    out: list[str] = []
    for tok in children or []:
        if tok.type in ("text", "code_inline", "html_inline", "image"):
            out.append(tok.content)
        elif tok.type in ("softbreak", "hardbreak"):
            out.append(" ")
        elif tok.children:
            out.append(_inline_text(tok.children))
        elif not tok.type.endswith(("_open", "_close")) and tok.content:
            out.append(tok.content)
    return "".join(out)


class _Walker:
    """Token-stream walker producing block markup strings."""

    def __init__(self, tokens: list[Any]):
        self.toks = tokens
        self.i = 0
        self.first_heading: str | None = None

    # ------------------------------------------------------------------
    def blocks(self, stop: str | None = None) -> list[str]:
        out: list[str] = []
        while self.i < len(self.toks):
            tok = self.toks[self.i]
            if stop and tok.type == stop:
                self.i += 1
                return out
            self.i += 1
            t = tok.type
            if t == "heading_open":
                inline = self.toks[self.i]
                self.i += 2  # inline + heading_close
                text = _inline(inline.children)
                if self.first_heading is None:
                    self.first_heading = _inline_text(inline.children)
                out.append(f"<{tok.tag}>{text}</{tok.tag}>")
            elif t == "paragraph_open":
                inline = self.toks[self.i]
                self.i += 2
                content = _inline(inline.children)
                if content:
                    out.append(f"<p>{content}</p>")
            elif t in ("fence", "code_block"):
                code = escape_text(tok.content.rstrip("\n"))
                out.append(f"<pre><code>{code}</code></pre>")
            elif t == "blockquote_open":
                inner = self.blocks(stop="blockquote_close")
                out.append("<blockquote>" + "".join(inner) + "</blockquote>")
            elif t in ("bullet_list_open", "ordered_list_open"):
                out.append(self._list(tok))
            elif t == "table_open":
                out.append(self._table())
            elif t == "hr":
                out.append("<hr>")
            elif t == "html_block":
                text = escape_text(tok.content.strip())
                if text:
                    out.append(f"<p>{text}</p>")
            elif t == "inline":  # stray inline (shouldn't happen at block level)
                content = _inline(tok.children)
                if content:
                    out.append(f"<p>{content}</p>")
            # *_close tokens and unknown block types fall through
        return out

    # ------------------------------------------------------------------
    def _list(self, open_tok: Any) -> str:
        tag = "ol" if open_tok.type == "ordered_list_open" else "ul"
        close = (
            f"{tag if tag != 'ol' else 'ordered_list'}_close"
            if tag == "ol"
            else "bullet_list_close"
        )
        items: list[str] = []
        while self.i < len(self.toks):
            tok = self.toks[self.i]
            self.i += 1
            if tok.type == close:
                break
            if tok.type == "list_item_open":
                items.append(self._list_item())
        return f"<{tag}>" + "".join(items) + f"</{tag}>"

    def _list_item(self) -> str:
        parts: list[str] = []
        while self.i < len(self.toks):
            tok = self.toks[self.i]
            self.i += 1
            t = tok.type
            if t == "list_item_close":
                break
            if t == "paragraph_open":
                inline = self.toks[self.i]
                self.i += 2
                content = _inline(inline.children)
                if not content:
                    continue
                # tight-list paragraphs are hidden: their text is the item
                parts.append(content if tok.hidden else f"<p>{content}</p>")
            elif t in ("bullet_list_open", "ordered_list_open"):
                parts.append(self._list(tok))
            elif t in ("fence", "code_block"):
                code = escape_text(tok.content.rstrip("\n"))
                parts.append(f"<pre><code>{code}</code></pre>")
            elif t == "table_open":
                parts.append(self._table())
            elif t == "blockquote_open":
                inner = self.blocks(stop="blockquote_close")
                parts.append("<blockquote>" + "".join(inner) + "</blockquote>")
            elif t == "heading_open":
                inline = self.toks[self.i]
                self.i += 2  # inline + heading_close
                content = _inline(inline.children)
                if content:  # headings demote to a bold line inside items
                    parts.append(f"<p><strong>{content}</strong></p>")
            elif t == "html_block":
                text = escape_text(tok.content.strip())
                if text:
                    parts.append(f"<p>{text}</p>")
            elif t == "hr":
                parts.append("<hr>")
        return "<li>" + "".join(parts) + "</li>"

    # ------------------------------------------------------------------
    def _table(self) -> str:
        head_rows: list[str] = []
        body_rows: list[str] = []
        current: list[str] | None = None
        cell_tag = "td"
        in_head = False
        while self.i < len(self.toks):
            tok = self.toks[self.i]
            self.i += 1
            t = tok.type
            if t == "table_close":
                break
            elif t == "thead_open":
                in_head = True
            elif t == "thead_close":
                in_head = False
            elif t == "tr_open":
                current = []
            elif t == "tr_close":
                if current is not None:
                    row = "<tr>" + "".join(current) + "</tr>"
                    (head_rows if in_head else body_rows).append(row)
                current = None
            elif t in ("th_open", "td_open"):
                cell_tag = "th" if t == "th_open" else "td"
                inline = self.toks[self.i]
                self.i += 2  # inline + *_close
                if current is not None:
                    current.append(f"<{cell_tag}>{_inline(inline.children)}</{cell_tag}>")
        html = "<table>"
        if head_rows:
            html += "<thead>" + "".join(head_rows) + "</thead>"
        if body_rows:
            html += "<tbody>" + "".join(body_rows) + "</tbody>"
        return html + "</table>"


def from_markdown(
    text: str,
    *,
    title: str | None = None,
    lang: str = "en",
    author: Actor | None = None,
    theme: dict[str, str] | None = None,
) -> AimDocument:
    """Build an :class:`AimDocument` from Markdown source.

    ``title`` defaults to the first heading's text (or "Imported document").
    ``author`` attributes the import events (default: ``external``
    ``markdown-import``).
    """
    tokens = _md().parse(text)
    walker = _Walker(tokens)
    blocks = walker.blocks()
    who = author or external("markdown-import")
    doc = new_document(
        title=title or walker.first_heading or "Imported document", lang=lang, theme=theme
    )
    with doc.batch():
        for markup in blocks:
            doc.add_chunk(_containerize(markup), author=who, explanation="Imported from markdown")
    return doc
