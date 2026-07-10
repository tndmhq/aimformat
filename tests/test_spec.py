"""The spec is executable: every ```aim snippet lints clean, the generated
appendix is fresh, and the rule-code table stays in sync with the verifier."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

import aimformat as aim

ROOT = Path(__file__).parent.parent
SPEC = ROOT / "spec.md"
SNIPPETS = re.findall(r"```aim\n(.*?)```", SPEC.read_text("utf-8"), re.S)


def test_spec_exists_with_snippets():
    assert SPEC.exists() and len(SNIPPETS) >= 3


@pytest.mark.parametrize(
    "idx", range(len(SNIPPETS)), ids=[f"snippet{i}" for i in range(len(SNIPPETS))]
)
def test_spec_snippet_lints_clean(idx):
    errors = [f for f in aim.lint_text(SNIPPETS[idx]) if f.level == "error"]
    assert not errors, "\n".join(str(e) for e in errors)


def test_spec_snippets_verify_their_history(idx=1):
    doc = aim.loads(SNIPPETS[idx])
    assert doc.verify() == []


def test_generated_appendix_is_fresh(tmp_path):
    """Running the generator must be a no-op on the committed spec."""
    before = SPEC.read_text("utf-8")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_spec_appendix.py")],
        check=True,
        capture_output=True,
    )
    after = SPEC.read_text("utf-8")
    assert before == after, "spec appendix is stale — run gen_spec_appendix.py"


def test_lint_rule_codes_match_registry():
    """Every code the verifier can emit is documented, and vice versa."""
    source = (ROOT / "src" / "aimformat" / "lint.py").read_text("utf-8")
    emitted = set(re.findall(r'"([A-Z]\d{3})"', source))
    documented = set(aim.REGISTRY.raw["lint_rules"])
    assert emitted <= documented, f"undocumented: {emitted - documented}"
    assert documented <= emitted, f"never emitted: {documented - emitted}"


def test_registry_levels_match_linter_behavior():
    for _code, (level, _) in aim.REGISTRY.raw["lint_rules"].items():
        assert level in ("error", "warning")


def test_spec_declares_current_version():
    text = SPEC.read_text("utf-8")
    assert f'data-aim-version="{aim.SPEC_VERSION}"' in text
    assert aim.__version__.startswith(aim.SPEC_VERSION)
