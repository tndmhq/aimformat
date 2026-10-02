"""The ``baseline`` history event (spec §6.9, v0.6) and the lifecycle around it.

A baseline makes the current state the origin of the retained log: ONE event
carrying the state as a snapshot, instead of an ``add`` per construct. These
tests pin what the decisions IMPORT-D9 to IMPORT-D16 promise: verification,
time travel and reconcile work from the file alone; undo has nothing to undo;
flatten collapses to a checkpoint; importers write exactly one history line
whose snapshot is the reduced projection; the TOC cache is built and kept
fresh.
"""

from __future__ import annotations

import pytest

import aimformat as aim
from aimformat.convert import from_markdown, from_text
from aimformat.document import snapshot_hash

ADA = aim.human("ada")
BOT = aim.agent("model-x")


def ts(i: int) -> str:
    return f"2026-10-01T12:{i // 60:02d}:{i % 60:02d}Z"


@pytest.fixture
def imported() -> aim.AimDocument:
    return from_markdown(
        "# Contract\n\nClause one.\n\n## Fees\n\nClause two.\n\n- alpha\n- beta\n",
        title="Contract",
    )


def _errors(doc: aim.AimDocument) -> list:
    return [f for f in aim.lint_text(doc.dumps()) if f.level == "error"]


class TestImportShape:
    @pytest.mark.parametrize(
        "make",
        [
            lambda: from_text("One.\n\nTwo.", title="t"),
            lambda: from_markdown("# H\n\nBody.\n\n1. a\n2. b\n", title="m"),
        ],
        ids=["text", "markdown"],
    )
    def test_one_history_line_whose_snapshot_is_the_projection(self, make):
        doc = make()
        (event,) = doc.history
        assert event.kind == "baseline" and event.seq == 1 and event.get("label") == "import"
        assert event.get("snapshot") == doc._state.snapshot()
        assert event.get("doc_hash") == doc.doc_hash == snapshot_hash(event.get("snapshot"))
        assert doc.spec_version == aim.SPEC_VERSION
        assert _errors(doc) == []

    def test_docx_import_is_one_baseline_with_provenance(self):
        pytest.importorskip("docx_parser_converter")
        from pathlib import Path

        path = Path(__file__).parent / "fixtures" / "docxs" / "legal-addendum.docx"
        with _no_warning():
            doc = aim.from_docx(path)
        (event,) = doc.history
        assert event.kind == "baseline"
        assert event.get("explanation") == "Imported from 'legal-addendum.docx'"
        (digest,) = event.get("source")
        assert digest.startswith("sha256:") and len(digest) == 71
        # theme and page setup are origin state now, not untracked extras
        assert "theme" in event.get("snapshot")

    def test_undo_after_import_has_nothing_to_undo(self, imported):
        with pytest.raises(aim.InvalidOperation, match="nothing to undo"):
            imported.undo(author=ADA)
        imported.add_chunk("<p>Added.</p>", author=ADA)
        imported.undo(author=ADA)  # edits after the import undo as before
        assert "Added." not in [c.text for c in imported.chunks]

    def test_imports_with_headings_carry_a_fresh_toc(self, imported):
        meta = imported.meta
        assert meta is not None and "summary" not in meta
        assert meta["toc"][0]["title"] == "Contract"
        assert meta["toc_doc_hash"] == imported.doc_hash
        assert imported.toc_is_fresh()

    def test_imports_without_headings_carry_no_toc(self):
        assert from_text("One.\n\nTwo.", title="t").meta is None


class TestVerifyAndTimeTravel:
    def test_a_baselined_document_verifies(self, imported):
        imported.add_chunk("<p>After.</p>", author=ADA, at=ts(1))
        assert imported.verify() == []
        assert _errors(imported) == []

    def test_state_at_the_baseline_is_the_snapshot_and_nothing_before_it(self, imported):
        origin = imported.doc_hash
        imported.add_chunk("<p>After.</p>", author=ADA, at=ts(1))
        assert imported.state_at(1).doc_hash == origin
        with pytest.raises(aim.HistoryError, match="baseline"):
            imported.state_at(0)

    def test_a_hand_edit_under_a_baseline_is_named(self, imported):
        # ">…</p>" hits the body line only: the snapshot spells it "<\/p>"
        text = imported.dumps().replace(">Clause two.</p>", ">Clause 2.</p>")
        doc = aim.loads(text)
        problems = doc.verify()
        assert problems and "body construct 4" in problems[0]
        assert "H006" in {f.code for f in aim.lint_text(text)}

    def test_a_tampered_snapshot_hash_is_H008(self, imported):
        text = imported.dumps()
        at = text.index('"kind":"baseline"')
        line_start = text.rindex("\n", 0, at)
        line = text[line_start : text.index("\n", at)]
        text = text.replace(line, line.replace(imported.doc_hash, "sha256:" + "0" * 64))
        codes = {f.code for f in aim.lint_text(text) if f.level == "error"}
        assert "H008" in codes
        assert aim.loads(text).verify()  # the API reports it too

    def test_two_constructs_in_one_entry_is_H008(self, imported):
        doc = aim.new_document(title="t")
        doc.add_chunk('<p data-aim="a">A.</p>', author=ADA)
        doc.baseline("import", at=ts(0))
        text = doc.dumps().replace(
            '"body":["<p data-aim=\\"a\\">A.<\\/p>"]',
            '"body":["<p data-aim=\\"a\\">A.<\\/p><p>B<\\/p>"]',
        )
        assert "H008" in {f.code for f in aim.lint_text(text)}

    def test_prune_by_the_baseline_label(self, imported):
        imported.add_chunk("<p>After.</p>", author=ADA, at=ts(1))
        assert imported.prune(before="import") == 0
        imported.checkpoint("later", at=ts(2))
        assert imported.prune(before="later") == 2
        assert imported.history[0].kind == "checkpoint"
        assert imported.verify() == []


class TestBaselineOperation:
    def test_collapses_a_log_and_keeps_seq_monotonic(self):
        doc = aim.new_document(title="t")
        for i in range(3):
            doc.add_chunk(f"<p>{i}</p>", author=ADA, at=ts(i))
        p = doc.propose_add("<p>pending</p>", author=BOT, at=ts(4))
        event = doc.baseline("restart", author=ADA, explanation="why", at=ts(5))
        assert event.seq == 4 and [e.kind for e in doc.history] == ["baseline"]
        assert doc.proposal(p.id)  # the pending lane stays
        doc.flatten(at=ts(6))
        assert [(e.kind, e.seq) for e in doc.history] == [("checkpoint", 5)]

    def test_raises_an_older_declaration_without_a_version_event(self):
        doc = aim.loads(
            aim.new_document(title="t")
            .dumps()
            .replace(f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.5"')
        )
        doc.add_chunk("<p>x</p>", author=ADA)
        doc.baseline("import")
        assert doc.spec_version == aim.REGISTRY.baseline_since
        assert doc.history[0].get("snapshot")["html"].count(aim.REGISTRY.baseline_since) == 1
        assert _errors(doc) == []

    def test_refuses_to_freeze_a_non_conforming_construct(self):
        doc = aim.new_document(title="t")
        doc.add_chunk("<p>Fine.</p>", author=ADA, at=ts(0))
        doc.add_chunk('<p class="bogus">Not fine.</p>', author=ADA, at=ts(1))
        before = doc.dumps()
        with pytest.raises(aim.InvalidOperation, match="V005"):
            doc.baseline("as-is", author=ADA, at=ts(2))
        assert doc.dumps() == before  # nothing changed, not even the version

    def test_an_import_with_a_non_conforming_construct_keeps_its_adds(self):
        from aimformat.ingest import finish_import

        doc = aim.new_document(title="t")
        doc.add_chunk('<p class="bogus">x</p>', author=ADA, at=ts(0))
        finish_import(doc, author=ADA, explanation="test")
        assert [e.action for e in doc.history] == ["add"]
        assert [f.code for f in _errors(doc)] == ["V005"]

    def test_classify_divergence_reads_a_rebaseline_as_rewritten(self, imported):
        before = aim.loads(imported.dumps())
        imported.add_chunk("<p>After.</p>", author=ADA)
        imported.baseline("again")
        verdict = aim.classify_divergence(before, aim.loads(imported.dumps()))
        assert verdict.history_rewritten

    def test_cli(self, tmp_path, capsys):
        from aimformat.cli import main

        doc = aim.new_document(title="t")
        doc.add_chunk("<p>x</p>", author=ADA)
        path = tmp_path / "f.aim"
        doc.save(path)
        assert main(["baseline", str(path), "--label", "start"]) == 0
        (event,) = aim.load(path).history
        assert event.kind == "baseline" and event.get("label") == "start"

    def test_is_not_an_mcp_tool(self):
        pytest.importorskip("mcp")
        from aimformat.mcp import create_server

        names = {t.name for t in create_server()._tool_manager.list_tools()}
        assert not any("baseline" in n for n in names)


class TestReconcileFromABaseline:
    def test_a_hand_edit_to_untouched_imported_content_is_adopted(self, imported):
        # the editor's desktop case: an agent hand-edits an imported contract
        imported.add_chunk("<p>Recorded edit.</p>", author=ADA, at=ts(1))
        text = imported.dumps().replace(">Clause one.</p>", ">Clause one, amended.</p>")
        doc = aim.loads(text)
        report = doc.reconcile(at=ts(2))
        (event,) = report.events
        assert event.action == "modify" and "Clause one.</p>" in event.get("before")
        assert doc.verify() == [] and _errors(doc) == []
        assert doc.state_at(1).doc_hash == imported.state_at(1).doc_hash

    def test_a_hand_edited_theme_is_tracked_origin_state(self):
        doc = from_text("One.", title="t", theme={"--aim-brand-1": "#112233"})
        text = doc.dumps().replace("#112233}</style>", "#445566}</style>")
        edited = aim.loads(text)
        report = edited.reconcile(at=ts(1))
        assert [e.target for e in report.events] == ["aim:theme"]
        assert edited.verify() == []

    def test_a_hand_edited_lang_does_not_break_reconcile(self, imported):
        # No event records `lang`; the expected state must keep the file's
        # own <html> attributes (as the empty-origin path does) instead of
        # the snapshot's, or reconcile cannot converge ("bug in aimformat").
        text = imported.dumps().replace('lang="en"', 'lang="de"', 1)
        doc = aim.loads(text)
        report = doc.reconcile(at=ts(1))
        assert report.events == []
        assert any("snapshot" in problem for problem in report.residual)
        # ...and a real edit next to it is still adopted, the lang kept
        text = text.replace(">Clause one.</p>", ">Clause one, amended.</p>")
        doc = aim.loads(text)
        report = doc.reconcile(at=ts(2))
        assert [e.action for e in report.events] == ["modify"]
        assert 'lang="de"' in doc.dumps()


class TestAdoption:
    HAND = (
        '<!doctype html>\n<html data-aim-version="{v}" lang="en">\n<head>\n'
        '<meta charset="utf-8">\n<title>Hand</title>\n</head>\n<body>\n'
        "<h1>Hand written</h1>\n<p>No history at all.</p>\n</body>\n</html>\n"
    )

    def test_a_06_file_is_adopted_as_one_baseline(self):
        doc = aim.loads(self.HAND.format(v="0.6"))
        report = doc.reconcile(at=ts(0))
        assert [e.kind for e in report.events] == ["baseline"]
        assert report.events[0].get("label") == "adopt"
        assert "baseline" in report.summary()
        assert doc.verify() == [] and _errors(doc) == []

    def test_a_05_file_keeps_the_per_construct_adoption(self):
        # never raise a declaration as a side effect (§3.7)
        doc = aim.loads(self.HAND.format(v="0.5"))
        report = doc.reconcile(at=ts(0))
        assert [e.action for e in report.events] == ["add", "add"]
        assert doc.spec_version == "0.5"

    def test_a_lint_error_in_the_body_is_not_frozen_into_the_origin(self):
        # A baseline is never undone: adopting <p class="bogus"> as one froze
        # H008 into the log for good, so fixing the body never cleared it.
        text = self.HAND.format(v="0.6").replace("<p>", '<p class="bogus">')
        doc = aim.loads(text)
        report = doc.reconcile(at=ts(0))
        assert [e.action for e in report.events] == ["add", "add"]
        assert [f.code for f in _errors(doc)] == ["V005"]
        target = doc.chunks[1].id
        doc.modify_chunk(target, f'<p data-aim="{target}">Fixed.</p>', author=ADA, at=ts(1))
        assert doc.verify() == [] and _errors(doc) == []

    @pytest.mark.parametrize("version", ["0.5", "0.6"])
    def test_an_empty_file_still_rejects_its_dangling_cards(self, version):
        # nothing to record as an origin, but a card aimed at a chunk the
        # body no longer has is rejected on both adoption paths (P008 else)
        doc = aim.new_document(title="t")
        target = doc.add_chunk("<p>x</p>", author=ADA, at=ts(0)).id
        doc.propose_delete(target, author=ADA, at=ts(1))
        text = doc.dumps().replace(f'<p data-aim="{target}">x</p>\n', "")
        start = text.index('<script type="application/aim-history+jsonl">')
        text = text[:start] + text[text.index("</script>", start) + len("</script>\n") :]
        text = text.replace('data-aim-version="0.6"', f'data-aim-version="{version}"', 1)
        hand = aim.loads(text)
        report = hand.reconcile(at=ts(2))
        assert hand.proposals == [] and len(report.rejected_proposals) == 1
        assert _errors(hand) == []


class TestFlatten:
    def test_flatten_after_an_sdk_edit_no_longer_crashes_reconcile(self, imported):
        # the measured bug: flatten → SDK edit → reconcile replayed from an
        # empty origin and raised "cannot replace …: not found"
        imported.flatten()
        imported.add_chunk("<p>Later.</p>", author=ADA)
        doc = aim.loads(imported.dumps())
        assert doc.verify() == []
        with pytest.raises(aim.HistoryError, match="pruned"):
            doc.reconcile()

    def test_flatten_then_hand_edit_is_detected(self, imported):
        imported.flatten()
        doc = aim.loads(imported.dumps().replace(">Clause one.</p>", ">Clause uno.</p>"))
        assert doc.verify()


class TestOrigin:
    def test_H009_warns_on_an_unrecorded_origin(self):
        doc = aim.new_document(title="t")
        doc.add_chunk('<p data-aim="p1">Recorded.</p>', author=ADA)
        text = doc.dumps().replace(
            '<p data-aim="p1">', '<p data-aim="p0">Unrecorded.</p>\n<p data-aim="p1">'
        )
        findings = aim.lint_text(text)
        assert "H009" in {f.code for f in findings}
        assert not [f for f in findings if f.level == "error"]

    def test_an_untracked_theme_is_tolerated(self):
        doc = aim.new_document(title="t", theme={"--aim-brand-1": "#112233"})
        doc.add_chunk("<p>x</p>", author=ADA)
        assert "H009" not in {f.code for f in aim.lint_text(doc.dumps())}


class TestToc:
    def test_dumps_refreshes_a_present_toc(self, imported):
        heading = next(c for c in imported.chunks if c.tag == "h1")
        imported.modify_chunk(heading.id, f'<h1 data-aim="{heading.id}">Agreement</h1>', author=ADA)
        doc = aim.loads(imported.dumps())
        assert doc.meta["toc"][0]["title"] == "Agreement"
        assert doc.toc_is_fresh()

    def test_a_document_without_a_toc_does_not_get_one(self):
        doc = aim.new_document(title="t")
        doc.add_chunk("<h1>H</h1>", author=ADA)
        assert aim.loads(doc.dumps()).meta is None

    def test_a_stale_toc_from_another_writer_is_M005(self, imported):
        text = imported.dumps().replace(
            f'"toc_doc_hash":"{imported.doc_hash}"', '"toc_doc_hash":"sha256:' + "1" * 64 + '"'
        )
        findings = aim.lint_text(text)
        assert "M005" in {f.code for f in findings}
        assert not [f for f in findings if f.level == "error"]


class _no_warning:
    def __enter__(self):
        import warnings

        self._ctx = warnings.catch_warnings()
        self._ctx.__enter__()
        warnings.simplefilter("ignore")

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)


def test_a_baseline_refuses_a_body_without_usable_ids(imported):
    # the snapshot is reconcile's expected origin: a duplicated id in it can
    # never converge with the fixed-up actual body ("bug in aimformat")
    text = imported.dumps()
    first = imported.chunks[1].id
    second = imported.chunks[3].id
    broken = aim.loads(text.replace(f'data-aim="{second}"', f'data-aim="{first}"', 1))
    with pytest.raises(aim.InvalidOperation, match="usable id"):
        broken.baseline("again")
