"""History: invertibility, verification, tampering, time travel, undo/redo,
checkpoints, flatten/prune."""

import pytest

import aimformat as aim
from aimformat.errors import HistoryError, InvalidOperation
from conftest import BOT, ME, ts


class TestEventShape:
    def test_events_are_canonical_json_lines(self, rich_doc):
        raw = rich_doc._state.script("history").raw
        for line in raw.strip().split("\n"):
            ev = aim.Event.from_json(line)
            assert ev.to_json() == line

    def test_actor_roundtrip(self):
        a = aim.agent("model-x", id="bot-7")
        assert aim.Actor.from_obj(a.to_obj()) == a

    def test_unknown_actor_type_rejected(self):
        with pytest.raises(ValueError):
            aim.Actor("robot")

    def test_event_validate_flags_missing_fields(self):
        ev = aim.Event({"seq": 1, "kind": "direct_edit", "t": ts(0)})
        problems = ev.validate()
        assert any("target" in p for p in problems)
        assert any("author" in p for p in problems)

    def test_event_validate_flags_unknown_kind_and_fields(self):
        assert aim.Event({"kind": "merge", "seq": 1}).validate()
        ev = aim.Event(
            {
                "seq": 1,
                "kind": "checkpoint",
                "t": ts(0),
                "label": "x",
                "doc_hash": "sha256:0",
                "bogus": 1,
            }
        )
        assert any("unknown field" in p for p in ev.validate())

    def test_x_fields_are_allowed(self):
        ev = aim.Event(
            {
                "seq": 1,
                "kind": "checkpoint",
                "t": ts(0),
                "label": "x",
                "doc_hash": "sha256:0",
                "x_vendor": 1,
            }
        )
        assert not ev.validate()


class TestHistoryIndex:
    def test_loaded_history_parses_once_then_updates_incrementally(self, basic_doc, monkeypatch):
        doc = aim.loads(basic_doc.dumps())
        assert doc._history_index is None
        parsed = 0
        original = aim.Event.from_json

        def counted(line):
            nonlocal parsed
            parsed += 1
            return original(line)

        monkeypatch.setattr(aim.Event, "from_json", staticmethod(counted))
        initial_events = len(basic_doc.history)

        assert doc.seq == initial_events
        assert len(doc.history) == initial_events
        index = doc._get_history_index()
        assert parsed == initial_events
        assert index.next_seq == initial_events + 1
        assert index.next_batch == f"b{initial_events + 1}"
        assert {"h1", "intro"} <= index.burned_ids == index.recorded_ids

        doc.add_chunk('<p data-aim="later">Later.</p>', author=BOT, at=ts(9))
        assert doc._get_history_index() is index
        assert doc.seq == initial_events + 1
        assert index.next_seq == initial_events + 2
        assert "later" in index.burned_ids
        assert parsed == initial_events

        # Event.data is open for x_* extensions, so public reads are copies:
        # mutating one must not poison the authoritative cached parse.
        doc.history[-1].data["seq"] = 999
        assert doc.seq == initial_events + 1

    def test_pending_reservations_update_on_amend_and_resolution(self):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="base">Base.</p>', author=BOT, at=ts(0))
        proposal = doc.propose_add(
            '<aim-slide data-aim-container="pending-slide">'
            '<p data-aim="inside-old">Old.</p></aim-slide>',
            author=BOT,
            at=ts(1),
        )
        index = doc._get_history_index()
        assert {proposal.id, "pending-slide", "inside-old"} <= index.recorded_ids
        assert "pending-slide" not in index.burned_ids

        amended = doc.amend_proposal(
            proposal.id,
            '<aim-slide><p data-aim="inside-new">New.</p></aim-slide>',
            at=ts(2),
        )
        assert amended.id == proposal.id
        assert "inside-old" not in index.recorded_ids
        assert {proposal.id, "pending-slide", "inside-new"} <= index.recorded_ids

        doc.reject(proposal.id, decided_by=ME, at=ts(3))
        assert doc.proposals == []
        assert {proposal.id, "pending-slide", "inside-new"} <= index.burned_ids
        # b2 belonged only to the removed card; the b3 resolution stays in
        # history, so smallest-unused allocation intentionally returns b2.
        assert index.next_batch == "b2"

    def test_amend_refreshes_trial_clone_pending_reservations(self):
        authored = aim.new_document(title="T")
        earlier = authored.propose_add(
            '<ul data-aim-container="x"><li data-aim="old">Old.</li></ul>',
            author=BOT,
            after=None,
            at=ts(0),
        )
        later = authored.propose_add(
            '<p data-aim="fresh">Later.</p>',
            author=BOT,
            after=earlier.id,
            at=ts(1),
        )
        foreign_text = authored.dumps().replace(
            '<p data-aim="fresh">Later.</p>', '<p data-aim="old">Later.</p>', 1
        )
        doc = aim.loads(foreign_text)

        amended = doc.amend_proposal(
            earlier.id,
            '<ul data-aim-container="x"><li data-aim="new">New.</li></ul>',
            at=ts(2),
        )

        assert 'data-aim="old"' not in (amended.payload_html or "")
        assert doc.proposal(later.id).payload_html == '<p data-aim="old">Later.</p>'
        index = doc._get_history_index()

        def assert_matches_recompute():
            rebuilt = type(index).build(doc._history_raw(), doc.proposals)
            assert [event.data for event in index.events] == [
                event.data for event in rebuilt.events
            ]
            assert index.raw == rebuilt.raw
            assert index.burned_ids == rebuilt.burned_ids
            assert index.recorded_ids == rebuilt.recorded_ids
            assert index.next_seq == rebuilt.next_seq
            assert index.next_batch == rebuilt.next_batch
            assert index._pending_id_counts == rebuilt._pending_id_counts
            assert index._proposal_payload_ids == rebuilt._proposal_payload_ids
            assert index._batch_counts == rebuilt._batch_counts

        assert_matches_recompute()

        doc.accept_all(decided_by=ME, at=ts(3))

        assert doc.body_ids == ["x", "old"]
        assert doc.verify() == []
        assert_matches_recompute()

    def test_undo_redo_return_events_that_do_not_alias_the_cached_log(self):
        # undo/redo build their inverse from a cached event; the returned
        # Event is the caller's to keep, so mutating its nested objects must
        # never corrupt the cached log out from under the JSONL.
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="c1">One.</p>', author=BOT, at=ts(0))
        doc.add_chunk('<p data-aim="c2">Two.</p>', author=BOT, at=ts(1))
        doc.move_chunk("c1", author=ME, after="c2", at=ts(2))
        doc.delete_chunk("c1", author=ME, at=ts(3))

        def assert_cache_matches_jsonl():
            raw = doc._state.script("history").raw
            jsonl = [aim.Event.from_json(line).data for line in raw.split("\n") if line.strip()]
            assert [e.data for e in doc.history] == jsonl
            assert doc.verify() == []

        undone_delete = doc.undo(author=ME, at=ts(4))  # inverse: add w/ anchor
        undone_delete.data["anchor"]["container"] = "mutated-by-caller"
        assert_cache_matches_jsonl()

        redone = doc.redo(author=ME, at=ts(5))  # inverse of the cached undo
        redone.data["anchor"]["container"] = "mutated-by-caller"
        assert_cache_matches_jsonl()

        doc.undo(author=ME, at=ts(6))  # cancel the redo again
        undone_move = doc.undo(author=ME, at=ts(7))  # inverse: move w/ from/to
        undone_move.data["from"]["after"] = "mutated-by-caller"
        undone_move.data["to"]["container"] = "mutated-by-caller"
        assert_cache_matches_jsonl()


class TestVerify:
    def test_clean_lifecycle_verifies(self, lifecycle_doc):
        assert lifecycle_doc.verify() == []

    def test_verify_survives_serialization_roundtrip(self, lifecycle_doc):
        doc2 = aim.loads(lifecycle_doc.dumps())
        assert doc2.verify() == []

    def test_external_body_edit_is_detected(self, lifecycle_doc):
        text = lifecycle_doc.dumps()
        tampered = text.replace(
            "We audited the Q2 numbers end to end.", "We audited most of the Q2 numbers."
        )
        assert tampered != text
        problems = aim.loads(tampered).verify()
        assert any("mismatch" in p for p in problems)

    def test_tampered_checkpoint_hash_is_detected(self, rich_doc):
        text = rich_doc.dumps()
        h = rich_doc.doc_hash
        tampered = text.replace(h, "sha256:" + "0" * 64)
        problems = aim.loads(tampered).verify()
        assert any("checkpoint" in p for p in problems)

    def test_seq_gap_is_detected(self, basic_doc):
        el = basic_doc._state.script("history")
        el.raw = el.raw.replace('"seq":2', '"seq":5')
        assert any("gap" in p or "ascending" in p for p in basic_doc.verify())

    def test_delete_without_anchor_not_invertible(self, basic_doc):
        basic_doc.delete_chunk("intro", author=ME, at=ts(5))
        el = basic_doc._state.script("history")
        el.raw = el.raw.replace('"anchor":{"after":"h1","container":"body"},', "")
        assert any("anchor" in p for p in basic_doc.verify())

    def test_verify_walks_pruned_history(self, rich_doc):
        rich_doc.prune(before="draft")
        assert rich_doc.verify() == []


class TestUndoRedo:
    def test_undo_restores_previous_state(self, basic_doc):
        h0 = basic_doc.doc_hash
        basic_doc.modify_chunk("intro", '<p data-aim="intro">Changed.</p>', author=ME, at=ts(5))
        basic_doc.undo(author=ME, at=ts(6))
        assert basic_doc.doc_hash == h0
        assert basic_doc.history[-1].origin == "undo"

    def test_undo_is_append_not_rewrite(self, basic_doc):
        n = len(basic_doc.history)
        basic_doc.modify_chunk("intro", '<p data-aim="intro">Changed.</p>', author=ME, at=ts(5))
        basic_doc.undo(author=ME, at=ts(6))
        assert len(basic_doc.history) == n + 2

    def test_redo_reapplies(self, basic_doc):
        basic_doc.modify_chunk("intro", '<p data-aim="intro">Changed.</p>', author=ME, at=ts(5))
        h1 = basic_doc.doc_hash
        basic_doc.undo(author=ME, at=ts(6))
        basic_doc.redo(author=ME, at=ts(7))
        assert basic_doc.doc_hash == h1
        assert basic_doc.history[-1].origin == "redo"

    def test_undo_undo_walks_back_two_edits(self, basic_doc):
        h0 = basic_doc.doc_hash
        basic_doc.modify_chunk("intro", '<p data-aim="intro">One.</p>', author=ME, at=ts(5))
        basic_doc.add_chunk('<p data-aim="extra">Two.</p>', author=ME, at=ts(6))
        basic_doc.undo(author=ME, at=ts(7))  # removes "extra"
        basic_doc.undo(author=ME, at=ts(8))  # restores intro
        assert basic_doc.doc_hash == h0

    def test_undo_of_add_removes_chunk(self, basic_doc):
        basic_doc.add_chunk('<p data-aim="extra">X.</p>', author=ME, at=ts(5))
        basic_doc.undo(author=ME, at=ts(6))
        with pytest.raises(aim.TargetNotFound):
            basic_doc.chunk("extra")

    def test_undo_of_delete_restores_at_anchor(self, rich_doc):
        rich_doc.delete_chunk("li1", author=ME, at=ts(7))
        rich_doc.undo(author=ME, at=ts(8))
        html = rich_doc._state.serial("list")
        assert html.index("li1") < html.index("li2")

    def test_undo_of_move_restores_position(self, basic_doc):
        order0 = basic_doc.body_ids
        basic_doc.move_chunk("intro", container="body", after=None, author=ME, at=ts(5))
        basic_doc.undo(author=ME, at=ts(6))
        assert basic_doc.body_ids == order0

    def test_nothing_to_undo_raises(self, empty_doc):
        with pytest.raises(InvalidOperation):
            empty_doc.undo(author=ME, at=ts(1))

    def test_nothing_to_redo_after_fresh_edit(self, basic_doc):
        basic_doc.modify_chunk("intro", '<p data-aim="intro">A.</p>', author=ME, at=ts(5))
        basic_doc.undo(author=ME, at=ts(6))
        basic_doc.modify_chunk("intro", '<p data-aim="intro">B.</p>', author=ME, at=ts(7))
        with pytest.raises(InvalidOperation):
            basic_doc.redo(author=ME, at=ts(8))

    def test_undo_accepted_resolution(self, basic_doc):
        h0 = basic_doc.doc_hash
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">New.</p>', author=BOT, at=ts(5))
        basic_doc.accept(p.id, decided_by=ME, at=ts(6))
        basic_doc.undo(author=ME, at=ts(7))
        assert basic_doc.doc_hash == h0


class TestCheckpointsAndTravel:
    def test_checkpoint_hash_matches_state(self, rich_doc):
        h = rich_doc.checkpoint("now", at=ts(50))
        assert h == rich_doc.doc_hash
        ev = rich_doc.history[-1]
        assert ev.kind == "checkpoint" and ev.get("doc_hash") == h

    def test_state_at_reconstructs_checkpoint_hash(self, lifecycle_doc):
        cp = next(e for e in lifecycle_doc.history if e.kind == "checkpoint")
        past = lifecycle_doc.state_at(cp.seq)
        assert past.doc_hash == cp.get("doc_hash")

    def test_state_at_before_accept_shows_old_text(self, basic_doc):
        seq0 = basic_doc.seq
        p = basic_doc.propose_modify("intro", '<p data-aim="intro">New.</p>', author=BOT, at=ts(5))
        basic_doc.accept(p.id, decided_by=ME, at=ts(6))
        past = basic_doc.state_at(seq0)
        assert past.chunk("intro").text == "Intro paragraph."
        assert basic_doc.chunk("intro").text == "New."

    def test_state_at_drops_pending_and_caches(self, basic_doc):
        basic_doc.set_summary("s", model="m")
        basic_doc.propose_delete("intro", author=BOT, at=ts(5))
        past = basic_doc.state_at(basic_doc.seq)
        assert past.proposals == [] and past.meta is None

    def test_state_at_truncates_history(self, lifecycle_doc):
        past = lifecycle_doc.state_at(8)
        assert past.seq == 8 and past.verify() == []

    def test_state_below_prune_floor_raises(self, rich_doc):
        rich_doc.prune(before="draft")
        floor = rich_doc.history[0].seq
        with pytest.raises(HistoryError):
            rich_doc.state_at(floor - 2)


class TestLifecycleOps:
    def test_flatten_removes_history_and_embeddings(self, rich_doc):
        rich_doc.set_embedding("intro", model="m", vec=[0.1, 0.2])
        rich_doc.flatten()
        assert rich_doc.history == [] and rich_doc.embeddings == []
        assert aim.loads(rich_doc.dumps()).chunks  # still a valid doc

    def test_prune_by_label_keeps_checkpoint(self, lifecycle_doc):
        dropped = lifecycle_doc.prune(before="reviewed")
        assert dropped > 0
        assert lifecycle_doc.history[0].kind == "checkpoint"
        assert lifecycle_doc.verify() == []

    def test_prune_unknown_label_raises(self, rich_doc):
        with pytest.raises(aim.TargetNotFound):
            rich_doc.prune(before="nope")

    def test_prune_by_seq(self, lifecycle_doc):
        keep_from = lifecycle_doc.history[-3].seq
        lifecycle_doc.prune(before=keep_from)
        assert lifecycle_doc.history[0].seq == keep_from
