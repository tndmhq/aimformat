"""`.aim` → Markdown.

A stdlib chunk walker over the canonical document tree. Fidelity notes:

- Marks map back to Markdown where Markdown has the concept (``strong``/
  ``b`` → ``**``, ``em``/``i`` → ``*``, ``s`` → ``~~``, ``code`` →
  backticks, links, images, ``br`` → hard break). ``u``/``mark``/``sub``/
  ``sup``/``span`` have no Markdown equivalent and degrade to their text.
- Classes, inline geometry, and the theme are presentation: dropped.
- Slides have no Markdown model: each ``aim-slide`` renders as a thematic
  break (``---``) followed by its chunks in reading order.
- Tables become pipe tables; row/col spans are flattened (each cell renders
  once, at its starting position).
- ``pending="drop"`` (default) exports the accepted document only;
  ``pending="criticmarkup"`` additionally renders the pending lane as
  CriticMarkup (``{~~old~>new~~}``, ``{++added++}``, ``{--deleted--}``,
  explanations as ``{>>comments<<}``) — the closest thing Markdown has to a
  change lane.
"""
from __future__ import annotations

import re
from typing import Optional

from ..document import AimDocument, Proposal
from ..dom import Element, Text, parse_html
from ..errors import InvalidOperation

__all__ = ["to_markdown"]

_SKIP_TAGS = {"aim-proposals", "aim-assets", "script", "style", "template"}
_MD_ESCAPE = re.compile(r"([\\`*_\[\]])")


def _esc(text: str, *, in_table: bool = False) -> str:
    out = _MD_ESCAPE.sub(r"\\\1", text)
    if in_table:
        out = out.replace("|", "\\|")
    out = out.replace("\n", " ")
    return out


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
        inner = _inline(child, in_table=in_table)
        if tag in ("strong", "b"):
            parts.append(f"**{inner}**")
        elif tag in ("em", "i"):
            parts.append(f"*{inner}*")
        elif tag == "s":
            parts.append(f"~~{inner}~~")
        elif tag == "code":
            parts.append(f"`{child.text()}`")
        elif tag == "a":
            href = child.get("href") or ""
            parts.append(f"[{inner}]({href})" if href else inner)
        elif tag == "img":
            alt = child.get("alt") or ""
            src = child.get("src") or ""
            parts.append(f"![{_esc(alt, in_table=in_table)}]({src})")
        elif tag == "br":
            parts.append("|" if in_table else "  \n")
        else:  # u/mark/sub/sup/span/svg…: text only
            parts.append(inner)
    return "".join(parts)


class _Renderer:
    def __init__(self, adds_by_anchor: dict[Optional[str], list[Proposal]],
                 mods: dict[str, Proposal], critic: bool):
        self.adds = adds_by_anchor
        self.mods = mods
        self.critic = critic

    # ------------------------------------------------------------------
    def block(self, el: Element, *, depth: int = 0) -> list[str]:
        """Render one element (a chunk member or container) to md blocks."""
        tag = el.tag
        if tag in _SKIP_TAGS:
            return []
        if tag == "aim-slide":
            out = ["---"]
            for child in el.elements():
                out.extend(self.chunk_or_container(child))
            return out
        if tag in ("ul", "ol", "table"):
            return [self._list(el)] if tag != "table" else [self._table(el)]
        if tag in ("div", "section"):
            out = []
            for child in el.elements():
                out.extend(self.block(child))
            return out
        if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
            return [("#" * int(tag[1])) + " " + _inline(el)]
        if tag == "p":
            text = _inline(el)
            return [text] if text else []
        if tag == "blockquote":
            inner: list[str] = []
            for child in el.elements():
                inner.extend(self.block(child))
            lines: list[str] = []
            for i, blk in enumerate(inner):
                if i:
                    lines.append(">")
                lines.extend("> " + line if line else ">"
                             for line in blk.splitlines())
            return ["\n".join(lines)]
        if tag == "pre":
            code = el.text().rstrip("\n")
            return [f"```\n{code}\n```"]
        if tag == "hr":
            return ["---"]
        if tag == "figure":
            out = []
            for child in el.elements():
                if child.tag == "img":
                    alt = child.get("alt") or ""
                    src = child.get("src") or ""
                    out.append(f"![{_esc(alt)}]({src})")
                elif child.tag == "figcaption":
                    out.append(f"*{_inline(child)}*")
                else:
                    out.extend(self.block(child))
            return out
        # unknown block: text content, never dropped silently
        text = _inline(el)
        return [text] if text else []

    # ------------------------------------------------------------------
    def _li_lines(self, li: Element, marker: str, indent: str) -> list[str]:
        inline_parts: list[str] = []
        sub_lines: list[str] = []
        for child in li.children:
            if isinstance(child, Text):
                inline_parts.append(_esc(child.data))
            elif isinstance(child, Element):
                if child.tag in ("ul", "ol"):
                    sub_lines.extend(
                        self._list(child, indent=indent + "  ").splitlines())
                elif child.tag == "p":
                    inline_parts.append(_inline(child))
                elif child.tag == "table":
                    sub_lines.extend(
                        (indent + "  " + line)
                        for line in self._table(child).splitlines())
                else:  # an inline mark directly inside the item
                    inline_parts.append(_inline_nodes([child]))
        first = indent + marker + "".join(inline_parts)
        return [first] + sub_lines

    def _list(self, el: Element, *, indent: str = "") -> str:
        ordered = el.tag == "ol"
        items = [li for li in el.elements() if li.tag == "li"]
        lines: list[str] = []
        n = 0
        i = 0
        while i < len(items):
            cid = items[i].chunk_id
            group = [items[i]]  # a run chunk = consecutive same-id items
            while cid and i + 1 < len(items) \
                    and items[i + 1].chunk_id == cid:
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
                body.extend(self._critic_adds(cid))
            lines.extend(body)
        return "\n".join(lines)

    def _table(self, el: Element) -> str:
        head: list[list[str]] = []
        body: list[list[str]] = []
        for section in el.elements():
            rows = ([section] if section.tag == "tr"
                    else [r for r in section.elements() if r.tag == "tr"])
            target = head if section.tag == "thead" else body
            for tr in rows:
                cells = [_inline(td, in_table=True)
                         for td in tr.elements() if td.tag in ("td", "th")]
                target.append(cells)
        if not head and body:
            head = [body.pop(0)]
        width = max((len(r) for r in head + body), default=0)
        if not width:
            return ""
        def fmt(row: list[str]) -> str:
            padded = row + [""] * (width - len(row))
            return "| " + " | ".join(padded) + " |"
        lines = []
        for r in head:
            lines.append(fmt(r))
        lines.append("|" + " --- |" * width)
        for r in body:
            lines.append(fmt(r))
        return "\n".join(lines)

    # -- pending lane (CriticMarkup) -----------------------------------
    def _payload_md(self, proposal: Proposal) -> str:
        if not proposal.payload_html:
            return ""
        frag = parse_html(proposal.payload_html)
        blocks: list[str] = []
        for node in frag.elements():
            blocks.extend(self.block(node))
        return "\n\n".join(blocks)

    def _critic_wrap_lines(self, cid: str, lines: list[str]) -> list[str]:
        prop = self.mods.get(cid)
        if not prop:
            return lines
        old = "\n".join(lines)
        note = f"{{>>{prop.explanation}<<}}" if prop.explanation else ""
        if prop.action == "delete":
            return [f"{{--{old}--}}{note}"]
        new = self._payload_md(prop)
        return [f"{{~~{old}~>{new}~~}}{note}"]

    def _critic_adds(self, anchor: Optional[str], _ctx: str = "") -> list[str]:
        out: list[str] = []
        for prop in self.adds.get(anchor, []):
            note = f"{{>>{prop.explanation}<<}}" if prop.explanation else ""
            out.append(f"{{++{self._payload_md(prop)}++}}{note}")
            out.extend(self._critic_adds(prop.id))  # chained adds
        return out

    def chunk_or_container(self, el: Element) -> list[str]:
        blocks = self.block(el)
        cid = el.chunk_id
        if self.critic and cid:
            blocks = self._critic_wrap_lines(cid, blocks) \
                if cid in self.mods else blocks
            blocks = blocks + self._critic_adds(cid)
        return blocks


def to_markdown(doc: AimDocument, *, pending: str = "drop") -> str:
    """Export *doc* as Markdown.

    ``pending="drop"`` (default) renders the accepted document only;
    ``pending="criticmarkup"`` renders pending proposals as CriticMarkup.
    """
    if pending not in ("drop", "criticmarkup"):
        raise InvalidOperation(
            f"pending must be 'drop' or 'criticmarkup', got {pending!r}")
    critic = pending == "criticmarkup"

    adds_by_anchor: dict[Optional[str], list[Proposal]] = {}
    mods: dict[str, Proposal] = {}
    notes: list[str] = []
    if critic:
        for p in doc.proposals:
            if p.action == "add":
                adds_by_anchor.setdefault(p.anchor_after, []).append(p)
            elif p.action in ("modify", "delete") and p.target \
                    and p.target != "aim:theme":
                mods[p.target] = p
            else:  # theme changes / moves have no textual place in Markdown
                what = p.target or p.action
                notes.append(f"{{>>pending {p.action} on {what}: "
                             f"{p.explanation or 'no explanation'}<<}}")

    frag = parse_html(doc.dumps())
    html = next(e for e in frag.elements() if e.tag == "html")
    body = next(e for e in html.elements() if e.tag == "body")

    renderer = _Renderer(adds_by_anchor, mods, critic)
    blocks: list[str] = []
    if critic:  # adds anchored at the very top of the body
        blocks.extend(renderer._critic_adds(None))
    elements = [e for e in body.elements() if e.tag not in _SKIP_TAGS]
    i = 0
    while i < len(elements):
        el = elements[i]
        cid = el.chunk_id
        group = [el]  # a run chunk = consecutive same-id siblings
        while cid and i + 1 < len(elements) \
                and elements[i + 1].chunk_id == cid:
            i += 1
            group.append(elements[i])
        i += 1
        blks: list[str] = []
        for member in group:
            blks.extend(renderer.block(member))
        if critic and cid:
            if cid in mods:
                blks = renderer._critic_wrap_lines(cid, blks)
            blks.extend(renderer._critic_adds(cid))
        blocks.extend(blks)
    blocks.extend(notes)
    return "\n\n".join(b for b in blocks if b) + "\n"
