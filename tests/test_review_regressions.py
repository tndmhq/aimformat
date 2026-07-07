"""Regression tests for findings from the v0.1 self-review.

Each test pins a bug that shipped in the initial 0.1.0 commit and was found
during the post-ship review (docs/log review entry). Keep them even when
they look redundant with broader tests — they encode the exact failure.
"""
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
