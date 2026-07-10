"""Converter tests: text/markdown import, md/html export, CLI, dispatch."""

import re

import pytest

import aimformat as aim
from aimformat.cli import main as cli_main
from aimformat.errors import InvalidOperation

pytest.importorskip("markdown_it")


def _errors(text: str):
    return [f for f in aim.lint_text(text) if f.level == "error"]


RICH_MD = """# Report Title

Intro with **bold**, *em*, ~~gone~~, `code`, a [link](https://example.com),
and a [bad](javascript:alert(1)) one.

## Section

- first item
- second with **bold**
  - nested item
1. ordered A
2. ordered B

> Quoted with *em*.
> Continued line.

```python
print("hi")
```

| H1 | H2 |
| --- | --- |
| a | b |

---

![pic](https://example.com/p.png) and ![weird](ftp://x/y.png) inline.

<div>raw html block</div>
"""


class TestFromText:
    def test_paragraphs_and_breaks(self):
        doc = aim.from_text("One.\n\nTwo\ncontinued.", title="T")
        assert doc.title == "T"
        assert [c.tag for c in doc.chunks] == ["p", "p"]
        assert "<br>" in doc.chunks[1].html
        assert not _errors(doc.dumps())

    def test_import_is_history(self):
        doc = aim.from_text("One.")
        assert doc.history and doc.history[0].author.type == "external"

    def test_empty_input(self):
        doc = aim.from_text("   \n\n  ")
        assert doc.chunks == []
        assert not _errors(doc.dumps())


class TestFromMarkdown:
    def test_rich_document(self):
        doc = aim.from_markdown(RICH_MD)
        assert doc.title == "Report Title"
        assert not _errors(doc.dumps())
        tags = [c.tag for c in doc.chunks]
        assert (
            "h1" in tags
            and "h2" in tags
            and "pre" in tags
            and "blockquote" in tags
            and "hr" in tags
        )
        # two lists + one table became containers with item chunks
        assert len(doc.containers) == 3
        assert "li" in tags and "tr" in tags

    def test_unsafe_content_degrades(self):
        doc = aim.from_markdown(RICH_MD)
        text = doc.dumps()
        # never as attributes (markdown-it already refuses javascript: links
        # at parse time; our scheme gate catches everything else)
        assert 'href="javascript:' not in text
        assert 'src="ftp:' not in text
        assert "<div>raw html block</div>" not in text  # escaped, not injected
        assert "raw html block" in text  # …but kept as text

    def test_disallowed_link_scheme_degrades_to_text(self):
        doc = aim.from_markdown("See [share](ftp://host/file) now.")
        html = doc.chunks[0].html
        assert "<a" not in html and "share" in html

    def test_nested_list_inline(self):
        doc = aim.from_markdown("- outer\n  - inner one\n  - inner two\n")
        li = [c for c in doc.chunks if c.tag == "li"]
        # nested list is inline content of the outer item chunk
        assert len(li) == 1 and "<ul>" in li[0].html and "inner two" in li[0].html

    def test_title_override(self):
        doc = aim.from_markdown("# Heading\n\nBody.", title="Chosen")
        assert doc.title == "Chosen"


class TestToMarkdown:
    def test_round_trip_stability(self):
        doc = aim.from_markdown(RICH_MD)
        md1 = aim.to_markdown(doc)
        doc2 = aim.from_markdown(md1)
        assert not _errors(doc2.dumps())
        md2 = aim.to_markdown(doc2)
        assert md1 == md2  # converged after one round trip
        for token in (
            "# Report Title",
            "| H1 | H2 |",
            "```",
            "- first item",
            "1. ordered A",
            "> Quoted",
            "**bold**",
            "~~gone~~",
        ):
            assert token in md1

    def test_marks_without_md_equivalent_degrade(self):
        doc = aim.new_document(title="t")
        doc.add_chunk("<p>a <u>u</u> and <mark>m</mark> here</p>", author=aim.human("x"))
        assert "a u and m here" in aim.to_markdown(doc)

    def test_figure(self):
        doc = aim.new_document(title="t")
        doc.add_chunk(
            '<figure><img alt="A" src="https://x/y.png"><figcaption>Cap</figcaption></figure>',
            author=aim.human("x"),
        )
        md = aim.to_markdown(doc)
        assert "![A](https://x/y.png)" in md and "*Cap*" in md

    def test_invalid_pending(self):
        doc = aim.new_document(title="t")
        with pytest.raises(InvalidOperation):
            aim.to_markdown(doc, pending="tracked")


class TestRoundTripHardening:
    """Regressions from the 2026-07-08 adversarial review."""

    PROSE = [
        "# not a heading",
        "- not a list",
        "> not a quote",
        "1. not ordered",
        "~~not struck~~",
        "&copy; entity text",
        "<https://example.com> literal",
        "--- dashes",
    ]

    def _doc(self, chunks):
        doc = aim.new_document(title="t")
        u = aim.human("u")
        for c in chunks:
            doc.add_chunk(c, author=u)
        return doc

    def test_prose_that_looks_like_markdown_survives(self):
        doc = self._doc(
            [
                "<p>" + c.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</p>"
                for c in self.PROSE
            ]
        )
        md1 = aim.to_markdown(doc)
        d2 = aim.from_markdown(md1)
        assert [ch.text for ch in d2.chunks] == self.PROSE
        assert aim.to_markdown(d2) == md1  # converged

    def test_table_cells_with_br_pipes_code(self):
        doc = self._doc(
            [
                "<table><thead><tr><th>H1</th><th>H2</th></tr></thead><tbody>"
                "<tr><td>a<br>b</td><td><code>x|y</code></td></tr>"
                "</tbody></table>"
            ]
        )
        d2 = aim.from_markdown(aim.to_markdown(doc))
        rows = [c for c in d2.chunks if c.tag == "tr"]
        assert len(rows) == 2  # header + body — the table survived
        assert "x|y" in rows[1].text

    def test_multi_row_thead_demotes(self):
        doc = self._doc(
            [
                "<table><thead><tr><th>A</th></tr><tr><th>B</th></tr></thead>"
                "<tbody><tr><td>c</td></tr></tbody></table>"
            ]
        )
        d2 = aim.from_markdown(aim.to_markdown(doc))
        assert len([c for c in d2.chunks if c.tag == "tr"]) == 3

    def test_backticks_in_code(self):
        doc = self._doc(
            [
                "<p>inline <code>a`b</code> code</p>",
                "<pre><code>line\n```\nfence inside</code></pre>",
            ]
        )
        d2 = aim.from_markdown(aim.to_markdown(doc))
        pre = [c for c in d2.chunks if c.tag == "pre"]
        assert len(pre) == 1 and "fence inside" in pre[0].text
        assert "a`b" in d2.chunks[0].text

    def test_ordered_nesting_indent(self):
        d = aim.from_markdown("1. outer\n   1. inner\n2. next\n")
        d2 = aim.from_markdown(aim.to_markdown(d))
        li = [c for c in d2.chunks if c.tag == "li"]
        assert len(li) == 2 and "inner" in li[0].html  # stayed nested

    def test_loose_item_keeps_second_paragraph(self):
        d = aim.from_markdown("- one\n\n  two\n")
        d2 = aim.from_markdown(aim.to_markdown(d))
        li = [c for c in d2.chunks if c.tag == "li"][0]
        # both paragraphs survive as separate blocks inside the item
        assert li.html.count("<p>") == 2
        assert "one" in li.text and "two" in li.text

    def test_heading_inside_item_kept_as_text(self):
        d = aim.from_markdown("- # heading in item\n")
        li = [c for c in d.chunks if c.tag == "li"][0]
        assert "heading in item" in li.text

    def test_link_with_space_and_parens(self):
        doc = self._doc(['<p><a href="https://x/a b(c)">t</a></p>'])
        d2 = aim.from_markdown(aim.to_markdown(doc))
        assert "<a" in d2.chunks[0].html

    def test_bom_stripped(self, tmp_path):
        p = tmp_path / "bom.md"
        p.write_bytes("﻿# BOM Title\n\nBody.".encode())
        doc = aim.from_path(p)
        assert doc.title == "BOM Title"

    def test_accept_all_delete_plus_dependent_add(self):
        doc = aim.from_text("Alpha.\n\nBeta.")
        bot = aim.agent("m")
        doomed = doc.chunks[1]
        doc.propose_delete(doomed.id, author=bot, explanation="x")
        doc.propose_add(
            "<p>After beta.</p>", author=bot, container="body", after=doomed.id, explanation="y"
        )
        html = aim.to_html(doc, pending="accept-all")
        assert "After beta." in html and "Beta." not in html

    def test_critic_container_and_row_proposals_visible(self):
        doc = aim.new_document(title="t")
        u = aim.human("u")
        doc.add_chunk(
            '<ul data-aim-container=""><li data-aim="">one</li><li data-aim="">two</li></ul>',
            author=u,
        )
        bot = aim.agent("m")
        li_id = [c for c in doc.chunks if c.tag == "li"][0].id
        doc.propose_delete(li_id, author=bot, explanation="drop item")
        md = aim.to_markdown(doc, pending="criticmarkup")
        assert "{--" in md and "drop item" in md

    def test_critic_delimiters_neutralized(self):
        doc = aim.from_text("a ~> b and x ~~ y.")
        bot = aim.agent("m")
        c = doc.chunks[0]
        doc.propose_modify(
            c.id, f'<p data-aim="{c.id}">clean.</p>', author=bot, explanation="note with <<} inside"
        )
        md = aim.to_markdown(doc, pending="criticmarkup")
        # spans still terminate exactly once each
        assert md.count("~~}") == 1 or md.count("~~}") == 0
        assert "{~~" in md and "~>clean" in md

    def test_export_force_flag(self, tmp_path):
        src = tmp_path / "s.md"
        src.write_text("# T\n\nx", "utf-8")
        doc_path = tmp_path / "d.aim"
        cli_main(["import", str(src), "-o", str(doc_path)])
        out = tmp_path / "o.md"
        out.write_text("sentinel", "utf-8")
        assert cli_main(["export", str(doc_path), "-o", str(out)]) == 2
        assert out.read_text("utf-8") == "sentinel"
        assert cli_main(["export", str(doc_path), "-o", str(out), "--force"]) == 0


class TestCriticMarkup:
    def _doc(self):
        doc = aim.from_text("Alpha.\n\nBeta.")
        bot = aim.agent("test-model")
        a, b = doc.chunks
        doc.propose_modify(
            a.id, f'<p data-aim="{a.id}">Alpha two.</p>', author=bot, explanation="tighter"
        )
        doc.propose_delete(b.id, author=bot, explanation="redundant")
        doc.propose_add(
            "<p>New tail.</p>", author=bot, container="body", explanation="closing note"
        )
        return doc

    def test_all_actions_render(self):
        md = aim.to_markdown(self._doc(), pending="criticmarkup")
        assert "{~~Alpha\\." in md or "{~~Alpha." in md
        assert "~>Alpha two" in md
        assert "{--" in md and "{++New tail" in md
        assert "{>>tighter<<}" in md and "{>>redundant<<}" in md

    def test_drop_is_clean(self):
        md = aim.to_markdown(self._doc())
        assert "{~~" not in md and "{++" not in md


class TestToHtml:
    def _doc(self):
        doc = aim.from_text("Alpha.")
        bot = aim.agent("test-model")
        c = doc.chunks[0]
        doc.propose_modify(
            c.id, f'<p data-aim="{c.id}">Alpha two.</p>', author=bot, explanation="e"
        )
        return doc

    def test_keep(self):
        html = aim.to_html(self._doc())
        assert "<aim-proposal" in html
        assert "application/aim-history" not in html
        assert not _errors(html)

    def test_accept_all(self):
        html = aim.to_html(self._doc(), pending="accept-all")
        assert "Alpha two." in html and "<aim-proposal" not in html
        assert not _errors(html)

    def test_reject_all(self):
        html = aim.to_html(self._doc(), pending="reject-all")
        assert "Alpha two." not in html and "<aim-proposal" not in html
        assert not _errors(html)

    def test_source_untouched(self):
        doc = self._doc()
        aim.to_html(doc, pending="accept-all")
        assert len(doc.proposals) == 1  # resolved on a copy only

    def test_invalid_pending(self):
        with pytest.raises(InvalidOperation):
            aim.to_html(self._doc(), pending="tracked")


class TestFromPath:
    def test_dispatch(self, tmp_path):
        md = tmp_path / "a.md"
        md.write_text("# Hi\n\nBody.", "utf-8")
        txt = tmp_path / "b.txt"
        txt.write_text("Only text.", "utf-8")
        assert aim.from_path(md).title == "Hi"
        assert aim.from_path(txt).title == "b"

    def test_aim_passthrough(self, tmp_path):
        doc = aim.from_text("One.", title="T")
        p = tmp_path / "c.aim"
        doc.save(p)
        assert aim.from_path(p).title == "T"

    def test_unsupported(self, tmp_path):
        with pytest.raises(ValueError, match="unsupported"):
            aim.from_path(tmp_path / "x.pptx")


class TestCli:
    def test_import_export(self, tmp_path, capsys):
        src = tmp_path / "in.md"
        src.write_text(RICH_MD, "utf-8")
        out = tmp_path / "doc.aim"
        assert cli_main(["import", str(src), "-o", str(out)]) == 0
        assert cli_main(["lint", str(out)]) == 0
        md_out = tmp_path / "out.md"
        assert cli_main(["export", str(out), "-o", str(md_out)]) == 0
        assert "# Report Title" in md_out.read_text("utf-8")
        html_out = tmp_path / "out.html"
        assert cli_main(["export", str(out), "-o", str(html_out)]) == 0
        assert not _errors(html_out.read_text("utf-8"))

    def test_bad_format_and_pending(self, tmp_path):
        src = tmp_path / "in.md"
        src.write_text("hi", "utf-8")
        out = tmp_path / "doc.aim"
        cli_main(["import", str(src), "-o", str(out)])
        assert cli_main(["export", str(out), "-o", str(tmp_path / "x.pptx")]) == 2
        assert (
            cli_main(["export", str(out), "-o", str(tmp_path / "x.md"), "--pending", "tracked"])
            == 2
        )
        assert cli_main(["import", str(src), "-o", str(out)]) == 2  # exists

    def test_import_unsupported(self, tmp_path):
        bad = tmp_path / "x.rtf"
        bad.write_text("x", "utf-8")
        assert cli_main(["import", str(bad), "-o", str(tmp_path / "y.aim")]) == 2


class TestDoclingWrappers:
    def test_from_docx_requires_docling(self, tmp_path):
        pytest.importorskip("docling")
        # covered end-to-end in the editor backend; here only the wiring

    def test_hierarchical_body_descends_into_headings(self):
        # docling's DOCX output parents section content UNDER the heading
        # node; the walker must descend (found live 2026-07-07)
        d = {
            "name": "x",
            "body": {"children": [{"$ref": "#/texts/0"}]},
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "label": "title",
                    "text": "T",
                    "children": [{"$ref": "#/texts/1"}],
                },
                {"self_ref": "#/texts/1", "label": "text", "text": "Body para", "children": []},
            ],
            "tables": [],
            "pictures": [],
        }
        doc = aim.from_docling(d)
        assert [c.tag for c in doc.chunks] == ["h1", "p"]
        assert doc.title == "T"


@pytest.mark.filterwarnings("ignore")
class TestToPdf:
    def test_pdf(self, tmp_path):
        pytest.importorskip("playwright")
        doc = aim.from_text("Hello PDF.")
        out = tmp_path / "o.pdf"
        try:
            aim.to_pdf(doc, out)
        except RuntimeError as exc:
            pytest.skip(str(exc))  # chromium not installed
        assert out.stat().st_size > 1000
        assert out.read_bytes()[:5] == b"%PDF-"


def _mixed_deck() -> aim.AimDocument:
    """A flow chunk, an A5-canvas page, and a default-canvas slide."""
    doc = aim.new_document(title="Mixed")
    u = aim.human("u")
    doc.add_chunk("<p>flow before</p>", author=u)
    doc.add_chunk(
        '<aim-slide data-aim-container="pg1" style="width:420px; height:595px">'
        '<h2 data-aim="t1" style="left:10px; top:10px; width:300px">Booklet page</h2>'
        "</aim-slide>",
        author=u,
    )
    doc.add_chunk(
        '<aim-slide data-aim-container="pg2">'
        '<p data-aim="t2" style="left:10px; top:10px; width:300px">Default canvas</p>'
        "</aim-slide>",
        author=u,
    )
    return doc


@pytest.mark.filterwarnings("ignore")
class TestPdfSlidePages:
    def test_print_css_names_a_page_per_slide(self):
        from aimformat.convert._pdf_out import _print_html

        html = _print_html(_mixed_deck(), "keep", None)
        # canvas-pt: the A5 canvas becomes a real 420×595pt page …
        assert "@page pg-pg1{size:420pt 595pt;margin:0}" in html
        # … an unsized canvas gets the 16:9 convention default …
        assert "@page pg-pg2{size:960pt 540pt;margin:0}" in html
        # … slides are assigned their page and print-scaled px→pt (×4/3) …
        assert 'aim-slide[data-aim-container="pg1"]{page:pg-pg1;zoom:1.33333}' in html
        # … and the flow keeps the document page setup (A4 default).
        assert "@page{size:210mm 297mm" in html

    def test_no_slides_no_named_pages(self):
        from aimformat.convert._pdf_out import _print_html

        assert "@page pg-" not in _print_html(aim.from_text("plain"), "keep", None)

    def test_resolution_updates_named_pages(self):
        from aimformat.convert._pdf_out import _print_html

        doc = _mixed_deck()
        doc.propose_add(
            '<aim-slide data-aim-container="pg3" style="width:595px; height:842px">'
            '<p data-aim="t3" style="left:10px; top:10px; width:300px">Pending page</p>'
            "</aim-slide>",
            container="body",
            after="pg2",
            author=aim.agent("m"),
        )
        accepted = _print_html(doc, "accept-all", None)
        assert "@page pg-pg3{size:595pt 842pt;margin:0}" in accepted
        rejected = _print_html(doc, "reject-all", None)
        assert "pg-pg3" not in rejected

    def test_pdf_pages_have_canvas_sizes(self, tmp_path):
        pytest.importorskip("playwright")
        out = tmp_path / "deck.pdf"
        try:
            aim.to_pdf(_mixed_deck(), out)
        except RuntimeError as exc:
            pytest.skip(str(exc))  # chromium not installed
        boxes = re.findall(rb"/MediaBox \[([\d. ]+)\]", out.read_bytes())
        sizes = set()
        for raw in boxes:
            x0, y0, x1, y1 = (float(v) for v in raw.split())
            sizes.add((round(x1 - x0), round(y1 - y0)))
        # the A5 canvas page, the default 16:9 canvas page, the A4 flow page
        assert (420, 595) in sizes
        assert (960, 540) in sizes
        assert (595, 842) in sizes


class TestMarkdownPendingResolution:
    def _doc(self):
        doc = aim.new_document(title="T")
        u = aim.human("u")
        doc.add_chunk('<p data-aim="p1">Old wording.</p>', author=u)
        doc.propose_modify("p1", '<p data-aim="p1">New wording.</p>', author=aim.agent("m"))
        return doc

    def test_accept_all(self):
        md = aim.to_markdown(self._doc(), pending="accept-all")
        assert "New wording." in md and "Old wording." not in md

    def test_reject_all(self):
        md = aim.to_markdown(self._doc(), pending="reject-all")
        assert "Old wording." in md and "New wording." not in md

    def test_original_untouched(self):
        doc = self._doc()
        aim.to_markdown(doc, pending="accept-all")
        assert len(doc.proposals) == 1

    def test_bogus_mode_still_rejected(self):
        with pytest.raises(InvalidOperation):
            aim.to_markdown(self._doc(), pending="merge")
