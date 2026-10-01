"""The document review policy in the aim:doc settings block (spec §5.6)."""

from __future__ import annotations

import json

import pytest

import aimformat as aim
from aimformat.review import ReviewPolicy
from conftest import BOT, ME, ts

ADA = aim.human("Ada")
MCP = aim.external("aim-mcp")


def _settings(doc: aim.AimDocument) -> dict:
    return doc.doc_settings


def _errors(doc: aim.AimDocument) -> list:
    return [f for f in aim.lint_text(doc.dumps()) if f.level == "error"]


def _declared(version: str) -> aim.AimDocument:
    doc = aim.new_document(title="Older")
    doc.add_chunk('<p data-aim="p1">One.</p>', author=BOT, at=ts(0))
    older = aim.loads(
        doc.dumps().replace(
            f'data-aim-version="{aim.SPEC_VERSION}"', f'data-aim-version="{version}"'
        )
    )
    older.checkpoint("as-authored", at=ts(1))
    return older


class TestSetAndClear:
    def test_off_by_default(self, basic_doc):
        assert basic_doc.review_policy is None

    def test_switching_on_is_an_evented_aim_doc_edit(self, basic_doc):
        basic_doc.set_page_setup({"size": "Letter"}, author=ME, at=ts(2))
        policy = basic_doc.set_review_policy(
            "auto", by=ADA, author=MCP, explanation="User asked: 'auto-accept'", at=ts(3)
        )
        assert policy == ReviewPolicy("auto", ADA)
        assert policy.auto
        ev = basic_doc.history[-1]
        assert (ev.kind, ev.target, ev.action) == ("direct_edit", "aim:doc", "modify")
        # author is whoever wrote it; by is the person it acts for
        assert ev.get("author") == {"type": "external", "id": "aim-mcp"}
        assert ev.get("explanation") == "User asked: 'auto-accept'"
        after = json.loads(ev.get("after").split("\n")[1])
        assert after["review"] == {"agents": "auto", "by": {"type": "human", "id": "Ada"}}
        assert after["page"]["size"] == "Letter"  # page kept
        assert "review" not in json.loads(ev.get("before").split("\n")[1])
        assert basic_doc.verify() == []
        assert _errors(basic_doc) == []

    def test_unnamed_human_reads_as_the_user(self, basic_doc):
        basic_doc.set_review_policy("auto", by=aim.Actor("human"), author=BOT, at=ts(2))
        assert basic_doc.review_policy.by == aim.Actor("human")
        assert _settings(basic_doc)["review"]["by"] == {"type": "human"}

    def test_clearing_removes_the_field_and_is_undoable(self, basic_doc):
        basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(2))
        basic_doc.set_review_policy(None, author=ADA, at=ts(3))
        assert basic_doc.review_policy is None
        assert "review" not in _settings(basic_doc)
        basic_doc.undo(author=ADA, at=ts(4))
        assert basic_doc.review_policy == ReviewPolicy("auto", ADA)
        assert basic_doc.verify() == []
        assert _errors(basic_doc) == []

    def test_unknown_keys_are_preserved(self, basic_doc):
        doc = basic_doc
        doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(2))
        settings = dict(doc.doc_settings)
        settings["review"]["x_note"] = "kept"
        settings["x_other"] = 1
        doc._state.set_doc_settings_markup(doc._settings_script(settings))
        doc.set_review_policy("auto", by=ME, author=ME, at=ts(3))
        after = doc.doc_settings
        assert after["review"]["x_note"] == "kept"
        assert after["x_other"] == 1
        assert after["review"]["by"] == {"type": "human", "id": "luca"}

    @pytest.mark.parametrize(
        "agents,by,match",
        [
            ("auto", BOT, "human actor"),
            ("auto", None, "human actor"),
            ("required", ADA, "reserved"),
            ("sometimes", ADA, "unknown review policy"),
        ],
    )
    def test_refusals(self, basic_doc, agents, by, match):
        with pytest.raises(aim.InvalidOperation, match=match):
            basic_doc.set_review_policy(agents, by=by, author=ADA, at=ts(2))
        assert basic_doc.review_policy is None

    def test_unchanged_is_refused(self, basic_doc):
        with pytest.raises(aim.InvalidOperation, match="review policy unchanged"):
            basic_doc.set_review_policy(None, author=ADA, at=ts(2))
        basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(3))
        with pytest.raises(aim.InvalidOperation, match="review policy unchanged"):
            basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(4))

    def test_set_page_setup_keeps_the_policy(self, basic_doc):
        basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(2))
        basic_doc.set_page_setup({"size": "A5"}, author=ME, at=ts(3))
        assert basic_doc.review_policy == ReviewPolicy("auto", ADA)
        assert basic_doc.page_setup.size == "A5"


class TestVersionFloor:
    def test_switching_on_a_05_document_records_the_upgrade_in_the_same_batch(self):
        older = _declared("0.5")
        older.set_review_policy("auto", by=ADA, author=MCP, at=ts(2))
        upgrade, edit = older.history[-2:]
        assert (upgrade.target, upgrade.get("before"), upgrade.get("after")) == (
            "aim:version",
            "0.5",
            "0.6",
        )
        assert upgrade.batch == edit.batch
        # the upgrade is authored by the author of the edit that needs it
        assert upgrade.get("author") == MCP.to_obj()
        assert older.spec_version == "0.6"
        assert older.verify() == []
        assert _errors(older) == []

    def test_clearing_keeps_06(self):
        older = _declared("0.5")
        older.set_review_policy("auto", by=ADA, author=ADA, at=ts(2))
        older.set_review_policy(None, author=ADA, at=ts(3))
        assert older.spec_version == "0.6"
        assert _errors(older) == []

    def test_doc_hash_replay_covers_the_settings_change_across_the_upgrade(self):
        older = _declared("0.5")
        h0 = older.doc_hash
        seq0 = older.seq
        older.set_review_policy("auto", by=ADA, author=ADA, at=ts(2))
        assert older.state_at(seq0).doc_hash == h0
        assert older.state_at(seq0).spec_version == "0.5"
        assert older.verify() == []

    def test_a_policy_under_an_05_declaration_is_s035(self, basic_doc):
        basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(2))
        stale = basic_doc.dumps().replace(
            f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.5"'
        )
        codes = {f.code for f in aim.lint_text(stale) if f.level == "error"}
        assert "S035" in codes


class TestLint:
    @pytest.mark.parametrize(
        "review",
        [
            {"agents": "sometimes", "by": {"type": "human"}},
            {"agents": "auto", "by": {"type": "agent", "model": "m"}},
            {"agents": "auto"},
            {"agents": "required", "by": {"type": "human"}},
            "auto",
        ],
    )
    def test_malformed_policy_is_d007(self, basic_doc, review):
        settings = dict(basic_doc.doc_settings)
        settings["review"] = review
        basic_doc._state.set_doc_settings_markup(basic_doc._settings_script(settings))
        codes = {f.code for f in aim.lint_text(basic_doc.dumps()) if f.level == "error"}
        assert "D007" in codes

    def test_a_newer_documents_unknown_agents_value_is_unchecked(self, basic_doc):
        settings = dict(basic_doc.doc_settings)
        settings["review"] = {"agents": "required", "by": {"type": "human"}}
        basic_doc._state.set_doc_settings_markup(basic_doc._settings_script(settings))
        newer = basic_doc.dumps().replace(
            f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="9.0"'
        )
        doc = aim.loads(newer)
        codes = {f.code for f in aim.lint_text(newer)}
        assert "D007" not in codes and "S002" in codes
        # read, but never honoured: only "auto" auto-accepts
        assert doc.review_policy.agents == "required"
        assert not doc.review_policy.auto
        assert doc._auto_policy() is None


class TestPendingSettingsCards:
    def test_a_card_cannot_change_the_policy(self, basic_doc):
        p = basic_doc.propose_page_setup({"size": "A5"}, author=BOT, at=ts(2))
        with_policy = basic_doc._settings_script(
            {**basic_doc.doc_settings, "review": {"agents": "auto", "by": {"type": "human"}}}
        )
        with pytest.raises(aim.InvalidOperation, match="cannot change the review policy"):
            basic_doc.amend_proposal(p.id, with_policy)

    def test_switching_resyncs_pending_settings_cards(self, basic_doc):
        p = basic_doc.propose_page_setup({"size": "A5"}, author=BOT, at=ts(2))
        basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(3))
        card = basic_doc.proposal(p.id)
        assert json.loads(card.payload_html.split("\n")[1])["review"]["agents"] == "auto"
        basic_doc.set_review_policy(None, author=ADA, at=ts(4))
        card = basic_doc.proposal(p.id)
        assert "review" not in json.loads(card.payload_html.split("\n")[1])
        assert _errors(basic_doc) == []

    def test_undoing_the_policy_event_resyncs_pending_cards(self, basic_doc):
        basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(2))
        p = basic_doc.propose_page_setup({"size": "A5"}, author=ME, at=ts(3))
        basic_doc.undo(author=ADA, at=ts(4))  # the policy event (the card is not an edit)
        assert basic_doc.review_policy is None
        card = basic_doc.proposal(p.id)
        assert "review" not in json.loads(card.payload_html.split("\n")[1])
        assert basic_doc.verify() == []

    def test_accepting_a_stale_card_keeps_the_live_policy(self, basic_doc):
        """A card that would flip the policy (written by an older tool, or a
        hostile one) applies its page setup and keeps the live policy; the
        resolution records what actually landed as `applied`."""
        p = basic_doc.propose_page_setup({"size": "A5"}, author=BOT, at=ts(2))
        basic_doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(3))
        # simulate a 0.5 tool that never re-synced the card
        stale = basic_doc._settings_script(
            {"page": {"size": "A5", "orientation": "portrait", "margins": {}}}
        )
        from aimformat.document import _set_card_payload

        _set_card_payload(basic_doc._card_el(p.id), stale)
        basic_doc._rebuild_history_index()
        ev = basic_doc.accept(p.id, decided_by=ME, at=ts(4))
        assert basic_doc.review_policy == ReviewPolicy("auto", ADA)
        assert basic_doc.page_setup.size == "A5"
        assert "applied" in ev.data
        assert basic_doc.verify() == []
