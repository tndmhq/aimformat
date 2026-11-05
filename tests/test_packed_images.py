"""Imported images are stored once (spec §9; plan items P9-P15).

Every importer moves PNG, JPEG and GIF data out of the body into the asset
registry BEFORE any history is written: the body, the baseline snapshot, a
tracked import's pending payloads and the ``add`` events kept on the H008
fallback path all point at one stored image. Other formats, and data URIs
that do not decode as strict base64, stay inline and the import goes on.
Around that: asset gc counts baseline snapshots, lint warns about a reference
that resolves to nothing (S036) and an asset whose id is not the hash of its
bytes (S037), and the DOCX and Markdown exporters resolve packed images.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import re
import struct
import zlib

import pytest

import aimformat as aim
from aimformat.canonical import serialize
from conftest import BOT, ME, ts


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _bmp(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    row = bytes((rgb[2], rgb[1], rgb[0])) * width
    row += b"\x00" * ((4 - len(row) % 4) % 4)
    pixels = row * height
    header = b"BM" + struct.pack("<IHHI", 54 + len(pixels), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    return header + info + pixels


PNG_A = _png(4, 2, (200, 30, 30))
PNG_B = _png(3, 3, (20, 120, 200))
#: the classic 1×1 GIF
GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
BMP = _bmp(5, 2, (10, 10, 200))


def _uri(blob: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(blob).decode()


def _asset(blob: bytes) -> str:
    return "asset-" + hashlib.sha256(blob).hexdigest()[:12]


def _parts(doc: aim.AimDocument) -> dict[str, str]:
    """The sections of a saved document an image could hide in."""
    text = doc.dumps()

    def block(pattern: str) -> str:
        match = re.search(pattern, text, re.S)
        return match.group(0) if match else ""

    return {
        "body": "\n".join(serialize(el) for el in doc._state.constructs()),
        "history": block(r'<script type="application/aim-history\+jsonl">.*?</script>'),
        "proposals": block(r"<aim-proposals>.*?</aim-proposals>"),
        "assets": block(r"<aim-assets>.*?</aim-assets>"),
    }


def _symbols(text: str) -> set[str]:
    return set(re.findall(r'<symbol id="(asset-[^"]+)"', text))


def _dangling(text: str) -> set[str]:
    return set(re.findall(r'href="#(asset-[^"]+)"', text)) - _symbols(text)


def _codes(doc_or_text, level: str | None = None) -> list[str]:
    text = doc_or_text if isinstance(doc_or_text, str) else doc_or_text.dumps()
    return [f.code for f in aim.lint_text(text) if level is None or f.level == level]


def _assert_stored_once(doc: aim.AimDocument, *blobs: bytes) -> None:
    parts = _parts(doc)
    assert "data:image/" not in parts["body"]
    assert "data:image/" not in parts["history"]  # the baseline snapshot included
    assert "data:image/" not in parts["proposals"]
    assert _symbols(parts["assets"]) == {_asset(b) for b in blobs}
    assert parts["assets"].count("data:image/") == len(set(blobs))
    assert not _dangling(doc.dumps())
    assert _codes(doc, "error") == []
    assert not {"S036", "S037"} & set(_codes(doc))
    assert doc.verify() == []


# -- every importer stores an image once ---------------------------------------


def _markdown_doc() -> aim.AimDocument:
    pytest.importorskip("markdown_it")
    return aim.from_markdown(
        f"# Pictures\n\nA ![red]({_uri(PNG_A)}) and again ![red]({_uri(PNG_A)}).\n\n"
        f"![blue]({_uri(PNG_B)})\n"
    )


def _docling_dict(*uris: str) -> dict:
    pytest.importorskip("docling_core")
    from docling_core.types.doc import DoclingDocument

    built = DoclingDocument(name="pictures")
    built.add_heading("Pictures", level=1)
    for _ in uris:
        built.add_picture()
    obj = built.export_to_dict()
    for pic, uri in zip(obj["pictures"], uris, strict=True):
        pic["image"] = {
            "mimetype": "image/png",
            "dpi": 72,
            "size": {"width": 4, "height": 2},
            "uri": uri,
        }
    return obj


def _docling_doc() -> aim.AimDocument:
    return aim.from_docling(_docling_dict(_uri(PNG_A), _uri(PNG_A), _uri(PNG_B)))


def _docx_bytes(*pictures: bytes, heading: str = "Pictures") -> bytes:
    pytest.importorskip("docx_parser_converter")
    docx = pytest.importorskip("docx")
    built = docx.Document()
    built.add_heading(heading, level=1)
    for blob in pictures:
        built.add_paragraph("Next picture:")
        built.add_picture(io.BytesIO(blob))
    out = io.BytesIO()
    built.save(out)
    return out.getvalue()


def _docx_doc() -> aim.AimDocument:
    return aim.from_docx(_docx_bytes(PNG_A, PNG_A, PNG_B))


class TestImportersStoreImagesOnce:
    @pytest.mark.parametrize(
        "make", [_markdown_doc, _docling_doc, _docx_doc], ids=["markdown", "docling", "docx"]
    )
    def test_the_body_and_the_snapshot_point_at_one_stored_image(self, make):
        doc = make()
        assert [e.kind for e in doc.history] == ["baseline"]
        snapshot = json.dumps(doc.history[0].get("snapshot"))
        assert f"#{_asset(PNG_A)}" in snapshot and f"#{_asset(PNG_B)}" in snapshot
        body = _parts(doc)["body"]
        assert body.count('role="img"') == 3
        assert body.count(f'<use href="#{_asset(PNG_A)}"/>') == 2  # two references, one blob
        _assert_stored_once(doc, PNG_A, PNG_B)

    def test_the_packed_form_is_what_pack_assets_writes(self):
        # one helper writes both: an importer and `aim pack` agree byte for byte
        pytest.importorskip("markdown_it")
        imported = aim.from_markdown(f"Logo: ![logo]({_uri(PNG_A)})\n")
        packed = aim.new_document(title="t")
        inline = f'<p data-aim="p1">Logo: <img alt="logo" src="{_uri(PNG_A)}"></p>'
        packed.add_chunk(inline, author=ME)
        packed.pack_assets(author=ME)
        strip = re.compile(r' data-aim="[^"]*"')
        assert strip.sub("", imported.chunks[0].html) == strip.sub("", packed.chunks[0].html)
        assert _parts(imported)["assets"] == _parts(packed)["assets"]

    def test_gif_is_packed_with_its_size(self):
        pytest.importorskip("markdown_it")
        doc = aim.from_markdown(f"![dot]({_uri(GIF, 'image/gif')})\n")
        assert 'viewBox="0 0 1 1"' in doc.chunks[0].html
        _assert_stored_once(doc, GIF)


# -- formats and data the SDK does not pack stay inline (P10, P11) --------------


class TestKeptInline:
    def test_a_bad_data_uri_stays_inline_and_the_import_goes_on(self):
        pytest.importorskip("markdown_it")
        bad = "data:image/png;base64,AAA"  # bad padding: not strict base64
        doc = aim.from_markdown(f"![bad]({bad}) beside ![good]({_uri(PNG_A)})\n")
        html = doc.chunks[0].html
        assert f'src="{bad}"' in html
        assert f'<use href="#{_asset(PNG_A)}"/>' in html
        assert _symbols(doc.dumps()) == {_asset(PNG_A)}
        assert _codes(doc, "error") == [] and doc.verify() == []

    def test_a_line_wrapped_docling_uri_stays_inline(self):
        b64 = base64.b64encode(PNG_B).decode()
        lines = [b64[i : i + 16] for i in range(0, len(b64), 16)]  # MIME-style wrapping
        wrapped = "data:image/png;base64," + "\n".join(lines)
        doc = aim.from_docling(_docling_dict(wrapped, _uri(PNG_A)))
        figures = [c.html for c in doc.chunks if c.tag == "figure"]
        assert "<img" in figures[0] and "data:image/png;base64," in figures[0]
        assert f'<use href="#{_asset(PNG_A)}"/>' in figures[1]
        assert _symbols(doc.dumps()) == {_asset(PNG_A)}

    @pytest.mark.parametrize("fmt", ["BMP", "TIFF", "WEBP"])
    def test_formats_the_sdk_cannot_size_stay_inline(self, fmt):
        # through docling, which embeds any data:image/* (markdown-it itself
        # refuses data:image/bmp and data:image/tiff links)
        image = pytest.importorskip("PIL.Image")
        buf = io.BytesIO()
        try:
            image.new("RGB", (6, 2), (1, 2, 3)).save(buf, fmt)
        except (KeyError, OSError):  # pragma: no cover - a Pillow built without it
            pytest.skip(f"Pillow cannot write {fmt}")
        uri = _uri(buf.getvalue(), f"image/{fmt.lower()}")
        doc = aim.from_docling(_docling_dict(uri, _uri(PNG_A)))
        figures = [c.html for c in doc.chunks if c.tag == "figure"]
        assert f'src="{uri}"' in figures[0]
        assert f'<use href="#{_asset(PNG_A)}"/>' in figures[1]
        assert _symbols(doc.dumps()) == {_asset(PNG_A)}
        assert _codes(doc, "error") == [] and doc.verify() == []

    def test_jpeg_is_packed(self):
        pytest.importorskip("markdown_it")
        image = pytest.importorskip("PIL.Image")
        buf = io.BytesIO()
        image.new("RGB", (6, 2), (1, 2, 3)).save(buf, "JPEG")
        doc = aim.from_markdown(f"![photo]({_uri(buf.getvalue(), 'image/jpeg')})\n")
        assert 'viewBox="0 0 6 2"' in doc.chunks[0].html
        _assert_stored_once(doc, buf.getvalue())

    def test_a_docx_bmp_stays_inline_and_the_report_says_so(self):
        result = aim.import_docx(_docx_bytes(PNG_A, BMP))
        body = _parts(result.document)["body"]
        assert 'src="data:image/bmp;base64,' in body
        assert body.count("<use href=") == 1
        assert _symbols(result.document.dumps()) == {_asset(PNG_A)}
        assert any("1 image(s)" in w and "inline" in w for w in result.report.warnings)

    def test_a_docx_with_only_packed_images_reports_nothing_about_them(self):
        result = aim.import_docx(_docx_bytes(PNG_A))
        assert not [w for w in result.report.warnings if "image" in w]

    def test_the_collection_counts_each_image_it_left_inline_once(self):
        # the "does not decode" line cannot come from a DOCX (the importer
        # encodes the package bytes itself), so the collection is checked here
        from aimformat.ingest import _ImportAssets

        images = _ImportAssets()
        plain = "<p>No image.</p>"
        bad = '<p><img alt="x" src="data:image/png;base64,AAA"></p>'
        assert images.pack(plain) is plain and images.pack(bad) is bad
        for _ in range(2):  # the same data twice is one image
            images.pack(f'<p><img alt="b" src="{_uri(BMP, "image/bmp")}"></p>')
        assert images.assets == {}
        format_line, data_line = images.warnings()
        assert format_line.startswith("1 image(s) stay inline") and "PNG, JPEG" in format_line
        assert data_line.startswith("1 image(s) stay inline") and "base64" in data_line


# -- a tracked DOCX packs both views before alignment (P9) ----------------------


def _revision(tag: str, rid: int):
    """A ``w:ins`` or ``w:del`` revision record by Alice (python-docx's OXML)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    el = OxmlElement(f"w:{tag}")
    el.set(qn("w:id"), str(rid))
    el.set(qn("w:author"), "Alice Smith")
    el.set(qn("w:date"), "2026-09-01T10:00:00Z")
    return el


def _ins(rid: int):
    """A ``w:ins`` revision record by Alice (python-docx's OXML)."""
    return _revision("ins", rid)


def _tracked_docx_with_images() -> bytes:
    """An unchanged picture, a tracked text insertion, and a tracked
    insertion of a second picture: an image only Word's Accept All has."""
    pytest.importorskip("docx_parser_converter")
    docx = pytest.importorskip("docx")
    from docx.oxml import OxmlElement

    built = docx.Document()
    built.add_heading("Tracked images", level=1)
    built.add_paragraph("Intro paragraph.")
    built.add_picture(io.BytesIO(PNG_A))
    text_para = built.add_paragraph("Text with ")
    logo_para = built.add_paragraph("Logo: ")
    run = logo_para.add_run()
    run.add_picture(io.BytesIO(PNG_B))

    inserted = _ins(1)
    words = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "inserted words"
    words.append(t)
    inserted.append(words)
    text_para._p.append(inserted)
    wrapper = _ins(2)
    run._r.addprevious(wrapper)
    wrapper.append(run._r)
    out = io.BytesIO()
    built.save(out)
    return out.getvalue()


def _tracked_docx_with_an_inserted_figure() -> bytes:
    """An unchanged picture, then a paragraph holding only a second picture,
    inserted with tracking (its run and its paragraph mark): a figure only
    Word's Accept All has."""
    pytest.importorskip("docx_parser_converter")
    docx = pytest.importorskip("docx")
    from docx.oxml import OxmlElement

    built = docx.Document()
    built.add_heading("Tracked figure", level=1)
    built.add_picture(io.BytesIO(PNG_A))
    built.add_picture(io.BytesIO(PNG_B))
    para = built.paragraphs[-1]
    for rid, run in enumerate(list(para.runs), start=1):
        wrapper = _ins(rid)
        run._r.addprevious(wrapper)
        wrapper.append(run._r)
    mark = OxmlElement("w:rPr")
    mark.append(_ins(99))
    para._p.get_or_add_pPr().append(mark)
    built.add_paragraph("After.")
    out = io.BytesIO()
    built.save(out)
    return out.getvalue()


#: what each tracked change does to a paragraph holding only a picture: the
#: pictures it holds, the revision wrapping each one's run, and whether its
#: paragraph mark carries the same revision (a whole paragraph inserted or
#: deleted) or stays (the picture replaced inside a paragraph both views keep)
_PICTURE_ONLY = {
    "insert": ((PNG_B,), ("ins",), True),
    "delete": ((PNG_A,), ("del",), True),
    "replace": ((PNG_A, PNG_B), ("del", "ins"), False),
}


def _track_run(run, tag: str, rid: int) -> None:
    """Wrap a ``w:r`` element in a ``w:ins`` or ``w:del`` revision (a deleted
    run's text becomes ``w:delText``)."""
    from docx.oxml.ns import qn

    wrapper = _revision(tag, rid)
    run.addprevious(wrapper)
    wrapper.append(run)
    if tag == "del":
        for text in run.findall(qn("w:t")):
            text.tag = qn("w:delText")


def _track_mark(para, tag: str, rid: int) -> None:
    """Track the paragraph mark of *para* (a python-docx paragraph)."""
    from docx.oxml import OxmlElement

    props = OxmlElement("w:rPr")
    props.append(_revision(tag, rid))
    para._p.get_or_add_pPr().append(props)


def _track_paragraph(para, tag: str) -> None:
    """A whole paragraph inserted or deleted with tracking: its runs and its
    mark."""
    from docx.oxml.ns import qn

    for rid, run in enumerate(para._p.findall(qn("w:r")), start=1):
        _track_run(run, tag, rid)
    _track_mark(para, tag, 99)


def _saved(built) -> bytes:
    out = io.BytesIO()
    built.save(out)
    return out.getvalue()


def _tracked_picture_only_paragraph(change: str) -> bytes:
    """A heading and an intro, then a paragraph holding only a picture and
    changed with tracking (*change*, see ``_PICTURE_ONLY``), then a closing
    paragraph. Such a paragraph converts to a figure."""
    pytest.importorskip("docx_parser_converter")
    docx = pytest.importorskip("docx")

    pictures, tags, mark = _PICTURE_ONLY[change]
    built = docx.Document()
    built.add_heading("Tracked picture", level=1)
    built.add_paragraph("Intro.")
    para = built.add_paragraph()
    for rid, (blob, tag) in enumerate(zip(pictures, tags, strict=True), start=1):
        run = para.add_run()
        run.add_picture(io.BytesIO(blob))
        _track_run(run._r, tag, rid)
    if mark:
        _track_mark(para, tags[0], 99)
    built.add_paragraph("After.")
    return _saved(built)


def _picture_joined_into_an_empty_paragraph() -> bytes:
    """A picture inserted with tracking into a paragraph whose mark is
    deleted with tracking too, followed by an empty paragraph: Word's Accept
    All joins the picture into that paragraph (the mark owner), which then
    holds only the picture. Reject All has neither."""
    pytest.importorskip("docx_parser_converter")
    docx = pytest.importorskip("docx")

    built = docx.Document()
    built.add_heading("Joined picture", level=1)
    built.add_paragraph("Intro.")
    para = built.add_paragraph()
    run = para.add_run()
    run.add_picture(io.BytesIO(PNG_B))
    _track_run(run._r, "ins", 1)
    _track_mark(para, "del", 2)
    built.add_paragraph()  # owns the mark the picture joins
    built.add_paragraph("After.")
    return _saved(built)


def _moved_picture_only_paragraph() -> bytes:
    """A heading, then a paragraph holding only a picture that Word moved
    with tracking past the next paragraph: the source's runs and mark are
    ``moveFrom``, the destination's ``moveTo``, each side inside a move range
    named "move1"."""
    pytest.importorskip("docx_parser_converter")
    docx = pytest.importorskip("docx")
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    built = docx.Document()
    built.add_heading("Moved picture", level=1)
    built.add_picture(io.BytesIO(PNG_A))
    source = built.paragraphs[-1]._p
    built.add_paragraph("Middle.")
    end = built.add_paragraph("End.")
    destination = copy.deepcopy(source)
    end._p.addprevious(destination)
    for n, (para, kind) in enumerate(((source, "moveFrom"), (destination, "moveTo"))):
        runs = para.findall(qn("w:r"))
        start = _revision(f"{kind}RangeStart", 10 + n)
        start.set(qn("w:name"), "move1")
        wrapper = _revision(kind, 20 + n)
        runs[0].addprevious(start)
        runs[0].addprevious(wrapper)
        for run in runs:
            wrapper.append(run)
        stop = OxmlElement(f"w:{kind}RangeEnd")
        stop.set(qn("w:id"), start.get(qn("w:id")))
        wrapper.addnext(stop)
        props = para.find(qn("w:pPr"))
        if props is None:
            props = OxmlElement("w:pPr")
            para.insert(0, props)
        mark = OxmlElement("w:rPr")
        mark.append(_revision(kind, 30 + n))
        props.append(mark)
    return _saved(built)


#: a legacy VML picture (``w:pict``): the paragraph's text, the revision on the
#: picture's run, and whether the paragraph mark carries it too (a paragraph
#: holding only the picture, inserted or deleted)
_VML = {
    "insert": ("", "ins", True),
    "delete": ("", "del", True),
    "insert-beside-text": ("Logo follows: ", "ins", False),
}
_VML_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def _tracked_vml_picture(change: str) -> bytes:
    """A heading, then a paragraph with a legacy VML picture changed with
    tracking (*change*, see ``_VML``), then a closing paragraph. The typed
    model cannot see a VML picture: the importer recovers it after its
    paragraph as a figure."""
    pytest.importorskip("docx_parser_converter")
    docx = pytest.importorskip("docx")
    from lxml import etree

    text, tag, mark = _VML[change]
    built = docx.Document()
    built.add_heading("VML picture", level=1)
    para = built.add_paragraph(text)
    rid, _image = built.part.get_or_add_image(io.BytesIO(PNG_B))
    declarations = " ".join(f'xmlns:{prefix}="{uri}"' for prefix, uri in _VML_NS.items())
    run = etree.fromstring(
        f'<w:r {declarations}><w:pict><v:shape id="s1" style="width:30pt;height:30pt">'
        f'<v:imagedata r:id="{rid}" o:title="Logo"/></v:shape></w:pict></w:r>'
    )
    para._p.append(run)
    _track_run(run, tag, 1)
    if mark:
        _track_mark(para, tag, 99)
    built.add_paragraph("After.")
    return _saved(built)


def _picture_between_list_items(tag: str) -> bytes:
    """Four bulleted items with a paragraph holding only a picture after the
    second, inserted (``ins``: Word's Accept All splits the list around it)
    or deleted (``del``: Accept All joins the two lists) with tracking."""
    pytest.importorskip("docx_parser_converter")
    docx = pytest.importorskip("docx")

    built = docx.Document()
    built.add_heading("Picture in a list", level=1)
    built.add_paragraph("one", style="List Bullet")
    built.add_paragraph("two", style="List Bullet")
    built.add_picture(io.BytesIO(PNG_B))
    _track_paragraph(built.paragraphs[-1], tag)
    built.add_paragraph("three", style="List Bullet")
    built.add_paragraph("four", style="List Bullet")
    built.add_paragraph("After.")
    return _saved(built)


_IDS = re.compile(r' data-aim(?:-container)?="[^"]*"')


def _body_without_ids(doc: aim.AimDocument) -> list[str]:
    return [_IDS.sub("", serialize(el)) for el in doc._state.constructs()]


def _assert_resolves_like_word(doc: aim.AimDocument, data: bytes) -> None:
    """accept_all() gives Word's Accept All (the tracked='accept' import of
    *data*) and reject_all() its Reject All, each with every image stored
    once and no reference left unresolved."""
    for mode in ("accept", "reject"):
        word = aim.import_docx(data, tracked=mode).document
        resolved = aim.loads(doc.dumps())
        getattr(resolved, f"{mode}_all")(decided_by=ME)
        assert _body_without_ids(resolved) == _body_without_ids(word), mode
        parts = _parts(resolved)
        assert not any("data:image/" in parts[k] for k in ("body", "history", "proposals"))
        text = resolved.dumps()
        assert not _dangling(text)
        assert _codes(text, "error") == [] and resolved.verify() == []


class TestTrackedDocxImport:
    def test_propose_mode_packs_the_body_and_the_lane(self):
        result = aim.import_docx(_tracked_docx_with_images())
        doc = result.document
        assert doc.proposals and result.report.proposals
        payloads = "\n".join(p.payload_html or "" for p in doc.proposals)
        # the accept-only image is registered for the card that brings it
        assert f'<use href="#{_asset(PNG_B)}"/>' in payloads
        _assert_stored_once(doc, PNG_A, PNG_B)

    @pytest.mark.parametrize("resolve", ["accept_all", "reject_all"])
    def test_resolving_the_lane_leaves_no_reference_unresolved(self, resolve):
        doc = aim.loads(aim.import_docx(_tracked_docx_with_images()).document.dumps())
        getattr(doc, resolve)(decided_by=ME)
        text = doc.dumps()
        assert not _dangling(text)
        want = {_asset(PNG_A), _asset(PNG_B)} if resolve == "accept_all" else {_asset(PNG_A)}
        assert set(re.findall(r'<use href="#(asset-[^"]+)"', _parts(doc)["body"])) == want
        assert _codes(text, "error") == [] and doc.verify() == []

    @pytest.mark.parametrize(("mode", "blobs"), [("accept", (PNG_A, PNG_B)), ("reject", (PNG_A,))])
    def test_resolved_modes_pack_too(self, mode, blobs):
        result = aim.import_docx(_tracked_docx_with_images(), tracked=mode)
        assert not result.document.proposals
        _assert_stored_once(result.document, *blobs)

    def test_an_accept_only_image_is_stored_for_the_card_that_brings_it(self):
        # The accept view's image is stored before the lane is written, and
        # the card that inserts the figure carries it: the figure's derived
        # source key ("#fig") cites the revisions of its paragraph.
        data = _tracked_docx_with_an_inserted_figure()
        accepted = aim.import_docx(data, tracked="accept").document
        assert _asset(PNG_B) in _symbols(accepted.dumps())  # Word's Accept All has it
        doc = aim.import_docx(data).document
        (card,) = doc.proposals
        assert card.action == "add"
        assert f'<use href="#{_asset(PNG_B)}"/>' in (card.payload_html or "")
        text = doc.dumps()
        assert _symbols(text) == {_asset(PNG_A), _asset(PNG_B)}
        assert not _dangling(text)
        assert aim.loads(text).gc_assets() == 0  # every stored image is referenced
        assert _codes(text, "error") == [] and aim.loads(text).verify() == []

    @pytest.mark.parametrize("change", sorted(_PICTURE_ONLY))
    def test_a_picture_only_paragraph_gets_its_card(self, change):
        # The paragraph converts to a figure under a derived source key
        # ("p3#fig0") while its revisions name the paragraph ("p3"). Looked up
        # by the exact key they produced no card, and accept_all kept the
        # original picture where Word's Accept All has none or another.
        data = _tracked_picture_only_paragraph(change)
        result = aim.import_docx(data)
        doc = result.document
        assert len(doc.proposals) == 1
        assert all(note.cards for note in result.report.revisions)
        _assert_stored_once(doc, *_PICTURE_ONLY[change][0])
        _assert_resolves_like_word(doc, data)

    def test_a_picture_joined_into_an_empty_paragraph_gets_its_card(self):
        # Accept All joins the picture's paragraph ("p3") into the empty one
        # after it ("p4"), whose mark survives, and the result converts to a
        # figure under a key derived from the mark owner's ("p4#fig0"). The
        # key kept only the owner's token and lost "p3", which the revisions
        # name: no card, accept_all without the picture, the report saying
        # "no visible difference", and the image dropped by gc.
        data = _picture_joined_into_an_empty_paragraph()
        result = aim.import_docx(data)
        doc = result.document
        (card,) = doc.proposals
        assert card.action == "add"
        assert f'<use href="#{_asset(PNG_B)}"/>' in (card.payload_html or "")
        assert all(note.cards == (card.id,) for note in result.report.revisions)
        _assert_stored_once(doc, PNG_B)
        _assert_resolves_like_word(doc, data)

    def test_a_moved_picture_only_paragraph_is_one_move_card(self):
        # IMPORT-D3: a named Word move becomes a move card. The move marks
        # name the paragraphs ("p2", "p4") while their figures carry derived
        # keys ("p2#fig0", "p4#fig0"): looked up by the exact key, the move
        # came out as a delete and an add.
        data = _moved_picture_only_paragraph()
        result = aim.import_docx(data)
        doc = result.document
        (card,) = doc.proposals
        figure = next(c for c in doc.chunks if c.tag == "figure")
        assert (card.action, card.target) == ("move", figure.id)
        assert all(note.cards == (card.id,) for note in result.report.revisions)
        _assert_stored_once(doc, PNG_A)
        _assert_resolves_like_word(doc, data)

    @pytest.mark.parametrize(
        ("change", "action"),
        [("insert", "add"), ("delete", "delete"), ("insert-beside-text", "add")],
    )
    def test_a_vml_picture_gets_its_card(self, change, action):
        # A legacy VML picture is recovered after its paragraph as a figure
        # keyed "p2#pic", while its revisions name the paragraph ("p2").
        data = _tracked_vml_picture(change)
        result = aim.import_docx(data)
        doc = result.document
        assert [p.action for p in doc.proposals] == [action]
        assert all(note.cards for note in result.report.revisions)
        _assert_stored_once(doc, PNG_B)
        _assert_resolves_like_word(doc, data)

    @pytest.mark.parametrize("tag", ["ins", "del"], ids=["splits", "joins"])
    def test_a_picture_that_splits_or_joins_a_list_gets_its_cards(self, tag):
        # Inserted, the figure splits the list in Accept All: the items after
        # it leave the list for a new one, citing the figure's revisions.
        # Deleted, it joins the two lists. Both follow from the figure's
        # derived key citing its paragraph's revisions.
        data = _picture_between_list_items(tag)
        result = aim.import_docx(data)
        doc = result.document
        assert sorted(p.action for p in doc.proposals) == ["add", "add", "delete", "delete"]
        assert all(note.cards for note in result.report.revisions)
        _assert_stored_once(doc, PNG_B)
        _assert_resolves_like_word(doc, data)

    def test_an_accept_only_image_no_card_carries_is_not_stored(self, monkeypatch):
        # A difference with no revision behind it is converter noise: no card
        # carries it and the original markup stays. An image only the accept
        # view has is stored before the lane is written, so when no card ends
        # up carrying it, the import's gc pass must drop it. Here every
        # difference reads as noise: the lane cites no revision at all.
        from aimformat.convert import _docx_tracked

        monkeypatch.setattr(_docx_tracked._Planner, "cites", lambda self, *units: [])
        result = aim.import_docx(_tracked_docx_with_an_inserted_figure())
        assert not result.document.proposals
        text = result.document.dumps()
        assert _symbols(text) == {_asset(PNG_A)}  # not the image nothing references
        assert aim.loads(text).gc_assets() == 0
        assert not _dangling(text)
        assert _codes(text, "error") == [] and aim.loads(text).verify() == []


# -- the H008 fallback keeps packed adds (plan review finding 1) ----------------


def test_the_h008_fallback_keeps_packed_references_in_its_adds(monkeypatch):
    # An importer that emits a construct a snapshot cannot hold keeps its
    # per-construct adds. Those adds must already carry the packed form: a
    # pack done after them would leave the bytes in the history for good.
    pytest.importorskip("markdown_it")
    import aimformat.convert._markdown_in as markdown_in

    real = markdown_in._containerize
    monkeypatch.setattr(
        markdown_in,
        "_containerize",
        lambda markup: real(markup.replace("<p>Broken.</p>", '<p class="bogus">Broken.</p>')),
    )
    doc = aim.from_markdown(f"Logo: ![logo]({_uri(PNG_A)})\n\nBroken.\n")
    assert [e.action for e in doc.history] == ["add", "add"]
    after = doc.history[0].get("after")
    assert f'<use href="#{_asset(PNG_A)}"/>' in after and "data:image/" not in after
    parts = _parts(doc)
    assert "data:image/" not in parts["history"] and "data:image/" not in parts["body"]
    assert _symbols(parts["assets"]) == {_asset(PNG_A)}
    assert doc.verify() == []
    assert _codes(doc, "error") == ["V005"]  # the bogus class, left where an edit can fix it
    assert "S036" not in _codes(doc)


# -- gc counts baseline snapshots (P12) -------------------------------------------


def _baselined_with_packed_image() -> aim.AimDocument:
    doc = aim.new_document(title="t")
    doc.add_chunk(f'<p data-aim="logo">Logo: <img alt="logo" src="{_uri(PNG_A)}"></p>', author=ME)
    doc.add_chunk('<p data-aim="text">Text.</p>', author=ME)
    doc.pack_assets(author=ME)
    doc.baseline("origin")  # the snapshot holds the packed chunk; no event before it
    return doc


class TestGcCountsSnapshots:
    def test_an_asset_only_the_snapshot_references_survives_gc(self):
        text = _baselined_with_packed_image().dumps()
        edited = re.sub(r'<p data-aim="logo">.*?</p>\n', "", text)  # out-of-band delete
        assert edited != text
        hand = aim.loads(edited)
        assert hand.gc_assets() == 0
        assert _symbols(hand.dumps()) == {_asset(PNG_A)}

    def test_reconcile_after_gc_leaves_history_resolvable(self):
        text = _baselined_with_packed_image().dumps()
        hand = aim.loads(re.sub(r'<p data-aim="logo">.*?</p>\n', "", text))
        hand.gc_assets()  # what pack, prune or flatten would run first
        hand.reconcile(author=aim.external("editor"))
        after = hand.dumps()
        assert not _dangling(after)
        assert "S036" not in _codes(after)
        origin = hand.history[0]
        assert origin.kind == "baseline"
        past = hand.state_at(origin.seq)  # time travel to the baseline still shows the image
        assert f'<use href="#{_asset(PNG_A)}"/>' in past.chunk("logo").html
        assert _asset(PNG_A) in _symbols(past.dumps())


# -- lint: unresolved references and ids that are not their bytes' hash (P13) ---

MISSING = "asset-" + "0" * 12


def _ref(asset_id: str, label: str = "logo") -> str:
    return f'<svg aria-label="{label}" role="img"><use href="#{asset_id}"/></svg>'


def _s035(text: str) -> list[aim.Finding]:
    return [f for f in aim.lint_text(text) if f.code == "S036"]


class TestLintWarnings:
    def test_registered_as_warnings(self):
        rules = aim.REGISTRY.raw["lint_rules"]
        assert rules["S036"][0] == "warning" and rules["S037"][0] == "warning"

    def test_a_packed_document_is_clean(self):
        doc = _baselined_with_packed_image()
        assert not {"S036", "S037"} & set(_codes(doc))

    def test_S036_a_body_reference_to_nothing(self):
        doc = aim.new_document(title="t")
        doc.add_chunk(f'<p data-aim="p1">Logo: {_ref(MISSING)}</p>', author=ME)
        (finding,) = _s035(doc.dumps())
        assert finding.level == "warning" and finding.where == "p1" and MISSING in finding.message
        assert doc.verify() == []  # the bytes are outside doc_hash: only lint can tell

    def test_S036_a_pending_payload_reference_to_nothing(self):
        doc = aim.new_document(title="t")
        doc.add_chunk('<p data-aim="p1">Text.</p>', author=ME)
        card = doc.propose_add(f"<p>Logo: {_ref(MISSING)}</p>", author=BOT, after="p1")
        (finding,) = _s035(doc.dumps())
        assert finding.where == card.id

    def test_S036_a_snapshot_reference_to_nothing(self):
        doc = aim.new_document(title="t")
        doc.add_chunk(f'<p data-aim="p1">Logo: {_ref(MISSING)}</p>', author=ME)
        doc.add_chunk('<p data-aim="p2">Text.</p>', author=ME)
        origin = doc.baseline("origin")
        doc.delete_chunk("p1", author=ME)  # the body no longer references it; history does
        (finding,) = _s035(doc.dumps())
        assert finding.where == str(origin.seq) and "baseline snapshot" in finding.message

    def test_S036_an_out_of_band_registry_delete(self):
        text = _baselined_with_packed_image().dumps()
        stripped = re.sub(r"<aim-assets>.*?</aim-assets>\n", "", text, flags=re.S)
        assert _s035(stripped) and _codes(stripped, "error") == []

    def _registry_swapped(self, new_registry_payload: str) -> str:
        text = _baselined_with_packed_image().dumps()
        start, end = text.index("<aim-assets>"), text.index("</aim-assets>")
        registry = text[start:end].replace(base64.b64encode(PNG_A).decode(), new_registry_payload)
        return text[:start] + registry + text[end:]

    def test_S037_swapped_image_bytes(self):
        # a swapped blob keeps its old id: doc_hash and verify cannot see it
        text = self._registry_swapped(base64.b64encode(PNG_B).decode())
        findings = [f for f in aim.lint_text(text) if f.code == "S037"]
        assert [(f.level, f.where) for f in findings] == [("warning", _asset(PNG_A))]
        assert _asset(PNG_B) in findings[0].message
        assert aim.loads(text).verify() == []

    def test_S037_image_bytes_that_do_not_decode(self):
        text = self._registry_swapped("AAA")
        assert [f.where for f in aim.lint_text(text) if f.code == "S037"] == [_asset(PNG_A)]

    def test_S037_ignores_vector_symbols(self):
        # §9.2 hashes a vector symbol's canonical children; the importers never
        # write one, and this check covers the raster assets they do write
        text = _baselined_with_packed_image().dumps()
        vector = (
            '<symbol id="asset-aaaaaaaaaaaa" viewBox="0 0 2 2">'
            '<rect height="2" width="2"/></symbol>'
        )
        text = text.replace("</symbol>\n", "</symbol>\n" + vector + "\n", 1)
        assert "S037" not in _codes(text)


# -- the exporters resolve packed images (P15) ----------------------------------


def _packed_doc() -> aim.AimDocument:
    doc = aim.new_document(title="t")
    doc.add_chunk(
        f'<figure data-aim="fig"><img alt="Chart" style="width:96px" src="{_uri(PNG_A)}">'
        "<figcaption>The chart.</figcaption></figure>",
        author=ME,
        at=ts(0),
    )
    doc.add_chunk(
        f'<p data-aim="p1">Inline <img alt="dot" src="{_uri(PNG_B)}"> here.</p>',
        author=ME,
        at=ts(1),
    )
    doc.pack_assets(author=ME, at=ts(2))
    assert "data:image/" not in _parts(doc)["body"]
    return doc


class TestExportersResolvePackedImages:
    def test_to_docx_embeds_a_packed_figure(self, tmp_path):
        pytest.importorskip("docx_parser_converter")
        docx = pytest.importorskip("docx")
        out = aim.to_docx(_packed_doc(), tmp_path / "packed.docx")
        word = docx.Document(str(out))
        assert len(word.inline_shapes) == 1
        assert abs(word.inline_shapes[0].width - 914400) <= 9525  # 96px = 1in
        text = "\n".join(p.text for p in word.paragraphs)
        # an inline image exports as the placeholder an inline <img> gets
        assert "[image: dot]" in text and "The chart." in text
        again = aim.from_docx(out)
        assert _asset(PNG_A) in _symbols(again.dumps())  # the same bytes came back

    def test_to_docx_tracked_payload_names_a_packed_image(self, tmp_path):
        pytest.importorskip("docx")
        import zipfile

        doc = _packed_doc()
        doc.propose_delete("fig", author=BOT, at=ts(3))
        out = aim.to_docx(doc, tmp_path / "tracked.docx")
        xml = zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")
        assert "[image: Chart]" in xml  # the deleted figure is named, not blank

    def test_to_markdown_writes_packed_images_with_their_data(self):
        md = aim.to_markdown(_packed_doc())
        assert f"![Chart]({_uri(PNG_A)})" in md
        assert f"Inline ![dot]({_uri(PNG_B)}) here." in md

    def test_to_markdown_resolves_packed_images_in_pending_payloads(self):
        doc = _packed_doc()
        changed = doc.chunk("p1").html.replace("Inline", "Changed")
        doc.propose_modify("p1", changed, author=BOT, at=ts(3))
        md = aim.to_markdown(doc, pending="criticmarkup")
        assert f"Changed ![dot]({_uri(PNG_B)}) here." in md
        assert md.count(f"![dot]({_uri(PNG_B)})") == 2
