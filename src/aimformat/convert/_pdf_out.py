"""`.aim` → PDF via headless Chromium (extra ``pdf``).

Prints the standalone-HTML rendering (see :mod:`._html_out`) through
Playwright's Chromium. The browser binary is a one-time setup step::

    pip install 'aimformat[pdf]'
    python -m playwright install chromium

Uses the sync Playwright API — call from a worker thread when inside an
asyncio event loop (sync Playwright refuses to run on a running loop).
"""

from __future__ import annotations

from pathlib import Path

from ..document import AimDocument
from ._html_out import to_html

__all__ = ["to_pdf"]


def to_pdf(doc: AimDocument, path: str | Path, *, pending: str = "keep") -> Path:
    """Print *doc* to a PDF file at *path*; returns the path.

    ``pending`` as in :func:`to_html` (default ``"keep"`` — the pending-
    changes memo prints as part of the document).
    """
    html = to_html(doc, pending=pending)
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
        except Exception as exc:  # browser binary missing
            raise RuntimeError(
                "Chromium is not installed for Playwright — run: "
                "python -m playwright install chromium"
            ) from exc
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.pdf(
                path=str(out),
                format="A4",
                print_background=True,
                margin={"top": "15mm", "bottom": "15mm", "left": "15mm", "right": "15mm"},
            )
        finally:
            browser.close()
    return out
