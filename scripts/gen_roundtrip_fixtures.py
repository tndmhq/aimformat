"""Regenerate tests/fixtures/roundtrip/: DOCX files exported with round-trip
marks and then RE-SAVED BY LIBREOFFICE, so CI can test survival of the
convention through a real word processor without installing one.

Needs ``soffice`` on PATH (LibreOffice). Usage:
    python scripts/gen_roundtrip_fixtures.py

Writes:
    legal-base.aim        the base document (ids are minted here, so the
                          base must be committed alongside the DOCX files)
    legal-null.lo.docx    marked export, re-saved unchanged
    legal-ce.lo.docx      marked export + a colleague edit set, re-saved
    legal-ce.truth.json   the edit set's ground truth (unit ids per op)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from _docx_colleague import Colleague  # noqa: E402
from aimformat.convert import from_docx  # noqa: E402
from aimformat.export_docx import docx_bytes  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "roundtrip"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = from_docx(ROOT / "tests" / "fixtures" / "docxs" / "legal-addendum.docx")
    base.save(OUT / "legal-base.aim")
    body = [c.id for c in base.chunks if len(c.text) > 80 and c.container == "body"]
    c = Colleague(docx_bytes(base, roundtrip_marks=True))
    c.replace(body[2], " ", " really ")
    c.delete(body[8])
    c.insert_after(body[10], "The Processor shall notify the Company without undue delay.")
    c.move_after(body[14], body[18])
    c.split(body[20], "appointed by")
    c.merge(body[24], body[25])
    truth = {
        "modified": sorted([body[2], body[20], body[24]]),
        "deleted": sorted([body[8], body[25]]),
        "moved": [body[14]],
        "added": 2,
    }
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in", Path(tmp) / "out"
        src.mkdir()
        (src / "legal-null.docx").write_bytes(docx_bytes(base, roundtrip_marks=True))
        (src / "legal-ce.docx").write_bytes(c.save())
        profile = (Path(tmp) / "profile").as_uri()
        subprocess.run(
            [
                "soffice",
                f"-env:UserInstallation={profile}",
                "--headless",
                "--convert-to",
                "docx:MS Word 2007 XML",
                "--outdir",
                str(dst),
                str(src / "legal-null.docx"),
                str(src / "legal-ce.docx"),
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
        for name in ("legal-null", "legal-ce"):
            (OUT / f"{name}.lo.docx").write_bytes((dst / f"{name}.docx").read_bytes())
    (OUT / "legal-ce.truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
