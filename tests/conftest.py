"""Shared fixtures: deterministic document builders used across the suite."""
from __future__ import annotations

import pytest

import aimformat as aim

BOT = aim.agent("claude-opus-4-8")
ME = aim.human("luca")
T = "2026-07-07T10:00:00Z"  # fixed timestamp for deterministic events


def ts(i: int) -> str:
    """Deterministic ascending timestamps."""
    return f"2026-07-07T10:{i // 60:02d}:{i % 60:02d}Z"


@pytest.fixture
def empty_doc() -> aim.AimDocument:
    return aim.new_document(title="Empty fixture")


@pytest.fixture
def basic_doc() -> aim.AimDocument:
    """Title + intro paragraph, deterministic ids."""
    doc = aim.new_document(title="Basic fixture",
                           theme={"--aim-brand-1": "#1a73e8"})
    doc.add_chunk('<h1 data-aim="h1" class="font-bold text-3xl">Title</h1>',
                  author=BOT, at=ts(0))
    doc.add_chunk('<p data-aim="intro">Intro paragraph.</p>',
                  author=BOT, at=ts(1))
    return doc


@pytest.fixture
def rich_doc() -> aim.AimDocument:
    """A document exercising most constructs: section chunk, list container
    with a run, table container, slide, checkpoint."""
    doc = aim.new_document(title="Rich fixture",
                           theme={"--aim-brand-1": "#1a73e8"})
    with doc.batch():
        doc.add_chunk('<h1 data-aim="h1" class="font-bold text-3xl">Report</h1>',
                      author=BOT, at=ts(0))
        doc.add_chunk('<p data-aim="intro">We looked at the numbers.</p>',
                      author=BOT, at=ts(1))
        doc.add_chunk('<section data-aim="scope"><h2>Scope</h2>'
                      "<p>Everything shipped in Q2.</p></section>",
                      author=BOT, at=ts(2))
        doc.add_chunk('<ul data-aim-container="list"><li data-aim="li1">First'
                      '</li><li data-aim="li2">Second, part one…</li>'
                      '<li data-aim="li2">…second, part two</li></ul>',
                      author=BOT, at=ts(3))
        doc.add_chunk('<table data-aim-container="tbl"><thead>'
                      '<tr data-aim="row0"><th>K</th><th>V</th></tr></thead>'
                      '<tbody><tr data-aim="row1"><td>alpha</td><td>1</td></tr>'
                      '<tr data-aim="row2"><td>beta</td><td>2</td></tr>'
                      "</tbody></table>",
                      author=BOT, at=ts(4))
        doc.add_chunk('<aim-slide data-aim-container="s1" '
                      'style="width:1920px; height:1080px">'
                      '<h2 data-aim="st" class="text-5xl" '
                      'style="left:120px; top:100px; width:1000px">Deck</h2>'
                      "</aim-slide>",
                      author=BOT, at=ts(5))
    doc.checkpoint("draft", at=ts(6))
    return doc


@pytest.fixture
def lifecycle_doc(rich_doc: aim.AimDocument) -> aim.AimDocument:
    """rich_doc plus a worked proposal lifecycle (accept/reject/tweak/undo)."""
    doc = rich_doc
    p1 = doc.propose_modify("intro", '<p data-aim="intro">We audited the Q2 '
                            "numbers end to end.</p>", author=BOT,
                            explanation="More precise.", at=ts(10))
    doc.accept(p1.id, decided_by=ME, at=ts(11))
    p2 = doc.propose_modify("li1", '<li data-aim="li1">First (reworded)</li>',
                            author=BOT, explanation="Reword.", at=ts(12))
    doc.reject(p2.id, decided_by=ME, at=ts(13))
    p3 = doc.propose_modify(
        "st", '<h2 data-aim="st" class="text-5xl" '
              'style="left:120px; top:100px; width:1000px">Deck, retitled</h2>',
        author=BOT, explanation="Better title.", at=ts(14))
    doc.accept(p3.id, decided_by=ME, at=ts(15),
               applied='<h2 data-aim="st" class="text-5xl" '
                       'style="left:120px; top:100px; width:1000px">'
                       "Deck — retitled</h2>")
    doc.modify_chunk("row1", '<tr data-aim="row1"><td>alpha</td><td>11</td></tr>',
                     author=ME, at=ts(16))
    doc.undo(author=ME, at=ts(17))
    doc.checkpoint("reviewed", at=ts(18))
    return doc
