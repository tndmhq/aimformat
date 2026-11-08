"""The compact table of contents and the section read (spec §8.1).

One entry per section — ``{chunk_count, id, level, title, word_count}`` — and
``AimDocument.section(id)`` returning that section's ids, text and markup.
A section opens at a top-level heading chunk or a slide and runs to the next
one of the same or a higher level, so it includes its subsections. Every save
of a document declared 0.6 or later builds the TOC when the body has a heading
or a slide; a document declared below 0.6 keeps its meta block as it is.
"""

from __future__ import annotations

import dataclasses
import re
from time import perf_counter

import pytest

import aimformat as aim
from aimformat.canonical import canonical_json
from conftest import BOT, ME, ts

META_BLOCK = re.compile(r'<script type="application/aim-meta\+json">\n(.*?)\n</script>', re.S)

SLIDE = (
    '<aim-slide data-aim-container="s1" style="width:960px; height:540px">'
    '<h2 data-aim="st" class="text-5xl" style="left:60px; top:50px; width:600px">'
    "Deck title</h2>"
    '<p data-aim="sp" style="left:60px; top:150px; width:600px">Body<br>text</p>'
    "</aim-slide>"
)
UNTITLED_SLIDE = (
    '<aim-slide data-aim-container="s2" style="width:960px; height:540px">'
    '<p data-aim="sx" style="left:60px; top:50px; width:600px">No heading</p>'
    '<p data-aim="sy" style="left:60px; top:150px; width:600px">here</p>'
    "</aim-slide>"
)


def _doc(*markups: str) -> aim.AimDocument:
    doc = aim.new_document(title="TOC fixture")
    with doc.batch():
        for i, markup in enumerate(markups):
            doc.add_chunk(markup, author=BOT, at=ts(i))
    return doc


def _with_meta(text: str, meta: dict) -> str:
    """*text* with its meta block's object replaced by *meta*."""
    block = canonical_json(meta)
    assert META_BLOCK.search(text), "no meta block to replace"
    return META_BLOCK.sub(lambda m: m.group(0).replace(m.group(1), block), text, count=1)


def _meta_codes(text: str) -> set[tuple[str, str]]:
    return {(f.code, f.level) for f in aim.lint_text(text) if f.code[0] == "M"}


class TestEntries:
    def test_an_entry_is_compact_and_stored_with_sorted_keys(self):
        doc = _doc('<h1 data-aim="h">Intro</h1>', '<p data-aim="p">Two words.</p>')
        (entry,) = doc.outline()
        assert entry == {"chunk_count": 2, "id": "h", "level": 1, "title": "Intro", "word_count": 3}
        assert all(type(entry[k]) is int for k in ("chunk_count", "level", "word_count"))
        assert type(entry["id"]) is str and type(entry["title"]) is str
        # canonical JSON sorts the keys: the stored entry reads in this order
        stored = '{"chunk_count":2,"id":"h","level":1,"title":"Intro","word_count":3}'
        assert f'"toc":[{stored}]' in doc.dumps()

    def test_sections_include_their_subsections(self):
        doc = _doc(
            '<h1 data-aim="a">Alpha</h1>',
            '<p data-aim="a1">One two.</p>',
            '<h2 data-aim="b">Beta</h2>',
            '<p data-aim="b1">Three.</p>',
            '<h3 data-aim="c">Gamma</h3>',
            '<p data-aim="c1">Four five six.</p>',
            '<h2 data-aim="d">Delta</h2>',
            '<p data-aim="d1">Seven.</p>',
            '<h1 data-aim="e">Epsilon</h1>',
            '<p data-aim="e1">Eight nine.</p>',
        )
        got = [
            (e["id"], e["level"], e["title"], e["chunk_count"], e["word_count"])
            for e in doc.outline()
        ]
        assert got == [
            ("a", 1, "Alpha", 8, 11),
            ("b", 2, "Beta", 4, 6),
            ("c", 3, "Gamma", 2, 4),  # ends at the h2 that follows it
            ("d", 2, "Delta", 2, 2),
            ("e", 1, "Epsilon", 2, 3),
        ]

    def test_content_before_the_first_heading_is_an_untitled_level_1_entry(self):
        doc = _doc(
            '<p data-aim="p0">Before any heading.</p>',
            '<h3 data-aim="h">Deep first heading</h3>',
            '<p data-aim="p1">After.</p>',
        )
        assert doc.outline() == [
            # it ends at the first heading whatever that one's level
            {"chunk_count": 1, "id": "p0", "level": 1, "title": "", "word_count": 3},
            {
                "chunk_count": 2,
                "id": "h",
                "level": 3,
                "title": "Deep first heading",
                "word_count": 4,
            },
        ]

    def test_the_untitled_entry_is_named_by_the_first_construct_even_a_container(self):
        doc = _doc(
            '<ul data-aim-container="l"><li data-aim="i1">One</li><li data-aim="i2">Two</li></ul>',
            '<h1 data-aim="h">Title</h1>',
        )
        assert doc.outline()[0] == {
            "chunk_count": 2,
            "id": "l",
            "level": 1,
            "title": "",
            "word_count": 2,
        }

    def test_a_slide_opens_a_level_1_section_titled_by_its_first_heading(self):
        doc = _doc(
            '<h2 data-aim="h">Before</h2>',
            SLIDE,
            '<p data-aim="after">After the slide.</p>',
            UNTITLED_SLIDE,
        )
        assert doc.outline() == [
            {"chunk_count": 1, "id": "h", "level": 2, "title": "Before", "word_count": 1},
            # top-level content after a slide joins its section, like a heading's
            {"chunk_count": 3, "id": "s1", "level": 1, "title": "Deck title", "word_count": 7},
            {"chunk_count": 2, "id": "s2", "level": 1, "title": "", "word_count": 3},
        ]

    def test_a_heading_inside_a_chunk_opens_no_section(self):
        doc = _doc(
            '<h1 data-aim="h">Report</h1>',
            '<section data-aim="s"><h2>Scope</h2><p>Everything shipped.</p></section>',
        )
        assert [e["id"] for e in doc.outline()] == ["h"]
        assert doc.outline()[0]["chunk_count"] == 2

    def test_a_numbered_clause_opens_no_section(self):
        # P8: outline numbering (num-1 … num-9 on a paragraph) is a clause,
        # not a heading; whether clauses enter the outline is not decided here
        doc = _doc(
            '<h1 data-aim="h">Terms</h1>',
            '<p data-aim="c1" class="num-1">First clause.</p>',
            '<p data-aim="c2" class="num-2">A subclause.</p>',
        )
        assert [(e["id"], e["chunk_count"]) for e in doc.outline()] == [("h", 3)]

    def test_a_title_is_the_visible_text(self):
        doc = _doc('<h1 data-aim="h">Annual<br>report <em>2026</em></h1>')
        assert doc.outline()[0]["title"] == "Annual report 2026"

    def test_a_body_without_constructs_has_no_entries(self):
        assert aim.new_document(title="Empty").outline() == []


class TestCounts:
    def test_items_and_rows_count_one_each_a_run_once_and_containers_nothing(self):
        doc = _doc(
            '<h1 data-aim="h">Lists</h1>',
            '<ul data-aim-container="l"><li data-aim="i1">One</li>'
            '<li data-aim="i2">Two, part one</li><li data-aim="i2">part two</li></ul>',
            '<table data-aim-container="t"><thead><tr data-aim="r0"><th>K</th><th>V</th></tr>'
            '</thead><tbody><tr data-aim="r1"><td>alpha</td><td>1</td></tr></tbody></table>',
        )
        (entry,) = doc.outline()
        assert entry["chunk_count"] == 5  # h, i1, i2 (a run), r0, r1
        assert entry["word_count"] == 1 + 1 + 3 + 2 + 2 + 2

    @pytest.mark.parametrize(
        ("markup", "words"),
        [
            ('<p data-aim="x">Ac<strong>me</strong> Corp</p>', 2),  # inline markup joins
            ('<p data-aim="x">one<br>two</p>', 2),  # a break separates
            ('<p data-aim="x">  spaced   out\n  text </p>', 3),
            ('<blockquote data-aim="x"><p>one</p><p>two</p></blockquote>', 2),
            ('<pre data-aim="x">line one\nline two</pre>', 4),
            (
                '<figure data-aim="x"><img alt="a long alt text" src="https://example.com/a.png">'
                "<figcaption>Figure one.</figcaption></figure>",
                2,
            ),
            (
                '<figure data-aim="x"><svg aria-label="Revenue chart">'
                '<use href="#asset-aaaaaaaaaaaa"/></svg></figure>',
                0,
            ),
            ('<hr data-aim="x">', 0),
            ('<aim-page-break data-aim="x"></aim-page-break>', 0),
            (
                '<table data-aim-container="x"><tbody><tr data-aim="r"><td>alpha</td><td>1</td>'
                "</tr></tbody></table>",
                2,
            ),
        ],
        ids=[
            "inline",
            "br",
            "whitespace",
            "blocks",
            "pre",
            "img-and-caption",
            "svg",
            "hr",
            "page-break",
            "cells",
        ],
    )
    def test_word_count(self, markup, words):
        doc = _doc('<h1 data-aim="h">T</h1>', markup)
        assert doc.outline()[0]["word_count"] == 1 + words


class TestSection:
    @pytest.fixture
    def doc(self) -> aim.AimDocument:
        return _doc(
            '<h1 data-aim="h">Report</h1>',
            '<p data-aim="p">Some <em>words</em> here.</p>',
            '<ul data-aim-container="l"><li data-aim="i1">One</li>'
            '<li data-aim="i2">Two</li><li data-aim="i2">and more</li></ul>',
            '<hr data-aim="rule">',
            '<h1 data-aim="next">Next</h1>',
        )

    def test_section_reads_ids_text_and_markup(self, doc):
        sec = doc.section("h")
        assert isinstance(sec, aim.Section)
        assert (sec.id, sec.level, sec.title) == ("h", 1, "Report")
        assert sec.ids == ("h", "p", "l", "i1", "i2", "rule")  # a container before its items
        # one line per chunk, in document order (an empty chunk is an empty line)
        assert sec.text == "Report\nSome words here.\nOne\nTwo and more\n"
        assert sec.word_count == len(sec.text.split()) == 8
        assert sec.chunk_count == len(sec.text.split("\n")) == 5
        assert sec.entry() == doc.outline()[0]

    def test_section_markup_is_the_files_own_lines(self, doc):
        html = doc.section("h").html.split("\n")
        lines = doc.dumps().split("\n")
        start = lines.index(html[0])
        assert lines[start : start + len(html)] == html
        assert len(html) == 4 and html[-1] == '<hr data-aim="rule">'

    def test_section_is_frozen(self, doc):
        with pytest.raises(dataclasses.FrozenInstanceError):
            doc.section("h").title = "Other"  # type: ignore[misc]

    def test_the_untitled_section_and_a_slide_are_read_by_their_ids(self):
        doc = _doc('<p data-aim="p0">Preface.</p>', SLIDE)
        assert doc.section("p0").entry() == doc.outline()[0]
        slide = doc.section("s1")
        assert slide.ids == ("s1", "st", "sp")
        assert slide.text == "Deck title\nBody text"
        assert slide.entry() == doc.outline()[1]

    @pytest.mark.parametrize("sid", ["nope", "p", "l", "i1", "rule"])
    def test_an_id_that_opens_no_section_is_not_found(self, doc, sid):
        with pytest.raises(aim.TargetNotFound):
            doc.section(sid)

    def test_a_slides_inner_heading_opens_no_section(self):
        with pytest.raises(aim.TargetNotFound):
            _doc(SLIDE).section("st")

    def test_pending_proposals_are_not_part_of_a_section(self, doc):
        doc.propose_add(
            "<p>Pending words here.</p>", author=BOT, after="p", explanation="More.", at=ts(20)
        )
        assert doc.section("h").ids == ("h", "p", "l", "i1", "i2", "rule")
        assert doc.section("h").word_count == 8


class TestSave:
    def test_a_save_builds_the_toc_when_the_body_has_a_heading(self):
        doc = _doc('<h1 data-aim="h">Title</h1>', '<p data-aim="p">Body.</p>')
        assert doc.meta is None
        text = doc.dumps()
        saved = aim.loads(text)
        assert saved.meta == {"toc": saved.outline(), "toc_doc_hash": saved.doc_hash}
        assert saved.toc_is_fresh()
        assert aim.lint_text(text) == []

    def test_a_slide_is_enough(self):
        saved = aim.loads(_doc(SLIDE).dumps())
        assert [e["id"] for e in saved.meta["toc"]] == ["s1"]

    def test_a_body_without_a_heading_or_slide_gets_no_toc(self):
        doc = _doc('<p data-aim="p">Only prose.</p>', '<p data-aim="q">More prose.</p>')
        assert aim.loads(doc.dumps()).meta is None

    def test_a_save_keeps_the_toc_fresh(self):
        doc = _doc('<h1 data-aim="h">Title</h1>', '<p data-aim="p">Body.</p>')
        doc.dumps()
        doc.modify_chunk("p", '<p data-aim="p">Body with more words.</p>', author=ME, at=ts(9))
        saved = aim.loads(doc.dumps())
        assert saved.meta["toc"][0]["word_count"] == 5
        assert saved.toc_is_fresh()

    def test_a_summary_is_kept_beside_the_toc(self):
        doc = _doc('<h1 data-aim="h">Title</h1>')
        doc.set_summary("A title.", model="m")
        meta = aim.loads(doc.dumps()).meta
        assert meta["summary"]["text"] == "A title." and meta["toc"][0]["id"] == "h"

    def test_a_save_rewrites_a_toc_list_holding_a_malformed_entry(self):
        doc = _doc('<h1 data-aim="h">Title</h1>', '<p data-aim="p">Body.</p>')
        text = _with_meta(doc.dumps(), {"toc": [{"level": 1}], "toc_doc_hash": doc.doc_hash})
        assert ("M003", "error") in _meta_codes(text)
        saved = aim.loads(aim.loads(text).dumps())
        assert saved.meta["toc"] == saved.outline() and saved.toc_is_fresh()

    @pytest.mark.parametrize(
        "raw", ['{"toc":"h","toc_doc_hash":"x"}', "{not json"], ids=["not-a-list", "not-json"]
    )
    def test_a_save_leaves_a_cache_it_cannot_read_to_lint(self, raw):
        text = META_BLOCK.sub(
            lambda m: m.group(0).replace(m.group(1), raw),
            _doc('<h1 data-aim="h">Title</h1>').dumps(),
            count=1,
        )
        assert aim.loads(text).dumps() == text
        assert ("M003", "error") in _meta_codes(text)

    def test_an_import_that_loses_every_heading_drops_its_toc(self):
        # Whether a document carries a TOC depends on its body, not on its
        # history: once no heading or slide is left, a save removes the TOC,
        # and the meta block with it when nothing is left in it (an empty
        # block is M004). The same body imported fresh stores no TOC either.
        pytest.importorskip("markdown_it")
        doc = aim.from_markdown("# Title\n\nSome text.\n\n## Sub\n\nMore text.\n")
        assert doc.meta["toc"] and doc.toc_is_fresh()
        headings = [c.id for c in doc.chunks if c.tag in ("h1", "h2")]
        assert len(headings) == 2
        for i, heading in enumerate(headings):
            doc.delete_chunk(heading, author=ME, at=ts(9 + i))
        text = doc.dumps()
        assert "application/aim-meta+json" not in text
        saved = aim.loads(text)
        assert saved.meta is None and not saved.toc_is_fresh()
        assert [f for f in aim.lint_text(text) if f.level == "error" or f.code[0] == "M"] == []
        assert aim.from_markdown("Some text.\n\nMore text.\n").meta is None
        assert saved.dumps() == text

    def test_a_summary_stays_when_the_toc_goes(self):
        pytest.importorskip("markdown_it")
        doc = aim.from_markdown("# Title\n\nSome text.\n")
        heading = next(c.id for c in doc.chunks if c.tag == "h1")
        doc.delete_chunk(heading, author=ME, at=ts(9))
        doc.set_summary("Some text.", model="m")
        assert set(doc.meta) == {"summary", "toc", "toc_doc_hash"}  # the TOC is now stale
        saved = aim.loads(doc.dumps())
        assert set(saved.meta) == {"summary"}
        assert saved.meta["summary"]["text"] == "Some text."
        assert _meta_codes(saved.dumps()) == set()

    def test_a_meta_block_left_without_summary_or_toc_goes(self):
        # fields the format does not define cannot keep the block: without a
        # summary or a TOC it is M004, so it goes with the TOC
        doc = _doc('<h1 data-aim="h">Title</h1>', '<p data-aim="p">Body.</p>')
        doc.dumps()
        doc._write_meta({**doc.meta, "x_vendor": 1})
        doc.delete_chunk("h", author=ME, at=ts(9))
        text = doc.dumps()
        assert aim.loads(text).meta is None
        assert _meta_codes(text) == set()

    def test_a_body_with_no_heading_left_and_no_toc_keeps_its_meta_block(self):
        # nothing to remove: a summary-only block is left exactly as it is
        doc = _doc('<p data-aim="p">Only prose.</p>')
        doc.set_summary("Prose.", model="m")
        block = doc._state.script("meta").raw
        assert aim.loads(doc.dumps())._state.script("meta").raw == block


class TestGenerateToc:
    """generate_toc() stores what a save would: with no heading or slide in
    the body, no TOC."""

    def test_a_body_without_a_heading_or_slide_stores_no_toc(self):
        # It stored one untitled entry, which the next save removed: what the
        # cache held depended on whether the caller saved after the call.
        doc = _doc('<p data-aim="p">Only prose.</p>', '<p data-aim="q">More prose.</p>')
        assert doc.generate_toc() == []
        assert doc.meta is None
        assert "application/aim-meta+json" not in doc.dumps()

    @pytest.mark.parametrize(
        "extra",
        [{}, {"summary": {"model": "m", "text": "Body."}}, {"x_vendor": 1}],
        ids=["toc-only", "with-summary", "with-unknown-field"],
    )
    def test_a_stored_toc_goes_once_no_heading_is_left_as_on_save(self, extra):
        # the same body saved instead: a save removes the TOC, and the meta
        # block with it when no summary is left (such a block is M004)
        docs = [_doc('<h1 data-aim="h">Title</h1>', '<p data-aim="p">Body.</p>') for _ in "ab"]
        for doc in docs:
            doc.generate_toc()
            doc._write_meta({**doc.meta, **extra})
            doc.delete_chunk("h", author=ME, at=ts(9))
        called, saved = docs
        assert called.generate_toc() == []
        assert called.meta == aim.loads(saved.dumps()).meta
        assert called.meta is None or set(called.meta) == {"summary"}
        assert _meta_codes(called.dumps()) == set()

    def test_a_slide_is_enough(self):
        doc = _doc(SLIDE)
        assert [e["id"] for e in doc.generate_toc()] == ["s1"]
        assert doc.meta["toc"] == doc.outline() and doc.toc_is_fresh()


class TestOldShape:
    """A TOC in the pre-0.6 shape ``{title, level, chunks}``."""

    @staticmethod
    def _old(doc: aim.AimDocument) -> str:
        text = doc.dumps()
        entries = [{"chunks": ["h", "p"], "level": 1, "title": "Title"}]
        return _with_meta(text, {"toc": entries, "toc_doc_hash": doc.doc_hash})

    def test_at_0_6_it_is_stale_even_with_a_matching_hash(self):
        text = self._old(_doc('<h1 data-aim="h">Title</h1>', '<p data-aim="p">Body.</p>'))
        assert not aim.loads(text).toc_is_fresh()
        assert _meta_codes(text) == {("M005", "warning")}
        assert not [f for f in aim.lint_text(text) if f.level == "error"]

    def test_at_0_6_a_save_rewrites_it(self):
        text = self._old(_doc('<h1 data-aim="h">Title</h1>', '<p data-aim="p">Body.</p>'))
        saved = aim.loads(aim.loads(text).dumps())
        assert saved.meta["toc"] == saved.outline() and saved.toc_is_fresh()
        assert _meta_codes(saved.dumps()) == set()


class TestBelow06:
    """P4: a save never touches the meta block of a document declared below 0.6."""

    @staticmethod
    def _declared_0_5() -> aim.AimDocument:
        doc = _doc('<h1 data-aim="h">Title</h1>', '<p data-aim="p">Body.</p>')
        doc._state.set_spec_version("0.5")
        return doc

    def test_a_save_builds_no_toc(self):
        assert aim.loads(self._declared_0_5().dumps()).meta is None

    def test_a_save_leaves_the_meta_block_untouched(self):
        doc = self._declared_0_5()
        meta = {
            "summary": {"model": "m", "text": "A title and a body."},
            "toc": [{"chunks": ["h", "p"], "level": 1, "title": "Title"}],
        }
        doc._write_meta(meta)
        block = f'<script type="application/aim-meta+json">\n{canonical_json(meta)}\n</script>'
        doc.modify_chunk("h", '<h1 data-aim="h">Renamed</h1>', author=ME, at=ts(9))
        text = doc.dumps()
        assert block in text
        # the old shape is that version's own: no finding
        assert _meta_codes(text) == set()

    def test_generate_toc_refuses_the_compact_toc(self):
        # it wrote a TOC-only block in the 0.6 shape, which that version's
        # tools reject (M004) and which no save below 0.6 refreshes
        doc = self._declared_0_5()
        with pytest.raises(aim.InvalidOperation, match=r"declared 0\.6 or later \(spec §8\.1\)"):
            doc.generate_toc()
        assert doc.meta is None
        doc.set_summary("A title and a body.", model="m")
        before = doc._state.script("meta").raw
        with pytest.raises(aim.InvalidOperation):
            doc.generate_toc()
        assert doc._state.script("meta").raw == before

    @pytest.mark.parametrize(
        "meta",
        [
            {"toc": [{"chunks": ["h", "p"], "level": 1, "title": "Title"}]},
            {"toc": [{"chunk_count": 2, "id": "h", "level": 1, "title": "Title", "word_count": 3}]},
            {},
        ],
        ids=["old-shape-toc-only", "compact-toc-only", "empty"],
    )
    def test_a_meta_block_without_a_summary_is_M004(self, meta):
        # v0.5 required a summary, whatever else the block holds
        doc = self._declared_0_5()
        doc._write_meta(meta)
        findings = [f for f in aim.lint_text(doc.dumps()) if f.code[0] == "M"]
        assert [(f.code, f.level) for f in findings] == [("M004", "error")]
        assert "summary" in findings[0].message and "0.6" in findings[0].message

    @pytest.mark.parametrize(
        "entry",
        [
            {"level": 1},
            {"chunks": "h", "level": "1", "title": "Title"},
            {"chunk_count": 2, "id": "h", "level": 1, "title": "Title", "word_count": "3"},
            "h",
        ],
        ids=["no-title", "chunks-not-list", "compact-string-count", "str"],
    )
    def test_entries_get_no_shape_finding(self, entry):
        # the pre-0.6 shape is that version's own: lint checks no entry
        # shape below 0.6, where no save would ever rewrite the entries
        doc = self._declared_0_5()
        doc._write_meta({"summary": {"model": "m", "text": "t"}, "toc": [entry]})
        assert _meta_codes(doc.dumps()) == set()

    def test_a_toc_that_is_not_a_list_is_still_M003(self):
        # not an entry-shape finding: v0.5 defined the TOC as a list too
        doc = self._declared_0_5()
        doc._write_meta({"summary": {"model": "m", "text": "t"}, "toc": "h"})
        assert _meta_codes(doc.dumps()) == {("M003", "error")}


class TestLint:
    @staticmethod
    def _doc() -> aim.AimDocument:
        return _doc('<h1 data-aim="h">Title</h1>', '<p data-aim="p">Body.</p>')

    def test_a_fresh_compact_toc_is_clean(self):
        assert _meta_codes(self._doc().dumps()) == set()

    @pytest.mark.parametrize(
        "entry",
        [
            {"chunk_count": 2, "id": "h", "level": 1, "title": "Title", "word_count": "2"},
            {"chunk_count": 2, "level": 1, "title": "Title", "word_count": 2},
            {"chunk_count": 2, "id": "h", "level": 7, "title": "Title", "word_count": 2},
            {"chunk_count": True, "id": "h", "level": 1, "title": "Title", "word_count": 2},
            {"chunk_count": -1, "id": "h", "level": 1, "title": "Title", "word_count": 2},
            {"chunks": "h", "level": 1, "title": "Title"},
            "h",
        ],
        ids=[
            "string-count",
            "no-id",
            "level-7",
            "bool-count",
            "negative",
            "chunks-not-list",
            "str",
        ],
    )
    def test_a_malformed_entry_is_M003(self, entry):
        doc = self._doc()
        text = _with_meta(doc.dumps(), {"toc": [entry], "toc_doc_hash": doc.doc_hash})
        assert {f.code for f in aim.lint_text(text) if f.level == "error"} == {"M003"}

    def test_counts_are_informative(self):
        # a writer that counts differently is not stale: only the hash decides
        doc = self._doc()
        entry = {"chunk_count": 99, "id": "h", "level": 1, "title": "Title", "word_count": 0}
        text = _with_meta(doc.dumps(), {"toc": [entry], "toc_doc_hash": doc.doc_hash})
        assert _meta_codes(text) == set()
        assert aim.loads(text).toc_is_fresh()

    def test_unknown_entry_fields_are_ignored(self):
        doc = self._doc()
        entry = {**doc.outline()[0], "x_vendor": 1}
        text = _with_meta(doc.dumps(), {"toc": [entry], "toc_doc_hash": doc.doc_hash})
        assert _meta_codes(text) == set()

    @pytest.mark.parametrize("value", ["n/a", 3, None], ids=["str", "int", "null"])
    def test_a_chunks_field_that_is_not_a_list_is_an_ignored_unknown_field(self, value):
        # only a chunks LIST marks the pre-0.6 shape (§8.1)
        doc = self._doc()
        entry = {**doc.outline()[0], "chunks": value}
        text = _with_meta(doc.dumps(), {"toc": [entry], "toc_doc_hash": doc.doc_hash})
        assert _meta_codes(text) == set()
        loaded = aim.loads(text)
        assert loaded.toc_is_fresh()
        assert loaded.dumps() == text  # fresh: the save keeps the entry as it is

    def test_a_chunks_list_marks_the_pre_0_6_shape(self):
        # a complete compact entry that also carries a chunks list is read as
        # the pre-0.6 shape: stale at 0.6, and the save rewrites it
        doc = self._doc()
        entry = {**doc.outline()[0], "chunks": ["h", "p"]}
        text = _with_meta(doc.dumps(), {"toc": [entry], "toc_doc_hash": doc.doc_hash})
        assert _meta_codes(text) == {("M005", "warning")}
        loaded = aim.loads(text)
        assert not loaded.toc_is_fresh()
        saved = aim.loads(loaded.dumps())
        assert saved.meta["toc"] == saved.outline() and saved.toc_is_fresh()


class TestScaling:
    """Every save after an edit rederives the outline, and a section read
    walks the whole body to find where its section ends: both must stay
    linear in the number of headings."""

    @staticmethod
    def _headings(n: int) -> aim.AimDocument:
        body = "\n".join(f'<h2 data-aim="h{i}">Heading {i}</h2>' for i in range(n))
        text = aim.new_document(title="Many headings").dumps()
        return aim.loads(text.replace("<body>\n", f"<body>\n{body}\n", 1))

    @staticmethod
    def _best_of_three(read) -> float:
        times = []
        for _ in range(3):
            started = perf_counter()
            read()
            times.append(perf_counter() - started)
        return min(times)

    def test_a_section_read_is_linear_in_the_headings(self):
        # eight times the headings: linear work grows about 8x; finding each
        # section's end by rescanning the openers after it grows about 40x
        # at these sizes (64x asymptotically)
        small, large = self._headings(1000), self._headings(8000)
        assert large.section("h7999").ids == ("h7999",)
        ratio = self._best_of_three(lambda: large.section("h7999")) / self._best_of_three(
            lambda: small.section("h999")
        )
        assert ratio < 20, f"8x the headings took {ratio:.1f}x the time"
