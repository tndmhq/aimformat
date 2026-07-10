"""The document loop: DoclingDocument → .aim → DOCX.

docling-core builds faithful fixture documents (the same dict shape the full
docling converter emits); python-docx reads back what the exporter wrote.
Both are dev/test dependencies only — the package itself stays stdlib-only.
"""

import pytest

import aimformat as aim
from aimformat.errors import InvalidOperation
from conftest import BOT, ME, ts

docling_core = pytest.importorskip("docling_core")
docx = pytest.importorskip("docx")

from docling_core.types.doc import GroupLabel, TableCell, TableData  # noqa: E402
from docling_core.types.doc.document import DoclingDocument  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402


def build_docling_fixture() -> DoclingDocument:
    d = DoclingDocument(name="pilot-report")
    d.add_title(text="Pilot report")
    d.add_text(label="text", text="We ran the pilot for six weeks & learned a lot.")
    d.add_heading(text="What worked", level=1)
    lst = d.add_group(label=GroupLabel.LIST)
    d.add_list_item(text="Review speed doubled", parent=lst)
    d.add_list_item(text="Fewer copy-paste errors", parent=lst)
    olst = d.add_group(label=GroupLabel.ORDERED_LIST)
    d.add_list_item(text="Roll out to briefs", parent=olst, enumerated=True)
    d.add_list_item(text="Then contracts", parent=olst, enumerated=True)
    d.add_heading(text="Numbers", level=2)
    td = TableData(
        num_rows=2,
        num_cols=2,
        table_cells=[
            TableCell(
                text="Metric",
                start_row_offset_idx=0,
                end_row_offset_idx=1,
                start_col_offset_idx=0,
                end_col_offset_idx=1,
                column_header=True,
            ),
            TableCell(
                text="Value",
                start_row_offset_idx=0,
                end_row_offset_idx=1,
                start_col_offset_idx=1,
                end_col_offset_idx=2,
                column_header=True,
            ),
            TableCell(
                text="Accepted edits",
                start_row_offset_idx=1,
                end_row_offset_idx=2,
                start_col_offset_idx=0,
                end_col_offset_idx=1,
            ),
            TableCell(
                text="38%",
                start_row_offset_idx=1,
                end_row_offset_idx=2,
                start_col_offset_idx=1,
                end_col_offset_idx=2,
            ),
        ],
    )
    d.add_table(data=td)
    d.add_text(label="code", text="aim lint report.aim")
    return d


@pytest.fixture
def ingested() -> aim.AimDocument:
    return aim.from_docling(build_docling_fixture())


class TestIngest:
    def test_accepts_object_and_dict_equivalently(self):
        src = build_docling_fixture()
        a = aim.from_docling(src)
        b = aim.from_docling(src.export_to_dict())
        assert [c.html.replace(c.id, "X") for c in a.chunks] == [
            c.html.replace(c.id, "X") for c in b.chunks
        ]

    def test_rejects_wrong_type(self):
        with pytest.raises(TypeError):
            aim.from_docling(42)

    def test_title_becomes_h1_and_doc_title(self, ingested):
        assert ingested.title == "Pilot report"
        assert ingested.chunks[0].tag == "h1"
        assert ingested.chunks[0].text == "Pilot report"

    def test_heading_levels_shift_below_title(self, ingested):
        tags = [c.tag for c in ingested.chunks]
        assert "h2" in tags and "h3" in tags  # docling levels 1,2 -> h2,h3

    def test_entities_escaped(self, ingested):
        para = next(c for c in ingested.chunks if c.tag == "p")
        assert "&amp;" in para.html and "&" in para.text

    def test_lists_become_containers_with_item_chunks(self, ingested):
        assert len(ingested.containers) == 3  # ul, ol, table
        lis = [c for c in ingested.chunks if c.tag == "li"]
        assert len(lis) == 4
        assert all(c.container in ingested.containers for c in lis)

    def test_ordered_list_is_ol(self, ingested):
        text = ingested.dumps()
        assert "<ol data-aim-container=" in text
        assert "<ul data-aim-container=" in text

    def test_table_grid_with_header(self, ingested):
        rows = [c for c in ingested.chunks if c.tag == "tr"]
        assert len(rows) == 2
        text = ingested.dumps()
        assert "<thead><tr" in text and "<th>Metric</th>" in text
        assert "<td>38%</td>" in text

    def test_code_becomes_pre(self, ingested):
        assert any(c.tag == "pre" for c in ingested.chunks)

    def test_ingestion_is_recorded_history(self, ingested):
        events = ingested.history
        assert events and all(e.action == "add" for e in events)
        assert all(e.author.type == "external" for e in events)
        assert len({e.batch for e in events}) == 1  # one ingestion batch

    def test_ingested_doc_lints_clean_and_verifies(self, ingested):
        assert ingested.verify() == []
        assert not [f for f in aim.lint_text(ingested.dumps()) if f.level == "error"]

    def test_picture_with_caption(self):
        d = DoclingDocument(name="pics")
        pic = d.add_picture()
        d.add_text(label="caption", text="Figure 1: the setup", parent=pic)
        obj = d.export_to_dict()
        # wire the caption ref the way docling emits it
        obj["pictures"][0]["captions"] = [{"$ref": obj["texts"][0]["self_ref"]}]
        doc = aim.from_docling(obj)
        fig = next(c for c in doc.chunks if c.tag == "figure")
        assert "figcaption" in fig.html and "Figure 1" in fig.text

    def test_colspan_survives(self):
        d = DoclingDocument(name="spans")
        td = TableData(
            num_rows=2,
            num_cols=2,
            table_cells=[
                TableCell(
                    text="Wide",
                    start_row_offset_idx=0,
                    end_row_offset_idx=1,
                    start_col_offset_idx=0,
                    end_col_offset_idx=2,
                    col_span=2,
                    column_header=True,
                ),
                TableCell(
                    text="a",
                    start_row_offset_idx=1,
                    end_row_offset_idx=2,
                    start_col_offset_idx=0,
                    end_col_offset_idx=1,
                ),
                TableCell(
                    text="b",
                    start_row_offset_idx=1,
                    end_row_offset_idx=2,
                    start_col_offset_idx=1,
                    end_col_offset_idx=2,
                ),
            ],
        )
        d.add_table(data=td)
        doc = aim.from_docling(d)
        assert 'colspan="2"' in doc.dumps()

    def test_furniture_skipped(self):
        d = DoclingDocument(name="f")
        from docling_core.types.doc.document import ContentLayer

        d.add_text(label="text", text="page header", content_layer=ContentLayer.FURNITURE)
        d.add_text(label="text", text="real content")
        doc = aim.from_docling(d)
        texts = [c.text for c in doc.chunks]
        assert texts == ["real content"]


def _docx_paragraphs(path):
    return [(p.style.name, p.text) for p in docx.Document(str(path)).paragraphs]


def _revision_authors(path, tag):
    d = docx.Document(str(path))
    return [el.get(qn("w:author")) for el in d.element.body.iter(qn(f"w:{tag}"))]


class TestExportDocx:
    def test_structure_maps_to_word_styles(self, ingested, tmp_path):
        out = aim.to_docx(ingested, tmp_path / "out.docx")
        paras = _docx_paragraphs(out)
        assert ("Heading 1", "Pilot report") in paras
        assert ("Heading 2", "What worked") in paras
        assert ("List Bullet", "Review speed doubled") in paras
        assert ("List Number", "Roll out to briefs") in paras

    def test_table_content_and_header_bold(self, ingested, tmp_path):
        out = aim.to_docx(ingested, tmp_path / "t.docx")
        tables = docx.Document(str(out)).tables
        assert len(tables) == 1
        assert tables[0].cell(0, 0).text == "Metric"
        assert tables[0].cell(1, 1).text == "38%"
        head_run = tables[0].cell(0, 0).paragraphs[0].runs[0]
        assert head_run.bold

    def test_inline_marks(self, tmp_path):
        doc = aim.new_document(title="T")
        doc.add_chunk(
            '<p data-aim="p1">plain <strong>bold</strong> <em>ital</em> <code>mono</code></p>',
            author=BOT,
            at=ts(0),
        )
        out = aim.to_docx(doc, tmp_path / "m.docx")
        para = docx.Document(str(out)).paragraphs[0]
        runs = {r.text: r for r in para.runs}
        assert runs["bold"].bold and runs["ital"].italic
        assert runs["mono"].font.name == "Consolas"

    def test_pending_tracked_emits_ins_and_del(self, tmp_path):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="p1">The old wording.</p>', author=ME, at=ts(0))
        doc.propose_modify(
            "p1",
            '<p data-aim="p1">The new wording.</p>',
            author=BOT,
            explanation="better",
            at=ts(1),
        )
        out = aim.to_docx(doc, tmp_path / "tracked.docx", pending="tracked")
        ins, dele = _revision_authors(out, "ins"), _revision_authors(out, "del")
        assert ins and dele
        assert ins[0].startswith("agent:") and dele[0].startswith("agent:")
        # original document untouched
        assert doc.proposals and doc.chunk("p1").text == "The old wording."

    def test_pending_add_tracked_as_inserted_paragraph(self, tmp_path):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="p1">Anchor.</p>', author=ME, at=ts(0))
        doc.propose_add("<p>Brand new paragraph.</p>", author=BOT, after="p1", at=ts(1))
        out = aim.to_docx(doc, tmp_path / "add.docx")
        d = docx.Document(str(out))
        # runs inside w:ins are invisible to Paragraph.text — check the XML
        xml = d.element.body.xml
        assert "Brand new paragraph." in xml
        assert _revision_authors(out, "ins")

    def test_pending_delete_tracked(self, tmp_path):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="p1">Doomed.</p>', author=ME, at=ts(0))
        doc.propose_delete("p1", author=BOT, at=ts(1))
        out = aim.to_docx(doc, tmp_path / "del.docx")
        assert _revision_authors(out, "del")

    def test_accept_all_resolves_on_copy(self, tmp_path):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="p1">Old.</p>', author=ME, at=ts(0))
        doc.propose_modify("p1", '<p data-aim="p1">New.</p>', author=BOT, at=ts(1))
        out = aim.to_docx(doc, tmp_path / "acc.docx", pending="accept-all")
        texts = [p.text for p in docx.Document(str(out)).paragraphs]
        assert "New." in texts and "Old." not in texts
        assert doc.proposals  # original untouched

    def test_reject_all(self, tmp_path):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="p1">Old.</p>', author=ME, at=ts(0))
        doc.propose_modify("p1", '<p data-aim="p1">New.</p>', author=BOT, at=ts(1))
        out = aim.to_docx(doc, tmp_path / "rej.docx", pending="reject-all")
        texts = [p.text for p in docx.Document(str(out)).paragraphs]
        assert "Old." in texts and "New." not in texts

    def test_accept_all_resolves_chained_adds_in_order(self, tmp_path):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="p1">Anchor.</p>', author=ME, at=ts(0))
        p1 = doc.propose_add('<p data-aim="n1">One.</p>', author=BOT, after="p1", at=ts(1))
        doc.propose_add('<p data-aim="n2">Two.</p>', author=BOT, after=p1.id, at=ts(2))
        out = aim.to_docx(doc, tmp_path / "chain.docx", pending="accept-all")
        texts = [p.text for p in docx.Document(str(out)).paragraphs]
        assert texts.index("One.") < texts.index("Two.")

    def test_row_modify_tracked_in_cells(self, rich_doc, tmp_path):
        rich_doc.propose_modify(
            "row1", '<tr data-aim="row1"><td>alpha</td><td>99</td></tr>', author=BOT, at=ts(20)
        )
        out = aim.to_docx(rich_doc, tmp_path / "row.docx")
        assert _revision_authors(out, "ins") and _revision_authors(out, "del")

    def test_invalid_pending_mode(self, ingested, tmp_path):
        with pytest.raises(InvalidOperation):
            aim.to_docx(ingested, tmp_path / "x.docx", pending="merge")

    def test_full_loop_docling_to_aim_to_docx(self, ingested, tmp_path):
        p = ingested.propose_modify(
            next(c.id for c in ingested.chunks if c.tag == "p"),
            "<p>We ran the pilot for six weeks and learned plenty.</p>",
            author=BOT,
            explanation="Tighter.",
            at=ts(30),
        )
        ingested.accept(p.id, decided_by=ME, at=ts(31))
        assert ingested.verify() == []
        out = aim.to_docx(ingested, tmp_path / "loop.docx", pending="accept-all")
        text = "\n".join(p.text for p in docx.Document(str(out)).paragraphs)
        assert "learned plenty" in text
        assert "Pilot report" in text

    def test_figure_caption_and_hr(self, tmp_path):
        doc = aim.new_document(title="T")
        doc.add_chunk(
            '<figure data-aim="f"><img alt="chart" '
            'src="https://example.org/x.png">'
            "<figcaption>The chart.</figcaption></figure>",
            author=BOT,
            at=ts(0),
        )
        doc.add_chunk('<hr data-aim="rule">', author=BOT, at=ts(1))
        out = aim.to_docx(doc, tmp_path / "fig.docx")
        text = "\n".join(p.text for p in docx.Document(str(out)).paragraphs)
        assert "[image: chart]" in text and "The chart." in text


_PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
    "AAAABQABh6FO1AAAAABJRU5ErkJggg=="
)


def _deck(with_flow=True):
    doc = aim.new_document(title="Deck")
    if with_flow:
        doc.add_chunk('<p data-aim="intro">Before the pages.</p>', author=ME, at=ts(0))
    doc.add_chunk(
        '<aim-slide data-aim-container="s1" style="width:420px; height:595px">'
        '<h2 data-aim="t1" style="left:10px; top:10px; width:300px">Page one</h2>'
        '<p data-aim="b1" style="left:10px; top:60px; width:300px">First body.</p>'
        "</aim-slide>",
        author=ME,
        at=ts(1),
    )
    doc.add_chunk(
        '<aim-slide data-aim-container="s2" style="width:420px; height:595px">'
        '<p data-aim="b2" style="left:10px; top:10px; width:300px">Second body.</p>'
        "</aim-slide>",
        author=ME,
        at=ts(2),
    )
    return doc


class TestExportDocxSlides:
    def test_slides_linearize_instead_of_dropping(self, tmp_path):
        out = aim.to_docx(_deck(), tmp_path / "deck.docx")
        paras = _docx_paragraphs(out)
        assert ("Heading 2", "Page one") in paras
        texts = [t for _, t in paras]
        assert "First body." in texts and "Second body." in texts
        # reading order preserved across the linearization
        assert texts.index("Page one") < texts.index("First body.") < texts.index("Second body.")

    def test_page_breaks_frame_slides(self, tmp_path):
        out = aim.to_docx(_deck(), tmp_path / "deck.docx")
        d = docx.Document(str(out))
        breaks = d.element.body.findall(".//" + qn("w:br") + "[@" + qn("w:type") + "='page']")
        # one before each slide (after the intro / between slides), none trailing
        assert len(breaks) == 2

    def test_leading_slide_gets_no_blank_first_page(self, tmp_path):
        out = aim.to_docx(_deck(with_flow=False), tmp_path / "deck.docx")
        d = docx.Document(str(out))
        breaks = d.element.body.findall(".//" + qn("w:br") + "[@" + qn("w:type") + "='page']")
        assert len(breaks) == 1  # only between the two slides

    def test_flow_after_slide_starts_on_fresh_page(self, tmp_path):
        doc = _deck(with_flow=False)
        doc.add_chunk('<p data-aim="after">Back to flow.</p>', author=ME, at=ts(3))
        out = aim.to_docx(doc, tmp_path / "deck.docx")
        d = docx.Document(str(out))
        breaks = d.element.body.findall(".//" + qn("w:br") + "[@" + qn("w:type") + "='page']")
        assert len(breaks) == 2  # s1→s2 and s2→flow

    def test_in_slide_pending_modify_rides_tracked_changes(self, tmp_path):
        doc = _deck()
        doc.propose_modify(
            "b1",
            '<p data-aim="b1" style="left:10px; top:60px; width:300px">Sharper body.</p>',
            author=BOT,
            at=ts(3),
        )
        out = aim.to_docx(doc, tmp_path / "deck.docx", pending="tracked")
        assert _revision_authors(out, "ins") and _revision_authors(out, "del")
        xml = docx.Document(str(out)).element.body.xml
        assert "Sharper body." in xml and "First body." in xml

    def test_in_slide_pending_add_emits_in_place(self, tmp_path):
        doc = _deck()
        doc.propose_add(
            '<p style="left:10px; top:110px; width:300px">Added into the page.</p>',
            container="s1",
            after="b1",
            author=BOT,
            at=ts(3),
        )
        out = aim.to_docx(doc, tmp_path / "deck.docx", pending="tracked")
        xml = docx.Document(str(out)).element.body.xml
        assert "Added into the page." in xml
        texts = [t for _, t in _docx_paragraphs(out)]
        joined = "\n".join(xml.split("Second body.")[0:1])
        assert "Added into the page." in joined  # lands inside s1, not at the end
        assert _revision_authors(out, "ins")
        assert texts  # document is non-empty for the plain reader too

    def test_accept_all_deck_exports_clean(self, tmp_path):
        doc = _deck()
        doc.propose_modify(
            "b2",
            '<p data-aim="b2" style="left:10px; top:10px; width:300px">Final body.</p>',
            author=BOT,
            at=ts(3),
        )
        out = aim.to_docx(doc, tmp_path / "deck.docx", pending="accept-all")
        texts = [t for _, t in _docx_paragraphs(out)]
        assert "Final body." in texts and "Second body." not in texts


class TestExportDocxFigureWidth:
    def _fig_doc(self, style: str | None):
        doc = aim.new_document(title="T")
        s = f' style="{style}"' if style else ""
        doc.add_chunk(
            f'<figure data-aim="f"><img alt="dot"{s} '
            f'src="data:image/png;base64,{_PNG_1PX}"></figure>',
            author=ME,
            at=ts(0),
        )
        return doc

    def _picture_width_emu(self, path):
        d = docx.Document(str(path))
        assert d.inline_shapes, "no picture was embedded"
        return d.inline_shapes[0].width

    def test_authored_width_is_honored(self, tmp_path):
        out = aim.to_docx(self._fig_doc("width:300px"), tmp_path / "f.docx")
        emu = self._picture_width_emu(out)
        assert abs(emu - int(914400 * 300 / 96)) <= 914400 // 96  # 3.125in ±1px

    def test_default_width_when_unspecified(self, tmp_path):
        out = aim.to_docx(self._fig_doc(None), tmp_path / "f.docx")
        assert abs(self._picture_width_emu(out) - int(4.5 * 914400)) <= 9144

    def test_oversized_width_clamps_to_content_box(self, tmp_path):
        out = aim.to_docx(self._fig_doc("width:2000px"), tmp_path / "f.docx")
        # A4 portrait, 15mm margins → 180mm content ≈ 7.09in
        assert self._picture_width_emu(out) <= int(7.1 * 914400)
