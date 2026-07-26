"""The version a release publishes must match what its published docs claim.

Nothing gated this until 0.5.0 shipped with a CHANGELOG still headed
`0.4.0 — unreleased` and a README that told PyPI the spec was v0.3 — the whole
numbering minor went out undocumented, and the project page was a version and
a half stale. `test_spec.py::test_spec_declares_current_version` already ties
`__version__` to `SPEC_VERSION`; this ties the *documents that ship with the
release* to both.

Only version numbers are asserted, never the prose around them: a release
must not be able to claim the wrong version, but rewording a sentence must not
fail the suite.
"""

import re
from pathlib import Path

import aimformat as aim

ROOT = Path(__file__).parent.parent


def test_changelog_top_matches_package_version():
    """The first numbered heading is the release these notes describe, and it
    is what `gh release create` publishes — so it must equal `__version__`.

    Robust to both release styles: a `## Unreleased` heading (no number) is
    skipped to the last real release, which during a clean dev cycle *is*
    `__version__`; and a repo that bumps `__version__` early is forced to give
    its in-progress section a matching numbered heading, which is exactly the
    step 0.5.0 skipped.
    """
    text = (ROOT / "CHANGELOG.md").read_text("utf-8")
    # The version must be the WHOLE token, delimited by the heading's " — date"
    # or end of line — otherwise a suffixed heading (`## 0.6.0-rc.1`, or the
    # typo `## 0.6.0.1`) would prefix-match `0.6.0` and pass against a `0.6.0`
    # package while the changelog names a different release.
    m = re.search(r"^## (\d+\.\d+\.\d+)(?= —|$)", text, re.M)
    assert m, "CHANGELOG.md has no versioned heading of the form '## X.Y.Z — …'"
    assert m.group(1) == aim.__version__, (
        f"CHANGELOG.md top release is {m.group(1)}, package is {aim.__version__} — "
        "when you bump __version__, add or rename its CHANGELOG section to match"
    )


def test_shipped_docs_declare_the_spec_version():
    """README is the PyPI project page (`pyproject: readme = "README.md"`), so
    its version claims are public the moment a release lands; CONTRIBUTING makes
    the same claim to contributors. Three claims, all of which have drifted in
    practice: README's intro line, README's Status-and-roadmap "current draft"
    entry (which stopped at v0.3 while the code was v0.5), and CONTRIBUTING's.

    Each is located by a short *stable phrase* around the version number, and
    only the number is asserted. Rewording elsewhere is free; rewording one of
    these three anchor phrases means updating its pattern here — a deliberate
    trade of a little brittleness for catching the exact drift a release ships.
    """
    for name, pattern in (
        ("README.md", r"The spec is a v(\d+\.\d+) draft"),
        ("README.md", r"\*\*v(\d+\.\d+)\*\* \(the current draft\)"),
        ("CONTRIBUTING.md", r"carries the v(\d+\.\d+) draft"),
    ):
        text = (ROOT / name).read_text("utf-8")
        m = re.search(pattern, text)
        assert m, f"{name} lost its version-claim phrase (pattern {pattern!r})"
        assert m.group(1) == aim.SPEC_VERSION, (
            f"{name} claims spec v{m.group(1)} (via {pattern!r}), "
            f"SPEC_VERSION is {aim.SPEC_VERSION}"
        )
