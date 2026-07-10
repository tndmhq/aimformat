"""MCP server: tool surface, read projection, propose/resolve round-trip."""
import json

import anyio
import pytest

import aimformat as aim
from aimformat.mcp import create_server

from conftest import BOT

mcp_memory = pytest.importorskip("mcp.shared.memory")


TOOLS = {"aim_read", "aim_edit", "aim_propose", "aim_resolve",
         "aim_lint", "aim_export"}


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
                create_server(), raise_exceptions=False) as session:
            return await session.call_tool(tool, arguments)
    return anyio.run(run)


def _payload(result) -> dict:
    assert not result.isError, result.content
    return json.loads(result.content[0].text)


def test_lists_exactly_the_six_tools():
    async def run():
        async with mcp_memory.create_connected_server_and_client_session(
                create_server()) as session:
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
    out = _payload(_call("aim_propose", {
        "path": str(path), "action": "modify", "target": "p1",
        "html": '<p data-aim="p1">Proposed better text.</p>',
        "explanation": "Tighter.", "author": "agent:test-model"}))
    pid = out["proposal"]
    assert pid.startswith("p-") and out["ok"]
    assert [p.id for p in aim.load(path).proposals] == [pid]

    out = _payload(_call("aim_resolve", {
        "path": str(path), "decision": "accept", "proposal_ids": [pid],
        "author": "human:ada"}))
    assert out["resolved"] == [pid] and out["ok"]
    doc = aim.load(path)
    assert not doc.proposals
    assert "Proposed better text." in doc.chunk("p1").html
    assert doc.seq > seq0


def test_edit_modify(tmp_path):
    path = _make_doc(tmp_path)
    out = _payload(_call("aim_edit", {
        "path": str(path), "action": "modify", "target": "p1",
        "html": '<p data-aim="p1">Directly edited.</p>'}))
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
    for tool, action in (("aim_edit", "delete"), ("aim_edit", "modify"),
                         ("aim_propose", "modify"), ("aim_propose", "move")):
        result = _call(tool, {"path": str(path), "action": action,
                              "html": "<p>x</p>"})
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
    doc.add_chunk(f'<figure data-aim="f1"><img alt="chart" '
                  f'src="{payload}"></figure>', author=BOT)
    path = tmp_path / "doc.aim"
    doc.save(path)
    out = _payload(_call("aim_read", {"path": str(path)}))
    html = next(c["html"] for c in out["chunks"] if c["id"] == "f1")
    assert "[data-uri elided]" in html
    assert "A" * 100 not in html


def test_export_markdown(tmp_path):
    path = _make_doc(tmp_path)
    out_md = tmp_path / "doc.md"
    out = _payload(_call("aim_export", {"path": str(path),
                                        "out_path": str(out_md)}))
    assert out["ok"] and out["pending"] == "drop"
    assert "Original text." in out_md.read_text()
