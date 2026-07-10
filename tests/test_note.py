"""The agent note (spec §2.5): emission, round-trip, helpers, CLI, S030."""
import json

import pytest

import aimformat as aim
from aimformat.cli import main
from aimformat.note import SIGIL, find_note, render_note

from conftest import BOT


class TestEmission:
    def test_new_document_carries_canonical_note(self):
        doc = aim.new_document(title="T")
        assert doc.has_canonical_note()
        assert doc.note == render_note()

    def test_note_sits_right_after_charset(self):
        text = aim.new_document(title="T").dumps()
        charset = text.index('<meta charset="utf-8">')
        note = text.index("<!--\naim-note:")
        title = text.index("<title>")
        assert charset < note < title

    def test_importers_carry_the_note(self):
        doc = aim.from_text("Hello world.\n\nSecond paragraph.")
        assert doc.has_canonical_note()

    def test_note_mentions_current_spec_version(self):
        doc = aim.new_document(title="T")
        assert f"v{aim.SPEC_VERSION}" in doc.note

    def test_lints_clean_and_round_trips(self):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="p1">Text.</p>', author=BOT)
        text = doc.dumps()
        assert not [f for f in aim.lint_text(text) if f.level == "error"]
        assert aim.loads(text).dumps() == text

    def test_note_is_outside_doc_hash(self):
        doc = aim.new_document(title="T")
        with_note = doc.doc_hash
        doc.remove_note()
        assert doc.doc_hash == with_note
        assert doc.seq == 0  # not evented either


class TestHelpers:
    def test_remove_then_set(self):
        doc = aim.new_document(title="T")
        doc.remove_note()
        assert doc.note is None
        doc.set_note()
        assert doc.has_canonical_note()

    def test_set_note_refreshes_stale_in_place(self):
        doc = aim.new_document(title="T")
        c = find_note(doc._state.head)
        c.data = "\naim-note: an old, stale note\n"
        assert not doc.has_canonical_note()
        doc.set_note()
        assert doc.has_canonical_note()
        # refreshed in place: still exactly one note
        text = doc.dumps()
        assert text.count(SIGIL) == 1

    def test_render_note_contains_no_markup(self):
        # No angle brackets ever: structural substring checks on documents
        # (e.g. "<aim-proposals>" in text) must not false-positive on the
        # note, and "-->" would terminate the comment early.
        data = render_note()
        assert "<" not in data
        assert "-->" not in data


class TestS030:
    def test_single_note_no_warning(self):
        doc = aim.new_document(title="T")
        assert not [f for f in aim.lint(doc) if f.code == "S030"]

    def test_duplicate_notes_warn(self):
        text = aim.new_document(title="T").dumps().replace(
            "<title>", "<!--\naim-note: duplicate\n-->\n<title>", 1)
        findings = [f for f in aim.lint_text(text) if f.code == "S030"]
        assert len(findings) == 1 and findings[0].level == "warning"

    def test_absent_note_is_not_flagged(self):
        doc = aim.new_document(title="T")
        doc.remove_note()
        assert not [f for f in aim.lint(doc) if f.code == "S030"]


class TestNoteCommand:
    @pytest.fixture
    def saved(self, tmp_path):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="p1">Text.</p>', author=BOT)
        path = tmp_path / "doc.aim"
        doc.save(path)
        return path

    def test_check_ok(self, saved, capsys):
        assert main(["note", "--check", str(saved)]) == 0
        assert "ok" in capsys.readouterr().out

    def test_check_missing_exits_1(self, saved, capsys):
        doc = aim.load(saved)
        doc.remove_note()
        doc.save(saved)
        assert main(["note", "--check", str(saved)]) == 1
        assert "missing" in capsys.readouterr().out

    def test_adds_when_missing(self, saved, capsys):
        doc = aim.load(saved)
        doc.remove_note()
        doc.save(saved)
        assert main(["note", str(saved)]) == 0
        assert "added" in capsys.readouterr().out
        assert aim.load(saved).has_canonical_note()

    def test_idempotent(self, saved, capsys):
        assert main(["note", str(saved)]) == 0
        assert "ok" in capsys.readouterr().out

    def test_remove(self, saved, capsys):
        assert main(["note", "--remove", str(saved)]) == 0
        assert "removed" in capsys.readouterr().out
        assert aim.load(saved).note is None

    def test_remove_strips_duplicate_notes(self, saved, capsys):
        # duplicate notes (the S030 warning case): --remove must strip them
        # all, not report "removed" while a second note survives
        text = saved.read_text("utf-8").replace(
            "<title>", "<!--\naim-note: duplicate\n-->\n<title>", 1)
        saved.write_text(text, "utf-8")
        assert main(["note", "--remove", str(saved)]) == 0
        assert "removed" in capsys.readouterr().out
        assert SIGIL not in saved.read_text("utf-8")

    def test_check_and_remove_conflict(self, saved):
        assert main(["note", "--check", "--remove", str(saved)]) == 2

    def test_json_format(self, saved, capsys):
        assert main(["note", "--check", "--format", "json", str(saved)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == [{"file": str(saved), "status": "ok"}]


class TestProposalVerbs:
    @pytest.fixture
    def saved(self, tmp_path):
        doc = aim.new_document(title="T")
        doc.add_chunk('<p data-aim="p1">Original.</p>', author=BOT)
        path = tmp_path / "doc.aim"
        doc.save(path)
        return path

    def test_propose_modify_then_accept(self, saved, capsys):
        rc = main(["propose", "modify", str(saved), "p1",
                   "--html", '<p data-aim="p1">Better.</p>',
                   "--author", "agent:test-model",
                   "--explanation", "Tighter."])
        assert rc == 0
        pid = capsys.readouterr().out.splitlines()[0]
        assert pid.startswith("p-")
        doc = aim.load(saved)
        assert [p.id for p in doc.proposals] == [pid]
        assert doc.proposals[0].author.model == "test-model"

        assert main(["accept", str(saved), pid,
                     "--author", "human:ada"]) == 0
        doc = aim.load(saved)
        assert not doc.proposals
        assert "Better." in doc.chunk("p1").html

    def test_propose_add_json_and_reject_all(self, saved, capsys):
        rc = main(["propose", "add", str(saved),
                   "--html", "<p>New paragraph.</p>", "--format", "json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["action"] == "add"

        assert main(["reject", str(saved), "--all",
                     "--author", "human:ada", "--format", "json"]) == 0
        decisions = json.loads(capsys.readouterr().out)
        assert [d["decision"] for d in decisions] == ["reject"]
        assert not aim.load(saved).proposals

    def test_accept_all_resolves_deletes_last(self, saved, capsys):
        # a pending add anchored on a chunk a sibling card deletes: raw card
        # order would kill the anchor first (mirrors the exporters' rule)
        main(["propose", "delete", str(saved), "p1"])
        main(["propose", "add", str(saved),
              "--html", "<p>after p1</p>", "--after", "p1"])
        capsys.readouterr()
        assert main(["accept", str(saved), "--all",
                     "--author", "human:ada"]) == 0
        doc = aim.load(saved)
        assert not doc.proposals
        assert "p1" not in [c.id for c in doc.chunks]
        assert any("after p1" in c.html for c in doc.chunks)

    def test_accept_all_resolves_add_chains_in_dependency_order(
            self, saved, capsys):
        # add B anchors on pending add A; a manual card reorder (legal —
        # lint does not require dependency order) puts B first, so raw
        # card order would hit "anchor proposal ... is still pending"
        doc = aim.load(saved)
        a = doc.propose_add("<p>first new</p>", author=BOT, after="p1")
        b = doc.propose_add("<p>second new</p>", author=BOT, after=a.id)
        doc.save(saved)
        lines = saved.read_text("utf-8").splitlines(keepends=True)
        ia = next(i for i, ln in enumerate(lines)
                  if ln.startswith(f'<aim-proposal id="{a.id}"'))
        ib = next(i for i, ln in enumerate(lines)
                  if ln.startswith(f'<aim-proposal id="{b.id}"'))
        lines[ia], lines[ib] = lines[ib], lines[ia]
        saved.write_text("".join(lines), "utf-8")
        assert [p.id for p in aim.load(saved).proposals] == [b.id, a.id]

        assert main(["accept", str(saved), "--all",
                     "--author", "human:ada"]) == 0
        capsys.readouterr()
        doc = aim.load(saved)
        assert not doc.proposals
        texts = [c.text for c in doc.chunks]
        assert texts == ["Original.", "first new", "second new"]

    def test_propose_theme_bare_slot_names(self, saved, capsys):
        # slot names start "--aim-", which argparse would eat as an option;
        # the bare form is qualified automatically
        assert main(["propose", "theme", str(saved),
                     "--set", "brand-1=#333333"]) == 0
        capsys.readouterr()
        doc = aim.load(saved)
        assert "--aim-brand-1:#333333" in doc.proposals[-1].payload_html

    def test_accept_requires_pids_or_all(self, saved):
        assert main(["accept", str(saved)]) == 2

    def test_accept_all_with_nothing_pending_is_noop(self, saved, capsys):
        assert main(["accept", str(saved), "--all"]) == 0
        assert "no pending" in capsys.readouterr().out

    def test_bad_author_is_domain_error(self, saved):
        assert main(["propose", "delete", str(saved), "p1",
                     "--author", "wizard:gandalf"]) == 1

    def test_show_json_shape(self, saved, capsys):
        main(["propose", "delete", str(saved), "p1"])
        capsys.readouterr()
        assert main(["show", str(saved), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["chunk_ids"] == ["p1"]
        assert payload["canonical_note"] is True
        assert payload["proposals"][0]["action"] == "delete"
        assert payload["history_events"] >= 1

    def test_mcp_subcommand_registered(self, capsys):
        # parse-only: the subcommand exists; execution is tested in test_mcp
        from aimformat.cli import build_parser
        args = build_parser().parse_args(["mcp"])
        assert args.command == "mcp"
