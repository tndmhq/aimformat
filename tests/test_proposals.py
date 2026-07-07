"""The pending lane: propose, supersede, chains, accept/reject, tweaks."""
import pytest

import aimformat as aim
from aimformat.errors import InvalidOperation, TargetNotFound

from conftest import BOT, ME, ts


class TestPropose:
    def test_propose_modify_creates_card_not_content_change(self, basic_doc):
        h = basic_doc.doc_hash
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">New.</p>',
                                     author=BOT, explanation="e", at=ts(9))
        assert basic_doc.doc_hash == h                 # body untouched
        assert basic_doc.proposal(p.id).action == "modify"
        assert basic_doc.chunk("intro").text == "Intro paragraph."

    def test_propose_add_with_anchor(self, basic_doc):
        p = basic_doc.propose_add("<p>After intro.</p>", author=ME,
                                  after="intro", at=ts(9))
        assert p.anchor_container == "body" and p.anchor_after == "intro"
        assert p.payload_html and "data-aim=" in p.payload_html

    def test_propose_add_first_position_omits_anchor_after(self, basic_doc):
        p = basic_doc.propose_add("<p>Front.</p>", author=ME, after=None,
                                  at=ts(9))
        assert p.anchor_after is None

    def test_propose_delete_and_move_are_payloadless(self, rich_doc):
        d = rich_doc.propose_delete("li1", author=BOT, at=ts(9))
        m = rich_doc.propose_move("row1", author=BOT, container="tbl",
                                  after="row2", at=ts(9))
        assert d.payload_html is None and m.payload_html is None

    def test_propose_theme(self, basic_doc):
        p = basic_doc.propose_theme({"--aim-brand-1": "#333333"}, author=BOT,
                                    at=ts(9))
        assert p.target == "aim:theme" and ":root{" in (p.payload_html or "")

    def test_propose_modify_unknown_target_raises(self, basic_doc):
        with pytest.raises(TargetNotFound):
            basic_doc.propose_modify("ghost", "<p>x</p>", author=BOT, at=ts(9))

    def test_chained_add_anchors_on_pending_proposal(self, basic_doc):
        p1 = basic_doc.propose_add("<p>First new.</p>", author=ME, at=ts(8))
        p2 = basic_doc.propose_add("<p>Second new.</p>", author=ME,
                                   after=p1.id, at=ts(9))
        assert p2.anchor_after == p1.id

    def test_chained_add_to_unknown_proposal_raises(self, basic_doc):
        with pytest.raises(TargetNotFound):
            basic_doc.propose_add("<p>x</p>", author=ME, after="p-nothere",
                                  at=ts(9))

    def test_depends_on_recorded(self, basic_doc):
        p1 = basic_doc.propose_theme({"--aim-brand-1": "#444444"}, author=BOT,
                                     at=ts(8))
        p2 = basic_doc.propose_modify(
            "h1", '<h1 data-aim="h1" class="font-bold text-3xl text-brand-1">'
                  "Title</h1>", author=BOT, depends_on=p1.id, at=ts(9))
        assert basic_doc.proposal(p2.id).depends_on == p1.id


class TestSupersede:
    def test_second_modify_supersedes_first(self, basic_doc):
        p1 = basic_doc.propose_modify("intro", '<p data-aim="intro">v1</p>',
                                      author=BOT, at=ts(8))
        p2 = basic_doc.propose_modify("intro", '<p data-aim="intro">v2</p>',
                                      author=BOT, at=ts(9))
        pending = [p.id for p in basic_doc.proposals]
        assert p2.id in pending and p1.id not in pending
        ev = basic_doc.history[-1]
        assert ev.kind == "resolution" and ev.decision == "superseded"
        assert ev.get("superseded_by") == p2.id
        assert ev.get("proposal") == p1.id

    def test_delete_supersedes_pending_modify(self, basic_doc):
        basic_doc.propose_modify("intro", '<p data-aim="intro">v1</p>',
                                 author=BOT, at=ts(8))
        basic_doc.propose_delete("intro", author=ME, at=ts(9))
        assert [p.action for p in basic_doc.proposals] == ["delete"]

    def test_superseded_is_not_state_changing(self, basic_doc):
        h = basic_doc.doc_hash
        basic_doc.propose_modify("intro", '<p data-aim="intro">v1</p>',
                                 author=BOT, at=ts(8))
        basic_doc.propose_modify("intro", '<p data-aim="intro">v2</p>',
                                 author=BOT, at=ts(9))
        assert basic_doc.doc_hash == h


class TestResolve:
    def test_accept_modify_applies_payload(self, basic_doc):
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">Applied.</p>',
                                     author=BOT, at=ts(8))
        ev = basic_doc.accept(p.id, decided_by=ME, at=ts(9))
        assert basic_doc.chunk("intro").text == "Applied."
        assert ev.decision == "accepted" and "applied" not in ev.data
        assert ev.get("proposed_by") == {"type": "agent",
                                         "model": "claude-opus-4-8"}
        assert not basic_doc.proposals

    def test_accept_with_tweaks_records_applied(self, basic_doc):
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">Robot text.</p>',
                                     author=BOT, at=ts(8))
        basic_doc.accept(p.id, decided_by=ME, at=ts(9),
                         applied='<p data-aim="intro">Human-corrected text.</p>')
        ev = basic_doc.history[-1]
        assert ev.get("applied") != ev.get("proposed")
        assert basic_doc.chunk("intro").text == "Human-corrected text."

    def test_reject_leaves_body_untouched(self, basic_doc):
        h = basic_doc.doc_hash
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">No.</p>',
                                     author=BOT, at=ts(8))
        ev = basic_doc.reject(p.id, decided_by=ME, at=ts(9))
        assert basic_doc.doc_hash == h and ev.decision == "rejected"
        assert "before" in ev.data and "proposed" in ev.data

    def test_accept_add_inserts_at_anchor(self, basic_doc):
        p = basic_doc.propose_add("<p>Front insert.</p>", author=ME,
                                  after=None, at=ts(8))
        basic_doc.accept(p.id, decided_by=ME, at=ts(9))
        assert basic_doc.chunks[0].text == "Front insert."
        ev = basic_doc.history[-1]
        assert ev.action == "add" and ev.get("anchor")["after"] is None

    def test_accept_delete_and_move(self, rich_doc):
        d = rich_doc.propose_delete("li1", author=BOT, at=ts(8))
        rich_doc.accept(d.id, decided_by=ME, at=ts(9))
        with pytest.raises(TargetNotFound):
            rich_doc.chunk("li1")
        m = rich_doc.propose_move("row2", author=BOT, container="tbl",
                                  after=None, at=ts(10))
        rich_doc.accept(m.id, decided_by=ME, at=ts(11))
        html = rich_doc._state.serial("tbl")
        assert html.index('data-aim="row2"') < html.index('data-aim="row1"')

    def test_accept_theme_proposal(self, basic_doc):
        p = basic_doc.propose_theme({"--aim-brand-1": "#555555"}, author=BOT,
                                    at=ts(8))
        basic_doc.accept(p.id, decided_by=ME, at=ts(9))
        assert basic_doc.theme["--aim-brand-1"] == "#555555"

    def test_chain_accept_parent_rebinds_child_to_new_chunk(self, basic_doc):
        p1 = basic_doc.propose_add('<p data-aim="n1">One.</p>', author=ME,
                                   at=ts(7))
        p2 = basic_doc.propose_add('<p data-aim="n2">Two.</p>', author=ME,
                                   after=p1.id, at=ts(8))
        basic_doc.accept(p1.id, decided_by=ME, at=ts(9))
        assert basic_doc.proposal(p2.id).anchor_after == "n1"
        basic_doc.accept(p2.id, decided_by=ME, at=ts(10))
        assert basic_doc.body_ids[-2:] == ["n1", "n2"]

    def test_chain_reject_parent_rebinds_child_to_parents_anchor(self, basic_doc):
        p1 = basic_doc.propose_add('<p data-aim="n1">One.</p>', author=ME,
                                   after="h1", at=ts(7))
        p2 = basic_doc.propose_add('<p data-aim="n2">Two.</p>', author=ME,
                                   after=p1.id, at=ts(8))
        basic_doc.reject(p1.id, decided_by=ME, at=ts(9))
        assert basic_doc.proposal(p2.id).anchor_after == "h1"
        basic_doc.accept(p2.id, decided_by=ME, at=ts(10))
        assert basic_doc.body_ids[:3] == ["h1", "n2", "intro"]

    def test_accept_child_before_parent_raises(self, basic_doc):
        p1 = basic_doc.propose_add("<p>One.</p>", author=ME, at=ts(7))
        p2 = basic_doc.propose_add("<p>Two.</p>", author=ME, after=p1.id,
                                   at=ts(8))
        with pytest.raises(InvalidOperation):
            basic_doc.accept(p2.id, decided_by=ME, at=ts(9))

    def test_resolution_carries_proposal_metadata(self, basic_doc):
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">x</p>',
                                     author=BOT, explanation="why", at=ts(8))
        ev = basic_doc.accept(p.id, decided_by=ME, at=ts(9))
        assert ev.get("proposed_at") == ts(8)
        assert ev.get("explanation") == "why"
        assert ev.get("decided_by") == {"type": "human", "id": "luca"}

    def test_resolve_unknown_proposal_raises(self, basic_doc):
        with pytest.raises(TargetNotFound):
            basic_doc.accept("p-ghost", decided_by=ME, at=ts(9))

    def test_empty_proposals_section_removed(self, basic_doc):
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">x</p>',
                                     author=BOT, at=ts(8))
        assert "<aim-proposals>" in basic_doc.dumps()
        basic_doc.reject(p.id, decided_by=ME, at=ts(9))
        assert "<aim-proposals>" not in basic_doc.dumps()
