"""Unit-level diff (`diff_documents`) and reload-divergence classification
(`classify_divergence`): the SDK surface a live consumer uses when a file
changes under it — what changed, and does the file's own history explain it.
"""

import json

import pytest

import aimformat as aim
from aimformat.cli import main
from conftest import BOT, ME, ts


def snap(doc: aim.AimDocument) -> aim.AimDocument:
    """An independent parse of *doc* as it stands (the 'held' old version)."""
    return aim.loads(doc.dumps())


@pytest.fixture
def doc() -> aim.AimDocument:
    d = aim.new_document(title="Diff fixture")
    d.add_chunk('<h1 data-aim="h1">Title</h1>', author=BOT, at=ts(0))
    d.add_chunk('<p data-aim="p1">First.</p>', author=BOT, at=ts(1))
    d.add_chunk('<p data-aim="p2">Second.</p>', author=BOT, at=ts(2))
    d.add_chunk('<p data-aim="p3">Third.</p>', author=BOT, at=ts(3))
    return d


# ---------------------------------------------------------------- diff_documents


def test_diff_identical_documents(doc):
    d = aim.diff_documents(snap(doc), snap(doc))
    assert not d.changed
    assert d.changed_ids == ()
    assert d.to_obj()["added"] == []


def test_diff_modified_chunk(doc):
    old = snap(doc)
    doc.modify_chunk("p1", '<p data-aim="p1">First, sharper.</p>', author=BOT, at=ts(10))
    d = aim.diff_documents(old, doc)
    assert d.modified == ("p1",)
    assert d.added == () and d.deleted == () and d.moved == ()
    assert d.changed and d.changed_ids == ("p1",)


def test_diff_added_chunk_between_does_not_move_neighbors(doc):
    old = snap(doc)
    doc.add_chunk(
        '<p data-aim="pnew">Between.</p>', author=BOT, container="body", after="p1", at=ts(10)
    )
    d = aim.diff_documents(old, doc)
    assert d.added == ("pnew",)
    assert d.moved == () and d.modified == () and d.deleted == ()


def test_diff_deleted_chunk(doc):
    old = snap(doc)
    doc.delete_chunk("p2", author=BOT, at=ts(10))
    d = aim.diff_documents(old, doc)
    assert d.deleted == ("p2",)
    assert d.added == () and d.moved == () and d.modified == ()
    assert d.changed_ids == ()  # nothing in *new* to mark


def test_diff_moved_chunk_marks_only_the_mover(doc):
    old = snap(doc)
    doc.move_chunk("p1", author=BOT, container="body", at=ts(10))  # to end
    d = aim.diff_documents(old, doc)
    assert d.moved == ("p1",)
    assert d.modified == () and d.added == () and d.deleted == ()


def test_diff_adjacent_swap_marks_one_not_both(doc):
    old = snap(doc)
    doc.move_chunk("p2", author=BOT, container="body", after="h1", at=ts(10))
    d = aim.diff_documents(old, doc)
    assert len(d.moved) == 1  # the stable subsequence keeps the longer run
    assert set(d.moved) <= {"p1", "p2"}


def test_diff_container_member_edit_marks_member_not_container(doc):
    doc.add_chunk(
        '<ul data-aim-container="list1">'
        '<li data-aim="li1">One</li><li data-aim="li2">Two</li></ul>',
        author=BOT,
        at=ts(5),
    )
    old = snap(doc)
    doc.modify_chunk("li1", '<li data-aim="li1">One, edited</li>', author=BOT, at=ts(10))
    d = aim.diff_documents(old, doc)
    assert d.modified == ("li1",)
    assert "list1" not in d.changed_ids


def test_diff_container_attr_edit_marks_container_not_members(doc):
    doc.add_chunk(
        '<ul data-aim-container="list1">'
        '<li data-aim="li1">One</li><li data-aim="li2">Two</li></ul>',
        author=BOT,
        at=ts(5),
    )
    old = snap(doc)
    doc.modify_chunk(
        "list1",
        '<ul data-aim-container="list1" class="list-disc">'
        '<li data-aim="li1">One</li><li data-aim="li2">Two</li></ul>',
        author=BOT,
        at=ts(10),
    )
    d = aim.diff_documents(old, doc)
    assert d.modified == ("list1",)
    assert d.moved == ()


def test_diff_cross_container_move(doc):
    doc.add_chunk(
        '<ul data-aim-container="list1"><li data-aim="li1">One</li></ul>',
        author=BOT,
        at=ts(5),
    )
    doc.add_chunk(
        '<ul data-aim-container="list2"><li data-aim="li2">Two</li></ul>',
        author=BOT,
        at=ts(6),
    )
    old = snap(doc)
    doc.move_chunk("li1", author=BOT, container="list2", at=ts(10))
    d = aim.diff_documents(old, doc)
    assert d.moved == ("li1",)


def test_diff_moved_container_members_ride_along(doc):
    doc.add_chunk(
        '<ul data-aim-container="list1">'
        '<li data-aim="li1">One</li><li data-aim="li2">Two</li></ul>',
        author=BOT,
        at=ts(5),
    )
    old = snap(doc)
    doc.move_chunk("list1", author=BOT, container="body", after="h1", at=ts(10))
    d = aim.diff_documents(old, doc)
    assert d.moved == ("list1",)
    assert "li1" not in d.moved and "li2" not in d.moved


def test_diff_run_chunk_is_one_unit(doc):
    doc.add_chunk(
        '<ul data-aim-container="list1"><li data-aim="run1">A</li><li data-aim="run1">B</li></ul>',
        author=BOT,
        at=ts(5),
    )
    old = snap(doc)
    doc.modify_chunk(
        "run1",
        '<li data-aim="run1">A!</li><li data-aim="run1">B!</li>',
        author=BOT,
        at=ts(10),
    )
    d = aim.diff_documents(old, doc)
    assert d.modified == ("run1",)


def test_diff_theme_settings_version_flags(doc):
    old = snap(doc)
    doc.set_theme({"--aim-brand-1": "#123456"}, author=BOT, at=ts(10))
    doc.set_page_setup(
        {"size": "A4", "margin": {"top": 72, "right": 72, "bottom": 72, "left": 72}},
        author=ME,
        at=ts(11),
    )
    d = aim.diff_documents(old, doc)
    assert d.theme_changed and d.doc_settings_changed
    assert not d.version_changed
    assert d.changed  # flags alone count


def test_diff_changed_ids_dedup_order(doc):
    old = snap(doc)
    doc.modify_chunk("p3", '<p data-aim="p3">Third, edited.</p>', author=BOT, at=ts(10))
    doc.move_chunk("p3", author=BOT, container="body", after="h1", at=ts(11))
    doc.add_chunk('<p data-aim="pnew">New.</p>', author=BOT, at=ts(12))
    d = aim.diff_documents(old, doc)
    assert set(d.changed_ids) == {"p3", "pnew"}
    assert len(d.changed_ids) == 2  # modified+moved p3 reported once


# ------------------------------------------------------------ classify_divergence


def test_divergence_identical(doc):
    div = aim.classify_divergence(snap(doc), snap(doc))
    assert not div.changed
    assert div.new_events == () and not div.history_rewritten
    assert div.new_proposals == () and div.removed_proposals == ()
    assert not div.content_drift
    assert div.explained


def test_divergence_sdk_edit_is_explained(doc):
    old = snap(doc)
    doc.modify_chunk("p1", '<p data-aim="p1">First, sharper.</p>', author=BOT, at=ts(10))
    div = aim.classify_divergence(old, snap(doc))
    assert div.changed
    assert len(div.new_events) == 1 and div.new_events[0].action == "modify"
    assert not div.content_drift and div.explained


def test_divergence_raw_write_is_drift(doc):
    old = snap(doc)
    tampered = aim.loads(
        doc.dumps().replace(
            '<p data-aim="p1">First.</p>', '<p data-aim="p1">First, hand-tweaked.</p>'
        )
    )
    div = aim.classify_divergence(old, tampered)
    assert div.changed
    assert div.new_events == () and not div.history_rewritten
    assert div.content_drift and not div.explained


def test_divergence_new_proposal_is_lane_only(doc):
    old = snap(doc)
    doc.propose_modify(
        "p1", '<p data-aim="p1">Proposed.</p>', author=BOT, explanation="e", at=ts(10)
    )
    div = aim.classify_divergence(old, snap(doc))
    assert div.changed
    assert len(div.new_proposals) == 1
    assert not div.content_drift and div.new_events == ()


def test_divergence_external_accept(doc):
    p = doc.propose_modify(
        "p1", '<p data-aim="p1">Proposed.</p>', author=BOT, explanation="e", at=ts(10)
    )
    old = snap(doc)
    doc.accept(p.id, decided_by=ME, at=ts(11))
    div = aim.classify_divergence(old, snap(doc))
    assert div.removed_proposals == (p.id,)
    assert len(div.new_events) == 1 and div.new_events[0].kind == "resolution"
    assert not div.content_drift and div.explained


def test_divergence_flatten_is_rewritten(doc):
    old = snap(doc)
    doc.flatten()
    div = aim.classify_divergence(old, snap(doc))
    assert div.history_rewritten
    assert div.new_events == () and not div.content_drift
    assert not div.explained


def test_divergence_mixed_sdk_edit_plus_raw_tamper(doc):
    old = snap(doc)
    doc.modify_chunk("p1", '<p data-aim="p1">First, sharper.</p>', author=BOT, at=ts(10))
    tampered = aim.loads(
        doc.dumps().replace(
            '<p data-aim="p2">Second.</p>', '<p data-aim="p2">Second, hand-tweaked.</p>'
        )
    )
    div = aim.classify_divergence(old, tampered)
    assert len(div.new_events) == 1
    assert div.content_drift and not div.explained


def test_divergence_checkpoint_only_append(doc):
    old = snap(doc)
    doc.checkpoint("save point", at=ts(10))
    div = aim.classify_divergence(old, snap(doc))
    assert div.changed  # the log line itself
    assert len(div.new_events) == 1 and div.new_events[0].kind == "checkpoint"
    assert not div.content_drift and div.explained


def test_divergence_then_reconcile_adopts_drift(doc):
    """The tier-3 reload flow end to end: raw write → drift; reconcile →
    the same old version now sees an explained, undoable transition."""
    old = snap(doc)
    tampered = aim.loads(
        doc.dumps().replace(
            '<p data-aim="p1">First.</p>', '<p data-aim="p1">First, hand-tweaked.</p>'
        )
    )
    assert aim.classify_divergence(old, tampered).content_drift

    report = tampered.reconcile(at=ts(20))
    assert [e.action for e in report.events] == ["modify"]
    adopted = aim.loads(tampered.dumps())
    div = aim.classify_divergence(old, adopted)
    assert div.explained and not div.content_drift
    assert [e.origin for e in div.new_events] == ["reconcile"]
    assert aim.diff_documents(old, adopted).modified == ("p1",)

    # and doc.undo() now reverts exactly the hand edit
    adopted.undo(author=ME, at=ts(21))
    assert "First." in adopted.chunk("p1").html
    assert adopted.verify() == []


# ------------------------------------------------------------------------- CLI


def test_cli_diff(tmp_path, doc, capsys):
    old_path = tmp_path / "old.aim"
    new_path = tmp_path / "new.aim"
    doc.save(old_path)
    doc.modify_chunk("p1", '<p data-aim="p1">First, sharper.</p>', author=BOT, at=ts(10))
    doc.add_chunk('<p data-aim="pnew">New.</p>', author=BOT, at=ts(11))
    doc.save(new_path)

    assert main(["diff", str(old_path), str(new_path)]) == 0
    out = capsys.readouterr().out
    assert "modified: p1" in out and "added: pnew" in out

    assert main(["diff", str(old_path), str(new_path), "--format", "json"]) == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["modified"] == ["p1"] and obj["added"] == ["pnew"]

    assert main(["diff", str(old_path), str(old_path)]) == 0
    assert "no unit-level differences" in capsys.readouterr().out


def test_diff_changed_ids_document_order(doc):
    # codex #35: an early modify plus a late add must come back in NEW
    # document order — the category tuples are each ordered, their
    # concatenation is not (the old property returned the add first)
    old = snap(doc)
    doc.modify_chunk("h1", '<h1 data-aim="h1">Title, retouched</h1>', author=BOT, at=ts(10))
    doc.add_chunk('<p data-aim="pnew">New closer.</p>', author=BOT, at=ts(11))
    d = aim.diff_documents(old, doc)
    assert d.changed_ids == ("h1", "pnew")


def test_divergence_malformed_appended_event_is_drift(doc):
    # codex #35: an appended history line that parses as JSON but lacks
    # "kind" made state_at() raise KeyError THROUGH classify_divergence.
    # A replay the log cannot account for is the definition of drift.
    old = snap(doc)
    text = doc.dumps()
    marker = "\n</script>"
    idx = text.index(marker)
    bogus = '\n{"seq": 99, "t": "2026-01-01T00:00:00Z"}'
    tampered = aim.loads(text[:idx] + bogus + text[idx:])
    div = aim.classify_divergence(old, tampered)
    assert div.content_drift and not div.explained


def test_divergence_unparseable_appended_history_is_drift(doc):
    # codex #35 round 2: history parsing is LAZY — loads() succeeds on a
    # file whose appended log line is not JSON at all, and the HistoryError
    # surfaced from the events extraction, not the replay guard.
    old = snap(doc)
    text = doc.dumps()
    marker = "\n</script>"
    idx = text.index(marker)
    tampered = aim.loads(text[:idx] + "\n{this is not json" + text[idx:])
    div = aim.classify_divergence(old, tampered)
    assert div.content_drift and not div.explained


def test_diff_to_obj_carries_changed_ids(doc):
    old = snap(doc)
    doc.modify_chunk("h1", '<h1 data-aim="h1">Title 2</h1>', author=BOT, at=ts(10))
    doc.add_chunk('<p data-aim="pz">Tail.</p>', author=BOT, at=ts(11))
    obj = aim.diff_documents(old, doc).to_obj()
    assert obj["changed_ids"] == ["h1", "pz"]


def test_divergence_bool_payload_event_is_drift(doc):
    # codex #35 round 3: a replayable-looking appended event with a bool
    # where markup belongs raised AttributeError through the replay guard
    old = snap(doc)
    text = doc.dumps()
    marker = "\n</script>"
    idx = text.index(marker)
    bogus = (
        '\n{"action":"modify","after":true,"before":true,"author":'
        '{"type":"agent","model":"m"},"batch":"bx","kind":"direct_edit",'
        '"seq":99,"t":"2026-01-01T00:00:00Z","target":"p1"}'
    )
    tampered = aim.loads(text[:idx] + bogus + text[idx:])
    div = aim.classify_divergence(old, tampered)
    assert div.content_drift and not div.explained


def test_divergence_malformed_proposal_actor_degrades(doc):
    # codex #35 round 3: a hand-mangled pending card (bogus data-author)
    # must degrade to "no lane information", never crash the classifier
    old = snap(doc)
    doc.propose_modify(
        "p1", '<p data-aim="p1">Prop.</p>', author=BOT, explanation="e", at=ts(9)
    )
    text = doc.dumps()
    assert 'data-author="agent:m"' in text or "data-author" in text
    tampered_text = text.replace("data-author=\"", "data-author=\"bogus ", 1)
    tampered = aim.loads(tampered_text)
    div = aim.classify_divergence(old, tampered)
    assert div.new_proposals == ()  # degraded, not crashed


def test_divergence_unknown_kind_suffix_is_drift_not_explained(doc):
    # codex #35 round 4: state_at SKIPS unknown kinds as non-state-changing,
    # so a kind:"bogus" suffix replayed to old's exact hash and came back
    # "explained" with the invalid event in new_events
    old = snap(doc)
    text = doc.dumps()
    idx = text.index("\n</script>")
    bogus = (
        '\n{"author":{"type":"agent","model":"m"},"batch":"bx",'
        '"kind":"bogus","seq":99,"t":"2026-01-01T00:00:00Z"}'
    )
    tampered = aim.loads(text[:idx] + bogus + text[idx:])
    div = aim.classify_divergence(old, tampered)
    assert div.content_drift and not div.explained
    assert div.new_events == ()  # an untrusted suffix is never surfaced


def test_divergence_unreadable_lane_makes_no_removal_claims(doc):
    # codex #35 round 4: one mangled card degraded the whole lane to [],
    # which reported every VALID old proposal as removed — an editor
    # reacting to removed_proposals dismissed real pending cards
    doc.propose_modify(
        "p1", '<p data-aim="p1">Keep me.</p>', author=BOT, explanation="e", at=ts(8)
    )
    old = snap(doc)
    doc.propose_modify(
        "p2", '<p data-aim="p2">Second card.</p>', author=BOT, explanation="e", at=ts(9)
    )
    text = doc.dumps()
    # mangle ONLY the second card's author; the first stays valid on disk
    tampered = aim.loads(text.replace('data-author="', 'data-author="bogus ', 1))
    div = aim.classify_divergence(old, tampered)
    assert div.removed_proposals == ()
    assert div.new_proposals == ()
