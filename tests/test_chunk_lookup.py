"""P1: ``AimDocument.chunks`` / ``chunk()`` are one tree walk, not one per chunk.

The views (``aimformat.views``) and every edit batch read chunk views
repeatedly; the old implementation re-walked the whole tree per chunk (and
``chunk()`` built the whole list), so a 775-chunk document spent ~1 s per
``doc.chunks`` and every ``modify_chunk`` paid that again. The rewrite must
return exactly the objects the reference walk returned — same ids, order,
containers, tags, serialization and text — on every document we ship.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import aimformat as aim
from aimformat.canonical import serialize_run
from aimformat.document import AimDocument, Chunk
from aimformat.errors import AimError, TargetNotFound
from conftest import BOT

ROOT = Path(__file__).resolve().parent.parent


def reference_chunks(doc: AimDocument) -> list[Chunk]:
    """The pre-0.6 implementation, verbatim, as the equivalence oracle."""
    out: list[Chunk] = []
    seen: set[str] = set()
    state = doc._state

    def emit(cid: str) -> None:
        if cid in seen:
            return
        seen.add(cid)
        _parent, members = state.find_chunk(cid)
        out.append(
            Chunk(
                id=cid,
                container=state.container_of_chunk(cid),
                tags=tuple(m.tag for m in members),
                html=serialize_run(members),
                text="".join(m.text() for m in members),
            )
        )

    for top in state.constructs():
        for el in top.iter():
            if el.chunk_id:
                emit(el.chunk_id)
    return out


def _corpus() -> list[Path]:
    paths = sorted((ROOT / "examples").glob("*.aim"))
    paths += sorted((ROOT / "tests" / "parity" / "fixtures").glob("*.aim"))
    paths += sorted((ROOT / "tests" / "fixtures").glob("*.aim"))
    return paths


@pytest.mark.parametrize("path", _corpus(), ids=lambda p: p.name)
def test_chunks_match_the_reference_walk(path: Path) -> None:
    try:
        doc = aim.load(path)
    except AimError:
        pytest.skip("not loadable (a parse-level nok fixture)")
    try:
        expected = reference_chunks(doc)
    except AimError as exc:
        with pytest.raises(type(exc)):
            doc.chunks  # noqa: B018 - the property access is the call under test
        return
    assert doc.chunks == expected
    for c in expected:
        assert doc.chunk(c.id) == c


def test_chunk_unknown_id_raises_target_not_found(rich_doc) -> None:
    with pytest.raises(TargetNotFound, match="no chunk 'nope'"):
        rich_doc.chunk("nope")
    # containers and reserved targets are not chunks
    for cid in ("list", "tbl", "aim:theme", "body"):
        with pytest.raises(TargetNotFound):
            rich_doc.chunk(cid)


def test_rich_doc_matches_reference(rich_doc) -> None:
    assert rich_doc.chunks == reference_chunks(rich_doc)
    assert rich_doc.chunk("li2").tags == ("li", "li")
    assert rich_doc.chunk("row1").container == "tbl"


def _big_doc(n: int) -> AimDocument:
    doc = aim.new_document(title="Big")
    with doc.batch():
        for i in range(n):
            doc.add_chunk(f'<p data-aim="c{i}">Paragraph number {i}.</p>', author=BOT)
    return doc


def test_chunk_lookup_is_linear() -> None:
    """Timing guard: generous bounds (the quadratic walk took minutes)."""
    doc = _big_doc(2000)
    t = time.perf_counter()
    assert len(doc.chunks) == 2000
    assert time.perf_counter() - t < 1.0
    t = time.perf_counter()
    for i in range(0, 2000, 200):
        doc.modify_chunk(f"c{i}", f'<p data-aim="c{i}">Changed {i}.</p>', author=BOT)
    assert time.perf_counter() - t < 1.0
