"""Simulated colleague edits on an exported DOCX, the way Word leaves the
XML: bookmarks travel with the paragraph content they sit in. Test helper."""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable
from copy import deepcopy

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def q(name: str) -> str:
    return f"{{{W}}}{name}"


class Colleague:
    """Load a DOCX, edit ``word/document.xml``, save as bytes."""

    def __init__(self, data: bytes):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.parts = {n: zf.read(n) for n in zf.namelist()}
        self.root = etree.fromstring(self.parts["word/document.xml"])
        self.body = self.root.find(q("body"))

    # -- finding --------------------------------------------------------------
    def paragraph(self, uid: str):
        """The paragraph carrying ``_aim_<uid>``."""
        for bm in self.body.iter(q("bookmarkStart")):
            if bm.get(q("name")) == f"_aim_{uid}":
                return next(bm.iterancestors(q("p")))
        raise KeyError(uid)

    @staticmethod
    def text(p) -> str:
        return "".join(t.text or "" for t in p.iter(q("t")))

    # -- edits ----------------------------------------------------------------
    def replace(self, uid: str, old: str, new: str) -> None:
        for t in self.paragraph(uid).iter(q("t")):
            if old in (t.text or ""):
                t.text = t.text.replace(old, new, 1)
                t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                return
        raise AssertionError(f"{old!r} not in {uid}")

    def retype(self, uid: str, text: str) -> None:
        ts = list(self.paragraph(uid).iter(q("t")))
        for t in ts:
            t.text = ""
        ts[0].text = text

    def delete(self, uid: str) -> None:
        p = self.paragraph(uid)
        p.getparent().remove(p)

    @staticmethod
    def _blank_copy(p, text: str):
        newp = deepcopy(p)
        for bm in list(newp.iter(q("bookmarkStart"))) + list(newp.iter(q("bookmarkEnd"))):
            bm.getparent().remove(bm)
        ts = list(newp.iter(q("t")))
        for t in ts:
            t.text = ""
        ts[0].text = text
        return newp

    def insert_after(self, uid: str, text: str):
        p = self.paragraph(uid)
        newp = self._blank_copy(p, text)
        p.addnext(newp)
        return newp

    def move_after(self, uid: str, anchor: str) -> None:
        p = self.paragraph(uid)
        p.getparent().remove(p)
        self.paragraph(anchor).addnext(p)

    def split(self, uid: str, at: str) -> None:
        """Enter mid-paragraph, right before *at*."""
        p = self.paragraph(uid)
        full = self.text(p)
        cut = full.index(at)
        second = self._blank_copy(p, full[cut:])
        ts = list(p.iter(q("t")))
        for t in ts:
            t.text = ""
        ts[0].text = full[:cut].rstrip()
        p.addnext(second)

    def merge(self, uid: str, other: str) -> None:
        """Delete the paragraph mark between *uid* and the following *other*."""
        a, b = self.paragraph(uid), self.paragraph(other)
        for child in list(b):
            if child.tag != q("pPr"):
                a.append(child)
        b.getparent().remove(b)

    def enter_at_start(self, uid: str, text: str) -> None:
        """Enter at a paragraph's start, then type into the new paragraph:
        the bookmark ends up on the NEW paragraph."""
        p = self.paragraph(uid)
        newp = self._blank_copy(p, text)
        for bm in p.findall(q("bookmarkStart")) + p.findall(q("bookmarkEnd")):
            p.remove(bm)
            ppr = newp.find(q("pPr"))
            (ppr.addnext(bm) if ppr is not None else newp.insert(0, bm))
        p.addprevious(newp)

    def strip_marks(self) -> None:
        for bm in list(self.body.iter(q("bookmarkStart"))) + list(self.body.iter(q("bookmarkEnd"))):
            bm.getparent().remove(bm)

    def drop_manifest(self) -> None:
        for name in [n for n in self.parts if n.startswith("customXml/")]:
            if b"urn:aimformat:docx-roundtrip" in self.parts[name]:
                del self.parts[name]

    def edit_xml(self, fn: Callable[[etree._Element], None]) -> None:
        fn(self.body)

    def save(self, *, author: str | None = "Rosa Lind", modified: str | None = None) -> bytes:
        self.parts["word/document.xml"] = etree.tostring(
            self.root, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        core = self.parts["docProps/core.xml"].decode()
        if author is not None:
            core = re.sub(
                r"<cp:lastModifiedBy>.*?</cp:lastModifiedBy>|<cp:lastModifiedBy/>", "", core
            )
            core = core.replace(
                "</cp:coreProperties>",
                f"<cp:lastModifiedBy>{author}</cp:lastModifiedBy></cp:coreProperties>",
            )
        if modified is not None:
            core = re.sub(r"<dcterms:modified[^>]*>.*?</dcterms:modified>", "", core)
            core = core.replace(
                "</cp:coreProperties>",
                f'<dcterms:modified xsi:type="dcterms:W3CDTF">{modified}</dcterms:modified>'
                "</cp:coreProperties>",
            )
        self.parts["docProps/core.xml"] = core.encode()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, blob in self.parts.items():
                zf.writestr(name, blob)
        return buf.getvalue()
