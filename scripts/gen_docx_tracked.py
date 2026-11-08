#!/usr/bin/env python3
"""Write the tracked-change shapes as .docx files for a manual check.

The shapes and their declared outcomes live in
``tests/tracked_docx_kit.py``; the test suite builds them in memory. This
script writes each one, with its two goldens, to a directory so a person can
open it in Word or LibreOffice and confirm, once, that *Reject All* and
*Accept All* leave exactly the declared text (steps in
``tests/fixtures/docx-tracked/README.md``). The binaries are not committed:
python-docx stamps every package with a fresh timestamp.

Run from the repo root:  python3 scripts/gen_docx_tracked.py [OUT_DIR]
(default OUT_DIR: tests/fixtures/docx-tracked/out, gitignored)
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import tracked_docx_kit as kit  # noqa: E402


def main(argv: list[str]) -> int:
    out = pathlib.Path(argv[1]) if len(argv) > 1 else ROOT / "tests/fixtures/docx-tracked/out"
    out.mkdir(parents=True, exist_ok=True)
    for shape in kit.shapes():
        (out / f"{shape.name}.docx").write_bytes(shape.docx)
        (out / f"{shape.name}.reject.txt").write_text("\n".join(shape.reject) + "\n", "utf-8")
        (out / f"{shape.name}.accept.txt").write_text("\n".join(shape.accept) + "\n", "utf-8")
    print(f"wrote {len(kit.shapes())} shapes to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
