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
            (["Old"], ["New1", "New2"]),
            (["Old"], ["New1", "New2", "New3"]),
            (["Old1", "Old2"], ["New"]),
            (["Old1", "Old2", "Old3"], ["New1", "New2"]),
            (["Old1", "Old2"], []),
        ],
        ids=["1-to-2", "1-to-3", "2-to-1", "3-to-2", "2-to-none"],
    )
    def test_a_moved_and_modified_chunk_resolves_like_its_two_cards(self, old, new, tmp_path):
        # Every block of the replacement went into the moved paragraph, so
        # Word's Accept All ran them together ("New1New2"). And every old
        # block kept a moved paragraph mark of its own that the modify left
        # standing, so Accept All kept an empty paragraph for each old block
        # but the last, and for the last one too when the replacement has no
        # block at all. Accepting the two cards gives none of these.
        ada, bot = aim.human("ada"), aim.agent("model-x")
        doc = aim.new_document(title="Moved and rewritten")
        before = "".join(f"<p>{text}</p>" for text in old)
        markups = ("<p>Alpha</p>", f"<div>{before}</div>", "<p>Beta</p>", "<p>Gamma</p>")
        with doc.batch():
            _, moved, beta, _ = (doc.add_chunk(m, author=ada).id for m in markups)
        doc.propose_move(moved, container="body", after=beta, author=ada)
        after = "".join(f"<p>{text}</p>" for text in new)
        doc.propose_modify(moved, f'<div data-aim="{moved}">{after}</div>', author=bot)
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
        "build",
        [
            lambda: _lettered_lane(
                "ABCD", [("move", "A", "B"), ("move", "B", None), ("move", "C", None)]
            ),
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
            # B's move and deletion: N1 and N2 share an anchor that moves
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
            lambda: _foreign_move_doc("pooled"),
            lambda: _backward_chained_move_doc(),
            lambda: _foreign_move_doc("chained"),
        ],
        ids=[
            "moves",
            "adds",
            "an-anchor-moved",
            "a-pooled-block-moved",
            "a-backward-chain",
            "a-chained-block-moved",
        ],
    )
    def test_skipping_the_replay_never_changes_the_export(self, build, monkeypatch, tmp_path):
        # The replay is skipped only for a pool whose card order it would
        # keep, so forcing it on every pool must leave the document as it
        # was. In the last case First and Later hold no moved block, yet a
        # hand-written move takes Chained, a card chained on First, in front
        # of B, and Later, made after that move, follows it: the check must
        # look down the chains too.
        doc = build()
        skipped = tmp_path / "skipped.docx"
        aim.to_docx(doc, skipped, pending="tracked")
        monkeypatch.setattr(export_docx._Exporter, "_lands_in_card_order", lambda *_: False)
        replayed = tmp_path / "replayed.docx"
        aim.to_docx(doc, replayed, pending="tracked")
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


def _lettered_lane(names: str, lane: list[tuple]) -> aim.AimDocument:
    """One paragraph per letter of *names*, then *lane*'s cards in order:
    ``("move", X, after)``, ``("add", text, after)``, ``("delete", X)`` and
    ``("modify", X)`` (X's text becomes X2, proposed by another author). An
    anchor is a letter, an earlier add's text, or None for the first
    position."""
    ada, bot = aim.human("ada"), aim.agent("model-x")
    doc = aim.new_document(title="Lane")
    with doc.batch():
        ids = {name: doc.add_chunk(f"<p>{name}</p>", author=ada).id for name in names}
    for action, name, *after in lane:
        anchor = ids[after[0]] if after and after[0] is not None else None
        if action == "move":
            doc.propose_move(ids[name], container="body", after=anchor, author=ada)
        elif action == "add":
            ids[name] = doc.propose_add(f"<p>{name}</p>", after=anchor, author=ada).id
        elif action == "delete":
            doc.propose_delete(ids[name], author=ada)
        else:
            markup = f'<p data-aim="{ids[name]}">{name}2</p>'
            doc.propose_modify(ids[name], markup, author=bot)
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
