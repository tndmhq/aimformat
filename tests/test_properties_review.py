"""Property-based tests generalizing the Codex deep-review fixes (AIM-01..08).

`tests/test_review_regressions.py` (wave 3) pins each finding with the exact
failing input; this file generalizes the fixed *bug classes* over generated
inputs so the invariants can't regress in a nearby shape:

1. AIM-01 — the parsed fragment is the trust boundary: any junk top level
   content outside the single ``<html>`` document element (text, comments,
   elements, a second ``<html>``) must never lint clean — lint reports S028
   and ``lint_text`` never crashes (no "verifier internal error" S000).
2. AIM-06 — URL checks match by *scheme*, not raw prefix: an ``a@href`` of
   the form ``<scheme>:<rest>`` lints clean iff the scheme (text before the
   first ``:``) is in the registry allowlist; ``img@src`` honors the
   ``data:image/`` token as an exact prefix.
3. AIM-04 — duplicate pending proposal ids lint P017, and accept/reject on
   the duplicated id raise InvalidOperation instead of silently resolving
   the first card.
4. AIM-03 — proposal add anchors are container-scoped: the API refuses a
   cross-container anchor (direct or chained), and a text-mutated document
   carrying one lints P016.
5. AIM-08 — ``propose_move`` mirrors ``move_chunk``: a no-op move (from ==
   to) raises InvalidOperation at every position, in body and in containers,
   without touching the document.

Documents are built through the public API with deterministic ids and
timestamps (same conventions as ``test_properties.py``); expected URL
allowlists come from the live registry, never hardcoded copies of the
implementation. Run with ``--hypothesis-seed=0`` for reproducibility.
"""

from __future__ import annotations

import string

import pytest

pytest.importorskip("hypothesis")

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import aimformat as aim

BOT = aim.agent("claude-opus-4-8")
ME = aim.human("luca")


def ts(i: int) -> str:
    """Deterministic ascending timestamps (same scheme as conftest)."""
    return f"2026-07-07T{10 + i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}Z"


# Plain inline text: no markup metacharacters and no surrounding whitespace
# (same alphabet as test_properties.py).
_TEXT_ALPHABET = string.ascii_letters + string.digits + " .,;:!?()'-"
inline_texts = st.text(_TEXT_ALPHABET, min_size=1, max_size=40).map(str.strip).filter(bool)

_DOC_SETTINGS = settings(
    max_examples=50,
    deadline=None,  # document building dominates; wall-clock varies by host
    suppress_health_check=[HealthCheck.too_slow],
)


@st.composite
def paragraph_docs(draw, min_chunks: int = 1, max_chunks: int = 4) -> aim.AimDocument:
    """A document of n paragraph chunks c0..c{n-1} built through the API."""
    doc = aim.new_document(title=draw(inline_texts))
    n = draw(st.integers(min_value=min_chunks, max_value=max_chunks))
    for k in range(n):
        doc.add_chunk(f'<p data-aim="c{k}">{draw(inline_texts)}</p>', author=BOT, at=ts(k))
    return doc


# ---------------------------------------------------------------- AIM-01
# The serialized shell is `<!doctype html>\n<html …>…</html>\n`; junk is
# either appended after the final newline or spliced right before the
# `<html` open tag (after the doctype). A *prepended second <html>* is the
# one shape the parser rejects outright (it becomes the document element and
# has no head/body) — that is a defined ParseError, pinned separately below,
# so the generator keeps that junk kind append-only.


@st.composite
def top_level_junk(draw) -> tuple[str, bool]:
    """(junk markup, append?) — junk that must never survive lint outside
    the single <html> document element."""
    kind = draw(st.sampled_from(["text", "comment", "element", "second-html"]))
    text = draw(inline_texts)
    if kind == "text":
        junk = text
    elif kind == "comment":
        junk = f"<!-- {text} -->"
    elif kind == "second-html":
        junk = f"<html>{text}</html>"
    else:
        tag = draw(st.sampled_from(["script", "style", "iframe", "div", "p", "aim-proposal"]))
        junk = f"<{tag}>{text}</{tag}>"
    append = True if kind == "second-html" else draw(st.booleans())
    return junk, append


@given(doc=paragraph_docs(), junk=top_level_junk())
@_DOC_SETTINGS
def test_junk_outside_html_never_lints_clean(doc: aim.AimDocument, junk: tuple[str, bool]) -> None:
    """AIM-01: generated text/comments/elements outside the single <html>
    document element always lint S028 — and the linter reports findings, it
    never crashes into the internal-error net."""
    markup, append = junk
    text = doc.dumps()
    mutated = text + markup + "\n" if append else text.replace("<html", markup + "<html", 1)
    try:
        findings = aim.lint_text(mutated)
    except aim.AimError:  # a defined parse rejection is an acceptable
        return  # failure mode — but never any other exception
    codes = {f.code for f in findings}
    assert "S028" in codes, [str(f) for f in findings]
    # the last-resort net in lint_text turns crashes into an S000 finding
    # with this marker — reaching it means a verifier bug
    assert not any("verifier internal error" in f.message for f in findings)


@given(doc=paragraph_docs(), text=inline_texts)
@_DOC_SETTINGS
def test_leading_second_html_is_rejected_not_crashed(doc: aim.AimDocument, text: str) -> None:
    """AIM-01 (edge): a second <html> *before* the real one becomes the
    document element and cannot parse — a defined rejection (ParseError /
    S000 parse-failed finding), never a crash and never lint-clean."""
    mutated = doc.dumps().replace("<html", f"<html>{text}</html><html", 1)
    findings = aim.lint_text(mutated)
    assert any(f.level == "error" for f in findings)
    assert not any("verifier internal error" in f.message for f in findings)
    with pytest.raises(aim.AimError):
        aim.loads(mutated)


# ---------------------------------------------------------------- AIM-06
# Adversarial prefixes of real schemes, plus arbitrary letter runs. The
# expected allowlist is read from the live registry (bare tokens without
# ':'), so the oracle is the *spec*, not a copy of check_url's code.

_ADVERSARIAL_SCHEMES = (
    "httpjavascript",
    "httpsx",
    "mailtox",
    "xhttp",
    "javascript",
    "vbscript",
    "data",
    "tel",
    "ftp",
    "http",
    "https",
    "mailto",
    "HTTP",
    "Mailto",
)
scheme_strategy = st.one_of(
    st.sampled_from(_ADVERSARIAL_SCHEMES),
    st.text(string.ascii_letters, min_size=1, max_size=12),
)
rest_strategy = st.text(string.ascii_letters + string.digits + "/.@-", max_size=20)

_URL_SETTINGS = settings(max_examples=120, deadline=None)


@given(scheme=scheme_strategy, rest=rest_strategy)
@_URL_SETTINGS
def test_href_lints_clean_iff_scheme_is_allowlisted(scheme: str, rest: str) -> None:
    """AIM-06: an <a href="scheme:rest"> chunk lints clean iff the text
    before the first ':' is a registry-allowed scheme (case-insensitive) —
    look-alike prefixes like httpjavascript:/httpsx:/mailtox: never pass."""
    allowed = {s.lower() for s in aim.REGISTRY.url_schemes("a.href") if ":" not in s and s != "#"}
    assert allowed == {"http", "https", "mailto"}  # registry ground truth (spec 0.1)
    doc = aim.new_document(title="U")
    doc.add_chunk(f'<p data-aim="p1"><a href="{scheme}:{rest}">l</a></p>', author=ME, at=ts(0))
    errors = [f for f in aim.lint_text(doc.dumps()) if f.level == "error"]
    if scheme.lower() in allowed:
        assert errors == [], [str(f) for f in errors]
    else:
        assert {f.code for f in errors} & {"V009", "X003"}, [str(f) for f in errors]


@given(
    prefix=st.sampled_from(
        ["data:image/", "data:imagex/", "data:imag", "data:", "datax:image/", "DATA:IMAGE/"]
    ),
    rest=rest_strategy,
)
@_URL_SETTINGS
def test_img_src_data_token_is_an_exact_prefix(prefix: str, rest: str) -> None:
    """AIM-06: the ':'-carrying registry token (data:image/) stays an exact,
    case-insensitive prefix on img@src — near-misses are rejected."""
    value = prefix + rest
    doc = aim.new_document(title="U")
    markup = f'<figure data-aim="f1"><img alt="a" src="{value}"></figure>'
    doc.add_chunk(markup, author=ME, at=ts(0))
    errors = [f for f in aim.lint_text(doc.dumps()) if f.level == "error"]
    if value.lower().startswith("data:image/"):
        assert errors == [], [str(f) for f in errors]
    else:
        assert {f.code for f in errors} & {"V009", "X003"}, [str(f) for f in errors]


# ---------------------------------------------------------------- AIM-04


@st.composite
def docs_with_duplicate_proposal_ids(draw) -> tuple[aim.AimDocument, str, str]:
    """A document with two pending proposals, then the serialized text is
    mutated so both cards carry the first proposal's id.
    Returns (clean doc, duplicated id, mutated text)."""
    doc = draw(paragraph_docs())
    p1 = doc.propose_add(f"<p>{draw(inline_texts)}</p>", author=BOT, at=ts(90))
    kind = draw(st.sampled_from(["add", "modify", "delete"]))
    target = draw(st.sampled_from([c.id for c in doc.chunks]))
    if kind == "add":
        p2 = doc.propose_add(f"<p>{draw(inline_texts)}</p>", author=BOT, at=ts(91))
    elif kind == "modify":
        p2 = doc.propose_modify(
            target,
            f'<p data-aim="{target}">mod {draw(inline_texts)}</p>',
            author=BOT,
            at=ts(91),
        )
    else:
        p2 = doc.propose_delete(target, author=BOT, at=ts(91))
    return doc, p1.id, doc.dumps().replace(p2.id, p1.id)


@given(data=docs_with_duplicate_proposal_ids())
@_DOC_SETTINGS
def test_duplicate_proposal_ids_lint_p017_and_refuse_resolution(
    data: tuple[aim.AimDocument, str, str],
) -> None:
    """AIM-04: whatever the two proposals' actions, a text-mutated duplicate
    pending id lints P017, and accept/reject on that id raise
    InvalidOperation without touching the document (no silent first-card
    resolution)."""
    _, dup_id, mutated = data
    assert "P017" in {f.code for f in aim.lint_text(mutated)}
    dup = aim.loads(mutated)
    before = dup.dumps()
    with pytest.raises(aim.InvalidOperation):
        dup.accept(dup_id, decided_by=ME, at=ts(92))
    with pytest.raises(aim.InvalidOperation):
        dup.reject(dup_id, decided_by=ME, at=ts(93))
    assert dup.dumps() == before  # neither card was resolved or applied


# ---------------------------------------------------------------- AIM-03


@st.composite
def docs_with_list(draw) -> tuple[aim.AimDocument, str, str]:
    """Body paragraphs plus a <ul> container; returns
    (doc, a body chunk id, a list item id)."""
    doc = draw(paragraph_docs())
    n_items = draw(st.integers(min_value=1, max_value=3))
    items = "".join(f'<li data-aim="i{j}">{draw(inline_texts)}</li>' for j in range(n_items))
    doc.add_chunk(f'<ul data-aim-container="lst">{items}</ul>', author=BOT, at=ts(40))
    body_cid = draw(st.sampled_from([c for c in doc.body_ids if c != "lst"]))
    item_cid = draw(st.sampled_from([f"i{j}" for j in range(n_items)]))
    return doc, body_cid, item_cid


@given(data=docs_with_list())
@_DOC_SETTINGS
def test_cross_container_add_anchors_are_refused_and_lint_p016(
    data: tuple[aim.AimDocument, str, str],
) -> None:
    """AIM-03: propose_add anchors resolve strictly inside their stated
    container — the API refuses direct and chained cross-container anchors,
    and a text-mutated card with one lints P016."""
    doc, body_cid, item_cid = data
    before = doc.dumps()
    # direct: anchor exists in the document but in ANOTHER container
    with pytest.raises(aim.TargetNotFound):
        doc.propose_add("<li>x</li>", author=BOT, container="lst", after=body_cid, at=ts(41))
    with pytest.raises(aim.TargetNotFound):
        doc.propose_add("<p>x</p>", author=BOT, container="body", after=item_cid, at=ts(42))
    assert doc.dumps() == before  # refused proposals leave no trace
    # chained: anchoring on a pending add that targets a different container
    pending_body = doc.propose_add(f"<p>{body_cid} tail</p>", author=BOT, at=ts(43))
    with pytest.raises(aim.InvalidOperation):
        doc.propose_add("<li>y</li>", author=BOT, container="lst", after=pending_body.id, at=ts(44))
    # text mutation: a valid in-container anchor rewritten to a body chunk
    doc.propose_add(
        '<li data-aim="nn">new</li>', author=BOT, container="lst", after=item_cid, at=ts(45)
    )
    mutated = doc.dumps().replace(
        f'data-anchor-after="{item_cid}"', f'data-anchor-after="{body_cid}"'
    )
    assert "P016" in {f.code for f in aim.lint_text(mutated)}


# ---------------------------------------------------------------- AIM-08


@st.composite
def docs_with_position(draw) -> tuple[aim.AimDocument, str, str, str | None]:
    """A document with body paragraphs and a list container, plus one chunk
    chosen anywhere in it. Returns (doc, chunk id, its container, the id it
    currently sits after) — i.e. the chunk's CURRENT position."""
    doc, _, _ = draw(docs_with_list())
    order = doc.body_ids  # body constructs in document order (incl. "lst")
    items = [c.id for c in doc.chunks if c.container == "lst"]
    place = draw(st.sampled_from(["body", "lst"]))
    if place == "body":
        i = draw(st.integers(min_value=0, max_value=len(order) - 1))
        return doc, order[i], "body", order[i - 1] if i else None
    j = draw(st.integers(min_value=0, max_value=len(items) - 1))
    return doc, items[j], "lst", items[j - 1] if j else None


@given(data=docs_with_position())
@_DOC_SETTINGS
def test_noop_moves_raise_for_any_position(
    data: tuple[aim.AimDocument, str, str, str | None],
) -> None:
    """AIM-08: moving any chunk to the position it already occupies raises
    InvalidOperation from BOTH move_chunk and propose_move (the proposal op
    mirrors the direct op), leaving the document untouched — no event, no
    pending card."""
    doc, cid, container, current_after = data
    before = doc.dumps()
    seq = doc.seq
    with pytest.raises(aim.InvalidOperation):
        doc.propose_move(cid, author=ME, container=container, after=current_after, at=ts(60))
    with pytest.raises(aim.InvalidOperation):
        doc.move_chunk(cid, author=ME, container=container, after=current_after, at=ts(61))
    is_last = (
        cid == doc.body_ids[-1]
        if container == "body"
        else cid == [c.id for c in doc.chunks if c.container == "lst"][-1]
    )
    if is_last:  # the LAST-anchor spelling of the same no-op
        with pytest.raises(aim.InvalidOperation):
            doc.propose_move(cid, author=ME, container=container, at=ts(62))
    assert doc.dumps() == before
    assert doc.seq == seq
    assert doc.proposals == []
