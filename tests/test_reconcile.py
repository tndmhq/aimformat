"""Reconcile (spec §6.8): detect out-of-band edits — hand edits, corruption,
files that never had history — and repair the document by synthesizing
origin:"reconcile" events so the chain verifies again.

The tests build mock damaged files (tampered canonical text, hand-written
documents) and assert target results: the exact synthesized events, the
repaired document verifying and linting clean, and time travel still
reconstructing the pre-tamper past.
"""

import pytest

import aimformat as aim
from aimformat.cli import main
from aimformat.errors import HistoryError, TargetNotFound
from aimformat.registry import REGISTRY
from conftest import BOT, ME, ts


def errors(doc):
    return [f for f in aim.lint(doc) if f.level == "error"]


def reconciled(doc, **kw):
    """Reconcile + the invariants every repaired document must satisfy."""
    report = doc.reconcile(at=kw.pop("at", ts(60)), **kw)
    assert doc.verify() == []
    assert errors(doc) == []
    assert aim.loads(doc.dumps()).verify() == []  # survives a round-trip
    return report


def hand_bumped_paint_text() -> str:
    doc = aim.new_document(title="hand-bumped document")
    doc.add_chunk('<p data-aim="p1">Plain.</p>', author=BOT, at=ts(0))
    older = aim.loads(
        doc.dumps().replace(f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.2"')
    )
    older.checkpoint("before hand edit", at=ts(1))
    return (
        older.dumps()
        .replace('data-aim-version="0.2"', f'data-aim-version="{aim.SPEC_VERSION}"', 1)
        .replace(
            '<p data-aim="p1">Plain.</p>',
            '<p data-aim="p1" style="color:#ff69b4">Painted.</p>',
            1,
        )
    )


# ===========================================================================
class TestNoOp:
    @pytest.mark.parametrize("fixture", ["empty_doc", "basic_doc", "rich_doc", "lifecycle_doc"])
    def test_consistent_documents_are_left_alone(self, fixture, request):
        doc = request.getfixturevalue(fixture)
        before = doc.dumps()
        report = doc.reconcile(at=ts(60))
        assert not report.changed
        assert report.events == [] and report.residual == []
        assert doc.dumps() == before

    def test_cosmetic_spelling_differences_are_not_drift(self, basic_doc):
        # attribute order / class-token order normalize away on parse:
        # not an out-of-band edit, no events
        text = basic_doc.dumps().replace(
            '<h1 data-aim="h1" class="font-bold text-3xl">',
            '<h1 class="text-3xl font-bold" data-aim="h1">',
            1,
        )
        doc = aim.loads(text)
        assert not doc.reconcile(at=ts(60)).changed


class TestDryRun:
    def test_dry_run_reports_but_does_not_touch(self, basic_doc):
        text = basic_doc.dumps().replace("Intro paragraph.", "Edited.", 1)
        doc = aim.loads(text)
        before = doc.dumps()
        report = doc.reconcile(at=ts(60), dry_run=True)
        assert report.changed
        assert [e.action for e in report.events] == ["modify"]
        assert doc.dumps() == before  # untouched…
        assert doc.verify() != []  # …still broken
        assert reconciled(doc).changed  # the real run then repairs


# ===========================================================================
class TestContentEdits:
    def test_reconcile_replays_the_recorded_version_upgrade(self):
        doc = aim.new_document(title="upgraded document")
        doc.add_chunk('<p data-aim="p1">Plain.</p>', author=BOT, at=ts(0))
        older = aim.loads(
            doc.dumps().replace(f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.2"')
        )
        older.checkpoint("before paint", at=ts(1))
        older.modify_chunk(
            "p1", '<p data-aim="p1" style="color:#ff69b4">Painted.</p>', author=ME, at=ts(2)
        )
        drifted = aim.loads(older.dumps().replace(">Painted.</p>", ">Hand edited.</p>", 1))

        report = reconciled(drifted)

        assert report.changed
        assert drifted.spec_version == REGISTRY.paint_since
        assert drifted.chunk("p1").text == "Hand edited."

    def test_reconcile_records_the_upgrade_for_out_of_band_paint(self):
        doc = aim.new_document(title="older document")
        doc.add_chunk('<p data-aim="p1">Plain.</p>', author=BOT, at=ts(0))
        older = aim.loads(
            doc.dumps().replace(f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.2"')
        )
        older.checkpoint("before paint", at=ts(1))
        drifted = aim.loads(
            older.dumps().replace(
                '<p data-aim="p1">Plain.</p>',
                '<p data-aim="p1" style="color:#ff69b4">Painted.</p>',
                1,
            )
        )

        report = reconciled(drifted)

        upgrade = next(event for event in report.events if event.target == "aim:version")
        edit = next(event for event in report.events if event.target == "p1")
        assert drifted.spec_version == REGISTRY.paint_since
        assert upgrade.batch == edit.batch

    def test_reconcile_refuses_paint_with_an_unrecorded_hand_bumped_version(self):
        drifted = aim.loads(hand_bumped_paint_text())
        before = drifted.dumps()

        with pytest.raises(HistoryError, match="unrecorded version marker"):
            drifted.reconcile(at=ts(60))

        assert drifted.dumps() == before

    def test_hand_edited_chunk_yields_exact_modify_event(self, basic_doc):
        text = basic_doc.dumps().replace("Intro paragraph.", "Intro, edited by hand.", 1)
        doc = aim.loads(text)
        assert any("mismatch" in p for p in doc.verify())
        report = reconciled(doc)
        assert [e.data for e in report.events] == [
            {
                "action": "modify",
                "after": '<p data-aim="intro">Intro, edited by hand.</p>',
                "author": {"type": "external"},
                "batch": "b3",
                "before": '<p data-aim="intro">Intro paragraph.</p>',
                "kind": "direct_edit",
                "origin": "reconcile",
                "seq": 3,
                "t": ts(60),
                "target": "intro",
            }
        ]
        assert doc.chunk("intro").text == "Intro, edited by hand."

    def test_reconcile_never_rewrites_the_body(self, basic_doc):
        text = basic_doc.dumps().replace("Intro paragraph.", "Corrupted!", 1)
        doc = aim.loads(text)
        h = doc.doc_hash
        reconciled(doc)
        assert doc.doc_hash == h  # the body is truth; only history grew

    def test_two_edits_one_batch(self, basic_doc):
        text = (
            basic_doc.dumps()
            .replace("Intro paragraph.", "Edited intro.", 1)
            .replace(">Title</h1>", ">Retitled</h1>", 1)
        )
        doc = aim.loads(text)
        report = reconciled(doc)
        assert sorted(e.target for e in report.events) == ["h1", "intro"]
        assert len({e.batch for e in report.events}) == 1

    def test_edited_list_item(self, rich_doc):
        text = rich_doc.dumps().replace(">First</li>", ">First!</li>", 1)
        doc = aim.loads(text)
        report = reconciled(doc)
        assert [(e.action, e.target) for e in report.events] == [("modify", "li1")]

    def test_edited_run_carries_whole_run_payload(self, rich_doc):
        text = rich_doc.dumps().replace("…second, part two", "…second, part two (edited)", 1)
        doc = aim.loads(text)
        report = reconciled(doc)
        ev = report.events[0]
        assert (ev.action, ev.target) == ("modify", "li2")
        assert ev.get("after") == (
            '<li data-aim="li2">Second, part one…</li>'
            '<li data-aim="li2">…second, part two '
            "(edited)</li>"
        )

    def test_edited_table_row_and_slide_chunk(self, rich_doc):
        text = (
            rich_doc.dumps()
            .replace("<td>alpha</td><td>1</td>", "<td>alpha</td><td>111</td>", 1)
            .replace(">Deck</h2>", ">Deck?</h2>", 1)
        )
        doc = aim.loads(text)
        report = reconciled(doc)
        assert sorted((e.action, e.target) for e in report.events) == [
            ("modify", "row1"),
            ("modify", "st"),
        ]


# ===========================================================================
class TestStructuralEdits:
    def test_hand_deleted_chunk_yields_exact_delete_event(self, basic_doc):
        text = basic_doc.dumps().replace('<p data-aim="intro">Intro paragraph.</p>\n', "", 1)
        doc = aim.loads(text)
        report = reconciled(doc)
        assert [e.data for e in report.events] == [
            {
                "action": "delete",
                "anchor": {"after": "h1", "container": "body"},
                "author": {"type": "external"},
                "batch": "b3",
                "before": '<p data-aim="intro">Intro paragraph.</p>',
                "kind": "direct_edit",
                "origin": "reconcile",
                "seq": 3,
                "t": ts(60),
                "target": "intro",
            }
        ]
        with pytest.raises(TargetNotFound):
            doc.chunk("intro")

    def test_hand_added_element_without_id_gets_one(self, basic_doc):
        text = basic_doc.dumps().replace(
            "Intro paragraph.</p>\n", "Intro paragraph.</p>\n<p>Hand-added.</p>\n", 1
        )
        doc = aim.loads(text)
        report = reconciled(doc)
        [(old, new)] = report.assigned_ids
        assert old is None
        [ev] = report.events
        assert (ev.action, ev.target) == ("add", new)
        assert ev.get("anchor") == {"after": "intro", "container": "body"}
        assert doc.chunk(new).text == "Hand-added."

    def test_hand_reordered_chunks_yield_one_move(self, basic_doc):
        lines = basic_doc.dumps().split("\n")
        i1 = next(i for i, ln in enumerate(lines) if ln.startswith("<h1"))
        i2 = next(i for i, ln in enumerate(lines) if ln.startswith('<p data-aim="intro"'))
        lines[i1], lines[i2] = lines[i2], lines[i1]
        doc = aim.loads("\n".join(lines))
        report = reconciled(doc)
        [ev] = report.events
        assert (ev.action, ev.target) == ("move", "intro")
        assert ev.get("from") == {"after": "h1", "container": "body"}
        assert ev.get("to") == {"after": None, "container": "body"}
        assert doc.body_ids == ["intro", "h1"]

    def test_reused_burned_id_is_reassigned(self, basic_doc):
        basic_doc.delete_chunk("intro", author=ME, at=ts(5))
        text = basic_doc.dumps().replace("</h1>\n", '</h1>\n<p data-aim="intro">Zombie.</p>\n', 1)
        doc = aim.loads(text)
        report = reconciled(doc)
        [(old, new)] = report.assigned_ids
        assert old == "intro" and new != "intro"
        assert [e.target for e in report.events] == [new]
        with pytest.raises(TargetNotFound):
            doc.chunk("intro")  # the deleted id stays burned (spec §4.4)
        assert doc.chunk(new).text == "Zombie."

    def test_duplicated_id_second_occurrence_reassigned(self, rich_doc):
        text = rich_doc.dumps().replace(
            "</section>\n", '</section>\n<p data-aim="intro">A pasted copy.</p>\n', 1
        )
        doc = aim.loads(text)
        report = reconciled(doc)
        [(old, new)] = report.assigned_ids
        assert old == "intro"
        assert doc.chunk("intro").text == "We looked at the numbers."
        assert doc.chunk(new).text == "A pasted copy."


# ===========================================================================
class TestContainers:
    def test_item_added_by_hand_inside_list(self, rich_doc):
        text = rich_doc.dumps().replace("</ul>", "<li>Fourth item</li></ul>", 1)
        doc = aim.loads(text)
        report = reconciled(doc)
        [(_, new)] = report.assigned_ids
        [ev] = report.events
        assert (ev.action, ev.target) == ("add", new)
        assert ev.get("anchor") == {"after": "li2", "container": "list"}

    def test_item_deleted_from_table_records_shell_anchor(self, rich_doc):
        text = rich_doc.dumps().replace('<tr data-aim="row2"><td>beta</td><td>2</td></tr>', "", 1)
        doc = aim.loads(text)
        report = reconciled(doc)
        [ev] = report.events
        assert (ev.action, ev.target) == ("delete", "row2")
        assert ev.get("anchor") == {"after": "row1", "container": "tbl", "shell": "tbody"}

    def test_items_reordered_within_list(self, rich_doc):
        old = (
            '<ul data-aim-container="list"><li data-aim="li1">First</li>'
            '<li data-aim="li2">Second, part one…</li>'
            '<li data-aim="li2">…second, part two</li></ul>'
        )
        new = (
            '<ul data-aim-container="list">'
            '<li data-aim="li2">Second, part one…</li>'
            '<li data-aim="li2">…second, part two</li>'
            '<li data-aim="li1">First</li></ul>'
        )
        doc = aim.loads(rich_doc.dumps().replace(old, new, 1))
        report = reconciled(doc)
        [ev] = report.events
        assert (ev.action, ev.target) == ("move", "li2")
        assert ev.get("to") == {"after": None, "container": "list"}

    def test_container_attribute_change_is_one_whole_modify(self, rich_doc):
        text = rich_doc.dumps().replace(
            '<ul data-aim-container="list">', '<ul data-aim-container="list" class="list-disc">', 1
        )
        doc = aim.loads(text)
        report = reconciled(doc)
        [ev] = report.events  # items are subsumed, not evented one by one
        assert (ev.action, ev.target) == ("modify", "list")
        assert 'class="list-disc"' in ev.get("after")

    def test_container_deleted_wholesale_is_one_delete(self, rich_doc):
        start = rich_doc.dumps().index('<ul data-aim-container="list"')
        end = rich_doc.dumps().index("</ul>\n") + len("</ul>\n")
        doc = aim.loads(rich_doc.dumps()[:start] + rich_doc.dumps()[end:])
        report = reconciled(doc)
        [ev] = report.events
        assert (ev.action, ev.target) == ("delete", "list")
        assert ev.get("anchor") == {"after": "scope", "container": "body"}
        assert "li1" in ev.get("before")  # items ride inside the payload

    def test_slide_moved_to_front(self, rich_doc):
        lines = rich_doc.dumps().split("\n")
        slide = next(ln for ln in lines if ln.startswith("<aim-slide"))
        lines.remove(slide)
        lines.insert(lines.index(next(ln for ln in lines if ln.startswith("<h1"))), slide)
        doc = aim.loads("\n".join(lines))
        report = reconciled(doc)
        [ev] = report.events
        assert (ev.action, ev.target) == ("move", "s1")
        assert doc.body_ids[0] == "s1"


# ===========================================================================
class TestTheme:
    def _themed(self, basic_doc):
        basic_doc.set_theme({"--aim-brand-1": "#123456"}, author=ME, at=ts(5))
        return basic_doc

    def test_hand_edited_theme_yields_theme_modify(self, basic_doc):
        doc = aim.loads(self._themed(basic_doc).dumps().replace("#123456", "#654321", 1))
        report = reconciled(doc)
        [ev] = report.events
        assert (ev.action, ev.target) == ("modify", "aim:theme")
        assert ev.get("before") == ("<style data-aim-theme>:root{--aim-brand-1:#123456}</style>")
        assert ev.get("after") == ("<style data-aim-theme>:root{--aim-brand-1:#654321}</style>")

    def test_hand_removed_theme_yields_removal_event(self, basic_doc):
        doc = aim.loads(
            self._themed(basic_doc)
            .dumps()
            .replace("<style data-aim-theme>:root{--aim-brand-1:#123456}</style>\n", "", 1)
        )
        report = reconciled(doc)
        [ev] = report.events
        assert (ev.action, ev.target) == ("modify", "aim:theme")
        assert "before" in ev.data and "after" not in ev.data
        assert doc.theme == {}

    def test_theme_never_touched_by_events_is_untracked(self, basic_doc):
        # basic_doc's theme comes from the constructor — no event ever wrote
        # it, so there is no baseline to compare against: left as-is.
        doc = aim.loads(basic_doc.dumps().replace("#1a73e8", "#00ff00", 1))
        report = doc.reconcile(at=ts(60))
        assert not report.changed and doc.verify() == []


# ===========================================================================
class TestPageSetup:
    """aim:doc mirrors aim:theme through reconcile: replayable removal
    events, out-of-band drift detection, untracked baselines, and pending
    proposals that must not be rejected as dangling."""

    def _paged(self, basic_doc):
        basic_doc.set_page_setup({"size": "A5"}, author=ME, at=ts(5))
        return basic_doc

    def test_settings_removal_event_replays(self, basic_doc):
        # set → undo produces a modify with `before` and no `after` (the
        # introduction inverted); reconcile must replay it, not crash
        doc = self._paged(basic_doc)
        doc.undo(author=ME, at=ts(6))
        report = doc.reconcile(at=ts(60))
        assert not report.changed
        assert doc.verify() == []

    def test_hand_edited_page_setup_yields_doc_modify(self, basic_doc):
        doc = aim.loads(self._paged(basic_doc).dumps().replace('"size":"A5"', '"size":"Legal"', 1))
        report = reconciled(doc)
        [ev] = report.events
        assert (ev.action, ev.target) == ("modify", "aim:doc")
        assert '"size":"A5"' in ev.get("before")
        assert '"size":"Legal"' in ev.get("after")
        assert doc.page_setup.size == "Legal"

    def test_hand_removed_settings_yields_removal_event(self, basic_doc):
        doc_text = self._paged(basic_doc).dumps()
        block_start = doc_text.index('<script type="application/aim-doc+json">')
        block_end = doc_text.index("</script>", block_start) + len("</script>\n")
        doc = aim.loads(doc_text[:block_start] + doc_text[block_end:])
        report = reconciled(doc)
        [ev] = report.events
        assert (ev.action, ev.target) == ("modify", "aim:doc")
        assert "before" in ev.data and "after" not in ev.data
        assert doc.page_setup.size == "A4"  # back to defaults

    def test_settings_never_touched_by_events_is_untracked(self, empty_doc):
        # a block no event wrote (imported/hand-added on a flat doc) has no
        # baseline: left as-is, no synthesized event
        empty_doc.flatten()
        text = empty_doc.dumps().replace(
            "</title>",
            '</title>\n<script type="application/aim-doc+json">\n{"page":{"size":"A5"}}\n</script>',
        )
        doc = aim.loads(text)
        report = doc.reconcile(at=ts(60))
        assert doc.page_setup.size == "A5"
        assert not any(e.target == "aim:doc" for e in report.events)
        assert doc.verify() == []

    def test_pending_settings_proposal_survives_reconcile(self, basic_doc):
        basic_doc.propose_page_setup({"size": "A5"}, author=BOT, at=ts(5))
        # an out-of-band body edit forces a real reconcile pass
        doc = aim.loads(
            basic_doc.dumps().replace("Intro paragraph.", "Intro paragraph, hand-edited.", 1)
        )
        report = reconciled(doc)
        assert report.rejected_proposals == []
        assert len(doc.proposals) == 1


# ===========================================================================
class TestAdoption:
    HAND_WRITTEN = """<!doctype html>
<html data-aim-version="0.1" lang="en">
<head>
<meta charset="utf-8">
<title>Hand-written</title>
</head>
<body>
<h1>Adopted</h1>
<p>Written in a text editor: no ids, no history.</p>
</body>
</html>
"""

    HAND_WRITTEN_WITH_IDS = """<!doctype html>
<html data-aim-version="0.1" lang="en">
<head>
<meta charset="utf-8">
<title>Hand-written, ids chosen</title>
</head>
<body>
<h1 data-aim="ttl">Adopted</h1>
<p data-aim="body1">The author picked the ids.</p>
<script type="application/aim-history+jsonl">
</script>
</body>
</html>
"""

    def test_file_without_history_or_ids(self):
        doc = aim.loads(self.HAND_WRITTEN)
        report = reconciled(doc)
        assert len(report.assigned_ids) == 2
        assert [e.action for e in report.events] == ["add", "add"]
        h1, p = doc.chunks
        assert (h1.text, p.text) == ("Adopted", "Written in a text editor: no ids, no history.")

    def test_file_with_ids_keeps_them(self):
        doc = aim.loads(self.HAND_WRITTEN_WITH_IDS)
        report = reconciled(doc)
        assert report.assigned_ids == []
        assert [e.data for e in report.events] == [
            {
                "action": "add",
                "after": '<h1 data-aim="ttl">Adopted</h1>',
                "anchor": {"after": None, "container": "body"},
                "author": {"type": "external"},
                "batch": "b1",
                "kind": "direct_edit",
                "origin": "reconcile",
                "seq": 1,
                "t": ts(60),
                "target": "ttl",
            },
            {
                "action": "add",
                "after": '<p data-aim="body1">The author picked the ids.</p>',
                "anchor": {"after": "ttl", "container": "body"},
                "author": {"type": "external"},
                "batch": "b1",
                "kind": "direct_edit",
                "origin": "reconcile",
                "seq": 2,
                "t": ts(60),
                "target": "body1",
            },
        ]

    def test_flattened_document_readopts(self, rich_doc):
        rich_doc.flatten()
        doc = aim.loads(rich_doc.dumps())
        h = doc.doc_hash
        report = reconciled(doc)
        # one add per top construct; items ride inside container payloads
        assert [(e.action, e.target) for e in report.events] == [
            ("add", "h1"),
            ("add", "intro"),
            ("add", "scope"),
            ("add", "list"),
            ("add", "tbl"),
            ("add", "s1"),
        ]
        assert doc.doc_hash == h  # adoption changed nothing visible

    def test_adoption_is_idempotent(self):
        doc = aim.loads(self.HAND_WRITTEN)
        reconciled(doc)
        assert not doc.reconcile(at=ts(61)).changed


# ===========================================================================
class TestPendingLane:
    def test_proposal_on_vanished_target_is_rejected(self, basic_doc):
        prop = basic_doc.propose_modify(
            "intro",
            '<p data-aim="intro">Sharper.</p>',
            author=BOT,
            explanation="Sharper.",
            at=ts(5),
        )
        text = basic_doc.dumps().replace('<p data-aim="intro">Intro paragraph.</p>\n', "", 1)
        doc = aim.loads(text)
        report = reconciled(doc)
        assert report.rejected_proposals == [prop.id]
        assert doc.proposals == []
        resolution = report.events[-1]
        assert resolution.kind == "resolution"
        assert resolution.decision == "rejected"
        assert resolution.data["decided_by"] == {"type": "external"}

    def test_unrelated_proposal_survives(self, basic_doc):
        basic_doc.propose_modify("intro", '<p data-aim="intro">Sharper.</p>', author=BOT, at=ts(5))
        text = basic_doc.dumps().replace(">Title</h1>", ">Retitled</h1>", 1)
        doc = aim.loads(text)
        report = reconciled(doc)
        assert report.rejected_proposals == []
        assert len(doc.proposals) == 1
        doc.accept(doc.proposals[0].id, decided_by=ME, at=ts(70))
        assert doc.verify() == []  # the lane still works after reconcile

    def test_chained_add_cycle_rejects_cycle_members_not_earlier_valid_modify(self, basic_doc):
        modify = basic_doc.propose_modify(
            "intro", '<p data-aim="intro">Sharper.</p>', author=BOT, at=ts(5)
        )
        add_x = basic_doc.propose_add('<p data-aim="x">X</p>', author=BOT, after="intro", at=ts(6))
        add_y = basic_doc.propose_add('<p data-aim="y">Y</p>', author=BOT, after=add_x.id, at=ts(7))
        # Foreign-authored corruption: X and Y now anchor on each other.
        basic_doc._card_el(add_x.id).set("data-anchor-after", add_y.id)

        report = reconciled(basic_doc)

        assert set(report.rejected_proposals) == {add_x.id, add_y.id}
        assert [proposal.id for proposal in basic_doc.proposals] == [modify.id]
        basic_doc.accept(modify.id, decided_by=ME, at=ts(70))
        assert basic_doc.chunk("intro").text == "Sharper."
        assert basic_doc.verify() == []
        assert errors(basic_doc) == []


# ===========================================================================
class TestGuardsAndResidual:
    def test_pruned_history_refuses(self, rich_doc):
        rich_doc.prune(before="draft")
        doc = aim.loads(rich_doc.dumps())
        with pytest.raises(HistoryError, match="pruned"):
            doc.reconcile(at=ts(60))

    def test_corrupt_history_line_refuses(self, basic_doc):
        text = basic_doc.dumps().replace('"kind":"direct_edit"', '"kind":"direct_edit', 1)
        with pytest.raises(HistoryError):
            aim.loads(text).reconcile(at=ts(60))

    def test_unknown_event_kind_refuses(self, basic_doc):
        text = basic_doc.dumps().replace('"kind":"direct_edit"', '"kind":"snapshot"', 1)
        with pytest.raises(HistoryError, match="unknown event kind"):
            aim.loads(text).reconcile(at=ts(60))

    def test_seq_gap_refuses(self, basic_doc):
        text = basic_doc.dumps().replace('"seq":2', '"seq":9', 1)
        with pytest.raises(HistoryError, match="gap"):
            aim.loads(text).reconcile(at=ts(60))

    def test_log_that_does_not_replay_refuses(self, basic_doc):
        text = basic_doc.dumps().replace(
            '"anchor":{"after":"h1","container":"body"}',
            '"anchor":{"after":"ghost","container":"body"}',
            1,
        )
        with pytest.raises(HistoryError, match="does not replay"):
            aim.loads(text).reconcile(at=ts(60))

    def test_tampered_checkpoint_hash_is_residual(self, rich_doc):
        # the body matches the log; the damage is inside the log itself —
        # detectable, but not repairable append-only
        text = rich_doc.dumps().replace(rich_doc.doc_hash, "sha256:" + "0" * 64)
        doc = aim.loads(text)
        report = doc.reconcile(at=ts(60))
        assert not report.changed
        assert any("checkpoint" in p for p in report.residual)


# ===========================================================================
class TestLifeGoesOn:
    def test_time_travel_across_the_reconcile_boundary(self, basic_doc):
        seq0 = basic_doc.seq
        text = basic_doc.dumps().replace("Intro paragraph.", "Hand-edited intro.", 1)
        doc = aim.loads(text)
        reconciled(doc)
        assert doc.chunk("intro").text == "Hand-edited intro."
        past = doc.state_at(seq0)
        assert past.chunk("intro").text == "Intro paragraph."

    def test_reconcile_twice_second_is_noop(self, rich_doc):
        text = (
            rich_doc.dumps()
            .replace(">First</li>", ">First!</li>", 1)
            .replace("We looked at the numbers.", "Numbers, hand-checked.", 1)
        )
        doc = aim.loads(text)
        assert reconciled(doc).changed
        again = doc.reconcile(at=ts(61))
        assert not again.changed and again.events == []

    def test_normal_editing_continues_after_reconcile(self, basic_doc):
        text = basic_doc.dumps().replace("Intro paragraph.", "Edited.", 1)
        doc = aim.loads(text)
        reconciled(doc)
        doc.modify_chunk("intro", '<p data-aim="intro">Post-repair edit.</p>', author=ME, at=ts(70))
        doc.checkpoint("repaired", at=ts(71))
        assert doc.verify() == [] and errors(doc) == []

    def test_undo_walks_back_a_reconcile_event(self, basic_doc):
        text = basic_doc.dumps().replace("Intro paragraph.", "Edited.", 1)
        doc = aim.loads(text)
        reconciled(doc)
        doc.undo(author=ME, at=ts(70))
        assert doc.chunk("intro").text == "Intro paragraph."
        assert doc.verify() == []


# ===========================================================================
class TestCli:
    @pytest.fixture
    def drifted(self, tmp_path, basic_doc):
        path = tmp_path / "doc.aim"
        path.write_text(
            basic_doc.dumps().replace("Intro paragraph.", "Edited by hand.", 1), "utf-8"
        )
        return path

    def test_fix_mode_repairs_in_place(self, drifted, capsys):
        assert main(["reconcile", str(drifted)]) == 0
        out = capsys.readouterr().out
        assert "reconciled 1 out-of-band change(s)" in out
        assert f"wrote {drifted}" in out
        assert main(["lint", str(drifted)]) == 0
        assert aim.load(drifted).verify() == []

    def test_fix_mode_does_not_write_an_ambiguous_hand_bumped_version(self, tmp_path, capsys):
        path = tmp_path / "hand-bumped.aim"
        path.write_text(hand_bumped_paint_text(), "utf-8")
        before = path.read_bytes()

        assert main(["reconcile", str(path)]) == 1

        assert "unrecorded version marker" in capsys.readouterr().err
        assert path.read_bytes() == before

    def test_fix_mode_with_output_leaves_source(self, drifted, tmp_path, capsys):
        before = drifted.read_bytes()
        out_path = tmp_path / "fixed.aim"
        assert main(["reconcile", str(drifted), "-o", str(out_path)]) == 0
        assert drifted.read_bytes() == before
        assert aim.load(out_path).verify() == []

    def test_check_mode_flags_drift_and_touches_nothing(self, drifted, capsys):
        before = drifted.read_bytes()
        assert main(["reconcile", "--check", str(drifted)]) == 1
        assert "out-of-band" in capsys.readouterr().out
        assert drifted.read_bytes() == before

    def test_check_mode_passes_clean_file(self, tmp_path, basic_doc, capsys):
        path = tmp_path / "clean.aim"
        basic_doc.save(path)
        assert main(["reconcile", "--check", str(path)]) == 0
        assert "no out-of-band changes" in capsys.readouterr().out

    def test_clean_file_is_not_rewritten(self, tmp_path, basic_doc, capsys):
        path = tmp_path / "clean.aim"
        basic_doc.save(path)
        assert main(["reconcile", str(path)]) == 0
        assert "wrote" not in capsys.readouterr().out

    def test_pruned_file_fails_with_message(self, tmp_path, rich_doc, capsys):
        rich_doc.prune(before="draft")
        path = tmp_path / "pruned.aim"
        rich_doc.save(path)
        assert main(["reconcile", str(path)]) == 1
        assert "pruned" in capsys.readouterr().err

    def test_unrepairable_residual_exits_one(self, tmp_path, rich_doc, capsys):
        path = tmp_path / "tampered-log.aim"
        path.write_text(rich_doc.dumps().replace(rich_doc.doc_hash, "sha256:" + "0" * 64), "utf-8")
        assert main(["reconcile", str(path)]) == 1
        assert "unrepairable" in capsys.readouterr().err
