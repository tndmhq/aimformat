"""The aim CLI: lint / hash / new / show / flatten / normalize / css."""

import json
from pathlib import Path

import pytest

import aimformat as aim
from aimformat.cli import main
from conftest import BOT, ts


@pytest.fixture
def saved(tmp_path, lifecycle_doc):
    path = tmp_path / "doc.aim"
    lifecycle_doc.save(path)
    return path


class TestLintCommand:
    def test_clean_file_exits_zero(self, saved, capsys):
        assert main(["lint", str(saved)]) == 0
        out = capsys.readouterr().out
        assert "PASS" in out

    def test_broken_file_exits_one_and_names_rule(self, saved, capsys):
        text = saved.read_text().replace(f'data-aim-version="{aim.SPEC_VERSION}"', "")
        bad = saved.with_name("bad.aim")
        bad.write_text(text)
        assert main(["lint", str(bad)]) == 1
        assert "S001" in capsys.readouterr().out

    def test_json_format(self, saved, capsys):
        assert main(["lint", "--format", "json", str(saved)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["errors"] == 0 and payload[0]["file"] == str(saved)

    def test_multiple_files_aggregate_exit_code(self, saved, tmp_path, capsys):
        bad = tmp_path / "bad.aim"
        bad.write_text(saved.read_text().replace('<meta charset="utf-8">\n', ""))
        assert main(["lint", str(saved), str(bad)]) == 1

    def test_missing_file_exits_two(self, tmp_path, capsys):
        assert main(["lint", str(tmp_path / "nope.aim")]) == 2


class TestOtherCommands:
    def test_hash_matches_api(self, saved, capsys):
        assert main(["hash", str(saved)]) == 0
        printed = capsys.readouterr().out.strip()
        assert printed == aim.load(saved).doc_hash

    def test_new_scaffolds_lintable_doc(self, tmp_path, capsys):
        out = tmp_path / "fresh.aim"
        assert main(["new", "-o", str(out), "--title", "Fresh"]) == 0
        assert main(["lint", str(out)]) == 0
        assert aim.load(out).title == "Fresh"

    def test_show_lists_pending_and_history(self, tmp_path, capsys):
        doc = aim.new_document(title="Show me")
        doc.add_chunk('<p data-aim="p1">Text.</p>', author=BOT, at=ts(0))
        doc.propose_delete("p1", author=BOT, explanation="drop it", at=ts(1))
        path = tmp_path / "s.aim"
        doc.save(path)
        assert main(["show", str(path)]) == 0
        out = capsys.readouterr().out
        assert "pending" in out and "delete" in out and "drop it" in out
        assert "doc_hash sha256:" in out

    def test_markdown_export_accepts_resolve_modes(self, tmp_path, capsys):
        doc = aim.new_document(title="Modes")
        doc.add_chunk('<p data-aim="p1">Kept.</p>', author=BOT, at=ts(0))
        doc.propose_add("<p>Fresh.</p>", after="p1", author=BOT, at=ts(1))
        src = tmp_path / "m.aim"
        doc.save(src)
        accepted = tmp_path / "accepted.md"
        assert main(["export", str(src), "-o", str(accepted), "--pending", "accept-all"]) == 0
        assert "Fresh." in accepted.read_text()
        rejected = tmp_path / "rejected.md"
        assert main(["export", str(src), "-o", str(rejected), "--pending", "reject-all"]) == 0
        text = rejected.read_text()
        assert "Kept." in text and "Fresh." not in text

    def test_aim_html_target_writes_the_file_not_a_flattened_copy(self, tmp_path):
        """`.aim.html` is the alias (spec §10), not an HTML conversion.

        It shares its suffix with the flattening `.html` export, so the
        regression this guards is silent: a lossy copy under a name that
        promises the whole file.
        """
        doc = aim.new_document(title="Alias")
        doc.add_chunk('<p data-aim="p1">Kept.</p>', author=BOT, at=ts(0))
        doc.propose_add("<p>Fresh.</p>", after="p1", author=BOT, at=ts(1))
        src = tmp_path / "a.aim"
        doc.save(src)

        alias = tmp_path / "a.aim.html"
        assert main(["export", str(src), "-o", str(alias)]) == 0
        assert alias.read_bytes() == src.read_bytes()

        flat = tmp_path / "a.html"
        assert main(["export", str(src), "-o", str(flat)]) == 0
        assert flat.read_bytes() != src.read_bytes()  # the conversion still converts

        loaded = aim.load(alias)
        assert loaded.history and len(loaded.proposals) == 1

    def test_aim_html_target_rejects_a_lane_fate(self, tmp_path, capsys):
        doc = aim.new_document(title="Alias")
        src = tmp_path / "a.aim"
        doc.save(src)
        out = tmp_path / "a.aim.html"
        assert main(["export", str(src), "-o", str(out), "--pending", "accept-all"]) == 2
        assert "not valid for .aim.html" in capsys.readouterr().err
        assert not out.exists()

    def test_flatten_removes_history(self, saved, tmp_path, capsys):
        out = tmp_path / "flat.aim"
        assert main(["flatten", str(saved), "-o", str(out)]) == 0
        # one anchoring checkpoint remains (IMPORT-D12): a pruned log
        assert [e.kind for e in aim.load(out).history] == ["checkpoint"]
        assert main(["lint", str(out)]) == 0  # flattened is still conformant

    def test_css_output_and_stats(self, capsys):
        assert main(["css"]) == 0
        css = capsys.readouterr().out
        assert "aim-proposal::" in css
        assert main(["css", "--stats"]) == 0
        assert "KB" in capsys.readouterr().out


class TestNormalizeCommand:
    """`aim normalize`: tier-2 canonicalization — lossless, idempotent."""

    @pytest.fixture
    def non_canonical(self, tmp_path):
        src = Path(__file__).parent / "fixtures" / "nok_C001_not_canonical.aim"
        dst = tmp_path / "doc.aim"
        dst.write_text(src.read_text("utf-8"), "utf-8")
        return dst

    def test_rewrites_to_canonical_and_lints_clean(self, non_canonical, capsys):
        assert main(["lint", str(non_canonical)]) == 1  # C001 before
        assert main(["normalize", str(non_canonical)]) == 0
        assert "wrote" in capsys.readouterr().out
        assert main(["lint", str(non_canonical)]) == 0  # canonical after

    def test_idempotent(self, non_canonical, capsys):
        assert main(["normalize", str(non_canonical)]) == 0
        first = non_canonical.read_text("utf-8")
        assert main(["normalize", str(non_canonical)]) == 0
        assert "already canonical" in capsys.readouterr().out
        assert non_canonical.read_text("utf-8") == first

    def test_doc_hash_unchanged(self, non_canonical):
        before = aim.load(non_canonical).doc_hash
        assert main(["normalize", str(non_canonical)]) == 0
        assert aim.load(non_canonical).doc_hash == before

    def test_lossless_on_content(self, non_canonical):
        chunks_before = {c.id: c.text for c in aim.load(non_canonical).chunks}
        assert main(["normalize", str(non_canonical)]) == 0
        assert {c.id: c.text for c in aim.load(non_canonical).chunks} == chunks_before

    def test_check_reports_without_writing(self, non_canonical, capsys):
        original = non_canonical.read_text("utf-8")
        assert main(["normalize", "--check", str(non_canonical)]) == 1
        assert "not canonical" in capsys.readouterr().out
        assert non_canonical.read_text("utf-8") == original

    def test_check_passes_on_canonical(self, saved, capsys):
        assert main(["normalize", "--check", str(saved)]) == 0
        assert "canonical" in capsys.readouterr().out

    def test_output_flag_keeps_original(self, non_canonical, tmp_path, capsys):
        original = non_canonical.read_text("utf-8")
        out = tmp_path / "normalized.aim"
        assert main(["normalize", str(non_canonical), "-o", str(out)]) == 0
        assert non_canonical.read_text("utf-8") == original
        assert main(["lint", str(out)]) == 0

    def test_crlf_agreement_between_check_and_c001(self, saved, tmp_path, capsys):
        """`normalize --check` and lint's C001 measure the same bytes
        (spec §11 byte equality) and may never disagree (Codex review #2).
        A blanket CRLF conversion also mangles machine-managed block
        INTERIORS (css, history JSONL) — those are flagged by their own
        rules (X006/H005) and are deliberately NOT normalize's to rewrite;
        the agreement contract is about C001 specifically."""
        from aimformat.lint import lint_path

        crlf = tmp_path / "crlf.aim"
        crlf.write_bytes(saved.read_bytes().replace(b"\n", b"\r\n"))
        assert main(["normalize", "--check", str(crlf)]) == 1
        assert any(f.code == "C001" for f in lint_path(crlf))
        assert main(["normalize", str(crlf)]) == 0
        # structure is canonical again: C001 gone AND --check agrees
        assert not any(f.code == "C001" for f in lint_path(crlf))
        assert main(["normalize", "--check", str(crlf)]) == 0
        # interior damage stays flagged by its dedicated rules, untouched
        # by the lossless re-speller
        assert any(f.code in ("X006", "H005") for f in lint_path(crlf))


# --------------------------------------------------------------------------- 0.6 agent surface
GOLDENS = Path(__file__).resolve().parent / "goldens" / "views"
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def contract(tmp_path):
    doc = aim.new_document(title="Contract")
    with doc.batch():
        doc.add_chunk('<h1 data-aim="h1" class="num-1">Definitions</h1>', author=BOT)
        doc.add_chunk('<p data-aim="a1" class="num-2">Terms mean things.</p>', author=BOT)
        doc.add_chunk(
            '<p data-aim="a2" class="num-2">"Subprocessor" means a vendor.</p>', author=BOT
        )
    path = tmp_path / "c.aim"
    doc.save(path)
    return path


class TestShowModes:
    def test_overview_is_unchanged(self, capsys, monkeypatch):
        """No flags: byte-for-byte the pre-0.6 output (golden captured from 0.5.2)."""
        monkeypatch.chdir(ROOT)
        assert main(["show", "examples/proposal.aim"]) == 0
        assert capsys.readouterr().out == (GOLDENS / "show-proposal.txt").read_text()
        assert main(["show", "examples/proposal.aim", "--format", "json"]) == 0
        assert capsys.readouterr().out == (GOLDENS / "show-proposal.json").read_text()

    def test_text_modes_print_what_mcp_returns(self, contract, capsys):
        from aimformat import views

        doc = aim.load(contract)
        for mode, expected in (
            ("toc", views.render_toc(doc)),
            ("skeleton", views.render_skeleton(doc)),
            ("text", views.render_text(doc)),
        ):
            assert main(["show", str(contract), "--mode", mode]) == 0
            assert capsys.readouterr().out == expected + "\n"
        assert main(["show", str(contract), "--mode", "chunks", "--ids", "a1,a2"]) == 0
        assert capsys.readouterr().out == views.render_chunks(doc, ["a1", "a2"]) + "\n"
        assert main(["show", str(contract), "--mode", "full"]) == 0
        assert json.loads(capsys.readouterr().out) == views.full_projection(doc)

    def test_json_forms(self, contract, capsys):
        assert main(["show", str(contract), "--mode", "toc", "--format", "json"]) == 0
        toc = json.loads(capsys.readouterr().out)
        assert toc[0]["first"] == "h1" and toc[0]["label"] == "1."
        assert main(["show", str(contract), "--mode", "skeleton", "--format", "json"]) == 0
        units = json.loads(capsys.readouterr().out)
        assert [u["id"] for u in units] == ["h1", "a1", "a2"]
        assert (
            main(["show", str(contract), "--mode", "chunks", "--ids", "a1..a2", "--format", "json"])
            == 0
        )
        got = json.loads(capsys.readouterr().out)
        assert [c["id"] for c in got["chunks"]] == ["a1", "a2"] and got["missing"] == []

    def test_exit_codes(self, contract, capsys):
        assert main(["show", str(contract), "--mode", "chunks", "--ids", "nope"]) == 1
        assert "[missing] nope" in capsys.readouterr().out
        assert main(["show", str(contract), "--mode", "chunks", "--ids", "a1,nope"]) == 0
        capsys.readouterr()
        assert main(["show", str(contract), "--mode", "chunks"]) == 2
        assert main(["show", str(contract), "--mode", "chunks", "--ids", "a2..h1"]) == 2
        assert "reversed range" in capsys.readouterr().err
        assert main(["show", str(contract), "--mode", "skeleton", "--words", "99"]) == 2
        assert main(["show", str(contract), "--ids", "a1"]) == 2
        assert main(["show", str(contract), "--mode", "text", "--ids", "a1"]) == 2


class TestSearchCommand:
    def test_text_and_json(self, contract, capsys):
        assert main(["search", str(contract), "subprocessors"]) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[1].startswith("[a2] § 1. Definitions | ")
        assert main(["search", str(contract), "1.2", "--format", "json", "-k", "1"]) == 0
        hits = json.loads(capsys.readouterr().out)
        assert [h["id"] for h in hits] == ["a2"]
        assert main(["search", str(contract), "x", "-k", "0"]) == 2


class TestEditAndBatchCommands:
    def test_edit_each_action(self, contract, capsys):
        f = str(contract)
        assert (
            main(["edit", "modify", f, "a1", "--html", '<p data-aim="a1" class="num-2">New.</p>'])
            == 0
        )
        assert capsys.readouterr().out.splitlines() == ["a1", f"wrote {f}"]
        assert main(["edit", "add", f, "--html", "<p>Added.</p>", "--after", "a1"]) == 0
        new_id = capsys.readouterr().out.splitlines()[0]
        doc = aim.load(contract)
        assert [c.id for c in doc.chunks] == ["h1", "a1", new_id, "a2"]
        assert main(["edit", "move", f, new_id, "--after", "first"]) == 0
        assert main(["edit", "delete", f, new_id]) == 0
        assert main(["edit", "theme", f, "--set", "brand-1=#333333"]) == 0
        doc = aim.load(contract)
        assert doc.theme["--aim-brand-1"] == "#333333"
        assert [c.id for c in doc.chunks] == ["h1", "a1", "a2"]
        assert not doc.proposals  # direct edits, not cards
        assert {ev.action for ev in doc.history[-4:]} == {"add", "move", "delete", "modify"}

    def test_edit_error_exits_one_and_writes_nothing(self, contract, capsys):
        before = contract.read_bytes()
        assert main(["edit", "delete", str(contract), "ghost"]) == 1
        assert "ghost" in capsys.readouterr().err
        assert contract.read_bytes() == before

    def test_edit_batch_from_file(self, contract, tmp_path, capsys):
        ops = [
            {"action": "add", "html": "<h2>New</h2>", "after": "a2"},
            {"action": "add", "html": "<p>Under.</p>", "after": "$0"},
        ]
        ops_file = tmp_path / "ops.json"
        ops_file.write_text(json.dumps(ops))
        assert main(["edit", "batch", str(contract), str(ops_file), "--format", "json"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] and len(out["results"]) == 2
        doc = aim.load(contract)
        assert [c.id for c in doc.chunks][-2:] == [r["id"] for r in out["results"]]
        assert {ev.get("batch") for ev in doc.history[-2:]} == {out["batch"]}

    def test_propose_batch_from_stdin(self, contract, capsys, monkeypatch):
        import io

        ops = [
            {
                "action": "modify",
                "target": "a1",
                "html": '<p data-aim="a1" class="num-2">Better.</p>',
            },
            {"action": "delete", "target": "a2", "explanation": "Unused."},
        ]
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(ops)))
        assert main(["propose", "batch", str(contract), "-", "--explanation", "Tidy."]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines[0].startswith("ops[0] p-") and lines[0].endswith("-> a1")
        doc = aim.load(contract)
        assert [p.explanation for p in doc.proposals] == ["Tidy.", "Unused."]
        assert len({p.batch for p in doc.proposals}) == 1

    def test_batch_input_errors(self, contract, tmp_path, capsys):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert main(["edit", "batch", str(contract), str(bad)]) == 2
        bad.write_text('{"action": "add"}')
        assert main(["edit", "batch", str(contract), str(bad)]) == 2
        bad.write_text(json.dumps([{"action": "delete", "target": "a1"}] * 26))
        assert main(["propose", "batch", str(contract), str(bad)]) == 2

    def test_edit_restores_elided_images(self, tmp_path, capsys):
        from aimformat import views

        uri = "data:image/png;base64," + "QUJD" * 100
        doc = aim.new_document(title="Img")
        doc.add_chunk(
            f'<figure data-aim="fig"><img alt="x" src="{uri}">'
            "<figcaption>cap</figcaption></figure>",
            author=BOT,
        )
        path = tmp_path / "img.aim"
        doc.save(path)
        html = views.render_chunks(doc, ["fig"]).splitlines()[2]
        assert uri not in html
        new = html.replace(">cap<", ">fixed<")
        assert main(["edit", "modify", str(path), "fig", "--html", new]) == 0
        live = aim.load(path).chunk("fig").html
        assert uri in live and "fixed" in live
