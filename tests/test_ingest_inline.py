"""Inline formatting in docling ingestion.

DOCX paragraphs with mixed run formatting export as an ``inline`` group of
per-run text items carrying ``formatting``/``hyperlink`` (verified against
docling 2.110.0 / docling-core 2.86.0). Before the ``inline`` branch existed,
the walker shattered such a paragraph into one <p> chunk per run at body
level and dropped it entirely inside list items — these tests are the
regression net for both.

docling-core fixture documents only (dev/test dependency; the package stays
stdlib-only).
"""
import pytest

import aimformat as aim

docling_core = pytest.importorskip("docling_core")

from docling_core.types.doc import GroupLabel  # noqa: E402
from docling_core.types.doc.document import (  # noqa: E402
    DoclingDocument, Formatting)
from docling_core.types.doc.labels import DocItemLabel  # noqa: E402


def fmt(**kwargs) -> Formatting:
    return Formatting(**kwargs)


def build_mixed_paragraph() -> DoclingDocument:
    d = DoclingDocument(name="fmt")
    d.add_title(text="Fmt")
    g = d.add_group(label=GroupLabel.INLINE)
    d.add_text(label=DocItemLabel.TEXT, text="Plain, then", parent=g,
               formatting=fmt())
    d.add_text(label=DocItemLabel.TEXT, text="bold", parent=g,
               formatting=fmt(bold=True))
    d.add_text(label=DocItemLabel.TEXT, text=", then", parent=g,
               formatting=fmt())
    d.add_text(label=DocItemLabel.TEXT, text="italic", parent=g,
               formatting=fmt(italic=True))
    d.add_text(label=DocItemLabel.TEXT, text="ends.", parent=g,
               formatting=fmt())
    return d


def chunk_htmls(doc: aim.AimDocument) -> list[str]:
    return [c.html for c in doc.chunks]


class TestInlineGroups:
    def test_mixed_paragraph_is_one_chunk(self):
        doc = aim.from_docling(build_mixed_paragraph())
        paras = [h for h in chunk_htmls(doc) if h.startswith("<p")]
        assert len(paras) == 1  # was: one chunk per run (shattered)

    def test_marks_and_join_heuristic(self):
        doc = aim.from_docling(build_mixed_paragraph())
        para = next(h for h in chunk_htmls(doc) if h.startswith("<p"))
        # punctuation hugs left: no space before ", then"
        assert ("Plain, then <strong>bold</strong>, then "
                "<em>italic</em> ends.") in para

    def test_subscript_hugs_base(self):
        d = DoclingDocument(name="chem")
        d.add_title(text="Chem")
        g = d.add_group(label=GroupLabel.INLINE)
        d.add_text(label=DocItemLabel.TEXT, text="H", parent=g,
                   formatting=fmt())
        d.add_text(label=DocItemLabel.TEXT, text="2", parent=g,
                   formatting=fmt(script="sub"))
        d.add_text(label=DocItemLabel.TEXT, text="O and x", parent=g,
                   formatting=fmt())
        d.add_text(label=DocItemLabel.TEXT, text="2", parent=g,
                   formatting=fmt(script="super"))
        doc = aim.from_docling(d)
        para = next(h for h in chunk_htmls(doc) if h.startswith("<p"))
        assert "H<sub>2</sub>O and x<sup>2</sup>" in para

    def test_existing_boundary_whitespace_is_not_doubled(self):
        """Backends that keep run-trailing whitespace must not get a second
        separator space (Codex review, aimformat#2)."""
        d = DoclingDocument(name="ws")
        d.add_title(text="Ws")
        g = d.add_group(label=GroupLabel.INLINE)
        d.add_text(label=DocItemLabel.TEXT, text="Docling supports ",
                   parent=g, formatting=fmt())
        d.add_text(label=DocItemLabel.TEXT, text="italic", parent=g,
                   formatting=fmt(italic=True))
        doc = aim.from_docling(d)
        para = next(h for h in chunk_htmls(doc) if h.startswith("<p"))
        assert "Docling supports <em>italic</em>" in para
        assert "supports  <em>" not in para  # no doubled space

    def test_script_join_is_directional(self):
        """Sub glues both sides (H2O); sup glues left only (x² grows)."""
        d = DoclingDocument(name="dir")
        d.add_title(text="Dir")
        g1 = d.add_group(label=GroupLabel.INLINE)
        d.add_text(label=DocItemLabel.TEXT, text="H", parent=g1,
                   formatting=fmt())
        d.add_text(label=DocItemLabel.TEXT, text="2", parent=g1,
                   formatting=fmt(script="sub"))
        d.add_text(label=DocItemLabel.TEXT, text="O flows", parent=g1,
                   formatting=fmt())
        g2 = d.add_group(label=GroupLabel.INLINE)
        d.add_text(label=DocItemLabel.TEXT, text="area x", parent=g2,
                   formatting=fmt())
        d.add_text(label=DocItemLabel.TEXT, text="2", parent=g2,
                   formatting=fmt(script="super"))
        d.add_text(label=DocItemLabel.TEXT, text="grows", parent=g2,
                   formatting=fmt())
        doc = aim.from_docling(d)
        paras = [h for h in chunk_htmls(doc) if h.startswith("<p")]
        assert any("H<sub>2</sub>O flows" in h for h in paras)
        assert any("area x<sup>2</sup> grows" in h for h in paras)

    def test_combined_marks_nest_in_fixed_order(self):
        d = DoclingDocument(name="all")
        d.add_title(text="All")
        d.add_text(label=DocItemLabel.TEXT, text="everything",
                   formatting=fmt(bold=True, italic=True, underline=True,
                                  strikethrough=True))
        doc = aim.from_docling(d)
        para = next(h for h in chunk_htmls(doc) if h.startswith("<p"))
        assert "<strong><em><u><s>everything</s></u></em></strong>" in para

    def test_whole_paragraph_formatting_without_group(self):
        d = DoclingDocument(name="solo")
        d.add_title(text="Solo")
        d.add_text(label=DocItemLabel.TEXT, text="All bold",
                   formatting=fmt(bold=True))
        doc = aim.from_docling(d)
        assert any(h.startswith("<p") and
                   "<strong>All bold</strong></p>" in h
                   for h in chunk_htmls(doc))

    def test_escaping_inside_marks(self):
        d = DoclingDocument(name="esc")
        d.add_title(text="Esc")
        d.add_text(label=DocItemLabel.TEXT, text="a < b & c",
                   formatting=fmt(bold=True))
        doc = aim.from_docling(d)
        assert any("<strong>a &lt; b &amp; c</strong>" in h
                   for h in chunk_htmls(doc))


class TestHyperlinks:
    def test_safe_hyperlink_becomes_anchor(self):
        d = DoclingDocument(name="link")
        d.add_title(text="Link")
        g = d.add_group(label=GroupLabel.INLINE)
        d.add_text(label=DocItemLabel.TEXT, text="Visit", parent=g,
                   formatting=fmt())
        d.add_text(label=DocItemLabel.TEXT, text="the site", parent=g,
                   formatting=fmt(), hyperlink="https://aimformat.com/")
        doc = aim.from_docling(d)
        para = next(h for h in chunk_htmls(doc) if h.startswith("<p"))
        assert 'Visit <a href="https://aimformat.com/">the site</a>' in para

    def test_unsafe_scheme_keeps_text_only(self):
        d = DoclingDocument(name="link")
        d.add_title(text="Link")
        d.add_text(label=DocItemLabel.TEXT, text="local file",
                   hyperlink="file:///etc/passwd")
        doc = aim.from_docling(d)
        joined = "".join(chunk_htmls(doc))
        assert "<a" not in joined and "local file" in joined
        assert not [f for f in aim.lint_text(doc.dumps())
                    if f.level == "error"]


class TestListItems:
    def test_item_content_in_inline_group_is_kept(self):
        d = DoclingDocument(name="list")
        d.add_title(text="List")
        lst = d.add_group(label=GroupLabel.LIST)
        item = d.add_list_item(text="", parent=lst)
        g = d.add_group(label=GroupLabel.INLINE, parent=item)
        d.add_text(label=DocItemLabel.TEXT, text="Item with", parent=g,
                   formatting=fmt())
        d.add_text(label=DocItemLabel.TEXT, text="bold", parent=g,
                   formatting=fmt(bold=True))
        doc = aim.from_docling(d)
        lis = [h for h in chunk_htmls(doc) if h.startswith("<li")]
        # was: the inline group had no branch in _li_markup -> empty <li>
        assert any("Item with <strong>bold</strong>" in h for h in lis)

    def test_item_own_formatting(self):
        d = DoclingDocument(name="list")
        d.add_title(text="List")
        lst = d.add_group(label=GroupLabel.LIST)
        d.add_list_item(text="plain but bold", parent=lst,
                        formatting=fmt(bold=True))
        doc = aim.from_docling(d)
        lis = [h for h in chunk_htmls(doc) if h.startswith("<li")]
        assert any("<strong>plain but bold</strong>" in h for h in lis)


class TestConformance:
    def test_ingested_formatting_lints_clean(self):
        doc = aim.from_docling(build_mixed_paragraph())
        assert not [f for f in aim.lint_text(doc.dumps())
                    if f.level == "error"]
