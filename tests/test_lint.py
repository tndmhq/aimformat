"""The verifier: every rule family exercised with an offending document.

Most cases take a known-good canonical document and surgically break one
thing, asserting the specific rule code fires — the string-mutation analogue
of the ok_*/nok_* conformance pairs (which live in tests/fixtures/ and are
checked in test_fixtures.py).
"""

import pytest

import aimformat as aim
from aimformat.lint import lint_text
from conftest import BOT, ME, ts


@pytest.fixture
def good_text(lifecycle_doc) -> str:
    text = lifecycle_doc.dumps()
    assert not [f for f in lint_text(text) if f.level == "error"]
    return text


def codes(text: str) -> set[str]:
    return {f.code for f in lint_text(text) if f.level == "error"}


def warn_codes(text: str) -> set[str]:
    return {f.code for f in lint_text(text) if f.level == "warning"}


class TestStructureRules:
    def test_S001_missing_version(self, good_text):
        broken = good_text.replace(f' data-aim-version="{aim.SPEC_VERSION}"', "")
        assert "S001" in codes(broken)

    def test_S002_future_version_warns(self, good_text):
        broken = good_text.replace(
            f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="9.9"'
        )
        assert "S002" in warn_codes(broken)

    def test_S002_older_version_does_not_warn(self, good_text):
        """A tool implementing 0.3 understands 0.2, so an older document is
        not a finding — warning on it would fire on every existing file."""
        older = good_text.replace(
            f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.1"'
        )
        assert "S002" not in warn_codes(older)

    def test_S006_older_stylesheet_does_not_warn(self, good_text):
        older = good_text.replace(f'data-aim-css="{aim.SPEC_VERSION}"', 'data-aim-css="0.1"')
        assert "S006" not in warn_codes(older)

    def test_S006_future_stylesheet_warns(self, good_text):
        ahead = good_text.replace(f'data-aim-css="{aim.SPEC_VERSION}"', 'data-aim-css="9.9"')
        assert "S006" in warn_codes(ahead)

    def test_S032_prior_version_cannot_contain_live_paint(self):
        doc = aim.new_document(title="prior version with paint")
        doc.add_chunk('<p data-aim="p1" style="color:#ff69b4">Painted.</p>', author=BOT, at=ts(0))
        text = doc.dumps().replace(
            f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.2"'
        )
        assert "S032" in codes(text)

    def test_S032_prior_version_cannot_contain_pending_paint(self):
        doc = aim.new_document(title="prior version pending paint")
        doc.add_chunk('<p data-aim="p1">Plain.</p>', author=BOT, at=ts(0))
        doc.propose_modify(
            "p1", '<p data-aim="p1" style="color:#ff69b4">Painted.</p>', author=BOT, at=ts(1)
        )
        text = doc.dumps().replace(
            f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.2"'
        )
        assert "S032" in codes(text)

    def test_S032_prior_version_cannot_retain_paint_in_history(self):
        doc = aim.new_document(title="prior version retained paint")
        doc.add_chunk('<p data-aim="p1" style="color:#ff69b4">Painted.</p>', author=BOT, at=ts(0))
        doc.modify_chunk("p1", '<p data-aim="p1">Plain.</p>', author=ME, at=ts(1))
        text = doc.dumps().replace(
            f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.2"'
        )
        assert "S032" in codes(text)

    def test_malformed_history_is_not_misreported_as_a_structure_crash(self):
        doc = aim.new_document(title="prior version malformed history")
        doc.add_chunk('<p data-aim="p1">Plain.</p>', author=BOT, at=ts(0))
        text = doc.dumps().replace(
            f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.2"'
        )
        text = text.replace(
            '<script type="application/aim-history+jsonl">',
            '<script type="application/aim-history+jsonl">\nnot json',
            1,
        )

        findings = codes(text)
        assert "H002" in findings
        assert "S000" not in findings

    def test_S003_missing_charset(self, good_text):
        broken = good_text.replace('<meta charset="utf-8">\n', "")
        assert "S003" in codes(broken)

    def test_S004_missing_title(self, good_text):
        broken = good_text.replace("<title>Rich fixture</title>\n", "")
        assert "S004" in codes(broken)

    def test_S005_missing_aimcss_warns(self, basic_doc):
        text = basic_doc.dumps()
        start = text.index(f'<style data-aim-css="{aim.SPEC_VERSION}">')
        end = text.index("</style>", start) + len("</style>\n")
        assert "S005" in warn_codes(text[:start] + text[end:])

    def test_S007_body_comment(self, good_text):
        broken = good_text.replace("<body>\n", "<body>\n<!-- note -->\n")
        assert "S007" in codes(broken)

    def test_S008_stray_body_text(self, good_text):
        broken = good_text.replace("<body>\n", "<body>\nloose words\n")
        assert "S008" in codes(broken)

    def test_V003_body_style_is_not_addressable_document_state(self, good_text):
        broken = good_text.replace("<body>", '<body style="color:#ff69b4">')
        assert "V003" in codes(broken)

    def test_S011_uncovered_body_child(self, good_text):
        broken = good_text.replace('<p data-aim="intro">', "<p>")
        assert "S011" in codes(broken)

    def test_S012_chunk_and_container_exclusive(self, good_text):
        broken = good_text.replace(
            '<ul data-aim-container="list">', '<ul data-aim-container="list" data-aim="listx">'
        )
        assert "S012" in codes(broken)

    def test_S014_section_order(self, basic_doc):
        basic_doc.propose_delete("intro", author=BOT, at=ts(5))
        text = basic_doc.dumps()
        # move the proposals section after the history script
        start = text.index("<aim-proposals>")
        end = text.index("</aim-proposals>") + len("</aim-proposals>\n")
        block = text[start:end]
        moved = text[:start] + text[end:]
        moved = moved.replace("</body>", block + "</body>")
        assert "S014" in codes(moved)

    def test_S015_invalid_id(self, good_text):
        broken = good_text.replace('data-aim="intro"', 'data-aim="In tro!"', 1)
        assert "S015" in codes(broken)

    def test_S016_same_id_two_parents(self, good_text):
        broken = good_text.replace(
            '<li data-aim="li1">First</li>', '<li data-aim="intro">First</li>'
        )
        assert "S016" in codes(broken)

    def test_S017_run_not_consecutive(self, good_text):
        broken = good_text.replace(
            '<li data-aim="li2">…second, part two</li>',
            '<li data-aim="li9">gap</li><li data-aim="li2">…second, part two</li>',
        )
        assert "S017" in codes(broken)

    def test_S021_row_without_id(self, good_text):
        broken = good_text.replace('<tr data-aim="row2">', "<tr>")
        assert "S021" in codes(broken)

    def test_S023_uncovered_container_child(self, good_text):
        broken = good_text.replace('<li data-aim="li1">First</li>', "<li>First</li>")
        assert "S023" in codes(broken)

    def test_S020_uncovered_slide_child(self, good_text):
        broken = good_text.replace(
            '<h2 data-aim="st" class="text-5xl" style="left:120px; top:100px; width:1000px">',
            '<h2 class="text-5xl" style="left:120px; top:100px; width:1000px">',
        )
        assert "S020" in codes(broken)


class TestVocabularyRules:
    def test_V002_unknown_element(self, good_text):
        broken = good_text.replace(
            '<p data-aim="intro">We audited', '<marquee data-aim="intro">We audited'
        )
        broken = broken.replace("end to end.</p>", "end to end.</marquee>")
        assert "V002" in codes(broken)

    def test_V003_unknown_attribute(self, good_text):
        broken = good_text.replace(
            '<p data-aim="intro">', '<p data-aim="intro" contenteditable="true">'
        )
        assert "V003" in codes(broken)

    def test_V004_arbitrary_value_class(self, good_text):
        broken = good_text.replace('class="font-bold text-3xl"', 'class="w-[347px]"')
        assert "V004" in codes(broken)

    def test_V005_unknown_class(self, good_text):
        broken = good_text.replace(
            'class="font-bold text-3xl"', 'class="font-bold text-3xl bg-neon"'
        )
        assert "V005" in codes(broken)

    def test_V007_style_prop_outside_whitelist(self, good_text):
        # `opacity` is genuinely unregistered. `color:red` would NOT do: colour
        # is a registered paint property since 0.3, so it fires V008 (bad
        # value) rather than V007 (unknown property).
        broken = good_text.replace(
            'style="left:120px; top:100px; width:1000px"', 'style="left:120px; opacity:.5"'
        )
        assert "V007" in codes(broken)

    def test_V008_style_value_grammar(self, good_text):
        broken = good_text.replace("left:120px", "left:12em")
        assert "V008" in codes(broken)

    def test_V009_img_scheme(self, basic_doc):
        basic_doc.add_chunk(
            '<figure data-aim="fig"><img alt="x" src="ftp://example.org/x.png"></figure>',
            author=BOT,
            at=ts(5),
        )
        assert "V009" in {f.code for f in aim.lint(basic_doc)}

    def test_V011_unregistered_theme_slot(self, good_text):
        broken = good_text.replace(":root{--aim-brand-1:#1a73e8}", ":root{--aim-nope:#1a73e8}")
        assert "V011" in codes(broken)

    def test_V010_theme_extra_rule(self, good_text):
        broken = good_text.replace(
            ":root{--aim-brand-1:#1a73e8}", ":root{--aim-brand-1:#1a73e8} body{background:#fecaca}"
        )
        assert "V010" in codes(broken)

    def test_V012_theme_value_grammar(self, good_text):
        broken = good_text.replace("--aim-brand-1:#1a73e8", "--aim-brand-1:url(javascript:x)")
        assert "V012" in codes(broken)


PAINT_PROPS = ("color", "background-color", "border-color")


class TestInlinePaint:
    """Literal per-element paint (spec §3.3): `style` carries geometry AND
    the three paint properties, on a closed six-digit lowercase grammar."""

    @staticmethod
    def _painted(markup: str) -> list:
        doc = aim.new_document(title="Paint")
        doc.add_chunk(markup, author=BOT, at=ts(0))
        return [f for f in aim.lint(doc) if f.level == "error"]

    @pytest.mark.parametrize("prop", PAINT_PROPS)
    def test_a_block_may_carry_each_paint_property(self, prop):
        assert self._painted(f'<p data-aim="p1" style="{prop}:#ff69b4">Painted</p>') == []

    @pytest.mark.parametrize("prop", PAINT_PROPS)
    def test_an_inline_span_may_carry_each_paint_property(self, prop):
        assert (
            self._painted(f'<p data-aim="p1">one <span style="{prop}:#ff69b4">run</span></p>') == []
        )

    @pytest.mark.parametrize("prop", PAINT_PROPS)
    def test_a_positioned_slide_child_may_mix_geometry_and_paint(self, prop):
        assert (
            self._painted(
                '<aim-slide data-aim-container="s1" style="width:960px; height:540px">'
                f'<h2 data-aim="t1" style="left:48px; top:32px; width:450px; {prop}:#ff69b4">'
                "Slide title</h2></aim-slide>"
            )
            == []
        )

    @pytest.mark.parametrize(
        "decl",
        [
            "color:red",
            "color:#fff",
            "color:#FF69B4",
            "color:rgb(255,105,180)",
            "color:rgb(255, 105, 180)",
            "color:#ff69b4ff",
            "color:currentColor",
            "color:transparent",
            "color:var(--aim-brand-1)",
            "color:#ff69b4 !important",
            "background-color:transparent",
            "background-color:url(x.png)",
            "border-color:var(--aim-brand-1)",
        ],
    )
    def test_every_forbidden_spelling_fails_the_grammar_not_the_whitelist(self, decl):
        findings = self._painted(f'<p data-aim="p1" style="{decl}">x</p>')
        assert {f.code for f in findings} == {"V008"}

    def test_an_unregistered_property_still_fails_the_whitelist(self):
        findings = self._painted('<p data-aim="p1" style="opacity:.5">x</p>')
        assert {f.code for f in findings} == {"V007"}

    def test_paint_is_legal_in_a_pending_payload(self, basic_doc):
        basic_doc.propose_modify(
            "intro",
            '<p data-aim="intro" style="color:#ff69b4">Pink intro.</p>',
            author=BOT,
            at=ts(20),
        )
        assert [f for f in aim.lint(basic_doc) if f.level == "error"] == []


class TestSecurityRules:
    def test_X001_forbidden_element(self, good_text):
        broken = good_text.replace(
            '<p data-aim="intro">We audited the Q2 numbers end to end.</p>',
            '<p data-aim="intro">x<iframe src="https://e.org"></iframe></p>',
        )
        assert "X001" in codes(broken)

    def test_X002_event_handler(self, good_text):
        broken = good_text.replace('<p data-aim="intro">', '<p data-aim="intro" onclick="steal()">')
        assert "X002" in codes(broken)

    def test_X002_inside_slide(self, good_text):
        # editors render slide markup verbatim — the linter is the boundary
        broken = good_text.replace('<h2 data-aim="st"', '<h2 data-aim="st" onclick="steal()"')
        assert "X002" in codes(broken)

    def test_X002_inside_figure(self, basic_doc):
        basic_doc.add_chunk(
            '<figure data-aim="fig"><img src="https://e.org/a.png" alt="a"></figure>',
            author=BOT,
            at=ts(5),
        )
        # canonical form sorts attributes, so anchor the splice on alt=
        text = basic_doc.dumps().replace('<img alt="a"', '<img alt="a" onerror="steal()"')
        assert "X002" in codes(text)

    def test_X003_javascript_url(self, basic_doc):
        basic_doc.add_chunk(
            '<p data-aim="lnk"><a href="https://ok.org">ok</a></p>', author=BOT, at=ts(5)
        )
        text = basic_doc.dumps().replace('href="https://ok.org"', 'href="javascript:alert(1)"')
        assert "X003" in codes(text)

    def test_X004_executable_script(self, good_text):
        broken = good_text.replace("</body>", "<script>alert(1)</script>\n</body>")
        # also violates section order; the security code must be among them
        assert "X004" in codes(broken)

    def test_X005_free_style_block(self, good_text):
        broken = good_text.replace("</head>", "<style>body{background:red}</style>\n</head>")
        assert "X005" in codes(broken)


class TestPendingLaneRules:
    def test_P008_unknown_target(self, basic_doc):
        basic_doc.propose_delete("intro", author=BOT, at=ts(5))
        text = basic_doc.dumps().replace('data-for="intro"', 'data-for="ghost"')
        assert "P008" in codes(text)

    def test_P009_double_pending_modify(self, basic_doc):
        basic_doc.propose_modify("intro", '<p data-aim="intro">a</p>', author=BOT, at=ts(5))
        text = basic_doc.dumps()
        card_start = text.index("<aim-proposal ")
        card_end = text.index("</aim-proposal>") + len("</aim-proposal>")
        card = text[card_start:card_end]
        dup = card.replace('id="p-', 'id="p-zz')
        text = text.replace(card, card + "\n" + dup)
        assert "P009" in codes(text)

    def test_P006_modify_without_payload(self, basic_doc):
        basic_doc.propose_modify("intro", '<p data-aim="intro">a</p>', author=BOT, at=ts(5))
        text = basic_doc.dumps()
        start = text.index("<template>")
        end = text.index("</template>") + len("</template>")
        assert "P006" in codes(text[:start] + text[end:])

    def test_P007_delete_with_payload(self, basic_doc):
        basic_doc.propose_delete("intro", author=BOT, at=ts(5))
        text = basic_doc.dumps().replace(
            'data-for="intro"></aim-proposal>',
            'data-for="intro"><template><p data-aim="intro">x</p></template></aim-proposal>',
        )
        assert "P007" in codes(text)

    def test_P010_payload_id_mismatch(self, basic_doc):
        basic_doc.propose_modify("intro", '<p data-aim="intro">a</p>', author=BOT, at=ts(5))
        text = basic_doc.dumps().replace(
            '<template><p data-aim="intro">', '<template><p data-aim="other">'
        )
        assert "P010" in codes(text)

    def test_P011_dangling_add_anchor(self, basic_doc):
        basic_doc.propose_add("<p>x</p>", author=BOT, after="intro", at=ts(5))
        text = basic_doc.dumps().replace('data-anchor-after="intro"', 'data-anchor-after="ghost"')
        assert "P011" in codes(text)

    def test_theme_payload_grammar_checked(self, basic_doc):
        basic_doc.propose_theme({"--aim-brand-1": "#333333"}, author=BOT, at=ts(5))
        text = basic_doc.dumps().replace(
            "<template><style data-aim-theme>:root{--aim-brand-1:#333333}",
            "<template><style data-aim-theme>:root{--aim-brand-1:#333333} body{background:#f00}",
        )
        assert "V010" in codes(text)


class TestHistoryAndCacheRules:
    def test_H005_non_canonical_json_line(self, good_text):
        broken = good_text.replace('{"action":"add",', '{ "action":"add",', 1)
        assert "H005" in codes(broken)

    def test_H006_chain_break_reported(self, good_text):
        broken = good_text.replace(
            "We audited the Q2 numbers end to end.", "Silently different text."
        )
        assert "H006" in codes(broken)

    def test_H001_flattened_doc_warns_only(self, lifecycle_doc):
        lifecycle_doc.flatten()
        text = lifecycle_doc.dumps()
        assert "H001" in warn_codes(text) and not codes(text)

    def test_H004_pruned_history_warns(self, lifecycle_doc):
        lifecycle_doc.prune(before="reviewed")
        assert "H004" in warn_codes(lifecycle_doc.dumps())

    def test_H003_bad_event_field(self, good_text):
        broken = good_text.replace('"action":"add"', '"action":"explode"', 1)
        assert "H003" in codes(broken)

    def test_M001_stale_summary_warns(self, basic_doc):
        basic_doc.set_summary("about the doc", model="m")
        basic_doc.modify_chunk("intro", '<p data-aim="intro">Moved on.</p>', author=ME, at=ts(5))
        assert "M001" in warn_codes(basic_doc.dumps())

    def test_M002_stale_embedding_warns(self, basic_doc):
        basic_doc.set_embedding("intro", model="m", vec=[0.5])
        basic_doc.modify_chunk("intro", '<p data-aim="intro">Moved on.</p>', author=ME, at=ts(5))
        assert "M002" in warn_codes(basic_doc.dumps())

    def test_C001_non_canonical_file(self, good_text):
        broken = good_text.replace('class="font-bold text-3xl"', 'class="text-3xl font-bold"')
        assert "C001" in codes(broken)

    def test_C002_self_closing_non_void_element(self):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="x"><span></span></p>', author=ME, at=ts(0))
        doc.flatten()
        broken = doc.dumps().replace("<span></span>", "<span/>")
        assert codes(broken) == {"C002"}

    def test_S000_unparseable(self):
        assert "S000" in {f.code for f in lint_text("<html><p></html>")}


def test_S031_slide_marked_as_chunk(good_text=None):
    doc = aim.new_document(title="t")
    doc.add_chunk("<p>anchor</p>", author=ME, at=ts(0))
    text = doc.dumps().replace(
        "</body>",
        '<aim-slide data-aim="sx" style="width:960px; height:540px">'
        '<h2 style="left:60px; top:50px; width:600px">T</h2></aim-slide>\n</body>',
    )
    assert "S031" in codes(text)
