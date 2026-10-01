#!/usr/bin/env python3
"""Regenerate the agent-view goldens in tests/goldens/views/.

- ``numbering.aim``: a small SDK-built fixture exercising outline numbering
  (§3.8: nesting, restart, prefix, an unnumbered block in between, slides,
  container items) and every list-marker class.
- ``<name>.text``: :func:`aimformat.views.render_text` for the three
  examples, the numbering fixture and one DOCX import (legal-addendum,
  imported with seeded ids so the golden is stable).
- ``legal-addendum.toc``: :func:`aimformat.views.render_toc` for the same.
- ``labels.json``: fixture -> chunk id -> rendered numbering label, so other
  implementations (e.g. a TypeScript port) can check their counters.

``tests/test_views.py`` regenerates everything in memory and asserts it is
byte-identical to the committed files. Run from the repo root:
``python3 scripts/gen_view_goldens.py`` (needs the ``[docx]`` extra).
"""

from __future__ import annotations

import json
import pathlib
import random
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import aimformat as aim  # noqa: E402
from aimformat import ids as _ids  # noqa: E402
from aimformat import views  # noqa: E402

OUT = ROOT / "tests" / "goldens" / "views"
BOT = aim.agent("claude-opus-4-8")
EXAMPLES = ("proposal", "deck", "booklet")
DOCX = ROOT / "tests" / "fixtures" / "docxs" / "legal-addendum.docx"


def t(i: int) -> str:
    return f"2026-07-07T15:{i // 60:02d}:{i % 60:02d}Z"


def numbering_doc() -> aim.AimDocument:
    doc = aim.new_document(title="Numbering fixture")
    blocks = [
        '<h1 data-aim="s1" class="num-1">Scope</h1>',
        '<p data-aim="s1a" class="num-2">Level two.</p>',
        '<p data-aim="s1a1" class="num-3">Level three.</p>',
        '<p data-aim="s1a1a" class="num-4">Level four.</p>',
        '<p data-aim="gap">Unnumbered text between numbered blocks changes nothing.</p>',
        '<p data-aim="s1a2" class="num-3">Back to level three.</p>',
        '<p data-aim="s1b" class="num-2 num-restart">Restarted level two.</p>',
        '<h1 data-aim="art" class="num-1" data-aim-num-prefix="Article ">Definitions</h1>',
        '<p data-aim="art1" class="num-2">Under the article.</p>',
        '<p data-aim="sec" class="num-2" data-aim-num-prefix="Section ">Prefixed.</p>',
        '<pre data-aim="code">[not-an-id] a pre line\nsecond line</pre>',
        '<p data-aim="marks">Keep <s>struck</s>, <strong>bold</strong>, <em>em</em>, '
        'a <a href="https://example.com">link</a>, line<br>break.</p>',
        '<table data-aim="grid"><tr><th colspan="2">Merged</th></tr>'
        '<tr><td rowspan="2">Tall</td><td>Cell</td></tr><tr><td>Below</td></tr></table>',
        '<ol data-aim-container="roman" class="list-lower-roman" start="3">'
        '<li data-aim="r1">Third, roman</li><li data-aim="r2">Fourth, roman'
        "<ul><li>bullet under it</li></ul></li></ol>",
        '<ol data-aim="alpha" class="list-paren list-upper-alpha">'
        "<li>Alpha one</li><li>Alpha two</li></ol>",
        '<ol data-aim="bare" class="list-bare"><li>Bare one</li></ol>',
        '<ol data-aim="multi" class="list-multilevel"><li>One'
        '<ol class="list-multilevel"><li>One-one'
        '<ol class="list-multilevel"><li>One-one-one</li></ol></li></ol></li>'
        "<li>Two</li></ol>",
        '<ul data-aim="bul"><li>Plain bullet</li></ul>',
        '<ol data-aim-container="items"><li data-aim="it1">'
        '<p class="num-2">Numbered paragraph inside an item.</p></li></ol>',
        '<aim-slide data-aim-container="sl" style="width:960px; height:540px">'
        '<h2 data-aim="slt" style="left:60px; top:50px; width:500px">Slide title</h2>'
        '<p data-aim="sln" class="num-1" style="left:60px; top:150px; width:700px">'
        "Numbered on a slide.</p></aim-slide>",
    ]
    with doc.batch():
        for i, markup in enumerate(blocks):
            doc.add_chunk(markup, author=BOT, at=t(i))
    return doc


def docx_doc() -> aim.AimDocument:
    """Import the DOCX fixture with seeded ids (random ids would churn)."""
    rng = random.Random(20261001)
    saved = _ids.secrets
    _ids.secrets = types.SimpleNamespace(choice=rng.choice)  # type: ignore[assignment]
    try:
        return aim.from_path(DOCX)
    finally:
        _ids.secrets = saved


def build() -> dict[str, str]:
    """name -> file content, for every golden."""
    out: dict[str, str] = {}
    numbering = numbering_doc()
    out["numbering.aim"] = numbering.dumps()
    docs = {"numbering": aim.loads(out["numbering.aim"])}
    for name in EXAMPLES:
        docs[name] = aim.load(ROOT / "examples" / f"{name}.aim")
    legal = docx_doc()
    docs["legal-addendum"] = legal
    for name, doc in docs.items():
        out[f"{name}.text"] = views.render_text(doc) + "\n"
    out["legal-addendum.toc"] = views.render_toc(legal) + "\n"
    labels = {name: views.numbering_labels(doc) for name, doc in docs.items()}
    out["labels.json"] = json.dumps(labels, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, content in build().items():
        (OUT / name).write_text(content, "utf-8")
        print(f"wrote {OUT.relative_to(ROOT) / name}")


if __name__ == "__main__":
    main()
