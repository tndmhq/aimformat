"""Converters between `.aim` and the main document formats.

Import: :func:`from_text` (stdlib), :func:`from_markdown` (extra
``markdown``), :func:`from_docx` / :func:`from_pdf` (extra ``ingest`` —
docling wrappers over :func:`aimformat.from_docling`), and the extension
dispatcher :func:`from_path`.

Export: :func:`to_markdown` (stdlib), :func:`to_html` (stdlib),
:func:`to_pdf` (extra ``pdf``), plus :func:`aimformat.to_docx` re-exported
for symmetry.

The core package stays dependency-free: every heavy dependency is an
optional extra, imported lazily with an actionable error message.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

from ..canonical import escape_text
from ..document import AimDocument, new_document
from ..events import Actor, external
from ..export_docx import to_docx
from ..ingest import from_docling
from ._html_out import to_html
from ._markdown_in import from_markdown
from ._markdown_out import to_markdown
from ._pdf_out import to_pdf

__all__ = [
    "from_text", "from_markdown", "from_docx", "from_pdf", "from_path",
    "to_markdown", "to_html", "to_pdf", "to_docx",
]

_BLANK_LINES = re.compile(r"\n\s*\n")


def from_text(text: str, *, title: Optional[str] = None, lang: str = "en",
              author: Optional[Actor] = None,
              theme: Optional[dict[str, str]] = None) -> AimDocument:
    """Plain text → one ``<p>`` chunk per blank-line-separated paragraph
    (single newlines inside a paragraph become ``<br>``)."""
    who = author or external("text-import")
    doc = new_document(title=title or "Imported text", lang=lang, theme=theme)
    paragraphs = [p for p in
                  (part.strip() for part in _BLANK_LINES.split(text))
                  if p]
    with doc.batch():
        for para in paragraphs:
            lines = [escape_text(line.strip())
                     for line in para.splitlines() if line.strip()]
            doc.add_chunk("<p>" + "<br>".join(lines) + "</p>", author=who,
                          explanation="Imported from plain text")
    return doc


def _docling_document(path: Union[str, Path]):
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise ImportError(
            "DOCX/PDF import requires docling (extra 'ingest'): "
            "pip install 'aimformat[ingest]'") from exc
    return DocumentConverter().convert(str(path)).document


def from_docx(path: Union[str, Path], *, title: Optional[str] = None,
              lang: str = "en", author: Optional[Actor] = None,
              theme: Optional[dict[str, str]] = None) -> AimDocument:
    """DOCX → .aim via docling (extra ``ingest``)."""
    return from_docling(
        _docling_document(path), title=title or Path(path).stem, lang=lang,
        author=author or external("docx-import"), theme=theme)


def from_pdf(path: Union[str, Path], *, title: Optional[str] = None,
             lang: str = "en", author: Optional[Actor] = None,
             theme: Optional[dict[str, str]] = None) -> AimDocument:
    """PDF → .aim via docling (extra ``ingest``; OCR per docling defaults)."""
    return from_docling(
        _docling_document(path), title=title or Path(path).stem, lang=lang,
        author=author or external("pdf-import"), theme=theme)


_DISPATCH = {
    ".md": "markdown", ".markdown": "markdown",
    ".txt": "text",
    ".docx": "docx",
    ".pdf": "pdf",
    ".aim": "aim", ".html": "aim",
}


def from_path(path: Union[str, Path], *, title: Optional[str] = None,
              lang: str = "en", author: Optional[Actor] = None,
              theme: Optional[dict[str, str]] = None) -> AimDocument:
    """Convert *path* to an :class:`AimDocument`, dispatching on extension
    (.md/.markdown, .txt, .docx, .pdf; .aim/.html load as-is)."""
    p = Path(path)
    kind = _DISPATCH.get(p.suffix.lower())
    if kind is None:
        raise ValueError(f"unsupported input format: {p.suffix!r} "
                         f"(supported: {', '.join(sorted(_DISPATCH))})")
    if kind == "aim":
        return AimDocument.load(p)
    default_title = title or p.stem
    if kind == "markdown":
        return from_markdown(p.read_text("utf-8"), title=title, lang=lang,
                             author=author, theme=theme)
    if kind == "text":
        return from_text(p.read_text("utf-8"), title=default_title,
                         lang=lang, author=author, theme=theme)
    if kind == "docx":
        return from_docx(p, title=default_title, lang=lang, author=author,
                         theme=theme)
    return from_pdf(p, title=default_title, lang=lang, author=author,
                    theme=theme)
