"""Whole-batch undo/redo, revert_batch, and the auto-accepted batch view
(spec §6.6 and §5.6)."""

from __future__ import annotations

import pytest

import aimformat as aim
from conftest import BOT, ME, ts

ADA = aim.human("Ada")


def _errors(doc: aim.AimDocument) -> list:
    return [f for f in aim.lint_text(doc.dumps()) if f.level == "error"]


@pytest.fixture
def turned(basic_doc) -> tuple[aim.AimDocument, str]:
    """An auto-accepted agent turn on top of the stack: one batch, three changes."""
    basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(5))
    with basic_doc.batch() as batch:
        a = basic_doc.propose_add('<p data-aim="a1">A.</p>', author=BOT, at=ts(6))
        basic_doc.propose_add('<p data-aim="a2">B.</p>', author=BOT, after=a.id, at=ts(7))
        basic_doc.propose_modify("intro", '<p data-aim="intro">New.</p>', author=BOT, at=ts(8))
    assert basic_doc.proposals == []
    return basic_doc, batch


def _ids(doc: aim.AimDocument) -> list[str]:
    return [c.id for c in doc.chunks]


class TestWholeBatchUndo:
    def test_undo_and_redo_the_whole_batch(self, turned):
        doc, batch = turned
        events = doc.undo(author=ADA, at=ts(9), whole_batch=True)
        assert len(events) == 3
        assert {e.origin for e in events} == {"undo"}
        assert len({e.batch for e in events}) == 1 and events[0].batch != batch
        assert _ids(doc) == ["h1", "intro"]
        assert doc.chunk("intro").text == "Intro paragraph."
        redone = doc.redo(author=ADA, at=ts(10), whole_batch=True)
        assert len(redone) == 3 and {e.origin for e in redone} == {"redo"}
        assert _ids(doc) == ["h1", "intro", "a1", "a2"]
        assert doc.chunk("intro").text == "New."
        assert doc.verify() == []
        assert _errors(doc) == []

    def test_naming_a_batch_that_is_not_on_top_is_refused(self, turned):
        doc, batch = turned
        doc.modify_chunk("h1", '<h1 data-aim="h1">Later</h1>', author=ME, at=ts(9))
        with pytest.raises(aim.InvalidOperation, match="newer edits came after"):
            doc.undo(author=ADA, at=ts(10), whole_batch=True, batch=batch)
        assert doc.chunk("h1").text == "Later"

    def test_the_policy_batch_below_is_a_separate_step(self, turned):
        doc, _ = turned
        doc.undo(author=ADA, at=ts(9), whole_batch=True)
        doc.undo(author=ADA, at=ts(10), whole_batch=True)  # the policy switch
        assert doc.review_policy is None
        assert doc.verify() == []

    def test_upgrade_in_the_batch_stays(self):
        doc = aim.new_document(title="Older")
        doc.add_chunk('<p data-aim="p1">One.</p>', author=BOT, at=ts(0))
        older = aim.loads(
            doc.dumps().replace(f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.5"')
        )
        p = older.propose_delete("p1", author=BOT, accept=True, at=ts(2))
        older.undo(author=ME, at=ts(3), whole_batch=True, batch=p.batch)
        assert older.chunk("p1").text == "One."
        assert older.spec_version == "0.6"  # the upgrade is never inverted
        assert older.verify() == []
        assert _errors(older) == []

    def test_batch_needs_whole_batch(self, turned):
        doc, batch = turned
        with pytest.raises(aim.InvalidOperation, match="whole_batch"):
            doc.undo(author=ADA, batch=batch)  # type: ignore[call-overload]

    def test_undo_refuses_to_strand_pending_cards(self, turned):
        doc, _ = turned
        doc.propose_modify("a1", '<p data-aim="a1">Mine.</p>', author=ME, at=ts(9))
        with pytest.raises(aim.InvalidOperation, match="pending suggestions"):
            doc.undo(author=ADA, at=ts(10), whole_batch=True)
        assert _ids(doc) == ["h1", "intro", "a1", "a2"]


class TestRevertBatch:
    def test_on_top_it_is_a_true_undo(self, turned):
        doc, batch = turned
        events = doc.revert_batch(batch, author=ADA, at=ts(9))
        assert {e.origin for e in events} == {"undo"}
        back = doc.unrevert_batch(events[0].batch, author=ADA, at=ts(10))
        assert {e.origin for e in back} == {"redo"}
        assert _ids(doc) == ["h1", "intro", "a1", "a2"]
        assert doc.verify() == []

    def test_after_an_unrelated_edit_it_writes_ordinary_edits(self, turned):
        doc, batch = turned
        doc.modify_chunk("h1", '<h1 data-aim="h1">Later</h1>', author=ME, at=ts(9))
        events = doc.revert_batch(batch, author=ADA, at=ts(10))
        assert len(events) == 3
        assert {e.origin for e in events} == {"user"}
        assert all(e.get("source") == [{"reverts": batch}] for e in events)
        assert events[0].get("explanation") == f"Reverted auto-accepted changes from batch {batch}"
        assert _ids(doc) == ["h1", "intro"]
        assert doc.chunk("intro").text == "Intro paragraph."
        assert doc.chunk("h1").text == "Later"  # the later edit survives
        assert doc.verify() == []
        assert _errors(doc) == []
        # bringing it back = undoing the revert batch, now on top
        doc.unrevert_batch(events[0].batch, author=ADA, at=ts(11))
        assert _ids(doc) == ["h1", "intro", "a1", "a2"]
        assert doc.chunk("intro").text == "New."
        assert doc.verify() == []

    def test_refused_when_a_touched_chunk_changed(self, turned):
        doc, batch = turned
        doc.modify_chunk("intro", '<p data-aim="intro">Typed.</p>', author=ME, at=ts(9))
        before = doc.dumps()
        with pytest.raises(aim.InvalidOperation, match="has changed since"):
            doc.revert_batch(batch, author=ADA, at=ts(10))
        assert doc.dumps() == before

    def test_refused_when_a_deleted_chunk_is_back(self, basic_doc):
        basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(5))
        p = basic_doc.propose_delete("intro", author=BOT, at=ts(6))
        basic_doc.undo(author=ME, at=ts(7))  # puts intro back through the stack
        with pytest.raises(aim.InvalidOperation, match="already undone"):
            basic_doc.revert_batch(p.batch, author=ADA, at=ts(8))

    def test_moves_revert(self, basic_doc):
        basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(5))
        p = basic_doc.propose_move("intro", author=BOT, container="body", after=None, at=ts(6))
        assert _ids(basic_doc) == ["intro", "h1"]
        basic_doc.add_chunk('<p data-aim="z">Z.</p>', author=ME, at=ts(7))
        basic_doc.revert_batch(p.batch, author=ADA, at=ts(8))
        assert _ids(basic_doc) == ["h1", "intro", "z"]
        assert basic_doc.verify() == []

    def test_a_page_setup_revert_keeps_the_live_policy(self, basic_doc):
        p = basic_doc.propose_page_setup({"size": "A5"}, author=BOT, accept=True, at=ts(5))
        basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(6))
        basic_doc.revert_batch(p.batch, author=ADA, at=ts(7))
        assert basic_doc.page_setup.size == "A4"
        assert basic_doc.review_policy is not None and basic_doc.review_policy.auto
        assert basic_doc.verify() == []
        assert _errors(basic_doc) == []

    def test_a_policy_only_batch_refuses_instead_of_a_silent_no_op(self, basic_doc):
        # the revert keeps the live review policy, so reverting the batch that
        # switched it on changes nothing: that must not read as a success
        basic_doc.set_review_policy("auto", by=ADA, author=BOT, at=ts(5))
        batch = basic_doc.history[-1].batch
        basic_doc.add_chunk('<p data-aim="z">Z.</p>', author=ME, at=ts(6))
        before = basic_doc.dumps()
        with pytest.raises(aim.InvalidOperation, match="only switched the review policy"):
            basic_doc.revert_batch(batch, author=ADA, at=ts(7))
        assert basic_doc.dumps() == before
        assert basic_doc.review_policy is not None

    def test_on_top_a_policy_switch_off_is_not_undone(self, basic_doc):
        """A batch that switched auto-accept off is on top: reverting it
        must not turn auto-accept back on (the revert keeps the live
        policy, on top or not), so it refuses like the non-top case."""
        basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(5))
        basic_doc.set_review_policy(None, author=ADA, at=ts(6))
        batch = basic_doc.history[-1].batch
        before = basic_doc.dumps()
        with pytest.raises(aim.InvalidOperation, match="only switched the review policy"):
            basic_doc.revert_batch(batch, author=BOT, at=ts(7))
        assert basic_doc.dumps() == before
        assert basic_doc.review_policy is None

    def test_on_top_a_page_setup_revert_keeps_the_live_policy(self, basic_doc):
        basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(5))
        with basic_doc.batch() as batch:
            basic_doc.set_page_setup({"size": "A5"}, author=ADA, at=ts(6))
            basic_doc.set_review_policy(None, author=ADA, at=ts(7))
        events = basic_doc.revert_batch(batch, author=BOT, at=ts(8))
        assert basic_doc.page_setup.size == "A4"
        assert basic_doc.review_policy is None
        assert {e.origin for e in events} == {"user"}
        basic_doc.unrevert_batch(events[0].batch, author=BOT, at=ts(9))
        assert basic_doc.page_setup.size == "A5"
        assert basic_doc.review_policy is None
        assert basic_doc.verify() == []
        assert _errors(basic_doc) == []

    def test_unrevert_does_not_redo_a_policy_switch(self, basic_doc):
        basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(5))
        (undo,) = basic_doc.undo(author=ADA, at=ts(6), whole_batch=True)
        assert basic_doc.review_policy is None
        before = basic_doc.dumps()
        with pytest.raises(aim.InvalidOperation, match="switch the review policy back"):
            basic_doc.unrevert_batch(undo.batch, author=BOT, at=ts(7))
        assert basic_doc.dumps() == before
        basic_doc.redo(author=ADA, at=ts(8), whole_batch=True)  # a plain redo still can
        assert basic_doc.review_policy is not None

    def test_unknown_batch(self, turned):
        doc, _ = turned
        with pytest.raises(aim.InvalidOperation, match="no changes"):
            doc.revert_batch("b999", author=ADA)


class TestAutoAcceptedBatches:
    def test_entry_shape(self, turned):
        doc, batch = turned
        (entry,) = doc.auto_accepted_batches()
        assert entry["batch"] == batch
        assert entry["via"] == "policy"
        assert entry["decided_by"] == ADA.to_obj()
        assert entry["proposed_by"] == BOT.to_obj()
        assert entry["changes"] == 3
        assert {t["target"] for t in entry["targets"]} == {"a1", "a2", "intro"}
        assert entry["t"] == ts(8)
        assert (entry["undone"], entry["undoable"], entry["revertable"]) == (False, True, True)

    def test_flags_follow_later_edits(self, turned):
        doc, batch = turned
        doc.modify_chunk("h1", '<h1 data-aim="h1">Later</h1>', author=ME, at=ts(9))
        (entry,) = doc.auto_accepted_batches()
        assert (entry["undoable"], entry["revertable"]) == (False, True)
        doc.modify_chunk("a1", '<p data-aim="a1">Typed.</p>', author=ME, at=ts(10))
        (entry,) = doc.auto_accepted_batches()
        assert (entry["undoable"], entry["revertable"]) == (False, False)

    def test_undone_and_reverted_batches_say_so(self, turned):
        doc, batch = turned
        doc.undo(author=ADA, at=ts(9), whole_batch=True)
        assert doc.auto_accepted_batches()[0]["undone"] is True
        doc.redo(author=ADA, at=ts(10), whole_batch=True)
        assert doc.auto_accepted_batches()[0]["undone"] is False
        doc.modify_chunk("h1", '<h1 data-aim="h1">Later</h1>', author=ME, at=ts(11))
        revert = doc.revert_batch(batch, author=ADA, at=ts(12))
        assert doc.auto_accepted_batches()[0]["undone"] is True
        doc.unrevert_batch(revert[0].batch, author=ADA, at=ts(13))
        assert doc.auto_accepted_batches()[0]["undone"] is False

    def test_bounded_scan_and_count(self, basic_doc):
        basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(5))
        for i in range(8):
            basic_doc.propose_modify(
                "intro", f'<p data-aim="intro">v{i}</p>', author=BOT, at=ts(10 + i)
            )
        assert len(basic_doc.auto_accepted_batches()) == 5
        assert len(basic_doc.auto_accepted_batches(max_batches=2)) == 2
        assert len(basic_doc.auto_accepted_batches(scan_limit=2)) == 2  # one event each
        assert basic_doc.auto_accepted_batches(scan_limit=0) == []
