#!/usr/bin/env python3
"""Regenerate the conformance fixtures in tests/fixtures/.

One file per rule: ``ok_*.aim`` must lint clean (no errors); ``nok_<CODE>_*``
must trigger exactly that rule code. The ok files are built through the SDK
(so they are canonical by construction); the nok files are ok files with one
surgical, human-readable defect. Third-party implementations can point their
own verifier at this directory — the names encode the expectation.

Run from the repo root:  python3 scripts/gen_fixtures.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

import aimformat as aim  # noqa: E402

OUT = pathlib.Path(__file__).parent.parent / "tests" / "fixtures"
BOT = aim.agent("claude-opus-4-8")
ME = aim.human("ada")


def t(i: int) -> str:
    return f"2026-07-07T12:{i // 60:02d}:{i % 60:02d}Z"


def base_doc() -> aim.AimDocument:
    doc = aim.new_document(title="Conformance fixture", theme={"--aim-brand-1": "#1a73e8"})
    doc.add_chunk('<h1 data-aim="h1" class="font-bold text-3xl">Fixture</h1>', author=BOT, at=t(0))
    doc.add_chunk('<p data-aim="p1">One paragraph &amp; some text.</p>', author=BOT, at=t(1))
    doc.add_chunk(
        '<ul data-aim-container="l1"><li data-aim="i1">First</li>'
        '<li data-aim="i2">Run…</li><li data-aim="i2">…run</li></ul>',
        author=BOT,
        at=t(2),
    )
    return doc


def _historyless(doc: aim.AimDocument) -> str:
    """The document with no history block at all — the shape a page export
    or a hand-written file has."""
    doc._drop_history(drop_embeddings=True)
    return doc.dumps()


def _stale_toc(text: str) -> str:
    """A TOC whose recorded hash no longer matches (a foreign writer)."""
    import re

    return re.sub(
        r'"toc_doc_hash":"sha256:[0-9a-f]+"', '"toc_doc_hash":"sha256:' + "0" * 64 + '"', text
    )


def _with_history(doc: aim.AimDocument, events: list[dict]) -> str:
    """*doc* serialized with its history block replaced by *events* (each
    a canonical event dict) — for nok files whose defect IS the log."""
    from aimformat.canonical import canonical_json

    text = doc.dumps()
    lines = "\n".join(canonical_json(e) for e in events)
    block = f'<script type="application/aim-history+jsonl">\n{lines}\n</script>'
    marker = '<script type="application/aim-history+jsonl">'
    if marker not in text:  # no history block yet: it goes last in <body>
        return text.replace("</body>", block + "\n</body>", 1)
    start = text.index(marker)
    end = text.index("</script>", start) + len("</script>")
    return text[:start] + block + text[end:]


def _baseline_docs() -> dict[str, str]:
    """The G2 nok files: a baseline in the wrong place, a snapshot whose
    hash or content is wrong, a baseline under an older declaration."""
    from aimformat.document import snapshot_hash

    out: dict[str, str] = {}

    # H007: a valid baseline appended after ordinary edits
    doc = aim.new_document(title="Baseline fixture")
    doc.add_chunk('<p data-aim="p1">One.</p>', author=ME, at=t(0))
    doc.add_chunk('<p data-aim="p2">Two.</p>', author=ME, at=t(1))
    events = [e.data for e in doc.history]
    snap = doc._state.snapshot()
    late = {
        "seq": 3,
        "kind": "baseline",
        "t": t(2),
        "label": "late",
        "doc_hash": snapshot_hash(snap),
        "snapshot": snap,
    }
    out["nok_H007_baseline_not_first.aim"] = _with_history(doc, events + [late])

    # H008: a snapshot entry carrying an event handler — the live body was
    # cleaned by a later recorded edit, so the chain itself verifies
    doc = aim.new_document(title="Baseline fixture")
    doc.add_chunk('<p data-aim="p1">One.</p>', author=ME, at=t(0))
    doc._drop_history(drop_embeddings=False)
    dirty = '<p data-aim="p1" onclick="steal()">One.</p>'
    snap = doc._state.snapshot()
    snap["body"] = [dirty]
    origin = {
        "seq": 1,
        "kind": "baseline",
        "t": t(1),
        "label": "import",
        "doc_hash": snapshot_hash(snap),
        "snapshot": snap,
    }
    clean = {
        "seq": 2,
        "kind": "direct_edit",
        "t": t(2),
        "target": "p1",
        "action": "modify",
        "before": dirty,
        "after": '<p data-aim="p1">One.</p>',
        "author": ME.to_obj(),
        "batch": "b1",
    }
    out["nok_H008_snapshot_event_handler.aim"] = _with_history(doc, [origin, clean])

    # S034: a baseline retained under a 0.5 declaration
    doc = aim.new_document(title="Baseline fixture")
    doc.add_chunk('<p data-aim="p1">One.</p>', author=ME, at=t(0))
    doc._drop_history(drop_embeddings=False)
    doc._state.set_spec_version("0.5")
    snap = doc._state.snapshot()
    origin = {
        "seq": 1,
        "kind": "baseline",
        "t": t(1),
        "label": "import",
        "doc_hash": snapshot_hash(snap),
        "snapshot": snap,
    }
    out["nok_S034_baseline_under_prior_version.aim"] = _with_history(doc, [origin])

    # H009 (warning): a construct the seq-1 log never recorded
    doc = aim.new_document(title="Unrecorded origin")
    doc.add_chunk('<p data-aim="p1">Recorded.</p>', author=ME, at=t(0))
    out["nok_H009_unrecorded_origin.aim"] = doc.dumps().replace(
        '<p data-aim="p1">Recorded.</p>',
        '<p data-aim="p0">Never recorded.</p>\n<p data-aim="p1">Recorded.</p>',
    )
    return out


def _pending_delete_doc() -> str:
    doc = base_doc()
    doc.propose_delete("i1", author=BOT, explanation="Trim.", at=t(3))
    return doc.dumps()


def _duplicate_move_doc() -> str:
    # two pending moves of one target can only be foreign-authored (§5.4:
    # a new move supersedes the pending one), so retarget the second card
    doc = base_doc()
    doc.propose_move("p1", author=BOT, container="body", after="l1", at=t(3))
    m2 = doc.propose_move("h1", author=BOT, container="body", after="p1", at=t(4))
    doc._card_el(m2.id).set("data-for", "p1")
    return doc.dumps()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.aim"):
        old.unlink()

    files: dict[str, str] = {}

    # -- ok --------------------------------------------------------------
    files["ok_minimal.aim"] = aim.new_document(title="Minimal").dumps()

    doc = base_doc()
    files["ok_document.aim"] = doc.dumps()

    lifecycle = base_doc()
    p = lifecycle.propose_modify(
        "p1", '<p data-aim="p1">Better text.</p>', author=BOT, explanation="Tighter.", at=t(3)
    )
    lifecycle.accept(p.id, decided_by=ME, at=t(4))
    rej = lifecycle.propose_delete("i1", author=BOT, explanation="Redundant.", at=t(5))
    lifecycle.reject(rej.id, decided_by=ME, at=t(6))
    lifecycle.propose_add(
        "<p>Pending addition.</p>", author=ME, after="p1", explanation="More context.", at=t(7)
    )
    lifecycle.checkpoint("reviewed", at=t(8))
    files["ok_lifecycle.aim"] = lifecycle.dumps()

    deck = aim.new_document(title="Deck fixture")
    deck.add_chunk(
        '<aim-slide data-aim-container="s1" '
        'style="width:960px; height:540px">'
        '<h2 data-aim="t1" class="font-bold text-5xl" '
        'style="left:60px; top:50px; width:600px; z-index:2">'
        "Slide one</h2>"
        '<p data-aim="b1" class="text-2xl" '
        'style="left:60px; top:150px; width:600px">Body</p>'
        "</aim-slide>",
        author=BOT,
        at=t(0),
    )
    files["ok_slides.aim"] = deck.dumps()

    flat = base_doc()
    flat.flatten(at=t(9))  # one anchoring checkpoint: a pruned log (H004)
    files["ok_flattened.aim"] = flat.dumps()

    files["ok_historyless.aim"] = _historyless(base_doc())  # H001 only

    imported = aim.new_document(title="Imported fixture")
    for markup in ('<h1 data-aim="t1">Imported</h1>', '<p data-aim="p1">From a file.</p>'):
        imported.add_chunk(markup, author=aim.external("docx-import"), at=t(0))
    imported._drop_history(drop_embeddings=False)  # the import's scaffolding
    imported.baseline("import", author=aim.external("docx-import"), explanation="Imported", at=t(1))
    imported.generate_toc()
    imported.add_chunk('<p data-aim="p2">Edited after import.</p>', author=ME, at=t(2))
    files["ok_baseline.aim"] = imported.dumps()

    self_closing_exceptions = aim.new_document(title="Self-closing exceptions")
    self_closing_exceptions.add_chunk(
        '<p data-aim="syntax"><span></span><br><svg><use href="#asset-unused"/></svg></p>',
        author=BOT,
        at=t(0),
    )
    files["ok_self_closing_exceptions.aim"] = _historyless(self_closing_exceptions)

    painted = aim.new_document(title="Literal paint fixture")
    painted.add_chunk(
        '<h1 data-aim="ttl" style="color:#ff69b4">Pink title</h1>', author=BOT, at=t(0)
    )
    painted.add_chunk(
        '<p data-aim="tint" style="background-color:#fff1f7">Tinted.</p>', author=BOT, at=t(1)
    )
    painted.add_chunk(
        '<p data-aim="callout" class="border" style="border-color:#ff69b4">'
        'Callout with <span style="color:#ff69b4">one painted run</span>.</p>',
        author=BOT,
        at=t(2),
    )
    files["ok_paint.aim"] = painted.dumps()

    # A previous-version document, still conforming under this toolkit: a
    # 0.3 tool understands 0.2, so neither the version marker nor the
    # stylesheet stamp is a finding (S002/S006 accept older).
    prior = (
        base_doc()
        .dumps()
        .replace(f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.2"')
        .replace(f'data-aim-css="{aim.SPEC_VERSION}"', 'data-aim-css="0.2"')
    )
    files["ok_prior_version.aim"] = prior

    # …and the same document after paint entered it: the version marker moved
    # and the history says so, on the reserved target aim:version (§3.7).
    upgraded = aim.loads(prior)
    upgraded.modify_chunk(
        "h1",
        '<h1 data-aim="h1" class="font-bold text-3xl" style="color:#ff69b4">Fixture</h1>',
        author=ME,
        at=t(4),
    )
    files["ok_version_upgrade.aim"] = upgraded.dumps()

    paginated = base_doc()
    paginated.set_page_setup(
        {
            "size": "A4",
            "orientation": "portrait",
            "margins": {"top": "20mm", "right": "18mm", "bottom": "20mm", "left": "18mm"},
        },
        author=ME,
        at=t(3),
    )
    paginated.add_chunk("<aim-page-break></aim-page-break>", author=ME, after="p1", at=t(4))
    files["ok_pagination.aim"] = paginated.dumps()

    # -- nok: one rule per file ------------------------------------------
    # Derived from a HISTORY-LESS base wherever possible, so a surgical body
    # defect cannot co-fire history-chain errors (H006) — each nok file
    # must trip exactly its named code and nothing else. (flatten() keeps
    # an anchoring checkpoint since v0.6, which any body edit would trip.)
    flat = _historyless(base_doc())
    life = files["ok_lifecycle.aim"]

    pag_doc = base_doc()
    pag_doc.set_page_setup(
        {
            "size": "A4",
            "orientation": "portrait",
            "margins": {"top": "20mm", "right": "18mm", "bottom": "20mm", "left": "18mm"},
        },
        author=ME,
        at=t(3),
    )
    pag_flat = _historyless(pag_doc)

    nok = {
        "nok_S001_missing_version.aim": flat.replace(f' data-aim-version="{aim.SPEC_VERSION}"', ""),
        "nok_S003_missing_charset.aim": flat.replace('<meta charset="utf-8">\n', ""),
        "nok_S004_missing_title.aim": flat.replace("<title>Conformance fixture</title>\n", ""),
        "nok_S007_body_comment.aim": flat.replace("<body>\n", "<body>\n<!-- stray -->\n"),
        "nok_S030_duplicate_note.aim": flat.replace(
            '<meta charset="utf-8">\n',
            '<meta charset="utf-8">\n<!--\naim-note: a second note\n-->\n',
        ),
        "nok_S011_uncovered_body_child.aim": flat.replace('<p data-aim="p1">', "<p>"),
        "nok_S012_chunk_and_container.aim": flat.replace(
            "</body>", '<ul data-aim="lx" data-aim-container="l9"></ul>\n</body>'
        ),
        "nok_S031_slide_as_chunk.aim": flat.replace(
            "</body>",
            '<aim-slide data-aim="sx" style="width:960px; height:540px">'
            '<h2 style="left:60px; top:50px; width:600px">T</h2></aim-slide>\n</body>',
        ),
        "nok_S032_paint_under_prior_version.aim": flat.replace(
            f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.2"'
        ).replace('<p data-aim="p1">', '<p data-aim="p1" style="color:#ff69b4">'),
        "nok_S016_id_reused_across_parents.aim": flat.replace(
            '<li data-aim="i1">First</li>', '<li data-aim="p1">First</li>'
        ),
        "nok_S017_run_not_consecutive.aim": flat.replace(
            '<li data-aim="i2">…run</li>', '<li data-aim="i9">gap</li><li data-aim="i2">…run</li>'
        ),
        "nok_S023_uncovered_item.aim": flat.replace(
            '<li data-aim="i1">First</li>', "<li>First</li>"
        ),
        "nok_S024_nested_chunk.aim": flat.replace(
            '<p data-aim="p1">One paragraph &amp; some text.</p>',
            '<section data-aim="p1"><p data-aim="p9">nested</p></section>',
        ),
        "nok_S025_stray_container_text.aim": flat.replace(
            '<li data-aim="i1">First</li>', 'STRAY<li data-aim="i1">First</li>'
        ),
        "nok_V002_unknown_element.aim": flat.replace(
            '<p data-aim="p1">One paragraph &amp; some text.</p>',
            '<blink data-aim="p1">One paragraph.</blink>',
        ),
        "nok_V005_unknown_class.aim": flat.replace(
            'class="font-bold text-3xl"', 'class="text-glow"'
        ),
        "nok_V004_arbitrary_value_class.aim": flat.replace(
            'class="font-bold text-3xl"', 'class="w-[347px]"'
        ),
        # `opacity` is genuinely outside the whitelist. `color:red` would fire
        # V008 instead since 0.3 registered the paint properties, so the
        # fixture would stop testing V007 at all.
        "nok_V007_style_outside_whitelist.aim": flat.replace(
            '<p data-aim="p1">', '<p data-aim="p1" style="opacity:.5">'
        ),
        "nok_V011_unknown_theme_slot.aim": flat.replace(
            "--aim-brand-1:#1a73e8", "--aim-accent:#1a73e8"
        ),
        # A list-marker class on a paragraph matches no generated rule, so it
        # draws nothing — silently. The counterpart (a num-N on a list) is
        # the same mistake in the other direction; one fixture per rule, and
        # this one is the direction a hand-editing author is likelier to hit.
        "nok_V013_class_on_wrong_element.aim": flat.replace(
            'class="font-bold text-3xl"', 'class="list-paren"'
        ),
        "nok_X002_event_handler.aim": flat.replace(
            '<p data-aim="p1">', '<p data-aim="p1" onmouseover="x()">'
        ),
        "nok_X004_executable_script.aim": flat.replace(
            "</body>", "<script>alert(1)</script>\n</body>"
        ),
        "nok_P008_proposal_unknown_target.aim": _pending_delete_doc().replace(
            'data-for="i1"', 'data-for="ghost"'
        ),
        "nok_P014_empty_proposals_section.aim": flat.replace(
            "</body>", "<aim-proposals>\n</aim-proposals>\n</body>"
        ),
        "nok_P018_duplicate_pending_move.aim": _duplicate_move_doc(),
        "nok_M003_malformed_meta_cache.aim": flat.replace(
            "<title>Conformance fixture</title>\n",
            "<title>Conformance fixture</title>\n"
            '<script type="application/aim-meta+json">\n'
            "{not json]\n</script>\n",
        ),
        "nok_D001_malformed_doc_settings.aim": flat.replace(
            "<title>Conformance fixture</title>\n",
            "<title>Conformance fixture</title>\n"
            '<script type="application/aim-doc+json">\n'
            "{not json]\n</script>\n",
        ),
        "nok_D002_duplicate_doc_settings.aim": pag_flat.replace(
            "<style data-aim-css=",
            '<script type="application/aim-doc+json">\n'
            '{"page":{"size":"A5"}}\n</script>\n<style data-aim-css=',
            1,
        ),
        "nok_D003_unknown_page_size.aim": pag_flat.replace('"size":"A4"', '"size":"A0"'),
        "nok_D004_margin_out_of_bounds.aim": pag_flat.replace('"top":"20mm"', '"top":"250mm"'),
        "nok_D005_page_break_not_empty.aim": flat.replace(
            '<p data-aim="p1">One paragraph &amp; some text.</p>',
            '<p data-aim="p1">One paragraph &amp; some text.</p>\n'
            '<aim-page-break data-aim="pbx">stray</aim-page-break>',
        ),
        # nested in a section chunk, NOT a list container: a ul member
        # would co-fire S022 (illegal item carrier) and break exactness
        "nok_D006_page_break_nested.aim": flat.replace(
            '<p data-aim="p1">One paragraph &amp; some text.</p>',
            '<section data-aim="s1"><h2>Heading</h2>'
            "<aim-page-break></aim-page-break></section>\n"
            '<p data-aim="p1">One paragraph &amp; some text.</p>',
        ),
        "nok_H006_history_chain_broken.aim": life.replace(
            "Better text.</p>", "Sneakily different.</p>", 1
        ),
        "nok_C001_not_canonical.aim": flat.replace(
            'class="font-bold text-3xl"', 'class="text-3xl font-bold"'
        ),
        "nok_C002_self_closing_non_void.aim": files["ok_self_closing_exceptions.aim"].replace(
            "<span></span>", "<span/>"
        ),
        "nok_M004_meta_without_summary_or_toc.aim": flat.replace(
            "<title>", '<script type="application/aim-meta+json">\n{}\n</script>\n<title>', 1
        ),
        "nok_M005_stale_toc.aim": _stale_toc(files["ok_baseline.aim"]),
        **_baseline_docs(),
    }
    files.update(nok)

    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(files)} fixtures to {OUT}")

    # sanity: every ok_* is clean; every nok_* trips EXACTLY its code
    # (warning-level codes must fire without introducing any error)
    levels = aim.REGISTRY.raw["lint_rules"]
    bad = 0
    for name in sorted(files):
        findings = aim.lint_text((OUT / name).read_text())
        errors = {f.code for f in findings if f.level == "error"}
        if name.startswith("ok_") and errors:
            print(f"  UNEXPECTED errors in {name}: {errors}")
            bad += 1
        if name.startswith("nok_"):
            want = name.split("_")[1]
            want_errors = {want} if levels[want][0] == "error" else set()
            fired = {f.code for f in findings}
            if want not in fired or errors != want_errors:
                print(
                    f"  {name}: expected {want} (errors {want_errors}), "
                    f"got fired={fired} errors={errors}"
                )
                bad += 1
    print("fixture sanity:", "OK" if not bad else f"{bad} problems")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
