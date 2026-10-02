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

import io
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
from aimformat.convert import AimImportWarning, import_docx  # noqa: E402
from aimformat.convert._docx_revisions import AUTHOR_CAP, clean, resolve_view  # noqa: E402
from aimformat.errors import AimError, ParseError  # noqa: E402
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
