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
