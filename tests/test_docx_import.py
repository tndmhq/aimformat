"""The native DOCX importer (extra ``docx``): styling fidelity, the
rhythm-vs-local-intent doctrine, structure, pagination, theme derivation.

Fixtures are built with python-docx (a dev/test dependency), same pattern
as the pagination tests — no binary files in the repo.
"""

from __future__ import annotations

import io
import re

import pytest

import aimformat as aim

pytest.importorskip("docx_parser_converter")
docx = pytest.importorskip("docx")

from docx import Document  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_COLOR_INDEX  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.shared import Emu, Pt, RGBColor  # noqa: E402

from aimformat.convert import from_docx  # noqa: E402

# a valid 1×1 red PNG
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8cfc000000301010018dd8db00000000049"
    "454e44ae426082"
)


def _add_hyperlink(paragraph, url: str, text: str) -> None:
    r_id = paragraph.part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    r_pr = OxmlElement("w:rPr")
    r_style = OxmlElement("w:rStyle")
    r_style.set(qn("w:val"), "Hyperlink")
    r_pr.append(r_style)
    run.append(r_pr)
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _styled_docx() -> io.BytesIO:
    doc = Document()
    doc.add_heading("Heading One Alpha", level=1)
    doc.add_heading("Heading Two Bravo", level=2)

    p = doc.add_paragraph()
    r = p.add_run("GeorgiaRun ")
    r.font.name = "Georgia"
    r.font.size = Pt(18)
    r2 = p.add_run("CourierRun")
    r2.font.name = "Courier New"
    r2.font.size = Pt(9)

    p = doc.add_paragraph()
    p.add_run("BoldRun").bold = True
    p.add_run(" plain ")
    p.add_run("ItalicRun").italic = True
    p.add_run(" plain ")
    p.add_run("UnderlineRun").underline = True
    p.add_run(" plain ")
    p.add_run("StrikeRun").font.strike = True

    p = doc.add_paragraph()
    rc = p.add_run("RedRun")
    rc.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
    p.add_run(" and ")
    rh = p.add_run("HighlightRun")
    rh.font.highlight_color = WD_COLOR_INDEX.YELLOW
    p.add_run(" and E=mc")
    p.add_run("2").font.superscript = True

    pc = doc.add_paragraph("Centered text.")
    pc.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pj = doc.add_paragraph("Justified text that is long enough to wrap.")
    pj.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    for item in ("BulletOne", "BulletTwo"):
        doc.add_paragraph(item, style="List Bullet")
    for item in ("NumberOne", "NumberTwo"):
        doc.add_paragraph(item, style="List Number")

    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "CellA"
    table.cell(0, 1).text = "CellB"
    table.cell(1, 0).text = "CellC"
    cell = table.cell(1, 1)
    cell.text = ""
    bold_run = cell.paragraphs[0].add_run("BoldCell")
    bold_run.bold = True

    p = doc.add_paragraph("Visit ")
    _add_hyperlink(p, "https://example.com/linktarget", "LinkText")
    p.add_run(" now.")

    p = doc.add_paragraph()
    p.add_run().add_break(WD_BREAK.PAGE)
    doc.add_paragraph("SecondPage.")

    doc.add_picture(io.BytesIO(_PNG), width=Emu(914400))  # 1 inch

    out = io.BytesIO()
    doc.save(out)
    out.seek(0)
    return out


@pytest.fixture(scope="module")
def imported() -> aim.AimDocument:
    from aimformat.convert._docx_in import convert_docx

    return convert_docx(_styled_docx())


def test_public_entry_point_takes_a_path(tmp_path):
    target = tmp_path / "styled.docx"
    target.write_bytes(_styled_docx().read())
    doc = from_docx(target)
    assert doc.title == "Heading One Alpha"
    assert any(e.author.id == "docx-import" for e in doc.history if e.author)


@pytest.fixture(scope="module")
def html(imported) -> str:
    return "\n".join(c.html for c in imported.chunks)


class TestStyling:
    def test_fonts_and_sizes_become_literal_typography(self, html):
        assert '<span style="font-size:18pt; font-family:Georgia">' in html
        assert '<span style="font-size:9pt; font-family:Courier New">' in html

    def test_color_becomes_literal_paint(self, html):
        assert '<span style="color:#ff0000">RedRun</span>' in html

    def test_highlight_becomes_mark(self, html):
        assert "<mark>HighlightRun</mark>" in html

    def test_classic_marks(self, html):
        assert "<strong>BoldRun</strong>" in html
        assert "<em>ItalicRun</em>" in html
        assert "<u>UnderlineRun</u>" in html
        assert "<s>StrikeRun</s>" in html
        assert "<sup>2</sup>" in html

    def test_alignment_becomes_classes(self, imported):
        tags = {c.html.split(">", 1)[0] for c in imported.chunks}
        assert any('class="text-center"' in t for t in tags)
        assert any('class="text-justify"' in t for t in tags)

    def test_list_item_alignment_becomes_a_class(self):
        # a centered bullet is visible structure, same as a centered heading
        doc = Document()
        p = doc.add_paragraph("centered bullet", style="List Bullet")
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        doc.add_paragraph("plain bullet", style="List Bullet")
        out = io.BytesIO()
        doc.save(out)
        out.seek(0)
        body = convert_docx(out).dumps()
        assert '<li data-aim="' in body
        assert re.search(r'<li[^>]*class="text-center"[^>]*>centered bullet</li>', body), body[:400]
        assert re.search(r"<li[^>]*>plain bullet</li>", body)

    def test_caps_combines_with_other_run_styling(self):
        # all-caps + colour on one run: one span carrying BOTH the uppercase
        # class and the literal paint — neither silently dropped
        doc = Document()
        p = doc.add_paragraph()
        run = p.add_run("Shouted")
        run.font.all_caps = True
        run.font.color.rgb = RGBColor(0x1D, 0x4E, 0xD8)
        lone = p.add_run(" and just caps")
        lone.font.all_caps = True
        out = io.BytesIO()
        doc.save(out)
        out.seek(0)
        html = "\n".join(c.html for c in convert_docx(out).chunks)
        assert '<span class="uppercase" style="color:#1d4ed8">Shouted</span>' in html
        assert '<span class="uppercase"> and just caps</span>' in html

    def test_style_driven_bold_is_suppressed_on_headings(self, imported):
        h1 = next(c for c in imported.chunks if c.tag == "h1")
        assert h1.html == f'<h1 data-aim="{h1.id}">Heading One Alpha</h1>'

    def test_hyperlink_character_style_is_suppressed(self, html):
        assert '<a href="https://example.com/linktarget">LinkText</a>' in html


class TestStructure:
    def test_heading_levels(self, imported):
        assert [c.tag for c in imported.chunks[:2]] == ["h1", "h2"]

    def test_lists_split_by_numbering(self, imported):
        text = imported.dumps()
        assert "<ul data-aim-container=" in text and "<ol data-aim-container=" in text

    def test_table_with_cell_formatting(self, html):
        # cells may now carry a width style (Card B); assert content + the
        # bold-inside-a-cell survive, robust to any cell geometry
        assert ">CellA</td>" in html
        assert "<strong>BoldCell</strong></td>" in html

    def test_page_break_chunk(self, imported):
        assert any(c.tag == "aim-page-break" for c in imported.chunks)

    def test_image_embeds_as_data_uri(self, html):
        assert 'src="data:image/png;base64,' in html

    def test_page_setup_carried(self, imported):
        text = imported.dumps()
        assert '"size":"Letter"' in text.replace(" ", "") or '"size": "Letter"' in text

    def test_document_title_falls_back_to_first_heading(self, imported):
        assert imported.title == "Heading One Alpha"


class TestConformance:
    def test_lints_clean(self, imported):
        assert [f for f in aim.lint(imported) if f.level == "error"] == []

    def test_roundtrip_is_byte_stable(self, imported):
        text = imported.dumps()
        assert aim.loads(text).dumps() == text

    def test_history_verifies(self, imported):
        assert imported.verify() == []


class TestTheme:
    def test_theme_derives_from_the_source(self, imported):
        text = imported.dumps()
        assert "--aim-font-heading:" in text and "--aim-font-body:" in text
        assert "--aim-brand-1:#" in text

    def test_caller_theme_slots_win(self):
        from aimformat.convert._docx_in import convert_docx

        doc = convert_docx(_styled_docx(), theme={"--aim-font-body": "Test Face"})
        assert "--aim-font-body:Test Face" in doc.dumps()


def test_a_list_starting_indented_keeps_its_outdented_items():
    """ilvl 1 then ilvl 0: nesting starts at the group's minimum level, so
    the outdented item must survive (not be dropped by the walk return)."""
    doc = Document()
    for text, ilvl in (("DeepFirst", 1), ("ShallowSecond", 0)):
        p = doc.add_paragraph(text, style="List Bullet")
        num_pr = OxmlElement("w:numPr")
        lvl = OxmlElement("w:ilvl")
        lvl.set(qn("w:val"), str(ilvl))
        num_id = OxmlElement("w:numId")
        num_id.set(qn("w:val"), "1")
        num_pr.append(lvl)
        num_pr.append(num_id)
        p._p.get_or_add_pPr().append(num_pr)
    out = io.BytesIO()
    doc.save(out)
    out.seek(0)
    from aimformat.convert._docx_in import convert_docx

    imported = convert_docx(out)
    text = imported.dumps()
    assert "DeepFirst" in text and "ShallowSecond" in text
    assert [f for f in aim.lint(imported) if f.level == "error"] == []


# --------------------------------------------------------------------------
# Card A: edge-case ports (strict-OOXML, textboxes, OMML, checkboxes, symbols)
# --------------------------------------------------------------------------

from docx.oxml import parse_xml  # noqa: E402

from aimformat.convert._docx_in import convert_docx  # noqa: E402
from aimformat.convert._docx_seam import (  # noqa: E402
    _is_safe_zip_member,
    _is_strict_ooxml,
    _strict_ns_to_transitional,
    symbol_char,
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"


def _one_para_html(builder) -> str:
    doc = Document()
    builder(doc)
    out = io.BytesIO()
    doc.save(out)
    out.seek(0)
    imported = convert_docx(out)
    assert [f for f in aim.lint(imported) if f.level == "error"] == []
    return "\n".join(c.html for c in imported.chunks)


class TestSymbols:
    def test_wingdings_glyphs_map_to_unicode(self):
        def build(doc):
            p = doc.add_paragraph()
            for char in ("F0FC", "F0B7", "F0E0"):
                run = p.add_run()
                sym = OxmlElement("w:sym")
                sym.set(qn("w:font"), "Wingdings")
                sym.set(qn("w:char"), char)
                run._r.append(sym)

        assert "✓•→" in _one_para_html(build)

    def test_unmapped_wingdings_glyph_drops_never_leaks_the_hex(self):
        # F001 is not in the curated table: it must vanish, not print "F001"
        assert symbol_char("Wingdings", "F001") is None
        assert symbol_char("Wingdings", "F0FC") == "✓"

    def test_non_symbol_font_passes_real_characters_and_drops_pua(self):
        assert symbol_char("Calibri", "2022") == "•"  # real BMP char
        assert symbol_char("SomeFont", "F0FC") is None  # private-use, no table

    def test_a_bad_char_is_dropped(self):
        assert symbol_char("Wingdings", "nothex") is None
        assert symbol_char("Wingdings", None) is None


class TestEquations:
    def test_omml_survives_as_literal_text(self):
        def build(doc):
            p = doc.add_paragraph("Result ")
            p._p.append(parse_xml(f'<m:oMath xmlns:m="{_M}"><m:r><m:t>x=y+1</m:t></m:r></m:oMath>'))

        assert "Result x=y+1" in _one_para_html(build)


class TestCheckbox:
    def test_inline_content_control_checkbox_becomes_a_glyph(self):
        def build(doc):
            p = doc.add_paragraph()
            p._p.append(
                parse_xml(
                    f'<w:sdt xmlns:w="{_W}" xmlns:w14="{_W14}"><w:sdtPr>'
                    '<w14:checkbox><w14:checked w14:val="1"/></w14:checkbox>'
                    "</w:sdtPr><w:sdtContent><w:r><w:t>x</w:t></w:r></w:sdtContent></w:sdt>"
                )
            )

        assert "☑" in _one_para_html(build)

    def test_unchecked_checkbox_is_the_empty_box(self):
        def build(doc):
            p = doc.add_paragraph("Task ")
            p._p.append(
                parse_xml(
                    f'<w:sdt xmlns:w="{_W}" xmlns:w14="{_W14}"><w:sdtPr>'
                    '<w14:checkbox><w14:checked w14:val="0"/></w14:checkbox>'
                    "</w:sdtPr><w:sdtContent><w:r><w:t>x</w:t></w:r></w:sdtContent></w:sdt>"
                )
            )

        html = _one_para_html(build)
        assert "☐" in html and "Task" in html


class TestTextbox:
    def test_textbox_paragraph_follows_its_anchor(self):
        def build(doc):
            doc.add_paragraph("Before")
            anchor = doc.add_paragraph("Anchor")
            anchor._p.append(
                parse_xml(
                    f'<w:txbxContent xmlns:w="{_W}"><w:p><w:r>'
                    "<w:t>TextboxLine</w:t></w:r></w:p></w:txbxContent>"
                )
            )
            doc.add_paragraph("After")

        doc = Document()
        build(doc)
        out = io.BytesIO()
        doc.save(out)
        out.seek(0)
        imported = convert_docx(out)
        texts = [c.text for c in imported.chunks]
        # the textbox line sits between its anchor and the following paragraph
        assert texts == ["Before", "Anchor", "TextboxLine", "After"]

    def test_alternate_content_textbox_emits_once(self):
        # Word wraps every inserted shape in mc:AlternateContent with the SAME
        # w:txbxContent in both the DrawingML Choice and the VML Fallback —
        # MCE says read exactly one branch, so the text must appear once.
        mc = "http://schemas.openxmlformats.org/markup-compatibility/2006"
        wps = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
        doc = Document()
        anchor = doc.add_paragraph("Anchor")
        run = parse_xml(f'<w:r xmlns:w="{_W}"/>')
        run.append(
            parse_xml(
                f'<mc:AlternateContent xmlns:mc="{mc}" xmlns:w="{_W}" '
                f'xmlns:wps="{wps}" xmlns:v="urn:schemas-microsoft-com:vml">'
                '<mc:Choice Requires="wps"><wps:wsp><wps:txbx>'
                "<w:txbxContent><w:p><w:r><w:t>BoxLine</w:t></w:r></w:p>"
                "</w:txbxContent></wps:txbx></wps:wsp></mc:Choice>"
                "<mc:Fallback><v:shape><v:textbox>"
                "<w:txbxContent><w:p><w:r><w:t>BoxLine</w:t></w:r></w:p>"
                "</w:txbxContent></v:textbox></v:shape></mc:Fallback>"
                "</mc:AlternateContent>"
            )
        )
        anchor._p.append(run)
        out = io.BytesIO()
        doc.save(out)
        out.seek(0)
        imported = convert_docx(out)
        texts = [c.text for c in imported.chunks]
        assert texts == ["Anchor", "BoxLine"]


class TestArchiveGuards:
    """Zip-slip / zip-bomb rejection on EVERY input, not only Strict OOXML."""

    @staticmethod
    def _plain_docx() -> io.BytesIO:
        doc = Document()
        doc.add_paragraph("ok")
        out = io.BytesIO()
        doc.save(out)
        out.seek(0)
        return out

    def test_zip_slip_member_rejected(self):
        import zipfile

        src = zipfile.ZipFile(self._plain_docx())
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for info in src.infolist():
                z.writestr(info, src.read(info.filename))
            z.writestr("../evil.txt", b"x")
        out.seek(0)
        with pytest.raises(ValueError, match="zip-slip"):
            convert_docx(out)

    def test_oversized_member_rejected(self, monkeypatch):
        from aimformat.convert import _docx_seam

        monkeypatch.setattr(_docx_seam, "_MAX_MEMBER_BYTES", 64)
        with pytest.raises(ValueError, match="oversized"):
            convert_docx(self._plain_docx())

    def test_total_size_cap_rejected(self, monkeypatch):
        from aimformat.convert import _docx_seam

        monkeypatch.setattr(_docx_seam, "_MAX_TOTAL_BYTES", 256)
        with pytest.raises(ValueError, match="size limit"):
            convert_docx(self._plain_docx())


class TestStrictOoxml:
    @staticmethod
    def _to_strict(transitional: io.BytesIO) -> io.BytesIO:
        import zipfile

        repl = [
            (f"{_W}", "http://purl.oclc.org/ooxml/wordprocessingml/main"),
            (
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
                "http://purl.oclc.org/ooxml/officeDocument/relationships",
            ),
        ]
        src = zipfile.ZipFile(transitional)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for info in src.infolist():
                data = src.read(info.filename)
                if info.filename.endswith((".xml", ".rels")):
                    text = data.decode()
                    for a, b in repl:
                        text = text.replace(a, b)
                    data = text.encode()
                z.writestr(info, data)
        out.seek(0)
        return out

    def test_strict_package_parses_after_normalization(self):
        doc = Document()
        doc.add_heading("StrictTitle", level=1)
        doc.add_paragraph("Strict body text.")
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        strict = self._to_strict(buf)

        import zipfile

        assert _is_strict_ooxml(zipfile.ZipFile(io.BytesIO(strict.getvalue())))
        imported = convert_docx(io.BytesIO(strict.getvalue()))
        text = imported.dumps()
        assert "StrictTitle" in text and "Strict body text." in text
        assert [f for f in aim.lint(imported) if f.level == "error"] == []

    def test_namespace_mapping_reverses_transitional(self):
        assert _strict_ns_to_transitional("http://purl.oclc.org/ooxml/wordprocessingml/main") == _W

    def test_zip_slip_members_are_rejected(self):
        assert not _is_safe_zip_member("../evil.xml")
        assert not _is_safe_zip_member("/etc/passwd")
        assert not _is_safe_zip_member("C:/windows")
        assert _is_safe_zip_member("word/document.xml")


# --------------------------------------------------------------------------
# Card B: table styling (cell shading + width; borders deliberately skipped)
# --------------------------------------------------------------------------


class TestTableStyling:
    @staticmethod
    def _shaded_table_html() -> str:
        doc = Document()
        table = doc.add_table(rows=2, cols=2)
        cell = table.cell(0, 0)
        cell.text = "Shaded"
        pr = cell._tc.get_or_add_tcPr()
        pr.append(parse_xml(f'<w:shd xmlns:w="{_W}" w:val="clear" w:fill="D9E2F3"/>'))
        pr.append(parse_xml(f'<w:tcW xmlns:w="{_W}" w:w="3000" w:type="dxa"/>'))
        table.cell(0, 1).text = "Plain"
        table.cell(1, 0).text = "A"
        table.cell(1, 1).text = "B"
        out = io.BytesIO()
        doc.save(out)
        out.seek(0)
        imported = convert_docx(out)
        assert [f for f in aim.lint(imported) if f.level == "error"] == []
        return "\n".join(c.html for c in imported.chunks)

    def test_cell_shading_becomes_background_paint(self):
        assert "background-color:#d9e2f3" in self._shaded_table_html()

    def test_cell_width_becomes_px_geometry(self):
        # python-docx redistributes the table width across cells, so the exact
        # px is its call; the conversion itself is pinned by the unit test.
        assert re.search(r"width:\d+px", self._shaded_table_html())

    def test_width_precedes_background_in_canonical_order(self):
        html = self._shaded_table_html()
        assert re.search(r'style="width:\d+px; background-color:#d9e2f3"', html)

    def test_dxa_conversion_and_non_dxa_skip(self):
        from aimformat.convert._docx_in import _cell_width_px

        assert _cell_width_px({"type": "dxa", "w": 1500}) == 100  # 1500 / 15
        assert _cell_width_px({"type": "dxa", "w": 3000}) == 200
        assert _cell_width_px({"type": "pct", "w": 5000}) is None
        assert _cell_width_px({"type": "auto"}) is None
        assert _cell_width_px(None) is None


# --------------------------------------------------------------------------
# Card C: to_docx export symmetry (DOCX → aim → DOCX round-trip idempotency)
# --------------------------------------------------------------------------


class TestImageParagraphs:
    def test_standalone_image_becomes_a_figure(self):
        # the system idiom is <figure> (from_docling, the editor's atomic
        # nodes, to_docx's figure exporter) — a paragraph that is only an
        # image must not stay a bare <p><img></p>
        from PIL import Image as PILImage

        doc = Document()
        img = io.BytesIO()
        PILImage.new("RGB", (12, 12), (10, 120, 40)).save(img, "PNG")
        img.seek(0)
        doc.add_picture(img)
        out = io.BytesIO()
        doc.save(out)
        out.seek(0)
        body = convert_docx(out).dumps()
        assert re.search(r"<figure[^>]*><img[^>]*data:image/png[^>]*></figure>", body), body[:400]

    def test_figure_roundtrips_through_export(self, tmp_path):
        from PIL import Image as PILImage

        doc = Document()
        img = io.BytesIO()
        PILImage.new("RGB", (12, 12), (10, 120, 40)).save(img, "PNG")
        img.seek(0)
        doc.add_picture(img)
        out = io.BytesIO()
        doc.save(out)
        out.seek(0)
        a = convert_docx(out)
        path = tmp_path / "img.docx"
        aim.to_docx(a, str(path))
        assert "data:image/" in convert_docx(str(path)).dumps(), "image lost on export"


class TestMergedCells:
    def test_vertical_merge_becomes_rowspan_alongside_gridspan(self):
        # python-docx merge(): row 0 = [gridSpan-2 "wide", vMerge-restart
        # "tall"], row 1 = [x, y, vMerge-continue]. The restart cell must
        # survive with rowspan=2 (dpc models w:vMerge as a plain string —
        # reading .val off it silently dropped the whole merged column).
        doc = Document()
        t = doc.add_table(rows=2, cols=3)
        t.cell(0, 0).merge(t.cell(0, 1))
        t.cell(0, 2).merge(t.cell(1, 2))
        t.cell(0, 0).text = "wide"
        t.cell(0, 2).text = "tall"
        t.cell(1, 0).text = "x"
        t.cell(1, 1).text = "y"
        out = io.BytesIO()
        doc.save(out)
        out.seek(0)
        dumped = convert_docx(out).dumps()
        table = re.search(r"<table.*?</table>", dumped, re.S)
        assert table, dumped[:600]
        html = table.group(0)
        assert 'colspan="2"' in html and ">wide<" in html
        assert 'rowspan="2"' in html and ">tall<" in html
        # the continuation slot collapses into the restart cell
        assert html.count("<td") + html.count("<th") == 4


class TestExportSymmetry:
    def _roundtrip(self, tmp_path):
        aim_doc = convert_docx(_styled_docx())
        out = tmp_path / "roundtrip.docx"
        aim.to_docx(aim_doc, str(out))
        return Document(str(out))

    def test_font_size_and_family_survive(self, tmp_path):
        d = self._roundtrip(tmp_path)
        faces = {
            (r.font.name, r.font.size.pt if r.font.size else None)
            for p in d.paragraphs
            for r in p.runs
            if r.text.strip()
        }
        assert ("Georgia", 18.0) in faces
        assert ("Courier New", 9.0) in faces

    def test_alignment_survives(self, tmp_path):
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        got = {p.alignment for p in self._roundtrip(tmp_path).paragraphs if p.alignment is not None}
        assert WD_ALIGN_PARAGRAPH.CENTER in got
        assert WD_ALIGN_PARAGRAPH.JUSTIFY in got

    def test_theme_fonts_reach_the_styles(self, tmp_path):
        d = self._roundtrip(tmp_path)
        # the source theme (Cambria body / Calibri headings) rides the styles
        assert d.styles["Normal"].font.name == "Cambria"
        assert d.styles["Heading 1"].font.name == "Calibri"

    def test_type_scale_class_exports_to_points(self, tmp_path):
        # text-2xl resolves through the normative pt table to 18pt
        doc = aim.new_document(title="Scale")
        doc.add_chunk('<p class="text-2xl">Big</p>', author=aim.external("t"))
        out = tmp_path / "scale.docx"
        aim.to_docx(doc, str(out))
        d = Document(str(out))
        sizes = [
            r.font.size.pt for p in d.paragraphs for r in p.runs if r.text == "Big" and r.font.size
        ]
        assert sizes == [18.0]

    def test_list_item_alignment_and_size_survive_export(self, tmp_path):
        # the synthetic li the exporter rebuilds must keep class/style — a
        # centered, sized list item exports with alignment and run size
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        doc = aim.new_document(title="Li")
        doc.add_chunk(
            '<ul><li class="text-center" style="font-size:14pt">Item</li></ul>',
            author=aim.external("t"),
        )
        out = tmp_path / "li.docx"
        aim.to_docx(doc, str(out))
        d = Document(str(out))
        para = next(p for p in d.paragraphs if "Item" in p.text)
        assert para.alignment == WD_ALIGN_PARAGRAPH.CENTER
        assert [r.font.size.pt for r in para.runs if r.text == "Item"] == [14.0]

    def test_uppercase_class_exports_as_all_caps(self, tmp_path):
        doc = aim.new_document(title="Caps")
        doc.add_chunk('<p><span class="uppercase">shout</span></p>', author=aim.external("t"))
        out = tmp_path / "caps.docx"
        aim.to_docx(doc, str(out))
        d = Document(str(out))
        flags = [r.font.all_caps for p in d.paragraphs for r in p.runs if r.text == "shout"]
        assert flags == [True]

    def test_font_stack_exports_its_first_family(self, tmp_path):
        # the inline grammar allows a stack; Word run props name one face
        doc = aim.new_document(title="Stack")
        doc.add_chunk(
            "<p><span style=\"font-family:'Segoe UI', Arial, sans-serif\">S</span></p>",
            author=aim.external("t"),
        )
        out = tmp_path / "stack.docx"
        aim.to_docx(doc, str(out))
        d = Document(str(out))
        names = [r.font.name for p in d.paragraphs for r in p.runs if r.text == "S"]
        assert names == ["Segoe UI"]

    def test_inline_typography_beats_the_class_on_the_same_run(self, tmp_path):
        # inline font-size wins over a type-scale class (CSS specificity)
        doc = aim.new_document(title="Override")
        doc.add_chunk(
            '<p><span class="text-2xl" style="font-size:30pt">X</span></p>',
            author=aim.external("t"),
        )
        out = tmp_path / "override.docx"
        aim.to_docx(doc, str(out))
        d = Document(str(out))
        sizes = [
            r.font.size.pt for p in d.paragraphs for r in p.runs if r.text == "X" and r.font.size
        ]
        assert sizes == [30.0]
