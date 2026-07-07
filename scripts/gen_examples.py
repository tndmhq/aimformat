#!/usr/bin/env python3
"""Regenerate examples/ through the SDK (so they are canonical and verified
by construction). Run from the repo root: python3 scripts/gen_examples.py"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

import aimformat as aim  # noqa: E402

OUT = pathlib.Path(__file__).parent.parent / "examples"
BOT = aim.agent("claude-opus-4-8")
ME = aim.human("ada")


def t(i: int) -> str:
    return f"2026-07-07T14:{i // 60:02d}:{i % 60:02d}Z"


def proposal_doc() -> aim.AimDocument:
    """A worked prose document: history, resolutions, and a pending lane."""
    doc = aim.new_document(title="Q3 Services Proposal — Acme GmbH",
                           theme={"--aim-brand-1": "#1a73e8"})
    with doc.batch():
        doc.add_chunk('<h1 data-aim="ttl" class="font-bold text-3xl '
                      'text-brand-1">Q3 Services Proposal — Acme GmbH</h1>',
                      author=BOT, at=t(0),
                      explanation="Initial draft from the discovery notes.")
        doc.add_chunk('<p data-aim="intro" class="text-gray-700 text-lg">'
                      "We propose a three-year services engagement.</p>",
                      author=BOT, at=t(1))
        doc.add_chunk('<section data-aim="scope"><h2 class="text-2xl">Scope'
                      "</h2><p>Consolidate authoring, review, and delivery "
                      "onto one platform across all three business units.</p>"
                      "</section>", author=BOT, at=t(2))
        doc.add_chunk('<h2 data-aim="hd" class="text-2xl">Deliverables</h2>',
                      author=BOT, at=t(3))
        doc.add_chunk('<ul data-aim-container="dl">'
                      '<li data-aim="d1">Discovery workshop and audit</li>'
                      '<li data-aim="d2">Platform implementation…</li>'
                      '<li data-aim="d2">…with staged rollout support</li>'
                      "</ul>", author=BOT, at=t(4))
        doc.add_chunk('<table data-aim-container="pr"><thead>'
                      '<tr data-aim="r0"><th>Phase</th><th>Weeks</th>'
                      "<th>Fee</th></tr></thead><tbody>"
                      '<tr data-aim="r1"><td>Discovery</td><td>4</td>'
                      "<td>€48,000</td></tr>"
                      '<tr data-aim="r2"><td>Implementation</td><td>16</td>'
                      "<td>€310,000</td></tr></tbody></table>",
                      author=BOT, at=t(5))
    # a human tightens the intro through the pending lane
    p = doc.propose_modify("intro", '<p data-aim="intro" class="text-gray-700 '
                           'text-lg">Acme saves €2.1M over three years by '
                           "consolidating vendor tooling onto one platform."
                           "</p>", author=BOT,
                           explanation="Lead with the outcome.", at=t(10))
    doc.accept(p.id, decided_by=ME, at=t(11))
    rej = doc.propose_delete("d1", author=BOT,
                             explanation="Audit is table stakes; cut it.",
                             at=t(12))
    doc.reject(rej.id, decided_by=ME, at=t(13))
    doc.checkpoint("sent-to-client", at=t(14))
    # still pending when the file is opened:
    doc.propose_add('<tr data-aim="r3"><td>Rollout &amp; training</td>'
                    "<td>8</td><td>€95,000</td></tr>", author=BOT,
                    container="pr", after="r2",
                    explanation="Client asked for rollout as its own line.",
                    at=t(15))
    doc.propose_theme({"--aim-brand-1": "#0f766e"}, author=BOT,
                      explanation="Client brand refresh: teal primary.",
                      at=t(16))
    doc.set_summary("Three-year services proposal for Acme GmbH: €358k over "
                    "two phases (pending: rollout line item), projected "
                    "€2.1M savings. One pending pricing row and a theme swap "
                    "await review.", model="claude-opus-4-8")
    doc.generate_toc()
    return doc


def deck_doc() -> aim.AimDocument:
    """A slide deck: positioned chunks, z-index, a pending slide."""
    doc = aim.new_document(title="Pilot read-out",
                           theme={"--aim-brand-1": "#7c3aed"})
    doc.add_chunk('<aim-slide data-aim-container="s1" '
                  'style="width:1920px; height:1080px">'
                  '<div data-aim="band" class="bg-brand-1" '
                  'style="left:0px; top:880px; width:1920px; height:200px; '
                  'z-index:1"></div>'
                  '<h2 data-aim="t1" class="font-bold text-6xl" '
                  'style="left:120px; top:340px; width:1400px; z-index:2">'
                  "Documents that review themselves</h2>"
                  '<p data-aim="st1" class="text-2xl text-gray-600" '
                  'style="left:120px; top:520px; width:1200px; z-index:2">'
                  "Pilot read-out — July 2026</p></aim-slide>",
                  author=BOT, at=t(0), explanation="Deck scaffold.")
    doc.add_chunk('<aim-slide data-aim-container="s2" '
                  'style="width:1920px; height:1080px">'
                  '<h2 data-aim="t2" class="font-bold text-5xl" '
                  'style="left:120px; top:100px; width:1000px">Next steps'
                  "</h2>"
                  '<p data-aim="b2" class="text-3xl" '
                  'style="left:120px; top:300px; width:1400px">Extend the '
                  "pilot to the briefs team for Q4.</p></aim-slide>",
                  author=BOT, at=t(1))
    doc.propose_modify("b2", '<p data-aim="b2" class="text-3xl" '
                       'style="left:120px; top:300px; width:1400px">Extend '
                       "the pilot to the briefs team for Q4; decision review "
                       "on 15 August.</p>", author=BOT,
                       explanation="Name the date.", at=t(5))
    doc.checkpoint("design-review", at=t(6))
    return doc


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, doc in (("proposal.aim", proposal_doc()),
                      ("deck.aim", deck_doc())):
        assert doc.verify() == [], name
        text = doc.dumps()
        errors = [f for f in aim.lint_text(text) if f.level == "error"]
        assert not errors, (name, [str(e) for e in errors])
        (OUT / name).write_text(text, "utf-8")
        print(f"wrote examples/{name} ({len(text) / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
