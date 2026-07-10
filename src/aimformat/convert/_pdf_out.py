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

Uses the sync Playwright API — call from a worker thread when inside an
asyncio event loop (sync Playwright refuses to run on a running loop).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from ..document import AimDocument
from ..pagesetup import page_css
from ._html_out import to_html

__all__ = ["to_pdf"]


def _print_html(doc: AimDocument, pending: str,
                extra_css: Optional[str]) -> str:
    if pending in ("accept-all", "reject-all"):
        # resolve the pending lane FIRST (on a throwaway copy), so the
        # @page rule and the printed HTML read the same document state — a
        # pending aim:doc proposal must not leave the print CSS on the old
        # geometry while the page itself resolves to the new one
        from ..export_docx import _resolve_copy
        doc, pending = _resolve_copy(doc, pending), "keep"
    css = page_css(doc.page_setup)
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


def to_pdf(doc: AimDocument, path: Union[str, Path], *,
           pending: str = "keep", extra_css: Optional[str] = None) -> Path:
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
            "install chromium") from exc
    out = Path(path)
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # browser binary missing
            raise RuntimeError(
                "Chromium is not installed for Playwright — run: "
                "python -m playwright install chromium") from exc
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.pdf(path=str(out), print_background=True,
                     prefer_css_page_size=True)
        finally:
            browser.close()
    return out
