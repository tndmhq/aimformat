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


def _pending_delete_doc() -> str:
    doc = base_doc()
    doc.propose_delete("i1", author=BOT, explanation="Trim.", at=t(3))
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
    flat.flatten()
    files["ok_flattened.aim"] = flat.dumps()

    self_closing_exceptions = aim.new_document(title="Self-closing exceptions")
    self_closing_exceptions.add_chunk(
        '<p data-aim="syntax"><span></span><br><svg><use href="#asset-unused"/></svg></p>',
        author=BOT,
        at=t(0),
    )
    self_closing_exceptions.flatten()
    files["ok_self_closing_exceptions.aim"] = self_closing_exceptions.dumps()

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
    # Derived from a FLATTENED base wherever possible, so a surgical body
    # defect cannot co-fire history-chain errors (H006) — each nok file
    # must trip exactly its named code and nothing else.
    flat_doc = base_doc()
    flat_doc.flatten()
    flat = flat_doc.dumps()
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
    pag_doc.flatten()
    pag_flat = pag_doc.dumps()

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
        "nok_V007_style_outside_whitelist.aim": flat.replace(
            '<p data-aim="p1">', '<p data-aim="p1" style="color:red">'
        ),
        "nok_V011_unknown_theme_slot.aim": flat.replace(
            "--aim-brand-1:#1a73e8", "--aim-accent:#1a73e8"
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
