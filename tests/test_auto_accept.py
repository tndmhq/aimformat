"""Auto-accept: the review policy honoured by propose_*, per-call accept,
the batch-close rule, and the ``auto`` resolution marker (spec §5.6, §6.2)."""

from __future__ import annotations

import re

import pytest

import aimformat as aim
from aimformat.events import Event
from conftest import BOT, ME, ts

ADA = aim.human("Ada")
TOOL = aim.external("some-script")


def _pids_blanked(text: str) -> str:
    return re.sub(r"p-[a-z0-9]{8}", "p-xxxxxxxx", text)


def _errors(doc: aim.AimDocument) -> list:
    return [f for f in aim.lint_text(doc.dumps()) if f.level == "error"]


def _with_policy(doc: aim.AimDocument) -> aim.AimDocument:
    doc.set_review_policy("auto", by=ADA, author=ADA, at=ts(5))
    return doc


@pytest.fixture
def auto_doc(basic_doc) -> aim.AimDocument:
    return _with_policy(basic_doc)


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


def _assert_auto(ev: Event, via: str, decider: aim.Actor, proposer: aim.Actor) -> None:
    assert ev.kind == "resolution" and ev.decision == "accepted"
    assert ev.get("auto") == via
    assert ev.get("decided_by") == decider.to_obj()
    assert ev.get("proposed_by") == proposer.to_obj()
    assert "applied" not in ev.data


class TestPolicy:
    def test_modify_is_accepted_in_the_creating_batch(self, auto_doc):
        p = auto_doc.propose_modify(
            "intro", '<p data-aim="intro">Sharper intro.</p>', author=BOT, at=ts(6)
        )
        assert auto_doc.proposals == []
        assert auto_doc.chunk("intro").text == "Sharper intro."
        ev = p.resolution
        assert ev is not None
        _assert_auto(ev, "policy", ADA, BOT)
        assert ev.get("proposed") == '<p data-aim="intro">Sharper intro.</p>'
        assert ev.batch == p.batch
        assert auto_doc.resolution_of(p.id).data == ev.data
        assert auto_doc.last_auto_accept.accepted == (p.id,)
        assert auto_doc.verify() == []
        assert _errors(auto_doc) == []

    @pytest.mark.parametrize("kind", ["add", "delete", "move", "theme", "page"])
    def test_every_change_kind(self, auto_doc, kind):
        if kind == "add":
            p = auto_doc.propose_add('<p data-aim="new">New.</p>', author=BOT, at=ts(6))
        elif kind == "delete":
            p = auto_doc.propose_delete("intro", author=BOT, at=ts(6))
        elif kind == "move":
            p = auto_doc.propose_move("intro", author=BOT, container="body", after=None, at=ts(6))
        elif kind == "theme":
            p = auto_doc.propose_theme({"--aim-brand-1": "#333333"}, author=BOT, at=ts(6))
        else:
            p = auto_doc.propose_page_setup({"size": "A5"}, author=BOT, at=ts(6))
        assert auto_doc.proposals == []
        _assert_auto(p.resolution, "policy", ADA, BOT)
        assert auto_doc.verify() == []
        assert _errors(auto_doc) == []
        if kind == "page":
            # applying the page setup kept the policy
            assert auto_doc.review_policy.auto and auto_doc.page_setup.size == "A5"

    def test_external_tools_are_covered(self, auto_doc):
        p = auto_doc.propose_delete("intro", author=TOOL, at=ts(6))
        _assert_auto(p.resolution, "policy", ADA, TOOL)

    def test_human_proposals_wait(self, auto_doc):
        p = auto_doc.propose_delete("intro", author=ME, at=ts(6))
        assert p.resolution is None
        assert [q.id for q in auto_doc.proposals] == [p.id]
        assert auto_doc.last_auto_accept is None

    def test_without_a_policy_agents_wait(self, basic_doc):
        p = basic_doc.propose_delete("intro", author=BOT, at=ts(6))
        assert p.resolution is None and len(basic_doc.proposals) == 1

    def test_switching_on_does_not_sweep_pending_cards(self, basic_doc):
        p = basic_doc.propose_delete("intro", author=BOT, at=ts(4))
        _with_policy(basic_doc)
        assert [q.id for q in basic_doc.proposals] == [p.id]

    def test_accept_false_keeps_one_card_pending(self, auto_doc):
        p = auto_doc.propose_delete("intro", author=BOT, accept=False, at=ts(6))
        assert p.resolution is None and len(auto_doc.proposals) == 1

    def test_batch_knob_false_suppresses_the_policy(self, auto_doc):
        with auto_doc.batch(auto_accept=False):
            auto_doc.propose_delete("intro", author=BOT, at=ts(6))
            auto_doc.propose_modify("h1", '<h1 data-aim="h1">T</h1>', author=BOT, at=ts(7))
        assert len(auto_doc.proposals) == 2
        assert auto_doc.last_auto_accept is None

    def test_nested_batches_inherit_the_outer_knob(self, auto_doc):
        with auto_doc.batch(auto_accept=False):
            with auto_doc.batch(auto_accept=True):
                auto_doc.propose_delete("intro", author=BOT, at=ts(6))
        assert len(auto_doc.proposals) == 1

    def test_a_malformed_policy_fails_closed(self, auto_doc):
        settings = dict(auto_doc.doc_settings)
        settings["review"] = {"agents": "auto", "by": {"type": "agent", "model": "m"}}
        auto_doc._state.set_doc_settings_markup(auto_doc._settings_script(settings))
        p = auto_doc.propose_delete("intro", author=BOT, at=ts(6))
        assert p.resolution is None


class TestRequest:
    def test_per_call_accept_on_an_05_document_records_the_upgrade(self):
        older = _declared("0.5")
        p = older.propose_modify(
            "p1", '<p data-aim="p1">Two.</p>', author=BOT, accept=True, at=ts(2)
        )
        _assert_auto(p.resolution, "request", aim.Actor("human"), BOT)
        upgrade = next(e for e in older.history if e.target == "aim:version")
        assert (upgrade.get("before"), upgrade.get("after")) == ("0.5", "0.6")
        assert upgrade.batch == p.resolution.batch == p.batch
        # authored by the resolution's decider, as §3.7 upgrades always are
        assert upgrade.get("author") == {"type": "human"}
        assert older.verify() == []
        assert _errors(older) == []

    def test_accept_by_names_the_person(self, basic_doc):
        p = basic_doc.propose_delete("intro", author=BOT, accept=True, accept_by=ME, at=ts(6))
        _assert_auto(p.resolution, "request", ME, BOT)

    def test_request_defaults_to_the_policy_owner(self, auto_doc):
        p = auto_doc.propose_delete("intro", author=ME, accept=True, at=ts(6))
        # any author may be requested, even a human one
        _assert_auto(p.resolution, "request", ADA, ME)

    def test_accept_by_needs_accept_true_and_a_human(self, basic_doc):
        with pytest.raises(aim.InvalidOperation, match="needs accept=True"):
            basic_doc.propose_delete("intro", author=BOT, accept_by=ME, at=ts(6))
        with pytest.raises(aim.InvalidOperation, match="human"):
            basic_doc.propose_delete("intro", author=BOT, accept=True, accept_by=BOT, at=ts(6))
        assert basic_doc.proposals == []

    def test_batch_knob_true_requests_every_card(self, basic_doc):
        with basic_doc.batch(auto_accept=True):
            a = basic_doc.propose_delete("intro", author=BOT, at=ts(6))
            b = basic_doc.propose_modify("h1", '<h1 data-aim="h1">T</h1>', author=ME, at=ts(7))
        assert basic_doc.proposals == []
        assert basic_doc.last_auto_accept.accepted == (a.id, b.id)
        assert basic_doc.last_auto_accept.via == "request"

    def test_mixed_vias_share_the_batch(self, auto_doc):
        with auto_doc.batch() as batch:
            a = auto_doc.propose_delete("intro", author=BOT, at=ts(6))
            b = auto_doc.propose_modify(
                "h1", '<h1 data-aim="h1">T</h1>', author=ME, accept=True, accept_by=ME, at=ts(7)
            )
        out = auto_doc.last_auto_accept
        assert out.via == "mixed" and out.accepted == (a.id, b.id)
        assert out.decided_by == ADA  # the policy group's decider
        ra, rb = auto_doc.resolution_of(a.id), auto_doc.resolution_of(b.id)
        assert (ra.get("auto"), rb.get("auto")) == ("policy", "request")
        assert rb.get("decided_by") == ME.to_obj()
        assert ra.batch == rb.batch == batch


class TestBatchClose:
    def test_a_turn_with_chained_adds_and_an_amend_lands_at_close(self, auto_doc):
        with auto_doc.batch() as batch:
            a = auto_doc.propose_add('<p data-aim="a1">A.</p>', author=BOT, at=ts(6))
            b = auto_doc.propose_add('<p data-aim="b1">B.</p>', author=BOT, after=a.id, at=ts(7))
            c = auto_doc.propose_modify(
                "intro", '<p data-aim="intro">Draft.</p>', author=BOT, at=ts(8)
            )
            auto_doc.amend_proposal(c.id, '<p data-aim="intro">Final.</p>')
            # nothing lands while the turn runs
            assert {p.id for p in auto_doc.proposals} == {a.id, b.id, c.id}
            assert a.resolution is None
        assert auto_doc.proposals == []
        assert auto_doc.last_auto_accept.accepted == (a.id, b.id, c.id)
        assert auto_doc.last_auto_accept.batch == batch
        assert [ch.id for ch in auto_doc.chunks] == ["h1", "intro", "a1", "b1"]
        assert auto_doc.chunk("intro").text == "Final."
        resolutions = [e for e in auto_doc.history if e.kind == "resolution"]
        assert [e.get("proposal") for e in resolutions] == [a.id, b.id, c.id]
        assert {e.batch for e in resolutions} == {batch}
        assert auto_doc.verify() == []
        assert _errors(auto_doc) == []

    def test_an_exception_accepts_nothing(self, auto_doc):
        with pytest.raises(RuntimeError):
            with auto_doc.batch():
                auto_doc.propose_delete("intro", author=BOT, at=ts(6))
                raise RuntimeError("turn failed")
        assert len(auto_doc.proposals) == 1

    def test_a_card_superseded_inside_the_batch_is_not_accepted(self, auto_doc):
        with auto_doc.batch():
            a = auto_doc.propose_modify(
                "intro", '<p data-aim="intro">One.</p>', author=BOT, at=ts(6)
            )
            b = auto_doc.propose_modify(
                "intro", '<p data-aim="intro">Two.</p>', author=BOT, at=ts(7)
            )
        assert auto_doc.last_auto_accept.accepted == (b.id,)
        assert auto_doc.resolution_of(a.id).decision == "superseded"
        assert auto_doc.chunk("intro").text == "Two."

    def test_a_refused_dry_run_leaves_the_whole_batch_pending(self, auto_doc):
        """An earlier human move of the anchor block makes the agent's add
        undecidable (§5.4 refuses rather than guesses): nothing lands, no
        exception, and the document is what it would be without auto."""
        auto_doc.add_chunk('<p data-aim="tail">Tail.</p>', author=ME, at=ts(6))
        auto_doc.propose_move("h1", author=ME, container="body", after="tail", at=ts(7))
        control = aim.loads(auto_doc.dumps())
        for d, knob in ((auto_doc, None), (control, False)):
            with d.batch(auto_accept=knob):
                d.propose_add('<p data-aim="n1">N.</p>', author=BOT, after="h1", at=ts(8))
                d.propose_delete("intro", author=BOT, at=ts(9))
        out = auto_doc.last_auto_accept
        assert out.accepted == () and len(out.deferred) == 2
        assert "refused" in out.reason
        # proposal ids are minted at random; everything else is byte-identical
        assert _pids_blanked(auto_doc.dumps()) == _pids_blanked(control.dumps())

    def test_a_card_replacing_a_persons_suggestion_stays_pending(self, auto_doc):
        human = auto_doc.propose_modify(
            "intro", '<p data-aim="intro">Mine.</p>', author=ME, at=ts(6)
        )
        with auto_doc.batch():
            replacing = auto_doc.propose_modify(
                "intro", '<p data-aim="intro">Bot.</p>', author=BOT, at=ts(7)
            )
            other = auto_doc.propose_delete("h1", author=BOT, at=ts(8))
        out = auto_doc.last_auto_accept
        assert out.accepted == (other.id,)
        assert out.deferred == (replacing.id,)
        assert "replaces a suggestion from a person" in out.reason
        assert auto_doc.resolution_of(human.id).decision == "superseded"
        assert [p.id for p in auto_doc.proposals] == [replacing.id]

    @pytest.mark.parametrize("shape", ["one batch", "two calls", "kept pending first"])
    def test_a_revision_of_a_card_that_replaced_a_person_stays_pending(self, auto_doc, shape):
        """The agent revising its own card, which had replaced a person's
        suggestion, still replaces that suggestion: the exclusion follows the
        supersession chain, inside one batch and across batches (§5.6)."""
        human = auto_doc.propose_modify(
            "intro", '<p data-aim="intro">Mine.</p>', author=ME, at=ts(6)
        )
        v1 = '<p data-aim="intro">Bot 1.</p>'
        v2 = '<p data-aim="intro">Bot 2.</p>'
        if shape == "one batch":
            with auto_doc.batch():
                auto_doc.propose_modify("intro", v1, author=BOT, at=ts(7))
                second = auto_doc.propose_modify("intro", v2, author=BOT, at=ts(8))
        else:
            keep = False if shape == "kept pending first" else None
            auto_doc.propose_modify("intro", v1, author=BOT, at=ts(7), accept=keep)
            second = auto_doc.propose_modify("intro", v2, author=BOT, at=ts(8))
        out = auto_doc.last_auto_accept
        assert out.accepted == () and out.deferred == (second.id,)
        assert "replaces a suggestion from a person" in out.reason
        assert auto_doc.chunk("intro").text == "Intro paragraph."
        assert [p.id for p in auto_doc.proposals] == [second.id]
        assert auto_doc.resolution_of(human.id).decision == "superseded"

    def test_auto_turn_equals_accept_all_on_the_same_lane(self, basic_doc):
        manual = aim.loads(basic_doc.dumps())
        auto = _with_policy(aim.loads(basic_doc.dumps()))
        _with_policy(manual)
        for d, knob in ((auto, None), (manual, False)):
            with d.batch(auto_accept=knob):
                a = d.propose_add('<p data-aim="a1">A.</p>', author=BOT, after="h1", at=ts(6))
                d.propose_add('<p data-aim="a2">B.</p>', author=BOT, after=a.id, at=ts(7))
                d.propose_move("intro", author=BOT, container="body", after=None, at=ts(8))
                d.propose_modify("h1", '<h1 data-aim="h1">New</h1>', author=BOT, at=ts(9))
        manual.accept_all(decided_by=ADA, at=ts(10))
        assert [c.html for c in auto.chunks] == [c.html for c in manual.chunks]
        assert auto.doc_hash == manual.doc_hash


class TestExistingCards:
    def test_auto_accept_groups_into_the_cards_batch(self, basic_doc):
        with basic_doc.batch() as batch:
            a = basic_doc.propose_delete("intro", author=TOOL, at=ts(6))
            b = basic_doc.propose_modify("h1", '<h1 data-aim="h1">T</h1>', author=TOOL, at=ts(7))
        _with_policy(basic_doc)
        out = basic_doc.auto_accept([a.id, b.id], at=ts(8))
        assert out.batch == batch and out.accepted == (a.id, b.id)
        assert basic_doc.resolution_of(a.id).batch == batch
        assert basic_doc.verify() == []

    def test_a_card_that_replaced_a_person_through_a_chain_is_deferred(self, basic_doc):
        basic_doc.propose_modify("intro", '<p data-aim="intro">Mine.</p>', author=ME, at=ts(6))
        basic_doc.propose_modify("intro", '<p data-aim="intro">A.</p>', author=TOOL, at=ts(7))
        last = basic_doc.propose_modify(
            "intro", '<p data-aim="intro">B.</p>', author=TOOL, at=ts(8)
        )
        _with_policy(basic_doc)
        out = basic_doc.auto_accept([last.id], at=ts(9))
        assert out.accepted == () and out.deferred == (last.id,)
        assert basic_doc.chunk("intro").text == "Intro paragraph."

    def test_policy_via_needs_a_policy_and_skips_people(self, basic_doc):
        p = basic_doc.propose_delete("intro", author=ME, at=ts(6))
        with pytest.raises(aim.InvalidOperation, match="no auto review policy"):
            basic_doc.auto_accept([p.id])
        _with_policy(basic_doc)
        out = basic_doc.auto_accept([p.id], at=ts(7))
        assert out.accepted == () and out.deferred == (p.id,)
        assert out.reason == "proposed by a person"

    def test_unknown_ids_raise(self, auto_doc):
        with pytest.raises(aim.TargetNotFound):
            auto_doc.auto_accept(["p-nope"])


class TestMarker:
    @pytest.fixture
    def resolved(self, auto_doc) -> dict:
        p = auto_doc.propose_delete("intro", author=BOT, at=ts(6))
        return dict(p.resolution.data)

    def test_valid_marker(self, resolved):
        assert Event(resolved).validate() == []

    @pytest.mark.parametrize(
        "patch,needle",
        [
            ({"auto": "always"}, "auto must be one of"),
            ({"decision": "rejected"}, "only valid on an accepted"),
            ({"decided_by": {"type": "agent", "model": "m"}}, "decided_by a human"),
            ({"applied": "<p></p>"}, "must not carry applied"),
        ],
    )
    def test_misuse_is_a_schema_problem(self, resolved, patch, needle):
        problems = Event({**resolved, **patch}).validate()
        assert any(needle in p for p in problems)

    def test_marker_under_an_05_declaration_is_s035(self, auto_doc):
        auto_doc.propose_delete("intro", author=BOT, at=ts(6))
        auto_doc.set_review_policy(None, author=ADA, at=ts(7))
        stale = auto_doc.dumps().replace(
            f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.5"'
        )
        codes = {f.code for f in aim.lint_text(stale) if f.level == "error"}
        assert "S035" in codes


class TestNewerSpecLeniency:
    def _newer(self, doc: aim.AimDocument) -> str:
        doc.modify_chunk("intro", '<p data-aim="intro">X.</p>', author=ME, at=ts(6))
        text = doc.dumps()
        last = doc.history[-1]
        line = last.to_json()
        patched = Event({**last.data, "z_future": 1}).to_json()
        text = text.replace(line, patched)
        return text.replace(f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="9.0"')

    def test_unknown_fields_in_a_newer_document_are_a_warning(self, basic_doc):
        text = self._newer(basic_doc)
        findings = aim.lint_text(text)
        assert not any(f.code == "H003" for f in findings)
        assert any(f.code == "H010" and f.level == "warning" for f in findings)

    def test_unknown_fields_in_a_current_document_stay_h003(self, basic_doc):
        text = self._newer(basic_doc).replace(
            'data-aim-version="9.0"', f'data-aim-version="{aim.SPEC_VERSION}"'
        )
        assert any(f.code == "H003" for f in aim.lint_text(text))

    def test_reconcile_and_diff_accept_the_newer_fields(self, basic_doc):
        old = aim.loads(self._newer(basic_doc))
        report = aim.loads(old.dumps()).reconcile(dry_run=True)
        assert report.residual == []
        assert not aim.classify_divergence(old, aim.loads(old.dumps())).content_drift

    def test_missing_required_fields_still_fail(self):
        ev = Event({"seq": 1, "kind": "checkpoint", "t": ts(1), "label": "x", "z": 1})
        problems = ev.validate(newer_spec=True)
        assert any("doc_hash" in p for p in problems)
        assert not any("unknown field" in p for p in problems)
