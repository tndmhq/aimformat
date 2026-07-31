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

    def test_same_anchor_adds_accept_in_creation_order(self, basic_doc):
        # two adds anchored on the SAME concrete block (a heading then its
        # paragraph, e.g. "add a translation of the abstract"): each accept
        # inserts directly after the anchor, so without the same-anchor
        # rebind the later block landed FIRST and the pair came out reversed
        t = basic_doc.propose_add(
            '<h2 data-aim="t">Sommario</h2>', author=BOT, after="intro", at=ts(7)
        )
        p = basic_doc.propose_add('<p data-aim="p">Testo.</p>', author=BOT, after="intro", at=ts(8))
        basic_doc.accept(t.id, decided_by=ME, at=ts(9))
        assert basic_doc.proposal(p.id).anchor_after == "t"
        basic_doc.accept(p.id, decided_by=ME, at=ts(10))
        assert basic_doc.body_ids == ["h1", "intro", "t", "p"]
        assert basic_doc.verify() == []

    def test_same_anchor_adds_accept_all_in_creation_order(self, basic_doc):
        basic_doc.propose_add('<h2 data-aim="t">Sommario</h2>', author=BOT, after="intro", at=ts(7))
        basic_doc.propose_add('<p data-aim="p">Testo.</p>', author=BOT, after="intro", at=ts(8))
        basic_doc.propose_add('<p data-aim="q">Coda.</p>', author=BOT, after="intro", at=ts(9))
        basic_doc.accept_all(decided_by=ME, at=ts(10))
        assert basic_doc.body_ids == ["h1", "intro", "t", "p", "q"]
        assert basic_doc.verify() == []

    def test_same_anchor_adds_out_of_order_accept_keeps_creation_order(self, basic_doc):
        t = basic_doc.propose_add(
            '<h2 data-aim="t">Sommario</h2>', author=BOT, after="intro", at=ts(7)
        )
        p = basic_doc.propose_add('<p data-aim="p">Testo.</p>', author=BOT, after="intro", at=ts(8))
        basic_doc.accept(p.id, decided_by=ME, at=ts(9))
        # the earlier-created card keeps its anchor: inserting there already
        # places it before the later card's block
        assert basic_doc.proposal(t.id).anchor_after == "intro"
        basic_doc.accept(t.id, decided_by=ME, at=ts(10))
        assert basic_doc.body_ids == ["h1", "intro", "t", "p"]

    def test_same_anchor_reject_earlier_leaves_later_at_anchor(self, basic_doc):
        t = basic_doc.propose_add(
            '<h2 data-aim="t">Sommario</h2>', author=BOT, after="intro", at=ts(7)
        )
        p = basic_doc.propose_add('<p data-aim="p">Testo.</p>', author=BOT, after="intro", at=ts(8))
        basic_doc.reject(t.id, decided_by=ME, at=ts(9))
        assert basic_doc.proposal(p.id).anchor_after == "intro"
        basic_doc.accept(p.id, decided_by=ME, at=ts(10))
        assert basic_doc.body_ids == ["h1", "intro", "p"]

    def test_same_anchor_head_inserts_land_in_creation_order(self, basic_doc):
        t = basic_doc.propose_add('<h2 data-aim="t">Titolo</h2>', author=BOT, after=None, at=ts(7))
        p = basic_doc.propose_add('<p data-aim="p">Testo.</p>', author=BOT, after=None, at=ts(8))
        basic_doc.accept(t.id, decided_by=ME, at=ts(9))
        assert basic_doc.proposal(p.id).anchor_after == "t"
        basic_doc.accept(p.id, decided_by=ME, at=ts(10))
        assert basic_doc.body_ids == ["t", "p", "h1", "intro"]

    def test_same_anchor_move_after_add_lands_in_creation_order(self, basic_doc):
        a = basic_doc.propose_add('<p data-aim="n1">One.</p>', author=BOT, after="intro", at=ts(7))
        m = basic_doc.propose_move("h1", author=BOT, container="body", after="intro", at=ts(8))
        basic_doc.accept(a.id, decided_by=ME, at=ts(9))
        assert basic_doc.proposal(m.id).anchor_after == "n1"
        basic_doc.accept(m.id, decided_by=ME, at=ts(10))
        assert basic_doc.body_ids == ["intro", "n1", "h1"]
        assert basic_doc.verify() == []

    def test_chain_on_out_of_order_accepted_sibling_keeps_creation_order(self, basic_doc):
        # P2 review finding: A and B share an anchor; C chains on A but was
        # proposed after B. accept(B), accept(A), accept(C) must converge to
        # the accept_all order A, B, C — a chain guarantees "after the
        # parent", and creation order places C after the already-landed B
        a = basic_doc.propose_add('<p data-aim="a">A.</p>', author=BOT, after="intro", at=ts(7))
        b = basic_doc.propose_add('<p data-aim="b">B.</p>', author=BOT, after="intro", at=ts(8))
        c = basic_doc.propose_add('<p data-aim="c">C.</p>', author=BOT, after=a.id, at=ts(9))
        basic_doc.accept(b.id, decided_by=ME, at=ts(10))
        assert basic_doc.proposal(c.id).anchor_after == "b"
        basic_doc.accept(a.id, decided_by=ME, at=ts(11))
        basic_doc.accept(c.id, decided_by=ME, at=ts(12))
        assert basic_doc.body_ids == ["h1", "intro", "a", "b", "c"]
        assert basic_doc.verify() == []

    def test_chain_zone_transitive_keeps_creation_order(self, basic_doc):
        # the same, one chain level deeper: C's zone is reached through TWO
        # pending parents (c → a2 → a1 → intro)
        a1 = basic_doc.propose_add('<p data-aim="a1">A1.</p>', author=BOT, after="intro", at=ts(7))
        a2 = basic_doc.propose_add('<p data-aim="a2">A2.</p>', author=BOT, after=a1.id, at=ts(8))
        b = basic_doc.propose_add('<p data-aim="b">B.</p>', author=BOT, after="intro", at=ts(9))
        c = basic_doc.propose_add('<p data-aim="c">C.</p>', author=BOT, after=a2.id, at=ts(10))
        basic_doc.accept(b.id, decided_by=ME, at=ts(11))
        assert basic_doc.proposal(c.id).anchor_after == "b"
        # a2 was proposed BEFORE b: it stays chained and lands before b
        assert basic_doc.proposal(a2.id).anchor_after == a1.id
        basic_doc.accept(a1.id, decided_by=ME, at=ts(12))
        basic_doc.accept(a2.id, decided_by=ME, at=ts(13))
        basic_doc.accept(c.id, decided_by=ME, at=ts(14))
        assert basic_doc.body_ids == ["h1", "intro", "a1", "a2", "b", "c"]
        assert basic_doc.verify() == []

    def test_chain_zone_accept_all_matches_manual_order(self):
        import aimformat as aim

        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="intro">I.</p>', author=BOT, at=ts(0))
        a = doc.propose_add('<p data-aim="a">A.</p>', author=BOT, after="intro", at=ts(1))
        doc.propose_add('<p data-aim="b">B.</p>', author=BOT, after="intro", at=ts(2))
        doc.propose_add('<p data-aim="c">C.</p>', author=BOT, after=a.id, at=ts(3))
        doc.accept_all(decided_by=ME, at=ts(4))
        assert doc.body_ids == ["intro", "a", "b", "c"]
        assert doc.verify() == []

    def test_chained_move_on_rejected_parent_accepts_as_noop(self):
        # rejecting the parent rebinds the chained move onto the parent's
        # anchor — which can be the move's own target ("after where the
        # parent would have been"). The block is already there: accepting is
        # a harmless no-op, never an "after itself" error
        import aimformat as aim

        doc = aim.new_document(title="T")
        for i, cid in enumerate(("x", "y", "z")):
            doc.add_chunk(f'<p data-aim="{cid}">{cid}</p>', author=BOT, at=ts(i))
        d = doc.propose_delete("z", author=BOT, at=ts(3))
        n = doc.propose_add('<p data-aim="n">N.</p>', author=BOT, at=ts(4))
        assert n.anchor_after == "y"  # LAST projected without the deleted z
        m = doc.propose_move("y", author=BOT, container="body", at=ts(5))
        assert m.anchor_after == n.id  # LAST projected onto the pending tail
        doc.reject(n.id, decided_by=ME, at=ts(6))
        assert doc.proposal(m.id).anchor_after == "y"
        doc.reject(d.id, decided_by=ME, at=ts(7))
        doc.accept(m.id, decided_by=ME, at=ts(8))
        assert doc.body_ids == ["x", "y", "z"]
        assert doc.verify() == []

    def test_move_vacation_dissolves_earlier_anchored_cards(self):
        # an accepted move vacates its source: a card created BEFORE the
        # move and anchored on its target chains onto the merge zone's
        # pending tail, exactly like a deleted anchor — creation order
        # survives accepting the move first
        import aimformat as aim

        def lane():
            doc = aim.new_document(title="T")
            for i, cid in enumerate(("x", "y", "z")):
                doc.add_chunk(f'<p data-aim="{cid}">{cid}</p>', author=BOT, at=ts(i))
            n3 = doc.propose_add('<p data-aim="n3">3.</p>', author=BOT, after="x", at=ts(3))
            n4 = doc.propose_add('<p data-aim="n4">4.</p>', author=BOT, after=None, at=ts(4))
            m = doc.propose_move("x", author=BOT, container="body", after="z", at=ts(5))
            return doc, n3, n4, m

        doc, n3, n4, m = lane()
        for p in (n3, n4, m):
            doc.accept(p.id, decided_by=ME, at=ts(6))
        truth = doc.body_ids

        doc, n3, n4, m = lane()
        doc.accept(m.id, decided_by=ME, at=ts(6))
        assert doc.proposal(n3.id).anchor_after == n4.id
        doc.accept(n3.id, decided_by=ME, at=ts(7))
        doc.accept(n4.id, decided_by=ME, at=ts(8))
        assert doc.body_ids == truth == ["n4", "n3", "y", "z", "x"]
        assert doc.verify() == []

    def test_dissolve_onto_move_pending_block_refuses(self):
        # deleting y would merge A onto x, whose own position is undecided
        # (earlier pending move): the delete's accept refuses out of order,
        # and the completed order converges with creation order
        import aimformat as aim

        doc = aim.new_document(title="T")
        for i, cid in enumerate(("x", "y", "z")):
            doc.add_chunk(f'<p data-aim="{cid}">{cid}</p>', author=BOT, at=ts(i))
        m = doc.propose_move("x", author=BOT, container="body", after="z", at=ts(3))
        a = doc.propose_add('<p data-aim="a">A.</p>', author=BOT, after="y", at=ts(4))
        d = doc.propose_delete("y", author=BOT, at=ts(5))
        before = doc.dumps()
        with pytest.raises(InvalidOperation, match="resolve that move first"):
            doc.accept(d.id, decided_by=ME, at=ts(6))
        assert doc.dumps() == before
        doc.accept(m.id, decided_by=ME, at=ts(6))
        doc.accept(d.id, decided_by=ME, at=ts(7))
        doc.accept(a.id, decided_by=ME, at=ts(8))
        assert doc.body_ids == ["a", "z", "x"]
        assert doc.verify() == []

    def test_same_second_stamps_still_land_in_creation_order(self, basic_doc):
        # _now_iso() has one-second precision: two same-anchor cards with
        # IDENTICAL data-at are a normal SDK lane. Position (dependency-
        # adjusted order) breaks the tie — round-4 review finding
        t = basic_doc.propose_add('<h2 data-aim="t">T.</h2>', author=BOT, after="intro", at=ts(7))
        p = basic_doc.propose_add('<p data-aim="p">P.</p>', author=BOT, after="intro", at=ts(7))
        basic_doc.accept(t.id, decided_by=ME, at=ts(8))
        assert basic_doc.proposal(p.id).anchor_after == "t"
        basic_doc.accept(p.id, decided_by=ME, at=ts(9))
        assert basic_doc.body_ids == ["h1", "intro", "t", "p"]
        assert basic_doc.verify() == []

    def test_superseding_a_move_rebinds_its_dissolve_dependents(self):
        # a delete can dissolve an add onto a pending MOVE (the zone tail);
        # superseding that move must rebind the dependent in the validation
        # projection exactly like the live path — round-4 review finding
        import aimformat as aim

        doc = aim.new_document(title="T")
        for i, cid in enumerate(("w", "x", "y", "z")):
            doc.add_chunk(f'<p data-aim="{cid}">{cid}</p>', author=BOT, at=ts(i))
        m = doc.propose_move("w", author=BOT, container="body", after="x", at=ts(4))
        a = doc.propose_add('<p data-aim="a">A.</p>', author=BOT, after="y", at=ts(5))
        d = doc.propose_delete("y", author=BOT, at=ts(6))
        doc.accept(d.id, decided_by=ME, at=ts(7))
        assert doc.proposal(a.id).anchor_after == m.id  # dissolved onto the move tail
        m2 = doc.propose_move("w", author=BOT, container="body", after="z", at=ts(8))
        assert [p.id for p in doc.proposals if p.action == "move"] == [m2.id]
        # the dependent rebound onto the superseded move's own anchor
        assert doc.proposal(a.id).anchor_after == "x"
        doc.accept(m2.id, decided_by=ME, at=ts(9))
        doc.accept(a.id, decided_by=ME, at=ts(10))
        assert doc.body_ids == ["x", "a", "z", "w"]
        assert doc.verify() == []

    def test_lint_flags_duplicate_pending_moves(self):
        # §5.4's one-move-per-target is a lint invariant too (P018): a
        # foreign lane with two pending moves of one target must not be
        # lint-clean — round-4 review finding
        import aimformat as aim

        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="x">X.</p>', author=BOT, at=ts(0))
        doc.add_chunk('<p data-aim="y">Y.</p>', author=BOT, at=ts(1))
        doc.add_chunk('<p data-aim="z">Z.</p>', author=BOT, at=ts(2))
        doc.propose_move("x", author=BOT, container="body", after="z", at=ts(3))
        m2 = doc.propose_move("y", author=BOT, container="body", after="z", at=ts(4))
        doc._card_el(m2.id).set("data-for", "x")  # foreign-authored duplicate
        assert any(f.code == "P018" for f in lint(doc))

    def test_lint_accepts_dissolve_chain_onto_move_tail(self):
        # §5.2 permits chains onto pending position cards: the dissolve-
        # created anchor onto a MOVE card must stay lint-clean
        import aimformat as aim

        doc = aim.new_document(title="T")
        for i, cid in enumerate(("w", "x", "y", "z")):
            doc.add_chunk(f'<p data-aim="{cid}">{cid}</p>', author=BOT, at=ts(i))
        m = doc.propose_move("w", author=BOT, container="body", after="x", at=ts(4))
        a = doc.propose_add('<p data-aim="a">A.</p>', author=BOT, after="y", at=ts(5))
        d = doc.propose_delete("y", author=BOT, at=ts(6))
        doc.accept(d.id, decided_by=ME, at=ts(7))
        assert doc.proposal(a.id).anchor_after == m.id
        assert lint(doc) == []
        assert doc.verify() == []

    def test_chain_through_non_position_card_is_rejected(self, basic_doc):
        # §5.2: only position cards may be chain anchors. A foreign add
        # anchored on a pending MODIFY id must lint dirty (P011) and refuse
        # to accept, never silently land at the container head
        mod = basic_doc.propose_modify(
            "intro", '<p data-aim="intro">New.</p>', author=BOT, at=ts(7)
        )
        a = basic_doc.propose_add('<p data-aim="a">A.</p>', author=BOT, after="h1", at=ts(8))
        basic_doc._card_el(a.id).set("data-anchor-after", mod.id)  # foreign-authored
        assert any(f.code == "P011" for f in lint(basic_doc))
        with pytest.raises(InvalidOperation, match="not a position card"):
            basic_doc.accept(a.id, decided_by=ME, at=ts(9))

    def test_reversed_stamps_still_land_in_lane_order(self, basic_doc):
        # data-at is advisory: a foreign lane may carry stamps that run
        # backwards. Lane position is the canonical creation order — the
        # same basis _creation_order() and the tracked views use
        a = basic_doc.propose_add('<p data-aim="a">A.</p>', author=BOT, after="intro", at=ts(9))
        b = basic_doc.propose_add('<p data-aim="b">B.</p>', author=BOT, after="intro", at=ts(8))
        basic_doc.accept(a.id, decided_by=ME, at=ts(10))
        assert basic_doc.proposal(b.id).anchor_after == "a"
        basic_doc.accept(b.id, decided_by=ME, at=ts(11))
        assert basic_doc.body_ids == ["h1", "intro", "a", "b"]
        assert basic_doc.verify() == []

    def test_criticmarkup_renders_move_chained_adds(self):
        # an add dissolved onto a pending MOVE tail must not vanish from the
        # criticmarkup view — leftovers surface at the end
        import aimformat as aim

        doc = aim.new_document(title="T")
        for i, cid in enumerate(("w", "x", "y", "z")):
            doc.add_chunk(f'<p data-aim="{cid}">{cid}</p>', author=BOT, at=ts(i))
        m = doc.propose_move("w", author=BOT, container="body", after="x", at=ts(4))
        a = doc.propose_add('<p data-aim="a">PAYLOAD-A</p>', author=BOT, after="y", at=ts(5))
        d = doc.propose_delete("y", author=BOT, at=ts(6))
        doc.accept(d.id, decided_by=ME, at=ts(7))
        assert doc.proposal(a.id).anchor_after == m.id
        md = aim.to_markdown(doc, pending="criticmarkup")
        assert "PAYLOAD-A" in md

    def test_rejected_move_reunifies_the_zone(self):
        # a move proposal splits its block's zone only WHILE PENDING; once
        # rejected, the zone re-unifies and plain creation order applies
        # among the cards still pending (§5.4 — orders interleaved around
        # the pending move fall under the documented move-space limitation)
        import aimformat as aim

        doc = aim.new_document(title="T")
        for i, cid in enumerate(("x", "y", "z")):
            doc.add_chunk(f'<p data-aim="{cid}">{cid}</p>', author=BOT, at=ts(i))
        a = doc.propose_add('<p data-aim="a">A.</p>', author=BOT, after="x", at=ts(7))
        m = doc.propose_move("x", author=BOT, container="body", after="z", at=ts(7))
        b = doc.propose_add('<p data-aim="b">B.</p>', author=BOT, after="x", at=ts(7))
        doc.reject(m.id, decided_by=ME, at=ts(8))
        doc.accept(a.id, decided_by=ME, at=ts(9))
        assert doc.proposal(b.id).anchor_after == "a"  # re-unified: plain siblings
        doc.accept(b.id, decided_by=ME, at=ts(10))
        assert doc.body_ids == ["x", "a", "b", "y", "z"]
        assert doc.verify() == []

    def test_rejecting_a_dissolve_tail_reselects_the_remaining_tail(self):
        # round-7 finding: rejecting the zone-tail card a dissolve chained
        # onto must hand its dependents to the REMAINING tail of that zone,
        # not to the raw block — otherwise they cut in front of zone-mates
        # that creation order puts first
        import aimformat as aim

        def lane():
            doc = aim.new_document(title="T")
            for i, cid in enumerate(("x", "y", "z")):
                doc.add_chunk(f'<p data-aim="{cid}">{cid}</p>', author=BOT, at=ts(i))
            a = doc.propose_add('<p data-aim="a">A.</p>', author=BOT, after="z", at=ts(3))
            b = doc.propose_add('<p data-aim="b">B.</p>', author=BOT, after=a.id, at=ts(4))
            c = doc.propose_add('<p data-aim="c">C.</p>', author=BOT, after="y", at=ts(5))
            d = doc.propose_delete("z", author=BOT, at=ts(6))
            e = doc.propose_add('<p data-aim="e">E.</p>', author=BOT, after=b.id, at=ts(7))
            f = doc.propose_add('<p data-aim="f">F.</p>', author=BOT, after="y", at=ts(8))
            return doc, {"a": a, "b": b, "c": c, "d": d, "e": e, "f": f}

        doc, p = lane()
        for key in ("a", "b", "c", "d", "e"):
            doc.accept(p[key].id, decided_by=ME, at=ts(9))
        doc.reject(p["f"].id, decided_by=ME, at=ts(9))
        truth = doc.body_ids

        doc, p = lane()
        doc.accept(p["d"].id, decided_by=ME, at=ts(9))
        assert doc.proposal(p["a"].id).anchor_after == p["f"].id  # dissolved onto tail F
        doc.accept(p["b"].id, decided_by=ME, at=ts(10))
        doc.accept(p["e"].id, decided_by=ME, at=ts(11))
        doc.reject(p["f"].id, decided_by=ME, at=ts(12))
        # F's rejection re-selects C — the remaining tail of the merged zone
        assert doc.proposal(p["a"].id).anchor_after == p["c"].id
        doc.accept(p["a"].id, decided_by=ME, at=ts(13))
        doc.accept(p["c"].id, decided_by=ME, at=ts(14))
        assert doc.body_ids == truth == ["x", "y", "c", "a", "b", "e"]
        assert doc.verify() == []

    def test_foreign_reordered_chain_keeps_descendants_attached(self):
        # round-8 finding: with a lint-clean foreign card order, a rebind
        # candidate's DIRECT parent can be a candidate while its ultimate
        # holder is not — the exclusion must walk every chain ancestor, or
        # the sibling rebind flattens the chain
        import aimformat as aim

        def lane():
            doc = aim.new_document(title="T")
            doc.add_chunk('<p data-aim="x">X.</p>', author=BOT, at=ts(0))
            n1 = doc.propose_add('<p data-aim="n1">1.</p>', author=BOT, after="x", at=ts(1))
            n2 = doc.propose_add('<p data-aim="n2">2.</p>', author=BOT, after=n1.id, at=ts(2))
            n5 = doc.propose_add('<p data-aim="n5">5.</p>', author=BOT, after=n2.id, at=ts(3))
            n0 = doc.propose_add('<p data-aim="n0">0.</p>', author=BOT, after="x", at=ts(4))
            sec = doc._state.section("aim-proposals")
            assert sec is not None
            order = {n5.id: 0, n1.id: 1, n0.id: 2, n2.id: 3}
            sec.children.sort(key=lambda c: order[c.get("id")])
            return aim.loads(doc.dumps()), (n0.id, n1.id, n2.id, n5.id)

        doc, (n0, n1, n2, n5) = lane()
        for pid in (n1, n2, n5, n0):  # dependency-adjusted creation order
            doc.accept(pid, decided_by=ME, at=ts(9))
        truth = doc.body_ids

        doc, (n0, n1, n2, n5) = lane()
        doc.accept(n0, decided_by=ME, at=ts(9))
        assert doc.proposal(n5).anchor_after == n2  # still chained, not flattened
        doc.accept(n2, decided_by=ME, at=ts(10))
        doc.accept(n1, decided_by=ME, at=ts(11))
        doc.accept(n5, decided_by=ME, at=ts(12))
        assert doc.body_ids == truth
        assert doc.verify() == []

    def test_direct_delete_refuses_with_anchored_pending_cards(self, basic_doc):
        # a dissolve mutates pending cards without an event, which undo()
        # cannot reverse — a DIRECT delete refuses instead: resolve or
        # re-anchor first (accepted delete PROPOSALS still dissolve;
        # resolutions are not undoable) — round-9 review finding
        p = basic_doc.propose_add('<p data-aim="n1">One.</p>', author=BOT, after="intro", at=ts(7))
        with pytest.raises(InvalidOperation, match="anchor on it"):
            basic_doc.delete_chunk("intro", author=ME, at=ts(8))
        basic_doc.accept(p.id, decided_by=ME, at=ts(8))
        basic_doc.delete_chunk("intro", author=ME, at=ts(9))
        assert basic_doc.body_ids == ["h1", "n1"]
        assert basic_doc.verify() == []

    def test_stacked_dissolves_refuse_rather_than_merge_zones(self):
        # round-9 finding: two dissolves collapsing DIFFERENT zones onto one
        # pending tail lose which zone each card came from; the second
        # dissolve refuses until the first tail's chained cards resolve
        import aimformat as aim

        doc = aim.new_document(title="T")
        for i, cid in enumerate(("x", "y", "z")):
            doc.add_chunk(f'<p data-aim="{cid}">{cid}</p>', author=BOT, at=ts(i))
        a = doc.propose_add('<p data-aim="a">A.</p>', author=BOT, after="z", at=ts(3))
        dy = doc.propose_delete("y", author=BOT, at=ts(4))
        b = doc.propose_add('<p data-aim="b">B.</p>', author=BOT, after="x", at=ts(5))
        h = doc.propose_add('<p data-aim="h">H.</p>', author=BOT, after=None, at=ts(6))
        dx = doc.propose_delete("x", author=BOT, at=ts(7))
        dz = doc.propose_delete("z", author=BOT, at=ts(8))

        doc.accept(dy.id, decided_by=ME, at=ts(9))
        doc.accept(dx.id, decided_by=ME, at=ts(10))
        assert doc.proposal(b.id).anchor_after == h.id  # dissolved onto tail H
        with pytest.raises(InvalidOperation, match="stack a second dissolved zone"):
            doc.accept(dz.id, decided_by=ME, at=ts(11))
        # resolving the tail unblocks the lane
        doc.reject(h.id, decided_by=ME, at=ts(11))
        doc.accept(dz.id, decided_by=ME, at=ts(12))
        doc.accept(b.id, decided_by=ME, at=ts(13))
        doc.accept(a.id, decided_by=ME, at=ts(14))
        assert doc.body_ids == ["b", "a"]
        assert doc.verify() == []

    def test_dissolve_retains_table_shell(self):
        # round-9 finding: a dissolved thead row keeps its shell through the
        # chain AND through a chain bypass — it must never fall back to a
        # bare container slot outside its row section
        import aimformat as aim

        doc = aim.new_document(title="T")
        doc.add_chunk(
            '<table data-aim-container="tbl"><thead>'
            '<tr data-aim="hh"><th>head</th></tr></thead><tbody>'
            '<tr data-aim="r1"><td>one</td></tr></tbody></table>',
            author=BOT,
            at=ts(0),
        )
        t = doc.propose_add(
            "<tr><th>T</th></tr>", author=BOT, container="tbl", after=None, at=ts(1)
        )
        doc._card_el(t.id).set("data-anchor-shell", "thead")  # foreign-authored
        assert doc.proposal(t.id).anchor_shell == "thead"
        a = doc.propose_add(
            "<tr><th>A</th></tr>", author=BOT, container="tbl", after="hh", at=ts(2)
        )
        d = doc.propose_delete("hh", author=BOT, at=ts(3))
        doc.accept(d.id, decided_by=ME, at=ts(4))
        assert doc.proposal(a.id).anchor_after == t.id
        assert doc.proposal(a.id).anchor_shell == "thead"  # retained
        doc.reject(t.id, decided_by=ME, at=ts(5))
        assert doc.proposal(a.id).anchor_shell == "thead"  # restored on bypass
        doc.accept(a.id, decided_by=ME, at=ts(6))
        html = doc._state.serial("tbl") or ""
        head = html.index("<thead")
        body = html.index("<tbody")
        assert head < html.index("<th>A</th>") < body
        assert doc.verify() == []

    def test_accepted_delete_rebinds_pending_cards_to_predecessor(self, basic_doc):
        p = basic_doc.propose_add('<p data-aim="n1">One.</p>', author=BOT, after="h1", at=ts(7))
        d = basic_doc.propose_delete("h1", author=BOT, at=ts(8))
        basic_doc.accept(d.id, decided_by=ME, at=ts(9))
        assert basic_doc.proposal(p.id).anchor_after is None  # h1 was first
        basic_doc.accept(p.id, decided_by=ME, at=ts(10))
        assert basic_doc.body_ids == ["n1", "intro"]
        assert basic_doc.verify() == []

    def test_new_move_supersedes_pending_move_of_same_target(self, basic_doc):
        # §5.4: a move replaces a pending move of the same target, exactly as
        # modify/delete replace each other — the latest instruction counts.
        # (Twin moves used to coexist and made the lane's outcome depend on
        # the acceptance sequence — P2 review finding on this fix.)
        m1 = basic_doc.propose_move("h1", author=BOT, container="body", after="intro", at=ts(7))
        m2 = basic_doc.propose_move("h1", author=BOT, container="body", after="intro", at=ts(8))
        assert [p.id for p in basic_doc.proposals] == [m2.id]
        ev = next(e for e in basic_doc.history if e.get("proposal") == m1.id)
        assert ev.get("decision") == "superseded" and ev.get("superseded_by") == m2.id
        basic_doc.accept(m2.id, decided_by=ME, at=ts(9))
        assert basic_doc.body_ids == ["intro", "h1"]
        assert basic_doc.verify() == []

    def test_superseded_move_lane_converges_for_any_accept_order(self):
        import aimformat as aim

        def lane():
            doc = aim.new_document(title="T")
            doc.add_chunk('<p data-aim="x">X.</p>', author=BOT, at=ts(0))
            doc.add_chunk('<p data-aim="z">Z.</p>', author=BOT, at=ts(1))
            doc.propose_move("x", author=BOT, container="body", after="z", at=ts(2))
            a = doc.propose_add('<p data-aim="a">A.</p>', author=BOT, after="z", at=ts(3))
            m2 = doc.propose_move("x", author=BOT, container="body", after="z", at=ts(4))
            return doc, a, m2

        doc, a, m2 = lane()
        doc.accept(a.id, decided_by=ME, at=ts(5))
        doc.accept(m2.id, decided_by=ME, at=ts(6))
        first = doc.body_ids

        doc, a, m2 = lane()
        doc.accept(m2.id, decided_by=ME, at=ts(5))
        doc.accept(a.id, decided_by=ME, at=ts(6))
        assert doc.body_ids == first == ["z", "a", "x"]
        assert doc.verify() == []

    def test_accept_child_before_parent_lands_at_the_chain_zone(self, basic_doc):
        # a chained card may be accepted first (§5.4): it lands at the
        # chain's zone, and the parent — inserting directly after the same
        # anchor — lands in front of it when it arrives
        p1 = basic_doc.propose_add('<p data-aim="n1">One.</p>', author=ME, at=ts(7))
        p2 = basic_doc.propose_add('<p data-aim="n2">Two.</p>', author=ME, after=p1.id, at=ts(8))
        basic_doc.accept(p2.id, decided_by=ME, at=ts(9))
        assert basic_doc.body_ids == ["h1", "intro", "n2"]
        ev = basic_doc.history[-1]
        assert ev.get("anchor") == {"after": "intro", "container": "body"}
        basic_doc.accept(p1.id, decided_by=ME, at=ts(10))
        assert basic_doc.body_ids == ["h1", "intro", "n1", "n2"]
        assert basic_doc.verify() == []

    def test_dissolved_zone_chains_onto_merged_zone_tail(self):
        # review round-3 example: A after y, B after x, then delete y. The
        # dissolved card chains onto the merged zone's tail card, so its
        # block keeps landing after that whole zone — under EVERY sequence
        import itertools

        import aimformat as aim

        def lane():
            doc = aim.new_document(title="T")
            for i, cid in enumerate(("x", "y", "z")):
                doc.add_chunk(f'<p data-aim="{cid}">{cid}</p>', author=BOT, at=ts(i))
            a = doc.propose_add('<p data-aim="a">A.</p>', author=BOT, after="y", at=ts(3))
            b = doc.propose_add('<p data-aim="b">B.</p>', author=BOT, after="x", at=ts(4))
            d = doc.propose_delete("y", author=BOT, at=ts(5))
            return doc, [a, b, d]

        for order in itertools.permutations(range(3)):
            doc, cards = lane()
            for step, i in enumerate(order):
                doc.accept(cards[i].id, decided_by=ME, at=ts(6 + step))
            assert doc.body_ids == ["x", "b", "a", "z"], f"order {order}"
            assert doc.verify() == []

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
