"""MCP server: tool surface, read projection, propose/resolve round-trip."""

import json
import re
from pathlib import Path

import pytest

# skip before touching anything optional: without the [mcp] extra these
# imports would abort collection instead of skipping the suite
mcp_memory = pytest.importorskip("mcp.shared.memory")
anyio = pytest.importorskip("anyio")

import aimformat as aim  # noqa: E402
from aimformat.mcp import create_server  # noqa: E402
from conftest import BOT  # noqa: E402

TOOLS = {
    "aim_read",
    "aim_search",
    "aim_edit",
    "aim_propose",
    "aim_resolve",
    "aim_lint",
    "aim_export",
}


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


def _list_tools():
    async def run():
        async with mcp_memory.create_connected_server_and_client_session(
            create_server()
        ) as session:
            return await session.list_tools()

    return anyio.run(run)


def test_lists_exactly_the_seven_tools():
    tools = _list_tools()
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
    # the action enum is in the schema, so the argument validator names the
    # allowed values before the tool body runs
    path = _make_doc(tmp_path)
    before = path.read_text()
    result = _call("aim_edit", {"path": str(path), "action": "explode"})
    assert result.isError
    assert "set_theme" in result.content[0].text
    assert path.read_text() == before


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
    assert re.search(r"\[elided: \d+B, sha256:[0-9a-f]{16}\]", html)
    assert "A" * 100 not in html


def test_export_markdown(tmp_path):
    path = _make_doc(tmp_path)
    out_md = tmp_path / "doc.md"
    out = _payload(_call("aim_export", {"path": str(path), "out_path": str(out_md)}))
    assert out["ok"] and out["pending"] == "drop"
    assert "Original text." in out_md.read_text()


def test_export_aim_html_is_the_file_itself(tmp_path):
    """The alias shares `.html` with the flattening export — writing a
    flattened copy under it would silently drop the history."""
    path = _make_doc(tmp_path)
    alias = tmp_path / "doc.aim.html"
    out = _payload(_call("aim_export", {"path": str(path), "out_path": str(alias)}))
    assert out["ok"]
    assert alias.read_bytes() == path.read_bytes()


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


def test_history_payloads_are_elided(tmp_path):
    # AF-23: the include_history branch returned raw event payloads, so
    # add/modify events dumped full base64 data URIs into model context —
    # the exact token blowup _elide exists to prevent
    uri = "data:image/png;base64," + "A" * 200
    doc = aim.new_document(title="MCP fixture")
    doc.add_chunk(f'<figure data-aim="fig"><img alt="dot" src="{uri}"></figure>', author=BOT)
    path = tmp_path / "img.aim"
    doc.save(path)
    out = _payload(_call("aim_read", {"path": str(path), "include_history": True}))
    dumped = json.dumps(out["history"])
    assert "A" * 64 not in dumped
    assert "[elided: " in dumped


def test_read_never_serves_a_stale_outline(tmp_path):
    # IMPORT-D13: no TOC cache (any 0.5 document) → derived live; a fresh
    # cache → served as is; a cache a foreign writer left stale → derived
    doc = aim.new_document(title="Outline")
    doc.add_chunk('<h1 data-aim="h1">Intro</h1>', author=BOT)
    doc.add_chunk('<p data-aim="p1">Text.</p>', author=BOT)
    path = tmp_path / "outline.aim"
    doc.save(path)
    out = _payload(_call("aim_read", {"path": str(path)}))
    assert out["toc_source"] == "derived"
    assert out["toc"] == [{"title": "Intro", "level": 1, "chunks": ["h1", "p1"]}]

    doc.generate_toc()
    doc.save(path)
    out = _payload(_call("aim_read", {"path": str(path)}))
    assert out["toc_source"] == "cache"

    text = path.read_text("utf-8").replace(">Intro</h1>", ">Overview</h1>")
    path.write_text(text, "utf-8")
    out = _payload(_call("aim_read", {"path": str(path)}))
    assert out["toc_source"] == "derived"
    assert out["toc"][0]["title"] == "Overview"


def test_history_snapshots_are_elided(tmp_path):
    # a baseline snapshot is a nested object carrying the body's blobs
    uri = "data:image/png;base64," + "A" * 200
    doc = aim.new_document(title="MCP fixture")
    doc.add_chunk(f'<figure data-aim="fig"><img alt="dot" src="{uri}"></figure>', author=BOT)
    doc.baseline("import")
    path = tmp_path / "img.aim"
    doc.save(path)
    out = _payload(_call("aim_read", {"path": str(path), "include_history": True}))
    dumped = json.dumps(out["history"])
    assert "A" * 64 not in dumped and "[elided: " in dumped


def test_read_does_not_repeat_every_id_for_a_headingless_body(tmp_path):
    # without a heading or slide the live outline is one untitled entry
    # listing every chunk id — the "chunks" list already carries them
    doc = aim.new_document(title="Flat")
    for i in range(3):
        doc.add_chunk(f'<p data-aim="p{i}">Text {i}.</p>', author=BOT)
    path = tmp_path / "flat.aim"
    doc.save(path)
    out = _payload(_call("aim_read", {"path": str(path)}))
    assert out["toc"] is None and out["toc_source"] is None


# --------------------------------------------------------------------------- 0.6 surface
# READS-D9/D10: one compact text block per result; a lean tool list.

SURFACE_BYTE_BUDGET = 6470  # compact tools/list + instructions; measured 5881 (0.6.0) + 10%


def test_tool_list_is_lean():
    tools = _list_tools().tools
    dumped = [t.model_dump(exclude_none=True, by_alias=True) for t in tools]
    text = json.dumps(dumped)
    assert all("outputSchema" not in d for d in dumped)
    assert '"title"' not in text and "$ref" not in text and "$defs" not in text
    for d in dumped:
        assert "\n" not in d["description"] and "  " not in d["description"], d["name"]
    by_name = {d["name"]: d["inputSchema"]["properties"] for d in dumped}
    assert by_name["aim_read"]["mode"]["enum"] == ["full", "toc", "skeleton", "text", "chunks"]
    edit_ops = by_name["aim_edit"]["ops"]["items"]
    assert edit_ops["properties"]["action"]["enum"][-1] == "set_theme"
    assert edit_ops["required"] == ["action"]
    propose_ops = by_name["aim_propose"]["ops"]["items"]
    assert propose_ops["properties"]["action"]["enum"][-1] == "theme"
    # optional arguments are plain types, not anyOf [T, null]
    assert by_name["aim_edit"]["target"] == {"type": "string"}


def test_tool_surface_byte_budget():
    from aimformat.mcp import _INSTRUCTIONS

    tools = _list_tools().tools
    dumped = [t.model_dump(exclude_none=True, by_alias=True) for t in tools]
    compact = json.dumps(dumped, separators=(",", ":"), ensure_ascii=False)
    size = len(compact.encode()) + len(_INSTRUCTIONS.encode())
    assert size <= SURFACE_BYTE_BUDGET, size


def test_results_are_one_compact_text_block(tmp_path):
    doc = aim.new_document(title="Ünïcode")
    doc.add_chunk('<p data-aim="p1">Grüße — 你好</p>', author=BOT)
    path = tmp_path / "u.aim"
    doc.save(path)
    for tool, args in (
        ("aim_read", {"path": str(path)}),
        ("aim_lint", {"path": str(path)}),
        ("aim_read", {"path": str(path), "mode": "text"}),
        ("aim_search", {"path": str(path), "query": "grüße"}),
    ):
        result = _call(tool, args)
        assert not result.isError, result.content
        assert result.structuredContent is None
        assert len(result.content) == 1 and result.content[0].type == "text"
    text = _call("aim_read", {"path": str(path)}).content[0].text
    assert "\n" not in text and ", " not in text.split('"chunks"')[0]
    assert "Grüße — 你好" in text and "\\u" not in text


def _rich(tmp_path):
    doc = aim.new_document(title="Contract")
    with doc.batch():
        doc.add_chunk('<h1 data-aim="h1" class="num-1">Definitions</h1>', author=BOT)
        doc.add_chunk('<p data-aim="a1" class="num-2">Terms mean things.</p>', author=BOT)
        doc.add_chunk(
            '<p data-aim="a2" class="num-2">"<strong>Subprocessor</strong>" means a vendor.</p>',
            author=BOT,
        )
        doc.add_chunk('<p data-aim="plain">Unnumbered body text.</p>', author=BOT)
        doc.add_chunk('<h1 data-aim="h2" class="num-1">Audit</h1>', author=BOT)
        doc.add_chunk(
            '<ol data-aim-container="lst"><li data-aim="i1">First item</li>'
            '<li data-aim="i2">Second item</li></ol>',
            author=BOT,
        )
    path = tmp_path / "contract.aim"
    doc.save(path)
    return path


def _text(result) -> str:
    assert not result.isError, result.content
    return result.content[0].text


def test_read_modes(tmp_path):
    path = _rich(tmp_path)
    toc = _text(_call("aim_read", {"path": str(path), "mode": "toc"}))
    assert "[h1..plain] # 1. Definitions (4)" in toc
    assert '[a2..plain] ## 1.2 "Subprocessor" means a vendor. (2)' in toc
    assert "[h2..i2] # 2. Audit (4)" in toc
    skel = _text(_call("aim_read", {"path": str(path), "mode": "skeleton", "words": 2}))
    assert "[a1] p.num-2 1.1 Terms …" in skel
    assert "  [i1] li 1. First" in skel
    text = _text(_call("aim_read", {"path": str(path), "mode": "text"}))
    assert text.splitlines()[1].startswith("(text view: lossy")
    assert '[a2] 1.2 "**Subprocessor**" means a vendor.' in text
    chunks = _text(_call("aim_read", {"path": str(path), "mode": "chunks", "ids": ["a2"]}))
    lines = chunks.splitlines()
    assert lines[0] == "seq 6 | 0 pending"  # the short header (C5)
    assert lines[1] == "[a2] 1.2"
    assert lines[2].startswith('<p data-aim="a2" class="num-2">')


def test_read_full_keeps_its_keys(tmp_path):
    path = _rich(tmp_path)
    out = _payload(_call("aim_read", {"path": str(path)}))
    assert set(out) == {
        "title",
        "lang",
        "spec_version",
        "seq",
        "doc_hash",
        "summary",
        "toc",
        "toc_source",
        "chunks",
        "proposals",
    }
    assert out == _payload(_call("aim_read", {"path": str(path), "mode": "full"}))


def test_read_chunks_ranges_containers_cards_and_missing(tmp_path):
    path = _rich(tmp_path)
    _payload(
        _call(
            "aim_propose",
            {
                "path": str(path),
                "action": "add",
                "html": "<li>Third item</li>",
                "container": "lst",
                "after": "i2",
                "explanation": "Add a third.",
            },
        )
    )
    pid = aim.load(path).proposals[0].id
    out = _text(
        _call(
            "aim_read",
            {"path": str(path), "mode": "chunks", "ids": ["a1..plain", "lst", pid, "nope"]},
        )
    )
    lines = out.splitlines()
    assert lines[0] == "seq 6 | 1 pending"
    ids = [ln.split("]")[0][1:] for ln in lines[1:] if ln.startswith("[")]
    assert ids == ["a1", "a2", "plain", "lst", pid, "missing"]
    assert lines[-1] == "[missing] nope"
    # a container prints once, as its subtree; its items are not repeated
    assert sum("Second item" in ln for ln in lines) == 1
    # an item shows the pending card anchored on it
    item = _text(_call("aim_read", {"path": str(path), "mode": "chunks", "ids": ["i2"]}))
    assert f"[i2] in lst; pending {pid} add after this" in item


def test_read_mode_argument_errors(tmp_path):
    path = _rich(tmp_path)
    r = _call("aim_read", {"path": str(path), "mode": "text", "include_history": True})
    assert r.isError and "mode=full only" in r.content[0].text
    r = _call("aim_read", {"path": str(path), "mode": "chunks"})
    assert r.isError and "needs ids" in r.content[0].text
    r = _call("aim_read", {"path": str(path), "mode": "chunks", "ids": ["i2..a1"]})
    assert r.isError and "reversed range" in r.content[0].text
    r = _call("aim_read", {"path": str(path), "mode": "skeleton", "words": 51})
    assert r.isError and "words" in r.content[0].text


def test_search_returns_fetchable_ids(tmp_path):
    path = _rich(tmp_path)
    out = _text(_call("aim_search", {"path": str(path), "query": "subprocessors", "k": 3}))
    first = out.splitlines()[1]
    assert first.startswith("[a2] § 1. Definitions | ")
    got = _text(_call("aim_read", {"path": str(path), "mode": "chunks", "ids": ["a2"]}))
    assert "Subprocessor" in got
    none = _text(_call("aim_search", {"path": str(path), "query": "zebra"}))
    assert none.splitlines()[1] == 'no matches for "zebra"'


# --------------------------------------------------------------------------- ops (READS-D7)
def _ops(tool, path, ops, **extra):
    return _call(tool, {"path": str(path), "ops": ops, **extra})


def test_edit_batch_one_history_batch_with_backrefs(tmp_path):
    path = _make_doc(tmp_path)
    out = _payload(
        _ops(
            "aim_edit",
            path,
            [
                {"action": "add", "html": "<h2>New section</h2>", "after": "p2"},
                {"action": "add", "html": "<p>Under it.</p>", "after": "$0"},
                {"action": "modify", "target": "p1", "html": '<p data-aim="p1">Changed.</p>'},
            ],
            author="agent:test-model",
            explanation="Restructure.",
        )
    )
    assert out["ok"] and out["lint_errors"] == 0
    doc = aim.load(path)
    new_heading, new_para = out["results"][0]["id"], out["results"][1]["id"]
    assert [c.id for c in doc.chunks] == ["p1", "p2", new_heading, new_para]
    assert out["results"][2] == {"op": 2, "id": "p1", "target": "p1"}
    batches = {ev.get("batch") for ev in doc.history[-3:]}
    assert batches == {out["batch"]}
    assert all(ev.get("explanation") == "Restructure." for ev in doc.history[-3:])


def test_single_edit_add_returns_its_id(tmp_path):
    path = _make_doc(tmp_path)
    out = _payload(_call("aim_edit", {"path": str(path), "action": "add", "html": "<p>x</p>"}))
    assert set(out) == {"ok", "seq", "doc_hash", "lint_errors", "id"}
    assert aim.load(path).chunks[-1].id == out["id"]


def test_failing_op_leaves_file_byte_identical(tmp_path):
    path = _make_doc(tmp_path)
    before = path.read_bytes()
    r = _ops(
        "aim_edit",
        path,
        [
            {"action": "add", "html": "<p>a</p>"},
            {"action": "modify", "target": "p1", "html": '<p data-aim="p1">b</p>'},
            {"action": "add", "html": "<p>c</p>"},
            {"action": "delete", "target": "ghost"},
        ],
    )
    assert r.isError
    msg = r.content[0].text
    assert "ops[3] (delete ghost):" in msg and "nothing was written" in msg
    assert path.read_bytes() == before


def test_ops_argument_rules(tmp_path):
    path = _make_doc(tmp_path)
    before = path.read_bytes()
    r = _call("aim_edit", {"path": str(path), "action": "add", "html": "<p>x</p>", "ops": []})
    assert r.isError and "not both" in r.content[0].text
    r = _call("aim_edit", {"path": str(path)})
    assert r.isError and "pass action" in r.content[0].text
    r = _ops("aim_edit", path, [{"action": "add", "html": "<p>x</p>", "after": "$0"}])
    assert r.isError and "earlier op" in r.content[0].text
    r = _ops("aim_edit", path, [{"action": "add", "html": "<p>x</p>"}] * 101)
    assert r.isError and "at most 100" in r.content[0].text
    r = _ops(
        "aim_propose",
        path,
        [
            {"action": "modify", "target": "p1", "html": f'<p data-aim="p1">{i}</p>'}
            for i in range(26)
        ],
    )
    assert r.isError and "at most 25" in r.content[0].text
    assert path.read_bytes() == before


def test_propose_batch_backrefs_and_card_limits(tmp_path):
    path = _make_doc(tmp_path)
    out = _payload(
        _ops(
            "aim_propose",
            path,
            [
                {"action": "add", "html": "<p>One.</p>", "after": "p2"},
                {"action": "add", "html": "<p>Two.</p>", "after": "$0"},
                {"action": "modify", "target": "p1", "html": '<p data-aim="p1">Better.</p>'},
            ],
            explanation="Suggestions.",
        )
    )
    doc = aim.load(path)
    ids = [r["id"] for r in out["results"]]
    assert [p.id for p in doc.proposals] == ids
    assert {p.batch for p in doc.proposals} == {out["batch"]}
    assert doc.proposal(ids[1]).anchor_after == ids[0]
    assert "target" not in out["results"][0] and out["results"][2]["target"] == "p1"
    before = path.read_bytes()
    # a proposed add's $N is only an add anchor in the same container
    r = _ops(
        "aim_propose",
        path,
        [
            {"action": "add", "html": "<p>x</p>"},
            {"action": "modify", "target": "$0", "html": "<p>y</p>"},
        ],
    )
    assert r.isError and "only be the after of a later add" in r.content[0].text
    r = _ops(
        "aim_propose",
        path,
        [{"action": "add", "html": "<p>x</p>"}, {"action": "move", "target": "p1", "after": "$0"}],
    )
    assert r.isError and "only be the after of a later add" in r.content[0].text
    # two cards for one target in one call are refused
    r = _ops(
        "aim_propose",
        path,
        [
            {"action": "modify", "target": "p2", "html": '<p data-aim="p2">a</p>'},
            {"action": "delete", "target": "p2"},
        ],
    )
    assert r.isError and "one modify-or-delete and one move per target" in r.content[0].text
    assert path.read_bytes() == before


def test_propose_reports_superseded(tmp_path):
    path = _make_doc(tmp_path)
    first = _payload(
        _call(
            "aim_propose",
            {
                "path": str(path),
                "action": "modify",
                "target": "p1",
                "html": '<p data-aim="p1">A.</p>',
            },
        )
    )
    assert first["superseded"] == []
    second = _payload(
        _call(
            "aim_propose",
            {
                "path": str(path),
                "action": "modify",
                "target": "p1",
                "html": '<p data-aim="p1">B.</p>',
            },
        )
    )
    assert second["superseded"] == [first["proposal"]]


def test_paint_upgrade_joins_the_batch(tmp_path):
    from aimformat.registry import REGISTRY

    doc = aim.new_document(title="Old")
    doc.add_chunk('<p data-aim="p1">Text.</p>', author=BOT)
    text = doc.dumps().replace(
        f'data-aim-version="{REGISTRY.spec_version}"', 'data-aim-version="0.2"'
    )
    path = tmp_path / "old.aim"
    path.write_text(text)
    if aim.load(path).verify():
        pytest.skip("cannot fabricate a verifiable 0.2 document here")
    out = _payload(
        _ops(
            "aim_edit",
            path,
            [
                {"action": "add", "html": "<p>Plain.</p>"},
                {"action": "add", "html": '<p style="color:#ff0000">Red.</p>'},
            ],
        )
    )
    doc = aim.load(path)
    assert doc.spec_version != "0.2"
    upgrade = [ev for ev in doc.history if ev.target == "aim:version"]
    assert upgrade and upgrade[-1].get("batch") == out["batch"]


# --------------------------------------------------------------------------- elision (D12)
def _image_doc(tmp_path):
    uri = "data:image/png;base64," + "QUJD" * 100
    doc = aim.new_document(title="Image")
    doc.add_chunk(
        f'<figure data-aim="fig"><img alt="x" src="{uri}"><figcaption>cap</figcaption></figure>',
        author=BOT,
    )
    path = tmp_path / "img.aim"
    doc.save(path)
    return path, uri


def test_editing_an_image_chunk_keeps_the_image(tmp_path):
    path, uri = _image_doc(tmp_path)
    got = _text(_call("aim_read", {"path": str(path), "mode": "chunks", "ids": ["fig"]}))
    html = got.splitlines()[2]
    assert uri not in html and "[elided: " in html
    out = _payload(
        _call(
            "aim_edit",
            {
                "path": str(path),
                "action": "modify",
                "target": "fig",
                "html": html.replace(">cap<", ">new cap<"),
            },
        )
    )
    assert out["ok"]
    live = aim.load(path).chunk("fig").html
    assert uri in live and "new cap" in live and "elided" not in live


def test_unknown_stub_is_refused(tmp_path):
    path, _uri = _image_doc(tmp_path)
    before = path.read_bytes()
    r = _call(
        "aim_edit",
        {
            "path": str(path),
            "action": "modify",
            "target": "fig",
            "html": '<figure data-aim="fig"><img alt="x" '
            'src="[elided: 1KB, sha256:0000000000000000]"></figure>',
        },
    )
    assert r.isError and "matches nothing in this document" in r.content[0].text
    assert path.read_bytes() == before


def test_stub_from_history_restores_deleted_content(tmp_path):
    """Re-adding deleted content: the only place its data URI survives is the
    history, which aim_read(include_history=True) elides the same way."""
    path, uri = _image_doc(tmp_path)
    doc = aim.load(path)
    doc.delete_chunk("fig", author=BOT)
    doc.save(path)
    full = _payload(_call("aim_read", {"path": str(path), "include_history": True}))
    stubbed = [
        v
        for ev in full["history"]
        for v in ev.values()
        if isinstance(v, str) and "[elided: " in v and v.startswith("<figure")
    ]
    assert stubbed and uri not in stubbed[0]
    html = stubbed[0].replace(' data-aim="fig"', "")
    out = _payload(_call("aim_edit", {"path": str(path), "action": "add", "html": html}))
    assert out["ok"]
    live = aim.load(path).chunk(out["id"]).html
    assert uri in live and "elided" not in live


def test_accept_with_tweaks_restores_stubs_from_a_read(tmp_path):
    """Substitute review round 1: aim_resolve(applied=) is a write path too,
    so a payload copied from a read (stubs and all) restores like aim_edit's."""
    path, uri = _image_doc(tmp_path)
    doc = aim.load(path)
    card = doc.propose_replace_text("fig", "cap", "caption", author=BOT)
    doc.save(path)
    got = _text(_call("aim_read", {"path": str(path), "mode": "chunks", "ids": [card.id]}))
    stubbed = next(line for line in got.splitlines() if line.startswith("<figure"))
    assert uri not in stubbed and "[elided: " in stubbed
    tweaked = stubbed.replace(">caption<", ">final caption<")
    out = _payload(
        _call(
            "aim_resolve",
            {
                "path": str(path),
                "decision": "accept",
                "proposal_ids": [card.id],
                "applied": tweaked,
            },
        )
    )
    assert out["ok"]
    live = aim.load(path).chunk("fig").html
    assert uri in live and "final caption" in live and "elided" not in live
    # an unknown stub is refused before anything is written
    doc = aim.load(path)
    card = doc.propose_replace_text("fig", "final", "last", author=BOT)
    doc.save(path)
    before = path.read_bytes()
    r = _call(
        "aim_resolve",
        {
            "path": str(path),
            "decision": "accept",
            "proposal_ids": [card.id],
            "applied": '<figure data-aim="fig"><img alt="x" '
            'src="[elided: 1KB, sha256:0000000000000000]"></figure>',
        },
    )
    assert r.isError and "matches nothing in this document" in r.content[0].text
    assert path.read_bytes() == before


def test_edit_batch_fills_a_container_it_created(tmp_path):
    path = _make_doc(tmp_path)
    out = _payload(
        _ops(
            "aim_edit",
            path,
            [
                {"action": "add", "html": '<ul data-aim-container="steps"><li>One</li></ul>'},
                {"action": "add", "html": "<li>Two</li>", "container": "$0"},
            ],
        )
    )
    doc = aim.load(path)
    cont = out["results"][0]["id"]
    assert doc.chunk(out["results"][1]["id"]).container == cont
    r = _call(
        "aim_edit",
        {"path": str(path), "target": "p1", "ops": [{"action": "delete", "target": "p2"}]},
    )
    assert r.isError and "put target inside each op" in r.content[0].text


def test_chosen_id_reuse_and_remint_are_refused(tmp_path):
    path = _make_doc(tmp_path)
    before = path.read_bytes()
    r = _ops(
        "aim_edit",
        path,
        [
            {"action": "add", "html": '<p data-aim="fresh">a</p>'},
            {"action": "add", "html": '<p data-aim="fresh">b</p>'},
        ],
    )
    assert r.isError and "already created by an earlier op" in r.content[0].text
    # p1 is taken, so ops[0] gets a fresh id — a later literal "p1" would
    # silently point at the old chunk
    r = _ops(
        "aim_edit",
        path,
        [
            {"action": "add", "html": '<p data-aim="p1">dup</p>'},
            {"action": "add", "html": "<p>next</p>", "after": "p1"},
        ],
    )
    msg = r.content[0].text
    # p1 is also a live chunk, so the literal reads either way: refuse, and
    # name both ways out
    assert r.isError and "is ambiguous" in msg and "$0" in msg and "drop data-aim" in msg
    assert path.read_bytes() == before


def test_reminted_burned_id_points_at_the_back_reference(tmp_path):
    path = _make_doc(tmp_path)
    doc = aim.load(path)
    doc.delete_chunk("p2", author=BOT)  # p2 is burned from now on
    doc.save(path)
    before = path.read_bytes()
    r = _ops(
        "aim_edit",
        path,
        [
            {"action": "add", "html": '<p data-aim="p2">again</p>'},
            {"action": "add", "html": "<p>next</p>", "after": "p2"},
        ],
    )
    msg = r.content[0].text
    assert r.isError and "refer to it as $0" in msg and "ambiguous" not in msg
    assert path.read_bytes() == before


def test_top_level_container_with_ops_is_refused(tmp_path):
    """A top-level container next to ops used to be dropped silently, so
    the adds landed in body instead of the container the caller named."""
    path = _make_doc(tmp_path)
    before = path.read_bytes()
    r = _call(
        "aim_edit",
        {
            "path": str(path),
            "container": "lst",
            "ops": [{"action": "add", "html": "<li>c</li>"}],
        },
    )
    assert r.isError and "put container inside each op" in r.content[0].text
    assert path.read_bytes() == before


def test_words_outside_skeleton_is_refused(tmp_path):
    path = _make_doc(tmp_path)
    r = _call("aim_read", {"path": str(path), "mode": "text", "words": 3})
    assert r.isError and "words works with mode=skeleton only" in r.content[0].text
    out = _call("aim_read", {"path": str(path), "mode": "skeleton", "words": 1})
    assert not out.isError and "Original …" in out.content[0].text


def test_non_object_op_error_names_the_op_cleanly(tmp_path):
    from aimformat._ops import OpError, apply_ops

    doc = aim.load(_make_doc(tmp_path))
    with pytest.raises(OpError) as exc:
        apply_ops(doc, [1], kind="edit", author=BOT)  # type: ignore[list-item]
    assert str(exc.value).startswith("aim: ops[0]: each op must be an object")


def test_misspelt_op_field_is_refused(tmp_path):
    path = _make_doc(tmp_path)
    before = path.read_bytes()
    r = _ops("aim_edit", path, [{"action": "add", "html": "<p>x</p>", "anchor": "p1"}])
    assert r.isError and "anchor" in r.content[0].text
    assert path.read_bytes() == before


# ------------------------------------------------------------------ replace_text (READS-D13)
def _rich_para(tmp_path):
    doc = aim.new_document(title="Terms")
    doc.add_chunk(
        '<p data-aim="t1">Either party may terminate on <strong>written</strong> notice.</p>',
        author=BOT,
    )
    path = tmp_path / "terms.aim"
    doc.save(path)
    return path


def test_edit_replace_text_keeps_markup_and_id(tmp_path):
    path = _rich_para(tmp_path)
    args = {"path": str(path), "action": "replace_text", "target": "t1"}
    args |= {"old_text": "on written", "new_text": "on 30 days' written"}
    out = _payload(_call("aim_edit", args))
    assert out["ok"] and out["id"] == "t1"
    html = aim.load(path).chunk("t1").html
    assert html == (
        '<p data-aim="t1">Either party may terminate on 30 days\' '
        "<strong>written</strong> notice.</p>"
    )


def test_propose_replace_text_and_batch_ops(tmp_path):
    path = _rich_para(tmp_path)
    out = _payload(
        _call(
            "aim_propose",
            {
                "path": str(path),
                "action": "replace_text",
                "target": "t1",
                "old_text": "written",
                "new_text": "prior written",
                "explanation": "Stricter notice.",
            },
        )
    )
    card = aim.load(path).proposal(out["proposal"])
    # an insertion at a markup boundary joins the run before it
    assert card.action == "modify" and "on prior <strong>written</strong>" in card.payload_html
    edit = _ops(
        "aim_edit",
        path,
        [
            {"action": "replace_text", "target": "t1", "old_text": "Either", "new_text": "Each"},
            {
                "action": "replace_text",
                "target": "t1",
                "old_text": "Each party",
                "new_text": "A party",
            },
        ],
    )
    assert _payload(edit)["ok"]
    assert aim.load(path).chunk("t1").text.startswith("A party may terminate")


def test_replace_text_refusals_write_nothing(tmp_path):
    path = _rich_para(tmp_path)
    before = path.read_bytes()
    base = {"path": str(path), "action": "replace_text", "target": "t1"}
    r = _call("aim_edit", {**base, "old_text": "on written", "new_text": "by email"})
    assert r.isError and "markup" in r.content[0].text  # crosses <strong>
    r = _call("aim_edit", {**base, "old_text": "nothing like this", "new_text": "x"})
    assert r.isError and "not found" in r.content[0].text
    r = _call("aim_edit", {**base, "old_text": "on", "new_text": "x", "html": "<p>x</p>"})
    assert r.isError and "html" in r.content[0].text
    r = _call("aim_edit", {**base, "old_text": "Either"})
    assert r.isError and "new_text" in r.content[0].text
    r = _call("aim_edit", {"path": str(path), "action": "modify", "target": "t1", "old_text": "x"})
    assert r.isError
    assert path.read_bytes() == before
