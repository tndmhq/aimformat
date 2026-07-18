"""`.aim` → PDF via headless Chromium (extra ``pdf``).

Prints the standalone-HTML rendering (see :mod:`._html_out`) through
Playwright's Chromium. The browser binary is a one-time setup step::

    pip install 'aimformat[pdf]'
    python -m playwright install chromium

Page geometry comes from the document's own page setup (`aim:doc`, registry
defaults when unset) as an ``@page`` rule spliced into the print copy — the
same :mod:`aimformat.pagesetup` resolution any live page-view preview uses,
so preview and PDF cannot disagree about the page box. The rule is injected
at print time rather than stored: a free ``<style>`` block is not part of
the `.aim` vocabulary (X005), and :func:`to_html` output stays conforming.

Slides are their own pages *at their own size*: every ``aim-slide`` gets a
CSS named page (``@page pg-<id>``) sized to its canvas under the canvas-pt
convention — 1 canvas px prints as 1 typographic point, so a 420×595 canvas
yields a real A5 page and a 960×540 deck slide the native 16:9 point size.
1 CSS px is physically 0.75 pt, so the print copy scales slides ×4/3
(``zoom``, the mechanism spec §3.4 already reserves for slide scaling);
flowing content around them keeps the document page setup, and the named-
page switch itself forces the break on both sides.

Uses the sync Playwright API — call from a worker thread when inside an
asyncio event loop (sync Playwright refuses to run on a running loop).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..document import AimDocument
from ..pagesetup import page_css
from ._html_out import to_html

__all__ = ["to_pdf"]

# Print scale bridging CSS px to typographic points: 1 px = 0.75 pt, so ×4/3
# makes W canvas px fill W pt of paper (the canvas-pt convention). Slightly
# under 4/3 so rounding can only undershoot — overshoot would spill a slide
# onto a blank second page.
_PRINT_ZOOM = "1.33333"
# Convention default when a slide declares no canvas size: the native 16:9
# point size (spec §3.3 examples; PPTX's own 13.33in × 7.5in).
_CANVAS_DEFAULT = (960.0, 540.0)

_PX_RE = re.compile(r"^(\d+(?:\.\d+)?)px$")


def _canvas_size(style: str | None) -> tuple[float, float]:
    """A slide's canvas (width, height) in px, defaulting per axis."""
    w, h = None, None
    for piece in (style or "").split(";"):
        if ":" not in piece:
            continue
        prop, val = (s.strip() for s in piece.split(":", 1))
        m = _PX_RE.match(val)
        if m is None:
            continue
        if prop == "width":
            w = float(m.group(1))
        elif prop == "height":
            h = float(m.group(1))
    return (w if w is not None else _CANVAS_DEFAULT[0], h if h is not None else _CANVAS_DEFAULT[1])


def _fmt_pt(v: float) -> str:
    out = f"{v:.2f}".rstrip("0").rstrip(".")
    return out or "0"


def _slide_page_css(doc: AimDocument) -> str:
    """Named-page rules printing each slide as its own canvas-sized page.

    ``pg-<id>`` is a valid CSS ident for every container id (ids match
    ``[a-z0-9][a-z0-9_-]*``, and the prefix keeps the first character
    alphabetic). The element rules ride ``@media print`` and out-rank the
    stylesheet's ``aim-slide{zoom:1}`` print rule by specificity.
    """
    pages: list[str] = []
    assigns: list[str] = []
    for el in doc._state.constructs():
        if el.tag != "aim-slide":
            continue
        sid = el.container_id
        if not sid:
            continue
        w, h = _canvas_size(el.get("style"))
        pages.append(f"@page pg-{sid}{{size:{_fmt_pt(w)}pt {_fmt_pt(h)}pt;margin:0}}")
        # the resolved size rides the element rule too: inline canvas styles
        # win the cascade when present, and a slide that omits them would
        # otherwise collapse to a zero-height box (the stylesheet gives
        # aim-slide no width/height) and print blank on its named page
        assigns.append(
            f'aim-slide[data-aim-container="{sid}"]'
            f"{{page:pg-{sid};zoom:{_PRINT_ZOOM};"
            f"width:{_fmt_pt(w)}px;height:{_fmt_pt(h)}px}}"
        )
    if not pages:
        return ""
    return "\n".join(pages) + "\n@media print{" + "".join(assigns) + "}"


def _print_html(doc: AimDocument, pending: str, extra_css: str | None) -> str:
    if pending in ("accept-all", "reject-all"):
        # resolve the pending lane FIRST (on a throwaway copy), so the
        # @page rule and the printed HTML read the same document state — a
        # pending aim:doc proposal must not leave the print CSS on the old
        # geometry while the page itself resolves to the new one (and a
        # pending add of a whole slide must gain/lose its named page too)
        from ..export_docx import _resolve_copy

        doc, pending = _resolve_copy(doc, pending), "keep"
    css = page_css(doc.page_setup)
    slide_css = _slide_page_css(doc)
    if slide_css:
        css += "\n" + slide_css
    if extra_css:
        css += "\n" + extra_css
    html = to_html(doc, pending=pending)
    block = f"<style>\n{css}\n</style>\n"
    # splice BEFORE the theme block when there is one: export-time CSS may
    # override the stylesheet's defaults but never the document's own theme.
    # Prefix match (no closing ">"): the canonical serializer may follow the
    # marker attribute with legal vendor attributes (data-x-*).
    theme_at = html.find("<style data-aim-theme")
    if theme_at != -1:
        return html[:theme_at] + block + html[theme_at:]
    return html.replace("</head>", block + "</head>", 1)


def to_pdf(
    doc: AimDocument, path: str | Path, *, pending: str = "keep", extra_css: str | None = None
) -> Path:
    """Print *doc* to a PDF file at *path*; returns the path.

    ``pending`` as in :func:`to_html` (default ``"keep"`` — the pending-
    changes memo prints as part of the document). ``extra_css`` is spliced
    into the print copy after the ``@page`` rule — the hook callers use for
    print-only additions such as ``@font-face`` for embedded fonts.
    """
    html = _print_html(doc, pending, extra_css)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise ImportError(
            "PDF export requires the 'pdf' extra: "
            "pip install 'aimformat[pdf]' && python -m playwright "
            "install chromium"
        ) from exc
    out = Path(path)
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            # translate ONLY the missing-binary case: swallowing every
            # launch failure into "not installed" let real to_pdf
            # regressions skip green in the test suite
            msg = str(exc)
            if "Executable doesn't exist" in msg or "playwright install" in msg:
                raise RuntimeError(
                    "Chromium is not installed for Playwright — run: "
                    "python -m playwright install chromium"
                ) from exc
            raise
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.pdf(path=str(out), print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()
    return out
