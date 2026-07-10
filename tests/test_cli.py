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
