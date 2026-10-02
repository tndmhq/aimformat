#!/usr/bin/env python3
"""Measure what a freshly imported .aim costs, section by section.

For every DOCX in ``tests/fixtures/docxs/`` this imports the file and prints
the raw file size (bytes, and o200k tokens when ``tiktoken`` is installed),
the body, the history block and the metadata cache. Run it before and after
a change to the importers or the history shape to record the effect; the
numbers are not asserted anywhere (ids are random, so counts move by about
0.1% between runs).

Run from the repo root:  python3 scripts/measure_import_sizes.py [--json]
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import warnings

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import aimformat as aim  # noqa: E402
from aimformat import canonical  # noqa: E402

try:  # optional: token counts as an agent's tokenizer sees them
    import tiktoken

    _ENC = tiktoken.get_encoding("o200k_base")

    def tokens(text: str) -> int | None:
        return len(_ENC.encode(text, disallowed_special=()))

except ImportError:  # pragma: no cover - optional dependency

    def tokens(text: str) -> int | None:
        return None


def _block(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.S)
    return match.group(0) if match else ""


def measure(path: pathlib.Path) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        doc = aim.from_docx(path)
    text = doc.dumps()
    body = "\n".join(canonical.serialize(el) for el in doc._state.constructs())
    history = _block(text, r'<script type="application/aim-history\+jsonl">.*?</script>')
    meta = _block(text, r'<script type="application/aim-meta\+json">.*?</script>')
    css = _block(text, r"<style data-aim-css=.*?</style>")
    row: dict = {"fixture": path.stem, "chunks": len(doc.chunks), "events": len(doc.history)}
    for name, part in (
        ("file", text),
        ("css", css),
        ("body", body),
        ("history", history),
        ("meta", meta),
    ):
        row[f"{name}_bytes"] = len(part.encode())
        row[f"{name}_tokens"] = tokens(part)
    return row


def main(argv: list[str]) -> int:
    rows = [measure(p) for p in sorted((ROOT / "tests/fixtures/docxs").glob("*.docx"))]
    if "--json" in argv:
        print(json.dumps(rows, indent=1))
        return 0
    cols = ["fixture", "chunks", "events", "file", "css", "body", "history", "meta"]
    print(" | ".join(cols))
    for row in rows:
        cells = [str(row["fixture"]), str(row["chunks"]), str(row["events"])]
        for name in cols[3:]:
            tok = row[f"{name}_tokens"]
            cells.append(f"{row[f'{name}_bytes']} B" + (f" / {tok} tok" if tok is not None else ""))
        print(" | ".join(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
