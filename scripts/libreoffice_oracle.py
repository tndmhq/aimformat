#!/usr/bin/env python3
"""Check the tracked-change goldens against LibreOffice (an independent oracle).

``scripts/gen_docx_tracked.py OUT`` writes every shape of
``tests/tracked_docx_kit.py`` as ``NAME.docx`` with its declared outcomes
``NAME.reject.txt`` / ``NAME.accept.txt``. This script opens each file in a
headless LibreOffice, runs *Reject All* and *Accept All*
(``.uno:RejectAllTrackedChanges`` / ``.uno:AcceptAllTrackedChanges``), saves
the result, and compares its paragraphs and table rows with the goldens.

Stdlib plus LibreOffice's own ``uno`` module only, so it runs under the
system Python that ships ``python3-uno`` (CI: the non-gating
``tracked-oracle`` job). Exit 1 when a golden disagrees with LibreOffice.

Two shapes are skipped on purpose: ``baked_numbering_insertion`` (Word and
LibreOffice draw list numbers that are not text, while the golden carries
the importer's baked labels) and ``textbox_revision`` (the synthetic
textbox's placeholder text exists only in the importer's output).

Run:  python3 scripts/libreoffice_oracle.py OUT_DIR
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import time
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SKIP = {"baked_numbering_insertion", "textbox_revision"}
COMMANDS = {"accept": ".uno:AcceptAllTrackedChanges", "reject": ".uno:RejectAllTrackedChanges"}


def blocks(path: pathlib.Path) -> list[str]:
    """Body paragraphs and table rows as the goldens spell them: a row is
    its cells' text run together; empty paragraphs are not blocks."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(f"{W}body")
    out: list[str] = []
    for child in list(body) if body is not None else []:
        if child.tag == f"{W}p":
            text = "".join(t.text or "" for t in child.iter(f"{W}t")).strip()
            if text:
                out.append(text)
        elif child.tag == f"{W}tbl":
            for row in child.iter(f"{W}tr"):
                cells = [
                    "".join(t.text or "" for t in cell.iter(f"{W}t")).strip()
                    for cell in row.iter(f"{W}tc")
                ]
                if any(cells):
                    out.append("".join(cells))
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    import uno  # type: ignore[import-not-found]
    from com.sun.star.beans import PropertyValue  # type: ignore[import-not-found]

    def prop(name: str, value: object) -> PropertyValue:
        p = PropertyValue()
        p.Name, p.Value = name, value
        return p

    folder = pathlib.Path(argv[1]).resolve()
    pipe = f"aimoracle{os.getpid()}"
    profile = tempfile.mkdtemp(prefix="lo-profile-")
    office = subprocess.Popen(
        [
            "soffice",
            "--headless",
            "--invisible",
            "--norestore",
            "--nodefault",
            f"-env:UserInstallation=file://{profile}",
            f"--accept=pipe,name={pipe};urp;StarOffice.ComponentContext",
        ]
    )
    try:
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", local
        )
        ctx = None
        for _ in range(60):
            try:
                ctx = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
                break
            except Exception:
                time.sleep(1)
        if ctx is None:
            print("could not connect to LibreOffice")
            return 2
        smgr = ctx.ServiceManager
        desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        dispatcher = smgr.createInstanceWithContext("com.sun.star.frame.DispatchHelper", ctx)
        failures = 0
        checked = 0
        for docx in sorted(folder.glob("*.docx")):
            name = docx.stem
            if name in SKIP or "." in name:
                continue
            for view, command in COMMANDS.items():
                golden = (folder / f"{name}.{view}.txt").read_text("utf-8").splitlines()
                golden = [line for line in golden if line.strip()]
                doc = desktop.loadComponentFromURL(
                    uno.systemPathToFileUrl(str(docx)), "_blank", 0, (prop("Hidden", True),)
                )
                frame = doc.getCurrentController().getFrame()
                dispatcher.executeDispatch(frame, command, "", 0, ())
                out = folder / f"{name}.lo-{view}.docx"
                doc.storeToURL(
                    uno.systemPathToFileUrl(str(out)), (prop("FilterName", "MS Word 2007 XML"),)
                )
                doc.close(True)
                got = blocks(out)
                checked += 1
                if got != golden:
                    failures += 1
                    print(f"DIFF {name} ({view}):\n  golden:      {golden}\n  libreoffice: {got}")
                else:
                    print(f"ok   {name} ({view})")
        print(f"{checked} resolutions checked, {failures} disagree with LibreOffice")
        return 1 if failures else 0
    finally:
        try:
            desktop.terminate()  # type: ignore[possibly-undefined]
        except Exception:
            pass
        office.terminate()
        try:
            office.wait(timeout=30)
        except subprocess.TimeoutExpired:
            office.kill()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
