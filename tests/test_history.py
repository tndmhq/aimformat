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
