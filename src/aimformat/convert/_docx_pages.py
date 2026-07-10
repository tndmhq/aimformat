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
  anchored by matching the last body text before the break against the
  ingested chunks' text (best-effort: an unmatched hint is skipped, never
  guessed).

Everything here is a no-op without python-docx (the ``docx`` extra): the
ingest itself never fails over pagination.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

from ..document import AimDocument
from ..errors import InvalidOperation
from ..events import Actor
from ..registry import REGISTRY

__all__ = ["apply_docx_pagination"]

_EMU_PER_MM = 36000
_SIZE_TOLERANCE_MM = 1.5
_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _match_named_size(w_mm: float, h_mm: float) -> Optional[tuple[str, str]]:
    """(size, orientation) for a registered size within tolerance, or None."""
    for name, (w, h) in REGISTRY.raw["page"]["sizes_mm"].items():
        if abs(w_mm - w) <= _SIZE_TOLERANCE_MM and \
                abs(h_mm - h) <= _SIZE_TOLERANCE_MM:
            return name, "portrait"
        if abs(w_mm - h) <= _SIZE_TOLERANCE_MM and \
                abs(h_mm - w) <= _SIZE_TOLERANCE_MM:
            return name, "landscape"
    return None


def _read_page_setup(docx_doc) -> Optional[dict]:
    section = docx_doc.sections[0]
    if section.page_width is None or section.page_height is None:
        return None
    match = _match_named_size(section.page_width / _EMU_PER_MM,
                              section.page_height / _EMU_PER_MM)
    if match is None:
        return None
    size, orientation = match

    def margin(emu) -> str:
        mm = max(0.0, round((emu or 0) / _EMU_PER_MM, 1))
        mm = min(mm, float(REGISTRY.raw["page"]["margin_max_mm"]))
        return f"{mm:g}mm"

    return {"size": size, "orientation": orientation,
            "margins": {"top": margin(section.top_margin),
                        "right": margin(section.right_margin),
                        "bottom": margin(section.bottom_margin),
                        "left": margin(section.left_margin)}}


def _read_break_anchors(docx_doc) -> list[str]:
    """Normalized text of the last body content before each explicit break,
    in document order ('' when a break precedes any text)."""
    from docx.oxml.ns import qn
    br_tag, type_attr = qn("w:br"), qn("w:type")
    anchors: list[str] = []
    last_text = ""
    for para in docx_doc.paragraphs:
        if para.paragraph_format.page_break_before and last_text:
            anchors.append(last_text)
        prefix = ""
        for run in para.runs:
            has_break = any(br.get(type_attr) == "page"
                            for br in run._r.findall(br_tag))
            if has_break:
                before = _norm(prefix) or last_text
                if before:
                    anchors.append(before)
            prefix += run.text or ""
        if _norm(para.text):
            last_text = _norm(para.text)
    return anchors


def _insert_breaks(doc: AimDocument, anchors: list[str], *,
                   author: Actor) -> int:
    """Insert an aim-page-break after each anchored top-level construct.

    A hint matches a construct when its text equals the construct's own
    normalized text or any of its item chunks' (a break after a list's last
    item anchors on the list container)."""
    candidates: list[tuple[str, set[str]]] = []
    for el in doc._state.constructs():
        cid = el.chunk_id or el.container_id
        if not cid:
            continue
        texts = {_norm(el.text())}
        texts.update(_norm(item.text()) for item in el.iter()
                     if item is not el and item.chunk_id)
        candidates.append((cid, {t for t in texts if t}))
    inserted, start = 0, 0
    for anchor in anchors:
        for i in range(start, len(candidates)):
            cid, texts = candidates[i]
            if anchor in texts:
                doc.add_chunk("<aim-page-break></aim-page-break>",
                              author=author, after=cid,
                              explanation="Explicit page break in the source "
                                          "document")
                inserted, start = inserted + 1, i + 1
                break
    return inserted


def apply_docx_pagination(doc: AimDocument, path: Union[str, Path], *,
                          author: Actor) -> None:
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
                doc.set_page_setup(page, author=author,
                                   explanation="Page setup from the source "
                                               "document")
            except InvalidOperation:
                pass  # defaults already, or a degenerate sectPr — skip
        _insert_breaks(doc, _read_break_anchors(docx_doc), author=author)
