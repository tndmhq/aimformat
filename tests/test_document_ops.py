"""Direct edits: add / modify / delete / move / theme, anchors, batches."""

import pytest

import aimformat as aim
from aimformat.errors import InvalidOperation, TargetNotFound
from conftest import BOT, ME, ts


class TestAdd:
    def test_add_appends_at_end_by_default(self, basic_doc):
        basic_doc.add_chunk('<p data-aim="new">End.</p>', author=BOT, at=ts(9))
        assert basic_doc.body_ids[-1] == "new"

    def test_add_first_position_with_none(self, basic_doc):
        basic_doc.add_chunk('<p data-aim="new">Front.</p>', author=BOT, after=None, at=ts(9))
        assert basic_doc.body_ids[0] == "new"

    def test_add_after_specific_chunk(self, basic_doc):
        basic_doc.add_chunk('<p data-aim="new">Mid.</p>', author=BOT, after="h1", at=ts(9))
        assert basic_doc.body_ids[:3] == ["h1", "new", "intro"]

    def test_add_into_list_container(self, rich_doc):
        rich_doc.add_chunk(
            '<li data-aim="li9">Ninth</li>', author=BOT, container="list", after="li1", at=ts(9)
        )
        html = rich_doc._state.serial("list")
        assert (
            html.index('data-aim="li1"')
            < html.index('data-aim="li9"')
            < html.index('data-aim="li2"')
        )

    def test_add_first_into_list(self, rich_doc):
        rich_doc.add_chunk(
            '<li data-aim="li0">Zeroth</li>', author=BOT, container="list", after=None, at=ts(9)
        )
        html = rich_doc._state.serial("list")
        assert html.index("li0") < html.index("li1")

    def test_add_row_into_table_shell(self, rich_doc):
        rich_doc.add_chunk(
            '<tr data-aim="row3"><td>gamma</td><td>3</td></tr>',
            author=BOT,
            container="tbl",
            after="row2",
            at=ts(9),
        )
        assert '<tr data-aim="row3">' in rich_doc._state.serial("tbl")

    def test_add_run_of_items(self, rich_doc):
        c = rich_doc.add_chunk(
            '<li data-aim="run">a…</li><li data-aim="run">…b</li>',
            author=BOT,
            container="list",
            at=ts(9),
        )
        assert c.is_run and c.tags == ("li", "li")

    def test_add_run_outside_container_rejected(self, basic_doc):
        with pytest.raises(InvalidOperation):
            basic_doc.add_chunk('<p data-aim="r">a</p><p data-aim="r">b</p>', author=BOT, at=ts(9))

    def test_add_into_unknown_container_raises(self, basic_doc):
        with pytest.raises(TargetNotFound):
            basic_doc.add_chunk("<li>x</li>", author=BOT, container="nope", at=ts(9))

    def test_add_after_unknown_anchor_raises(self, basic_doc):
        with pytest.raises(TargetNotFound):
            basic_doc.add_chunk("<p>x</p>", author=BOT, after="ghost", at=ts(9))

    def test_add_records_event_with_anchor_and_payload(self, basic_doc):
        basic_doc.add_chunk('<p data-aim="new">x</p>', author=BOT, after=None, at=ts(9))
        ev = basic_doc.history[-1]
        assert ev.action == "add" and ev.target == "new"
        assert ev.get("anchor") == {"container": "body", "after": None}
        assert ev.get("after") == '<p data-aim="new">x</p>'

    def test_add_slide_container_whole_subtree(self, basic_doc):
        basic_doc.add_chunk(
            '<aim-slide data-aim-container="s9" '
            'style="width:1920px; height:1080px">'
            '<h2 data-aim="t9" style="left:1px; top:2px">T</h2>'
            "</aim-slide>",
            author=BOT,
            at=ts(9),
        )
        ev = basic_doc.history[-1]
        assert ev.target == "s9" and "aim-slide" in ev.get("after")


class TestModify:
    def test_modify_replaces_and_logs_before_after(self, basic_doc):
        before = basic_doc.chunk("intro").html
        basic_doc.modify_chunk("intro", '<p data-aim="intro">Better.</p>', author=ME, at=ts(9))
        ev = basic_doc.history[-1]
        assert ev.get("before") == before
        assert basic_doc.chunk("intro").text == "Better."

    def test_modify_multi_block_section(self, rich_doc):
        rich_doc.modify_chunk(
            "scope",
            '<section data-aim="scope"><h2>Scope'
            "</h2><p>Q2 and Q3.</p><p>Nothing else.</p>"
            "</section>",
            author=ME,
            at=ts(9),
        )
        assert "Q2 and Q3." in rich_doc.chunk("scope").html

    def test_modify_run_member_count_can_change(self, rich_doc):
        rich_doc.modify_chunk("li2", '<li data-aim="li2">Single now</li>', author=ME, at=ts(9))
        assert not rich_doc.chunk("li2").is_run

    def test_modify_identical_content_rejected(self, basic_doc):
        with pytest.raises(InvalidOperation):
            basic_doc.modify_chunk("intro", basic_doc.chunk("intro").html, author=ME, at=ts(9))

    def test_modify_payload_id_mismatch_rejected(self, basic_doc):
        with pytest.raises(InvalidOperation):
            basic_doc.modify_chunk("intro", '<p data-aim="other">x</p>', author=ME, at=ts(9))

    def test_modify_unknown_target_raises(self, basic_doc):
        with pytest.raises(TargetNotFound):
            basic_doc.modify_chunk("ghost", "<p>x</p>", author=ME, at=ts(9))

    def test_modify_payload_without_id_gets_target_id(self, basic_doc):
        basic_doc.modify_chunk("intro", "<p>No id, adopted.</p>", author=ME, at=ts(9))
        assert basic_doc.chunk("intro").text == "No id, adopted."


class TestDeleteMove:
    def test_delete_removes_and_stores_anchor(self, rich_doc):
        rich_doc.delete_chunk("li1", author=ME, at=ts(9))
        ev = rich_doc.history[-1]
        assert ev.action == "delete"
        assert ev.get("anchor") == {"container": "list", "after": None}
        with pytest.raises(TargetNotFound):
            rich_doc.chunk("li1")

    def test_delete_emptying_container_is_legal(self, rich_doc):
        for cid in ("li1", "li2"):
            rich_doc.delete_chunk(cid, author=ME, at=ts(9))
        assert rich_doc._state.serial("list") == '<ul data-aim-container="list"></ul>'

    def test_move_within_body(self, basic_doc):
        basic_doc.move_chunk("intro", container="body", after=None, author=ME, at=ts(9))
        assert basic_doc.body_ids[0] == "intro"
        ev = basic_doc.history[-1]
        assert ev.get("from") == {"container": "body", "after": "h1"}
        assert ev.get("to") == {"container": "body", "after": None}

    def test_move_between_containers(self, rich_doc):
        rich_doc.add_chunk(
            '<ul data-aim-container="list2"><li data-aim="lx">x</li></ul>', author=BOT, at=ts(8)
        )
        rich_doc.move_chunk("li1", container="list2", after="lx", author=ME, at=ts(9))
        assert 'data-aim="li1"' in rich_doc._state.serial("list2")
        assert 'data-aim="li1"' not in rich_doc._state.serial("list")

    def test_move_run_moves_all_members(self, rich_doc):
        rich_doc.move_chunk("li2", container="list", after=None, author=ME, at=ts(9))
        html = rich_doc._state.serial("list")
        assert html.count('data-aim="li2"') == 2
        assert html.index('data-aim="li2"') < html.index('data-aim="li1"')

    def test_container_move_in_body(self, rich_doc):
        rich_doc.move_chunk("list", container="body", after=None, author=ME, at=ts(9))
        assert rich_doc.body_ids[0] == "list"


class TestTheme:
    def test_set_theme_records_modify(self, basic_doc):
        basic_doc.set_theme({"--aim-brand-1": "#0f766e"}, author=ME, at=ts(9))
        ev = basic_doc.history[-1]
        assert ev.target == "aim:theme" and "before" in ev.data
        assert basic_doc.theme["--aim-brand-1"] == "#0f766e"

    def test_theme_introduction_has_no_before(self):
        doc = aim.new_document(title="T")  # no theme block
        doc.set_theme({"--aim-brand-2": "#111111"}, author=ME, at=ts(0))
        ev = doc.history[-1]
        assert "before" not in ev.data and doc.theme == {"--aim-brand-2": "#111111"}

    def test_unknown_slot_rejected(self, basic_doc):
        with pytest.raises(InvalidOperation):
            basic_doc.set_theme({"--aim-nope": "#fff"}, author=ME, at=ts(9))


class TestBatchesAndViews:
    def test_batch_groups_events(self, basic_doc):
        with basic_doc.batch():
            basic_doc.add_chunk("<p>a</p>", author=BOT, at=ts(8))
            basic_doc.add_chunk("<p>b</p>", author=BOT, at=ts(9))
        b1, b2 = [e.batch for e in basic_doc.history[-2:]]
        assert b1 == b2

    def test_ops_outside_batch_get_fresh_batches(self, basic_doc):
        basic_doc.add_chunk("<p>a</p>", author=BOT, at=ts(8))
        basic_doc.add_chunk("<p>b</p>", author=BOT, at=ts(9))
        b1, b2 = [e.batch for e in basic_doc.history[-2:]]
        assert b1 != b2

    def test_chunk_view_fields(self, rich_doc):
        c = rich_doc.chunk("li2")
        assert c.container == "list" and c.is_run and c.tag == "li"
        assert "part one" in c.text
        s = rich_doc.chunk("st")
        assert s.container == "s1"

    def test_containers_listing(self, rich_doc):
        assert set(rich_doc.containers) == {"list", "tbl", "s1"}

    def test_seq_monotonic(self, rich_doc):
        seqs = [e.seq for e in rich_doc.history]
        assert seqs == list(range(1, len(seqs) + 1))


class TestBareSlidePayload:
    """A payload whose root is aim-slide ALWAYS takes the container path —
    a bare slide (no markers at all, e.g. an editor's new-page template)
    must never be demoted to an opaque chunk with unaddressable children."""

    def test_add_bare_slide_becomes_container(self):
        import aimformat as aim

        doc = aim.new_document(title="t")
        created = doc.add_chunk(
            '<aim-slide style="width:420px; height:595px">'
            '<h2 style="left:42px; top:42px; width:336px">Title</h2>'
            "</aim-slide>",
            author=aim.human("u"),
        )
        assert created.id in doc.containers
        text = doc.dumps()
        assert f'data-aim-container="{created.id}"' in text
        # the child got covered with its own chunk id
        assert '<h2 data-aim="' in text
        assert not [f for f in aim.lint(doc) if f.level == "error"]

    def test_propose_bare_slide_add_accepts_clean(self):
        import aimformat as aim

        doc = aim.new_document(title="t")
        me, bot = aim.human("u"), aim.agent("m")
        doc.add_chunk("<p>anchor</p>", author=me)
        p = doc.propose_add(
            '<aim-slide style="width:960px; height:540px">'
            '<p style="left:60px; top:50px; width:600px">Body</p></aim-slide>',
            container="body",
            author=bot,
        )
        doc.accept(p.id, decided_by=me)
        assert any(True for _ in doc.containers)
        assert not [f for f in aim.lint(doc) if f.level == "error"]


class TestReplacementKindGuard:
    """A replacement keeps the target's kind: a slide payload can never take
    a chunk's place (and a container never becomes a flat block) — the write
    would produce exactly the S030/S031 states the linter rejects."""

    SLIDE = (
        '<aim-slide style="width:420px; height:595px">'
        '<h2 style="left:10px; top:10px; width:300px">X</h2></aim-slide>'
    )

    def test_modify_chunk_to_slide_rejected(self):
        doc = aim.new_document(title="g")
        doc.add_chunk('<p data-aim="p1">text</p>', author=ME, at=ts(0))
        with pytest.raises(InvalidOperation, match="slides are containers"):
            doc.modify_chunk("p1", self.SLIDE, author=ME, at=ts(1))

    def test_propose_modify_chunk_to_slide_rejected(self):
        doc = aim.new_document(title="g")
        doc.add_chunk('<p data-aim="p1">text</p>', author=ME, at=ts(0))
        with pytest.raises(InvalidOperation, match="slides are containers"):
            doc.propose_modify("p1", self.SLIDE, author=BOT, at=ts(1))

    def test_modify_container_to_flat_block_rejected(self):
        doc = aim.new_document(title="g")
        doc.add_chunk(
            '<ul data-aim-container="lst"><li data-aim="i1">one</li></ul>',
            author=ME,
            at=ts(0),
        )
        with pytest.raises(InvalidOperation, match="cannot replace container"):
            doc.modify_chunk("lst", "<p>flattened</p>", author=ME, at=ts(1))

    def test_external_slide_modify_proposal_rejected_at_accept(self):
        # an externally-authored file can carry the bad proposal (creation-time
        # normalization never ran): the accept path must re-guard, not write
        # a slide-as-chunk into the body
        doc = aim.new_document(title="g")
        doc.add_chunk('<p data-aim="p1">text</p>', author=ME, at=ts(0))
        prop = doc.propose_modify("p1", '<p data-aim="p1">tweak</p>', author=BOT, at=ts(1))
        surgical = doc.dumps().replace(
            '<p data-aim="p1">tweak</p>',
            '<aim-slide data-aim="p1" style="width:420px; height:595px"><h2>X</h2></aim-slide>',
            1,
        )
        external = aim.loads(surgical)
        with pytest.raises(InvalidOperation, match="slides are containers"):
            external.accept(prop.id, decided_by=ME)
