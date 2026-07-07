"""Canonical serialization and hashing (spec §11).

Byte-determinism is load-bearing: equality of serializations — not any
parser's opinion — is what verifies the history chain, and `doc_hash` anchors
checkpoints. The rules implemented here:

- attribute order: ``data-aim``/``data-aim-container``, ``id``, ``class``,
  ``style`` first; remaining attributes alphabetical; ``src``/``href`` last
- ``class`` tokens sorted alphabetically; inline style props in whitelist
  order, ``; ``-separated, no trailing semicolon
- text escapes ``& < >`` only; attribute values escape ``& "`` only
- foreign (SVG) attribute case re-adjusted (``viewBox`` …)
- one top-level construct per line — precisely: constructs never *share* a
  line (a construct with significant internal newlines, e.g. ``pre``, spans
  physical lines)
- typed scripts and the embedded stylesheet are block-laid-out (open tag /
  content lines / close tag); the theme block is a single hashed line
- JSON/JSONL: sorted keys, compact separators, raw UTF-8, ``</`` written
  ``<\\/``
- ``doc_hash`` = sha256 over the reduced projection: the ``<html …>`` open
  tag, the theme line, and each body content construct line, LF-joined with
  a trailing LF
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable, Optional, Union

from .dom import Comment, Element, Fragment, Nodeish, Text
from .registry import REGISTRY

LINE_CONTAINERS = frozenset({"html", "head", "body", "aim-proposals", "aim-assets"})


def escape_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;")


def sort_class_tokens(value: str) -> str:
    return " ".join(sorted(value.split()))


def canonical_attrs(el: Element, *, in_svg: bool) -> str:
    adjust = REGISTRY.svg_case_adjust

    def fix(name: str) -> str:
        return adjust.get(name, name) if in_svg else name

    remaining = {k: v for k, v in el.attrs}
    ordered: list[tuple[str, Optional[str]]] = []
    for k in REGISTRY.attr_first:
        if k in remaining:
            ordered.append((k, remaining.pop(k)))
    tail = [(k, remaining.pop(k)) for k in REGISTRY.attr_last if k in remaining]
    ordered += sorted(remaining.items()) + tail

    parts = []
    for k, v in ordered:
        if k == "class" and v:
            v = sort_class_tokens(v)
        parts.append(fix(k) if v is None else f'{fix(k)}="{escape_attr(v)}"')
    return (" " + " ".join(parts)) if parts else ""


def _is_block_raw(el: Element) -> bool:
    if el.tag == "script" and "+" in (el.get("type") or ""):
        return True
    return el.tag == "style" and el.has("data-aim-css")


def _is_registry_svg(el: Element) -> bool:
    return el.tag == "svg" and el.has("aria-hidden")


def serialize(node: Nodeish, *, in_svg: bool = False) -> str:
    """Inline canonical serialization of one node (no trailing newline)."""
    if isinstance(node, Text):
        return escape_text(node.data)
    if isinstance(node, Comment):
        return f"<!--{node.data}-->"
    svg_here = in_svg or node.tag == "svg"
    open_tag = f"<{node.tag}{canonical_attrs(node, in_svg=svg_here)}"
    if node.self_closing:
        return open_tag + "/>"
    if node.tag in REGISTRY.void_elements:
        return open_tag + ">"
    if node.raw is not None:
        return f"{open_tag}>{node.raw}</{node.tag}>"
    inner = "".join(serialize(c, in_svg=svg_here) for c in node.children)
    return f"{open_tag}>{inner}</{node.tag}>"


def serialize_run(members: Iterable[Element]) -> str:
    """A chunk's serialization: its member elements concatenated in order."""
    return "".join(serialize(m) for m in members)


def _lines(node: Nodeish, *, in_svg: bool = False) -> list[str]:
    if isinstance(node, (Text, Comment)):
        s = serialize(node)
        return [s] if s.strip() else []
    svg_here = in_svg or node.tag == "svg"
    if node.tag in LINE_CONTAINERS or _is_registry_svg(node):
        out = [f"<{node.tag}{canonical_attrs(node, in_svg=svg_here)}>"]
        for c in node.children:
            out += _lines(c, in_svg=svg_here)
        out.append(f"</{node.tag}>")
        return out
    if node.raw is not None and _is_block_raw(node):
        body = node.raw
        body = body[1:] if body.startswith("\n") else body
        body = body[:-1] if body.endswith("\n") else body
        return ([f"<{node.tag}{canonical_attrs(node, in_svg=svg_here)}>"]
                + (body.split("\n") if body else [])
                + [f"</{node.tag}>"])
    return [serialize(node, in_svg=in_svg)]


def document_text(fragment: Fragment) -> str:
    """Full canonical text of a parsed document."""
    lines = [f"<!{fragment.doctype}>"] if fragment.doctype else []
    for child in fragment.children:
        lines += _lines(child)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# JSON canonical form
def canonical_json(obj: object) -> str:
    """RFC 8785-flavoured canonical JSON, safe to embed in a script block."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).replace("</", "<\\/")


# --------------------------------------------------------------------------
# hashing
def sha256_prefixed(data: Union[str, bytes]) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def doc_hash(html_open_line: str, theme_line: Optional[str],
             construct_lines: Iterable[str]) -> str:
    """The reduced-projection hash anchoring checkpoints (spec §11.4)."""
    lines = [html_open_line]
    if theme_line:
        lines.append(theme_line)
    lines.extend(construct_lines)
    return sha256_prefixed("\n".join(lines) + "\n")
