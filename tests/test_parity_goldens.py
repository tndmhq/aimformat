"""The cross-implementation parity contract stays honest on the Python side.

The goldens under ``tests/parity/goldens/`` pin the Python SDK's projection
of each parity source; the TypeScript reader (``ts/``) asserts equality
against them. Here: regenerating every golden is byte-stable (any Python
behavior change must show up as a reviewed golden diff, never silent drift),
every fixture lints clean, and the corpus/golden sets stay in sync.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import aimformat as aim

ROOT = Path(__file__).parent.parent
FIXTURE_DIR = ROOT / "tests" / "parity" / "fixtures"
GOLDEN_DIR = ROOT / "tests" / "parity" / "goldens"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dump = _load_script("dump_projection")
SOURCES = dump.sources()


def test_corpus_covers_examples_and_fixtures():
    names = {p.stem for p in SOURCES}
    assert {"booklet", "deck", "proposal"} <= names, "shipped examples missing"
    assert len([p for p in SOURCES if p.parent == FIXTURE_DIR]) >= 8, "edge fixtures missing"


@pytest.mark.parametrize("path", SOURCES, ids=[p.stem for p in SOURCES])
def test_fixture_lints_clean(path):
    errors = [f for f in aim.lint_text(path.read_text("utf-8")) if f.level == "error"]
    assert not errors, "\n".join(str(e) for e in errors)


@pytest.mark.parametrize("path", SOURCES, ids=[p.stem for p in SOURCES])
def test_golden_is_byte_stable(path):
    """Regenerating the golden must reproduce the committed bytes exactly."""
    golden = GOLDEN_DIR / f"{path.stem}.json"
    assert golden.exists(), f"missing golden for {path.stem} — run dump_projection.py"
    regenerated = dump.render(aim.load(path))
    assert regenerated == golden.read_text("utf-8"), (
        f"golden {golden.name} is stale — the Python projection changed; "
        "run scripts/dump_projection.py and review the diff"
    )


def test_no_orphan_goldens():
    expected = {f"{p.stem}.json" for p in SOURCES} | {"conformance-hashes.json"}
    actual = {p.name for p in GOLDEN_DIR.glob("*.json")}
    assert actual == expected, f"orphans: {actual - expected}, missing: {expected - actual}"


def test_conformance_hashes_are_byte_stable():
    golden = GOLDEN_DIR / "conformance-hashes.json"
    assert golden.exists(), "missing conformance-hashes.json — run dump_projection.py"
    assert dump.render_conformance_hashes() == golden.read_text("utf-8"), (
        "conformance-hashes.json is stale — run scripts/dump_projection.py"
    )
    assert len(json.loads(golden.read_text("utf-8"))) == len(dump.conformance_sources())


def test_projection_matches_public_read_surface():
    """Spot-check that the dump derives from the same views the SDK exposes."""
    doc = aim.load(ROOT / "examples" / "proposal.aim")
    proj = dump.projection(doc)
    assert proj["docHash"] == doc.doc_hash
    assert [c["id"] for c in proj["chunks"]] == [c.id for c in doc.chunks]
    assert proj["containers"] == doc.containers
    assert proj["bodyIds"] == doc.body_ids
    assert [p["id"] for p in proj["proposals"]] == [p.id for p in doc.proposals]
    # every full golden parses as JSON with the full field set
    fields = set(proj)
    for golden in GOLDEN_DIR.glob("*.json"):
        if golden.name == "conformance-hashes.json":
            continue
        assert set(json.loads(golden.read_text("utf-8"))) == fields
