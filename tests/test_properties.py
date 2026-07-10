"""Property-based tests (Hypothesis) for the public SDK surface (WS5, L4).

Four invariant families, each exercised over generated inputs:

1. Round-trip identity — ``dumps()`` is a fixed point of ``loads``:
   ``aim.loads(doc.dumps()).dumps()`` is byte-identical.
2. Canonical/lint idempotence — every SDK-built document is lint-clean
   (zero error-level findings), already in canonical form (no C001), and
   linting is deterministic; the history always replays (``verify() == []``).
3. Reconcile — a no-op on untouched documents (twice over), and an
   out-of-band edit to one chunk is adopted without touching any other
   chunk's payload.
4. Parser totality — ``aim.loads`` on arbitrary text either returns a
   document or raises a defined ``AimError``; never any other exception.

Documents are built through the public API (``new_document`` + document
ops) with deterministic ids and timestamps so failures shrink and replay
cleanly. Run with ``--hypothesis-seed=0`` for reproducibility.
"""

from __future__ import annotations

import string

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import aimformat as aim

BOT = aim.agent("claude-opus-4-8")
ME = aim.human("luca")


def ts(i: int) -> str:
    """Deterministic ascending timestamps (same scheme as conftest)."""
    return f"2026-07-07T{10 + i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}Z"


# Plain inline text: no markup metacharacters (payload text is generated
# *inside* markup we assemble ourselves) and no surrounding whitespace.
_TEXT_ALPHABET = string.ascii_letters + string.digits + " .,;:!?()'-"
inline_texts = st.text(_TEXT_ALPHABET, min_size=1, max_size=40).map(str.strip).filter(bool)

_CHUNK_TAGS = ("p", "h2", "blockquote")


def _markup(tag: str, cid: str, text: str) -> str:
    return f'<{tag} data-aim="{cid}">{text}</{tag}>'


@st.composite
def documents(draw) -> aim.AimDocument:
    """An AimDocument built through the public API: a batch of adds followed
    by a random sequence of ops (modify / delete / move / proposal lifecycle /
    undo+redo / checkpoint), all with deterministic ids and timestamps."""
    doc = aim.new_document(title=draw(inline_texts))
    clock = 0
    tags: dict[str, str] = {}

    n_chunks = draw(st.integers(min_value=1, max_value=6))
    for k in range(n_chunks):
        cid = f"c{k}"
        tag = draw(st.sampled_from(_CHUNK_TAGS))
        tags[cid] = tag
        doc.add_chunk(_markup(tag, cid, draw(inline_texts)), author=BOT, at=ts(clock))
        clock += 1

    n_ops = draw(st.integers(min_value=0, max_value=6))
    for k in range(n_ops):
        live = [c.id for c in doc.chunks]  # document order
        ops = ["add", "undo_redo", "checkpoint"]
        if live:
            ops += ["modify", "delete", "propose_accept", "propose_reject"]
        if len(live) >= 2:
            ops += ["move"]
        op = draw(st.sampled_from(ops))
        if op == "add":
            cid = f"x{k}"
            tag = draw(st.sampled_from(_CHUNK_TAGS))
            tags[cid] = tag
            doc.add_chunk(_markup(tag, cid, draw(inline_texts)), author=BOT, at=ts(clock))
            clock += 1
        elif op == "modify":
            cid = draw(st.sampled_from(live))
            # unique "mod{k}" prefix: modifying to identical content is a
            # defined InvalidOperation, not a case we want to generate
            doc.modify_chunk(
                cid,
                _markup(tags[cid], cid, f"mod{k} {draw(inline_texts)}"),
                author=ME,
                at=ts(clock),
            )
            clock += 1
        elif op == "delete":
            doc.delete_chunk(draw(st.sampled_from(live)), author=ME, at=ts(clock))
            clock += 1
        elif op == "move":
            # moving the current last chunk to LAST is a defined no-op
            # InvalidOperation — draw from everything but the last
            doc.move_chunk(draw(st.sampled_from(live[:-1])), author=ME, at=ts(clock))
            clock += 1
        elif op in ("propose_accept", "propose_reject"):
            cid = draw(st.sampled_from(live))
            prop = doc.propose_modify(
                cid,
                _markup(tags[cid], cid, f"prop{k} {draw(inline_texts)}"),
                author=BOT,
                explanation="property test",
                at=ts(clock),
            )
            clock += 1
            if op == "propose_accept":
                doc.accept(prop.id, decided_by=ME, at=ts(clock))
            else:
                doc.reject(prop.id, decided_by=ME, at=ts(clock))
            clock += 1
        elif op == "undo_redo":
            doc.undo(author=ME, at=ts(clock))
            doc.redo(author=ME, at=ts(clock + 1))
            clock += 2
        elif op == "checkpoint":
            doc.checkpoint(f"cp{k}", at=ts(clock))
            clock += 1
    return doc


_DOC_SETTINGS = settings(
    max_examples=50,
    deadline=None,  # document building dominates; wall-clock varies by host
    suppress_health_check=[HealthCheck.too_slow],
)


@given(doc=documents())
@_DOC_SETTINGS
def test_parse_serialize_roundtrip_identity(doc: aim.AimDocument) -> None:
    """dumps() is a fixed point: loads(dumps(doc)).dumps() is byte-identical."""
    text = doc.dumps()
    assert aim.loads(text).dumps() == text


@given(doc=documents())
@_DOC_SETTINGS
def test_sdk_documents_are_lint_clean_canonical_and_verifiable(doc: aim.AimDocument) -> None:
    """Any API-built document lints with zero errors, is already canonical
    (lint of the serialized text raises no C001), lints deterministically,
    and replays its own history cleanly."""
    errors = [f for f in aim.lint(doc) if f.level == "error"]
    assert errors == [], [str(f) for f in errors]
    text = doc.dumps()
    findings = aim.lint_text(text)
    assert not any(f.code == "C001" for f in findings), [str(f) for f in findings]
    assert not any(f.level == "error" for f in findings), [str(f) for f in findings]
    # lint is deterministic: a second pass yields the identical findings
    assert [str(f) for f in aim.lint_text(text)] == [str(f) for f in findings]
    assert doc.verify() == []


@given(doc=documents())
@_DOC_SETTINGS
def test_reconcile_is_a_noop_on_untouched_documents(doc: aim.AimDocument) -> None:
    """reconcile() on a document with no out-of-band edits changes nothing,
    twice over, and leaves the history verifiable."""
    before = doc.dumps()
    seq = doc.seq
    report = doc.reconcile(author=aim.external(), at=ts(7200))
    assert report.changed is False
    assert report.events == []
    assert doc.seq == seq
    assert doc.dumps() == before
    second = doc.reconcile(author=aim.external(), at=ts(7201))
    assert second.changed is False
    assert doc.verify() == []


@st.composite
def sentinel_docs(draw) -> tuple[aim.AimDocument, int, int]:
    """A document of n paragraphs carrying unique sentinel payloads, plus a
    chosen index to edit out-of-band. Returns (doc, n, edited_index)."""
    n = draw(st.integers(min_value=2, max_value=6))
    doc = aim.new_document(title="Reconcile property")
    for k in range(n):
        doc.add_chunk(_markup("p", f"c{k}", f"sentinel{k}"), author=BOT, at=ts(k))
    edited = draw(st.integers(min_value=0, max_value=n - 1))
    return doc, n, edited


@given(data=sentinel_docs())
@_DOC_SETTINGS
def test_reconcile_preserves_untouched_chunks(
    data: tuple[aim.AimDocument, int, int],
) -> None:
    """Hand-editing one chunk's payload in the serialized file and
    reconciling adopts that edit without touching any other chunk: every
    other payload stays byte-identical, the edit lands as reconcile-origin
    events, and the repaired history verifies."""
    doc, n, edited = data
    original_html = {f"c{k}": doc.chunk(f"c{k}").html for k in range(n)}
    text = doc.dumps()
    # The raw body line (unescaped quotes) is unique — the payload also sits
    # in the history log, but there its quotes are JSON-escaped.
    needle = f'<p data-aim="c{edited}">sentinel{edited}</p>'
    assert text.count(needle) == 1, text
    touched = aim.loads(text.replace(needle, f'<p data-aim="c{edited}">edited out of band</p>'))
    report = touched.reconcile(author=aim.external(), at=ts(7200))
    assert report.changed is True
    assert touched.chunk(f"c{edited}").text == "edited out of band"
    for k in range(n):
        if k != edited:
            assert touched.chunk(f"c{k}").html == original_html[f"c{k}"]
    assert touched.verify() == []
    # and the repaired document is itself a serialization fixed point
    repaired = touched.dumps()
    assert aim.loads(repaired).dumps() == repaired


@given(text=st.text(max_size=2000))
@settings(max_examples=300, deadline=None)
def test_parser_never_raises_unhandled_exceptions(text: str) -> None:
    """aim.loads on arbitrary text either parses or raises a defined
    AimError subclass — never an unrelated exception."""
    try:
        doc = aim.loads(text)
    except aim.AimError:
        return  # defined failure mode
    assert isinstance(doc, aim.AimDocument)


@given(text=st.text(alphabet=string.printable, max_size=2000))
@settings(max_examples=300, deadline=None)
def test_linter_is_total_on_parseable_text(text: str) -> None:
    """aim.lint_text mirrors the parser's totality: findings or AimError."""
    try:
        findings = aim.lint_text(text)
    except aim.AimError:
        return
    assert isinstance(findings, list)
