"""Page setup (aim:doc) and aim-page-break: grammars, ops, undo/verify,
hash coverage, proposals, and the export mappings (spec §3.6)."""
import json
import re

import pytest

import aimformat as aim
from aimformat.canonical import canonical_json
from aimformat.errors import InvalidOperation, ParseError
from aimformat.pagesetup import (default_page_setup, page_css,
                                 page_setup_from_obj)

from conftest import BOT, ME, ts


# --------------------------------------------------------------------------
class TestGrammar:
    def test_defaults_are_a4_15mm(self):
        s = default_page_setup()
        assert (s.size, s.orientation) == ("A4", "portrait")
        assert s.margins_mm == {"top": 15.0, "right": 15.0,
                                "bottom": 15.0, "left": 15.0}
        assert (s.page_width_mm, s.page_height_mm) == (210.0, 297.0)
        assert (s.content_width_mm, s.content_height_mm) == (180.0, 267.0)

    def test_partial_object_falls_back_to_defaults(self):
        s = page_setup_from_obj({"size": "A5"})
        assert s.size == "A5" and s.orientation == "portrait"
        assert s.margins_mm["top"] == 15.0

    def test_landscape_swaps_dimensions(self):
        s = page_setup_from_obj({"size": "A4", "orientation": "landscape"})
        assert (s.page_width_mm, s.page_height_mm) == (297.0, 210.0)

    def test_unknown_size_is_d003(self):
        with pytest.raises(InvalidOperation) as exc:
            page_setup_from_obj({"size": "A0"})
        assert exc.value.lint_code == "D003"

    def test_unknown_orientation_is_d003(self):
        with pytest.raises(InvalidOperation) as exc:
            page_setup_from_obj({"orientation": "sideways"})
        assert exc.value.lint_code == "D003"

    def test_margin_grammar_is_d004(self):
        with pytest.raises(InvalidOperation) as exc:
            page_setup_from_obj({"margins": {"top": "15px"}})
        assert exc.value.lint_code == "D004"

    def test_margin_bound_is_d004(self):
        with pytest.raises(InvalidOperation) as exc:
            page_setup_from_obj({"margins": {"top": "250mm"}})
        assert exc.value.lint_code == "D004"

    def test_margins_must_leave_content_area(self):
        with pytest.raises(InvalidOperation) as exc:
            page_setup_from_obj({"size": "A5",
                                 "margins": {"left": "80mm", "right": "80mm"}})
        assert exc.value.lint_code == "D004"

    def test_non_object_is_d001(self):
        with pytest.raises(InvalidOperation) as exc:
            page_setup_from_obj(["A4"])
        assert exc.value.lint_code == "D001"

    def test_page_css(self):
        s = page_setup_from_obj({"size": "Letter", "orientation": "landscape",
                                 "margins": {"top": "20mm", "right": "15mm",
                                             "bottom": "20mm", "left": "15mm"}})
        assert page_css(s) == ("@page{size:279.4mm 215.9mm;"
                               "margin:20mm 15mm 20mm 15mm}")

    def test_to_obj_round_trips(self):
        obj = {"size": "Legal", "orientation": "portrait",
               "margins": {"top": "12.5mm", "right": "15mm",
                           "bottom": "12.5mm", "left": "15mm"}}
        assert page_setup_from_obj(page_setup_from_obj(obj).to_obj()).to_obj() \
            == page_setup_from_obj(obj).to_obj()


# --------------------------------------------------------------------------
class TestSetPageSetup:
    def test_set_writes_block_and_event(self, basic_doc):
        basic_doc.set_page_setup({"size": "A5"}, author=ME, at=ts(5))
        assert basic_doc.page_setup.size == "A5"
        ev = basic_doc.history[-1]
        assert (ev.target, ev.action) == ("aim:doc", "modify")
        assert ev.get("before") is None
        text = basic_doc.dumps()
        assert 'type="application/aim-doc+json"' in text
        assert not [f for f in aim.lint_text(text) if f.level == "error"]

    def test_unchanged_setup_raises(self, basic_doc):
        basic_doc.set_page_setup({"size": "A5"}, author=ME, at=ts(5))
        with pytest.raises(InvalidOperation):
            basic_doc.set_page_setup({"size": "A5"}, author=ME, at=ts(6))

    def test_undo_removes_introduced_block(self, basic_doc):
        basic_doc.set_page_setup({"size": "A5"}, author=ME, at=ts(5))
        basic_doc.undo(author=ME, at=ts(6))
        assert basic_doc.page_setup.size == "A4"
        assert basic_doc._state.script("doc") is None
        assert basic_doc.verify() == []
        basic_doc.redo(author=ME, at=ts(7))
        assert basic_doc.page_setup.size == "A5"
        assert basic_doc.verify() == []

    def test_second_set_carries_before_and_undoes_to_previous(self, basic_doc):
        basic_doc.set_page_setup({"size": "A5"}, author=ME, at=ts(5))
        basic_doc.set_page_setup({"size": "Letter"}, author=ME, at=ts(6))
        assert basic_doc.history[-1].get("before") is not None
        basic_doc.undo(author=ME, at=ts(7))
        assert basic_doc.page_setup.size == "A5"
        assert basic_doc.verify() == []

    def test_doc_hash_covers_settings(self, basic_doc):
        before = basic_doc.doc_hash
        basic_doc.set_page_setup({"size": "A5"}, author=ME, at=ts(5))
        changed = basic_doc.doc_hash
        assert changed != before
        basic_doc.undo(author=ME, at=ts(6))
        assert basic_doc.doc_hash == before  # absent block = pre-0.2 hash

    def test_state_at_reconstructs_setup(self, basic_doc):
        seq_before = basic_doc.seq
        basic_doc.set_page_setup({"size": "A5"}, author=ME, at=ts(5))
        assert basic_doc.state_at(seq_before).page_setup.size == "A4"
        assert basic_doc.state_at(basic_doc.seq).page_setup.size == "A5"

    def test_unknown_settings_fields_survive(self, empty_doc):
        empty_doc.flatten()
        text = empty_doc.dumps().replace(
            "</title>",
            '</title>\n<script type="application/aim-doc+json">\n'
            + canonical_json({"x_vendor": {"k": 1}}) + "\n</script>")
        doc = aim.loads(text)
        doc.set_page_setup({"size": "A5"}, author=ME, at=ts(1))
        assert doc.doc_settings["x_vendor"] == {"k": 1}
        assert doc.doc_settings["page"]["size"] == "A5"

    def test_malformed_block_is_parse_error(self, empty_doc):
        empty_doc.flatten()
        text = empty_doc.dumps().replace(
            "</title>",
            '</title>\n<script type="application/aim-doc+json">\n'
            "{not json]\n</script>")
        doc = aim.loads(text)
        with pytest.raises(ParseError):
            doc.doc_settings
        codes = {f.code for f in aim.lint_text(text)}
        assert "D001" in codes

    def test_invalid_setup_rejected_before_mutation(self, basic_doc):
        before = basic_doc.dumps()
        with pytest.raises(InvalidOperation):
            basic_doc.set_page_setup({"size": "A0"}, author=ME, at=ts(5))
        assert basic_doc.dumps() == before


# --------------------------------------------------------------------------
class TestPageBreakChunk:
    def test_add_move_delete_undo(self, basic_doc):
        pb = basic_doc.add_chunk("<aim-page-break></aim-page-break>",
                                 author=ME, after="h1", at=ts(5))
        serial = basic_doc._state.serial(pb.id)
        assert serial == (f'<aim-page-break data-aim="{pb.id}">'
                          "</aim-page-break>")
        assert not [f for f in aim.lint_text(basic_doc.dumps())
                    if f.level == "error"]
        basic_doc.move_chunk(pb.id, author=ME, at=ts(6))  # to end
        assert basic_doc.body_ids[-1] == pb.id
        basic_doc.delete_chunk(pb.id, author=ME, at=ts(7))
        assert not basic_doc._state.exists(pb.id)
        basic_doc.undo(author=ME, at=ts(8))
        assert basic_doc._state.exists(pb.id)
        assert basic_doc.verify() == []

    def test_propose_and_accept_break(self, basic_doc):
        p = basic_doc.propose_add("<aim-page-break></aim-page-break>",
                                  author=BOT, after="h1", at=ts(5),
                                  explanation="Start a fresh page.")
        assert not [f for f in aim.lint_text(basic_doc.dumps())
                    if f.level == "error"]
        basic_doc.accept(p.id, decided_by=ME, at=ts(6))
        tags = [c.tag for c in basic_doc.chunks]
        assert "aim-page-break" in tags
        assert basic_doc.verify() == []

    def test_break_inside_container_is_d006(self, rich_doc):
        rich_doc.add_chunk("<aim-page-break></aim-page-break>",
                           author=ME, container="list", at=ts(20))
        codes = {f.code for f in aim.lint_text(rich_doc.dumps())}
        assert "D006" in codes


# --------------------------------------------------------------------------
class TestPageSetupProposals:
    def test_propose_accept(self, basic_doc):
        p = basic_doc.propose_page_setup({"size": "A5"}, author=BOT, at=ts(5),
                                         explanation="Booklet.")
        assert p.target == "aim:doc"
        assert not [f for f in aim.lint_text(basic_doc.dumps())
                    if f.level == "error"]
        basic_doc.accept(p.id, decided_by=ME, at=ts(6))
        assert basic_doc.page_setup.size == "A5"
        assert basic_doc.verify() == []

    def test_propose_reject_keeps_defaults(self, basic_doc):
        p = basic_doc.propose_page_setup({"size": "A5"}, author=BOT, at=ts(5))
        basic_doc.reject(p.id, decided_by=ME, at=ts(6))
        assert basic_doc.page_setup.size == "A4"
        assert basic_doc.verify() == []

    def test_accept_with_invalid_tweak_raises(self, basic_doc):
        p = basic_doc.propose_page_setup({"size": "A5"}, author=BOT, at=ts(5))
        with pytest.raises(InvalidOperation):
            basic_doc.accept(
                p.id, decided_by=ME, at=ts(6),
                applied='<script type="application/aim-doc+json">\n'
                        '{"page":{"size":"A0"}}\n</script>')

    def test_modify_via_propose_modify(self, basic_doc):
        basic_doc.set_page_setup({"size": "A5"}, author=ME, at=ts(4))
        p = basic_doc.propose_modify(
            "aim:doc",
            '<script type="application/aim-doc+json">\n'
            '{"page":{"margins":{"bottom":"15mm","left":"15mm",'
            '"right":"15mm","top":"15mm"},"orientation":"landscape",'
            '"size":"A5"}}\n</script>',
            author=BOT, at=ts(5))
        basic_doc.accept(p.id, decided_by=ME, at=ts(6))
        assert basic_doc.page_setup.orientation == "landscape"
        assert basic_doc.verify() == []


# --------------------------------------------------------------------------
class TestDeclaredVersionStability:
    """The <html> open line is hashed state: an implicit version upgrade on
    save would break every checkpoint recorded under the old line (found via
    v0.1 documents 422-ing on re-save during the pagination work)."""

    def test_dumps_never_touches_the_declared_version(self, empty_doc):
        empty_doc.flatten()
        old = empty_doc.dumps().replace(
            f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.1"')
        round_tripped = aim.loads(old).dumps()
        assert 'data-aim-version="0.1"' in round_tripped
        findings = aim.lint_text(round_tripped)
        assert "S002" in {f.code for f in findings if f.level == "warning"}
        assert not [f for f in findings if f.level == "error"]

    def test_old_version_checkpoints_survive_a_resave(self, basic_doc):
        basic_doc.checkpoint("pinned", at=ts(5))
        text = basic_doc.dumps()
        # simulate the load→save cycle every backend write performs
        again = aim.loads(text)
        assert again.verify() == []
        assert aim.loads(again.dumps()).verify() == []


# --------------------------------------------------------------------------
class TestPrintCssAndPdfHtml:
    def test_aim_css_carries_print_pagination_layer(self):
        css = aim.generate_aim_css()
        assert "aim-page-break{display:block" in css
        assert "@media print{aim-page-break{border:0;margin:0;" \
               "break-after:page}}" in css
        assert "@media print{[data-aim]{break-inside:avoid}}" in css
        assert "@media print{body{margin:0;padding:0;max-width:none}}" in css

    def test_print_html_splices_page_rule(self, basic_doc):
        from aimformat.convert._pdf_out import _print_html
        basic_doc.set_page_setup({"size": "A5"}, author=ME, at=ts(5))
        html = _print_html(basic_doc, "keep", "@font-face{font-family:X}")
        assert "@page{size:148mm 210mm;margin:15mm 15mm 15mm 15mm}" in html
        assert "@font-face{font-family:X}" in html
        assert html.index("@page{") < html.index("</head>")

    def test_to_pdf_smoke_page_count(self, basic_doc, tmp_path):
        pytest.importorskip("playwright")
        basic_doc.add_chunk("<aim-page-break></aim-page-break>",
                            author=ME, after="h1", at=ts(5))
        out = tmp_path / "smoke.pdf"
        try:
            aim.to_pdf(basic_doc, out)
        except RuntimeError as exc:  # chromium binary not installed
            pytest.skip(str(exc))
        data = out.read_bytes()
        assert data.startswith(b"%PDF")
        pages = data.count(b"/Type /Page") - data.count(b"/Type /Pages")
        assert pages >= 2  # the hard break yields at least two pages


# --------------------------------------------------------------------------
class TestDocxMappings:
    docx = pytest.importorskip("docx")

    def test_export_emits_section_and_break(self, basic_doc, tmp_path):
        from docx import Document
        basic_doc.set_page_setup(
            {"size": "Letter", "orientation": "landscape",
             "margins": {"top": "20mm", "right": "15mm",
                         "bottom": "20mm", "left": "15mm"}},
            author=ME, at=ts(5))
        basic_doc.add_chunk("<aim-page-break></aim-page-break>",
                            author=ME, after="h1", at=ts(6))
        out = tmp_path / "out.docx"
        aim.to_docx(basic_doc, out)
        got = Document(str(out))
        section = got.sections[0]
        assert round(section.page_width / 36000, 1) == 279.4
        assert round(section.page_height / 36000, 1) == 215.9
        assert round(section.top_margin / 36000, 1) == 20.0
        from docx.oxml.ns import qn
        brs = [br for p in got.paragraphs for r in p.runs
               for br in r._r.findall(qn("w:br"))
               if br.get(qn("w:type")) == "page"]
        assert len(brs) == 1

    def test_ingest_side_pass_reads_intent(self, tmp_path):
        from docx import Document
        from docx.enum.text import WD_BREAK
        from docx.shared import Mm
        from aimformat.convert._docx_pages import (_read_break_anchors,
                                                   _read_page_setup,
                                                   apply_docx_pagination)
        src = Document()
        section = src.sections[0]
        section.page_width, section.page_height = Mm(210), Mm(297)
        section.top_margin = section.bottom_margin = Mm(20)
        section.left_margin = section.right_margin = Mm(18)
        src.add_paragraph("Alpha paragraph.")
        beta = src.add_paragraph("Beta paragraph.")
        beta.add_run().add_break(WD_BREAK.PAGE)  # break after beta's text
        src.add_paragraph("Gamma paragraph.")
        delta = src.add_paragraph("Delta paragraph.")
        delta.paragraph_format.page_break_before = True
        path = tmp_path / "src.docx"
        src.save(str(path))

        assert _read_page_setup(Document(str(path))) == {
            "size": "A4", "orientation": "portrait",
            "margins": {"top": "20mm", "right": "18mm",
                        "bottom": "20mm", "left": "18mm"}}
        assert _read_break_anchors(Document(str(path))) == [
            "Beta paragraph.", "Gamma paragraph."]

        doc = aim.new_document(title="Ingested")
        for i, txt in enumerate(["Alpha paragraph.", "Beta paragraph.",
                                 "Gamma paragraph.", "Delta paragraph."]):
            doc.add_chunk(f"<p>{txt}</p>", author=ME, at=ts(i))
        apply_docx_pagination(doc, path, author=ME)
        assert doc.page_setup.margins_mm["left"] == 18.0
        tags = [c.tag for c in doc.chunks]
        assert tags.count("aim-page-break") == 2
        texts = [(c.tag, c.text) for c in doc.chunks]
        assert texts.index(("aim-page-break", "")) \
            == texts.index(("p", "Beta paragraph.")) + 1
        assert not [f for f in aim.lint_text(doc.dumps())
                    if f.level == "error"]
        assert doc.verify() == []

    def test_unmatched_hints_are_skipped(self, tmp_path):
        from docx import Document
        from docx.enum.text import WD_BREAK
        from aimformat.convert._docx_pages import apply_docx_pagination
        src = Document()
        src.add_paragraph("Only in the source.")
        src.add_paragraph("Also only here.").add_run().add_break(WD_BREAK.PAGE)
        path = tmp_path / "src.docx"
        src.save(str(path))
        doc = aim.new_document(title="Different content")
        doc.add_chunk("<p>Entirely different.</p>", author=ME, at=ts(0))
        apply_docx_pagination(doc, path, author=ME)
        assert "aim-page-break" not in [c.tag for c in doc.chunks]
