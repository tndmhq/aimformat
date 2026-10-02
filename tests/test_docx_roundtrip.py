"""DOCX round trip with colleagues: chunk identity survives
.aim -> DOCX -> edited in Word -> DOCX -> .aim, so the returned file becomes
a revision of the SAME document and only the colleague's changes show.

Colleague edits are simulated on the XML the way Word leaves it (bookmarks
travel with the paragraph content they sit in) by ``_docx_colleague``; the
``fixtures/roundtrip`` files were re-saved by a real word processor
(LibreOffice, ``scripts/gen_roundtrip_fixtures.py``).
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from itertools import product
from pathlib import Path

import pytest

import aimformat as aim
from aimformat.errors import InvalidOperation
from aimformat.events import agent, human

pytest.importorskip("docx_parser_converter")
pytest.importorskip("docx")

from lxml import etree  # noqa: E402

from _docx_colleague import Colleague, q  # noqa: E402
from aimformat import docx_marks  # noqa: E402
from aimformat.cli import main as cli  # noqa: E402
from aimformat.convert import from_docx  # noqa: E402
from aimformat.export_docx import docx_bytes, to_docx  # noqa: E402

HERE = Path(__file__).parent
DOCXS = HERE / "fixtures" / "docxs"
RT = HERE / "fixtures" / "roundtrip"
EXAMPLES = HERE.parent / "examples"
A = agent("model-x")


@pytest.fixture(scope="module")
def legal() -> aim.AimDocument:
    return from_docx(DOCXS / "legal-addendum.docx")


def _copy(doc: aim.AimDocument) -> aim.AimDocument:
    return aim.AimDocument.loads(doc.dumps())


def _body_ids(doc: aim.AimDocument, *, min_len: int = 80) -> list[str]:
    return [c.id for c in doc.chunks if len(c.text) > min_len and c.container == "body"]


def _healthy(doc: aim.AimDocument) -> None:
    """Every import must leave a verifying document whose lane resolves."""
    assert doc.verify() == []
    accepted = _copy(doc)
    accepted.accept_all(decided_by=human("reviewer"))
    assert accepted.verify() == []
    rejected = _copy(doc)
    rejected.reject_all(decided_by=human("reviewer"))
    assert rejected.verify() == []


def _colleague_edit_set(c: Colleague, ids: list[str]) -> dict:
    """Word edits, a delete, an insert, a move, a split and a merge."""
    c.replace(ids[2], " ", " really ")
    c.delete(ids[8])
    c.insert_after(ids[10], "The Processor shall notify the Company without undue delay.")
    c.move_after(ids[14], ids[18])
    c.split(ids[20], "appointed by")
    c.merge(ids[24], ids[25])
    return {
        "modified": sorted([ids[2], ids[20], ids[24]]),
        "deleted": sorted([ids[8], ids[25]]),
        "moved": [ids[14]],
        "added": 2,
    }


def _ops(report) -> dict:
    return {
        "modified": sorted(report.modified),
        "deleted": sorted(report.deleted),
        "moved": sorted(report.moved),
        "added": len(report.added),
    }


# =============================================================================
# the gap


def test_plain_export_loses_every_id(legal, tmp_path):
    """What this feature closes: without marks no id survives the trip."""
    back = from_docx(to_docx(legal, tmp_path / "plain.docx"))
    assert not {c.id for c in legal.chunks} & {c.id for c in back.chunks}
    assert len(back.chunks) == len(legal.chunks)


def test_marks_are_off_by_default(legal):
    with zipfile.ZipFile(io.BytesIO(docx_bytes(legal))) as zf:
        xml = zf.read("word/document.xml")
        assert b"_aim" not in xml
        assert not any(docx_marks.NS.encode() in zf.read(n) for n in zf.namelist())


# =============================================================================
# the convention


@pytest.mark.parametrize(
    ("uid", "ordinal", "name"),
    [
        ("abc123", 1, "_aim_abc123"),
        ("abc123", 2, "_aim2_abc123"),
        ("a_b", 12, "_aim12_a_b"),
        ("x" * 32, 99, "_aim99_" + "x" * 32),
    ],
)
def test_bookmark_names(uid, ordinal, name):
    assert docx_marks.bookmark_name(uid, ordinal) == name
    assert len(name) <= 40  # Word's limit
    marker = docx_marks.parse_bookmark_name(name)
    assert marker is not None
    assert (marker.key, marker.ordinal, marker.hashed) == (uid, ordinal, False)


@pytest.mark.parametrize("uid", ["Has-Upper", "with-dash", "x" * 33, "ünïcode"])
def test_unsafe_ids_are_hashed_and_resolve(uid):
    name = docx_marks.bookmark_name(uid, 3)
    assert name.startswith("_aimh3_") and len(name) <= 40
    assert re.fullmatch(r"[A-Za-z0-9_]+", name)
    marker = docx_marks.parse_bookmark_name(name)
    assert marker is not None and marker.hashed
    assert docx_marks.resolve_marker(marker, docx_marks.hash_index([uid, "other"])) == uid
    assert docx_marks.resolve_marker(marker, docx_marks.hash_index(["other"])) is None


@pytest.mark.parametrize(
    "name",
    [
        None,
        "",
        "_GoBack",
        "_Toc123",
        "_aim1_x",  # ordinal 1 is never spelled
        "_aim0_x",
        "_aim_x Copy 1",  # LibreOffice's duplicate naming
        "_aimh_nothex12345",
        "aim_x",
    ],
)
def test_non_markers_are_ignored(name):
    assert docx_marks.parse_bookmark_name(name) is None


def test_manifest_round_trip_and_untrusted_input():
    m = docx_marks.Manifest(
        exporter="aimformat test",
        base_doc_hash="sha256:" + "a" * 64,
        base_seq=7,
        pending="tracked",
        salt="00" * 16,
        exported_at="2026-10-01T00:00:00Z",
        units=[docx_marks.ManifestUnit("u1", "body", None, "f" * 16)],
        cards=["p-abc"],
    )
    back = docx_marks.parse_manifest(docx_marks.manifest_xml(m))
    assert back == m
    assert b"<aim:u" in docx_marks.manifest_xml(m)
    # no document text, ever: only ids and hashes
    assert set(re.findall(rb'([\w:-]+)="', docx_marks.manifest_xml(m))) <= {
        b"xmlns:aim",
        b"exporter",
        b"base-doc-hash",
        b"base-seq",
        b"pending",
        b"salt",
        b"exported-at",
        b"id",
        b"scope",
        b"shell",
        b"x",
        b"version",
        b"encoding",
        b"standalone",
    }
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        b'<aim:roundtrip xmlns:aim="urn:aimformat:docx-roundtrip:1" base-seq="1" '
        b'exporter="&b;"/>'
    )
    parsed = docx_marks.parse_manifest(bomb)
    assert parsed is None or "aaaa" not in parsed.exporter
    big = docx_marks.manifest_xml(m) + b" " * docx_marks.MANIFEST_MAX_BYTES
    assert docx_marks.parse_manifest(big) is None
    other = docx_marks.manifest_xml(m).replace(b"docx-roundtrip:1", b"docx-roundtrip:2")
    assert docx_marks.parse_manifest(other) is None


def test_export_marks_every_unit_once(legal):
    from aimformat.reconcile import _units

    data = docx_bytes(legal, roundtrip_marks=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml").decode()
        manifest = next(
            docx_marks.parse_manifest(zf.read(n))
            for n in zf.namelist()
            if re.fullmatch(r"customXml/item\d+\.xml", n) and docx_marks.parse_manifest(zf.read(n))
        )
    names = re.findall(r'w:name="(_aim[^"]*)"', xml)
    firsts = [n for n in names if n.startswith("_aim_")]
    units = _units(legal._state)
    assert sorted(firsts) == sorted(f"_aim_{u}" for u in units)
    assert manifest is not None
    assert manifest.base_doc_hash == legal.doc_hash and manifest.base_seq == legal.seq
    assert [u.id for u in manifest.units] == list(units)
    # collapsed (point) bookmarks: every start is immediately closed
    starts = re.findall(
        r'<w:bookmarkStart w:id="(\d+)" w:name="_aim[^"]*"/><w:bookmarkEnd w:id="(\d+)"/>', xml
    )
    assert len(starts) == len(names) and all(a == b for a, b in starts)


def test_multi_paragraph_units_carry_ordinals():
    d = aim.new_document(title="Ordinals")
    with d.batch():
        d.add_chunk("<section><h2>Scope</h2><p>One.</p><p>Two.</p></section>", author=A)
        d.add_chunk(
            '<ul data-aim-container=""><li data-aim="">A</li><li data-aim="">B</li></ul>', author=A
        )
    sec = d.chunks[0].id
    lst = d.containers[0]
    with zipfile.ZipFile(io.BytesIO(docx_bytes(d, roundtrip_marks=True))) as zf:
        xml = zf.read("word/document.xml").decode()
    names = re.findall(r'w:name="(_aim[^"]*)"', xml)
    assert names[:3] == [f"_aim_{sec}", f"_aim2_{sec}", f"_aim3_{sec}"]
    assert f"_aim_{lst}" in names  # the container, on its first paragraph


# =============================================================================
# null round trips: export then import onto the same document = no changes


def _null_cases():
    for name in ("deck", "booklet", "proposal"):
        for mode in ("tracked", "accept-all", "reject-all"):
            yield pytest.param(EXAMPLES / f"{name}.aim", mode, id=f"{name}-{mode}")
    for path in sorted(DOCXS.glob("*.docx")):
        yield pytest.param(path, "tracked", id=path.stem)


@pytest.mark.parametrize(("source", "mode"), list(_null_cases()))
@pytest.mark.parametrize("changes", ["proposals", "edits"])
def test_null_round_trip_is_zero_changes(source, mode, changes):
    doc = aim.AimDocument.load(source) if source.suffix == ".aim" else from_docx(source)
    before = doc.dumps()
    report = doc.import_revision(
        docx_bytes(doc, pending=mode, roundtrip_marks=True), changes=changes
    )
    assert report.aligned_by_marker == report.units > 0
    assert not report.proposals and not report.events and not report.conflicts
    assert not (report.added or report.deleted or report.modified or report.moved)
    assert doc.dumps() == before


def test_null_round_trip_with_a_pending_lane(legal):
    doc = _copy(legal)
    ids = _body_ids(doc)
    doc.propose_modify(ids[4], doc.chunk(ids[4]).html.replace("</p>", " (agent)</p>"), author=A)
    doc.propose_add("<p>Agent proposed paragraph.</p>", author=A, after=ids[6])
    doc.propose_delete(ids[9], author=A)
    before = doc.dumps()
    for mode in ("tracked", "accept-all", "reject-all"):
        report = doc.import_revision(docx_bytes(doc, pending=mode, roundtrip_marks=True))
        assert not report.proposals and not report.conflicts, mode
        assert doc.dumps() == before


def test_libreoffice_resave_keeps_identity():
    base = aim.AimDocument.load(RT / "legal-base.aim")
    before = base.dumps()
    report = base.import_revision(RT / "legal-null.lo.docx")
    assert report.aligned_by_marker == report.units
    assert not report.proposals and base.dumps() == before

    truth = json.loads((RT / "legal-ce.truth.json").read_text())
    doc = aim.AimDocument.load(RT / "legal-base.aim")
    report = doc.import_revision(RT / "legal-ce.lo.docx")
    assert _ops(report) == truth
    _healthy(doc)


# =============================================================================
# colleague edits


def test_colleague_edits_land_as_proposals_on_the_same_ids(legal):
    doc = _copy(legal)
    ids = _body_ids(doc)
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    truth = _colleague_edit_set(c, ids)
    report = doc.import_revision(c.save(author="Rosa Lind", modified="2999-01-01T00:00:00Z"))
    assert _ops(report) == truth
    assert report.conflicts == [] and report.warnings == []
    assert report.author == human("docx:Rosa Lind")
    assert len(report.proposals) == len(doc.proposals) == 8
    for card in doc.proposals:
        assert card.author == human("docx:Rosa Lind")
        assert card.explanation and card.explanation.startswith("Rosa Lind in Word")
        assert len(card.explanation) <= 120
        assert card.at <= aim.document._now_iso()  # a future timestamp is clamped
    # only the colleague's words changed; base markup (classes) survived
    modified = doc.proposal(report.proposals[0])
    assert "really" in (modified.payload_html or "")
    assert 'class="text-justify"' in (modified.payload_html or "")
    # the body itself is untouched until someone reviews
    assert [c.id for c in doc.chunks] == [c.id for c in legal.chunks]
    _healthy(doc)
    # accepting everything gives the colleague's text, in their order
    accepted = _copy(doc)
    accepted.accept_all(decided_by=human("reviewer"))
    text = " ".join(ch.text for ch in accepted.chunks)
    assert "really" in text and "notify the Company without undue delay" in text


def test_reimport_is_idempotent(legal):
    doc = _copy(legal)
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    _colleague_edit_set(c, _body_ids(doc))
    data = c.save()
    first = doc.import_revision(data)
    snapshot = doc.dumps()
    second = doc.import_revision(data)
    assert second.proposals == [] and second.conflicts == []
    assert sorted(second.already_pending) == sorted(first.proposals)
    assert doc.dumps() == snapshot


def test_edits_mode_writes_reconcile_events_and_refuses_a_repeat(legal):
    doc = _copy(legal)
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    truth = _colleague_edit_set(c, _body_ids(doc))
    data = c.save()
    report = doc.import_revision(data, changes="edits")
    assert _ops(report) == truth
    assert report.events and not doc.proposals
    for ev in report.events:
        assert ev.get("origin") == "reconcile"
        assert ev.get("source") == ["docx-sha256:" + __import__("hashlib").sha256(data).hexdigest()]
        assert ev.author == human("docx:Rosa Lind")
    assert len({ev.get("batch") for ev in report.events}) == 1
    assert doc.verify() == []
    with pytest.raises(InvalidOperation, match="already imported"):
        doc.import_revision(data, changes="edits")


@pytest.mark.parametrize("shape", ["split", "merge"])
def test_split_and_merge_cards_are_linked_and_every_resolution_is_safe(legal, shape):
    doc = _copy(legal)
    ids = _body_ids(doc)
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    if shape == "split":
        c.split(ids[20], "appointed by")
    else:
        c.merge(ids[24], ids[25])
    report = doc.import_revision(c.save())
    cards = [doc.proposal(pid) for pid in report.proposals]
    assert len(cards) == 2
    head, tail = cards
    assert head.action == "modify"
    assert tail.action == ("add" if shape == "split" else "delete")
    assert tail.depends_on == head.id
    assert ("split of" if shape == "split" else "merged into") in (tail.explanation or "")
    for first, second in product(("accept", "reject"), repeat=2):
        trial = _copy(doc)
        getattr(trial, first)(head.id, decided_by=human("r"))
        getattr(trial, second)(tail.id, decided_by=human("r"))
        assert trial.verify() == [], (first, second)


def test_without_markers_content_alignment_still_finds_the_edits(legal):
    """Tools that drop bookmarks (and Google Docs) fall back to content."""
    doc = _copy(legal)
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    truth = _colleague_edit_set(c, _body_ids(doc))
    c.strip_marks()
    report = doc.import_revision(c.save())
    assert report.aligned_by_marker == 0
    assert report.aligned_by_content >= 0.9 * report.units
    assert _ops(report) == truth
    _healthy(doc)


def test_without_a_manifest_the_import_compares_with_the_current_document(legal):
    doc = _copy(legal)
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    truth = _colleague_edit_set(c, _body_ids(doc))
    c.drop_manifest()
    report = doc.import_revision(c.save())
    assert report.base_match == "unknown"
    assert any("no round-trip manifest" in w for w in report.warnings)
    assert _ops(report) == truth


def test_enter_at_a_paragraph_start_is_an_addition_not_a_rewrite(legal):
    doc = _copy(legal)
    ids = _body_ids(doc)
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    c.enter_at_start(ids[30], "Each party shall bear its own costs.")
    report = doc.import_revision(c.save())
    assert report.modified == [] and report.deleted == [] and len(report.added) == 1
    order = [ch.id for ch in legal.chunks]
    card = doc.proposal(report.proposals[0])
    assert card.anchor_after == order[order.index(ids[30]) - 1]


def test_repeated_boilerplate_never_invents_moves():
    d = aim.new_document(title="Forms")
    with d.batch():
        for i in range(6):
            d.add_chunk(f"<h2>Section {i} of the agreement between the parties</h2>", author=A)
            d.add_chunk("<p>Intentionally left blank.</p>", author=A)
    c = Colleague(docx_bytes(d, roundtrip_marks=True))
    c.strip_marks()
    body = c.body
    paras = [p for p in body if p.tag == q("p")]
    body.remove(paras[1])  # one "Intentionally left blank." goes
    report = _copy(d).import_revision(c.save())
    assert report.moved == []
    assert len(report.deleted) == 1 and report.added == []


def test_text_edits_rebase_onto_markup_docx_cannot_carry():
    d = aim.new_document(title="Marks")
    d.add_chunk(
        '<p>Pay <mark style="background-color:#fde68a">within 30 days</mark> of '
        "<strong>invoice</strong>.</p>",
        author=A,
    )
    uid = d.chunks[0].id
    c = Colleague(docx_bytes(d, roundtrip_marks=True))
    c.replace(uid, "30", "45")
    report = d.import_revision(c.save())
    payload = d.proposal(report.proposals[0]).payload_html or ""
    assert report.rebased == 1
    assert "within 45 days" in payload
    assert 'style="background-color:#fde68a"' in payload  # survived although DOCX dropped it


def test_lists_tables_and_sections():
    d = aim.new_document(title="Rich")
    with d.batch():
        d.add_chunk("<h1>Service Agreement</h1>", author=A)
        d.add_chunk(
            "<section><h2>Scope</h2><p>The supplier provides hosting.</p>"
            "<p>Support is included.</p></section>",
            author=A,
        )
        d.add_chunk(
            '<ul data-aim-container=""><li data-aim="">Hosting</li>'
            '<li data-aim="">Daily backups</li><li data-aim="">Monitoring</li></ul>',
            author=A,
        )
        d.add_chunk(
            '<table data-aim-container=""><tbody>'
            '<tr data-aim=""><td>Hosting</td><td>EUR 400</td></tr>'
            '<tr data-aim=""><td>Support</td><td>EUR 200</td></tr>'
            '<tr data-aim=""><td>Backups</td><td>EUR 50</td></tr></tbody></table>',
            author=A,
        )
    by_text = {ch.text: ch.id for ch in d.chunks}
    section = by_text["ScopeThe supplier provides hosting.Support is included."]
    c = Colleague(docx_bytes(d, roundtrip_marks=True))
    c.retype(by_text["Daily backups"], "Daily encrypted backups")
    c.insert_after(by_text["Monitoring"], "Incident reports")
    tr = next(c.paragraph(by_text["SupportEUR 200"]).iterancestors(q("tr")))
    next(tr.findall(q("tc"))[1].iter(q("t"))).text = "EUR 250"
    backups = next(c.paragraph(by_text["BackupsEUR 50"]).iterancestors(q("tr")))
    backups.getparent().remove(backups)
    for t in c.body.iter(q("t")):
        if t.text == "Support is included.":
            t.text = "Support is included on weekdays."
    report = d.import_revision(c.save())
    assert sorted(report.modified) == sorted(
        [section, by_text["Daily backups"], by_text["SupportEUR 200"]]
    )
    assert report.deleted == [by_text["BackupsEUR 50"]]
    assert len(report.added) == 1
    payloads = {p.target or "add": p.payload_html or "" for p in d.proposals}
    assert payloads[section].startswith(f'<section data-aim="{section}"><h2>Scope</h2>')
    assert "on weekdays" in payloads[section]
    add = next(p for p in d.proposals if p.action == "add")
    assert add.anchor_container == d.containers[0]
    _healthy(d)


def test_a_list_kind_flip_is_a_container_modify():
    d = aim.new_document(title="Flip")
    d.add_chunk(
        '<ul data-aim-container=""><li data-aim="">One</li><li data-aim="">Two</li></ul>', author=A
    )
    cid = d.containers[0]
    item_ids = [ch.id for ch in d.chunks]
    # rebuild the file from the same document with an ordered list, keeping
    # the markers: what a colleague clicking "Numbering" produces
    flipped = _copy(d)
    flipped._state.container_node(cid).tag = "ol"
    data = docx_bytes(flipped, roundtrip_marks=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    good = docx_bytes(d, roundtrip_marks=True)
    with zipfile.ZipFile(io.BytesIO(good)) as zf:
        for n in zf.namelist():
            if n.startswith("customXml/"):
                parts[n] = zf.read(n)  # the manifest of the real export
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, blob in parts.items():
            zf.writestr(n, blob)
    report = d.import_revision(buf.getvalue())
    assert report.modified == [cid]
    payload = d.proposal(report.proposals[0]).payload_html or ""
    assert payload.startswith(f'<ol data-aim-container="{cid}">')
    assert all(f'data-aim="{i}"' in payload for i in item_ids)  # items keep their ids
    _healthy(d)


def test_slides_take_text_edits_but_not_structure():
    deck = aim.AimDocument.load(EXAMPLES / "deck.aim")
    slide_chunks = [ch.id for ch in deck.chunks if ch.container != "body" and len(ch.text) > 10]
    target = slide_chunks[0]
    c = Colleague(docx_bytes(deck, pending="reject-all", roundtrip_marks=True))
    first_word = deck.chunk(target).text.split()[0]
    c.replace(target, first_word, first_word + " (revised)")
    c.insert_after(target, "A paragraph a colleague typed into the slide.")
    report = deck.import_revision(c.save())
    assert report.modified == [target] and report.added == []
    assert [cf.reason for cf in report.conflicts] == [
        "an addition inside a slide is not representable through DOCX"
    ]
    payload = next(p for p in deck.proposals if p.target == target).payload_html or ""
    assert "(revised)" in payload and "style=" in payload  # geometry kept
    _healthy(deck)


def test_figure_caption_edit_keeps_every_image():
    png = (
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVQI12P4"
        "z8AAAAMBAQAY3Y2wAAAAAElFTkSuQmCC"
    )
    d = aim.new_document(title="Figure")
    d.add_chunk(
        f'<figure><img alt="a" src="{png}"><img alt="b" src="{png}">'
        "<figcaption>Two logos side by side</figcaption></figure>",
        author=A,
    )
    uid = d.chunks[0].id
    c = Colleague(docx_bytes(d, roundtrip_marks=True))
    for t in c.body.iter(q("t")):
        if t.text and "side by side" in t.text:
            t.text = t.text.replace("side by side", "next to each other")
    report = d.import_revision(c.save())
    # DOCX import collapses the two pictures, so the unit is lossy: the
    # caption edit is replayed onto the original figure (never a payload
    # built from the returned, one-image figure)
    assert report.modified == [uid] and report.rebased == 1
    payload = d.proposal(report.proposals[0]).payload_html or ""
    assert payload.count("<img") == 2 and "next to each other" in payload
    _healthy(d)


def test_empty_document_round_trip_and_first_content():
    d = aim.new_document(title="Empty")
    assert d.import_revision(docx_bytes(d, roundtrip_marks=True)).proposals == []
    c = Colleague(docx_bytes(d, roundtrip_marks=True))
    p = etree.SubElement(c.body, q("p"))
    etree.SubElement(etree.SubElement(p, q("r")), q("t")).text = "First words."
    sect = c.body.find(q("sectPr"))
    if sect is not None:
        c.body.remove(sect)
        c.body.append(sect)
    report = d.import_revision(c.save())
    assert len(report.added) == 1
    _healthy(d)


# =============================================================================
# drift, pending lanes, conflicts


def test_changes_made_since_export_are_conflicts_not_overwrites(legal):
    doc = _copy(legal)
    ids = _body_ids(doc)
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    c.replace(ids[2], " ", " really ")
    c.replace(ids[3], " ", " also ")
    data = c.save()
    doc.modify_chunk(ids[2], doc.chunk(ids[2]).html.replace("</p>", " (agent)</p>"), author=A)
    doc.add_chunk("<p>An agent addition after the export.</p>", author=A, after=ids[5])
    added = doc.chunks[[ch.id for ch in doc.chunks].index(ids[5]) + 1].id
    report = doc.import_revision(data)
    assert report.base_match == "advanced"
    assert [cf.unit for cf in report.conflicts] == [ids[2]]
    assert report.modified == [ids[3]] and report.deleted == []
    assert added in [ch.id for ch in doc.chunks]  # never reverted
    flagged = _copy(doc)
    flagged.reject_all(decided_by=human("r"))
    flagged_report = flagged.import_revision(data, conflicts="propose")
    assert ids[2] in flagged_report.modified
    card = next(p for p in flagged.proposals if p.target == ids[2])
    assert "conflicts with an edit made after export" in (card.explanation or "")


@pytest.mark.parametrize(
    ("mode", "outcome"),
    [("accept-all", "supersede"), ("reject-all", "conflict"), ("tracked", "untouched")],
)
def test_pending_cards_on_edited_units_follow_the_export_mode(legal, mode, outcome):
    doc = _copy(legal)
    ids = _body_ids(doc)
    agent_card = doc.propose_modify(
        ids[4], doc.chunk(ids[4]).html.replace("</p>", " (agent)</p>"), author=A
    )
    c = Colleague(docx_bytes(doc, pending=mode, roundtrip_marks=True))
    if mode != "tracked":  # a tracked card's paragraphs are revisions, not text
        c.replace(ids[4], " ", " colleague ")
    c.replace(ids[7], " ", " yy ")
    report = doc.import_revision(c.save())
    assert ids[7] in report.modified
    if outcome == "supersede":
        assert ids[4] in report.modified and agent_card.id in report.superseded
        assert agent_card.id not in [p.id for p in doc.proposals]
    elif outcome == "conflict":
        assert [cf.unit for cf in report.conflicts] == [ids[4]]
        assert agent_card.id in [p.id for p in doc.proposals]
    else:
        assert ids[4] not in report.modified and not report.conflicts
    _healthy(doc)


def test_the_colleagues_track_changes_are_read_as_accepted(legal):
    doc = _copy(legal)
    ids = _body_ids(doc)
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    p = c.paragraph(ids[3])
    ins = etree.SubElement(p, q("ins"))
    ins.set(q("id"), "901")
    ins.set(q("author"), "Rosa Lind")
    ins.set(q("date"), "2026-10-02T09:00:00Z")
    etree.SubElement(etree.SubElement(ins, q("r")), q("t")).text = " Tracked addition."
    report = doc.import_revision(c.save())
    assert report.modified == [ids[3]]
    assert "Tracked addition." in (doc.proposal(report.proposals[0]).payload_html or "")
    assert any("tracked changes by Rosa Lind" in w for w in report.warnings)


def test_untrusted_author_names_are_marked_as_names_from_a_file(legal):
    doc = _copy(legal)
    ids = _body_ids(doc)
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    c.replace(ids[2], " ", " x ")
    report = _copy(doc).import_revision(c.save(author="ceo@example.com"))
    assert report.author == human("docx:ceo@example.com")
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    c.replace(ids[2], " ", " x ")
    report = _copy(doc).import_revision(c.save(author="  " + "x" * 200))
    assert report.author.id == "docx:" + "x" * 64
    raw = c.save(author=None)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    parts["docProps/core.xml"] = re.sub(
        rb"<cp:lastModifiedBy>.*?</cp:lastModifiedBy>", b"", parts["docProps/core.xml"]
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, blob in parts.items():
            zf.writestr(n, blob)
    report = _copy(doc).import_revision(buf.getvalue())
    assert report.author == aim.events.external("docx-import")
    explicit = _copy(doc).import_revision(raw, author=human("rosa@example.com"))
    assert explicit.author == human("rosa@example.com")


def test_refuses_a_document_that_does_not_verify(legal, tmp_path):
    doc = _copy(legal)
    data = docx_bytes(doc, roundtrip_marks=True)
    broken = doc.dumps().replace(doc.chunks[3].text[:30], "Edited by hand outside the tools", 1)
    with pytest.raises(InvalidOperation, match="does not verify"):
        aim.AimDocument.loads(broken).import_revision(data)


def test_refuses_an_unrelated_file(legal):
    other = aim.new_document(title="Other")
    other.add_chunk("<p>Completely unrelated content about gardening.</p>", author=A)
    with pytest.raises(InvalidOperation, match="does not look like a revision"):
        _copy(legal).import_revision(docx_bytes(other, roundtrip_marks=True))


def test_dry_run_changes_nothing(legal):
    doc = _copy(legal)
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    truth = _colleague_edit_set(c, _body_ids(doc))
    before = doc.dumps()
    report = doc.import_revision(c.save(), dry_run=True)
    assert _ops(report) == truth and report.proposals
    assert doc.dumps() == before


def test_dropped_parts_are_warned_about(legal):
    c = Colleague(docx_bytes(legal, roundtrip_marks=True))
    c.parts["word/footnotes.xml"] = (
        b'<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:footnote><w:p><w:r><w:t>A colleague footnote</w:t></w:r></w:p></w:footnote>"
        b"</w:footnotes>"
    )
    report = _copy(legal).import_revision(c.save())
    assert any("footnotes" in w for w in report.warnings)


def test_content_alignment_is_budgeted(monkeypatch):
    import aimformat.revision_import as ri

    d = aim.new_document(title="Long")
    with d.batch():
        for i in range(30):
            d.add_chunk(f"<p>Clause {i}: the parties agree to item number {i * 7}.</p>", author=A)
    c = Colleague(docx_bytes(d, roundtrip_marks=True))
    c.strip_marks()
    for t in c.body.iter(q("t")):
        t.text = (t.text or "").replace("agree", "consent")  # every paragraph edited
    monkeypatch.setattr(ri, "_FUZZY_BUDGET", 50)
    report = _copy(d).import_revision(c.save(), dry_run=True)
    assert any("budget exhausted" in w for w in report.warnings)


# =============================================================================
# CLI and MCP


def test_cli_export_and_import_onto(legal, tmp_path, capsys):
    base = tmp_path / "base.aim"
    legal.save(base)
    sent = tmp_path / "sent.docx"
    assert cli(["export", str(base), "-o", str(sent), "--roundtrip-marks"]) == 0
    c = Colleague(sent.read_bytes())
    truth = _colleague_edit_set(c, _body_ids(legal))
    back = tmp_path / "back.docx"
    back.write_bytes(c.save())
    capsys.readouterr()
    assert cli(["import", str(back), "--onto", str(base), "--dry-run", "--format", "json"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["wrote"] is None and len(dry["proposals"]) == 8
    assert aim.AimDocument.load(base).proposals == []
    assert cli(["import", str(back), "--onto", str(base)]) == 0
    out = capsys.readouterr().out
    assert "8 proposal(s)" in out and f"wrote {base}" in out
    doc = aim.AimDocument.load(base)
    assert len(doc.proposals) == 8
    assert sorted(p.target for p in doc.proposals if p.action == "modify") == truth["modified"]
    # a second run is a no-op
    assert cli(["import", str(back), "--onto", str(base), "--format", "json"]) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["proposals"] == [] and len(again["already_pending"]) == 8


def test_cli_usage_and_refusals(legal, tmp_path, capsys):
    base = tmp_path / "base.aim"
    legal.save(base)
    assert cli(["export", str(base), "-o", str(tmp_path / "x.md"), "--roundtrip-marks"]) == 2
    assert cli(["import", str(tmp_path / "x.md"), "--onto", str(base)]) == 2
    assert cli(["import", str(base)]) == 2  # no -o and no --onto
    other = aim.new_document(title="Other")
    other.add_chunk("<p>Unrelated gardening notes.</p>", author=A)
    stray = tmp_path / "stray.docx"
    stray.write_bytes(docx_bytes(other, roundtrip_marks=True))
    assert cli(["import", str(stray), "--onto", str(base)]) == 1
    edits = tmp_path / "edits.docx"
    c = Colleague(docx_bytes(legal, roundtrip_marks=True))
    c.replace(_body_ids(legal)[2], " ", " z ")
    edits.write_bytes(c.save())
    assert cli(["import", str(edits), "--onto", str(base), "--as", "edits"]) == 0
    assert cli(["import", str(edits), "--onto", str(base), "--as", "edits"]) == 1


def test_mcp_import_revision(legal, tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session

    from aimformat.mcp import create_server

    def call(tool: str, arguments: dict):
        async def run():
            async with create_connected_server_and_client_session(
                create_server(), raise_exceptions=False
            ) as s:
                return await s.call_tool(tool, arguments)

        return anyio.run(run)

    base = tmp_path / "base.aim"
    legal.save(base)
    sent = tmp_path / "sent.docx"
    result = call("aim_export", {"path": str(base), "out_path": str(sent), "roundtrip_marks": True})
    assert not result.isError
    c = Colleague(sent.read_bytes())
    _colleague_edit_set(c, _body_ids(legal))
    back = tmp_path / "back.docx"
    back.write_bytes(c.save())
    result = call("aim_import_revision", {"path": str(base), "docx_path": str(back)})
    assert not result.isError
    payload = json.loads(result.content[0].text)
    assert payload["ok"] and payload["lint_errors"] == 0 and len(payload["proposals"]) == 8
    assert len(aim.AimDocument.load(base).proposals) == 8
    monkeypatch.setenv("AIMFORMAT_MCP_ROOT", str(tmp_path / "jail"))
    (tmp_path / "jail").mkdir()
    result = call("aim_import_revision", {"path": str(base), "docx_path": str(back)})
    assert result.isError


# =============================================================================
# review regressions: what the document did between export and import


def _propose_one(doc: aim.AimDocument, kind: str, ids: list[str]):
    if kind == "modify":
        html = f'<p data-aim="{ids[3]}">Rewritten clause text by the agent.</p>'
        return doc.propose_modify(ids[3], html, author=A)
    if kind == "delete":
        return doc.propose_delete(ids[3], author=A)
    if kind == "move":
        return doc.propose_move(ids[3], author=A, container="body", after=ids[6])
    return doc.propose_add("<p>A brand new paragraph from the agent.</p>", author=A, after=ids[3])


@pytest.mark.parametrize(
    ("mode", "decision", "kind"),
    [
        *[("tracked", d, k) for d in ("accept", "reject") for k in ("modify", "delete", "add")],
        ("tracked", "accept", "move"),
        ("accept-all", "reject", "modify"),
        ("accept-all", "reject", "move"),
        ("reject-all", "accept", "move"),
    ],
)
def test_a_card_resolved_after_the_export_is_not_reproposed(legal, mode, decision, kind):
    """The exported file still shows a card the document has since decided:
    the import must not hand it back as the colleague's change (or undo it)."""
    doc = _copy(legal)
    ids = _body_ids(doc)
    card = _propose_one(doc, kind, ids)
    sent = docx_bytes(doc, pending=mode, roundtrip_marks=True)
    getattr(doc, decision)(card.id, decided_by=human("reviewer"))
    c = Colleague(sent)
    c.replace(ids[10], " ", " really ")
    report = doc.import_revision(c.save())
    assert report.modified == [ids[10]], report.summary()
    assert not (report.added or report.deleted or report.moved or report.conflicts)
    assert [p.target for p in doc.proposals] == [ids[10]]
    _healthy(doc)


@pytest.mark.parametrize("changes", ["proposals", "edits"])
def test_a_move_made_after_the_export_is_not_undone(legal, changes):
    doc = _copy(legal)
    ids = _body_ids(doc)
    sent = docx_bytes(doc, roundtrip_marks=True)
    doc.move_chunk(ids[3], container="body", after=ids[6], author=human("me"))
    before = doc.dumps()
    report = doc.import_revision(sent, changes=changes)
    assert not (report.moved or report.proposals or report.events), report.summary()
    assert doc.dumps() == before


def test_reimport_after_accepting_writes_nothing(legal):
    doc = _copy(legal)
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    _colleague_edit_set(c, _body_ids(doc))
    data = c.save()
    doc.import_revision(data)
    doc.accept_all(decided_by=human("reviewer"))
    snapshot = doc.dumps()
    again = doc.import_revision(data)
    assert not again.proposals and not again.conflicts, again.summary()
    assert doc.dumps() == snapshot


@pytest.mark.parametrize("changes", ["proposals", "edits"])
def test_a_new_first_body_row_stays_out_of_the_header(changes):
    d = aim.new_document(title="Prices")
    d.add_chunk(
        '<table data-aim-container=""><thead><tr data-aim=""><th>Item</th><th>Price</th></tr>'
        '</thead><tbody><tr data-aim=""><td>Hosting</td><td>EUR 400</td></tr>'
        '<tr data-aim=""><td>Support</td><td>EUR 200</td></tr></tbody></table>',
        author=A,
    )
    by_text = {ch.text: ch.id for ch in d.chunks}
    c = Colleague(docx_bytes(d, roundtrip_marks=True))
    row = next(c.paragraph(by_text["HostingEUR 400"]).iterancestors(q("tr")))
    new = __import__("copy").deepcopy(row)
    for bm in list(new.iter(q("bookmarkStart"))) + list(new.iter(q("bookmarkEnd"))):
        bm.getparent().remove(bm)
    texts = list(new.iter(q("t")))
    texts[0].text, texts[1].text = "Setup", "EUR 99"
    row.addprevious(new)
    report = d.import_revision(c.save(), changes=changes)
    assert len(report.added) == 1 and not report.conflicts
    if changes == "proposals":
        d.accept_all(decided_by=human("reviewer"))
    table = d._state.container_node(d.containers[0])
    head, body = (s for s in table.elements() if s.tag in ("thead", "tbody"))
    assert [r.text() for r in head.elements()] == ["ItemPrice"]
    assert [r.text() for r in body.elements()][0] == "SetupEUR 99"
    assert d.verify() == []


def test_edits_cannot_be_proposed_over_a_conflict(legal, tmp_path, capsys):
    doc = _copy(legal)
    with pytest.raises(InvalidOperation, match="needs changes='proposals'"):
        doc.import_revision(
            docx_bytes(doc, roundtrip_marks=True), changes="edits", conflicts="propose"
        )
    base = tmp_path / "base.aim"
    legal.save(base)
    sent = tmp_path / "sent.docx"
    sent.write_bytes(docx_bytes(legal, roundtrip_marks=True))
    args = ["import", str(sent), "--onto", str(base), "--as", "edits", "--conflicts", "propose"]
    assert cli(args) == 2


def test_unreadable_files_fail_cleanly(legal, tmp_path, capsys):
    from aimformat.errors import ParseError

    doc = _copy(legal)
    with pytest.raises(ParseError, match="not a readable .docx"):
        doc.import_revision(b"not a zip at all")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("docProps/core.xml", "<x/>")
    with pytest.raises(ParseError, match="not a readable .docx"):
        doc.import_revision(buf.getvalue())
    base = tmp_path / "base.aim"
    legal.save(base)
    junk = tmp_path / "junk.docx"
    junk.write_bytes(b"junk")
    capsys.readouterr()
    assert cli(["import", str(junk), "--onto", str(base)]) == 1
    assert "not a readable .docx" in capsys.readouterr().err


def test_cli_onto_output_needs_force_to_overwrite(legal, tmp_path):
    base = tmp_path / "base.aim"
    legal.save(base)
    sent = tmp_path / "sent.docx"
    sent.write_bytes(docx_bytes(legal, roundtrip_marks=True))
    other = tmp_path / "other.aim"
    other.write_text("keep me")
    assert cli(["import", str(sent), "--onto", str(base), "-o", str(other)]) == 2
    assert other.read_text() == "keep me"
    assert cli(["import", str(sent), "--onto", str(base), "-o", str(other), "--force"]) == 0
    assert cli(["import", str(sent), "--onto", str(base), "-o", str(base)]) == 0  # in place


def test_long_names_fall_back_to_the_hash():
    uid = "x" * 32
    name = docx_marks.bookmark_name(uid, 1000)
    assert len(name) <= 40 and name.startswith("_aimh1000_")
    marker = docx_marks.parse_bookmark_name(name)
    assert marker is not None and marker.ordinal == 1000
    assert docx_marks.resolve_marker(marker, docx_marks.hash_index([uid])) == uid


def test_edit_time_honours_the_file_offset():
    from aimformat.revision_import import _clamped_time

    assert _clamped_time("2026-01-02T03:00:00+02:00", "2026-01-01T00:00:00Z") == (
        "2026-01-02T01:00:00Z"
    )


# =============================================================================
# substitute review round 1


def _tracked_delete_mark(p, rid: int) -> None:
    """Word's Track Changes deleting a paragraph's mark (joins it with the next)."""
    ppr = p.find(q("pPr"))
    if ppr is None:
        ppr = etree.Element(q("pPr"))
        p.insert(0, ppr)
    rpr = ppr.find(q("rPr"))
    if rpr is None:
        rpr = etree.SubElement(ppr, q("rPr"))
    mark = etree.SubElement(rpr, q("del"))
    mark.set(q("id"), str(rid))
    mark.set(q("author"), "Rosa Lind")
    mark.set(q("date"), "2026-10-02T09:00:00Z")


def _tracked_delete_runs(p, rid: int) -> None:
    for r in list(p.findall(q("r"))):
        for t in r.findall(q("t")):
            t.tag = q("delText")
        wrap = etree.Element(q("del"))
        wrap.set(q("id"), str(rid))
        wrap.set(q("author"), "Rosa Lind")
        wrap.set(q("date"), "2026-10-02T09:00:00Z")
        r.addprevious(wrap)
        wrap.append(r)


def _three(*html: str) -> tuple[aim.AimDocument, list[str]]:
    d = aim.new_document(title="Three")
    with d.batch():
        for h in html:
            d.add_chunk(h, author=A)
    return d, [c.id for c in d.chunks]


@pytest.mark.parametrize("accepted_since", [False, True])
def test_deleting_a_pending_addition_the_export_showed_applied(accepted_since):
    d, ids = _three(
        "<p>Clause one about payment within thirty days of invoice.</p>",
        "<p>Clause two about governing law and jurisdiction of courts.</p>",
    )
    card = d.propose_add(
        "<p>Inserted clause about confidentiality obligations.</p>", after=ids[0], author=A
    )
    added = d._payload_root_id(card.payload_html or "")
    c = Colleague(docx_bytes(d, pending="accept-all", roundtrip_marks=True))
    c.delete(added)
    if accepted_since:
        d.accept(card.id, decided_by=human("owner"))
    report = d.import_revision(c.save())
    if accepted_since:  # live under its payload id now: a delete of it
        assert report.deleted == [added] and not report.conflicts, report.summary()
    else:  # not silently dropped
        assert not report.proposals
        assert [cf.unit for cf in report.conflicts] == [added]
        assert card.id in report.conflicts[0].reason
    _healthy(d)


def test_an_addition_matching_one_made_since_elsewhere_is_still_proposed():
    d, ids = _three(*(f"<p>Clause {i} says enough about matter number {i}.</p>" for i in range(4)))
    data = docx_bytes(d, roundtrip_marks=True)
    d.add_chunk("<p>Signature: ____</p>", author=human("owner"))  # at the end
    c = Colleague(data)
    sig = c.insert_after(ids[0], "Signature: ____")
    sig.addnext(c._blank_copy(sig, "Extra clause added by the colleague."))
    report = d.import_revision(c.save())
    assert len(report.added) == 2, report.summary()
    cards = [d.proposal(pid) for pid in report.proposals]
    assert "Signature" in (cards[0].payload_html or "") and cards[0].anchor_after == ids[0]
    assert cards[1].anchor_after == cards[0].id  # after the colleague's own line
    _healthy(d)


def test_a_tracked_deletion_across_paragraphs_joins_them_all():
    d, ids = _three(
        "<p>First paragraph that is long enough to matter here.</p>",
        "<p>Middle paragraph that the colleague removes entirely.</p>",
        "<p>Last paragraph that joins the first one after the deletion.</p>",
    )
    c = Colleague(docx_bytes(d, roundtrip_marks=True))
    _tracked_delete_runs(c.paragraph(ids[1]), 900)
    _tracked_delete_mark(c.paragraph(ids[0]), 901)
    _tracked_delete_mark(c.paragraph(ids[1]), 902)
    report = d.import_revision(c.save())
    assert report.modified == [ids[0]] and sorted(report.deleted) == sorted(ids[1:])
    joined = d.proposal(report.proposals[0]).payload_html or ""
    assert "matter here.Last paragraph" in joined and "Middle" not in joined
    _healthy(d)


def test_a_tracked_deletion_of_a_whole_heading_leaves_the_next_paragraph_as_it_was():
    d, ids = _three(
        "<p>Intro paragraph that is long enough to matter here.</p>",
        "<h2>Obsolete section heading</h2>",
        "<p>Body paragraph under the heading, which stays.</p>",
    )
    c = Colleague(docx_bytes(d, roundtrip_marks=True))
    _tracked_delete_runs(c.paragraph(ids[1]), 900)
    _tracked_delete_mark(c.paragraph(ids[1]), 901)
    report = d.import_revision(c.save())
    assert report.deleted == [ids[1]] and not report.modified, report.summary()
    _healthy(d)


def test_continuation_markers_regroup_in_linear_time():
    import time

    from aimformat.revision_import import _structure

    n = 20_000  # quadratic took ~20 s here; linear well under one
    blocks = (
        ['<p data-aim-mark="_aim_a">x</p>']
        + [f"<p>u{i}</p>" for i in range(n)]
        + ['<ul data-aim-container=""><li data-aim="">i</li></ul>']
        + [f'<p data-aim-mark="_aim2_a">m{i}</p>' for i in range(n)]
    )
    started = time.perf_counter()
    rets = _structure(blocks, {}, {})
    assert time.perf_counter() - started < 5
    assert len(rets) == 2 * n + 2  # nothing joined across the list


def test_continuation_markers_still_rejoin_a_flattened_unit():
    from aimformat.revision_import import _structure

    rets = _structure(
        [
            '<p data-aim-mark="_aim_a">one</p>',
            "<p>two</p>",
            '<p data-aim-mark="_aim3_a">three</p>',
            '<p data-aim-mark="_aim_b">next</p>',
        ],
        {},
        {},
    )
    assert [r.text for r in rets] == ["onetwothree", "next"]


@pytest.mark.parametrize(
    ("html", "old", "new", "want"),
    [
        (  # the export writes a link as "text (URL)"
            '<p>See <a href="https://x.example">the terms</a>; pay '
            '<mark style="background-color:#fde68a">within 30 days</mark>.</p>',
            "30",
            "45",
            '<p>See <a href="https://x.example">the terms</a>; pay '
            '<mark style="background-color:#fde68a">within 45 days</mark>.</p>',
        ),
        (  # typing at a line's end stays on that line
            "<p>Acme Ltd<br>12 High Street</p>",
            "Acme Ltd",
            "Acme Ltd (Registered)",
            "<p>Acme Ltd (Registered)<br>12 High Street</p>",
        ),
        (  # a word replaced just before a break is not split across it
            "<p>Line one of the clause<br>line two of the clause here.</p>",
            "clause",
            "section",
            "<p>Line one of the section<br>line two of the clause here.</p>",
        ),
    ],
)
def test_text_edits_next_to_markup_docx_reshapes_rebase_exactly(html, old, new, want):
    d = aim.new_document(title="Marks")
    uid = d.add_chunk(html, author=A).id
    c = Colleague(docx_bytes(d, roundtrip_marks=True))
    c.replace(uid, old, new)
    report = d.import_revision(c.save())
    assert report.rebased == 1 and not report.conflicts, report.summary()
    payload = d.proposal(report.proposals[0]).payload_html or ""
    assert _strip(payload) == want


def _strip(markup: str) -> str:
    return re.sub(r' data-aim="[^"]*"', "", markup)


def test_a_formatting_change_never_drops_a_link_docx_cannot_carry():
    d = aim.new_document(title="Link")
    uid = d.add_chunk(
        '<p>See <a href="https://x.example">the terms</a>; pay within 30 days.</p>', author=A
    ).id
    c = Colleague(docx_bytes(d, roundtrip_marks=True))
    run = c.paragraph(uid).findall(q("r"))[-1]
    rpr = run.find(q("rPr"))
    if rpr is None:
        rpr = etree.Element(q("rPr"))
        run.insert(0, rpr)
    etree.SubElement(rpr, q("b"))  # the colleague bolds the last run
    report = d.import_revision(c.save())
    assert not report.proposals
    assert [cf.unit for cf in report.conflicts] == [uid]


# =============================================================================
# a merge's delete and a split's addition never go out without their head


def _owner_edits(d: aim.AimDocument, uid: str) -> None:
    d.modify_chunk(uid, d.chunk(uid).html.replace("</p>", " (owner)</p>"), author=A)


MERGE_A = "<p>Alpha <b>bold</b> text in clause one of the agreement.</p>"
MERGE_B = "<p>Second paragraph <em>says</em> something else entirely here.</p>"
PLAIN_A = "<p>Alpha plain text in clause one of the agreement.</p>"


@pytest.mark.parametrize("changes", ["proposals", "edits"])
@pytest.mark.parametrize("why", ["markup", "drift"])
def test_a_merge_whose_survivor_is_a_conflict_deletes_nothing(changes, why):
    d, (a, b) = _three(MERGE_A if why == "markup" else PLAIN_A, MERGE_B)
    c = Colleague(docx_bytes(d, roundtrip_marks=True))
    c.merge(a, b)
    data = c.save()
    if why == "drift":
        _owner_edits(d, a)
    report = d.import_revision(data, changes=changes)
    assert a in [cf.unit for cf in report.conflicts]
    assert b in [cf.unit for cf in report.conflicts]
    assert report.deleted == [] and report.modified == []
    assert not d.proposals
    assert "Second paragraph says" in d.chunk(b).text  # nothing lost
    _healthy(d)


@pytest.mark.parametrize("changes", ["proposals", "edits"])
@pytest.mark.parametrize("why", ["markup", "drift"])
def test_a_split_whose_head_is_a_conflict_adds_nothing(changes, why):
    html = (
        "<p>See the terms for details. Payment is <b>due</b> within 30 days.</p>"
        if why == "markup"
        else "<p>See the terms for details. Payment is due within 30 days.</p>"
    )
    d, (a,) = _three(html)
    c = Colleague(docx_bytes(d, roundtrip_marks=True))
    c.split(a, "Payment is")
    data = c.save()
    if why == "drift":
        _owner_edits(d, a)
    report = d.import_revision(data, changes=changes)
    assert [cf.unit for cf in report.conflicts] == [a, a]
    assert report.added == [] and report.modified == []
    assert not d.proposals
    assert [ch.id for ch in d.chunks] == [a]
    assert d.chunk(a).text.count("Payment is") == 1  # never duplicated
    _healthy(d)


def test_a_merge_delete_is_dropped_with_its_unproposable_modify(legal, monkeypatch):
    """The emitter drops a dependent card whose head could not be proposed."""
    doc = _copy(legal)
    ids = _body_ids(doc)
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    c.merge(ids[24], ids[25])
    data = c.save()
    real = aim.AimDocument.propose_modify

    def refuse(self, uid, *args, **kwargs):
        if uid == ids[24]:
            raise InvalidOperation("refused for the test")
        return real(self, uid, *args, **kwargs)

    monkeypatch.setattr(aim.AimDocument, "propose_modify", refuse)
    report = doc.import_revision(data)
    assert not report.proposals and report.deleted == []
    assert {cf.unit for cf in report.conflicts} == {ids[24], ids[25]}
    assert ids[25] in [ch.id for ch in doc.chunks]


# =============================================================================
# a container whose skeleton changed keeps every item guard


def _reexported(d: aim.AimDocument, change) -> Colleague:
    """The file a colleague's container-level change (numbering, a header
    row) produces: the same markers, the real export's manifest."""
    changed = _copy(d)
    change(changed)
    c = Colleague(docx_bytes(changed, roundtrip_marks=True))
    good = Colleague(docx_bytes(d, roundtrip_marks=True))
    for n, blob in good.parts.items():
        if n.startswith("customXml/"):
            c.parts[n] = blob
    return c


def _bold_last_run(c: Colleague, uid: str) -> None:
    run = c.paragraph(uid).findall(q("r"))[-1]
    rpr = run.find(q("rPr"))
    if rpr is None:
        rpr = etree.Element(q("rPr"))
        run.insert(0, rpr)
    etree.SubElement(rpr, q("b"))


def test_a_container_change_never_drops_an_items_link():
    d = aim.new_document(title="List")
    d.add_chunk(
        '<ul data-aim-container=""><li data-aim="">See <a href="https://x.example">the terms'
        '</a>; pay within 30 days.</li><li data-aim="">Two</li></ul>',
        author=A,
    )
    cid = d.containers[0]
    first = d.chunks[0].id

    def number(doc):
        doc._state.container_node(cid).tag = "ol"

    c = _reexported(d, number)
    _bold_last_run(c, first)
    before = d.dumps()
    report = d.import_revision(c.save())
    assert not report.proposals
    assert cid in [cf.unit for cf in report.conflicts]
    assert d.dumps() == before


def test_a_header_row_change_never_drops_a_cells_link():
    d = aim.new_document(title="Table")
    d.add_chunk(
        '<table data-aim-container=""><tr data-aim=""><td>Name</td><td>Value</td></tr>'
        '<tr data-aim=""><td>Terms</td><td>See <a href="https://x.example">the terms</a> now.'
        "</td></tr></table>",
        author=A,
    )
    cid = d.containers[0]
    row = d.chunks[1].id
    c = Colleague(docx_bytes(d, roundtrip_marks=True))
    tr = next(c.body.iter(q("tr")))
    trpr = tr.find(q("trPr"))
    if trpr is None:
        trpr = etree.Element(q("trPr"))
        tr.insert(1 if tr.find(q("tblPrEx")) is not None else 0, trpr)
    etree.SubElement(trpr, q("tblHeader"))  # "repeat as header row"
    cell = list(c.paragraph(row).getparent().getparent().iter(q("p")))[-1]
    run = cell.findall(q("r"))[-1]
    rpr = run.find(q("rPr"))
    if rpr is None:
        rpr = etree.Element(q("rPr"))
        run.insert(0, rpr)
    etree.SubElement(rpr, q("b"))
    before = d.dumps()
    report = d.import_revision(c.save())
    assert not report.proposals
    assert cid in [cf.unit for cf in report.conflicts]
    assert d.dumps() == before


# =============================================================================
# under an auto review policy (spec §5.6): an import is a review request


@pytest.mark.parametrize("conflicts", ["report", "propose"])
def test_an_import_under_the_auto_policy_stays_pending(legal, conflicts):
    """The policy auto-accepts agent and external cards at batch close; a
    colleague's revision imported as proposals is still for review, even
    when it is attributed to an agent or the external fallback author."""
    doc = _copy(legal)
    ids = _body_ids(doc)
    doc.set_review_policy("auto", by=human("Ada"), author=human("Ada"))
    c = Colleague(docx_bytes(doc, roundtrip_marks=True))
    c.replace(ids[2], " ", " really ")
    c.delete(ids[8])
    data = c.save()
    doc.modify_chunk(
        ids[2], doc.chunk(ids[2]).html.replace("</p>", " (Ada)</p>"), author=human("Ada")
    )
    before = {i: doc.chunk(i).html for i in (ids[2], ids[8])}
    report = doc.import_revision(data, author=A, conflicts=conflicts)
    assert report.proposals and sorted(p.id for p in doc.proposals) == sorted(report.proposals)
    assert {i: doc.chunk(i).html for i in (ids[2], ids[8])} == before
    assert not [e for e in doc.history if e.kind == "resolution"]
    _healthy(doc)


def test_a_card_resolved_after_export_under_the_auto_policy_round_trips(legal):
    """Rebuilding the export-time lane must not auto-accept the replayed
    agent cards: an untouched file is a null round trip, policy or not."""
    doc = _copy(legal)
    ids = _body_ids(doc)
    card = _propose_one(doc, "modify", ids)  # pending before the policy
    doc.set_review_policy("auto", by=human("Ada"), author=human("Ada"))
    sent = docx_bytes(doc, pending="tracked", roundtrip_marks=True)
    doc.accept(card.id, decided_by=human("Ada"))
    snapshot = doc.dumps()
    report = doc.import_revision(Colleague(sent).save())
    assert not (report.proposals or report.conflicts or report.warnings), report.summary()
    assert doc.dumps() == snapshot
