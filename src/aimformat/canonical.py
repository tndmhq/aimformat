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
from collections.abc import Iterable

from .dom import Comment, Element, Fragment, Nodeish, Text
from .registry import REGISTRY

LINE_CONTAINERS = frozenset({"html", "head", "body", "aim-proposals", "aim-assets"})


def escape_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;")


def sort_class_tokens(value: str) -> str:
    """Class tokens sorted and de-duplicated (a set, canonically spelled)."""
    return " ".join(sorted(set(value.split())))


def normalize_style(value: str) -> str:
    """Inline style as a normal form: whitelist properties in registry
    order, later duplicates win, `; `-separated, no trailing semicolon.
    Unknown properties (a lint error anyway) keep authored order at the
    end so the violation stays visible rather than being reshuffled."""
    known: dict[str, str] = {}
    unknown: list[tuple[str, str]] = []
    for piece in value.split(";"):
        piece = piece.strip()
        if not piece or ":" not in piece:
            continue
        prop, val = (s.strip() for s in piece.split(":", 1))
        if prop in REGISTRY.style_prop_order:
            known[prop] = val
        else:
            unknown.append((prop, val))
    ordered = [(p, known[p]) for p in REGISTRY.style_prop_order if p in known]
    return "; ".join(f"{p}:{v}" for p, v in ordered + unknown)


def canonical_attrs(el: Element, *, in_svg: bool) -> str:
    adjust = REGISTRY.svg_case_adjust

    def fix(name: str) -> str:
        return adjust.get(name, name) if in_svg else name

    remaining: dict[str, str | None] = {}
    for k, v in el.attrs:
        # HTML semantics: the FIRST duplicate wins, matching Element.get —
        # last-wins here would rename a chunk under `aim normalize` and
        # break the events targeting the id the reader resolved
        remaining.setdefault(k, v)
    ordered: list[tuple[str, str | None]] = []
    for k in REGISTRY.attr_first:
        if k in remaining:
            ordered.append((k, remaining.pop(k)))
    tail = [(k, remaining.pop(k)) for k in REGISTRY.attr_last if k in remaining]
    ordered += sorted(remaining.items()) + tail

    parts = []
    for k, v in ordered:
        if k == "class" and v is not None:
            v = sort_class_tokens(v)
            if not v:
                continue  # empty class has no canonical spelling
        if k == "style" and v is not None:
            v = normalize_style(v)
            if not v:
                continue
        parts.append(fix(k) if v is None else f'{fix(k)}="{escape_attr(v)}"')
    return (" " + " ".join(parts)) if parts else ""


def _is_block_raw(el: Element) -> bool:
    if el.tag == "script" and "+" in (el.get("type") or ""):
        return True
    return el.tag == "style" and el.has("data-aim-css")


def _is_registry_svg(el: Element) -> bool:
    return el.tag == "svg" and el.has("aria-hidden")


def serialize(node: Nodeish, *, in_svg: bool = False) -> str:
    """Inline canonical serialization of one node (no trailing newline).

    A normal form, not an echo: HTML void elements never carry a slash
    however they were written, foreign (SVG-context) elements with no
    content always self-close, and every other element always has an
    explicit end tag (spec §11.1)."""
    if isinstance(node, Text):
        return escape_text(node.data)
    if isinstance(node, Comment):
        return f"<!--{node.data}-->"
    svg_here = in_svg or node.tag == "svg"
    open_tag = f"<{node.tag}{canonical_attrs(node, in_svg=svg_here)}"
    if node.tag in REGISTRY.void_elements and not svg_here:
        return open_tag + ">"
    if svg_here and not node.children and node.raw is None:
        return open_tag + "/>"
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
        return (
            [f"<{node.tag}{canonical_attrs(node, in_svg=svg_here)}>"]
            + (body.split("\n") if body else [])
            + [f"</{node.tag}>"]
        )
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
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace(
        "</", "<\\/"
    )


# --------------------------------------------------------------------------
# hashing
def sha256_prefixed(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def doc_hash(
    html_open_line: str,
    theme_line: str | None,
    construct_lines: Iterable[str],
    *,
    doc_settings_line: str | None = None,
) -> str:
    """The reduced-projection hash anchoring checkpoints (spec §11.4).

    The settings block participates only when present, so documents without
    one hash exactly as they did before it existed."""
    lines = [html_open_line]
    if doc_settings_line:
        lines.append(doc_settings_line)
    if theme_line:
        lines.append(theme_line)
    lines.extend(construct_lines)
    return sha256_prefixed("\n".join(lines) + "\n")
