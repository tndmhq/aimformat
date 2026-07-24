"""The native DOCX importer (extra ``docx``): styling fidelity, the
rhythm-vs-local-intent doctrine, structure, pagination, theme derivation.

Fixtures are built with python-docx (a dev/test dependency), same pattern
as the pagination tests — no binary files in the repo.
"""

from __future__ import annotations

import io

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
        assert "<td>CellA</td>" in html
        assert "<td><strong>BoldCell</strong></td>" in html

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
