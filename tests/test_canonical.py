"""Canonical form: escaping, attribute order, hashing, round-trips (spec §11)."""

import aimformat as aim
from aimformat.canonical import (
    canonical_json,
    doc_hash,
    escape_attr,
    escape_text,
    serialize,
    sha256_prefixed,
    sort_class_tokens,
)
from aimformat.dom import parse_fragment
from conftest import BOT, ts


def el(markup: str):
    return parse_fragment(markup)[0]


class TestEscaping:
    def test_text_escapes_amp_lt_gt_only(self):
        assert escape_text('a & b < c > "d"') == 'a &amp; b &lt; c &gt; "d"'

    def test_attr_escapes_amp_quote_only(self):
        assert escape_attr('</script> & "x"') == "</script> &amp; &quot;x&quot;"

    def test_entities_roundtrip_through_parse(self):
        e = el("<p>1 &amp; 2 &lt;tag&gt;</p>")
        assert serialize(e) == "<p>1 &amp; 2 &lt;tag&gt;</p>"

    def test_unicode_kept_raw(self):
        e = el("<p>中文 — עברית — 𝔘 — é</p>")
        assert serialize(e) == "<p>中文 — עברית — 𝔘 — é</p>"


class TestAttributeOrder:
    def test_data_aim_first_then_class_style_then_alpha(self):
        e = el('<p title="t" style="left:1px" data-aim="x" class="b a" lang="en">.</p>')
        assert serialize(e) == (
            '<p data-aim="x" class="a b" style="left:1px" lang="en" title="t">.</p>'
        )

    def test_src_href_forced_last(self):
        e = el('<img src="https://x/y.png" alt="pic">')
        assert serialize(e) == '<img alt="pic" src="https://x/y.png">'

    def test_class_tokens_sorted(self):
        assert sort_class_tokens("text-3xl font-bold aaa") == "aaa font-bold text-3xl"

    def test_boolean_attr_serialized_bare(self):
        e = el("<style data-aim-theme>:root{}</style>")
        assert serialize(e) == "<style data-aim-theme>:root{}</style>"

    def test_svg_foreign_case_readjusted(self):
        e = el('<svg><symbol id="s" viewBox="0 0 1 1"></symbol></svg>')
        assert 'viewBox="0 0 1 1"' in serialize(e)

    def test_self_closing_svg_child(self):
        e = el('<svg><use href="#a"/></svg>')
        assert serialize(e) == '<svg><use href="#a"/></svg>'

    def test_void_element_no_slash(self):
        assert serialize(el("<hr>")) == "<hr>"

    def test_non_void_self_closing_spelling_gets_explicit_end_tag(self):
        assert serialize(el("<span/>")) == "<span></span>"


class TestStyleNormalForm:
    """Paint appends to the geometry order (spec §3.3): geometry first, then
    color, background-color, border-color."""

    def test_paint_follows_geometry_in_registry_order(self):
        e = el(
            '<h2 style="border-color:#ff69b4; color:#ff69b4; top:32px; '
            'background-color:#fff1f7; left:48px">T</h2>'
        )
        assert serialize(e) == (
            '<h2 style="left:48px; top:32px; color:#ff69b4; '
            'background-color:#fff1f7; border-color:#ff69b4">T</h2>'
        )

    def test_a_duplicate_paint_declaration_keeps_the_last(self):
        e = el('<p style="color:#111111; left:2px; color:#ff69b4">x</p>')
        assert serialize(e) == '<p style="left:2px; color:#ff69b4">x</p>'

    def test_a_style_that_normalizes_to_nothing_is_dropped(self):
        assert serialize(el('<p style="  ;  ">x</p>')) == "<p>x</p>"

    def test_a_geometry_only_document_serializes_unchanged(self):
        e = el('<h2 style="left:48px; top:32px">T</h2>')
        assert serialize(e) == '<h2 style="left:48px; top:32px">T</h2>'


class TestJson:
    def test_sorted_keys_compact(self):
        assert canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'

    def test_script_terminator_escaped(self):
        out = canonical_json({"x": "</script>"})
        assert "</script" not in out and "<\\/script>" in out

    def test_unicode_not_ascii_escaped(self):
        assert canonical_json({"x": "€ 中"}) == '{"x":"€ 中"}'


class TestDocHash:
    def test_recipe_shape(self):
        h = doc_hash("<html>", "<style data-aim-theme>:root{}</style>", ['<p data-aim="a">x</p>'])
        assert h.startswith("sha256:") and len(h) == 7 + 64

    def test_hash_ignores_caches_and_proposals(self):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="a">x</p>', author=BOT, at=ts(0))
        before = doc.doc_hash
        doc.set_summary("a summary", model="m")
        doc.set_embedding("a", model="m", vec=[0.1])
        doc.propose_modify("a", '<p data-aim="a">y</p>', author=BOT, at=ts(1))
        assert doc.doc_hash == before

    def test_hash_changes_on_content_and_theme_and_order(self):
        doc = aim.new_document(title="T", theme={"--aim-brand-1": "#111111"})
        doc.add_chunk('<p data-aim="a">x</p>', author=BOT, at=ts(0))
        doc.add_chunk('<p data-aim="b">y</p>', author=BOT, at=ts(1))
        h0 = doc.doc_hash
        doc.modify_chunk("a", '<p data-aim="a">x2</p>', author=BOT, at=ts(2))
        h1 = doc.doc_hash
        doc.move_chunk("b", container="body", after=None, author=BOT, at=ts(3))
        h2 = doc.doc_hash
        doc.set_theme({"--aim-brand-1": "#222222"}, author=BOT, at=ts(4))
        h3 = doc.doc_hash
        assert len({h0, h1, h2, h3}) == 4

    def test_geometry_counts_in_hash(self):
        doc = aim.new_document(title="T")
        doc.add_chunk(
            '<aim-slide data-aim-container="s" '
            'style="width:1920px; height:1080px">'
            '<p data-aim="a" style="left:10px; top:10px">x</p>'
            "</aim-slide>",
            author=BOT,
            at=ts(0),
        )
        h0 = doc.doc_hash
        doc.modify_chunk(
            "a", '<p data-aim="a" style="left:20px; top:10px">x</p>', author=BOT, at=ts(1)
        )
        assert doc.doc_hash != h0


class TestRoundTrip:
    def test_dumps_parse_dumps_stable(self, lifecycle_doc):
        text = lifecycle_doc.dumps()
        again = aim.loads(text).dumps()
        assert text == again

    def test_multiline_pre_survives(self):
        doc = aim.new_document(title="T")
        doc.add_chunk(
            '<pre data-aim="code"><code>line one\nline two</code></pre>', author=BOT, at=ts(0)
        )
        text = doc.dumps()
        doc2 = aim.loads(text)
        assert (
            doc2.chunk("code").html == '<pre data-aim="code"><code>line one\nline two</code></pre>'
        )
        assert doc2.dumps() == text

    def test_paint_survives_a_full_round_trip(self):
        doc = aim.new_document(title="T")
        doc.add_chunk('<h1 data-aim="t" style="color:#ff69b4">Pink</h1>', author=BOT, at=ts(0))
        doc.add_chunk(
            '<p data-aim="b" class="border" style="background-color:#fff1f7; '
            'border-color:#ff69b4">Tinted</p>',
            author=BOT,
            at=ts(1),
        )
        text = doc.dumps()
        again = aim.loads(text)
        assert again.dumps() == text
        assert again.chunk("t").html == '<h1 data-aim="t" style="color:#ff69b4">Pink</h1>'

    def test_constructs_never_share_a_line(self):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="a">one</p>', author=BOT, at=ts(0))
        doc.add_chunk('<p data-aim="b">two</p>', author=BOT, at=ts(1))
        lines = doc.dumps().split("\n")
        assert '<p data-aim="a">one</p>' in lines
        assert '<p data-aim="b">two</p>' in lines

    def test_sha256_prefixed_bytes_and_str_agree(self):
        assert sha256_prefixed("abc") == sha256_prefixed(b"abc")
