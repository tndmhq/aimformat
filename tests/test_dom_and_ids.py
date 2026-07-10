"""Mini-DOM parsing behavior and id assignment."""

import pytest

import aimformat as aim
from aimformat import ids
from aimformat.dom import parse_fragment, parse_html
from aimformat.errors import ParseError
from conftest import BOT, ts


class TestDom:
    def test_raw_script_content_preserved(self):
        frag = parse_html(
            '<script type="application/aim-history+jsonl">\n{"a":"<\\/script>"}\n</script>'
        )
        el = frag.elements()[0]
        assert '{"a":"<\\/script>"}' in el.raw

    def test_template_children_parse_normally(self):
        nodes = parse_fragment("<template><tr><td>x</td></tr></template>")
        tmpl = nodes[0]
        assert tmpl.elements()[0].tag == "tr"

    def test_unmatched_close_raises(self):
        with pytest.raises(ParseError):
            parse_html("<p>x</p></section>")

    def test_unclosed_raises(self):
        with pytest.raises(ParseError):
            parse_html("<body><section><p>x</p>")

    def test_unterminated_script_raises(self):
        with pytest.raises(ParseError):
            parse_html('<script type="application/aim-history+jsonl">{}')

    def test_attr_helpers(self):
        el = parse_fragment('<p data-aim="x" class="a">t</p>')[0]
        assert el.chunk_id == "x" and el.get("class") == "a"
        el.set("class", "b")
        el.remove_attr("data-aim")
        assert el.get("class") == "b" and el.chunk_id is None

    def test_text_extraction_skips_markup(self):
        el = parse_fragment("<p>a <strong>b</strong> c</p>")[0]
        assert el.text() == "a b c"

    def test_iter_depth_first(self):
        el = parse_fragment("<section><h2>t</h2><p><em>x</em></p></section>")[0]
        assert [e.tag for e in el.iter()] == ["section", "h2", "p", "em"]


class TestIds:
    def test_valid_patterns(self):
        assert ids.is_valid_chunk_id("a1")
        assert ids.is_valid_chunk_id("chunk-name_2")
        assert not ids.is_valid_chunk_id("-leading")
        assert not ids.is_valid_chunk_id("UPPER")
        assert not ids.is_valid_chunk_id("body")  # reserved
        assert not ids.is_valid_chunk_id("aim:theme")  # reserved
        assert ids.is_valid_proposal_id("p-1a")
        assert not ids.is_valid_proposal_id("x-1a")

    def test_new_id_avoids_taken(self):
        taken = {"aaaaaaaa"}
        got = ids.new_id(taken)
        assert got != "aaaaaaaa" and got in taken and len(got) == 8

    def test_assignment_honors_valid_unused_payload_id(self):
        doc = aim.new_document(title="T")
        c = doc.add_chunk('<p data-aim="mine">x</p>', author=BOT, at=ts(0))
        assert c.id == "mine"

    def test_assignment_replaces_taken_payload_id(self):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="mine">x</p>', author=BOT, at=ts(0))
        c2 = doc.add_chunk('<p data-aim="mine">y</p>', author=BOT, at=ts(1))
        assert c2.id != "mine"

    def test_deleted_ids_stay_burned(self):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="gone">x</p>', author=BOT, at=ts(0))
        doc.delete_chunk("gone", author=BOT, at=ts(1))
        c = doc.add_chunk('<p data-aim="gone">y</p>', author=BOT, at=ts(2))
        assert c.id != "gone"

    def test_assignment_replaces_invalid_payload_id(self):
        doc = aim.new_document(title="T")
        c = doc.add_chunk('<p data-aim="Not Valid!">x</p>', author=BOT, at=ts(0))
        assert ids.is_valid_chunk_id(c.id)
