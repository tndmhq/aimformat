"""aimformat.views: units, numbering labels, list markers, outline, refs,
the lossy text view, skeleton, exact chunks and lexical search."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

import aimformat as aim
from aimformat import views
from aimformat.css import generate_aim_css
from conftest import BOT

ROOT = Path(__file__).resolve().parent.parent
GOLDENS = ROOT / "tests" / "goldens" / "views"
FIXTURES = sorted((ROOT / "examples").glob("*.aim")) + sorted(
    (ROOT / "tests" / "parity" / "fixtures").glob("*.aim")
)


def _doc(*blocks: str, title: str = "T") -> aim.AimDocument:
    doc = aim.new_document(title=title)
    with doc.batch():
        for b in blocks:
            doc.add_chunk(b, author=BOT)
    return doc


# --------------------------------------------------------------------------- units
def _conforming(path: Path) -> bool:
    return not [f for f in aim.lint_path(path) if f.level == "error"]


@pytest.mark.parametrize("path", [p for p in FIXTURES if _conforming(p)], ids=lambda p: p.name)
def test_units_agree_with_chunks_and_containers(path: Path) -> None:
    doc = aim.load(path)
    us = views.units(doc)
    chunks = doc.chunks
    assert [u.id for u in us if u.kind == "chunk"] == [c.id for c in chunks]
    assert [u.id for u in us if u.kind == "container"] == doc.containers
    by_id = {c.id: c for c in chunks}
    for u in us:
        if u.kind == "chunk":
            assert u.container == by_id[u.id].container
            assert u.tags == by_id[u.id].tags


def test_units_depth_runs_and_flags(rich_doc) -> None:
    us = {u.id: u for u in views.units(rich_doc)}
    assert us["list"].kind == "container" and us["list"].depth == 0
    assert us["li2"].depth == 1 and us["li2"].tags == ("li", "li")
    assert us["h1"].classes == ("font-bold", "text-3xl") and not us["h1"].styled
    assert us["row1"].text == "| alpha | 1 |"


# --------------------------------------------------------------------------- labels
def test_numbering_labels_follow_the_counters() -> None:
    doc = aim.load(GOLDENS / "numbering.aim")
    labels = views.numbering_labels(doc)
    assert labels["s1"] == "1."
    assert labels["s1a"] == "1.1"
    assert labels["s1a1"] == "1.1.1"
    assert labels["s1a1a"] == "1.1.1.1"
    assert "gap" not in labels
    assert labels["s1a2"] == "1.1.2"  # the unnumbered block changed nothing
    assert labels["s1b"] == "1.1"  # num-restart sets the level to 1
    assert labels["art"] == "Article 2"  # prefix + counter(k)
    assert labels["art1"] == "2.1"
    assert labels["sec"] == "Section 2"
    assert labels["sln"] == "3."  # counters are document-wide, slides included


def _css_rules() -> dict[str, str]:
    rules: dict[str, str] = {}
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", generate_aim_css()):
        rules[m.group(1).strip()] = m.group(2)
    return rules


@pytest.mark.parametrize("k", range(1, 10))
def test_label_formula_matches_the_stylesheet(k: int) -> None:
    """Read the num-k rules out of generate_aim_css() so the stylesheet and
    the simulated labels cannot drift apart."""
    rules = _css_rules()
    body = rules[f".num-{k}"]
    assert f"counter-increment:aim-c{k}" in body
    zeroed = re.search(r"counter-set:([^;]*)", body)
    deeper = [f"aim-c{j} 0" for j in range(k + 1, 10)]
    assert (zeroed.group(1).split(" aim") if zeroed else []) == (
        " ".join(deeper).split(" aim") if deeper else []
    )
    content = rules[f".num-{k}::before"]
    chain = re.findall(r"counter\(aim-c(\d)\)", content)
    assert chain == [str(j) for j in range(1, k + 1)]
    assert ('".\\a0"' in content) == (k == 1)  # only level 1 has the "." suffix
    prefixed = rules[f".num-{k}[data-aim-num-prefix]::before"]
    assert re.findall(r"counter\(aim-c(\d)\)", prefixed) == [str(k)]
    assert "attr(data-aim-num-prefix)" in prefixed
    restart = rules[f".num-{k}.num-restart"]
    assert "counter-increment:none" in restart and f"aim-c{k} 1" in restart
    # and the simulation agrees: build a chain down to level k twice
    blocks = [f'<p class="num-{j}">L{j}</p>' for j in range(1, k + 1)]
    blocks.append(f'<p class="num-{k}">again</p>')
    doc = _doc(*blocks)
    labels = list(views.numbering_labels(doc).values())
    expected = "1." if k == 1 else ".".join(["1"] * k)
    assert labels[k - 1] == expected
    assert labels[k] == ("2." if k == 1 else ".".join(["1"] * (k - 1) + ["2"]))


def test_list_markers() -> None:
    text = (GOLDENS / "numbering.text").read_text()
    for line in (
        "  [r1] iii. Third, roman",  # start=3 + lower-roman
        "  [r2] iv. Fourth, roman",
        "      - bullet under it",
        "  A) Alpha one",  # upper-alpha + paren
        "  1 Bare one",  # bare
        "  1 One",  # multilevel, three levels
        "    1.1 One-one",
        "      1.1.1 One-one-one",
        "  2 Two",
        "  - Plain bullet",
    ):
        assert line in text.splitlines(), line
    doc = _doc('<ol data-aim="x" class="list-lower-alpha"><li>a</li><li>b</li></ol>')
    assert "  b. b" in views.render_text(doc)
    doc = _doc('<ol data-aim="x" class="list-upper-roman"><li>a</li><li>b</li></ol>')
    assert "  II. b" in views.render_text(doc)


# --------------------------------------------------------------------------- outline
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_outline_heading_entries_match_generate_toc(path: Path) -> None:
    """generate_toc() stays untouched (it writes the cache the editor reads);
    outline()'s heading and slide entries start at exactly its titled ids."""
    doc = aim.load(path)
    tags = {u.id: u.tags[0] for u in views.units(doc) if u.depth == 0}
    entries = [e for e in views.outline(doc) if e.kind in ("heading", "slide")]
    toc = [
        t
        for t in aim.loads(doc.dumps()).generate_toc()
        if tags.get(t["chunks"][0]) in (*views.HEADINGS, "aim-slide")
    ]
    assert [e.first for e in entries] == [t["chunks"][0] for t in toc]
    assert [e.title for e in entries] == [" ".join(t["title"].split()) for t in toc]


def test_outline_nests_numbered_blocks_and_ranges_round_trip() -> None:
    doc = aim.load(GOLDENS / "numbering.aim")
    entries = views.outline(doc)
    kinds = [(e.kind, e.label, e.level) for e in entries]
    assert kinds[:4] == [
        ("heading", "1.", 1),
        ("numbered", "1.1", 2),
        ("numbered", "1.1", 2),
        ("heading", "Article 2", 1),
    ]
    first = entries[0]
    assert (first.first, first.last) == ("s1", "s1b")
    ids, missing = views.resolve_refs(doc, [f"{first.first}..{first.last}"])
    assert missing == [] and ids[0] == "s1" and ids[-1] == "s1b" and len(ids) == first.units


def test_outline_untitled_and_empty() -> None:
    assert views.outline(aim.new_document(title="E")) == []
    assert "(empty document)" in views.render_toc(aim.new_document(title="E"))
    doc = _doc('<p data-aim="a">x</p>', '<p data-aim="b">y</p>')
    (entry,) = views.outline(doc)
    assert (entry.kind, entry.first, entry.last, entry.units) == ("untitled", "a", "b", 2)


# --------------------------------------------------------------------------- refs
def test_resolve_refs(rich_doc) -> None:
    doc = rich_doc
    pid = doc.propose_modify("intro", '<p data-aim="intro">New.</p>', author=BOT).id
    ids, missing = views.resolve_refs(doc, ["row1", "list", pid, "aim:theme", "ghost"])
    assert ids == ["aim:theme", "list", "row1", pid]
    assert missing == ["ghost"]
    # inclusive range; a container wholly inside prints once as its subtree
    ids, _ = views.resolve_refs(doc, ["intro..li2"])
    assert ids == ["intro", "scope", "list"]
    # a range starting inside a container: covered items one by one
    ids, _ = views.resolve_refs(doc, ["row1..row2"])
    assert ids == ["row1", "row2"]
    ids, _ = views.resolve_refs(doc, ["li2..row1"])
    assert ids == ["li2", "row0", "row1"]
    with pytest.raises(ValueError, match="reversed range"):
        views.resolve_refs(doc, ["row2..intro"])
    with pytest.raises(ValueError, match="at most 200"):
        views.resolve_refs(doc, ["intro"] * 201)


def test_render_chunks_context_lines() -> None:
    doc = aim.load(ROOT / "examples" / "proposal.aim")
    out = views.render_chunks(doc, ["r2", "d2"])
    lines = out.splitlines()
    assert lines[0] == f"seq {doc.seq} | {len(doc.proposals)} pending"
    assert lines[1] == "[d2] in dl"
    assert lines[2] == doc.chunk("d2").html
    assert lines[3].startswith("[r2] in pr; pending p-") and lines[3].endswith("add after this")


# --------------------------------------------------------------------------- text view
def test_goldens_regenerate_byte_stable() -> None:
    pytest.importorskip("docx_parser_converter")
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import gen_view_goldens
    finally:
        sys.path.pop(0)
    for name, content in gen_view_goldens.build().items():
        assert (GOLDENS / name).read_text("utf-8") == content, name


@pytest.mark.parametrize("name", ["proposal", "deck", "booklet", "numbering", "legal-addendum"])
def test_text_goldens_are_lossy_views(name: str) -> None:
    text = (GOLDENS / f"{name}.text").read_text()
    assert text.splitlines()[1] == views.LOSSY_NOTICE
    assert not re.search(r"data:[\w.+-]+/", text)
    for leak in ("class=", "style=", "<p ", "<p>", "<li ", "<li>"):
        assert leak not in text, leak
    # the view never puts document text at a unit's own indentation
    for line in text.splitlines()[2:]:
        assert re.match(r"^( *)(\[[a-z0-9:_-]+\]|## pending|\(empty)", line) or line.startswith(
            "  "
        ), line


def test_text_view_targeted_rendering() -> None:
    text = (GOLDENS / "numbering.text").read_text().splitlines()
    assert "[marks] Keep ~~struck~~, **bold**, *em*, a link, line / break." in text
    assert "  | Merged {2 cols} |" in text and "  | Tall {2 rows} | Cell |" in text
    assert "[code] \\[not-an-id] a pre line" in text
    svg = (
        '<figure data-aim="f"><svg role="img" aria-label="Logo"><use href="#asset-0123456789ab"/>'
        "</svg><figcaption>The logo</figcaption></figure>"
    )
    doc = aim.loads(_doc().dumps())
    doc.add_chunk(svg, author=BOT)
    rendered = views.render_text(doc)
    assert "[f] [image: Logo]" in rendered and "  caption: The logo" in rendered


def test_text_view_elides_images_and_lists_pending() -> None:
    uri = "data:image/png;base64," + "A" * 400
    doc = _doc(f'<figure data-aim="f"><img alt="Chart" src="{uri}"></figure>')
    doc.propose_modify("f", '<figure data-aim="f"><img alt="New" src="x.png"></figure>', author=BOT)
    text = views.render_text(doc)
    assert "data:" not in text and "[f] [image: Chart]" in text
    assert "## pending (1)" in text and "→ [image: New]" in text


def test_skeleton() -> None:
    doc = aim.load(ROOT / "examples" / "proposal.aim")
    sk = views.render_skeleton(doc, words=3).splitlines()
    assert "[intro] p.text-gray-700.text-lg Acme saves €2.1M …" in sk
    assert "  [d2] li x2 - Platform implementation… …" in sk
    assert "[pr] table (3 units)" in sk
    deck = views.render_skeleton(aim.load(ROOT / "examples" / "deck.aim"), words=0)
    assert "  [t1] h2.font-bold.text-6xl{style}" in deck.splitlines()
    with pytest.raises(ValueError):
        views.render_skeleton(doc, words=51)


def test_elide_stub_form() -> None:
    uri = "data:image/png;base64," + "B" * 2000
    stub = views.elide(f'<img src="{uri}">')
    assert re.fullmatch(r'<img src="\[elided: 2KB, sha256:[0-9a-f]{16}\]">', stub)
    assert views.elide('<img src="data:image/png;base64,AAAA">') == (
        '<img src="data:image/png;base64,AAAA">'
    )


# --------------------------------------------------------------------------- search
@pytest.fixture
def legal() -> aim.AimDocument:
    return aim.loads(_legal_text())


def _legal_text() -> str:
    doc = _doc(
        '<h1 data-aim="h1" class="num-1">Definitions</h1>',
        '<p data-aim="d" class="num-2">In this Addendum:</p>',
        *[
            f'<p data-aim="t{i}" class="num-3">Term {i} means something about item {i}.</p>'
            for i in range(1, 8)
        ],
        '<p data-aim="t8" class="num-3">"EU Data Protection Laws" means the directive.</p>',
        '<p data-aim="t9" class="num-3">"Data Protection Laws" means EU Data Protection Laws.</p>',
        '<p data-aim="t10" class="num-3">"Subprocessor" means any vendor appointed.</p>',
        '<p data-aim="mix">Vendor audits vendor data.</p>',
        '<p data-aim="zh">数据保护法律适用于本附录。</p>',
    )
    return doc.dumps()


def test_search_dotted_label_first(legal) -> None:
    hits = views.search(legal, "1.1.8")
    assert hits[0].id == "t8"
    assert hits[0].section == "1. Definitions"


def test_search_more_terms_rank_higher(legal) -> None:
    hits = views.search(legal, "vendor data")
    both = {"mix"}
    assert hits[0].id in both
    some = [h.id for h in hits if h.id not in both]
    assert some  # chunks with only one of the terms still appear, below


def test_search_phrase_filters(legal) -> None:
    hits = views.search(legal, '"EU Data Protection Laws"')
    assert {h.id for h in hits} == {"t8", "t9"}
    assert views.search(legal, '"protection directive"') == []


def test_search_plural_finds_singular(legal) -> None:
    assert views.search(legal, "subprocessors")[0].id == "t10"


def test_search_cjk(legal) -> None:
    assert views.search(legal, "保护法")[0].id == "zh"


def test_search_empty_and_caps(legal) -> None:
    assert views.search(legal, "") == []
    assert views.search(legal, " ?!. ") == []
    assert "no matches for" in views.render_search(legal, "?!")
    with pytest.raises(ValueError, match="512"):
        views.search(legal, "x" * 513)
    with pytest.raises(ValueError, match="32 terms"):
        views.search(legal, " ".join(f"w{i}" for i in range(33)))
    with pytest.raises(ValueError, match="k must"):
        views.search(legal, "term", k=0)
    with pytest.raises(ValueError, match="k must"):
        views.search(legal, "term", k=51)


def test_search_flags_pending_cards(legal) -> None:
    p = legal.propose_delete("t10", author=BOT)
    hit = views.search(legal, "subprocessor")[0]
    assert hit.pending == (p.id,)
    assert f"[pending {p.id}]" in views.render_search(legal, "subprocessor")


def test_labels_golden_is_current() -> None:
    labels = json.loads((GOLDENS / "labels.json").read_text())
    doc = aim.load(GOLDENS / "numbering.aim")
    assert labels["numbering"] == views.numbering_labels(doc)


def test_indented_document_text_cannot_mimic_a_unit_line() -> None:
    doc = _doc('<pre data-aim="code">ok\n  [fake] spoof</pre>')
    lines = views.render_text(doc).splitlines()
    assert "    \\[fake] spoof" in lines
    assert not any(line.lstrip().startswith("[fake]") for line in lines)
