"""Converters between `.aim` and the main document formats.

Import: :func:`from_text` (stdlib), :func:`from_markdown` (extra
``markdown``), :func:`from_docx` (extra ``docx`` — a native OOXML importer
that preserves styling and turns Word tracked changes into pending
proposals, :mod:`._docx_in`; :func:`import_docx` also returns an
:class:`ImportReport`), :func:`from_pdf` (extra
``ingest`` — a docling wrapper over :func:`aimformat.from_docling`), and
the extension dispatcher :func:`from_path`.

Export: :func:`to_markdown` (stdlib), :func:`to_html` (stdlib),
:func:`to_pdf` (extra ``pdf``), plus :func:`aimformat.to_docx` re-exported
for symmetry.

The core package stays dependency-free: every heavy dependency is an
optional extra, imported lazily with an actionable error message.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import BinaryIO

from ..canonical import escape_text
from ..document import AimDocument, new_document
from ..events import Actor, external
from ..export_docx import to_docx
from ..ingest import finish_import, from_docling
from ._docx_pages import apply_docx_pagination
from ._html_out import to_html
from ._markdown_in import from_markdown
from ._markdown_out import to_markdown
from ._pdf_out import to_pdf
from ._report import AimImportWarning, CommentNote, ImportReport, ImportResult, RevisionNote

__all__ = [
    "from_text",
    "from_markdown",
    "from_docx",
    "import_docx",
    "ImportResult",
    "ImportReport",
    "RevisionNote",
    "CommentNote",
    "AimImportWarning",
    "from_pdf",
    "from_path",
    "to_markdown",
    "to_html",
    "to_pdf",
    "to_docx",
    # the python-docx pagination side pass stays available for callers who
    # run a DOCX through docling themselves (from_docling) — the native
    # from_docx no longer needs it
    "apply_docx_pagination",
]

_BLANK_LINES = re.compile(r"\n\s*\n")


def from_text(
    text: str,
    *,
    title: str | None = None,
    lang: str = "en",
    author: Actor | None = None,
    theme: dict[str, str] | None = None,
) -> AimDocument:
    """Plain text → one ``<p>`` chunk per blank-line-separated paragraph
    (single newlines inside a paragraph become ``<br>``)."""
    who = author or external("text-import")
    doc = new_document(title=title or "Imported text", lang=lang, theme=theme)
    paragraphs = [p for p in (part.strip() for part in _BLANK_LINES.split(text)) if p]
    with doc.batch():
        for para in paragraphs:
            lines = [escape_text(line.strip()) for line in para.splitlines() if line.strip()]
            doc.add_chunk("<p>" + "<br>".join(lines) + "</p>", author=who)
    return finish_import(doc, author=who, explanation="Imported from plain text")


def _docling_document(path: str | Path):
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise ImportError(
            "DOCX/PDF import requires docling (extra 'ingest'): pip install 'aimformat[ingest]'"
        ) from exc
    return DocumentConverter().convert(str(path)).document


def import_docx(
    source: str | Path | bytes | BinaryIO,
    *,
    title: str | None = None,
    lang: str = "en",
    author: Actor | None = None,
    theme: dict[str, str] | None = None,
    tracked: str = "propose",
    max_revisions: int = 5000,
) -> ImportResult:
    """DOCX → .aim plus an :class:`ImportReport` of what was and was not
    carried (extra ``docx``).

    Word tracked changes become pending proposals on the ORIGINAL text by
    default (``tracked="propose"``), attributed to their Word authors: the
    document's accept-all then equals Word's *Accept All* and its reject-all
    equals Word's *Reject All* — except baked numbering labels, which keep
    the original numbering, and formatting the format cannot express; the
    report lists both. ``tracked="accept"`` / ``"reject"`` import that
    resolved text instead, with no proposals. A document carrying more than
    ``max_revisions`` revision records is refused in ``"propose"`` mode.

    Word comments are reported, never stored: the format has no comment
    construct, and the text they were anchored on stays intact."""
    try:
        from ._docx_in import import_docx_source
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise ImportError(
            "DOCX import requires docx-parser-converter (extra 'docx'): "
            "pip install 'aimformat[docx]'"
        ) from exc
    return import_docx_source(
        source,
        title=title,
        lang=lang,
        author=author or external("docx-import"),
        theme=theme,
        tracked=tracked,
        max_revisions=max_revisions,
    )


def from_docx(
    path: str | Path | bytes | BinaryIO,
    *,
    title: str | None = None,
    lang: str = "en",
    author: Actor | None = None,
    theme: dict[str, str] | None = None,
    tracked: str = "propose",
    max_revisions: int = 5000,
) -> AimDocument:
    """DOCX → .aim natively (extra ``docx``), styling preserved.

    The importer walks the OOXML itself (docx-parser-converter parse layer
    behind the :mod:`._docx_seam` boundary): the document theme derives
    from ``theme1.xml`` (caller-supplied ``theme`` slots win), local run
    intent becomes literal paint/typography and the classic marks, and
    explicit pagination intent lands inline — see :mod:`._docx_in`.

    An explicit ``title`` wins; otherwise the Title-styled paragraph, the
    first ``h1``, and the file stem are the fallbacks in that order.

    Tracked changes and comments: see :func:`import_docx`, which this wraps.
    Whatever the import does not carry is announced as an
    :class:`AimImportWarning`."""
    result = import_docx(
        path,
        title=title,
        lang=lang,
        author=author,
        theme=theme,
        tracked=tracked,
        max_revisions=max_revisions,
    )
    result.report.emit_warnings(stacklevel=3)
    return result.document


def from_pdf(
    path: str | Path,
    *,
    title: str | None = None,
    lang: str = "en",
    author: Actor | None = None,
    theme: dict[str, str] | None = None,
) -> AimDocument:
    """PDF → .aim via docling (extra ``ingest``; OCR per docling defaults)."""
    return from_docling(
        _docling_document(path),
        title=title,
        lang=lang,
        author=author or external("pdf-import"),
        theme=theme,
    )


_DISPATCH = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".docx": "docx",
    ".pdf": "pdf",
    ".aim": "aim",
    ".html": "aim",
}


def from_path(
    path: str | Path,
    *,
    title: str | None = None,
    lang: str = "en",
    author: Actor | None = None,
    theme: dict[str, str] | None = None,
    tracked: str = "propose",
    max_revisions: int = 5000,
) -> AimDocument:
    """Convert *path* to an :class:`AimDocument`, dispatching on extension
    (.md/.markdown, .txt, .docx, .pdf; .aim/.html load as-is). ``tracked``
    and ``max_revisions`` apply to DOCX input (see :func:`import_docx`)."""
    p = Path(path)
    kind = _DISPATCH.get(p.suffix.lower())
    if kind is None:
        raise ValueError(
            f"unsupported input format: {p.suffix!r} (supported: {', '.join(sorted(_DISPATCH))})"
        )
    if kind == "aim":
        return AimDocument.load(p)
    if kind == "markdown":  # utf-8-sig: a Windows BOM must not become text
        return from_markdown(
            p.read_text("utf-8-sig"), title=title, lang=lang, author=author, theme=theme
        )
    if kind == "text":
        return from_text(
            p.read_text("utf-8-sig"), title=title or p.stem, lang=lang, author=author, theme=theme
        )
    if kind == "docx":
        return from_docx(
            p,
            title=title,
            lang=lang,
            author=author,
            theme=theme,
            tracked=tracked,
            max_revisions=max_revisions,
        )
    return from_pdf(p, title=title, lang=lang, author=author, theme=theme)
