"""Conformance fixtures: the committed ok_*/nok_* suite in tests/fixtures/.

Third-party implementations can run their verifier over the same directory:
ok_* files must produce no errors; nok_<CODE>_* files must produce <CODE>.
Regenerate with scripts/gen_fixtures.py.
"""

from pathlib import Path

import pytest

from aimformat.lint import lint_path
from aimformat.registry import REGISTRY

FIXTURES = Path(__file__).parent / "fixtures"
OK = sorted(FIXTURES.glob("ok_*.aim"))
NOK = sorted(FIXTURES.glob("nok_*.aim"))


def test_fixture_suite_is_present():
    # floors track the shipped kit: a fixture-losing refactor must fail,
    # not shrink quietly (the old >= 15 floor hid half the nok set)
    assert len(OK) >= 5 and len(NOK) >= 31


@pytest.mark.parametrize("path", OK, ids=lambda p: p.name)
def test_ok_fixture_lints_clean(path):
    errors = [f for f in lint_path(path) if f.level == "error"]
    assert not errors, "\n".join(str(e) for e in errors)


@pytest.mark.parametrize("path", NOK, ids=lambda p: p.name)
def test_nok_fixture_trips_its_rule(path):
    """The rule must fire at its registered level — an error rule that
    started emitting only warnings would otherwise still pass."""
    want = path.name.split("_")[1]
    level = REGISTRY.raw["lint_rules"][want][0]  # "error" | "warning"
    codes = {f.code for f in lint_path(path) if f.level == level}
    assert want in codes, f"expected {level}-level {want}, got {codes or 'no findings'}"
