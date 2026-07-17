"""`.aim` → Markdown.

A stdlib chunk walker over the canonical document tree. Fidelity notes:

- Marks map back to Markdown where Markdown has the concept (``strong``/
  ``b`` → ``**``, ``em``/``i`` → ``*``, ``s`` → ``~~``, ``code`` →
  backticks, links, images, ``br`` → hard break). ``u``/``mark``/``sub``/
  ``sup``/``span`` have no Markdown equivalent and degrade to their text.
- Escaping is defensive both inline (``\\ ` * _ [ ] ~ & <``) and at line
  starts (``#``, ``>``, list markers, thematic breaks, setext underlines),
  so prose that *looks like* Markdown syntax survives a round trip.
- Code spans/fences size their delimiters past the longest backtick run in
  the content (CommonMark rule).
- Tables: pipes are escaped in every cell fragment (GFM honors ``\\|``
  inside code spans in tables); ``<br>`` in a cell becomes a space; only
  the first ``thead`` row is the header row (extra head rows demote to
  body — Markdown has single-row headers).
- Classes, inline geometry, and the theme are presentation: dropped.
- Slides render as thematic breaks followed by their chunks in reading
  order.
- ``pending="drop"`` (default) exports the accepted document only;
  ``pending="criticmarkup"`` also renders the pending lane as CriticMarkup
  (``{~~old~>new~~}``, ``{++added++}``, ``{--deleted--}``, explanations as
  ``{>>comments<<}``) — covering chunk, list-item, table-row, and
  container-level proposals; critic delimiters occurring in content are
  neutralized with a zero-width space so spans cannot terminate early.
"""

from __future__ import annotations

import re

from ..document import AimDocument, Proposal
from ..dom import Element, Text, parse_html
from ..errors import InvalidOperation

__all__ = ["to_markdown"]

_SKIP_TAGS = {"aim-proposals", "aim-assets", "script", "style", "template"}
_BLOCK_TAGS = {
    "aim-slide",
    "ul",
    "ol",
    "table",
    "div",
    "section",
    "p",
    "blockquote",
    "pre",
    "hr",
    "figure",
    "aim-page-break",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
} | _SKIP_TAGS
_MD_ESCAPE = re.compile(r"([\\`*_\[\]~&<])")
_LINE_START = re.compile(
    r"^(\s{0,3})"
    r"(#{1,6}(?=\s|$)|[>+*-](?=\s|$)|\d{1,9}[.)](?=\s|$)"
    r"|(?:-\s*){3,}$|(?:\*\s*){3,}$|(?:_\s*){3,}$|=+\s*$|-+\s*$)"
)


def _esc(text: str, *, in_table: bool = False) -> str:
    out = _MD_ESCAPE.sub(r"\\\1", text)
    if in_table:
        out = out.replace("|", "\\|")
    out = out.replace("\n", " ")
    return out


def _protect_line_starts(text: str) -> str:
    """Backslash-escape sequences that would re-parse as block syntax when
    they open a line (paragraph text containing '# x', '- y', '---', …)."""
    lines = text.split("\n")
    out = []
    for line in lines:
        m = _LINE_START.match(line)
        if m:
            i = len(m.group(1))
            token = m.group(2)
            if token and token[0].isdigit():
                # ordered markers escape on the punctuation: 1\. not \1.
                p = i + len(token) - 1
                line = line[:p] + "\\" + line[p:]
            else:
                line = line[:i] + "\\" + line[i:]
        out.append(line)
    return "\n".join(out)


def _code_span(text: str, *, in_table: bool = False) -> str:
    if in_table:
        text = text.replace("|", "\\|")  # GFM unescapes \| even in code
    text = text.replace("\n", " ")
    runs = re.findall(r"`+", text)
    fence = "`" * (max((len(r) for r in runs), default=0) + 1)
    pad = " " if (not text or text.startswith("`") or text.endswith("`")) else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _code_fence(code: str) -> str:
    runs = re.findall(r"`+", code)
    fence = "`" * max(3, max((len(r) for r in runs), default=0) + 1)
    return f"{fence}\n{code}\n{fence}"


def _dest(url: str, *, in_table: bool = False) -> str:
    """A CommonMark-safe link/image destination."""
    if in_table:
        url = url.replace("|", "\\|")
    if not url or re.search(r"[\s()]", url):
        return f"<{url}>"
    return url


def _neutralize_critic(text: str) -> str:
    """Content inside critic spans must not close/open spans."""
    for seq in ("~~", "~>", "{--", "{++", "{>>", "<<}", "--}", "++}"):
        text = text.replace(seq, seq[0] + "​" + seq[1:])
    return text


def _inline(el: Element, *, in_table: bool = False) -> str:
    return _inline_nodes(el.children, in_table=in_table)


def _inline_nodes(nodes: list, *, in_table: bool = False) -> str:
    parts: list[str] = []
    for child in nodes:
        if isinstance(child, Text):
            parts.append(_esc(child.data, in_table=in_table))
            continue
        if not isinstance(child, Element):
            continue
        tag = child.tag
        inner = _inline_nodes(child.children, in_table=in_table)
        if tag in ("strong", "b"):
            parts.append(f"**{inner}**")
        elif tag in ("em", "i"):
            parts.append(f"*{inner}*")
        elif tag == "s":
            parts.append(f"~~{inner}~~")
        elif tag == "code":
            parts.append(_code_span(child.text(), in_table=in_table))
        elif tag == "a":
            href = child.get("href") or ""
            parts.append(f"[{inner}]({_dest(href, in_table=in_table)})" if href else inner)
        elif tag == "img":
            alt = child.get("alt") or ""
            src = child.get("src") or ""
            parts.append(f"![{_esc(alt, in_table=in_table)}]({_dest(src, in_table=in_table)})")
        elif tag == "br":
            parts.append(" " if in_table else "  \n")
        else:  # u/mark/sub/sup/span/svg…: text only
            parts.append(inner)
    return "".join(parts)


class _Renderer:
    def __init__(
        self,
        adds_by_anchor: dict[tuple[str | None, str | None], list[Proposal]],
        mods: dict[str, Proposal],
        critic: bool,
    ):
        self.adds = adds_by_anchor
        self.mods = mods
        self.critic = critic

    # ------------------------------------------------------------------
    def block(self, el: Element) -> list[str]:
        """Render one element (a chunk member or container) to md blocks."""
        tag = el.tag
        if tag in _SKIP_TAGS:
            return []
        if tag == "aim-slide":
            out = ["---"]
            sid = el.container_id
            if self.critic and sid:
                out.extend(self._critic_adds((sid, None)))
            for child in el.elements():
                out.extend(self.chunk_blocks(child, child.chunk_id))
                if self.critic and sid:
                    out.extend(self._critic_adds((sid, child.chunk_id or child.container_id)))
            return out
        if tag in ("ul", "ol"):
            return [self._list(el)]
        if tag == "table":
            return [self._table(el)]
        if tag in ("div", "section"):
            return self._grouped(el)
        if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
            return [("#" * int(tag[1])) + " " + _inline(el)]
        if tag == "p":
            text = _protect_line_starts(_inline(el))
            return [text] if text else []
        if tag == "blockquote":
            inner = self._grouped(el)
            lines: list[str] = []
            for i, blk in enumerate(inner):
                if i:
                    lines.append(">")
                lines.extend("> " + line if line else ">" for line in blk.splitlines())
            return ["\n".join(lines)]
        if tag == "pre":
            return [_code_fence(el.text().rstrip("\n"))]
        if tag == "hr":
            return ["---"]
        if tag == "figure":
            out = []
            for child in el.elements():
                if child.tag == "img":
                    alt = child.get("alt") or ""
                    src = child.get("src") or ""
                    out.append(f"![{_esc(alt)}]({_dest(src)})")
                elif child.tag == "figcaption":
                    cap = _inline(child)
                    if cap:
                        out.append(f"*{cap}*")
                else:
                    out.extend(self.block(child))
            return out
        # unknown block: text content, never dropped silently
        text = _protect_line_starts(_inline(el))
        return [text] if text else []

    def _grouped(self, el: Element) -> list[str]:
        """Children of a grouping block (div/section/blockquote) in order;
        runs of direct inline content (text the element carries itself)
        render as paragraphs of their own instead of being dropped."""
        out: list[str] = []
        run: list = []

        def flush() -> None:
            if not run:
                return
            text = _inline_nodes(run).strip()
            if text:
                out.append(_protect_line_starts(text))
            run.clear()

        for child in el.children:
            if isinstance(child, Element) and child.tag in _BLOCK_TAGS:
                flush()
                out.extend(self.block(child))
            else:
                run.append(child)
        flush()
        return out

    # ------------------------------------------------------------------
    def _li_lines(self, li: Element, marker: str, indent: str) -> list[str]:
        content_indent = indent + " " * len(marker)
        inline_parts: list[str] = []
        blocks: list[str] = []
        first_paragraph_taken = False
        for child in li.children:
            if isinstance(child, Text):
                inline_parts.append(_esc(child.data))
            elif isinstance(child, Element):
                tag = child.tag
                if tag == "p" and not first_paragraph_taken and not blocks:
                    inline_parts.append(_inline(child))
                    first_paragraph_taken = True
                elif tag in ("ul", "ol"):
                    blocks.append(self._list(child, indent=content_indent))
                elif tag in ("p", "pre", "blockquote", "table", "hr"):
                    blocks.extend(
                        "\n".join(content_indent + line if line else "" for line in b.splitlines())
                        for b in self.block(child)
                    )
                elif tag == "br":
                    inline_parts.append("  \n" + content_indent)
                elif tag == "a":
                    inline_parts.append(_inline_nodes([child]))
                else:
                    inline_parts.append(_inline_nodes([child]))
        first = (
            indent
            + marker
            + _protect_line_starts("".join(inline_parts)).replace("\n", "\n" + content_indent)
        )
        lines = [first]
        for blk in blocks:
            lines.append("")  # loose separation keeps blocks in the item
            lines.extend(blk.splitlines())
        return lines

    def _list(self, el: Element, *, indent: str = "") -> str:
        ordered = el.tag == "ol"
        container_id = el.container_id
        items = [li for li in el.elements() if li.tag == "li"]
        lines: list[str] = []
        n = 0
        if self.critic and container_id:
            lines.extend(self._critic_adds((container_id, None)))
        i = 0
        while i < len(items):
            cid = items[i].chunk_id
            group = [items[i]]  # a run chunk = consecutive same-id items
            while cid and i + 1 < len(items) and items[i + 1].chunk_id == cid:
                i += 1
                group.append(items[i])
            i += 1
            body: list[str] = []
            for li in group:
                n += 1
                marker = f"{n}. " if ordered else "- "
                body.extend(self._li_lines(li, marker, indent))
            if self.critic and cid:
                body = self._critic_wrap_lines(cid, body)
                body.extend(self._critic_adds((container_id, cid)))
            lines.extend(body)
        return "\n".join(lines)

    def _table(self, el: Element) -> str:
        container_id = el.container_id
        head: list[tuple[str | None, list[str]]] = []
        body: list[tuple[str | None, list[str]]] = []
        for section in el.elements():
            rows = (
                [section]
                if section.tag == "tr"
                else [r for r in section.elements() if r.tag == "tr"]
            )
            target = head if section.tag == "thead" else body
            for tr in rows:
                cells = [
                    _inline(td, in_table=True) for td in tr.elements() if td.tag in ("td", "th")
                ]
                target.append((tr.chunk_id, cells))
        if not head and body:
            head = [body.pop(0)]
        if len(head) > 1:  # markdown has single-row headers
            body = head[1:] + body
            head = head[:1]
        width = max((len(r) for _, r in head + body), default=0)
        if not width:
            return ""

        def fmt(cells: list[str]) -> str:
            padded = cells + [""] * (width - len(cells))
            return "| " + " | ".join(padded) + " |"

        lines: list[str] = []
        if self.critic and container_id:
            lines.extend(self._critic_adds((container_id, None)))
        head_adds: list[str] = []  # drained after the separator: accepting a
        for cid, cells in head:  # header-anchored add makes it the first body row
            row = fmt(cells)
            if self.critic and cid:
                lines.extend(self._critic_wrap_lines(cid, [row]))
                head_adds.extend(self._critic_adds((container_id, cid)))
            else:
                lines.append(row)
        lines.append("|" + " --- |" * width)
        lines.extend(head_adds)
        for cid, cells in body:
            row = fmt(cells)
            if self.critic and cid:
                lines.extend(self._critic_wrap_lines(cid, [row]))
                lines.extend(self._critic_adds((container_id, cid)))
            else:
                lines.append(row)
        return "\n".join(lines)

    # -- pending lane (CriticMarkup) -----------------------------------
    def _payload_md(self, proposal: Proposal) -> str:
        if not proposal.payload_html:
            return ""
        frag = parse_html(proposal.payload_html)
        blocks: list[str] = []
        for node in frag.elements():
            if node.tag == "tr":  # keep cell boundaries: accepting the
                cells = [  # suggestion must not fuse the row into one word
                    _inline(td, in_table=True)
                    for td in node.elements()
                    if td.tag in ("td", "th")
                ]
                blocks.append("| " + " | ".join(cells) + " |")
            elif node.tag == "li":
                blocks.append("- " + _inline(node))
            else:
                blocks.extend(self.block(node))
        return _neutralize_critic("\n\n".join(b for b in blocks if b))

    def _note(self, prop: Proposal) -> str:
        return f"{{>>{_neutralize_critic(prop.explanation)}<<}}" if prop.explanation else ""

    def _critic_wrap_lines(self, cid: str, lines: list[str]) -> list[str]:
        prop = self.mods.get(cid)
        if not prop:
            return lines
        old = _neutralize_critic("\n".join(lines))
        if prop.action == "delete":
            return [f"{{--{old}--}}{self._note(prop)}"]
        new = self._payload_md(prop)
        return [f"{{~~{old}~>{new}~~}}{self._note(prop)}"]

    def _critic_adds(self, key: tuple[str | None, str | None]) -> list[str]:
        out: list[str] = []
        # reversed: resolution inserts every same-anchor add at
        # index(anchor)+1, so accept-all leaves the LAST-proposed sibling
        # closest to the anchor — render what accepting produces
        for prop in reversed(self.adds.get(key, [])):
            out.append(f"{{++{self._payload_md(prop)}++}}{self._note(prop)}")
            out.extend(self._critic_adds((prop.anchor_container, prop.id)))
        return out

    def chunk_blocks(self, el: Element, cid: str | None) -> list[str]:
        blocks = self.block(el)
        if self.critic and cid:
            if cid in self.mods:
                blocks = self._critic_wrap_lines(cid, blocks)
            blocks = blocks + self._critic_adds((None, cid)) + self._critic_adds(("body", cid))
        return blocks


def to_markdown(doc: AimDocument, *, pending: str = "drop") -> str:
    """Export *doc* as Markdown.

    ``pending="drop"`` (default) renders the accepted document only;
    ``pending="criticmarkup"`` renders pending proposals as CriticMarkup;
    ``pending="accept-all"`` / ``pending="reject-all"`` resolve the pending
    lane on a throwaway copy first (the DOCX/PDF exporters' semantics) and
    render the resolved document.
    """
    if pending in ("accept-all", "reject-all"):
        from ..export_docx import _resolve_copy

        doc, pending = _resolve_copy(doc, pending), "drop"
    if pending not in ("drop", "criticmarkup"):
        raise InvalidOperation(
            f"pending must be 'drop', 'criticmarkup', 'accept-all', or "
            f"'reject-all', got {pending!r}"
        )
    critic = pending == "criticmarkup"

    adds_by_anchor: dict[tuple[str | None, str | None], list[Proposal]] = {}
    mods: dict[str, Proposal] = {}
    notes: list[str] = []
    if critic:
        for p in doc.proposals:
            if p.action == "add":
                key = (p.anchor_container, p.anchor_after)
                adds_by_anchor.setdefault(key, []).append(p)
            elif (
                p.action in ("modify", "delete")
                and p.target
                and p.target not in ("aim:theme", "aim:doc")
            ):
                mods[p.target] = p
            else:  # theme/page-setup changes / moves have no textual place in Markdown
                what = p.target or p.action
                notes.append(
                    f"{{>>pending {p.action} on {what}: {p.explanation or 'no explanation'}<<}}"
                )

    frag = parse_html(doc.dumps())
    html = next(e for e in frag.elements() if e.tag == "html")
    body = next(e for e in html.elements() if e.tag == "body")

    renderer = _Renderer(adds_by_anchor, mods, critic)
    blocks: list[str] = []
    if critic:  # adds anchored at the very top of the body
        blocks.extend(renderer._critic_adds(("body", None)))
        blocks.extend(renderer._critic_adds((None, None)))
    elements = [e for e in body.elements() if e.tag not in _SKIP_TAGS]
    i = 0
    while i < len(elements):
        el = elements[i]
        cid = el.chunk_id
        group = [el]  # a run chunk = consecutive same-id siblings
        while cid and i + 1 < len(elements) and elements[i + 1].chunk_id == cid:
            i += 1
            group.append(elements[i])
        i += 1
        blks: list[str] = []
        for member in group:
            blks.extend(renderer.block(member))
        ref_id = cid or el.container_id
        if critic and ref_id:
            if ref_id in mods:
                blks = renderer._critic_wrap_lines(ref_id, blks)
            blks.extend(renderer._critic_adds((None, ref_id)))
            blks.extend(renderer._critic_adds(("body", ref_id)))
        blocks.extend(blks)
    blocks.extend(notes)
    return "\n\n".join(b for b in blocks if b) + "\n"
