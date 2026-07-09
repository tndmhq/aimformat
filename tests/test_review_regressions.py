"""Regression tests for findings from the v0.1 self-review.

Each test pins a bug that shipped in the initial 0.1.0 commit and was found
during the post-ship review (docs/log review entry). Keep them even when
they look redundant with broader tests — they encode the exact failure.
"""
import re

import pytest

import aimformat as aim
from aimformat.errors import InvalidOperation

from conftest import BOT, ME, ts


@pytest.fixture
def table_doc():
    doc = aim.new_document(title="T")
    doc.add_chunk('<table data-aim-container="tbl">'
                  '<thead><tr data-aim="h"><th>K</th></tr></thead>'
                  '<tbody><tr data-aim="r1"><td>1</td></tr>'
                  '<tr data-aim="r2"><td>2</td></tr></tbody></table>',
                  author=ME, at=ts(0))
    return doc


class TestShellAnchors:
    """Deleting the first tbody row must not un-delete into the thead."""

    def test_delete_first_body_row_records_shell(self, table_doc):
        table_doc.delete_chunk("r1", author=ME, at=ts(1))
        anchor = table_doc.history[-1].get("anchor")
        assert anchor == {"after": None, "container": "tbl", "shell": "tbody"}

    def test_undo_restores_row_into_its_shell(self, table_doc):
        before = table_doc._state.serial("tbl")
        table_doc.delete_chunk("r1", author=ME, at=ts(1))
        table_doc.undo(author=ME, at=ts(2))
        assert table_doc._state.serial("tbl") == before
        assert table_doc.verify() == []

    def test_delete_header_row_round_trips(self, table_doc):
        before = table_doc._state.serial("tbl")
        table_doc.delete_chunk("h", author=ME, at=ts(1))
        assert table_doc.history[-1].get("anchor")["shell"] == "thead"
        table_doc.undo(author=ME, at=ts(2))
        assert table_doc._state.serial("tbl") == before
        assert table_doc.verify() == []

    def test_state_at_walks_shelled_deletes(self, table_doc):
        h0 = table_doc.doc_hash
        table_doc.delete_chunk("r1", author=ME, at=ts(1))
        past = table_doc.state_at(1)
        assert past.doc_hash == h0

    def test_list_anchors_carry_no_shell(self, rich_doc):
        rich_doc.delete_chunk("li1", author=ME, at=ts(30))
        assert "shell" not in rich_doc.history[-1].get("anchor")


class TestUndoRedoZone:
    """Stacked and interleaved undo/redo walk the zone as a proper stack."""

    def test_undo_undo_redo_redo_full_cycle(self, basic_doc):
        ids_full = basic_doc.body_ids
        basic_doc.undo(author=ME, at=ts(10))
        basic_doc.undo(author=ME, at=ts(11))
        assert basic_doc.body_ids == []
        basic_doc.redo(author=ME, at=ts(12))
        basic_doc.redo(author=ME, at=ts(13))
        assert basic_doc.body_ids == ids_full
        assert basic_doc.verify() == []

    def test_undo_after_redo_targets_the_redone_edit(self, basic_doc):
        basic_doc.undo(author=ME, at=ts(10))   # undoes intro-add
        basic_doc.redo(author=ME, at=ts(11))   # restores intro
        basic_doc.undo(author=ME, at=ts(12))   # must undo intro again
        assert basic_doc.body_ids == ["h1"]
        assert basic_doc.verify() == []

    def test_third_redo_raises_cleanly(self, basic_doc):
        basic_doc.undo(author=ME, at=ts(10))
        basic_doc.redo(author=ME, at=ts(11))
        with pytest.raises(InvalidOperation):
            basic_doc.redo(author=ME, at=ts(12))


class TestThemePayloadValidation:
    def test_accept_with_hostile_theme_tweak_rejected(self, basic_doc):
        p = basic_doc.propose_theme({"--aim-brand-1": "#333333"}, author=BOT,
                                    at=ts(10))
        with pytest.raises(InvalidOperation):
            basic_doc.accept(p.id, decided_by=ME, at=ts(11),
                             applied='<style data-aim-theme>:root{--aim-brand-1:'
                                     "#333333} body{background:red}</style>")

    def test_accept_with_valid_theme_tweak(self, basic_doc):
        p = basic_doc.propose_theme({"--aim-brand-1": "#333333"}, author=BOT,
                                    at=ts(10))
        basic_doc.accept(p.id, decided_by=ME, at=ts(11),
                         applied="<style data-aim-theme>:root{--aim-brand-1:"
                                 "#444444}</style>")
        assert basic_doc.theme["--aim-brand-1"] == "#444444"
        assert basic_doc.verify() == []

    def test_propose_raw_theme_markup_validated(self, basic_doc):
        with pytest.raises(InvalidOperation):
            basic_doc.propose_modify(
                "aim:theme",
                "<style data-aim-theme>:root{--aim-evil:#000}</style>",
                author=BOT, at=ts(10))


class TestSupersededByIntegrity:
    def test_superseded_by_is_never_a_placeholder(self, basic_doc):
        basic_doc.propose_modify("intro", '<p data-aim="intro">v1</p>',
                                 author=BOT, at=ts(10))
        p2 = basic_doc.propose_modify("intro", '<p data-aim="intro">v2</p>',
                                      author=BOT, at=ts(11))
        ev = next(e for e in basic_doc.history
                  if e.get("decision") == "superseded")
        assert ev.get("superseded_by") == p2.id
        assert "(new)" not in basic_doc.dumps()

    def test_supersede_chain_of_three(self, basic_doc):
        basic_doc.propose_modify("intro", '<p data-aim="intro">v1</p>',
                                 author=BOT, at=ts(10))
        basic_doc.propose_modify("intro", '<p data-aim="intro">v2</p>',
                                 author=BOT, at=ts(11))
        p3 = basic_doc.propose_modify("intro", '<p data-aim="intro">v3</p>',
                                      author=BOT, at=ts(12))
        assert [p.id for p in basic_doc.proposals] == [p3.id]
        chain = [e.get("superseded_by") for e in basic_doc.history
                 if e.get("decision") == "superseded"]
        assert len(chain) == 2 and all(chain)
        assert basic_doc.verify() == []


# ===========================================================================
# Wave 2: findings from the three independent review agents
# ===========================================================================

class TestAnchorResolutionFamily:
    """The systemic family: resolution must honor the context it claims."""

    def test_move_to_last_when_already_last_is_a_noop_error(self, basic_doc):
        with pytest.raises(InvalidOperation):
            basic_doc.move_chunk("intro", author=ME, at=ts(10))  # already last
        basic_doc.chunk("intro")  # still present, tree unharmed
        assert basic_doc.verify() == []

    def test_failed_move_never_mutates(self, rich_doc):
        before = rich_doc.doc_hash
        with pytest.raises(aim.TargetNotFound):
            rich_doc.move_chunk("li1", container="list", after="ghost",
                                author=ME, at=ts(30))
        assert rich_doc.doc_hash == before and rich_doc.verify() == []

    def test_delete_after_nested_container_records_container_anchor(self):
        doc = aim.new_document(title="T")
        doc.add_chunk('<aim-slide data-aim-container="s1" '
                      'style="width:1920px; height:1080px">'
                      '<ul data-aim-container="lst" '
                      'style="left:10px; top:10px; width:100px">'
                      '<li data-aim="i1">a</li></ul>'
                      '<p data-aim="x" style="left:10px; top:200px">after</p>'
                      "</aim-slide>", author=ME, at=ts(0))
        doc.checkpoint("cp", at=ts(1))
        doc.delete_chunk("x", author=ME, at=ts(2))
        ev = doc.history[-1]
        assert ev.get("anchor")["after"] == "lst"
        doc.undo(author=ME, at=ts(3))
        assert doc.verify() == []
        past = doc.state_at(1)
        slide_kids = [e.chunk_id or e.container_id for e in
                      past._state.container_node("s1").elements()]
        assert slide_kids == ["lst", "x"]

    def test_delete_of_nested_container_itself_round_trips(self):
        doc = aim.new_document(title="T")
        doc.add_chunk('<aim-slide data-aim-container="s1" '
                      'style="width:1920px; height:1080px">'
                      '<h2 data-aim="t" style="left:10px; top:10px">T</h2>'
                      '<ul data-aim-container="lst" '
                      'style="left:10px; top:100px; width:100px">'
                      '<li data-aim="i1">a</li></ul></aim-slide>',
                      author=ME, at=ts(0))
        h0 = doc.doc_hash
        doc.delete_chunk("lst", author=ME, at=ts(1))
        doc.undo(author=ME, at=ts(2))
        assert doc.doc_hash == h0 and doc.verify() == []

    def test_insert_anchor_must_live_in_stated_container(self, rich_doc):
        rich_doc.add_chunk('<ul data-aim-container="lb">'
                           '<li data-aim="b1">x</li></ul>', author=ME,
                           at=ts(30))
        with pytest.raises(aim.TargetNotFound):
            rich_doc.add_chunk("<li>stray</li>", author=ME, container="list",
                               after="b1", at=ts(31))

    def test_last_in_slide_ignores_items_of_nested_containers(self):
        doc = aim.new_document(title="T")
        doc.add_chunk('<aim-slide data-aim-container="s1" '
                      'style="width:1920px; height:1080px">'
                      '<ul data-aim-container="lst" '
                      'style="left:10px; top:10px; width:100px">'
                      '<li data-aim="i1">a</li></ul></aim-slide>',
                      author=ME, at=ts(0))
        c = doc.add_chunk('<p data-aim="cap" style="left:10px; top:400px">'
                          "caption</p>", author=ME, container="s1", at=ts(1))
        slide = doc._state.container_node("s1")
        assert [e.chunk_id or e.container_id for e in slide.elements()] == \
            ["lst", "cap"]
        assert doc.history[-1].get("anchor") == \
            {"after": "lst", "container": "s1"}

    def test_public_add_first_into_table_defaults_to_tbody(self, table_doc):
        doc = table_doc
        doc.add_chunk('<tr data-aim="r0"><td>zero</td></tr>', author=ME,
                      container="tbl", after=None, at=ts(1))
        html = doc._state.serial("tbl")
        thead = html[html.index("<thead>"):html.index("</thead>")]
        assert "r0" not in thead and doc.verify() == []
        assert doc.history[-1].get("anchor")["shell"] == "tbody"

    def test_chunk_ids_with_proposal_prefix_are_reassigned(self, basic_doc):
        c = basic_doc.add_chunk('<p data-aim="p-note1">n</p>', author=ME,
                                at=ts(10))
        assert not c.id.startswith("p-")


class TestPayloadAndIdIntegrity:
    def test_modify_container_keeps_marker_and_covers_new_items(self, rich_doc):
        got = rich_doc.modify_chunk(
            "list", '<ul data-aim-container="list">'
                    '<li data-aim="li1">First</li><li>NEW ITEM</li></ul>',
            author=ME, at=ts(30))
        assert got.id == "list"
        assert "list" in rich_doc.containers
        html = rich_doc._state.serial("list")
        assert 'data-aim-container="list"' in html
        assert html.count("data-aim=") == 2  # li1 kept + NEW ITEM covered
        assert not [f for f in aim.lint(rich_doc) if f.level == "error"]

    def test_modify_container_with_chunk_marker_rejected(self, rich_doc):
        with pytest.raises(InvalidOperation):
            rich_doc.modify_chunk("list",
                                  '<ul data-aim="list"><li>x</li></ul>',
                                  author=ME, at=ts(30))

    def test_payload_only_ids_stay_burned(self):
        doc = aim.new_document(title="T")
        doc.add_chunk('<ul data-aim-container="lst">'
                      '<li data-aim="item1">a</li></ul>', author=ME, at=ts(0))
        doc.delete_chunk("lst", author=ME, at=ts(1))
        c = doc.add_chunk('<p data-aim="item1">new life</p>', author=ME,
                          at=ts(2))
        assert c.id != "item1"

    def test_accepted_move_resolution_passes_event_schema(self, basic_doc):
        pr = basic_doc.propose_move("intro", container="body", after=None,
                                    author=BOT, at=ts(10))
        basic_doc.accept(pr.id, decided_by=ME, at=ts(11))
        ev = basic_doc.history[-1]
        assert ev.get("from") and ev.get("to")
        assert ev.validate() == []
        assert not [f for f in aim.lint(basic_doc) if f.level == "error"]

    def test_theme_value_grammar_enforced_on_write(self, basic_doc):
        with pytest.raises(InvalidOperation):
            basic_doc.set_theme(
                {"--aim-brand-1": 'url("https://evil.example/x")'},
                author=ME, at=ts(10))
        with pytest.raises(InvalidOperation):
            basic_doc.propose_theme({"--aim-font-body": "x;}body{color:red"},
                                    author=BOT, at=ts(11))

    def test_pack_assets_atomic_on_undecodable_image(self):
        from test_css_assets_meta import DATA_URI
        doc = aim.new_document(title="T")
        doc.add_chunk(f'<figure data-aim="f"><img alt="ok" src="{DATA_URI}">'
                      '<img alt="bad" src="data:image/svg+xml,<svg/>">'
                      "</figure>", author=ME, at=ts(0))
        h0 = doc.doc_hash
        with pytest.raises(InvalidOperation):
            doc.pack_assets(author=aim.external("packer"), at=ts(1))
        assert doc.doc_hash == h0 and doc.verify() == []

    def test_pack_assets_events_share_one_batch(self):
        from test_css_assets_meta import DATA_URI
        doc = aim.new_document(title="T")
        for i, cid in enumerate(("f1", "f2")):
            doc.add_chunk(f'<figure data-aim="{cid}">'
                          f'<img alt="a" src="{DATA_URI}"></figure>',
                          author=ME, at=ts(i))
        doc.pack_assets(author=aim.external("packer"), at=ts(5))
        packs = [e for e in doc.history if e.action == "modify"]
        assert len({e.batch for e in packs}) == 1

    def test_prune_refuses_to_drop_everything(self, rich_doc):
        with pytest.raises(InvalidOperation):
            rich_doc.prune(before=10_000)


class TestVerifierHardening:
    """lint_text must convert hostile input into findings, never raise."""

    HOSTILE_META = ('<script type="application/aim-meta+json">\n{not json]\n'
                    "</script>\n")

    def hostile(self, basic_doc, mutate):
        return mutate(basic_doc.dumps())

    @pytest.mark.parametrize("mutate", [
        lambda t: t.replace("<title>", TestVerifierHardening.HOSTILE_META
                            + "<title>"),
        lambda t: t.replace("</body>", '<script type="application/'
                            'aim-embeddings+jsonl">\n[1,2,3]\n</script>\n'
                            "</body>"),
        lambda t: t.replace('{"action":"add"', "42 ", 1),
        lambda t: t.replace("</body>", '<script type="application/'
                            'aim-meta+json">\n{"summary":"a string"}\n'
                            "</script>\n</body>"),
    ], ids=["malformed-meta", "embeddings-array", "history-non-object",
            "summary-not-dict"])
    def test_never_raises_on_hostile_caches(self, basic_doc, mutate):
        findings = aim.lint_text(self.hostile(basic_doc, mutate))
        assert any(f.level == "error" for f in findings)

    def test_meta_missing_summary_is_M004(self, basic_doc):
        text = basic_doc.dumps().replace(
            "<title>", '<script type="application/aim-meta+json">\n'
            '{"toc":[]}\n</script>\n<title>')
        assert "M004" in {f.code for f in aim.lint_text(text)}

    def test_asset_registry_not_exempt_from_security(self, basic_doc):
        text = basic_doc.dumps().replace(
            "</body>",
            "<aim-assets>\n"
            '<svg aria-hidden="true" height="0" width="0">\n'
            '<symbol id="asset-aaaaaaaaaaaa" viewBox="0 0 10 10">'
            '<image height="10" width="10" '
            'href="javascript:alert(1)"/></symbol>\n'
            "</svg>\n</aim-assets>\n</body>")
        codes = {f.code for f in aim.lint_text(text) if f.level == "error"}
        assert codes & {"X003", "V009"}

    def test_container_modify_proposal_lints_clean(self, rich_doc):
        rich_doc.propose_modify(
            "list", '<ul data-aim-container="list">'
                    '<li data-aim="li1">First</li></ul>',
            author=BOT, at=ts(30))
        assert not [f for f in aim.lint_text(rich_doc.dumps())
                    if f.level == "error"]

    def test_nested_chunk_is_S024(self, basic_doc):
        text = basic_doc.dumps().replace(
            '<p data-aim="intro">Intro paragraph.</p>',
            '<section data-aim="intro"><p data-aim="inner">x</p></section>')
        assert "S024" in {f.code for f in aim.lint_text(text)}

    def test_add_anchor_cycle_is_P015(self, basic_doc):
        basic_doc.propose_add("<p>one</p>", author=BOT, at=ts(10))
        text = basic_doc.dumps()
        card = text[text.index("<aim-proposal "):
                    text.index("</aim-proposal>") + len("</aim-proposal>")]
        pid = card.split('id="')[1].split('"')[0]
        looped = card.replace('data-anchor-after="intro"',
                              'data-anchor-after="p-zzzz"')
        twin = card.replace(pid, "p-zzzz").replace(
            'data-anchor-after="intro"', f'data-anchor-after="{pid}"')
        text = text.replace(card, looped + "\n" + twin)
        assert "P015" in {f.code for f in aim.lint_text(text)}


class TestNormalFormHardening:
    def test_doc_hash_single_valued_for_void_and_style_spellings(self):
        a = aim.loads(_mini('<p data-aim="x" style="left:3px; top:5px">t<br>'
                            "</p>"))
        b = aim.loads(_mini('<p data-aim="x" style="top:5px;left:3px">t<br/>'
                            "</p>"))
        assert a.doc_hash == b.doc_hash

    def test_c001_rejects_non_normal_style_order(self, basic_doc):
        basic_doc.add_chunk('<aim-slide data-aim-container="s" '
                            'style="width:1920px; height:1080px">'
                            '<p data-aim="q" style="left:1px; top:2px">x</p>'
                            "</aim-slide>", author=ME, at=ts(10))
        text = basic_doc.dumps().replace("left:1px; top:2px",
                                         "top:2px;left:1px")
        assert "C001" in {f.code for f in aim.lint_text(text)}

    def test_class_tokens_deduped(self):
        doc = aim.new_document(title="T")
        c = doc.add_chunk('<p class="font-bold font-bold text-lg">x</p>',
                          author=ME, at=ts(0))
        assert 'class="font-bold text-lg"' in c.html

    def test_duplicate_style_props_last_wins(self):
        doc = aim.new_document(title="T")
        doc.add_chunk('<aim-slide data-aim-container="s" '
                      'style="width:1920px; height:1080px">'
                      '<p data-aim="q" style="left:1px; left:9px; top:2px">x'
                      "</p></aim-slide>", author=ME, at=ts(0))
        assert 'style="left:9px; top:2px"' in doc._state.serial("q")


# ===========================================================================
# Wave 3: findings from the Codex deep code review (2026-07-08)
# ===========================================================================


class TestFragmentIsTheTrustBoundary:
    """AIM-01: lint validates the whole parsed file, not just the first
    <html>. Forbidden markup outside the modeled body must not lint clean."""

    def test_top_level_script_after_html_is_S028(self, basic_doc):
        text = basic_doc.dumps() + "<script>alert(1)</script>\n"
        assert "S028" in {f.code for f in aim.lint_text(text)}

    def test_top_level_element_after_html_is_S028(self, basic_doc):
        text = basic_doc.dumps() + "<p>loose</p>\n"
        assert "S028" in {f.code for f in aim.lint_text(text)}

    def test_second_html_element_is_S028(self, basic_doc):
        text = basic_doc.dumps() + '<html data-aim-version="0.1"></html>\n'
        assert "S028" in {f.code for f in aim.lint_text(text)}

    def test_forbidden_head_child_is_flagged(self, basic_doc):
        text = basic_doc.dumps().replace(
            "</title>",
            '</title>\n<iframe src="https://evil.example"></iframe>')
        codes = {f.code for f in aim.lint_text(text)}
        assert codes & {"S029", "X001"}

    def test_event_handler_on_proposal_card_is_X002(self, basic_doc):
        basic_doc.propose_delete("intro", author=BOT, at=ts(10))
        canon = aim.loads(basic_doc.dumps().replace(
            "<aim-proposal ", '<aim-proposal onclick="alert(1)" ', 1)).dumps()
        assert "X002" in {f.code for f in aim.lint_text(canon)}


class TestEmbeddedCssIsVerified:
    """AIM-02: the machine-managed aim.css block is trusted at the raw tier,
    so lint must pin it to the generated stylesheet."""

    def test_tampered_aim_css_is_X006(self, basic_doc):
        bad = re.sub(
            r'<style data-aim-css="0.1">[\s\S]*?</style>',
            '<style data-aim-css="0.1">\n@import url(https://evil.example/x'
            '.css);\nbody{background:red}\n</style>', basic_doc.dumps())
        assert "X006" in {f.code for f in aim.lint_text(bad)}

    def test_generated_css_lints_clean(self, basic_doc):
        assert "X006" not in {f.code for f in aim.lint_text(basic_doc.dumps())}


class TestProposalAnchorsAreContainerScoped:
    """AIM-03: proposal anchors resolve container-scoped, like direct ops —
    a proposal that can never be accepted must never be created or lint clean."""

    def test_propose_add_cross_container_anchor_raises(self, rich_doc):
        with pytest.raises(aim.TargetNotFound):
            rich_doc.propose_add("<li>x</li>", author=BOT, container="list",
                                 after="intro", at=ts(30))

    def test_propose_move_to_foreign_anchor_raises(self, rich_doc):
        with pytest.raises(aim.TargetNotFound):
            rich_doc.propose_move("li1", author=BOT, container="body",
                                  after="row1", at=ts(30))

    def test_cross_container_add_anchor_is_P016(self, rich_doc):
        rich_doc.propose_add('<li data-aim="x">new</li>', author=BOT,
                             container="list", after="li1", at=ts(30))
        text = rich_doc.dumps().replace('data-anchor-after="li1"',
                                        'data-anchor-after="intro"')
        assert "P016" in {f.code for f in aim.lint_text(text)}


class TestDuplicateProposalIds:
    """AIM-04: duplicate pending ids are rejected, not silently shadowed."""

    def test_duplicate_ids_are_P017(self, basic_doc):
        basic_doc.propose_add("<p>one</p>", author=BOT, at=ts(10))
        p2 = basic_doc.propose_add("<p>two</p>", author=BOT, at=ts(11))
        p1_id = basic_doc.proposals[0].id
        text = basic_doc.dumps().replace(p2.id, p1_id)
        assert "P017" in {f.code for f in aim.lint_text(text)}

    def test_accept_on_duplicate_id_raises(self, basic_doc):
        basic_doc.propose_add("<p>one</p>", author=BOT, at=ts(10))
        p2 = basic_doc.propose_add("<p>two</p>", author=BOT, at=ts(11))
        p1_id = basic_doc.proposals[0].id
        dup = aim.loads(basic_doc.dumps().replace(p2.id, p1_id))
        with pytest.raises(InvalidOperation):
            dup.accept(p1_id, decided_by=ME, at=ts(12))


class TestHistoryValidationIsActionAware:
    """AIM-05: malformed history yields targeted H-findings, never a generic
    S000 verifier crash."""

    def _move_doc(self, basic_doc):
        basic_doc.move_chunk("h1", author=ME, container="body", after="intro",
                             at=ts(10))
        return basic_doc.dumps()

    def test_move_missing_to_is_H003_not_S000(self, basic_doc):
        broken = re.sub(r',"to":\{[^}]*?\}', "", self._move_doc(basic_doc),
                        count=1)
        codes = {f.code for f in aim.lint_text(broken)}
        assert "H003" in codes and "S000" not in codes

    def test_move_missing_from_is_not_S000(self, basic_doc):
        broken = re.sub(r'"from":\{[^}]*?\},', "", self._move_doc(basic_doc),
                        count=1)
        codes = {f.code for f in aim.lint_text(broken)}
        assert "S000" not in codes and "H003" in codes

    def test_add_missing_anchor_is_H003(self, basic_doc):
        broken = re.sub(r',"anchor":\{[^}]*?\}', "", basic_doc.dumps(), count=1)
        codes = {f.code for f in aim.lint_text(broken)}
        assert "H003" in codes and "S000" not in codes

    def test_event_missing_seq_is_not_S000(self, basic_doc):
        broken = re.sub(r'"seq":\d+,', "", basic_doc.dumps(), count=1)
        codes = {f.code for f in aim.lint_text(broken)}
        assert "S000" not in codes


class TestUrlSchemeValidation:
    """AIM-06: URL checks match by scheme, not raw prefix."""

    @pytest.mark.parametrize("href", ["httpjavascript:alert(1)",
                                      "httpsx://example.com", "mailtox:test"])
    def test_fake_schemes_rejected(self, href):
        doc = aim.new_document(title="T")
        doc.add_chunk(f'<p data-aim="p1"><a href="{href}">l</a></p>',
                      author=ME, at=ts(0))
        codes = {f.code for f in aim.lint_text(doc.dumps())}
        assert codes & {"V009", "X003"}

    @pytest.mark.parametrize("href", ["https://example.com",
                                      "http://example.com", "mailto:a@b.co",
                                      "#sec"])
    def test_real_schemes_pass(self, href):
        doc = aim.new_document(title="T")
        doc.add_chunk(f'<p data-aim="p1"><a href="{href}">l</a></p>',
                      author=ME, at=ts(0))
        assert not [f for f in aim.lint_text(doc.dumps()) if f.level == "error"]


class TestConstructorThemeValues:
    """AIM-07: new_document validates theme values, not just slot names."""

    def test_bad_theme_value_raises(self):
        with pytest.raises(InvalidOperation):
            aim.new_document(title="x", theme={"--aim-brand-1": "not-a-color"})

    def test_valid_theme_value_lints_clean(self):
        doc = aim.new_document(title="x", theme={"--aim-brand-1": "#123456"})
        assert not [f for f in aim.lint_text(doc.dumps()) if f.level == "error"]


class TestProposeMoveNoop:
    """AIM-08: a no-op move proposal is rejected like the direct move is."""

    def test_propose_move_noop_raises(self, basic_doc):
        with pytest.raises(InvalidOperation):
            basic_doc.propose_move("intro", author=BOT, container="body",
                                   after="h1", at=ts(10))

    def test_propose_move_real_move_lints_clean(self, basic_doc):
        p = basic_doc.propose_move("h1", author=BOT, container="body",
                                   after="intro", at=ts(10))
        assert p.action == "move"
        assert not [f for f in aim.lint_text(basic_doc.dumps())
                    if f.level == "error"]


def _mini(construct: str) -> str:
    doc = aim.new_document(title="mini")
    text = doc.dumps()
    return text.replace("<body>", "<body>\n" + construct)
