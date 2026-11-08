"""Word tracked changes on DOCX import: pending proposals on the original.

The oracle is the shape kit (``tracked_docx_kit``): every shape DECLARES
what Word's Reject All and Accept All leave behind, written from ECMA-376
§17.13.5 by hand — never derived from the importer's own resolver. For each
shape the import must give:

1. a body equal to the reject-all golden (the original text);
2. ``accept_all()`` equal to the accept-all golden;
3. ``reject_all()`` equal to the body;
4. a document that lints clean and verifies;
5. the declared card set (action, author);
6. no card without a revision behind it.
"""

from __future__ import annotations

import importlib.util
import io
import pathlib
import random
import re
import signal
import time
import warnings
import zipfile

import pytest

import aimformat as aim

pytest.importorskip("docx_parser_converter")
pytest.importorskip("docx")

from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

import tracked_docx_kit as kit  # noqa: E402
from aimformat import export_docx  # noqa: E402
from aimformat.convert import AimImportWarning, import_docx  # noqa: E402
from aimformat.convert._docx_revisions import AUTHOR_CAP, clean, resolve_view  # noqa: E402
from aimformat.document import AimDocument  # noqa: E402
from aimformat.errors import AimError, InvalidOperation, ParseError  # noqa: E402
from aimformat.events import human  # noqa: E402

DECIDER = human("reviewer")
SHAPES = kit.shapes()


def _texts(doc: aim.AimDocument) -> list[str]:
    return [c.text for c in doc.chunks]


def _fills(doc: aim.AimDocument) -> list[list[str | None]]:
    """Each table row's cell fills (``background-color``), row by row."""
    from aimformat.dom import Element, parse_fragment

    out: list[list[str | None]] = []
    for chunk in doc.chunks:
        if chunk.tag != "tr":
            continue
        (row,) = [x for x in parse_fragment(chunk.html) if isinstance(x, Element)]
        fills: list[str | None] = []
        for cell in row.elements():
            found = re.search(r"background-color:\s*([^;]+)", cell.get("style") or "")
            fills.append(found.group(1).strip() if found else None)
        out.append(fills)
    return out


def _label(actor: aim.Actor) -> str:
    return f"{actor.type}:{actor.id or actor.model}"


def _resolved(doc: aim.AimDocument, decision: str) -> aim.AimDocument:
    copy = aim.loads(doc.dumps())
    if decision == "accept":
        copy.accept_all(decided_by=DECIDER)
    else:
        copy.reject_all(decided_by=DECIDER)
    return copy


@pytest.fixture(scope="module")
def imported() -> dict[str, aim.ImportResult]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", AimImportWarning)
        return {shape.name: import_docx(shape.docx) for shape in SHAPES}


@pytest.mark.parametrize("shape", SHAPES, ids=lambda s: s.name)
class TestDeclaredShapes:
    def test_body_is_the_original(self, shape, imported):
        assert _texts(imported[shape.name].document) == shape.reject

    def test_accept_all_is_words_accept_all(self, shape, imported):
        assert _texts(_resolved(imported[shape.name].document, "accept")) == shape.accept

    def test_reject_all_is_the_body(self, shape, imported):
        doc = imported[shape.name].document
        assert _texts(_resolved(doc, "reject")) == _texts(doc)

    def test_lints_clean_and_verifies(self, shape, imported):
        doc = imported[shape.name].document
        assert [f for f in aim.lint(doc) if f.level == "error"] == []
        assert doc.verify() == []
        # and survives its own file format
        assert [f for f in aim.lint_text(doc.dumps()) if f.level == "error"] == []

    def test_cards_match_the_declaration(self, shape, imported):
        if shape.cards is None:
            pytest.skip("card shape not declared")
        cards = [(p.action, _label(p.author)) for p in imported[shape.name].document.proposals]
        assert cards == shape.cards

    def test_every_card_cites_a_revision(self, shape, imported):
        result = imported[shape.name]
        cited = {card for note in result.report.revisions for card in note.cards}
        assert {p.id for p in result.document.proposals} <= cited
        assert set(result.report.proposals) == {p.id for p in result.document.proposals}


class TestRevisionNesting:
    @pytest.mark.parametrize(
        ("name", "outer", "inner"),
        [("nested_del_ins", "ins", "del"), ("nested_del_outside_ins", "del", "ins")],
    )
    def test_inserted_then_deleted_text_keeps_its_nesting(self, name, outer, inner):
        # Word writes a deletion of another author's tracked insertion inside
        # the insertion, ins(del(r)); LibreOffice drops the outer deletion of
        # del(ins(r)) on import. nested_del_ins carries Word's nesting, so the
        # LibreOffice check can confirm its golden, and nested_del_outside_ins
        # keeps the importer tested on the other nesting.
        from lxml import etree

        shape = next(s for s in SHAPES if s.name == name)
        with zipfile.ZipFile(io.BytesIO(shape.docx)) as z:
            root = etree.fromstring(z.read("word/document.xml"))
        ns = {"w": kit.W_NS}
        assert root.xpath(f"//w:{outer}/w:{inner}/w:r/w:delText", namespaces=ns)
        assert not root.xpath(f"//w:{inner}/w:{outer}", namespaces=ns)


class TestLibreOfficeOracle:
    """How ``scripts/libreoffice_oracle.py`` judges one resolution. Running
    it needs LibreOffice, which only the non-gating CI job has."""

    @pytest.fixture
    def oracle(self):
        path = pathlib.Path(__file__).parent.parent / "scripts" / "libreoffice_oracle.py"
        spec = importlib.util.spec_from_file_location("libreoffice_oracle", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_known_misreadings_name_shapes_the_oracle_runs(self, oracle):
        # an entry for a renamed, removed or skipped shape would never match
        shapes = {s.name: s for s in SHAPES}
        for (name, view), result in oracle.KNOWN_MISREADINGS.items():
            assert name in shapes and name not in oracle.SKIP, name
            assert result != getattr(shapes[name], view), (name, view)

    @pytest.mark.parametrize("listed", [["Wrong."], []], ids=["text", "no_text"])
    def test_a_listed_resolution(self, oracle, listed):
        oracle.KNOWN_MISREADINGS = {("shape", "accept"): listed}
        verdict, line = oracle.compare("shape", "accept", ["Right."], listed)
        assert verdict == "known" and "known LibreOffice misreading" in line
        verdict, line = oracle.compare("shape", "accept", ["Right."], ["Right."])
        assert verdict == "ok"
        assert line.endswith(": LibreOffice now agrees, drop it from KNOWN_MISREADINGS")
        # any other wrong result, or the other view, is still a disagreement
        assert oracle.compare("shape", "accept", ["Right."], ["Other."])[0] == "diff"
        assert oracle.compare("shape", "reject", ["Right."], listed)[0] == "diff"

    def test_an_unlisted_resolution(self, oracle):
        oracle.KNOWN_MISREADINGS = {}
        assert oracle.compare("shape", "accept", ["Right."], ["Right."]) == (
            "ok",
            "ok   shape (accept)",
        )
        assert oracle.compare("shape", "accept", ["Right."], ["Wrong."])[0] == "diff"


class TestAttribution:
    def test_word_author_and_date_become_the_card(self, imported):
        (card,) = imported["inserted_paragraph"].document.proposals
        assert card.author == aim.human("Bob Jones")
        assert card.at == "2026-09-02T11:30:00Z"
        assert card.explanation == "Word: Bob Jones inserted this paragraph"

    def test_mixed_authors_are_never_misattributed(self, imported):
        (card,) = imported["inline_two_authors"].document.proposals
        assert card.author == aim.external("docx-import")
        # both authors are named, with what each did
        assert card.explanation.startswith("Word tracked change (Alice Smith)")
        assert '"thirty" → "fifteen"' in card.explanation
        assert "(Bob Jones)" in card.explanation
        # the latest date among the card's revisions
        assert card.at == "2026-09-02T11:30:00Z"

    def test_one_batch_per_author(self, imported):
        cards = imported["table_rows"].document.proposals
        assert len({c.batch for c in cards}) == 2

    def test_agent_label_round_trips_to_an_agent_actor(self, tmp_path):
        doc = aim.new_document(title="t")
        a = doc.add_chunk("<p>Alpha.</p>", author=aim.human("ada")).id
        doc.propose_modify(a, f'<p data-aim="{a}">Beta.</p>', author=aim.agent("model-x"))
        path = tmp_path / "agent.docx"
        aim.to_docx(doc, path)
        (card,) = import_docx(path).document.proposals
        assert card.author == aim.agent("model-x")

    def test_missing_date_uses_import_time_and_valid_utc(self, imported):
        (card,) = imported["missing_date"].document.proposals
        assert card.author == aim.human("Carol White")
        assert card.at.endswith("Z") and card.at[:4].isdigit()

    def test_author_names_are_cleaned_and_capped(self):
        assert clean("Eve\x07\x1b Evil", AUTHOR_CAP) == "Eve Evil"
        assert len(clean("x" * 1000, AUTHOR_CAP)) == AUTHOR_CAP
        d = kit.Doc()
        # XML itself forbids most C0 controls; the C1 range and DEL are legal
        d.body = [d.p(d.t("A "), d.ins("b", ("Mallory\x7f\x85\x9f\t" + "m" * 500, None)))]
        (card,) = import_docx(d.build()).document.proposals
        assert not set("\x7f\x85\x9f\t") & set(card.author.id or "")
        assert len(card.author.id or "") <= AUTHOR_CAP


class TestNoCardWithoutRevision:
    def test_a_neighbours_insertion_never_becomes_a_card_on_untouched_text(self, imported):
        # "I.2 Second rule." converts as "I.3 Second rule." in the accept view
        # only because a rule was inserted before it: no revision touches it
        result = imported["baked_numbering_insertion"]
        untouched = next(c for c in result.document.chunks if "Second rule" in c.text)
        assert all(p.target != untouched.id for p in result.document.proposals)
        assert any("converted differently" in w for w in result.report.warnings)

    def test_formatting_the_format_cannot_express_is_reported(self):
        d = kit.Doc()
        rpr = (
            '<w:rPr><w:spacing w:val="20"/><w:rPrChange w:id="900" w:author="Bob Jones">'
            "<w:rPr/></w:rPrChange></w:rPr>"
        )
        d.body = [d.p(d.t("Letter-spaced", rpr))]
        result = import_docx(d.build())
        assert result.document.proposals == []
        (note,) = result.report.revisions
        assert note.cards == () and note.reason == "formatting the format cannot express"
        assert any("produced no proposal" in w for w in result.report.warnings)


class TestTablePropertyRevisions:
    """A ``w:tblPrChange`` sits in the table's own ``tblPr``, outside every
    row, yet the table style and its ``tblLook`` flags paint the rows'
    cells. No row cited it: the new paint was converter noise, the original
    paint was kept, and the change was reported as formatting the format
    cannot express."""

    # the kit's Medium Shading 1 Accent 1, as python-docx's template defines it
    HEAD, BAND, NONE = ["#4f81bd"] * 2, ["#d3dfee"] * 2, [None, None]

    @staticmethod
    def _table_note(result: aim.ImportResult):
        (note,) = [n for n in result.report.revisions if n.kind == "tblPrChange"]
        return note

    @pytest.mark.parametrize("name", ["table_style_change", "table_banding_change"])
    def test_the_rows_it_repaints_become_modifies(self, name, imported):
        result = imported[name]
        doc = result.document
        assert _fills(doc) == [self.NONE] * 4
        assert _fills(_resolved(doc, "accept")) == [self.HEAD, self.BAND, self.NONE, self.BAND]
        assert _fills(_resolved(doc, "reject")) == [self.NONE] * 4
        note = self._table_note(result)
        assert sorted(note.cards) == sorted(p.id for p in doc.proposals)
        assert note.reason is None
        assert {p.explanation for p in doc.proposals} == {"Word: Bob Jones changed formatting"}
        assert not any("converted differently" in w for w in result.report.warnings)
        # the converter's private marks
        assert not re.search(r"data-aim-(?:src|look|row)", doc.dumps())

    def test_a_row_the_style_does_not_repaint_keeps_its_own_author(self, imported):
        result = imported["table_style_change_with_cell_edits"]
        doc = result.document
        assert _fills(_resolved(doc, "accept")) == [self.HEAD, self.BAND, self.NONE, self.BAND]
        training = next(c for c in doc.chunks if c.text.startswith("Training"))
        (card,) = [p for p in doc.proposals if p.target == training.id]
        assert card.author == aim.human("Alice Smith")
        assert "formatting" not in (card.explanation or "")
        note = self._table_note(result)
        assert len(note.cards) == 3 and card.id not in note.cards

    def test_a_fill_in_a_row_the_style_does_not_repaint_is_its_authors_alone(self, imported):
        # Training is the second band, which neither style paints: under the
        # old properties and the new its paint is Alice's fill, so the switch
        # changed nothing in it, although its row changed too
        result = imported["table_style_change_with_cell_fill"]
        doc = result.document
        filled = ["#ffff00", None]
        assert _fills(_resolved(doc, "accept")) == [self.HEAD, self.BAND, filled, self.BAND]
        training = next(c for c in doc.chunks if c.text.startswith("Training"))
        (card,) = [p for p in doc.proposals if p.target == training.id]
        assert card.author == aim.human("Alice Smith")
        assert card.explanation == "Word: Alice Smith changed formatting"
        note = self._table_note(result)
        assert len(note.cards) == 3 and card.id not in note.cards

    def test_rows_an_insertion_moves_to_another_band_do_not_cite_it(self, imported):
        # Light Shading Accent 1 paints the first band as Medium Shading 1
        # Accent 1 does: the switch repaints the header row and nothing else.
        # Training and Hosting change band only because Alice inserted Audit
        # above them, which no revision of theirs records: converter noise.
        result = imported["table_style_change_same_band_with_row_insertion"]
        doc = result.document
        note = self._table_note(result)
        item = next(c for c in doc.chunks if c.text.startswith("Item"))
        assert [p.target for p in doc.proposals if p.id in note.cards] == [item.id]
        assert any("converted differently" in w for w in result.report.warnings)

    @pytest.mark.parametrize("style", [kit.SHADED_STYLE, kit.DARK_STYLE])
    def test_a_row_the_change_alone_repaints_cites_it_though_an_insertion_would_too(
        self, style, imported
    ):
        # Banded Rows off clears Support's and Hosting's first band; Alice's
        # Audit above them would too, by moving them to the second band. So
        # without Bob's change the rows would end the same, but Bob's change
        # on its own gives them exactly that paint: it repainted them.
        # Uncited, they would keep their old band through accept_all.
        name = {
            kit.SHADED_STYLE: "table_banding_off_with_row_insertion",
            kit.DARK_STYLE: "dark_table_banding_off_with_row_insertion",
        }[style]
        result = imported[name]
        doc = result.document
        note = self._table_note(result)
        assert note.reason is None
        texts = {c.id: c.text for c in doc.chunks}
        bob = [p for p in doc.proposals if p.id in note.cards]
        assert [texts[p.target] for p in bob] == ["SupportEUR 500", "HostingEUR 100"]
        assert {p.explanation for p in bob} == {"Word: Bob Jones changed formatting"}
        # rejecting Alice's row and accepting the rest is Word with Bob's
        # change alone: the same table without Audit, accepted
        from aimformat.convert._docx_tracked import actual_signature

        partial = aim.loads(doc.dumps())
        (add,) = [p for p in partial.proposals if p.action == "add"]
        partial.reject(add.id, decided_by=DECIDER)
        partial.accept_all(decided_by=DECIDER)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", AimImportWarning)
            bob_alone = import_docx(kit.banding_off_table(style, audit=False), tracked="accept")
        assert actual_signature(partial) == actual_signature(bob_alone.document)

    @pytest.mark.parametrize(
        "name",
        [
            "table_width_change_with_cell_edit",
            "table_width_change_with_row_insertion",
            "table_first_column_change_with_row_insertion",
            "table_column_flags_change_with_row_insertion",
        ],
    )
    def test_a_change_the_format_cannot_express_is_cited_by_no_row(self, name, imported):
        # The rows below an inserted row change band; that is not the
        # change's doing. Column flags gate column formats the import does
        # not read.
        result = imported[name]
        note = self._table_note(result)
        assert note.cards == () and note.reason == "formatting the format cannot express"
        assert any("produced no proposal" in w for w in result.report.warnings)

    @pytest.mark.parametrize(
        "name",
        [
            "table_style_change",
            "table_banding_change",
            "table_style_change_with_cell_edits",
            "table_style_change_with_cell_fill",
            "table_width_change_with_cell_edit",
            "table_style_change_with_row_insertion",
            "table_banding_off_with_row_insertion",
            "dark_table_banding_off_with_row_insertion",
            "cell_shading_change",
            "header_row_change",
        ],
    )
    def test_accept_all_is_the_accept_import_cell_for_cell(self, name, imported):
        # paint, header text colour, th/thead and all. (Not with the width
        # change and an inserted row: the bands it shifts stay as they were.)
        from aimformat.convert._docx_tracked import actual_signature

        shape = next(s for s in SHAPES if s.name == name)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", AimImportWarning)
            accepted = import_docx(shape.docx, tracked="accept").document
            rejected = import_docx(shape.docx, tracked="reject").document
        doc = imported[name].document
        assert actual_signature(_resolved(doc, "accept")) == actual_signature(accepted)
        assert actual_signature(_resolved(doc, "reject")) == actual_signature(rejected)
        assert actual_signature(doc) == actual_signature(rejected)

    def test_a_cells_own_fill_change_is_its_rows_modify(self, imported):
        doc = imported["cell_shading_change"].document
        assert _fills(doc) == [self.NONE, self.NONE]
        assert _fills(_resolved(doc, "accept")) == [self.NONE, ["#ffff00", None]]


class TestComments:
    def test_comment_is_reported_and_its_text_kept(self, imported):
        result = imported["comment"]
        (note,) = result.report.comments
        assert note.author == "Carol White"
        assert note.text == "Net or gross?"
        assert note.anchor_text == "EUR 10,000"
        assert note.chunk_id == result.document.chunks[0].id
        assert "EUR 10,000" in result.document.chunks[0].text
        assert any("comment" in w for w in result.report.warnings)

    def test_comment_on_inserted_text_points_at_the_card(self, imported):
        result = imported["comment_on_insertion"]
        (note,) = result.report.comments
        assert note.anchor_text == "plus added words"
        (card,) = result.document.proposals
        assert note.chunk_id == card.id

    def test_reply_threading_is_reported(self, imported):
        notes = {n.id: n for n in imported["comment_reply"].report.comments}
        assert notes["6"].parent_id == "5"
        assert notes["5"].parent_id is None

    def test_from_docx_warns_about_what_it_did_not_carry(self):
        shape = next(s for s in SHAPES if s.name == "comment")
        with pytest.warns(AimImportWarning, match="comment"):
            aim.from_docx(shape.docx)

    def test_comments_are_never_stored_in_the_document(self, imported):
        text = imported["comment"].document.dumps()
        assert "Net or gross?" not in text


class TestModes:
    @pytest.mark.parametrize("shape", SHAPES, ids=lambda s: s.name)
    def test_accept_and_reject_import_the_resolved_text(self, shape):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", AimImportWarning)
            accepted = import_docx(shape.docx, tracked="accept")
            rejected = import_docx(shape.docx, tracked="reject")
        assert accepted.document.proposals == [] and rejected.document.proposals == []
        assert _texts(rejected.document) == shape.reject
        if shape.name != "baked_numbering_insertion":  # Word's own labels shift
            assert _texts(accepted.document) == shape.accept

    def test_unknown_mode_is_refused(self):
        with pytest.raises(ValueError, match="tracked"):
            import_docx(SHAPES[0].docx, tracked="merge")

    def test_revision_free_documents_report_nothing(self):
        d = kit.Doc()
        d.body = [d.p(d.t("Plain."))]
        result = import_docx(d.build())
        assert result.report.tracked is None
        assert result.report.revisions == [] and result.report.warnings == []

    @pytest.mark.parametrize("body_revision", [True, False], ids=["two-view", "single-view"])
    def test_revisions_in_parts_the_import_skips_are_reported(self, body_revision):
        # A header's tracked change was reported only when the body had one
        # too: a body with no revision and no comment took the single-view
        # path, which never looked at the other parts.
        d = kit.Doc()
        d.body = [d.p(d.t("Body."), *([d.ins(" More.", kit.BOB)] if body_revision else []))]
        data = _with_header_revision(d.build())
        result = import_docx(data)
        assert result.report.tracked == ("propose" if body_revision else None)
        warning = "1 tracked change(s) in headers were not imported"
        assert [w for w in result.report.warnings if "headers" in w] == [warning]
        with pytest.warns(AimImportWarning, match="in headers were not imported"):
            aim.from_docx(data)

    def test_a_damaged_part_the_import_skips_does_not_stop_it(self):
        # Counting the skipped parts' revisions reads them on every import; a
        # header whose bytes fail their checksum must not make that fatal.
        d = kit.Doc()
        d.body = [d.p(d.t("Body."))]
        data = _damaged_member(_with_header_revision(d.build()), "word/header")
        result = import_docx(data)
        assert _texts(result.document) == ["Body."]

    def test_cli_flag(self, tmp_path, capsys):
        from aimformat.cli import main

        src = tmp_path / "in.docx"
        src.write_bytes(next(s for s in SHAPES if s.name == "inserted_paragraph").docx)
        out = tmp_path / "out.aim"
        assert main(["import", str(src), "-o", str(out)]) == 0
        assert len(aim.load(out).proposals) == 1
        assert "pending proposal" in capsys.readouterr().err
        out2 = tmp_path / "accepted.aim"
        assert main(["import", str(src), "-o", str(out2), "--tracked", "accept"]) == 0
        assert aim.load(out2).proposals == []


class TestGuards:
    def test_too_many_revisions_is_refused_with_the_alternatives(self):
        data = kit.oversized(12)
        with pytest.raises(ParseError, match="tracked='accept'"):
            import_docx(data, max_revisions=10)
        # the resolved modes never build a lane, so the ceiling does not apply
        assert import_docx(data, max_revisions=10, tracked="accept").document.chunks

    def test_too_many_proposals_is_refused_before_the_lane_is_written(self):
        # One deleted paragraph joins two lists: two revision records, and one
        # card per item of the second list. The revision ceiling cannot bound
        # that; writing and validating 1,500 cards took over a minute.
        d = kit.Doc()
        d.numbering = kit.NUMBERING
        d.body = [d.p(d.t(f"a{i}"), num=(7, 0)) for i in range(3)]
        d.body.append(d.deleted_p("Part two", kit.BOB))
        d.body += [d.p(d.t(f"b{i}"), num=(7, 0)) for i in range(1500)]
        start = time.monotonic()
        with pytest.raises(ParseError, match="max_proposals=500.*tracked='accept'"):
            import_docx(d.build())
        assert time.monotonic() - start < 10

    def test_the_proposal_budget_is_the_callers(self):
        d = kit.Doc()
        d.body = [d.inserted_p(f"New {i}.", kit.BOB) for i in range(3)]
        with pytest.raises(ParseError, match="would become 3 proposals"):
            import_docx(d.build(), max_proposals=2)
        assert len(import_docx(d.build(), max_proposals=3).document.proposals) == 3

    def test_a_lane_at_the_proposal_budget_imports_in_bounded_time(self):
        d = kit.Doc()
        d.body = [
            d.p(d.t(f"Clause {i} "), d.ins("added", kit.ALICE), d.dele(" gone", kit.BOB))
            for i in range(500)
        ]
        start = time.monotonic()
        result = import_docx(d.build())
        assert time.monotonic() - start < 40
        assert len(result.document.proposals) == 500

    @pytest.mark.parametrize("closed", [True, False], ids=["survivor", "end_of_body"])
    def test_a_run_of_joined_paragraphs_resolves_in_linear_time(self, closed):
        # Joining pairwise re-moved the accumulated content at every gone
        # mark: 2,000 tracked joins took ~8 s, 4,000 over a minute.
        d = kit.Doc()
        n = 2000
        d.body += [d.p(d.t(f"w{i} "), mark=("del", kit.ALICE)) for i in range(n)]
        if closed:
            d.body.append(d.p(d.t("end")))
        start = time.monotonic()
        texts = _view_texts(d.build(), "accept")
        assert time.monotonic() - start < 3
        want = "".join(f"w{i} " for i in range(n)) + ("end" if closed else "")
        assert texts == [want.strip()]

    def test_many_open_comment_ranges_import_in_linear_time(self):
        # Every text node re-summed every open range's excerpt buffer: 800
        # unterminated ranges over 800 one-letter runs (a 40 KB file) took
        # ~5 s, 1,600 minutes.
        d = kit.Doc()
        n = 800
        starts = "".join(f'<w:commentRangeStart w:id="{i}"/>' for i in range(n))
        d.body.append(d.p(starts + "".join(d.t("x") for _ in range(n))))
        d.body.append(d.p(d.comment("c1", d.t("anchored"), "a note")))
        start = time.monotonic()
        result = import_docx(d.build())
        assert time.monotonic() - start < 3
        assert result.document.chunks

    def test_a_megabyte_revision_imports_in_linear_time(self):
        data = kit.hostile_paragraph(1_000_000)
        start = time.monotonic()
        result = import_docx(data)
        assert time.monotonic() - start < 30
        (card,) = result.document.proposals
        assert len(card.explanation or "") <= 240

    def test_an_entity_declaring_comments_part_is_refused(self):
        shape = next(s for s in SHAPES if s.name == "comment")
        with zipfile.ZipFile(io.BytesIO(shape.docx)) as z:
            parts = {n: z.read(n) for n in z.namelist()}
        parts["word/comments.xml"] = (
            b'<?xml version="1.0"?><!DOCTYPE c [<!ENTITY a "aaaaaaaaaa">'
            b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
            b'<w:comments xmlns:w="http://schemas.openxmlformats.org/'
            b'wordprocessingml/2006/main"><w:comment w:id="0">&b;</w:comment></w:comments>'
        )
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for name, data in parts.items():
                z.writestr(name, data)
        with pytest.raises(ParseError, match="DOCTYPE"):
            import_docx(out.getvalue())

    def test_performance_800_chunks_300_cards(self):
        d = kit.Doc()
        for i in range(800):
            if i % 8 == 0:
                d.body.append(
                    d.p(d.t(f"Clause {i} keeps "), d.dele("old"), d.ins("new", kit.BOB), d.t("."))
                )
            elif i % 8 == 3:
                d.body.append(d.inserted_p(f"Inserted clause {i}.", kit.BOB))
            elif i % 8 == 5:
                d.body.append(d.deleted_p(f"Deleted clause {i}.", kit.ALICE))
            else:
                d.body.append(d.p(d.t(f"Plain clause {i}.")))
        start = time.monotonic()
        result = import_docx(d.build())
        assert time.monotonic() - start < 15
        assert len(result.document.proposals) == 300


class TestWholeContainerFallback:
    def test_a_changed_list_becomes_one_modify_keeping_its_item_ids(self, monkeypatch):
        # The fallback runs when the fine alignment fails; force that. Its
        # container modify used to carry placeholder ids, which every changed
        # list or table refused ("payload id '' does not match target").
        real = AimDocument._propose_lane
        calls: list[int] = []

        def fine_fails(self, specs):
            calls.append(len(specs))
            if len(calls) == 1:
                raise InvalidOperation("forced fine-alignment failure")
            return real(self, specs)

        monkeypatch.setattr(AimDocument, "_propose_lane", fine_fails)
        d = kit.Doc()
        d.numbering = kit.NUMBERING
        d.body = [
            d.p(d.t("Intro.")),
            d.p(d.t("One"), d.ins(" more", kit.ALICE), num=(7, 0)),
            d.p(d.t("Two"), num=(7, 0)),
            d.inserted_p("Three", kit.BOB, num=(7, 0)),
        ]
        result = import_docx(d.build())
        doc = result.document
        assert len(calls) == 2
        assert any("whole-container" in w for w in result.report.warnings)
        assert [p.action for p in doc.proposals] == ["modify"]
        before = {c.text: c.id for c in doc.chunks}
        accepted = _resolved(doc, "accept")
        assert _texts(accepted) == ["Intro.", "One more", "Two", "Three"]
        after = {c.text: c.id for c in accepted.chunks}
        assert after["One more"] == before["One"] and after["Two"] == before["Two"]
        assert _texts(_resolved(doc, "reject")) == _texts(doc)
        assert [f for f in aim.lint(doc) if f.level == "error"] == []
        assert doc.verify() == []


class TestRoundTrip:
    def _aim_with_every_card(self) -> aim.AimDocument:
        doc = aim.new_document(title="Round trip")
        ada, bot, bea = aim.human("ada"), aim.agent("model-x"), aim.human("bea")
        with doc.batch():
            ids = [
                doc.add_chunk(f"<p>{t}</p>", author=ada).id
                for t in ("Alpha.", "Bravo.", "Charlie.", "Delta.", "Echo.")
            ]
            doc.add_chunk(
                '<table data-aim-container=""><tbody><tr data-aim=""><td>Item</td>'
                "<td>Price</td></tr></tbody></table>",
                author=ada,
            )
        doc.propose_modify(ids[0], f'<p data-aim="{ids[0]}">Alpha, revised.</p>', author=bot)
        doc.propose_add("<p>Inserted.</p>", after=ids[1], author=bot)
        doc.propose_delete(ids[2], author=bea)
        doc.propose_move(ids[3], container="body", after=ids[4], author=bea)
        doc.propose_move(ids[4], container="body", after=ids[0], author=bea)
        doc.propose_modify(ids[4], f'<p data-aim="{ids[4]}">Echo, moved.</p>', author=bot)
        container = doc.containers[-1]
        doc.propose_add(
            "<tr><td>Support</td><td>EUR 500</td></tr>", container=container, author=bot
        )
        return doc

    def test_aim_to_docx_to_aim_keeps_actions_actors_and_outcomes(self, tmp_path):
        doc = self._aim_with_every_card()
        path = tmp_path / "rt.docx"
        aim.to_docx(doc, path, pending="tracked")
        back = import_docx(path).document
        want = sorted((p.action, _label(p.author)) for p in doc.proposals)
        got = sorted((p.action, _label(p.author)) for p in back.proposals)
        assert got == want
        assert _texts(back) == _texts(doc)
        assert _texts(_resolved(back, "accept")) == _texts(_resolved(doc, "accept"))
        assert _texts(_resolved(back, "reject")) == _texts(_resolved(doc, "reject"))

    @pytest.mark.parametrize(
        "name",
        ["inline_two_authors", "split", "merge", "move", "moved_and_modified", "table_rows"],
    )
    def test_word_to_aim_to_docx_resolves_to_the_same_text(self, name, tmp_path):
        shape = next(s for s in SHAPES if s.name == name)
        back = tmp_path / f"{name}.docx"
        aim.to_docx(import_docx(shape.docx).document, back, pending="tracked")
        for view in ("accept", "reject"):
            assert _view_texts(back.read_bytes(), view) == _view_texts(shape.docx, view)

    def test_a_paragraph_inserted_after_a_move_destination_stays_there(self, tmp_path):
        # The import anchors the insertion on the moved chunk; the export used
        # to write it at the move SOURCE, so Word's Accept All put "New para"
        # where the moved text had been.
        d = kit.Doc()
        d.body += [
            d.p(d.t("Alpha")),
            d.moved("moveFrom", "Moved text", "m1"),
            d.p(d.t("Beta")),
            d.moved("moveTo", "Moved text", "m1"),
            d.inserted_p("New para"),
            d.p(d.t("Gamma")),
        ]
        doc = import_docx(d.build()).document
        want = ["Alpha", "Beta", "Moved text", "New para", "Gamma"]
        assert _texts(_resolved(doc, "accept")) == want
        back = tmp_path / "moved.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert _view_texts(back.read_bytes(), "accept") == want
        assert _view_texts(back.read_bytes(), "reject") == _texts(doc)

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            (["Old"], ["New"]),
            (["Old"], ["New1", "New2"]),
            (["Old"], ["New1", "New2", "New3"]),
            (["Old1", "Old2"], ["New"]),
            (["Old1", "Old2", "Old3"], ["New1", "New2"]),
            (["Old1", "Old2"], []),
        ],
        ids=["1-to-1", "1-to-2", "1-to-3", "2-to-1", "3-to-2", "2-to-none"],
    )
    def test_a_moved_and_modified_chunk_resolves_like_its_two_cards(self, old, new, tmp_path):
        # Every block of the replacement went into the moved paragraph, so
        # Word's Accept All ran them together ("New1New2"). And every old
        # block kept a moved paragraph mark of its own that the modify left
        # standing, so Accept All kept an empty paragraph for each old block
        # but the last, and for the last one too when the replacement has no
        # block at all. Accepting the two cards gives none of these.
        #
        # Then all the old blocks went into ONE moved paragraph, and Word
        # gives a rejected move's old paragraphs back as the destination
        # splits them: its Reject All, and accepting the move while rejecting
        # the modify, ran them together ("Old1Old2"). The resolver restores a
        # rejected move from its source and cannot see that, so the layout is
        # pinned too: one moved paragraph per old block.
        ada, bot = aim.human("ada"), aim.agent("model-x")
        doc = aim.new_document(title="Moved and rewritten")
        before = "".join(f"<p>{text}</p>" for text in old)
        markups = ("<p>Alpha</p>", f"<div>{before}</div>", "<p>Beta</p>", "<p>Gamma</p>")
        with doc.batch():
            _, moved, beta, _ = (doc.add_chunk(m, author=ada).id for m in markups)
        move = doc.propose_move(moved, container="body", after=beta, author=ada)
        after = "".join(f"<p>{text}</p>" for text in new)
        rewrite = doc.propose_modify(moved, f'<div data-aim="{moved}">{after}</div>', author=bot)
        tracked = tmp_path / "tracked.docx"
        aim.to_docx(doc, tracked, pending="tracked")
        want = {
            "accept": ["Alpha", "Beta", *new, "Gamma"],
            "reject": ["Alpha", *old, "Beta", "Gamma"],
        }
        for view, paragraphs in want.items():
            resolved = tmp_path / f"{view}.docx"
            aim.to_docx(doc, resolved, pending=f"{view}-all")  # the SDK's own outcome
            assert _view_paragraphs(resolved.read_bytes(), view) == paragraphs
            assert _view_paragraphs(tracked.read_bytes(), view) == paragraphs
        assert _moved_paragraph_texts(tracked.read_bytes()) == old
        mixed = aim.loads(doc.dumps())
        mixed.accept(move.id, decided_by=DECIDER)
        mixed.reject(rewrite.id, decided_by=DECIDER)
        resolved = tmp_path / "mixed.docx"
        aim.to_docx(mixed, resolved, pending="accept-all")  # nothing is left pending
        paragraphs = ["Alpha", "Beta", *old, "Gamma"]
        assert _view_paragraphs(resolved.read_bytes(), "accept") == paragraphs
        rewriter = export_docx._actor_label(bot)
        assert _view_paragraphs_rejecting(tracked.read_bytes(), rewriter) == paragraphs

    @pytest.mark.parametrize("forward", [True, False], ids=["later", "earlier"])
    @pytest.mark.parametrize(
        "order",
        [
            ("move", "add", "chained"),
            ("add", "move", "chained"),
            ("add", "chained", "move"),
            ("move", "other_move", "add"),
            ("other_move", "move", "add"),
        ],
    )
    def test_adds_anchored_on_a_moved_chunk_follow_creation_order(self, order, forward, tmp_path):
        # Accepting in creation order, an add made before the move lands at
        # the chunk's old place and one made after it at the new place; the
        # tracked export must give Word's Accept All the same outcome.
        ada = aim.human("ada")
        doc = aim.new_document(title="Moves")
        with doc.batch():
            ids = [
                doc.add_chunk(f"<p>{t}</p>", author=ada).id
                for t in ("Alpha", "Moved", "Beta", "Gamma", "Delta")
            ]
        target, dest = (ids[1], ids[3]) if forward else (ids[3], ids[0])
        other = ids[0] if forward else ids[2]
        added = None
        for step in order:
            if step == "move":
                doc.propose_move(target, container="body", after=dest, author=ada)
            elif step == "other_move":
                doc.propose_move(other, container="body", after=target, author=ada)
            elif step == "add":
                added = doc.propose_add("<p>New</p>", after=target, author=ada)
            else:
                doc.propose_add("<p>Chained</p>", after=added.id, author=ada)
        want = _texts(_resolved(doc, "accept"))
        back = tmp_path / "moves.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert _view_texts(back.read_bytes(), "accept") == want
        assert _view_texts(back.read_bytes(), "reject") == _texts(doc)

    @pytest.mark.parametrize(
        ("lane", "want"),
        [
            # codex #40 round 3: once A lands after B, B already stands first
            ([("move", "A", "B"), ("move", "B", None), ("move", "C", None)], "C B A D"),
            ([("move", "A", "B"), ("move", "B", None), ("add", "New", None)], "New B A C D"),
            (
                [
                    ("move", "A", "B"),
                    ("move", "B", None),
                    ("add", "New", None),
                    ("add", "Chained", "New"),
                ],
                "New Chained B A C D",
            ),
            # the deletion of C leaves D after B already
            (
                [("delete", "C"), ("move", "D", "B"), ("add", "New", "B"), ("add", "Newer", "B")],
                "A B New Newer D",
            ),
            # moving C after A puts B after C already
            ([("move", "C", "A"), ("move", "B", "C"), ("move", "A", "C")], "C A B D"),
            # an add made after A's move follows A to its new place
            (
                [("move", "A", "B"), ("add", "New", "A"), ("move", "B", None), ("move", "C", None)],
                "C B A New D",
            ),
            # A's own move is the no-op: the add made before it stays after A
            (
                [
                    ("add", "New", "A"),
                    ("move", "B", None),
                    ("move", "A", "B"),
                    ("add", "Newer", "B"),
                ],
                "B Newer A New C D",
            ),
            # B also has a pending modify: the moved-and-modified layout
            (
                [("move", "A", "B"), ("move", "B", None), ("modify", "B"), ("move", "C", None)],
                "C B2 A D",
            ),
            # C's anchor holds A's move alone; the two cards made after it on
            # A join that pool only once A's arrival is written, and D's move
            # is the no-op (D already follows A)
            ([("move", "A", "C"), ("move", "D", "A"), ("add", "New", "A")], "B C A New D"),
            # the pool at the start (C's move, N1) makes the replay; B's pool,
            # sorted later, must use it too: D's move lands nothing once C has
            # left, so A lands in front of D
            (
                [("move", "C", None), ("add", "N1", None), ("move", "D", "B"), ("move", "A", "B")],
                "C N1 B A D",
            ),
        ],
        ids=[
            "move-on-its-anchor",
            "add-on-its-anchor",
            "chained-add-on-its-anchor",
            "after-a-deletion",
            "chain-of-moves",
            "add-following-a-move",
            "adds-on-both-sides",
            "moved-and-modified",
            "after-move-join",
            "a-later-pool-reuses-the-replay",
        ],
    )
    def test_a_move_earlier_cards_made_a_no_op_lands_like_accept_all(self, lane, want, tmp_path):
        # A move whose block already stands at its destination once the
        # earlier cards are accepted changes nothing: it lands nothing, so it
        # rebinds no later card on its anchor, and those land in front of the
        # block. The export ordered the arrivals as if every move relocated
        # its block.
        doc = _lettered_lane("ABCD", lane)
        assert _texts(_resolved(doc, "accept")) == want.split()
        back = tmp_path / "noop.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert _view_texts(back.read_bytes(), "accept") == want.split()
        assert _view_texts(back.read_bytes(), "reject") == list("ABCD")

    @pytest.mark.parametrize(
        ("names", "lane", "want"),
        [
            # accepting Y's deletion chains A's move on E's move, made after
            # it. accept_all() still takes A's move first (only an add waits
            # for the card it chains onto): it lands after P, where the chain
            # starts, and takes N3 behind A's block, so E, after N2, lands in
            # front of N3. A's deletion keeps the move out of the export.
            # Deciding every card in creation order gives P N2 N3 E Q.
            (
                "PYQEA",
                [
                    ("move", "A", "Y"),
                    ("add", "N2", "P"),
                    ("add", "N3", "P"),
                    ("move", "E", "N2"),
                    ("delete", "Y"),
                    ("delete", "A"),
                    ("accept", "delete Y"),
                ],
                "P N2 E N3 Q",
            ),
            # the same pending cards, reached through a reject: A's move
            # chained on T, then handed to E's move, the zone's remaining last
            # card, when T is rejected
            (
                "PYQEAF",
                [
                    ("move", "A", "Y"),
                    ("add", "N2", "P"),
                    ("add", "N3", "P"),
                    ("move", "E", "N2"),
                    ("add", "T", "P"),
                    ("delete", "Y"),
                    ("delete", "A"),
                    ("accept", "delete Y"),
                    ("reject", "add T"),
                ],
                "P N2 E N3 Q F",
            ),
            # the same pending cards, reached through a move's accept: A's
            # move, made before Y's, handed to E's move when Y's move is
            # accepted
            (
                "PYQEAFG",
                [
                    ("move", "A", "Y"),
                    ("add", "N2", "P"),
                    ("add", "N3", "P"),
                    ("move", "E", "N2"),
                    ("move", "Y", "G"),
                    ("delete", "A"),
                    ("accept", "move Y"),
                ],
                "P N2 E N3 Q F G Y",
            ),
            # the same with A's move chained on N4, an add chained on N2 and
            # made after the move: A's move takes N3 behind A's block, so N4
            # lands in front of N3. A's move is hidden by A's deletion, and a
            # hidden move alone does not make the export replay (a known gap:
            # it then writes card order), so F's move keeps a body move shown
            (
                "PYQEAF",
                [
                    ("move", "A", "Y"),
                    ("add", "N2", "P"),
                    ("add", "N3", "P"),
                    ("add", "N4", "N2"),
                    ("delete", "Y"),
                    ("delete", "A"),
                    ("move", "F", None),
                    ("accept", "delete Y"),
                ],
                "F P N2 N4 N3 Q E",
            ),
            # and with Q's move after E as the shown move instead of F's
            (
                "PYQEAF",
                [
                    ("move", "A", "Y"),
                    ("add", "N2", "P"),
                    ("add", "N3", "P"),
                    ("add", "N4", "N2"),
                    ("delete", "Y"),
                    ("delete", "A"),
                    ("move", "Q", "E"),
                    ("accept", "delete Y"),
                ],
                "P N2 N4 N3 E Q F",
            ),
            # accepting B's move hands D's move, made before it, to N2, the
            # last card on A, chained on N1 and made after D's move (D's
            # deletion keeps the move out of the export): taken first, D's
            # move lands after A and takes S behind D's block, so N2 lands in
            # front of S. G's move keeps a body move shown, as F's does above.
            (
                "ABCDEFG",
                [
                    ("move", "D", "B"),
                    ("add", "N1", "A"),
                    ("add", "S", "A"),
                    ("add", "N2", "N1"),
                    ("move", "B", "F"),
                    ("move", "G", None),
                    ("delete", "D"),
                    ("accept", "move B"),
                ],
                "G A N1 N2 S C E F B",
            ),
            # rejecting N1, the lane's first card (so in creation order),
            # hands N2 and N4 to G, and accepting G's deletion re-anchors them
            # on E. E's move, made between them, splits E's zone: accepting N2
            # does not rebind N4 onto N2's block, so N4 lands right after E,
            # in front of N2 (E's move lands nothing once Y is gone).
            (
                "AXYEGH",
                [
                    ("add", "N1", "G"),
                    ("delete", "G"),
                    ("add", "N2", "N1"),
                    ("delete", "Y"),
                    ("move", "E", "X"),
                    ("add", "N4", "N1"),
                    ("reject", "add N1"),
                ],
                "A X E N4 N2 H",
            ),
        ],
        ids=[
            "accept-a-deletion",
            "reject-the-zone-tail",
            "accept-a-move",
            "chained-on-a-later-add",
            "chained-on-a-later-add-q-moved",
            "accept-a-move-on-a-later-add",
            "a-zone-split-after-a-reject",
        ],
    )
    def test_a_lane_after_a_decision_lands_like_accept_all(self, names, lane, want, tmp_path):
        # An accept or reject re-anchors cards still pending: a block that
        # leaves (its deletion, or a move made after them, accepted) hands
        # the cards anchored on it to the last card on its own anchor, which
        # may be a card made after them, and a rejected card hands its
        # dependents to the remaining last card of its zone, or to its
        # anchor. Plain SDK calls and one or two decisions, all but the last
        # made out of creation order, reach lanes whose arrivals card order
        # misplaces. The export must agree with accept_all(). In the first
        # six lanes that is not what deciding every card in creation order
        # gives: spec section 5.4 lets out-of-order decisions around moves
        # diverge from it.
        doc = _lettered_lane(names, lane)
        assert _texts(_resolved(doc, "accept")) == want.split()
        back = tmp_path / "decided.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert _view_texts(back.read_bytes(), "accept") == want.split()
        assert _view_texts(back.read_bytes(), "reject") == _texts(_resolved(doc, "reject"))

    def test_a_hand_written_shell_on_a_body_add_keeps_the_replay_order(self, tmp_path):
        # accept() keys a zone by container, anchor AND shell, so accepting
        # First does not rebind a Second whose card names a shell (only a
        # hand-written file can: the SDK refuses a shell outside a table);
        # Second lands right after B, in front of First. The export pools both
        # on B, and with a body move pending it must order them by the replay.
        ada = aim.human("ada")
        doc = aim.new_document(title="Shell")
        with doc.batch():
            _, b, _, d = (doc.add_chunk(f"<p>{t}</p>", author=ada).id for t in "ABCD")
        doc.propose_add("<p>First</p>", after=b, author=ada)
        second = doc.propose_add("<p>Second</p>", after=b, author=ada)
        doc.propose_move(d, container="body", after=None, author=ada)
        doc._card_el(second.id).set("data-anchor-shell", "tbody")  # foreign-authored
        doc = aim.loads(doc.dumps())
        want = _texts(_resolved(doc, "accept"))
        assert want == ["D", "A", "B", "Second", "First", "C"]
        back = tmp_path / "shell.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert _view_texts(back.read_bytes(), "accept") == want
        assert _view_texts(back.read_bytes(), "reject") == list("ABCD")

    @pytest.mark.parametrize(
        ("lane", "shelled", "want"),
        [
            ([("add", "New", "A"), ("move", "D", "A")], 1, "A D New B C E"),
            ([("move", "D", "A"), ("add", "New", "A")], 0, "A New D B C E"),
            ([("add", "New", "A"), ("move", "D", "A")], 0, "A D New B C E"),
            ([("move", "D", "A"), ("move", "E", "A")], 1, "A E D B C"),
        ],
        ids=["on-a-later-move", "on-an-earlier-move", "on-an-earlier-add", "on-one-of-two-moves"],
    )
    def test_two_arrivals_one_naming_a_shell_land_like_accept_all(
        self, lane, shelled, want, tmp_path
    ):
        # accept() keys a zone by container, anchor AND shell, so when one of
        # two arrivals on A names a shell (the card *shelled*), accepting the
        # earlier does not rebind the later onto its block: the later lands
        # right after A, in front of it. The export pools both on A, and with
        # a move among them it must order them by the replay. The format
        # defines a shell only for table rows, and the SDK refuses one in the
        # body, so this pins how accept() reads a stray one in a hand-written
        # file.
        doc = _lettered_lane("ABCDE", lane)
        card = doc.proposals[shelled]
        doc._card_el(card.id).set("data-anchor-shell", "tbody")  # foreign-authored
        doc = aim.loads(doc.dumps())
        assert _texts(_resolved(doc, "accept")) == want.split()
        back = tmp_path / "shell.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert _view_texts(back.read_bytes(), "accept") == want.split()
        assert _view_texts(back.read_bytes(), "reject") == list("ABCDE")

    def test_a_foreign_move_to_where_its_block_stands_lands_nothing(self, tmp_path):
        # propose_move refuses a move that changes nothing in the current
        # body, but a hand-written card can still say "B after A" with B
        # already there: an add made after it on the same anchor lands in
        # front of B.
        ada = aim.human("ada")
        doc = aim.new_document(title="Moves")
        with doc.batch():
            a, b, c, _ = (doc.add_chunk(f"<p>{t}</p>", author=ada).id for t in "ABCD")
        move = doc.propose_move(b, container="body", after=c, author=ada)
        doc.propose_add("<p>New</p>", after=a, author=ada)
        doc._card_el(move.id).set("data-anchor-after", a)  # foreign-authored
        doc = aim.loads(doc.dumps())
        want = ["A", "New", "B", "C", "D"]
        assert _texts(_resolved(doc, "accept")) == want
        back = tmp_path / "noop.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert _view_texts(back.read_bytes(), "accept") == want
        assert _view_texts(back.read_bytes(), "reject") == list("ABCD")

    @pytest.mark.parametrize(
        ("lane", "want", "moved"),
        [
            # rejecting N hands A's move, chained on N, N's own anchor: the
            # card now says "A after A", which accept() takes as a no-op
            ([("add", "N", "A"), ("move", "A", "N"), ("reject", "add N")], "A B C D", []),
            # cards made after the move on A land right after A, a card
            # chained on them after that
            (
                [
                    ("add", "N", "A"),
                    ("move", "A", "N"),
                    ("reject", "add N"),
                    ("add", "Later", "A"),
                    ("add", "Chained", "Later"),
                ],
                "A Later Chained B C D",
                [],
            ),
            # A rewritten too
            (
                [
                    ("add", "N", "A"),
                    ("move", "A", "N"),
                    ("reject", "add N"),
                    ("modify", "A"),
                    ("add", "Later", "A"),
                ],
                "A2 Later B C D",
                [],
            ),
            # beside a real move onto A, in a pool the replay orders
            (
                [
                    ("add", "N", "A"),
                    ("move", "A", "N"),
                    ("reject", "add N"),
                    ("move", "D", "A"),
                    ("add", "Later", "A"),
                ],
                "A D Later B C",
                ["D"],
            ),
            # two leftovers, on neighbouring blocks
            (
                [
                    ("add", "N", "A"),
                    ("move", "A", "N"),
                    ("add", "M", "B"),
                    ("move", "B", "M"),
                    ("reject", "add N"),
                    ("reject", "add M"),
                    ("add", "L1", "A"),
                    ("add", "L2", "B"),
                ],
                "A L1 B L2 C D",
                [],
            ),
            # rejecting N hands X, chained on N, to the last card made before
            # N on A: the leftover move card itself, so X follows A
            (
                [
                    ("add", "M", "A"),
                    ("move", "A", "M"),
                    ("reject", "add M"),
                    ("add", "N", "A"),
                    ("add", "X", "N"),
                    ("reject", "add N"),
                ],
                "A X B C D",
                [],
            ),
            # the same with a move: rejecting N hands C's move, chained on
            # N's block, to the leftover card, so C lands after A
            (
                [
                    ("add", "M", "A"),
                    ("move", "A", "M"),
                    ("reject", "add M"),
                    ("add", "N", "A"),
                    ("move", "C", "N"),
                    ("reject", "add N"),
                ],
                "A C B D",
                ["C"],
            ),
        ],
        ids=[
            "alone",
            "a-later-add-and-its-chain",
            "modified",
            "beside-a-move-onto-it",
            "two",
            "a-card-chained-on-the-leftover",
            "a-move-chained-on-the-leftover",
        ],
    )
    def test_a_move_left_anchored_on_its_own_target_keeps_its_block(
        self, lane, want, moved, tmp_path
    ):
        # propose_move refuses "A after A", but a reject leaves it: A's move,
        # chained on N, falls back to N's anchor, A itself (spec section 5.4).
        # With no card made before it in A's zone it moves nothing. The
        # export wrote it as a Word move whose arrival never came out, so
        # Accept All lost A and every card made after the move on A; it now
        # writes no move.
        doc = _lettered_lane("ABCD", lane)
        assert any(p.action == "move" and p.anchor_after == p.target for p in doc.proposals)
        assert _texts(_resolved(doc, "accept")) == want.split()
        back = tmp_path / "self.docx"
        aim.to_docx(doc, back, pending="tracked")
        data = back.read_bytes()
        assert _view_texts(data, "accept") == want.split()
        assert _view_texts(data, "reject") == list("ABCD")
        assert _moved_paragraph_texts(data) == moved

    @pytest.mark.parametrize(
        ("lane", "want"),
        [
            # E1, made after N but before A's move, waits in A's zone too
            (
                [("add", "N", "A"), ("add", "E1", "A"), ("move", "A", "N"), ("reject", "add N")],
                "E1 A B C D",
            ),
            # X1, chained on N, falls back to A as well
            (
                [("add", "N", "A"), ("add", "X1", "N"), ("move", "A", "N"), ("reject", "add N")],
                "X1 A B C D",
            ),
            # D's move onto N, made before A's: D lands first, A after it
            (
                [("add", "N", "A"), ("move", "D", "N"), ("move", "A", "N"), ("reject", "add N")],
                "D A B C",
            ),
        ],
        ids=["an-add-made-before-the-move", "a-card-chained-on-n", "a-move-made-before-it"],
    )
    def test_a_move_left_on_its_own_target_lands_after_the_cards_made_before_it(
        self, lane, want, tmp_path
    ):
        # The "A after A" leftover moves nothing only while no position card
        # made before it waits in A's zone: accept_all() lands such a card
        # first and, when it lands something, rebinds the move onto its
        # block, so A moves after it.
        doc = _lettered_lane("ABCD", lane)
        assert any(p.action == "move" and p.anchor_after == p.target for p in doc.proposals)
        assert _texts(_resolved(doc, "accept")) == want.split()
        back = tmp_path / "captured.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert _view_texts(back.read_bytes(), "accept") == want.split()
        assert _view_texts(back.read_bytes(), "reject") == list("ABCD")

    def test_a_move_written_as_no_move_keeps_the_replay_for_other_pools(self, tmp_path):
        # The leftover "A after A" is the lane's only move. Written as no
        # move, it must still count as a pending move, or the pool on B,
        # where a hand-written shell on Second keeps accepting First from
        # rebinding it (see the shell test above), would fall back to card
        # order.
        ada, rev = aim.human("ada"), aim.human("reviewer")
        doc = aim.new_document(title="Shell")
        with doc.batch():
            a, b, _, _ = (doc.add_chunk(f"<p>{t}</p>", author=ada).id for t in "ABCD")
        n = doc.propose_add("<p>N</p>", after=a, author=ada)
        n_block = doc._payload_root_id(n.payload_html or "")
        doc.propose_move(a, container="body", after=n_block, author=ada)
        doc.reject(n.id, decided_by=rev)
        doc.propose_add("<p>First</p>", after=b, author=ada)
        second = doc.propose_add("<p>Second</p>", after=b, author=ada)
        doc._card_el(second.id).set("data-anchor-shell", "tbody")  # foreign-authored
        doc = aim.loads(doc.dumps())
        want = _texts(_resolved(doc, "accept"))
        assert want == ["A", "B", "Second", "First", "C", "D"]
        back = tmp_path / "noop.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert _view_texts(back.read_bytes(), "accept") == want
        assert _view_texts(back.read_bytes(), "reject") == list("ABCD")

    @pytest.mark.parametrize(
        ("lane", "want", "stays"),
        [
            # rejecting N leaves B's move after A, where B already stands:
            # accepted first, it lands nothing and rebinds nothing
            (
                [("add", "N", "A"), ("move", "B", "N"), ("move", "A", "N"), ("reject", "add N")],
                "A B C D",
                "A",
            ),
            # rejecting Q then hands K, chained on Q, to A's leftover card
            (
                [
                    ("add", "N", "A"),
                    ("move", "B", "N"),
                    ("move", "A", "N"),
                    ("reject", "add N"),
                    ("add", "Q", "A"),
                    ("add", "K", "Q"),
                    ("reject", "add Q"),
                ],
                "A K B C D",
                "A",
            ),
            # accepting the delete of B leaves D's move after A
            (
                [
                    ("move", "D", "B"),
                    ("delete", "B"),
                    ("add", "N", "A"),
                    ("move", "A", "N"),
                    ("reject", "add N"),
                    ("accept", "delete B"),
                ],
                "A D C",
                "A",
            ),
            # C's move, left after B where C stands, waits in B's zone
            (
                [
                    ("add", "N", "B"),
                    ("move", "C", "N"),
                    ("move", "B", "N"),
                    ("reject", "add N"),
                    ("add", "N2", "B"),
                    ("add", "X", "N2"),
                    ("reject", "add N2"),
                ],
                "A B X C D",
                "B",
            ),
        ],
        ids=[
            "a-move-made-before-that-lands-nothing",
            "and-a-card-chained-on-the-leftover",
            "a-move-left-by-an-accepted-delete",
            "a-capture-by-a-move-that-lands-nothing",
        ],
    )
    def test_a_move_left_on_its_own_target_stays_when_the_card_before_it_moves_nothing(
        self, lane, want, stays, tmp_path
    ):
        # A card made before the leftover waits in its block's zone, but
        # accepting it does not move the block: the replay decides. The
        # export wrote a Word move of the block to where it stands, and a
        # card chained on the leftover landed at the end of the document.
        doc = _lettered_lane("ABCD", lane)
        assert any(p.action == "move" and p.anchor_after == p.target for p in doc.proposals)
        assert _texts(_resolved(doc, "accept")) == want.split()
        back = tmp_path / "stays.docx"
        aim.to_docx(doc, back, pending="tracked")
        data = back.read_bytes()
        assert _view_texts(data, "accept") == want.split()
        assert _view_texts(data, "reject") == _texts(_resolved(doc, "reject"))
        assert stays not in _moved_paragraph_texts(data)

    @pytest.mark.parametrize("kind", ["table", "list"])
    @pytest.mark.parametrize("lane", ["captured", "plain"])
    def test_a_moved_container_whose_members_all_go_brings_none_back(self, kind, lane, tmp_path):
        # Every row (or item) of A has a pending delete, so accept_all()
        # leaves no A. Its arrival wrote the current rows back. Captured: the
        # "A after A" leftover with E made before it, which moves A after E.
        ada, rev = aim.human("ada"), aim.human("reviewer")
        doc = aim.new_document(title="Gone")
        if kind == "table":
            rows = "".join(f'<tr data-aim=""><td>A{i}</td></tr>' for i in (1, 2))
            first = f'<table data-aim-container=""><tbody>{rows}</tbody></table>'
        else:
            first = '<ul data-aim-container=""><li data-aim="">A1</li><li data-aim="">A2</li></ul>'
        with doc.batch():
            a = doc.add_chunk(first, author=ada).id
            _, c, _ = (doc.add_chunk(f"<p>{t}</p>", author=ada).id for t in "BCD")
        if lane == "captured":
            n = doc.propose_add("<p>N</p>", after=a, author=ada)
            doc.propose_add("<p>E</p>", after=a, author=ada)
            n_block = doc._payload_root_id(n.payload_html or "")
            doc.propose_move(a, container="body", after=n_block, author=ada)
            doc.reject(n.id, decided_by=rev)
            assert any(p.action == "move" and p.anchor_after == a for p in doc.proposals)
        else:
            doc.propose_move(a, container="body", after=c, author=ada)
        for member in [ch.id for ch in doc.chunks if ch.container == a]:
            doc.propose_delete(member, author=ada)
        want = _texts(_resolved(doc, "accept"))
        assert want == (["E", "B", "C", "D"] if lane == "captured" else ["B", "C", "D"])
        back = tmp_path / "gone.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert _view_texts(back.read_bytes(), "accept") == want
        assert _view_texts(back.read_bytes(), "reject") == _texts(_resolved(doc, "reject"))

    @pytest.mark.parametrize("replays", [True, False], ids=["replayed", "not-replayable"])
    def test_two_moves_of_one_block_anchored_on_it_write_it_once(self, replays, tmp_path):
        # A hand edit copies the "A after A" leftover, captured by E, as a
        # second card (lint P018). Each joined the cards made before the
        # move, so A arrived twice. The replay finds the copy lands nothing;
        # in a lane that does not replay (two adds chained on each other,
        # lint P015) both moves are written, with one arrival.
        steps = [("add", "N", "A"), ("add", "E", "A"), ("move", "A", "N"), ("reject", "add N")]
        if not replays:
            steps += [("add", "X", "C"), ("add", "Y", "X")]
        doc = _lettered_lane("ABCD", steps)
        move = next(p for p in doc.proposals if p.action == "move")
        assert move.anchor_after == move.target
        if not replays:
            x = next(p for p in doc.proposals if p.payload_html and ">X<" in p.payload_html)
            y = next(p for p in doc.proposals if p.payload_html and ">Y<" in p.payload_html)
            doc._card_el(x.id).set("data-anchor-after", y.id)  # foreign-authored
        text = doc.dumps()
        card = re.search(f'<aim-proposal id="{re.escape(move.id)}"[^>]*></aim-proposal>', text)
        assert card is not None
        copy = card.group(0).replace(f'id="{move.id}"', 'id="p-handcopy1"', 1)
        doc = aim.loads(text.replace("</aim-proposals>", copy + "\n</aim-proposals>", 1))
        assert [p.id for p in doc.proposals if p.action == "move"] == [move.id, "p-handcopy1"]
        back = tmp_path / "twice.docx"
        _export_within(doc, back)
        data = back.read_bytes()
        assert _moved_paragraph_texts(data) == ["A"]
        assert _view_texts(data, "reject") == list("ABCD")
        if replays:
            want = _texts(_resolved(doc, "accept"))
            assert want == ["E", "A", "B", "C", "D"]
            assert _view_texts(data, "accept") == want
        else:
            with pytest.raises(AimError):
                doc.accept_all(decided_by=DECIDER)
            assert _view_texts(data, "accept").count("A") == 1

    def test_a_foreign_move_chained_on_a_pending_add_lands_like_accept_all(self, tmp_path):
        # A's anchor holds New alone; B's hand-written move and Chained both
        # anchor on New's proposal id, so they join the pool once New is
        # written. Once New lands B already follows it: the move lands
        # nothing and Chained lands in front of B.
        ada = aim.human("ada")
        doc = aim.new_document(title="Moves")
        with doc.batch():
            a, b, c, _ = (doc.add_chunk(f"<p>{t}</p>", author=ada).id for t in "ABCD")
        new = doc.propose_add("<p>New</p>", after=a, author=ada)
        move = doc.propose_move(b, container="body", after=c, author=ada)
        doc.propose_add("<p>Chained</p>", after=new.id, author=ada)
        doc._card_el(move.id).set("data-anchor-after", new.id)  # foreign-authored
        doc = aim.loads(doc.dumps())
        want = ["A", "New", "Chained", "B", "C", "D"]
        assert _texts(_resolved(doc, "accept")) == want
        back = tmp_path / "chained.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert _view_texts(back.read_bytes(), "accept") == want
        assert _view_texts(back.read_bytes(), "reject") == list("ABCD")

    def test_a_cyclic_lane_with_a_pending_move_still_exports(self, tmp_path):
        # A hand-written file can chain two adds on each other (lint P015).
        # With a move chained into that cycle and a body move pending, the
        # check that decides whether to replay walks the chain and must stop
        # where it started; so must the check of a move left anchored on its
        # own target, which walks every earlier card's chain.
        ada, rev = aim.human("ada"), aim.human("reviewer")
        doc = aim.new_document(title="Cycle")
        with doc.batch():
            a, b, c, d = (doc.add_chunk(f"<p>{t}</p>", author=ada).id for t in "ABCD")
        x = doc.propose_add("<p>X</p>", after=b, author=ada)
        y = doc.propose_add("<p>Y</p>", after=x.id, author=ada)
        doc.propose_add("<p>Z</p>", after=b, author=ada)
        doc.propose_add("<p>W</p>", after=b, author=ada)
        x_block = doc._payload_root_id(x.payload_html or "")
        doc.propose_move(c, container="body", after=x_block, author=ada)
        doc.propose_move(d, container="body", after=None, author=ada)
        # rejecting N leaves A's move anchored on A
        n = doc.propose_add("<p>N</p>", after=a, author=ada)
        n_block = doc._payload_root_id(n.payload_html or "")
        doc.propose_move(a, container="body", after=n_block, author=ada)
        doc.reject(n.id, decided_by=rev)
        doc._card_el(x.id).set("data-anchor-after", y.id)  # foreign-authored
        doc = aim.loads(doc.dumps())
        assert any(p.action == "move" and p.anchor_after == p.target for p in doc.proposals)
        back = tmp_path / "cycle.docx"
        _export_within(doc, back)
        assert _view_texts(back.read_bytes(), "reject") == list("ABCD")

    def test_an_export_whose_anchors_each_hold_one_arrival_replays_nothing(
        self, monkeypatch, tmp_path
    ):
        # Only an anchor that receives two or more arrivals has an order to
        # settle, so only such an anchor may pay for replaying the lane. A
        # Word move among inserted paragraphs that each have their own place
        # (the usual imported document) has none: replaying the lane for it
        # anyway made every such export quadratic in the number of cards.
        d = kit.Doc()
        for i in range(10):
            d.body.append(d.p(d.t(f"Plain {i}.")))
            if i == 0:
                d.body.append(d.moved("moveFrom", "Moved.", "m1"))
            if i == 4:
                d.body.append(d.moved("moveTo", "Moved.", "m1"))
            if i % 2:
                d.body.append(d.inserted_p(f"Inserted {i}.", kit.BOB))
        doc = import_docx(d.build()).document
        assert sorted({p.action for p in doc.proposals}) == ["add", "move"]
        want = _texts(_resolved(doc, "accept"))
        accepted: list[str] = []
        real_accept = AimDocument.accept

        def counting_accept(self, pid, **kwargs):
            accepted.append(pid)
            return real_accept(self, pid, **kwargs)

        monkeypatch.setattr(AimDocument, "accept", counting_accept)
        back = tmp_path / "single.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert accepted == []
        assert _view_texts(back.read_bytes(), "accept") == want
        assert _view_texts(back.read_bytes(), "reject") == _texts(doc)

    def test_the_lane_replays_once_however_many_anchors_need_ordering(self, monkeypatch, tmp_path):
        # Two anchors hold several arrivals each (the beginning: B's and C's
        # moves; E: two adds); the lane's replay is shared, not redone per
        # anchor.
        doc = _lettered_lane(
            "ABCDEF",
            [
                ("move", "A", "B"),
                ("move", "B", None),
                ("move", "C", None),
                ("add", "X", "E"),
                ("add", "Y", "E"),
            ],
        )
        want = "C B A D E X Y F".split()
        assert _texts(_resolved(doc, "accept")) == want
        replays: list[AimDocument] = []
        real_projection = export_docx._projected_body_index

        def counting_projection(source):
            replays.append(source)
            return real_projection(source)

        monkeypatch.setattr(export_docx, "_projected_body_index", counting_projection)
        back = tmp_path / "shared.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert len(replays) == 1
        assert _view_texts(back.read_bytes(), "accept") == want
        assert _view_texts(back.read_bytes(), "reject") == list("ABCDEF")

    @pytest.mark.parametrize(
        ("names", "lane", "want"),
        [
            (
                "ABCDEF",
                [("move", "F", None), ("add", "X", "B"), ("add", "Y", "B")],
                "F A B X Y C D E",
            ),
            # Z chains on X and joins C's pool once X is written; a deletion
            # and a modify elsewhere change nothing for it
            (
                "ABCDEF",
                [
                    ("add", "X", "C"),
                    ("move", "A", "E"),
                    ("add", "Y", "C"),
                    ("add", "Z", "X"),
                    ("delete", "D"),
                    ("modify", "B"),
                ],
                "B2 C X Y Z E A F",
            ),
            ("ABCD", [("add", "X", None), ("move", "C", "A"), ("add", "Y", None)], "X Y A C B D"),
        ],
        ids=["two-adds-on-one-anchor", "with-a-chained-add", "at-the-beginning"],
    )
    def test_adds_sharing_an_anchor_replay_nothing_beside_a_move(
        self, names, lane, want, monkeypatch, tmp_path
    ):
        # Adds on one anchor land in card order whatever a move elsewhere
        # does: accepting one rebinds every later card on its anchor onto its
        # block. Only a move among them (it may land nothing), a card that
        # moves or deletes one of their blocks or the block they anchor on,
        # or a move made before one of them that chains onto it can change
        # that order. Two adds after one paragraph with a move
        # pending elsewhere replayed the whole lane anyway, which made the
        # export quadratic in the number of cards.
        doc = _lettered_lane(names, lane)
        assert _texts(_resolved(doc, "accept")) == want.split()
        replays: list[AimDocument] = []
        real_projection = export_docx._projected_body_index

        def counting_projection(source):
            replays.append(source)
            return real_projection(source)

        monkeypatch.setattr(export_docx, "_projected_body_index", counting_projection)
        back = tmp_path / "adds.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert replays == []
        assert _view_texts(back.read_bytes(), "accept") == want.split()
        assert _view_texts(back.read_bytes(), "reject") == list(names)

    @pytest.mark.parametrize(
        ("build", "want"),
        [
            # a hand-written move takes First's block after Second's
            (lambda: _foreign_move_doc("pooled"), "D A B Second First C"),
            # B's hand-written move chains on Third, a card made after it, so
            # it is accepted first: it lands where the chain starts, after C,
            # and takes Second, a sibling made before Third, behind it
            (lambda: _backward_chained_move_doc(), "A C First Third B Second D"),
        ],
        ids=["a-pooled-block-moved", "a-move-chained-on-a-later-add"],
    )
    def test_adds_on_one_anchor_that_a_foreign_move_reorders_land_like_accept_all(
        self, build, want, tmp_path
    ):
        # The adds no longer land in card order though no move waits on
        # their anchor, so their pool still needs the lane's replay.
        doc = build()
        assert _texts(_resolved(doc, "accept")) == want.split()
        back = tmp_path / "foreign.docx"
        aim.to_docx(doc, back, pending="tracked")
        assert _view_texts(back.read_bytes(), "accept") == want.split()
        assert _view_texts(back.read_bytes(), "reject") == list("ABCD")

    @pytest.mark.parametrize(
        ("build", "skips"),
        [
            (
                lambda: _lettered_lane(
                    "ABCD", [("move", "A", "B"), ("move", "B", None), ("move", "C", None)]
                ),
                False,
            ),
            (
                lambda: _lettered_lane(
                    "ABCDEF",
                    [
                        ("add", "X", "C"),
                        ("move", "A", "E"),
                        ("add", "Y", "C"),
                        ("add", "Z", "X"),
                        ("delete", "D"),
                        ("modify", "B"),
                    ],
                ),
                True,
            ),
            # B's move and deletion: N1 and N2 share an anchor that moves
            (
                lambda: _lettered_lane(
                    "ABCD",
                    [
                        ("move", "D", "A"),
                        ("add", "N1", "B"),
                        ("move", "B", None),
                        ("add", "N2", "B"),
                        ("delete", "B"),
                    ],
                ),
                False,
            ),
            (lambda: _foreign_move_doc("pooled"), False),
            (lambda: _backward_chained_move_doc(), False),
            (lambda: _foreign_move_doc("chained"), False),
            (
                lambda: _lettered_lane(
                    "ABCDEF", [("move", "F", None), ("add", "X", "B"), ("add", "Y", "B")]
                ),
                True,
            ),
            # W, chained on X, joins the pool once X is written and lands
            # after Z, chained on Y but made before W
            (
                lambda: _lettered_lane(
                    "ABCDEF",
                    [
                        ("add", "X", "B"),
                        ("add", "Y", "B"),
                        ("move", "F", None),
                        ("add", "Z", "Y"),
                        ("add", "W", "X"),
                    ],
                ),
                True,
            ),
            (
                lambda: _lettered_lane(
                    "ABCDEF",
                    [
                        ("move", "E", "A"),
                        ("add", "X", "B"),
                        ("add", "Y", "B"),
                        ("add", "Z", "D"),
                        ("add", "W", "D"),
                        ("add", "V", None),
                        ("add", "U", None),
                    ],
                ),
                True,
            ),
            # C, the block after B, moves away between the two adds
            (
                lambda: _lettered_lane(
                    "ABCDEF", [("add", "X", "B"), ("move", "C", "F"), ("add", "Y", "B")]
                ),
                True,
            ),
            # E's move chains on Y, made before it, so it marks no card for
            # the replay
            (
                lambda: _lettered_lane(
                    "ABCDEF", [("add", "X", "B"), ("add", "Y", "B"), ("move", "E", "Y")]
                ),
                True,
            ),
        ],
        ids=[
            "moves",
            "adds",
            "an-anchor-moved",
            "a-pooled-block-moved",
            "a-backward-chain",
            "a-chained-block-moved",
            "two-adds-beside-a-move",
            "adds-chained-on-both",
            "adds-on-three-anchors",
            "the-next-block-moved-away",
            "a-move-chained-on-an-earlier-add",
        ],
    )
    def test_skipping_the_replay_never_changes_the_export(
        self, build, skips, monkeypatch, tmp_path
    ):
        # The replay is skipped only for a pool whose card order it would
        # keep, so forcing it on every pool must leave the document as it
        # was. A lane marked *skips* must skip it on every pool, and every
        # lane must replay once forced, or both exports take the same path
        # and the check holds trivially. In a-chained-block-moved First and
        # Later hold no moved block, yet a hand-written move takes Chained, a
        # card chained on First, in front of B, and Later, made after that
        # move, follows it: the check must look down the chains too.
        doc = build()
        replays: list[AimDocument] = []
        real_projection = export_docx._projected_body_index

        def counting_projection(source):
            replays.append(source)
            return real_projection(source)

        monkeypatch.setattr(export_docx, "_projected_body_index", counting_projection)
        skipped = tmp_path / "skipped.docx"
        aim.to_docx(doc, skipped, pending="tracked")
        if skips:
            assert replays == []
        replays.clear()
        monkeypatch.setattr(export_docx._Exporter, "_lands_in_card_order", lambda *_: False)
        replayed = tmp_path / "replayed.docx"
        aim.to_docx(doc, replayed, pending="tracked")
        assert len(replays) == 1
        with zipfile.ZipFile(skipped) as a, zipfile.ZipFile(replayed) as b:
            assert a.read("word/document.xml") == b.read("word/document.xml")

    def test_an_inserted_list_item_round_trips_as_one_card(self, tmp_path):
        # The exported insertion must keep its list's numPr: without it Word
        # shows the item unnumbered and the re-import reads a list split
        # (delete + two new lists, numbered 1, 1, 2 after Accept All).
        first = import_docx(
            next(s for s in SHAPES if s.name == "dynamic_numbering_insertion").docx
        ).document
        back = tmp_path / "numbered.docx"
        aim.to_docx(first, back, pending="tracked")
        again = import_docx(back).document
        assert [p.action for p in again.proposals] == [p.action for p in first.proposals]
        accepted = _resolved(again, "accept")
        assert len({c.container for c in accepted.chunks}) == 1  # one list, not three

    @pytest.mark.parametrize("shape", SHAPES, ids=lambda s: s.name)
    def test_exported_paragraph_properties_keep_schema_order(self, shape, tmp_path):
        # CT_PPr is a sequence; Word refuses a file whose pPr children are out
        # of order (the paragraph-mark rPr used to be inserted FIRST, before
        # pStyle/numPr). Our own resolver does not care, so check it here.
        from lxml import etree

        w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        order = [
            "pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr",
            "widowControl", "numPr", "suppressLineNumbers", "pBdr", "shd", "tabs",
            "suppressAutoHyphens", "kinsoku", "wordWrap", "overflowPunct",
            "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd",
            "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents",
            "suppressOverlap", "jc", "textDirection", "textAlignment",
            "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr", "sectPr",
            "pPrChange",
        ]  # fmt: skip
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", AimImportWarning)
            doc = import_docx(shape.docx).document
        path = tmp_path / "out.docx"
        aim.to_docx(doc, path, pending="tracked")
        with zipfile.ZipFile(path) as z:
            root = etree.fromstring(z.read("word/document.xml"))
        for ppr in root.iter(f"{w}pPr"):
            names = [etree.QName(c).localname for c in ppr if isinstance(c.tag, str)]
            ranks = [order.index(n) for n in names if n in order]
            assert ranks == sorted(ranks), names


def _view_texts(data: bytes, view: str) -> list[str]:
    """Resolved paragraph texts of a .docx (the resolver stands in for Word
    here; the shapes' declared goldens pin the resolver itself above)."""
    from lxml import etree

    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    out = []
    for p in resolve_view(root, view).iter(f"{w}p"):
        text = "".join(t.text or "" for t in p.iter(f"{w}t")).strip()
        if text:
            out.append(text)
    return out


def _view_paragraphs(data: bytes, view: str) -> list[str]:
    """Every paragraph of a resolved .docx view, empty ones included: an
    empty paragraph is a blank line in Word, which ``_view_texts`` hides."""
    from lxml import etree

    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    view_root = resolve_view(root, view)
    return ["".join(t.text or "" for t in p.iter(f"{w}t")) for p in view_root.iter(f"{w}p")]


def _view_paragraphs_rejecting(data: bytes, author: str) -> list[str]:
    """Every paragraph of a .docx with *author*'s insertions and deletions
    rejected and every other revision accepted. The resolver has no such
    view, so that author's revisions are swapped for their inverses (an
    insertion for a deletion, text for deleted text) and it accepts all."""
    from lxml import etree

    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    inverse = {f"{w}ins": f"{w}del", f"{w}del": f"{w}ins"}
    for el in [e for e in root.iter(*inverse) if e.get(f"{w}author") == author]:
        el.tag = inverse[el.tag]
    for text in list(root.iter(f"{w}t", f"{w}delText")):
        holder = next(text.iterancestors(*inverse), None)
        if holder is not None and holder.get(f"{w}author") == author:
            text.tag = f"{w}delText" if holder.tag == f"{w}del" else f"{w}t"
    view_root = resolve_view(root, "accept")
    return ["".join(t.text or "" for t in p.iter(f"{w}t")) for p in view_root.iter(f"{w}p")]


def _export_within(doc: aim.AimDocument, path: pathlib.Path, seconds: int = 60) -> None:
    """The tracked export of a lane whose cards chain into a cycle: an alarm
    interrupts a chain walk that loops, so a regression fails its test and
    leaves nothing running to slow the tests after it."""
    if not hasattr(signal, "setitimer"):
        pytest.skip("needs SIGALRM")

    def stop(signum, frame):
        raise TimeoutError("the export did not finish: a chain walk loops")

    previous = signal.signal(signal.SIGALRM, stop)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        aim.to_docx(doc, path, pending="tracked")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _moved_paragraph_texts(data: bytes) -> list[str]:
    """The text each paragraph of a .docx holds as moved (``w:moveTo``), for
    the paragraphs that hold any: Word restores a rejected move's old
    paragraphs as these split them."""
    from lxml import etree

    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    out = []
    for p in root.iter(f"{w}p"):
        moved = [child for child in p if child.tag == f"{w}moveTo"]
        if moved:
            out.append("".join(t.text or "" for m in moved for t in m.iter(f"{w}t", f"{w}delText")))
    return out


def _lettered_lane(names: str, lane: list[tuple]) -> aim.AimDocument:
    """One paragraph per letter of *names*, then *lane*'s steps in order:
    the cards ``("move", X, after)``, ``("add", text, after)``,
    ``("delete", X)`` and ``("modify", X)`` (X's text becomes X2, proposed
    by another author), and ``("accept", card)`` or ``("reject", card)`` on
    an earlier card, named ``"<action> <X or text>"``. An anchor is a
    letter, an earlier add's text (a move after it chains on that add), or
    None for the first position."""
    ada, bot = aim.human("ada"), aim.agent("model-x")
    doc = aim.new_document(title="Lane")
    with doc.batch():
        ids = {name: doc.add_chunk(f"<p>{name}</p>", author=ada).id for name in names}
    blocks: dict[str, str] = {}  # each add's block: propose_move chains on it
    cards: dict[str, aim.Proposal] = {}
    for action, name, *after in lane:
        anchor = ids[after[0]] if after and after[0] is not None else None
        if action == "move":
            anchor = blocks.get(after[0], anchor)
            card = doc.propose_move(ids[name], container="body", after=anchor, author=ada)
        elif action == "add":
            card = doc.propose_add(f"<p>{name}</p>", after=anchor, author=ada)
            ids[name], blocks[name] = card.id, doc._payload_root_id(card.payload_html or "")
        elif action == "delete":
            card = doc.propose_delete(ids[name], author=ada)
        elif action == "modify":
            markup = f'<p data-aim="{ids[name]}">{name}2</p>'
            card = doc.propose_modify(ids[name], markup, author=bot)
        else:
            decide = doc.accept if action == "accept" else doc.reject
            decide(cards[name].id, decided_by=DECIDER)
            continue
        cards[f"{action} {name}"] = card
    return doc


def _foreign_move_doc(moved: str) -> aim.AimDocument:
    """A B C D with D's move to the beginning pending, and a hand-written
    move of a pending add's block. ``"pooled"``: First and Second after B,
    First's block moved after Second's. ``"chained"``: First after B,
    Chained on First, Chained's block moved after A, then Later after B."""
    ada = aim.human("ada")
    doc = aim.new_document(title="Moves")
    with doc.batch():
        a, b, c, d = (doc.add_chunk(f"<p>{t}</p>", author=ada).id for t in "ABCD")
    first = doc.propose_add("<p>First</p>", after=b, author=ada)
    if moved == "pooled":
        second = doc.propose_add("<p>Second</p>", after=b, author=ada)
        target, anchor = first, doc._payload_root_id(second.payload_html or "")
    else:
        target, anchor = doc.propose_add("<p>Chained</p>", after=first.id, author=ada), a
    doc.propose_move(d, container="body", after=None, author=ada)
    move = doc.propose_move(a, container="body", after=c, author=ada)
    if moved == "chained":
        doc.propose_add("<p>Later</p>", after=b, author=ada)
    card = doc._card_el(move.id)
    card.set("data-for", doc._payload_root_id(target.payload_html or ""))
    card.set("data-anchor-after", anchor)
    return aim.loads(doc.dumps())


def _backward_chained_move_doc() -> aim.AimDocument:
    """A B C D: B's move after D, made first, then First after C, Second and
    Third chained on First, and the move's anchor rewritten by hand to
    Third's proposal id (a chain on a card made after it)."""
    ada = aim.human("ada")
    doc = aim.new_document(title="Moves")
    with doc.batch():
        _, b, c, d = (doc.add_chunk(f"<p>{t}</p>", author=ada).id for t in "ABCD")
    move = doc.propose_move(b, container="body", after=d, author=ada)
    first = doc.propose_add("<p>First</p>", after=c, author=ada)
    doc.propose_add("<p>Second</p>", after=first.id, author=ada)
    third = doc.propose_add("<p>Third</p>", after=first.id, author=ada)
    doc._card_el(move.id).set("data-anchor-after", third.id)
    return aim.loads(doc.dumps())


def _damaged_member(data: bytes, prefix: str) -> bytes:
    """*data* with one byte flipped inside the compressed bytes of the first
    member whose name starts with *prefix*: the archive still opens, reading
    that member fails its checksum or inflate."""
    buf = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        info = next(i for i in zf.infolist() if i.filename.startswith(prefix))
    off = info.header_offset
    start = off + 30 + int.from_bytes(buf[off + 26 : off + 28], "little")
    start += int.from_bytes(buf[off + 28 : off + 30], "little")
    buf[start + info.compress_size // 2] ^= 0xFF
    return bytes(buf)


def _with_header_revision(data: bytes) -> bytes:
    """*data* plus a header holding one tracked insertion: a part the
    importer does not read."""
    import docx
    from docx.oxml import parse_xml

    document = docx.Document(io.BytesIO(data))
    header = document.sections[0].header
    header.is_linked_to_previous = False
    header.paragraphs[0]._p.append(
        parse_xml(f'<w:ins xmlns:w="{kit.W_NS}" w:id="90" w:author="Bob">{kit.run("v2")}</w:ins>')
    )
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


# -- property tests ------------------------------------------------------------------

_WORDS = st.sampled_from(["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"])
_KINDS = st.sampled_from(["plain", "inline", "inserted", "deleted", "split", "merge"])


@st.composite
def _paragraphs(draw):
    n = draw(st.integers(min_value=1, max_value=7))
    return [(draw(_KINDS), draw(_WORDS), draw(_WORDS), i) for i in range(n)]


def _build(spec) -> tuple[bytes, list[str], list[str]]:
    """Revision markup plus the outcomes it DECLARES (paragraph-mark joins
    computed on the declared texts, not on the XML)."""
    d = kit.Doc()
    views: dict[str, list[tuple[str, bool]]] = {"reject": [], "accept": []}
    for kind, a, b, i in spec:
        base = f"P{i} {a}"
        if kind == "plain":
            d.body.append(d.p(d.t(base)))
            texts = {"reject": (base, True), "accept": (base, True)}
        elif kind == "inline":
            d.body.append(d.p(d.t(f"P{i} "), d.dele(a, kit.ALICE), d.ins(b, kit.BOB)))
            texts = {"reject": (f"P{i} {a}", True), "accept": (f"P{i} {b}", True)}
        elif kind == "inserted":
            d.body.append(d.inserted_p(base, kit.BOB))
            texts = {"reject": ("", False), "accept": (base, True)}
        elif kind == "deleted":
            d.body.append(d.deleted_p(base, kit.ALICE))
            texts = {"reject": (base, True), "accept": ("", False)}
        elif kind == "split":
            d.body.append(d.p(d.t(base), mark=("ins", kit.BOB)))
            texts = {"reject": (base, False), "accept": (base, True)}
        else:
            d.body.append(d.p(d.t(base), mark=("del", kit.ALICE)))
            texts = {"reject": (base, True), "accept": (base, False)}
        for view in views:
            views[view].append(texts[view])
    out: dict[str, list[str]] = {}
    for view, paras in views.items():
        blocks: list[str] = []
        carry = ""
        for j, (text, mark) in enumerate(paras):
            text = carry + text
            carry = ""
            if not mark and j + 1 < len(paras):
                carry = text  # the paragraph joins the next one
                continue
            if text.strip():
                blocks.append(text.strip())
        out[view] = blocks
    return d.build(), out["reject"], out["accept"]


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_paragraphs())
def test_random_revisions_resolve_like_word(spec):
    data, reject, accept = _build(spec)
    doc = import_docx(data).document
    assert _texts(doc) == reject
    assert _texts(_resolved(doc, "accept")) == accept
    assert _texts(_resolved(doc, "reject")) == reject
    assert [f for f in aim.lint(doc) if f.level == "error"] == []


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_paragraphs(), st.randoms(use_true_random=False))
def test_imported_lanes_resolve_in_any_order(spec, rnd: random.Random):
    """§5.4: a move-free lane converges to the creation-order result whatever
    order its cards are accepted in (a chained add waits for its anchor)."""
    data, _reject, accept = _build(spec)
    doc = import_docx(data).document
    pending = [p.id for p in doc.proposals]
    rnd.shuffle(pending)
    stalled = 0
    while pending and stalled <= len(pending):
        pid = pending.pop(0)
        try:
            doc.accept(pid, decided_by=DECIDER)
            stalled = 0
        except AimError:
            pending.append(pid)
            stalled += 1
    assert pending == []
    assert _texts(doc) == accept
