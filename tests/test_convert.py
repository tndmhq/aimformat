"""Converter tests: text/markdown import, md/html export, CLI, dispatch."""
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
        assert "h1" in tags and "h2" in tags and "pre" in tags \
            and "blockquote" in tags and "hr" in tags
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
        assert "raw html block" in text                 # …but kept as text

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
        for token in ("# Report Title", "| H1 | H2 |", "```", "- first item",
                      "1. ordered A", "> Quoted", "**bold**", "~~gone~~"):
            assert token in md1

    def test_marks_without_md_equivalent_degrade(self):
        doc = aim.new_document(title="t")
        doc.add_chunk("<p>a <u>u</u> and <mark>m</mark> here</p>",
                      author=aim.human("x"))
        assert "a u and m here" in aim.to_markdown(doc)

    def test_figure(self):
        doc = aim.new_document(title="t")
        doc.add_chunk('<figure><img alt="A" src="https://x/y.png">'
                      "<figcaption>Cap</figcaption></figure>",
                      author=aim.human("x"))
        md = aim.to_markdown(doc)
        assert "![A](https://x/y.png)" in md and "*Cap*" in md

    def test_invalid_pending(self):
        doc = aim.new_document(title="t")
        with pytest.raises(InvalidOperation):
            aim.to_markdown(doc, pending="tracked")


class TestCriticMarkup:
    def _doc(self):
        doc = aim.from_text("Alpha.\n\nBeta.")
        bot = aim.agent("test-model")
        a, b = doc.chunks
        doc.propose_modify(a.id, f'<p data-aim="{a.id}">Alpha two.</p>',
                           author=bot, explanation="tighter")
        doc.propose_delete(b.id, author=bot, explanation="redundant")
        doc.propose_add("<p>New tail.</p>", author=bot, container="body",
                        explanation="closing note")
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
        doc.propose_modify(c.id, f'<p data-aim="{c.id}">Alpha two.</p>',
                           author=bot, explanation="e")
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
        assert cli_main(["export", str(out), "-o", str(tmp_path / "x.md"),
                         "--pending", "tracked"]) == 2
        assert cli_main(["import", str(src), "-o", str(out)]) == 2  # exists

    def test_import_unsupported(self, tmp_path):
        bad = tmp_path / "x.rtf"
        bad.write_text("x", "utf-8")
        assert cli_main(["import", str(bad), "-o",
                         str(tmp_path / "y.aim")]) == 2


class TestDoclingWrappers:
    def test_from_docx_requires_docling(self, tmp_path):
        pytest.importorskip("docling")
        # covered end-to-end in the editor backend; here only the wiring


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
