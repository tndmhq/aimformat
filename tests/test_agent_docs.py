"""The agent-facing docs describe the tool surface the server actually has.

The MCP server grew a seventh tool (aim_search) in 0.6.0; every place that
counted or listed the tools must say so, or agents reading the docs under-use
the surface (or look for a tool count that no longer matches)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SURFACES = [
    "README.md",
    "AGENTS.md",
    "docs/for-agents.md",
    "docs/knowledge/architecture.md",
    "skills/aimformat/SKILL.md",
    "skills/aimformat/references/sdk.md",
    "src/aimformat/mcp.py",
]


@pytest.mark.parametrize("rel", SURFACES)
def test_no_stale_tool_count(rel: str) -> None:
    text = (ROOT / rel).read_text("utf-8").lower()
    for stale in ("six", "seven", "eight", "nine"):
        assert f"{stale} tools" not in text and f"{stale} workflow tools" not in text
        assert f"{stale} typed tools" not in text


@pytest.mark.parametrize(
    "rel", ["docs/for-agents.md", "skills/aimformat/SKILL.md", "skills/aimformat/references/sdk.md"]
)
def test_lists_search_and_read_modes(rel: str) -> None:
    text = (ROOT / rel).read_text("utf-8")
    for needle in ("aim_search", "aim search", "--mode", "aim edit", "batch"):
        assert needle in text, needle
