"""MCP server: tool surface, read projection, propose/resolve round-trip."""

import json
from pathlib import Path

import pytest

# skip before touching anything optional: without the [mcp] extra these
# imports would abort collection instead of skipping the suite
mcp_memory = pytest.importorskip("mcp.shared.memory")
anyio = pytest.importorskip("anyio")

import aimformat as aim  # noqa: E402
from aimformat.mcp import create_server  # noqa: E402
from conftest import BOT  # noqa: E402

TOOLS = {"aim_read", "aim_edit", "aim_propose", "aim_resolve", "aim_lint", "aim_export"}


def _make_doc(tmp_path, with_summary=False):
    doc = aim.new_document(title="MCP fixture")
    doc.add_chunk('<p data-aim="p1">Original text.</p>', author=BOT)
    doc.add_chunk('<p data-aim="p2">Second paragraph.</p>', author=BOT)
    if with_summary:
        doc.set_summary("Two paragraphs.", model="test-model")
    path = tmp_path / "doc.aim"
    doc.save(path)
    return path


def _call(tool: str, arguments: dict):
    """Run one tool call against an in-memory client session."""

    async def run():
        async with mcp_memory.create_connected_server_and_client_session(
            create_server(), raise_exceptions=False
        ) as session:
            return await session.call_tool(tool, arguments)

    return anyio.run(run)


def _payload(result) -> dict:
    assert not result.isError, result.content
    return json.loads(result.content[0].text)


def test_lists_exactly_the_six_tools():
    async def run():
        async with mcp_memory.create_connected_server_and_client_session(
            create_server()
        ) as session:
            return await session.list_tools()

    tools = anyio.run(run)
    assert {t.name for t in tools.tools} == TOOLS
    for t in tools.tools:
        assert t.description  # docstrings are the tool descriptions


def test_read_projection(tmp_path):
    path = _make_doc(tmp_path, with_summary=True)
    out = _payload(_call("aim_read", {"path": str(path)}))
    assert out["title"] == "MCP fixture"
    assert [c["id"] for c in out["chunks"]] == ["p1", "p2"]
    assert out["summary"] == {"text": "Two paragraphs.", "stale": False}
    assert out["proposals"] == []
    assert "history" not in out


def test_read_flags_stale_summary(tmp_path):
    path = _make_doc(tmp_path, with_summary=True)
    doc = aim.load(path)
    doc.modify_chunk("p2", '<p data-aim="p2">Changed.</p>', author=BOT)
    doc.save(path)
    out = _payload(_call("aim_read", {"path": str(path)}))
    assert out["summary"]["stale"] is True


def test_propose_then_resolve_accept_mutates_file(tmp_path):
    path = _make_doc(tmp_path)
    seq0 = aim.load(path).seq
    out = _payload(
        _call(
            "aim_propose",
            {
                "path": str(path),
                "action": "modify",
                "target": "p1",
                "html": '<p data-aim="p1">Proposed better text.</p>',
                "explanation": "Tighter.",
                "author": "agent:test-model",
            },
        )
    )
    pid = out["proposal"]
    assert pid.startswith("p-") and out["ok"]
    assert [p.id for p in aim.load(path).proposals] == [pid]

    out = _payload(
        _call(
            "aim_resolve",
            {"path": str(path), "decision": "accept", "proposal_ids": [pid], "author": "human:ada"},
        )
    )
    assert out["resolved"] == [pid] and out["ok"]
    doc = aim.load(path)
    assert not doc.proposals
    assert "Proposed better text." in doc.chunk("p1").html
    assert doc.seq > seq0


def test_edit_modify(tmp_path):
    path = _make_doc(tmp_path)
    out = _payload(
        _call(
            "aim_edit",
            {
                "path": str(path),
                "action": "modify",
                "target": "p1",
                "html": '<p data-aim="p1">Directly edited.</p>',
            },
        )
    )
    assert out["ok"] and out["lint_errors"] == 0
    assert "Directly edited." in aim.load(path).chunk("p1").html


def test_unknown_action_is_clean_error(tmp_path):
    path = _make_doc(tmp_path)
    result = _call("aim_edit", {"path": str(path), "action": "explode"})
    assert result.isError
    assert "unknown edit action" in result.content[0].text


def test_omitted_target_is_rejected_before_any_mutation(tmp_path):
    # target=None must never fall through to id resolution (it would match
    # the first chunk) or persist a broken proposal card
    path = _make_doc(tmp_path)
    before = path.read_text()
    for tool, action in (
        ("aim_edit", "delete"),
        ("aim_edit", "modify"),
        ("aim_propose", "modify"),
        ("aim_propose", "move"),
    ):
        result = _call(tool, {"path": str(path), "action": action, "html": "<p>x</p>"})
        assert result.isError, (tool, action)
        assert "requires target" in result.content[0].text
    assert path.read_text() == before


def test_omitted_payload_is_rejected(tmp_path):
    path = _make_doc(tmp_path)
    before = path.read_text()
    result = _call("aim_edit", {"path": str(path), "action": "add"})
    assert result.isError and "requires html" in result.content[0].text
    result = _call("aim_propose", {"path": str(path), "action": "theme"})
    assert result.isError and "requires theme_slots" in result.content[0].text
    assert path.read_text() == before


def test_missing_file_is_clean_error():
    result = _call("aim_read", {"path": "/nonexistent/nope.aim"})
    assert result.isError
    assert "not a file" in result.content[0].text


def test_lint_reports_findings(tmp_path):
    path = _make_doc(tmp_path)
    text = path.read_text().replace('<p data-aim="p2">', "<p>")
    path.write_text(text)
    out = _payload(_call("aim_lint", {"path": str(path)}))
    assert out["errors"] >= 1
    assert any(f["code"] == "S011" for f in out["findings"])


def test_read_elides_standard_base64_data_uris(tmp_path):
    doc = aim.new_document(title="Elide fixture")
    payload = "data:image/png;base64," + "A" * 300
    doc.add_chunk(f'<figure data-aim="f1"><img alt="chart" src="{payload}"></figure>', author=BOT)
    path = tmp_path / "doc.aim"
    doc.save(path)
    out = _payload(_call("aim_read", {"path": str(path)}))
    html = next(c["html"] for c in out["chunks"] if c["id"] == "f1")
    assert "[data-uri elided]" in html
    assert "A" * 100 not in html


def test_export_markdown(tmp_path):
    path = _make_doc(tmp_path)
    out_md = tmp_path / "doc.md"
    out = _payload(_call("aim_export", {"path": str(path), "out_path": str(out_md)}))
    assert out["ok"] and out["pending"] == "drop"
    assert "Original text." in out_md.read_text()


def test_root_gate_allows_inside_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMFORMAT_MCP_ROOT", str(tmp_path))
    path = _make_doc(tmp_path)
    out = _payload(_call("aim_read", {"path": str(path)}))
    assert [c["id"] for c in out["chunks"]] == ["p1", "p2"]
    out = _payload(_call("aim_lint", {"path": str(path)}))
    assert out["errors"] == 0
    out_md = tmp_path / "doc.md"
    out = _payload(_call("aim_export", {"path": str(path), "out_path": str(out_md)}))
    assert out["ok"] and "Original text." in out_md.read_text()


def test_root_gate_rejects_outside_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("AIMFORMAT_MCP_ROOT", str(root))
    outside = _make_doc(tmp_path)
    for tool in ("aim_read", "aim_lint"):
        result = _call(tool, {"path": str(outside)})
        assert result.isError, tool
        assert "escapes workspace root" in result.content[0].text
    # out_path is the write target — escaping there must fail even when the
    # source document sits safely inside the root
    inside = _make_doc(root)
    result = _call("aim_export", {"path": str(inside), "out_path": str(tmp_path / "escape.md")})
    assert result.isError
    assert "escapes workspace root" in result.content[0].text
    assert not (tmp_path / "escape.md").exists()


def test_root_gate_rejects_symlink_escape(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("AIMFORMAT_MCP_ROOT", str(root))
    outside = _make_doc(tmp_path)
    link = root / "doc.aim"
    link.symlink_to(outside)
    result = _call("aim_read", {"path": str(link)})
    assert result.isError
    assert "escapes workspace root" in result.content[0].text


def test_no_root_env_is_unscoped(tmp_path, monkeypatch):
    # deliberate default: with AIMFORMAT_MCP_ROOT unset the server keeps the
    # local trusted-stdio trust model — any absolute path works
    monkeypatch.delenv("AIMFORMAT_MCP_ROOT", raising=False)
    path = _make_doc(tmp_path)
    out = _payload(_call("aim_read", {"path": str(path)}))
    assert [c["id"] for c in out["chunks"]] == ["p1", "p2"]
    out_md = tmp_path / "doc.md"
    out = _payload(_call("aim_export", {"path": str(path), "out_path": str(out_md)}))
    assert out["ok"] and out_md.exists()


def test_guard_resolves_and_scopes(tmp_path, monkeypatch):
    from aimformat.mcp import _guard

    inside = tmp_path / "in.txt"
    inside.write_text("x")
    monkeypatch.delenv("AIMFORMAT_MCP_ROOT", raising=False)
    assert _guard(str(inside)) == inside.resolve()
    assert _guard("/etc/hosts") == Path("/etc/hosts").resolve()

    monkeypatch.setenv("AIMFORMAT_MCP_ROOT", str(tmp_path))
    assert _guard(str(inside)) == inside.resolve()
    with pytest.raises(ValueError, match="escapes workspace root"):
        _guard("/etc/hosts")
    link = tmp_path / "link.txt"
    link.symlink_to("/etc/hosts")
    with pytest.raises(ValueError, match="escapes workspace root"):
        _guard(str(link))
