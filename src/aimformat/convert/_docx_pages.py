"""Explicit DOCX pagination intent → .aim (python-docx pass beside docling).

docling gives the logical structure but abstracts pagination away: its only
page signal is ``prov.page_no``, a *layout* artifact that ingest drops by
design (a renderer recomputes soft breaks). Author *intent*, though, lives
in the DOCX XML and is worth carrying over:

- the first section's ``sectPr`` (page size, orientation, margins) →
  ``set_page_setup``, when the size matches a registered named size within
  tolerance (v0.2 registers named sizes only — odd custom sizes are skipped);
- explicit page breaks (``<w:br w:type="page"/>`` runs and
  ``w:pageBreakBefore`` paragraph properties) → ``<aim-page-break>`` chunks,
  anchored by matching body text — by text *and* occurrence, so repeated
  sentences resolve to the right copy — against the ingested chunks' text.
  A break between paragraphs anchors on the paragraph before it; a break
  *inside* a paragraph anchors on that paragraph's completed text (docling
  does not split it). Best-effort: an unmatched hint is skipped, never
  guessed.

Everything here is a no-op without python-docx (the ``docx`` extra): the
ingest itself never fails over pagination.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..document import AimDocument
from ..errors import InvalidOperation
from ..events import Actor
from ..pagesetup import _fmt_mm
from ..registry import REGISTRY

__all__ = ["apply_docx_pagination"]

_EMU_PER_MM = 36000
_SIZE_TOLERANCE_MM = 1.5
_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _match_named_size(w_mm: float, h_mm: float) -> tuple[str, str] | None:
    """(size, orientation) for a registered size within tolerance, or None."""
    for name, (w, h) in REGISTRY.page_sizes_mm.items():
        if abs(w_mm - w) <= _SIZE_TOLERANCE_MM and abs(h_mm - h) <= _SIZE_TOLERANCE_MM:
            return name, "portrait"
        if abs(w_mm - h) <= _SIZE_TOLERANCE_MM and abs(h_mm - w) <= _SIZE_TOLERANCE_MM:
            return name, "landscape"
    return None


def _read_page_setup(docx_doc) -> dict | None:
    section = docx_doc.sections[0]
    if section.page_width is None or section.page_height is None:
        return None
    match = _match_named_size(section.page_width / _EMU_PER_MM, section.page_height / _EMU_PER_MM)
    if match is None:
        return None
    size, orientation = match

    def margin(emu) -> str:
        # clamp into the registry bounds up front: a source file's exotic
        # sectPr must degrade the margin, not fail the whole ingest
        mm = max(0.0, round((emu or 0) / _EMU_PER_MM, 1))
        mm = min(mm, float(REGISTRY.margin_max_mm))
        return _fmt_mm(mm)

    return {
        "size": size,
        "orientation": orientation,
        "margins": {
            "top": margin(section.top_margin),
            "right": margin(section.right_margin),
            "bottom": margin(section.bottom_margin),
            "left": margin(section.left_margin),
        },
    }


def _read_break_anchors(docx_doc) -> list[tuple[str, int]]:
    """(normalized text, occurrence) of the last body content before each
    explicit break, in document order.

    ``occurrence`` counts how many body paragraphs reading exactly that text
    have appeared so far (1-based): the same sentence may legitimately occur
    twice, and a bare-text hint would anchor the break on the *first* copy.
    Run XML is walked node by node — WordprocessingML allows the break in
    the same run as the text before it (``<w:t>Beta</w:t><w:br/>``), which
    ``run.text``-then-``findall`` bookkeeping would miss."""
    from docx.oxml.ns import qn

    br_tag, t_tag, type_attr = qn("w:br"), qn("w:t"), qn("w:type")
    anchors: list[tuple[str, int]] = []
    seen: dict[str, int] = {}
    last: tuple[str, int] = ("", 0)
    for para in docx_doc.paragraphs:
        if para.paragraph_format.page_break_before and last[0]:
            anchors.append(last)
        prefix = ""
        mid_breaks = 0
        for run in para.runs:
            for node in run._r:
                if node.tag == br_tag and node.get(type_attr) == "page":
                    if _norm(prefix):
                        # a break inside this paragraph: docling keeps the
                        # paragraph whole, so anchor on its completed full
                        # text — the prefix alone would match whatever OTHER
                        # paragraph happens to read exactly the same
                        mid_breaks += 1
                    elif last[0]:
                        anchors.append(last)
                elif node.tag == t_tag:
                    prefix += node.text or ""
        text = _norm(para.text)
        if text:
            seen[text] = seen.get(text, 0) + 1
            last = (text, seen[text])
            anchors.extend([last] * mid_breaks)
    return anchors


def _insert_breaks(doc: AimDocument, anchors: list[tuple[str, int]], *, author: Actor) -> int:
    """Insert an aim-page-break after each anchored top-level construct.

    A hint matches a construct when its text equals the construct's own
    normalized text or any of its item chunks' (a break after a list's last
    item anchors on the list container). Matching is by *occurrence*,
    counted from the top of the document on both sides, so duplicate texts
    resolve to the right copy; a hint whose occurrence cannot be reached
    (or that would move backwards) is skipped, never guessed."""
    candidates: list[tuple[str, dict[str, int]]] = []
    for el in doc._state.constructs():
        cid = el.chunk_id or el.container_id
        if not cid:
            continue
        texts: dict[str, int] = {}
        for item in el.iter():
            if item is not el and item.chunk_id:
                t = _norm(item.text())
                if t:
                    texts[t] = texts.get(t, 0) + 1
        own = _norm(el.text())
        if own and own not in texts:  # items already cover a one-item list
            texts[own] = 1
        candidates.append((cid, texts))
    inserted, start = 0, 0
    prev_break_id, prev_i = None, -1
    for anchor, occurrence in anchors:
        count, hit = 0, None
        for i, (cid, texts) in enumerate(candidates):
            count += texts.get(anchor, 0)
            if count >= occurrence:
                hit = (i, cid)
                break
        if hit is None:
            continue
        i, cid = hit
        if i < start:
            if prev_break_id is None or i != prev_i:
                continue  # would move backwards: skip, never guess
            # a repeated hint on the same anchor is a consecutive break
            # (an intentional blank page): chain it after the previous one
            cid = prev_break_id
        chunk = doc.add_chunk(
            "<aim-page-break></aim-page-break>",
            author=author,
            after=cid,
            explanation="Explicit page break in the source document",
        )
        prev_break_id, prev_i = chunk.id, i
        inserted, start = inserted + 1, i + 1
    return inserted


def apply_docx_pagination(doc: AimDocument, path: str | Path, *, author: Actor) -> None:
    """Carry a DOCX file's explicit pagination intent onto *doc* (in place).

    Best-effort by contract: without python-docx, or on any surprise in the
    source XML, the ingested document is simply left unpaginated.
    """
    try:
        from docx import Document
    except ImportError:  # pragma: no cover - environment-dependent
        return
    try:
        docx_doc = Document(str(path))
    except Exception:
        return
    with doc.batch():
        page = _read_page_setup(docx_doc)
        if page is not None:
            try:
                doc.set_page_setup(
                    page, author=author, explanation="Page setup from the source document"
                )
            except InvalidOperation:
                pass  # defaults already, or a degenerate sectPr — skip
        _insert_breaks(doc, _read_break_anchors(docx_doc), author=author)
