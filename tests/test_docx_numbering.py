"""The numbering counter engine: how Word decides a clause is "1.1.11".

A Word document does not contain the string "1.1.11". It contains *"this
paragraph is item at level 2 of numbering-instance 19"*, and the label is
computed by walking the document in order. Everything here follows from
that.

**The evidence.** ``tests/fixtures/docxs/legal-addendum.docx`` declares:

===========  ==========  ==================================
``w:num``    abstract    ``w:lvlOverride``
===========  ==========  ==================================
2            16          —
19           16          levels 0-8, ``startOverride=1`` on every one
20 … 29      16          —
===========  ==========  ==================================

Its definitions list runs **1.1.1 … 1.1.10 on numId 19**, then
**1.1.11 … 1.1.14 on numId 2** — one continuous sequence in Word, across
two instances, *despite* numId 19 carrying ``startOverride=1``.

That table killed three successive heuristics:

1. count per numId (the dependency's behaviour) → restarts at 1.1.1 when
   the document switches instance;
2. alias every instance of one abstract onto a shared counter → correct
   here, but erases Word's deliberate "Restart at 1" elsewhere;
3. alias unless the instance has any ``lvlOverride`` → breaks this document
   again, because its override carries no *semantic* restart.

The rule that explains all of it, and what these tests pin:

    Counters are shared per (abstract definition, level). A
    ``startOverride`` resets that shared counter when its instance is FIRST
    ENCOUNTERED in the document — it does not open a separate sequence.

Each test below is one clause of that sentence. The three heuristics above
each pass SOME of them, which is why they are written as a set.
"""

from __future__ import annotations

import pytest

pytest.importorskip("docx_parser_converter")

from aimformat.convert._docx_seam import (  # noqa: E402
    NumberingEngine,
    format_number,
)

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _level(
    ilvl: int,
    fmt: str = "decimal",
    text: str | None = None,
    start: int = 1,
    restart: int | None = None,
) -> str:
    text = text if text is not None else ".".join(f"%{k + 1}" for k in range(ilvl + 1))
    restart_xml = f'<w:lvlRestart w:val="{restart}"/>' if restart is not None else ""
    return (
        f'<w:lvl w:ilvl="{ilvl}"><w:start w:val="{start}"/>'
        f'<w:numFmt w:val="{fmt}"/><w:lvlText w:val="{text}"/>{restart_xml}</w:lvl>'
    )


def _numbering(abstracts: str, instances: str) -> bytes:
    return f"<w:numbering {_W}>{abstracts}{instances}</w:numbering>".encode()


#: The shape of the legal fixture: one abstract definition (decimal chain),
#: a plain instance, and two instances that each carry startOverride=1.
_CLAUSES = _numbering(
    abstracts=(
        '<w:abstractNum w:abstractNumId="16">'
        + _level(0, text="%1.")
        + _level(1)
        + _level(2)
        + "</w:abstractNum>"
    ),
    instances=(
        '<w:num w:numId="2"><w:abstractNumId w:val="16"/></w:num>'
        '<w:num w:numId="19"><w:abstractNumId w:val="16"/>'
        '<w:lvlOverride w:ilvl="2"><w:startOverride w:val="1"/></w:lvlOverride></w:num>'
        '<w:num w:numId="33"><w:abstractNumId w:val="16"/>'
        '<w:lvlOverride w:ilvl="2"><w:startOverride w:val="1"/></w:lvlOverride></w:num>'
    ),
)


class TestCountersAreSharedPerDefinition:
    def test_a_sequence_continues_across_instances(self):
        # the legal fixture in miniature: ten items on numId 19, then four
        # more on numId 2 — one unbroken run, as Word draws it
        engine = NumberingEngine(_CLAUSES)
        engine.label(2, 0)
        engine.label(2, 1)
        seen = [engine.label(19, 2) for _ in range(10)]
        seen += [engine.label(2, 2) for _ in range(4)]
        assert seen == [f"1.1.{n}" for n in range(1, 15)]

    def test_counting_per_instance_would_have_restarted(self):
        # the failure this replaced, stated so the test names it: keyed by
        # instance, the switch to numId 2 would begin again at 1.1.1
        engine = NumberingEngine(_CLAUSES)
        engine.label(2, 0)
        engine.label(2, 1)
        [engine.label(19, 2) for _ in range(10)]
        assert engine.label(2, 2) == "1.1.11", "counters are keyed per instance again"


class TestStartOverrideResetsOnFirstEncounter:
    def test_a_fresh_instance_with_start_override_restarts(self):
        # Word's "Restart at 1" — the case that plain aliasing erased
        engine = NumberingEngine(_CLAUSES)
        engine.label(2, 0)
        engine.label(2, 1)
        before = [engine.label(2, 2) for _ in range(3)]
        after = [engine.label(33, 2) for _ in range(3)]
        assert before == ["1.1.1", "1.1.2", "1.1.3"]
        assert after == ["1.1.1", "1.1.2", "1.1.3"], "a deliberate restart was ignored"

    def test_seeding_a_fresh_counter_is_not_a_restart(self):
        # Word writes startOverride on fresh instances routinely, so treating
        # every pending override as a restart marks the FIRST block of a level
        # `num-restart`. Harmless until someone inserts a block before it —
        # then the document shows two clauses numbered 1.1.1, after exactly
        # the edit dynamic numbering exists to survive.
        engine = NumberingEngine(_CLAUSES)
        engine.label(2, 0)
        engine.label(2, 1)
        assert engine.draw(19, 2).restarted is False, "seeding was reported as a restart"

    def test_a_restart_over_a_running_sequence_is_reported(self):
        engine = NumberingEngine(_CLAUSES)
        engine.label(2, 0)
        engine.label(2, 1)
        [engine.label(2, 2) for _ in range(3)]  # the sequence is running
        assert engine.draw(33, 2).restarted is True

    def test_it_resets_once_not_on_every_paragraph(self):
        # "first encountered" is the whole rule: applying the override on
        # each paragraph would peg the list at 1.1.1 forever
        engine = NumberingEngine(_CLAUSES)
        engine.label(2, 0)
        engine.label(2, 1)
        assert [engine.label(19, 2) for _ in range(4)] == [
            "1.1.1",
            "1.1.2",
            "1.1.3",
            "1.1.4",
        ]

    def test_a_later_instance_continues_from_the_reset(self):
        # 19 seeds the shared counter, 2 carries on from it — neither
        # restarting nor ignoring the override
        engine = NumberingEngine(_CLAUSES)
        engine.label(2, 0)
        engine.label(2, 1)
        [engine.label(19, 2) for _ in range(2)]
        assert engine.label(2, 2) == "1.1.3"

    def test_returning_to_the_instance_does_not_reset_again(self):
        # THE test for "first encountered". Every other test here uses each
        # override instance in one contiguous run, so a materially wrong
        # rule — "reset on every transition INTO the instance" — passes them
        # all (a reviewer demonstrated exactly that). Interleaving is what
        # separates the two readings.
        engine = NumberingEngine(_CLAUSES)
        engine.label(2, 0)
        engine.label(2, 1)
        seen = [
            engine.label(19, 2),
            engine.label(19, 2),
            engine.label(2, 2),
            engine.label(19, 2),  # back to 19: must NOT re-apply its override
        ]
        assert seen == ["1.1.1", "1.1.2", "1.1.3", "1.1.4"], seen

    def test_start_override_can_be_a_value_other_than_one(self):
        numbering = _numbering(
            abstracts='<w:abstractNum w:abstractNumId="1">'
            + _level(0, text="%1.")
            + "</w:abstractNum>",
            instances='<w:num w:numId="5"><w:abstractNumId w:val="1"/>'
            '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="7"/>'
            "</w:lvlOverride></w:num>",
        )
        engine = NumberingEngine(numbering)
        assert [engine.label(5, 0) for _ in range(3)] == ["7.", "8.", "9."]


class TestLevelRestarts:
    def test_a_deeper_level_restarts_when_its_parent_moves(self):
        engine = NumberingEngine(_CLAUSES)
        engine.label(2, 0)
        seen = [
            engine.label(2, 1),
            engine.label(2, 2),
            engine.label(2, 2),
            engine.label(2, 1),
            engine.label(2, 2),
        ]
        assert seen == ["1.1", "1.1.1", "1.1.2", "1.2", "1.2.1"]

    def test_lvl_restart_zero_means_never(self):
        # legal templates use this for appendix parts that must keep counting
        # across sections ("APPENDIX 1 … Part 3" then "APPENDIX 2 … Part 4")
        numbering = _numbering(
            abstracts='<w:abstractNum w:abstractNumId="1">'
            + _level(0, text="%1.")
            + _level(1, text="Part %2", restart=0)
            + "</w:abstractNum>",
            instances='<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>',
        )
        engine = NumberingEngine(numbering)
        engine.label(1, 0)
        first = [engine.label(1, 1) for _ in range(2)]
        engine.label(1, 0)  # the parent moves on
        assert first == ["Part 1", "Part 2"]
        assert engine.label(1, 1) == "Part 3", "lvlRestart=0 was treated as a restart"

    def test_lvl_restart_names_which_level_resets_it(self):
        # lvlRestart=1 (1-based) → only level 0 advancing resets this level
        numbering = _numbering(
            abstracts='<w:abstractNum w:abstractNumId="1">'
            + _level(0, text="%1.")
            + _level(1, text="%1.%2")
            + _level(2, text="%1.%2.%3", restart=1)
            + "</w:abstractNum>",
            instances='<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>',
        )
        engine = NumberingEngine(numbering)
        engine.label(1, 0)
        engine.label(1, 1)
        assert engine.label(1, 2) == "1.1.1"
        engine.label(1, 1)  # level 1 moves: level 2 must NOT reset
        assert engine.label(1, 2) == "1.2.2"
        engine.label(1, 0)  # level 0 moves: now it resets
        engine.label(1, 1)
        assert engine.label(1, 2) == "2.1.1"

    def test_a_shallower_level_than_the_named_one_also_restarts_it(self):
        # §17.9.10: the level restarts when the named level "or any lower
        # level" is used. Reading it as "only that exact level" leaves a
        # stale deep counter whenever a document skips a level — which is
        # the ordinary heading-then-clause shape, so it shows up fast.
        numbering = _numbering(
            abstracts='<w:abstractNum w:abstractNumId="1">'
            + _level(0, text="%1.")
            + _level(1, text="%1.%2")
            + _level(2, text="%1.%2.%3", restart=2)
            + "</w:abstractNum>",
            instances='<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>',
        )
        engine = NumberingEngine(numbering)
        engine.label(1, 0)
        engine.label(1, 1)
        engine.label(1, 2)
        engine.label(1, 2)
        engine.label(1, 0)  # level 0 is SHALLOWER than the named level 2
        # NOTE: no level-1 call here. Level 1 advancing resets level 2 under
        # BOTH readings of lvlRestart, so an intermediate one hides the bug —
        # this test passed against the wrong implementation until that call
        # was removed. The skipped level is the whole point.
        assert engine.label(1, 2) == "2.1.1", "a shallower level did not restart it"

    def test_a_start_override_reapplies_when_the_level_restarts(self):
        # §17.9.27: a startOverride applies when the level "initially starts
        # in a given document, as well as whenever it is restarted".
        # Invisible while override == start (the common Restart-at-1), wrong
        # whenever they differ — so it needs a value that is not 1.
        numbering = _numbering(
            abstracts='<w:abstractNum w:abstractNumId="1">'
            + _level(0, text="%1.")
            + _level(1, text="%1.%2")
            + "</w:abstractNum>",
            instances='<w:num w:numId="1"><w:abstractNumId w:val="1"/>'
            '<w:lvlOverride w:ilvl="1"><w:startOverride w:val="5"/>'
            "</w:lvlOverride></w:num>",
        )
        engine = NumberingEngine(numbering)
        engine.label(1, 0)
        assert [engine.label(1, 1) for _ in range(2)] == ["1.5", "1.6"]
        engine.label(1, 0)  # the parent advances, restarting level 1
        assert engine.label(1, 1) == "2.5", "the restart fell back to the abstract start"


class TestLevelOverridesRedefineFormat:
    def test_an_instance_can_replace_a_level_definition(self):
        # a w:lvlOverride carrying a w:lvl body is not a restart at all — it
        # redefines the level's format for that instance. The dependency's
        # tracker keeps only startOverride and drops this entirely.
        numbering = _numbering(
            abstracts='<w:abstractNum w:abstractNumId="1">'
            + _level(0, text="%1.")
            + "</w:abstractNum>",
            instances='<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>'
            '<w:num w:numId="2"><w:abstractNumId w:val="1"/>'
            '<w:lvlOverride w:ilvl="0">'
            + _level(0, fmt="upperLetter", text="(%1)")
            + "</w:lvlOverride></w:num>",
        )
        engine = NumberingEngine(numbering)
        assert engine.label(1, 0) == "1."
        assert engine.label(2, 0) == "(B)", "the level override was ignored"


class TestLevelsThatDrawNothing:
    def test_a_none_level_draws_no_label_but_still_counts(self):
        # abstract 18 of the legal fixture: level 0 is numFmt="none", and the
        # deeper levels reference it as %1 — so it must count, and render as
        # nothing. Dropping the count desyncs every label below it.
        numbering = _numbering(
            abstracts='<w:abstractNum w:abstractNumId="1">'
            + _level(0, fmt="none", text="%1")
            + _level(1, text="%1%2.")
            + "</w:abstractNum>",
            instances='<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>',
        )
        engine = NumberingEngine(numbering)
        assert engine.label(1, 0) == ""
        assert engine.label(1, 1) == "1."

    def test_a_bullet_level_draws_no_label(self):
        numbering = _numbering(
            abstracts='<w:abstractNum w:abstractNumId="1">'
            + _level(0, fmt="bullet", text="")
            + "</w:abstractNum>",
            instances='<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>',
        )
        engine = NumberingEngine(numbering)
        assert engine.label(1, 0) == ""
        assert not engine.is_ordered(1, 0)


class TestUnknownDefinitions:
    def test_a_missing_instance_yields_no_label(self):
        engine = NumberingEngine(_CLAUSES)
        assert engine.label(999, 0) == ""

    def test_a_document_without_numbering_is_not_an_error(self):
        engine = NumberingEngine(None)
        assert engine.label(1, 0) == ""
        assert engine.level(1, 0) is None
        assert not engine.is_ordered(1, 0)

    def test_a_corrupt_numbering_part_degrades_instead_of_raising(self):
        # from_docx ingests arbitrary uploads, and the parse layer tolerates
        # a broken part by design. Losing the numbering is a degraded import;
        # raising out of the importer is a failed one.
        engine = NumberingEngine(b"<w:numbering unclosed")
        assert engine.label(1, 0) == ""

    def test_a_corrupt_numbering_part_still_imports_the_document(self):
        import io
        import zipfile

        import aimformat as aim

        docx = pytest.importorskip("docx")
        buf = io.BytesIO()
        doc = docx.Document()
        doc.add_paragraph("the body survives")
        doc.save(buf)
        buf.seek(0)
        source = zipfile.ZipFile(buf)
        broken = io.BytesIO()
        with zipfile.ZipFile(broken, "w") as out:
            for name in source.namelist():
                payload = source.read(name)
                out.writestr(name, b"<w:numbering unclosed" if "numbering" in name else payload)
        broken.seek(0)
        assert "the body survives" in aim.from_docx(broken).dumps()


class TestSchemeClassification:
    """Outline-numbered blocks or a list? The answer belongs to the SCHEME.

    Deciding per paragraph tore Word's stock multilevel list in half — its
    top level (``%1.``) looked like a list item and everything below it
    (``%1.%2.``) like an outline block, so one list emitted both shapes and
    the blocks counted against a level nothing incremented. They rendered
    ``0.1, 0.2, 0.3``.
    """

    @staticmethod
    def _scheme(*levels: str, fmt: str = "decimal", start: int = 1) -> NumberingEngine:
        body = "".join(_level(i, text=text, fmt=fmt, start=start) for i, text in enumerate(levels))
        return NumberingEngine(
            _numbering(
                abstracts=f'<w:abstractNum w:abstractNumId="1">{body}</w:abstractNum>',
                instances='<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>',
            )
        )

    def test_a_multilevel_list_is_one_scheme_not_two_shapes(self):
        # Word's stock gallery list: "%1." then "%1.%2."
        engine = self._scheme("%1.", "%1.%2.", "%1.%2.%3.")
        assert engine.scheme_is_outline(1, {0, 1})

    def test_a_flat_numbered_list_stays_a_list(self):
        # "1. 2. 3." is a list, not outline numbering — treating it as blocks
        # would strip real <ol> structure from every document that has one
        engine = self._scheme("%1.")
        assert not engine.scheme_is_outline(1, {0})

    def test_a_chained_level_alone_is_still_outline(self):
        engine = self._scheme("%1.", "%1.%2")
        assert engine.scheme_is_outline(1, {1})

    def test_an_unused_exotic_level_does_not_disqualify_the_scheme(self):
        # the real fixture defines a parenthesised level 5 it never reaches;
        # judging by DEFINED levels would bake the whole contract
        engine = NumberingEngine(
            _numbering(
                abstracts='<w:abstractNum w:abstractNumId="1">'
                + _level(0, text="%1.")
                + _level(1, text="%1.%2")
                + _level(2, text="%1.%2.%3")
                + _level(3, text="(%4)", fmt="lowerLetter")
                + "</w:abstractNum>",
                instances='<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>',
            )
        )
        assert engine.scheme_is_outline(1, {0, 1, 2})
        assert not engine.scheme_is_outline(1, {0, 1, 2, 3}), "the exotic level must bake"

    def test_a_level_that_does_not_start_at_one_bakes(self):
        # the classes carry no start value and the CSS restart sets 1, so a
        # scheme starting at 3 would render 1, 2 where Word draws 3., 4.
        engine = self._scheme("%1.", "%1.%2", start=3)
        assert not engine.scheme_is_outline(1, {0, 1})

    def test_a_bullet_level_bakes(self):
        engine = self._scheme("%1.", "", fmt="bullet")
        assert not engine.scheme_is_outline(1, {0, 1})


class TestNumberFormats:
    """Pinned because the label text depends on them and a silent change
    here would misnumber whole documents rather than fail loudly."""

    @pytest.mark.parametrize(
        ("value", "fmt", "expected"),
        [
            (1, "decimal", "1"),
            (42, "decimal", "42"),
            (1, "lowerLetter", "a"),
            (26, "lowerLetter", "z"),
            (27, "lowerLetter", "aa"),
            (2, "upperLetter", "B"),
            (4, "lowerRoman", "iv"),
            (9, "upperRoman", "IX"),
            (14, "lowerRoman", "xiv"),
            (1990, "upperRoman", "MCMXC"),
            (3, "bullet", ""),
            (3, "none", ""),
            (5, "someFutureFormat", "5"),  # degrade to decimal, never vanish
        ],
    )
    def test_format(self, value, fmt, expected):
        assert format_number(value, fmt) == expected
