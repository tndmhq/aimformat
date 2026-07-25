"""Ingestion fidelity against whole, real-world documents.

Two of these fixtures — ``legal-addendum.docx`` and ``sample3.docx`` — were
written and saved by Word, and carry the markup only Word emits: multilevel
clause numbering hung off HeadingN styles, grouped VML/DrawingML artwork,
footnotes, highlighted form fields. The other three are published sample
documents generated through python-docx; they are NOT Word output (their
``dc:creator`` says so), but they ride Word's own built-in table styles and
section machinery, which is the part they exist to exercise.

The distinction matters when reading a failure: a break in the first two is
evidence about real Word markup, a break in the last three is evidence about
table styles, banding and section breaks specifically.

Each assertion below corresponds to a fidelity bug these documents exposed,
so they are regression locks, not descriptions:

* clause numbers ("1.1.1") are drawn by Word from numbering.xml and exist
  nowhere in the text — they have to be materialised or the contract loses
  its structure;
* those HeadingN styles resolve to plain body text, so emitting <h2>-<h4>
  rendered whole contracts at heading size and weight;
* a logo row is a *group* of pictures, which dpc's typed model cannot see at
  all, and whose members are sized in the group's coordinate space — missing
  either fact means no logo, or one blown up to its full pixel size.

Provenance and redaction of every fixture: ``tests/fixtures/docxs/README.md``.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

import aimformat as aim

pytest.importorskip("docx_parser_converter")

FIXTURES = Path(__file__).parent / "fixtures" / "docxs"
LEGAL = FIXTURES / "legal-addendum.docx"
SAMPLE = FIXTURES / "sample3.docx"
TABLES = FIXTURES / "tables-merged.docx"
COLUMNS = FIXTURES / "multi-column.docx"
REPORT = FIXTURES / "long-report.docx"


@pytest.fixture(scope="module")
def legal() -> aim.AimDocument:
    return aim.from_docx(LEGAL)


@pytest.fixture(scope="module")
def legal_body(legal: aim.AimDocument) -> str:
    return legal.dumps()


@pytest.fixture(scope="module")
def sample() -> aim.AimDocument:
    return aim.from_docx(SAMPLE)


@pytest.fixture(scope="module")
def tables() -> aim.AimDocument:
    return aim.from_docx(TABLES)


class TestRealDocumentsIngestCleanly:
    def test_both_parse_and_re_parse(self, legal_body, sample):
        # a document that imports but does not lint is not usable downstream
        aim.loads(legal_body)
        aim.loads(sample.dumps())

    def test_content_actually_arrives(self, legal, sample):
        assert len(legal.chunks) > 60
        assert len(sample.chunks) > 30


def _numbered_paragraphs(path: Path) -> list[tuple[int, int]]:
    """``(numId, ilvl)`` for every effectively-numbered paragraph in document
    order, read straight from the XML — including the numbering a paragraph
    inherits from its style, which is how legal templates carry it."""
    from lxml import etree

    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    z = zipfile.ZipFile(path)
    styles = etree.fromstring(z.read("word/styles.xml"))
    style_num: dict[str, tuple[str | None, str | None]] = {}
    based: dict[str, str] = {}
    for style in styles.iter(f"{w}style"):
        sid = style.get(f"{w}styleId")
        num_pr = style.find(f"{w}pPr/{w}numPr")
        if num_pr is not None:
            num = num_pr.find(f"{w}numId")
            lvl = num_pr.find(f"{w}ilvl")
            style_num[sid] = (
                num.get(f"{w}val") if num is not None else None,
                lvl.get(f"{w}val") if lvl is not None else None,
            )
        parent = style.find(f"{w}basedOn")
        if parent is not None:
            based[sid] = parent.get(f"{w}val")

    def inherited(sid: str | None) -> tuple[str | None, str | None]:
        seen: set[str] = set()
        while sid and sid not in seen:
            seen.add(sid)
            if sid in style_num:
                return style_num[sid]
            sid = based.get(sid)
        return (None, None)

    out: list[tuple[int, int]] = []
    for para in etree.fromstring(z.read("word/document.xml")).iter(f"{w}p"):
        num_pr = para.find(f"{w}pPr/{w}numPr")
        if num_pr is not None:
            num = num_pr.find(f"{w}numId")
            lvl = num_pr.find(f"{w}ilvl")
            num_id = num.get(f"{w}val") if num is not None else None
            ilvl = lvl.get(f"{w}val") if lvl is not None else "0"
        else:
            style = para.find(f"{w}pPr/{w}pStyle")
            num_id, ilvl = inherited(style.get(f"{w}val") if style is not None else None)
        if num_id is None or num_id == "0":
            continue  # numId 0 is OOXML for "numbering removed here"
        out.append((int(num_id), int(ilvl or 0)))
    return out


def render_outline_numbers(doc: aim.AimDocument) -> list[tuple[str, str]]:
    """``[(rendered number, text)]`` for every outline-numbered block, by
    running the counter arithmetic the generated stylesheet declares.

    Since v0.5 the number is NOT in the document — it is computed at render
    time, which is what makes it survive an edit. That leaves a test with no
    string to assert, so this stands in for the browser: same rules as the
    CSS (a level increments its own counter and zeroes every deeper one;
    ``num-restart`` sets it to 1), no engine involvement, so the two can
    actually disagree.
    """
    counters = [0] * 10
    out: list[tuple[str, str]] = []
    for chunk in doc.chunks:
        match = re.search(r'class="([^"]*)"', chunk.html)
        classes = (match.group(1) if match else "").split()
        level = next(
            (int(c.split("-")[1]) for c in classes if re.fullmatch(r"num-[1-9]", c)),
            None,
        )
        if level is None:
            continue
        if "num-restart" in classes:
            counters[level] = 1
        else:
            counters[level] += 1
        for deeper in range(level + 1, 10):
            counters[deeper] = 0
        prefix = re.search(r'data-aim-num-prefix="([^"]*)"', chunk.html)
        number = (
            prefix.group(1) + str(counters[level])
            if prefix
            else ".".join(str(counters[i]) for i in range(1, level + 1))
        )
        out.append((number, chunk.text))
    return out


class TestOutlineNumbering:
    """The legal document's whole structure is its numbering."""

    def test_every_level_is_numbered_dynamically(self, legal):
        # each level pinned on its own: a check for the deepest label alone
        # would not notice the top level going missing
        rendered = {number for number, _ in render_outline_numbers(legal)}
        for label in ("1", "1.1", "1.1.1", "1.1.2", "1.1.10.1"):
            assert label in rendered, f"number {label} lost"

    def test_the_numbers_are_not_written_into_the_text(self, legal, legal_body):
        # the point of v0.5: nothing stores the number, so an edit cannot
        # leave a stale one behind
        assert not any(re.match(r"^\d+(\.\d+)*\.?\s", c.text) for c in legal.chunks), (
            "a number is baked into the text"
        )
        assert 'class="num-' in legal_body

    def test_numbering_continues_across_word_num_instances(self, legal):
        # Word starts a fresh w:num whenever a list is interrupted (here the
        # nested 1.1.10.1/.2), so this sequence spans two numIds over one
        # abstract definition. Counting per instance restarted it at 1.1.1
        # mid-contract; the definitions must run 1.1.1 … 1.1.14 unbroken.
        seen = [n for n, _ in render_outline_numbers(legal) if re.fullmatch(r"1\.1\.\d+", n)]
        numbers = [int(n.rsplit(".", 1)[1]) for n in seen]
        assert numbers == list(range(1, len(numbers) + 1)), seen
        assert len(numbers) >= 14, "the definitions list is truncated"

    def test_the_rendered_numbers_are_the_ones_word_draws(self, legal):
        # The engine and the stylesheet are two independent implementations
        # of the same arithmetic — the engine walks numbering.xml, the CSS
        # counts classes. This checks they agree on the real document, which
        # is the only thing that makes the class emission trustworthy.
        from aimformat.convert._docx_seam import NumberingEngine, parse_docx

        parsed = parse_docx(str(LEGAL))
        oracle = NumberingEngine(
            zipfile.ZipFile(LEGAL).read("word/numbering.xml")
        )  # a second, untouched engine
        del parsed
        expected: list[str] = []
        for num_id, ilvl in _numbered_paragraphs(LEGAL):
            drawn = oracle.draw(num_id, ilvl)
            if drawn.level is not None:
                expected.append(drawn.label.rstrip("."))
        rendered = [n for n, _ in render_outline_numbers(legal)]
        assert rendered == expected, (
            f"stylesheet and engine disagree\n  css: {rendered[:8]}\n  word: {expected[:8]}"
        )

    def test_a_numbered_clause_keeps_its_text(self, legal):
        text = next(t for n, t in render_outline_numbers(legal) if n == "1.1.1")
        assert "Applicable Laws" in text

    def test_outline_numbering_survives_a_docx_round_trip(self, legal, tmp_path):
        # Since v0.5 the number is not in the text, so an exporter that
        # ignores the clause classes writes a contract with NO numbers at
        # all — worse than the baked labels it replaced. Word must get real
        # numbering back, and re-importing it must land on the same clauses.
        out = tmp_path / "clauses.docx"
        aim.to_docx(legal, str(out))
        back = aim.from_docx(out)
        assert [n for n, _ in render_outline_numbers(back)] == [
            n for n, _ in render_outline_numbers(legal)
        ]

    def test_each_restart_gets_its_own_numbering_instance(self, tmp_path):
        # A startOverride applies on an instance's FIRST use only, so two
        # restarts sharing one instance leave the second counting straight
        # on: 1 2 1 2 3 4 where the document says 1 2 1 2 1 2.
        doc = aim.new_document(title="Restarts")
        for i, cls in enumerate(["num-1", "num-1", "num-1 num-restart", "num-1"]):
            doc.add_chunk(f'<p class="{cls}">block {i}</p>', author=aim.external("t"))
        doc.add_chunk('<p class="num-1 num-restart">again</p>', author=aim.external("t"))
        out = tmp_path / "restarts.docx"
        aim.to_docx(doc, str(out))
        numbering = zipfile.ZipFile(out).read("word/numbering.xml").decode("utf-8")
        assert numbering.count("startOverride") == 2, "the restarts share an instance"

    def test_a_numbering_prefix_survives_export(self, tmp_path):
        # the literal has nowhere to live in Word but the level's lvlText, so
        # an exporter that ignores it turns "Article 1" into "1." — undoing
        # what the importer just preserved
        doc = aim.new_document(title="Prefixed")
        doc.add_chunk(
            '<h2 class="num-1" data-aim-num-prefix="Article ">Scope</h2>',
            author=aim.external("t"),
        )
        out = tmp_path / "prefixed.docx"
        aim.to_docx(doc, str(out))
        numbering = zipfile.ZipFile(out).read("word/numbering.xml").decode("utf-8")
        assert "Article %1" in numbering, "the prefix never reached the numbering"

    def test_the_exported_docx_carries_word_numbering(self, legal, tmp_path):
        # not literal text that merely looks like a number: w:numPr, so Word
        # itself renumbers the document after an edit
        out = tmp_path / "clauses.docx"
        aim.to_docx(legal, str(out))
        body = zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")
        assert "numPr" in body, "the export lost its numbering entirely"
        numbering = zipfile.ZipFile(out).read("word/numbering.xml").decode("utf-8")
        assert "multilevel" in numbering


class TestHeadingsAreOnlyRealHeadings:
    """HeadingN styles that resolve to body text must not be promoted — that
    rendered the entire contract oversized and bold."""

    def test_body_clauses_are_paragraphs(self, legal):
        promoted = [c for c in legal.chunks if c.tag in ("h2", "h3", "h4", "h5", "h6")]
        assert not promoted, [c.text[:40] for c in promoted[:5]]

    def test_the_genuinely_bold_headings_survive(self, legal):
        h1 = [c.text for c in legal.chunks if c.tag == "h1"]
        assert any("Definitions" in t for t in h1)

    def test_clause_bodies_carry_no_size_override(self, legal):
        # body text must inherit the theme, not a literal heading size
        clause = next(c for c in legal.chunks if "Applicable Laws" in c.text)
        assert "font-size" not in clause.html


class TestGroupedArtwork:
    """The title-page logo row: a group of pictures dpc cannot see."""

    def test_every_logo_is_recovered(self, legal):
        figures = [c for c in legal.chunks if c.tag == "figure"]
        assert len(figures) == 1, "the group should stay one visual unit"
        assert figures[0].html.count("<img") == 3

    def test_each_logo_is_sized_as_the_group_draws_it(self, legal):
        figure = next(c for c in legal.chunks if c.tag == "figure")
        widths = [int(w) for w in re.findall(r"width:(\d+)px", figure.html)]
        assert len(widths) == 3
        # scaled into the group's real width (~511px), not natural pixels
        assert all(40 <= w <= 320 for w in widths), widths
        assert sum(widths) <= 560, widths

    def test_images_are_embedded_not_referenced(self, legal):
        figure = next(c for c in legal.chunks if c.tag == "figure")
        assert figure.html.count("data:image/") == 3


class TestStylingSurvives:
    def test_justification_and_highlights(self, legal_body):
        assert 'class="text-justify"' in legal_body
        assert "<mark" in legal_body  # the yellow fill-in fields

    def test_inline_emphasis_is_local_not_blanket(self, legal, legal_body):
        # defined terms are bold inline; the document is not bold throughout
        assert "<strong>" in legal_body
        bolded = sum(1 for c in legal.chunks if c.html.count("<strong>") > 0)
        assert bolded < len(legal.chunks) * 0.75

    def test_source_theme_is_derived(self, legal):
        assert (legal.theme or {}).get("--aim-font-body")

    def test_pagination_intent_is_kept(self, legal):
        assert any(c.tag == "aim-page-break" for c in legal.chunks)


class TestSampleDocumentStructure:
    """sample3.docx is an accessibility sample: headings, lists, tables,
    inline images with their alt text."""

    def test_heading_hierarchy(self, sample):
        tags = [c.tag for c in sample.chunks]
        assert "h1" in tags and "h2" in tags

    def test_lists_and_tables(self, sample):
        body = sample.dumps()
        assert "<ul" in body and "<ol" in body
        assert any(c.tag == "tr" for c in sample.chunks)

    def test_images_keep_their_alt_text(self, sample):
        body = sample.dumps()
        assert body.count("data:image/") >= 2
        assert 'alt="' in body


class TestTableStylesCarryTheLook:
    """Word tables usually carry their whole appearance in a table STYLE —
    a shaded header band, banded body rows, recoloured header text — not on
    the cells. Reading only w:tcPr/w:shd rendered every such table flat."""

    def test_header_band_is_shaded_and_legible(self, tables):
        rows = [c for c in tables.chunks if c.tag == "tr"]
        header = next((c for c in rows if "#4f81bd" in c.html), None)
        assert header is not None, "the styled header band was lost"
        # a dark fill without its light text is worse than no fill at all
        assert "color:#ffffff" in header.html

    def test_body_rows_alternate(self, tables):
        rows = [c for c in tables.chunks if c.tag == "tr"]
        banded = [i for i, c in enumerate(rows) if "#d3dfee" in c.html]
        assert len(banded) >= 4, "row banding was lost"
        # ALTERNATING, not merely "some": no two banded rows may be adjacent
        # within a table. Shading every row would otherwise pass.
        assert not any(i + 1 in banded for i in banded), banded

    def test_the_band_does_not_paint_every_row(self, tables):
        # the wholeTable look must be what the style declares DIRECTLY: a
        # subtree search hoisted the dark firstRow band onto every row
        rows = [c for c in tables.chunks if c.tag == "tr"]
        dark = [c for c in rows if "#4f81bd" in c.html]
        assert len(dark) < len(rows) / 2, (
            f"{len(dark)}/{len(rows)} rows carry the header band — it leaked"
        )

    def test_merged_cells_survive(self, tables):
        body = tables.dumps()
        assert 'rowspan="' in body


class TestHardLayoutsDegradeSafely:
    """Multi-column sections and a long report: .aim has no column model, so
    the requirement is that nothing is LOST or corrupted."""

    def test_multi_column_loses_no_text_and_keeps_order(self):
        # .aim has no column model, so the requirement is not "looks the
        # same" but "says the same, in the same order": every source run of
        # text present, in document order.
        import zipfile

        source = zipfile.ZipFile(COLUMNS).read("word/document.xml").decode("utf-8")
        runs = [t for t in re.findall(r"<w:t[^>]*>([^<]+)</w:t>", source) if t.strip()]
        doc = aim.from_docx(COLUMNS)
        aim.loads(doc.dumps())
        body = "\n".join(c.text for c in doc.chunks)
        missing = [t for t in runs if t.strip() not in body]
        assert not missing, f"text lost from the columns: {missing[:5]}"
        cursor, out_of_order = 0, []
        for t in runs:
            found = body.find(t.strip(), cursor)
            if found < 0:
                out_of_order.append(t)
            else:
                cursor = found
        assert not out_of_order, f"column text reordered: {out_of_order[:5]}"

    def test_long_report_structure(self):
        doc = aim.from_docx(REPORT)
        aim.loads(doc.dumps())
        tags = {c.tag for c in doc.chunks}
        assert {"h1", "h2", "tr", "li"} <= tags
        assert len(doc.chunks) > 500

    def test_every_fixture_round_trips_without_losing_content(self, tmp_path):
        # an exporter that wrote an empty document would pass a
        # "does not raise" check, so compare what comes back
        for name in (LEGAL, SAMPLE, TABLES, COLUMNS, REPORT):
            doc = aim.from_docx(name)
            out = tmp_path / f"{name.stem}.docx"
            aim.to_docx(doc, str(out))
            back = aim.from_docx(out)
            before = [c.text.strip() for c in doc.chunks if c.text.strip()]
            after = {c.text.strip() for c in back.chunks if c.text.strip()}
            kept = sum(1 for t in before if t in after)
            assert kept >= len(before) * 0.9, (
                f"{name.stem}: only {kept}/{len(before)} texts survived export"
            )


class TestKnownGaps:
    """Content we do NOT carry yet, recorded rather than ignored.

    These are `xfail`, not skips or inverted assertions: they state the
    behaviour we want, so they do not block CI today and they start passing
    — visibly, as XPASS — the moment support lands. An inverted assertion
    ("footnote text is absent") would instead have to be deleted by whoever
    implements it, and a silent gap is how a document quietly loses text.

    The gap itself is documented in the importer's module docstring.
    """

    @pytest.mark.xfail(
        reason="footnotes/endnotes are not read yet — word/footnotes.xml is a "
        "separate part the walk never opens",
        strict=False,
    )
    def test_footnote_text_survives(self, legal_body):
        # the legal fixture's footnotes carry substantive drafting guidance
        # ("Parties to consider whether to adopt a group-to-group contracting
        # structure…"), referenced 11 times from the body
        assert "group-to-group contracting structure" in legal_body

    @pytest.mark.xfail(
        reason="headers/footers are not read yet; page-number fields have no "
        "meaning in a format with no page furniture",
        strict=False,
    )
    def test_footer_reference_survives(self, legal_body):
        assert "REF/DOC/00000001.1" in legal_body

    def test_the_loss_is_bounded_to_those_parts(self, legal, legal_body):
        # whatever we drop, the BODY must still be complete: this is the
        # guard that a future footnote implementation cannot regress into
        # dropping body text instead
        assert len(legal.chunks) > 60
        assert "Applicable Laws" in legal_body
