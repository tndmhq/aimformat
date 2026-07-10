"""The aim CLI: lint / hash / new / show / flatten / css."""

import json

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
        text = saved.read_text().replace('data-aim-version="0.1"', "")
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

    def test_flatten_removes_history(self, saved, tmp_path, capsys):
        out = tmp_path / "flat.aim"
        assert main(["flatten", str(saved), "-o", str(out)]) == 0
        assert aim.load(out).history == []
        assert main(["lint", str(out)]) == 0  # flattened is still conformant

    def test_css_output_and_stats(self, capsys):
        assert main(["css"]) == 0
        css = capsys.readouterr().out
        assert "aim-proposal::" in css
        assert main(["css", "--stats"]) == 0
        assert "KB" in capsys.readouterr().out
