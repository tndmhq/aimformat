"""aim.css generation (spec §3).

One static stylesheet per spec minor version, generated deterministically
from the registry: an element base layer (raw-tier typography), every class
utility in the closed vocabulary, theme-slot defaults + theme-backed
utilities, and the ``aim-*`` chrome (slide canvas, proposal cards). The
stylesheet is machine-managed and excluded from content hashing — tools
regenerate it freely; documents stay valid.
"""

from __future__ import annotations

from functools import lru_cache

from .registry import REGISTRY


def _base_layer() -> list[str]:
    slots = ";".join(f"{k}:{v['default']}" for k, v in REGISTRY.theme_slots.items())
    return [
        ":root{" + slots + "}",
        "html{-webkit-text-size-adjust:100%}",
        "body{margin:2rem auto;padding:0 1.5rem;max-width:52rem;"
        "font-family:var(--aim-font-body);font-size:1rem;line-height:1.6;"
        "color:#1f2937;background:#fff}",
        "h1,h2,h3,h4,h5,h6{font-family:var(--aim-font-heading);line-height:1.2;"
        "margin:1.6em 0 .5em;font-weight:700}",
        "h1{font-size:1.875rem;margin-top:.4em}h2{font-size:1.5rem}h3{font-size:1.25rem}",
        "h4,h5,h6{font-size:1rem}",
        "p{margin:.75em 0}",
        "ul,ol{margin:.75em 0;padding-left:1.5rem}li{margin:.25em 0}",
        "ul{list-style:disc}ol{list-style:decimal}",
        "table{border-collapse:collapse;margin:1em 0;width:100%;font-size:.9375rem}",
        "th,td{border:1px solid #d1d5db;padding:.5rem .75rem;text-align:left;vertical-align:top}",
        "thead th{background:#f3f4f6;font-weight:600}",
        "blockquote{margin:1em 0;padding:.25rem 1rem;border-left:3px solid #d1d5db;color:#4b5563}",
        "code{font-family:var(--aim-font-mono);font-size:.875em;background:#f3f4f6;"
        "padding:.125rem .375rem;border-radius:.25rem}",
        "pre{background:#f3f4f6;padding:.75rem 1rem;border-radius:.375rem;"
        "overflow-x:auto;margin:1em 0}pre code{background:none;padding:0}",
        "figure{margin:1.5em 0}figcaption{font-size:.875rem;color:#6b7280;margin-top:.5rem}",
        "img,svg{max-width:100%;height:auto}",
        "hr{border:0;border-top:1px solid #e5e7eb;margin:2em 0}",
        "a{color:var(--aim-brand-1)}",
    ]


def _chrome_layer() -> list[str]:
    return [
        # slides: zoom scales the layout box too, so scaled canvases sit in
        # flow with no wrapper element and no JS; canvas size is inline
        # geometry on the element. Stepped raw-tier scale for narrow windows.
        "aim-slide{display:block;position:relative;overflow:hidden;"
        "background:#fff;border:1px solid #e5e7eb;"
        "box-shadow:0 1px 4px rgba(0,0,0,.08);"
        "zoom:var(--aim-slide-scale,.42);margin-bottom:1.5rem}",
        "aim-slide>[data-aim],aim-slide>[data-aim-container]{position:absolute;margin:0}",
        "@media (max-width:1000px){aim-slide{zoom:var(--aim-slide-scale,.28)}}",
        "@media (max-width:640px){aim-slide{zoom:var(--aim-slide-scale,.17)}}",
        "@media print{aim-slide{zoom:1;margin-bottom:0;page-break-after:always;"
        "border:0;box-shadow:none}}",
        # pending lane: the raw-tier change memo
        "aim-proposals{display:block;margin-top:3rem;padding-top:1rem;"
        "border-top:2px solid #e5e7eb}",
        "aim-proposals::before{content:'Pending changes';display:block;"
        "font-family:var(--aim-font-heading);font-weight:700;font-size:1.25rem;"
        "margin-bottom:.75rem}",
        "aim-proposal{display:block;border:1px solid #e5e7eb;"
        "border-left:3px solid var(--aim-brand-1);border-radius:.375rem;"
        "padding:.625rem .875rem;margin:.5rem 0;background:#f9fafb;"
        "font-size:.875rem}",
        "aim-proposal::before{content:attr(data-action) ' \\00b7 ' attr(data-for)"
        " ' \\2014 ' attr(data-author) ' \\00b7 ' attr(data-at);display:block;"
        "font-family:var(--aim-font-mono);font-size:.75rem;color:#6b7280;"
        "text-transform:uppercase;letter-spacing:.03em}",
        "aim-proposal[data-action=add]::before{content:attr(data-action)"
        " ' into ' attr(data-anchor-container) ' after ' attr(data-anchor-after)"
        " ' \\2014 ' attr(data-author) ' \\00b7 ' attr(data-at)}",
        "aim-proposal::after{content:attr(data-explanation);display:block;"
        "margin-top:.25rem;color:#1f2937}",
        "aim-proposal[data-action=delete]{border-left-color:#dc2626}",
        "aim-proposal[data-action=add]{border-left-color:#16a34a}",
        "aim-proposal[data-action=move]{border-left-color:#d97706}",
        # registries / trailers are not visible content
        "aim-assets{display:none}",
    ]


@lru_cache(maxsize=1)
def generate_aim_css() -> str:
    """The full deterministic stylesheet for this spec version."""
    lines = _base_layer()
    for name, decl in sorted(REGISTRY.class_declarations.items()):
        escaped = name.replace("/", "\\/")  # defensive; v0.1 names have no '/'
        lines.append("." + escaped + "{" + decl + "}")
    lines += _chrome_layer()
    return "\n".join(lines) + "\n"


def css_stats() -> dict:
    css = generate_aim_css()
    import gzip

    return {
        "rules": css.count("{"),
        "raw_bytes": len(css.encode()),
        "gzip_bytes": len(gzip.compress(css.encode(), 9)),
    }
