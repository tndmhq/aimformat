"""The committed examples are canonical, verified, and lint-clean."""

from pathlib import Path

import pytest

import aimformat as aim
from aimformat.lint import lint_path

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.aim"))


def test_examples_exist():
    assert len(EXAMPLES) >= 2


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_lints_clean(path):
    errors = [f for f in lint_path(path) if f.level == "error"]
    assert not errors, "\n".join(str(e) for e in errors)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_history_verifies(path):
    doc = aim.load(path)
    assert doc.verify() == []


def test_proposal_example_has_a_worked_lifecycle():
    doc = aim.load(next(p for p in EXAMPLES if p.name == "proposal.aim"))
    decisions = [e.decision for e in doc.history if e.kind == "resolution"]
    assert "accepted" in decisions and "rejected" in decisions
    assert doc.proposals, "example should ship with a pending lane"
    assert doc.meta and doc.meta["summary"]["doc_hash"] == doc.doc_hash
