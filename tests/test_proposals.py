"""The pending lane: propose, supersede, chains, accept/reject, tweaks, amend."""

import pytest

from aimformat.document import AimDocument
from aimformat.errors import InvalidOperation, TargetNotFound
from aimformat.lint import lint
from conftest import BOT, ME, ts


class TestPropose:
    def test_propose_modify_creates_card_not_content_change(self, basic_doc):
        h = basic_doc.doc_hash
        p = basic_doc.propose_modify(
            "intro", '<p data-aim="intro">New.</p>', author=BOT, explanation="e", at=ts(9)
        )
        assert basic_doc.doc_hash == h  # body untouched
        assert basic_doc.proposal(p.id).action == "modify"
        assert basic_doc.chunk("intro").text == "Intro paragraph."

    def test_propose_add_with_anchor(self, basic_doc):
        p = basic_doc.propose_add("<p>After intro.</p>", author=ME, after="intro", at=ts(9))
        assert p.anchor_container == "body" and p.anchor_after == "intro"
        assert p.payload_html and "data-aim=" in p.payload_html

    def test_propose_add_first_position_omits_anchor_after(self, basic_doc):
        p = basic_doc.propose_add("<p>Front.</p>", author=ME, after=None, at=ts(9))
        assert p.anchor_after is None

    def test_propose_delete_and_move_are_payloadless(self, rich_doc):
        d = rich_doc.propose_delete("li1", author=BOT, at=ts(9))
        m = rich_doc.propose_move("row1", author=BOT, container="tbl", after="row2", at=ts(9))
        assert d.payload_html is None and m.payload_html is None

    def test_propose_theme(self, basic_doc):
        p = basic_doc.propose_theme({"--aim-brand-1": "#333333"}, author=BOT, at=ts(9))
        assert p.target == "aim:theme" and ":root{" in (p.payload_html or "")

    def test_propose_modify_unknown_target_raises(self, basic_doc):
        with pytest.raises(TargetNotFound):
            basic_doc.propose_modify("ghost", "<p>x</p>", author=BOT, at=ts(9))

    def test_chained_add_anchors_on_pending_proposal(self, basic_doc):
        p1 = basic_doc.propose_add("<p>First new.</p>", author=ME, at=ts(8))
        p2 = basic_doc.propose_add("<p>Second new.</p>", author=ME, after=p1.id, at=ts(9))
        assert p2.anchor_after == p1.id

    def test_chained_add_to_unknown_proposal_raises(self, basic_doc):
        with pytest.raises(TargetNotFound):
            basic_doc.propose_add("<p>x</p>", author=ME, after="p-nothere", at=ts(9))

    def test_depends_on_recorded(self, basic_doc):
        p1 = basic_doc.propose_theme({"--aim-brand-1": "#444444"}, author=BOT, at=ts(8))
        p2 = basic_doc.propose_modify(
            "h1",
            '<h1 data-aim="h1" class="font-bold text-3xl text-brand-1">Title</h1>',
            author=BOT,
            depends_on=p1.id,
            at=ts(9),
        )
        assert basic_doc.proposal(p2.id).depends_on == p1.id


class TestSupersede:
    def test_second_modify_supersedes_first(self, basic_doc):
        p1 = basic_doc.propose_modify("intro", '<p data-aim="intro">v1</p>', author=BOT, at=ts(8))
        p2 = basic_doc.propose_modify("intro", '<p data-aim="intro">v2</p>', author=BOT, at=ts(9))
        pending = [p.id for p in basic_doc.proposals]
        assert p2.id in pending and p1.id not in pending
        ev = basic_doc.history[-1]
        assert ev.kind == "resolution" and ev.decision == "superseded"
        assert ev.get("superseded_by") == p2.id
        assert ev.get("proposal") == p1.id

    def test_delete_supersedes_pending_modify(self, basic_doc):
        basic_doc.propose_modify("intro", '<p data-aim="intro">v1</p>', author=BOT, at=ts(8))
        basic_doc.propose_delete("intro", author=ME, at=ts(9))
        assert [p.action for p in basic_doc.proposals] == ["delete"]

    def test_superseded_is_not_state_changing(self, basic_doc):
        h = basic_doc.doc_hash
        basic_doc.propose_modify("intro", '<p data-aim="intro">v1</p>', author=BOT, at=ts(8))
        basic_doc.propose_modify("intro", '<p data-aim="intro">v2</p>', author=BOT, at=ts(9))
        assert basic_doc.doc_hash == h


class TestResolve:
    def test_accept_modify_applies_payload(self, basic_doc):
        p = basic_doc.propose_modify(
            "intro", '<p data-aim="intro">Applied.</p>', author=BOT, at=ts(8)
        )
        ev = basic_doc.accept(p.id, decided_by=ME, at=ts(9))
        assert basic_doc.chunk("intro").text == "Applied."
        assert ev.decision == "accepted" and "applied" not in ev.data
        assert ev.get("proposed_by") == {"type": "agent", "model": "claude-opus-4-8"}
        assert not basic_doc.proposals

    def test_accept_with_tweaks_records_applied(self, basic_doc):
        p = basic_doc.propose_modify(
            "intro", '<p data-aim="intro">Robot text.</p>', author=BOT, at=ts(8)
        )
        basic_doc.accept(
            p.id, decided_by=ME, at=ts(9), applied='<p data-aim="intro">Human-corrected text.</p>'
        )
        ev = basic_doc.history[-1]
        assert ev.get("applied") != ev.get("proposed")
        assert basic_doc.chunk("intro").text == "Human-corrected text."

    def test_reject_leaves_body_untouched(self, basic_doc):
        h = basic_doc.doc_hash
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">No.</p>', author=BOT, at=ts(8))
        ev = basic_doc.reject(p.id, decided_by=ME, at=ts(9))
        assert basic_doc.doc_hash == h and ev.decision == "rejected"
        assert "before" in ev.data and "proposed" in ev.data

    def test_accept_add_inserts_at_anchor(self, basic_doc):
        p = basic_doc.propose_add("<p>Front insert.</p>", author=ME, after=None, at=ts(8))
        basic_doc.accept(p.id, decided_by=ME, at=ts(9))
        assert basic_doc.chunks[0].text == "Front insert."
        ev = basic_doc.history[-1]
        assert ev.action == "add" and ev.get("anchor")["after"] is None

    def test_accept_delete_and_move(self, rich_doc):
        d = rich_doc.propose_delete("li1", author=BOT, at=ts(8))
        rich_doc.accept(d.id, decided_by=ME, at=ts(9))
        with pytest.raises(TargetNotFound):
            rich_doc.chunk("li1")
        m = rich_doc.propose_move("row2", author=BOT, container="tbl", after=None, at=ts(10))
        rich_doc.accept(m.id, decided_by=ME, at=ts(11))
        html = rich_doc._state.serial("tbl")
        assert html.index('data-aim="row2"') < html.index('data-aim="row1"')

    def test_accept_theme_proposal(self, basic_doc):
        p = basic_doc.propose_theme({"--aim-brand-1": "#555555"}, author=BOT, at=ts(8))
        basic_doc.accept(p.id, decided_by=ME, at=ts(9))
        assert basic_doc.theme["--aim-brand-1"] == "#555555"

    def test_chain_accept_parent_rebinds_child_to_new_chunk(self, basic_doc):
        p1 = basic_doc.propose_add('<p data-aim="n1">One.</p>', author=ME, at=ts(7))
        p2 = basic_doc.propose_add('<p data-aim="n2">Two.</p>', author=ME, after=p1.id, at=ts(8))
        basic_doc.accept(p1.id, decided_by=ME, at=ts(9))
        assert basic_doc.proposal(p2.id).anchor_after == "n1"
        basic_doc.accept(p2.id, decided_by=ME, at=ts(10))
        assert basic_doc.body_ids[-2:] == ["n1", "n2"]

    def test_chain_reject_parent_rebinds_child_to_parents_anchor(self, basic_doc):
        p1 = basic_doc.propose_add('<p data-aim="n1">One.</p>', author=ME, after="h1", at=ts(7))
        p2 = basic_doc.propose_add('<p data-aim="n2">Two.</p>', author=ME, after=p1.id, at=ts(8))
        basic_doc.reject(p1.id, decided_by=ME, at=ts(9))
        assert basic_doc.proposal(p2.id).anchor_after == "h1"
        basic_doc.accept(p2.id, decided_by=ME, at=ts(10))
        assert basic_doc.body_ids[:3] == ["h1", "n2", "intro"]

    def test_accept_child_before_parent_raises(self, basic_doc):
        p1 = basic_doc.propose_add("<p>One.</p>", author=ME, at=ts(7))
        p2 = basic_doc.propose_add("<p>Two.</p>", author=ME, after=p1.id, at=ts(8))
        with pytest.raises(InvalidOperation):
            basic_doc.accept(p2.id, decided_by=ME, at=ts(9))

    def test_resolution_carries_proposal_metadata(self, basic_doc):
        p = basic_doc.propose_modify(
            "intro", '<p data-aim="intro">x</p>', author=BOT, explanation="why", at=ts(8)
        )
        ev = basic_doc.accept(p.id, decided_by=ME, at=ts(9))
        assert ev.get("proposed_at") == ts(8)
        assert ev.get("explanation") == "why"
        assert ev.get("decided_by") == {"type": "human", "id": "luca"}

    def test_resolve_unknown_proposal_raises(self, basic_doc):
        with pytest.raises(TargetNotFound):
            basic_doc.accept("p-ghost", decided_by=ME, at=ts(9))

    def test_empty_proposals_section_removed(self, basic_doc):
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">x</p>', author=BOT, at=ts(8))
        assert "<aim-proposals>" in basic_doc.dumps()
        basic_doc.reject(p.id, decided_by=ME, at=ts(9))
        assert "<aim-proposals>" not in basic_doc.dumps()


class TestAmend:
    """In-place amend of a pending proposal (spec §5.4: allowed, unrecorded)."""

    def test_amend_modify_replaces_payload_keeps_identity(self, basic_doc):
        p = basic_doc.propose_modify(
            "intro", '<p data-aim="intro">v1</p>', author=BOT, explanation="first", at=ts(8)
        )
        h = basic_doc.doc_hash
        events = len(basic_doc.history)
        out = basic_doc.amend_proposal(p.id, '<p data-aim="intro">v2</p>')
        assert out.id == p.id and out.payload_html and ">v2</p>" in out.payload_html
        assert out.explanation == "first"  # untouched unless passed
        assert out.at == ts(8) and out.batch == p.batch and out.author == p.author
        assert basic_doc.doc_hash == h  # body untouched
        assert len(basic_doc.history) == events  # unrecorded (spec §5.4)
        assert [str(f) for f in lint(basic_doc) if f.level == "error"] == []

    def test_amend_payload_without_marker_inherits_target(self, basic_doc):
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">v1</p>', author=BOT, at=ts(8))
        out = basic_doc.amend_proposal(p.id, "<p>bare replacement</p>")
        assert out.payload_html and 'data-aim="intro"' in out.payload_html

    def test_amend_explanation_only_and_clear(self, basic_doc):
        p = basic_doc.propose_delete("intro", author=BOT, explanation="old", at=ts(8))
        assert basic_doc.amend_proposal(p.id, explanation="new").explanation == "new"
        assert basic_doc.amend_proposal(p.id, explanation="").explanation is None

    def test_amend_add_keeps_proposed_root_id(self, basic_doc):
        p1 = basic_doc.propose_add('<p data-aim="n1">One.</p>', author=ME, at=ts(7))
        p2 = basic_doc.propose_add("<p>Two.</p>", author=ME, after=p1.id, at=ts(8))
        basic_doc.amend_proposal(p1.id, "<p>One, reworded.</p>")
        assert 'data-aim="n1"' in (basic_doc.proposal(p1.id).payload_html or "")
        basic_doc.accept(p1.id, decided_by=ME, at=ts(9))
        assert basic_doc.proposal(p2.id).anchor_after == "n1"  # chain intact
        assert basic_doc.chunk("n1").text == "One, reworded."

    def test_accept_after_amend_applies_amended_payload(self, basic_doc):
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">v1</p>', author=BOT, at=ts(8))
        basic_doc.amend_proposal(p.id, '<p data-aim="intro">v2</p>')
        ev = basic_doc.accept(p.id, decided_by=ME, at=ts(9))
        assert basic_doc.chunk("intro").text == "v2"
        assert ">v2</p>" in ev.get("proposed")  # the amended payload IS the proposal
        assert "applied" not in ev.data

    def test_amend_theme_payload(self, basic_doc):
        p = basic_doc.propose_theme({"--aim-brand-1": "#333333"}, author=BOT, at=ts(8))
        out = basic_doc.amend_proposal(
            p.id, "<style data-aim-theme>:root{--aim-brand-1:#444444}</style>"
        )
        assert "#444444" in (out.payload_html or "")

    def test_amend_survives_roundtrip(self, basic_doc):
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">v1</p>', author=BOT, at=ts(8))
        basic_doc.amend_proposal(p.id, '<p data-aim="intro">v2</p>', explanation="better")
        reloaded = AimDocument.loads(basic_doc.dumps())
        again = reloaded.proposal(p.id)
        assert ">v2</p>" in (again.payload_html or "") and again.explanation == "better"

    def test_amend_add_cannot_flip_root_kind(self, basic_doc):
        """Codex finding: an add amended across the container↔chunk line
        would mint a V003 card (container marker on <p>) or an S031
        document (aim-slide marked as a chunk)."""
        slide = (
            '<aim-slide style="width:960px; height:540px">'
            '<h2 style="left:10px; top:10px; width:400px">T</h2></aim-slide>'
        )
        p_slide = basic_doc.propose_add(slide, author=BOT, at=ts(7))
        with pytest.raises(InvalidOperation):
            basic_doc.amend_proposal(p_slide.id, "<p>now prose?</p>")
        p_chunk = basic_doc.propose_add("<p>Prose.</p>", author=BOT, at=ts(8))
        with pytest.raises(InvalidOperation):
            basic_doc.amend_proposal(p_chunk.id, slide)
        # same-kind amends keep working on both sides
        amended = basic_doc.amend_proposal(
            p_slide.id,
            '<aim-slide style="width:960px; height:540px">'
            '<h2 style="left:20px; top:20px; width:400px">T2</h2></aim-slide>',
        )
        assert "T2" in (amended.payload_html or "")
        assert basic_doc.amend_proposal(p_chunk.id, "<h2>Heading now.</h2>").payload_html

    def test_accept_with_tweaks_cannot_flip_add_root_kind(self, basic_doc):
        """_payload_like is shared with accept(applied=…) on adds — the
        same kind guard applies there."""
        p = basic_doc.propose_add("<p>Prose.</p>", author=BOT, at=ts(7))
        with pytest.raises(InvalidOperation):
            basic_doc.accept(
                p.id,
                decided_by=ME,
                at=ts(8),
                applied='<aim-slide style="width:960px; height:540px"></aim-slide>',
            )

    def test_amend_dangling_modify_fails_fast(self, basic_doc):
        """Target deleted out from under a pending modify: amend refuses
        with a clear error instead of rewriting a card that can only
        explode later at accept (review finding)."""
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">v1</p>', author=BOT, at=ts(8))
        basic_doc.delete_chunk("intro", author=ME, at=ts(9))
        with pytest.raises(TargetNotFound):
            basic_doc.amend_proposal(p.id, '<p data-aim="intro">v2</p>')
        # explanation-only amends still work (no payload validation needed)
        assert basic_doc.amend_proposal(p.id, explanation="still here").explanation == "still here"

    def test_amend_errors(self, basic_doc, rich_doc):
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">v1</p>', author=BOT, at=ts(8))
        with pytest.raises(TargetNotFound):
            basic_doc.amend_proposal("p-ghost", "<p>x</p>")
        with pytest.raises(InvalidOperation):  # nothing to amend
            basic_doc.amend_proposal(p.id)
        with pytest.raises(InvalidOperation):  # wrong id in replacement
            basic_doc.amend_proposal(p.id, '<p data-aim="other">x</p>')
        d = rich_doc.propose_delete("li1", author=BOT, at=ts(8))
        with pytest.raises(InvalidOperation):  # payloadless action
            rich_doc.amend_proposal(d.id, "<p>x</p>")
