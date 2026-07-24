#!/usr/bin/env python3
"""Regenerate the cross-implementation parity fixtures.

Edge-focused .aim documents under ``tests/parity/fixtures/``, built through
the SDK so they are canonical and lint-clean by construction (like
``gen_examples.py``). The shipped ``examples/*.aim`` cover the happy paths;
these target the constructs a second implementation is most likely to get
wrong: runs, nested containers, every proposal action, theme/doc blocks,
packed assets, unicode/whitespace edges, and a flattened file.

A second tier, ``noncanonical-*.aim``, is deliberately NOT lint-clean:
SDK-built documents with deterministic string edits that simulate malformed
hand-editing (duplicate attributes, self-closing non-void tags,
semicolonless character references, Unicode-digit margins, duplicate
chunk ids). Both readers must
still agree on them — where Python decodes/normalizes, TS must project
identically — so they get goldens like every other source, but they are
exempt from the lint-clean check.

Run from the repo root: python3 scripts/gen_parity_fixtures.py
Then refresh the goldens: python3 scripts/dump_projection.py

Chunk ids are pinned in the payloads, but proposal ids are tool-assigned
(random, like ``gen_examples.py``), so regenerating rewrites them —
always regenerate fixtures and goldens together and review the diff.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

import aimformat as aim  # noqa: E402

OUT = pathlib.Path(__file__).parent.parent / "tests" / "parity" / "fixtures"
BOT = aim.agent("model-x")
ME = aim.human("ada")

# 1×1 transparent PNG, base64 (decodes cleanly; content-addresses stably)
PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def t(i: int) -> str:
    return f"2026-07-17T10:{i // 60:02d}:{i % 60:02d}Z"


def runs_doc() -> aim.AimDocument:
    """Runs: sibling li/tr sharing one id; a multi-block section; pre."""
    doc = aim.new_document(title="Runs — lists, tables, multi-block chunks")
    with doc.batch():
        doc.add_chunk('<h2 data-aim="hd">Items</h2>', author=BOT, at=t(0))
        doc.add_chunk(
            '<ul data-aim-container="lst">'
            '<li data-aim="i1">First</li>'
            '<li data-aim="i2">Second, spilling over…</li>'
            '<li data-aim="i2">…into a second bullet</li>'
            '<li data-aim="i3">Third</li></ul>',
            author=BOT,
            at=t(1),
        )
        doc.add_chunk(
            '<table data-aim-container="tbl"><thead>'
            '<tr data-aim="h0"><th>Key</th><th>Value</th></tr></thead><tbody>'
            '<tr data-aim="b1"><td>alpha</td><td>1</td></tr>'
            '<tr data-aim="b1"><td>alpha (cont.)</td><td>2</td></tr>'
            '<tr data-aim="b2"><td>beta</td><td>3</td></tr>'
            "</tbody></table>",
            author=BOT,
            at=t(2),
        )
        doc.add_chunk(
            '<section data-aim="sec"><h3>Grouped</h3>'
            "<p>A multi-block chunk under one grouping element.</p></section>",
            author=BOT,
            at=t(3),
        )
        doc.add_chunk(
            '<pre data-aim="code"><code>def f():\n    return 42\n</code></pre>',
            author=BOT,
            at=t(4),
        )
    return doc


def nested_slides_doc() -> aim.AimDocument:
    """Nested containers: slide → list → items, slide → table → rows."""
    doc = aim.new_document(title="Nested containers in slides")
    with doc.batch():
        doc.add_chunk(
            '<aim-slide data-aim-container="s1" style="width:960px; height:540px">'
            '<h2 data-aim="t1" class="font-bold text-5xl" '
            'style="left:48px; top:32px; width:600px; z-index:2">Agenda</h2>'
            '<ul data-aim-container="lst1" style="left:48px; top:150px; width:520px">'
            '<li data-aim="a1">Kickoff</li><li data-aim="a2">Numbers</li></ul>'
            "</aim-slide>",
            author=BOT,
            at=t(0),
        )
        doc.add_chunk('<aim-page-break data-aim="pgb1"></aim-page-break>', author=BOT, at=t(1))
        doc.add_chunk(
            '<aim-slide data-aim-container="s2" style="width:960px; height:540px">'
            '<h2 data-aim="t2" style="left:48px; top:32px; width:600px">Numbers</h2>'
            '<table data-aim-container="tb2" style="left:48px; top:140px; width:640px">'
            '<tbody><tr data-aim="n1"><td>Q1</td><td>12</td></tr>'
            '<tr data-aim="n2"><td>Q2</td><td>17</td></tr></tbody></table>'
            '<p data-aim="fn" class="text-gray-600 text-sm" '
            'style="left:48px; top:480px; width:640px; z-index:1">Unaudited.</p>'
            "</aim-slide>",
            author=BOT,
            at=t(2),
        )
    return doc


def paint_doc() -> aim.AimDocument:
    """Literal per-element paint: block, inline span, mixed slide geometry
    and paint, and a paint-only pending payload."""
    doc = aim.new_document(title="Literal paint — colour, background, border")
    with doc.batch():
        doc.add_chunk(
            '<h1 data-aim="ttl" class="font-bold" style="color:#ff69b4">Pink title</h1>',
            author=BOT,
            at=t(0),
        )
        doc.add_chunk(
            '<p data-aim="tint" style="background-color:#fff1f7">Tinted paragraph.</p>',
            author=BOT,
            at=t(1),
        )
        doc.add_chunk(
            '<p data-aim="callout" class="border" style="border-color:#ff69b4">Callout.</p>',
            author=BOT,
            at=t(2),
        )
        doc.add_chunk(
            '<p data-aim="run">One <span style="color:#ff69b4">painted run</span> only.</p>',
            author=BOT,
            at=t(3),
        )
        doc.add_chunk(
            '<aim-slide data-aim-container="s1" style="width:960px; height:540px">'
            '<h2 data-aim="st" class="text-5xl" '
            'style="left:48px; top:32px; width:450px; color:#ff69b4">Slide title</h2>'
            '<p data-aim="sb" style="left:48px; top:150px; width:600px; '
            'background-color:#fff1f7; border-color:#ff69b4">Body.</p>'
            "</aim-slide>",
            author=BOT,
            at=t(4),
        )
    doc.propose_modify(
        "tint",
        '<p data-aim="tint" style="background-color:#f0fdf4">Tinted paragraph.</p>',
        author=BOT,
        explanation="Green tint instead.",
        at=t(10),
    )
    return doc


def proposals_doc() -> aim.AimDocument:
    """Every proposal action, chained adds, shells, and dependencies."""
    doc = aim.new_document(title="Pending lane — every action")
    with doc.batch():
        doc.add_chunk('<h1 data-aim="c1">Title</h1>', author=BOT, at=t(0))
        doc.add_chunk('<p data-aim="c2">Second.</p>', author=BOT, at=t(1))
        doc.add_chunk('<p data-aim="c3">Third.</p>', author=BOT, at=t(2))
        doc.add_chunk('<p data-aim="c4">Fourth.</p>', author=BOT, at=t(3))
        doc.add_chunk(
            '<table data-aim-container="tb"><tbody>'
            '<tr data-aim="r1"><td>one</td></tr>'
            '<tr data-aim="r2"><td>two</td></tr></tbody></table>',
            author=BOT,
            at=t(4),
        )
    p_mod = doc.propose_modify(
        "c2",
        '<p data-aim="c2">Second, sharpened.</p>',
        author=BOT,
        explanation="Lead with the point.",
        at=t(10),
    )
    doc.propose_delete("c3", author=BOT, explanation="Redundant with c2.", at=t(11))
    doc.propose_move("c4", author=ME, container="body", after=None, at=t(12))
    add1 = doc.propose_add(
        '<p data-aim="add1">Inserted after the title.</p>',
        author=BOT,
        container="body",
        after="c1",
        explanation="Bridge paragraph.",
        at=t(13),
    )
    doc.propose_add(
        '<p data-aim="add2">Chained onto the pending add.</p>',
        author=BOT,
        container="body",
        after=add1.id,
        at=t(14),
    )
    doc.propose_add(
        '<tr data-aim="add3"><td>three</td></tr>',
        author=ME,
        container="tb",
        after=None,
        explanation="Row at the top of the body section.",
        at=t(15),
    )
    doc.propose_theme(
        {"--aim-brand-1": "#0f766e"},
        author=BOT,
        explanation="Teal primary.",
        depends_on=p_mod.id,
        at=t(16),
    )
    doc.propose_page_setup(
        {
            "size": "A5",
            "orientation": "landscape",
            "margins": {"top": "10mm", "right": "12mm", "bottom": "10mm", "left": "12mm"},
        },
        author=ME,
        explanation="Booklet trim.",
        at=t(17),
    )
    return doc


def theme_doc_meta_doc() -> aim.AimDocument:
    """Theme block, aim:doc settings, meta cache, embeddings, checkpoint."""
    doc = aim.new_document(
        title="Head state — theme, settings, caches",
        theme={"--aim-brand-1": "#1a73e8", "--aim-font-heading": "Georgia, serif"},
    )
    with doc.batch():
        doc.add_chunk('<h1 data-aim="ttl">Head state</h1>', author=BOT, at=t(0))
        doc.add_chunk(
            '<p data-aim="body1">One paragraph to summarize and embed.</p>',
            author=BOT,
            at=t(1),
        )
    doc.set_page_setup(
        {
            "size": "Letter",
            "orientation": "portrait",
            "margins": {"top": "25.4mm", "right": "19mm", "bottom": "25.4mm", "left": "19mm"},
        },
        author=ME,
        at=t(2),
    )
    doc.checkpoint("draft", at=t(3))
    doc.set_summary("A one-paragraph document about head state.", model="model-x")
    doc.generate_toc()
    doc.set_embedding("body1", model="embed-model", vec=[0.25, -0.5, 0.125])
    return doc


def assets_doc() -> aim.AimDocument:
    """Packed assets: data-URI images hoisted into the registry."""
    doc = aim.new_document(title="Assets — packed registry")
    with doc.batch():
        doc.add_chunk(
            '<figure data-aim="fig1">'
            f'<img alt="A dot — été 🎨" src="data:image/png;base64,{PNG_1PX}">'
            "<figcaption>Packed into the registry.</figcaption></figure>",
            author=BOT,
            at=t(0),
        )
        doc.add_chunk(
            '<p data-aim="p1">Same blob again: '
            f'<img alt="dot twice" src="data:image/png;base64,{PNG_1PX}"> deduplicated.</p>',
            author=BOT,
            at=t(1),
        )
        doc.add_chunk(
            '<figure data-aim="fig2"><img alt="External, authoring form" '
            'src="https://example.com/chart.png"></figure>',
            author=BOT,
            at=t(2),
        )
    doc.pack_assets(author=ME, at=t(3))
    return doc


def unicode_whitespace_doc() -> aim.AimDocument:
    """Unicode (astral, CJK, NBSP), escapes, significant whitespace."""
    doc = aim.new_document(title="Unicode & whitespace — café, 文書, 🎉")
    with doc.batch():
        doc.add_chunk('<h1 data-aim="ttl">Καφές &amp; crème brûlée — 🎉</h1>', author=BOT, at=t(0))
        doc.add_chunk(
            '<p data-aim="esc">1 &lt; 2 &amp; 3 &gt; 2, non-breaking space, 中文文書.</p>',
            author=BOT,
            at=t(1),
        )
        doc.add_chunk(
            '<p data-aim="inline">  Leading spaces, <strong>bold&amp;co</strong>, '
            "<em>emphasis</em>, trailing spaces.  </p>",
            author=BOT,
            at=t(2),
        )
        doc.add_chunk(
            '<pre data-aim="pre1"><code>line one\n\tindented\n  spaced\n</code></pre>',
            author=BOT,
            at=t(3),
        )
        doc.add_chunk(
            '<blockquote data-aim="q1"><p>Links: '
            '<a href="https://example.com/a?b=1&amp;c=2">query</a>, '
            '<a href="mailto:luca@example.com">mail</a>, '
            '<a href="#aim:ttl">fragment</a>.</p></blockquote>',
            author=BOT,
            at=t(4),
        )
        doc.add_chunk(
            '<figure data-aim="alt1"><img alt="say &quot;hi&quot; — 1 &lt; 2" '
            'src="https://example.com/x.png"></figure>',
            author=BOT,
            at=t(5),
        )
    return doc


def unicode_attrs_doc() -> aim.AimDocument:
    """Non-ASCII ``data-x-*`` attribute names: canonical attribute order is
    *code-point* order (Python ``sorted``), which diverges from UTF-16
    code-unit order exactly when a BMP char above U+D7FF (here U+F8FF,
    private use) meets an astral char (here U+1F600, a surrogate pair) —
    a UTF-16 comparator puts the astral name first and changes the hash."""
    doc = aim.new_document(title="Unicode attribute names — code-point order")
    with doc.batch():
        doc.add_chunk(
            '<p data-aim="p1" data-x-\U0001f600="astral" data-x-="bmp-pua" '
            'data-x-zeta="ascii">Attribute ordering.</p>',
            author=BOT,
            at=t(0),
        )
        doc.add_chunk(
            '<p data-aim="p2" data-x-="bmp-pua" data-x-\U0001f600="astral">'
            "Authored in the other order.</p>",
            author=BOT,
            at=t(1),
        )
    return doc


def flattened_doc() -> aim.AimDocument:
    """A flattened file: no history trailer (H001 warning tier)."""
    doc = aim.new_document(title="Flattened")
    with doc.batch():
        doc.add_chunk('<h1 data-aim="ttl">Flattened</h1>', author=BOT, at=t(0))
        doc.add_chunk('<p data-aim="p1">History was dropped.</p>', author=BOT, at=t(1))
    p = doc.propose_modify(
        "p1", '<p data-aim="p1">History has been dropped.</p>', author=BOT, at=t(2)
    )
    doc.accept(p.id, decided_by=ME, at=t(3))
    doc.flatten()
    return doc


def empty_registry_doc() -> aim.AimDocument:
    """An asset registry present but empty — the degenerate packed form."""
    doc = aim.new_document(title="Empty asset registry")
    with doc.batch():
        doc.add_chunk('<p data-aim="p1">No assets are packed.</p>', author=BOT, at=t(0))
    # the public write path garbage-collects an empty registry away, so the
    # degenerate shape is materialized directly for reader coverage
    doc._assets_section()
    return doc


# --------------------------------------------------------------------------
# non-canonical tier: hand-editing simulated by deterministic string edits


def _edited(doc: aim.AimDocument, *edits: tuple[str, str]) -> str:
    """The document's text with each edit applied exactly once, verified to
    still load — these are reader-parity fixtures, not nok parse cases."""
    text = doc.dumps()
    for old, new in edits:
        if text.count(old) != 1:
            raise SystemExit(f"edit target occurs {text.count(old)}× (want 1): {old!r}")
        text = text.replace(old, new)
    aim.loads(text)
    return text


def noncanonical_dup_attrs_text() -> str:
    """Duplicate attributes: HTML first-wins semantics (Element.get and the
    canonical serializer agree on the FIRST value; docHash follows)."""
    doc = aim.new_document(title="Non-canonical — duplicate attributes")
    with doc.batch():
        doc.add_chunk('<p data-aim="p1" class="zz">First attribute wins.</p>', author=BOT, at=t(0))
    doc.flatten()
    return _edited(
        doc,
        (
            '<p data-aim="p1" class="zz">',
            '<p data-aim="p1" data-aim="p9" class="b a" class="zz" title="one" title="two">',
        ),
    )


def noncanonical_self_closing_text() -> str:
    """Non-void elements written self-closing: parsed as empty, serialized
    back open+close; a void element's stray slash is dropped either way."""
    doc = aim.new_document(title="Non-canonical — self-closing spellings")
    with doc.batch():
        doc.add_chunk('<p data-aim="sc">PLACEHOLDER</p>', author=BOT, at=t(0))
        doc.add_chunk(
            '<p data-aim="wrap">Inline <span class="x">SPAN</span> content.</p>',
            author=BOT,
            at=t(1),
        )
        doc.add_chunk(
            '<figure data-aim="fig"><img alt="dot" src="https://example.com/x.png">'
            "<figcaption>A void element.</figcaption></figure>",
            author=BOT,
            at=t(2),
        )
    doc.flatten()
    return _edited(
        doc,
        ('<p data-aim="sc">PLACEHOLDER</p>', '<p data-aim="sc"/>'),
        ('<span class="x">SPAN</span>', '<span class="x"/>'),
        (
            '<img alt="dot" src="https://example.com/x.png">',
            '<img alt="dot" src="https://example.com/x.png"/>',
        ),
    )


def noncanonical_charrefs_text() -> str:
    """Semicolonless character references, decoded like ``html.unescape``:
    numeric with trailing garbage, bare core names, the HTML4 legacy set,
    and longest-prefix matching — plus ``&apos`` which HTML never resolves
    bare and so stays literal.

    Every case here must decode identically on all supported CPythons.
    In particular, a semicolonless named reference followed by an
    alphanumeric inside an ATTRIBUTE (e.g. ``&quothi``) is off-limits:
    ``HTMLParser`` stopped decoding that spelling in Python 3.13
    (matching the HTML5 attribute rule), so pinning it would make the
    golden Python-version-dependent."""
    doc = aim.new_document(title="Non-canonical — semicolonless references")
    with doc.batch():
        doc.add_chunk('<p data-aim="c1">TEXT_REFS</p>', author=BOT, at=t(0))
        doc.add_chunk(
            '<p data-aim="c2" title="ATTR_REFS">Attribute references.</p>', author=BOT, at=t(1)
        )
    doc.flatten()
    return _edited(
        doc,
        (
            "TEXT_REFS",
            "A&#65b, amp &amp b, &copy 2026, hex &#x41z, "
            "prefix &ampx, dropped &#6a;, literal &apos b",
        ),
        ("ATTR_REFS", "1 &lt 2 &amp 3"),
    )


def noncanonical_unicode_margins_text() -> str:
    """Margins spelled with non-ASCII Unicode decimal digits: Python's
    ``re`` ``\\d`` and ``float()`` are Nd-aware, so ``"١٥mm"`` is
    grammar-valid and resolves to 15 — including an astral-plane digit
    (``𝟝``, U+1D7DD) from the mathematical block, where 0-9 decades sit
    adjacent to each other."""
    doc = aim.new_document(title="Non-canonical — Unicode-digit margins")
    with doc.batch():
        doc.add_chunk(
            '<p data-aim="m1">Margins written in other digit scripts.</p>',
            author=BOT,
            at=t(0),
        )
    doc.set_page_setup(
        {
            "size": "A4",
            "orientation": "portrait",
            "margins": {"top": "15mm", "right": "19mm", "bottom": "12.7mm", "left": "5mm"},
        },
        author=ME,
        at=t(1),
    )
    doc.flatten()
    return _edited(
        doc,
        ('"top":"15mm"', '"top":"١٥mm"'),  # Arabic-Indic 15
        ('"right":"19mm"', '"right":"१९mm"'),  # Devanagari 19
        ('"bottom":"12.7mm"', '"bottom":"١٢.٧mm"'),  # Arabic-Indic 12.7
        ('"left":"5mm"', '"left":"𝟝mm"'),  # mathematical double-struck 5
    )


def noncanonical_dup_ids_text() -> str:
    """One chunk id repeated across containers (S016): each container's
    member view stays LOCAL — the second container shows its own html/text,
    including a local run — while the flat chunk list keeps the first hit."""
    doc = aim.new_document(title="Non-canonical — duplicate chunk ids")
    with doc.batch():
        doc.add_chunk('<h2 data-aim="hd">Duplicated ids</h2>', author=BOT, at=t(0))
        doc.add_chunk(
            '<ul data-aim-container="l1"><li data-aim="dup">First list item.</li></ul>',
            author=BOT,
            at=t(1),
        )
        doc.add_chunk(
            '<ul data-aim-container="l2"><li data-aim="d2a">Second list, spilling…</li>'
            '<li data-aim="d2b">…into a second bullet.</li></ul>',
            author=BOT,
            at=t(2),
        )
        doc.add_chunk(
            '<table data-aim-container="tb"><tbody>'
            '<tr data-aim="d3"><td>A table row too.</td></tr></tbody></table>',
            author=BOT,
            at=t(3),
        )
    doc.flatten()
    return _edited(
        doc,
        ('data-aim="d2a"', 'data-aim="dup"'),
        ('data-aim="d2b"', 'data-aim="dup"'),
        ('data-aim="d3"', 'data-aim="dup"'),
    )


def noncanonical_dup_top_level_text() -> str:
    """A nested chunk id reused by a LATER top-level construct (S016):
    the public chunk list keeps the FIRST (nested) hit's html/text, but
    ``container_of_chunk`` consults ``top_index`` before the hit's ancestry
    — a top-level construct carrying the id (as chunk id ``dup``, or even
    as CONTAINER id ``dup2``) pins the chunk's container to "body".
    Per-container member views stay local (the c1/c2 members keep their
    own container)."""
    doc = aim.new_document(title="Non-canonical — top-level id shadowing")
    with doc.batch():
        doc.add_chunk(
            '<section data-aim-container="c1"><p data-aim="n1">Nested first.</p></section>',
            author=BOT,
            at=t(0),
        )
        doc.add_chunk('<p data-aim="t1">Top-level second.</p>', author=BOT, at=t(1))
        doc.add_chunk(
            '<section data-aim-container="c2"><p data-aim="n2">Nested other.</p></section>',
            author=BOT,
            at=t(2),
        )
        doc.add_chunk(
            '<div data-aim-container="t2"><p data-aim="x1">Inside the shadow.</p></div>',
            author=BOT,
            at=t(3),
        )
    doc.flatten()
    return _edited(
        doc,
        ('data-aim="n1"', 'data-aim="dup"'),
        ('data-aim="t1"', 'data-aim="dup"'),
        ('data-aim="n2"', 'data-aim="dup2"'),
        ('data-aim-container="t2"', 'data-aim-container="dup2"'),
    )


def noncanonical_rawtext_closes_text() -> str:
    """Hand-edited raw-text end tags, the version-STABLE cases only:
    ``</SCRIPT>``/``</STYLE >`` close on every CPython (the CDATA scan is
    case-insensitive) and a close-like run whose tag name continues
    (``</styleq>``) is data on every CPython. The version-dependent forms
    (``</ script>``, ``</script/>``) are pinned by TS unit tests instead —
    a golden for them could not stay byte-identical across 3.10-3.13."""
    doc = aim.new_document(title="Non-canonical — raw-text end tags")
    with doc.batch():
        doc.add_chunk('<p data-aim="r1">Raw-text close spellings.</p>', author=BOT, at=t(0))
    doc.set_page_setup(
        {
            "size": "A4",
            "orientation": "portrait",
            "margins": {"top": "20mm", "right": "20mm", "bottom": "20mm", "left": "20mm"},
        },
        author=ME,
        at=t(1),
    )
    doc.flatten()
    return _edited(
        doc,
        ("</style>", "/* a fake close </styleq> stays raw */</STYLE >"),
        ("</script>", "</SCRIPT>"),
    )


FIXTURES = {
    "runs": runs_doc,
    "paint": paint_doc,
    "nested-slides": nested_slides_doc,
    "proposals": proposals_doc,
    "theme-doc-meta": theme_doc_meta_doc,
    "assets": assets_doc,
    "unicode-whitespace": unicode_whitespace_doc,
    "unicode-attrs": unicode_attrs_doc,
    "flattened": flattened_doc,
    "empty-registry": empty_registry_doc,
}

# intentionally NOT lint-clean (see module docstring); builders return text
NONCANONICAL = {
    "noncanonical-dup-attrs": noncanonical_dup_attrs_text,
    "noncanonical-self-closing": noncanonical_self_closing_text,
    "noncanonical-charrefs": noncanonical_charrefs_text,
    "noncanonical-unicode-margins": noncanonical_unicode_margins_text,
    "noncanonical-dup-ids": noncanonical_dup_ids_text,
    "noncanonical-dup-top-level": noncanonical_dup_top_level_text,
    "noncanonical-rawtext-closes": noncanonical_rawtext_closes_text,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in FIXTURES.items():
        doc = build()
        errors = [f for f in aim.lint(doc) if f.level == "error"]
        if errors:
            raise SystemExit(f"{name}: generated fixture fails lint: {errors}")
        doc.save(OUT / f"{name}.aim")
        print(f"wrote tests/parity/fixtures/{name}.aim")
    for name, build_text in NONCANONICAL.items():
        (OUT / f"{name}.aim").write_text(build_text(), "utf-8")
        print(f"wrote tests/parity/fixtures/{name}.aim (non-canonical)")


if __name__ == "__main__":
    main()
