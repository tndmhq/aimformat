"""Literal typography (spec §3.3, since v0.4): grammars, class gating,
version upgrades, and the normative pt equivalents of the type scale."""

from __future__ import annotations

import pytest

import aimformat as aim
from aimformat.css import generate_aim_css
from aimformat.registry import REGISTRY
from conftest import BOT, ME, ts

PAINT_SINCE = REGISTRY.paint_since
TYPO_SINCE = REGISTRY.typography_since


def _declared(version: str) -> aim.AimDocument:
    """A verifiable document whose marker was rewritten to *version* before
    its checkpoint — the same shape test_paint uses for legacy files."""
    doc = aim.new_document(title="An older document")
    doc.add_chunk('<h1 data-aim="t">Title</h1>', author=BOT, at=ts(0))
    older = aim.loads(
        doc.dumps().replace(
            f'data-aim-version="{aim.SPEC_VERSION}"', f'data-aim-version="{version}"'
        )
    )
    older.checkpoint("as-authored", at=ts(1))
    return older


class TestGrammar:
    def test_inline_typography_lints_clean_on_a_current_document(self):
        doc = aim.new_document(title="Typo")
        doc.add_chunk(
            '<p data-aim="p1" style="font-size:11pt; font-family:Georgia, serif">Sized.</p>',
            author=BOT,
            at=ts(0),
        )
        doc.add_chunk(
            '<p data-aim="p2"><span style="font-family:\'Courier New\'">run</span></p>',
            author=BOT,
            at=ts(1),
        )
        assert [f for f in aim.lint(doc) if f.level == "error"] == []

    def test_fractional_points_are_legal(self):
        doc = aim.new_document(title="Typo")
        doc.add_chunk('<p data-aim="p1" style="font-size:10.5pt">x</p>', author=BOT, at=ts(0))
        assert [f for f in aim.lint(doc) if f.level == "error"] == []

    @staticmethod
    def _lint_with_style(style: str) -> set[str]:
        doc = aim.new_document(title="Typo")
        doc.add_chunk('<p data-aim="p1" style="font-size:11pt">x</p>', author=BOT, at=ts(0))
        text = doc.dumps().replace("font-size:11pt", style)
        return {f.code for f in aim.lint_text(text)}

    def test_font_size_rejects_non_point_units(self):
        assert "V008" in self._lint_with_style("font-size:16px")

    def test_font_family_rejects_functions(self):
        assert "V008" in self._lint_with_style("font-family:url(evil)")

    def test_unregistered_properties_stay_rejected(self):
        assert "V007" in self._lint_with_style("font-weight:700")


class TestClasses:
    def test_justify_and_display_sizes_lint_clean(self):
        doc = aim.new_document(title="Typo")
        doc.add_chunk('<p data-aim="p1" class="text-justify">Justified.</p>', author=BOT, at=ts(0))
        doc.add_chunk('<h1 data-aim="h1" class="text-9xl">Hero</h1>', author=BOT, at=ts(1))
        assert [f for f in aim.lint(doc) if f.level == "error"] == []

    def test_the_generated_stylesheet_carries_the_new_utilities(self):
        css = generate_aim_css()
        assert ".text-justify{text-align:justify}" in css
        assert ".text-7xl{font-size:4.5rem;line-height:1}" in css
        assert ".text-9xl{font-size:8rem;line-height:1}" in css


class TestPtEquivalents:
    def test_every_type_scale_step_has_a_pt_equivalent(self):
        assert set(REGISTRY.type_scale_pt) == set(REGISTRY.raw["classes"]["type_scale"])

    def test_the_equivalence_is_rem_times_twelve(self):
        for step, (size, _lh) in REGISTRY.raw["classes"]["type_scale"].items():
            assert size.endswith("rem")
            expected = float(size[: -len("rem")]) * 12
            assert float(REGISTRY.type_scale_pt[step]) == expected


class TestGating:
    def test_a_03_document_retaining_inline_typography_fails_S033(self):
        older = _declared("0.3")
        text = older.dumps().replace(
            '<h1 data-aim="t">Title</h1>',
            '<h1 data-aim="t" style="font-size:26pt">Title</h1>',
        )
        codes = {f.code for f in aim.lint_text(text)}
        assert "S033" in codes
        assert "S032" not in codes

    def test_a_03_document_retaining_a_gated_class_fails_S033(self):
        older = _declared("0.3")
        text = older.dumps().replace(
            '<h1 data-aim="t">Title</h1>',
            '<h1 data-aim="t" class="text-justify">Title</h1>',
        )
        assert "S033" in {f.code for f in aim.lint_text(text)}

    def test_a_02_document_retaining_both_families_fails_both_gates(self):
        older = _declared("0.2")
        text = older.dumps().replace(
            '<h1 data-aim="t">Title</h1>',
            '<h1 data-aim="t" style="color:#ff69b4; font-size:26pt">Title</h1>',
        )
        codes = {f.code for f in aim.lint_text(text)}
        assert {"S032", "S033"} <= codes


class TestVersionUpgrade:
    def test_adding_typography_to_a_03_document_records_the_upgrade(self):
        older = _declared("0.3")
        older.modify_chunk(
            "t", '<h1 data-aim="t" style="font-size:26pt">Title</h1>', author=ME, at=ts(2)
        )
        assert older.spec_version == TYPO_SINCE
        upgrades = [e for e in older.history if e.target == "aim:version"]
        assert len(upgrades) == 1
        assert (upgrades[0].get("before"), upgrades[0].get("after")) == ("0.3", TYPO_SINCE)
        assert upgrades[0].get("explanation") == f"literal typography requires spec {TYPO_SINCE}"
        assert older.verify() == []
        assert [f for f in aim.lint_text(older.dumps()) if f.level == "error"] == []

    def test_a_gated_class_upgrades_too(self):
        older = _declared("0.3")
        older.modify_chunk(
            "t", '<h1 data-aim="t" class="text-justify">Title</h1>', author=ME, at=ts(2)
        )
        assert older.spec_version == TYPO_SINCE
        assert len([e for e in older.history if e.target == "aim:version"]) == 1

    def test_paint_alone_still_upgrades_only_to_the_paint_floor(self):
        older = _declared("0.2")
        older.modify_chunk(
            "t", '<h1 data-aim="t" style="color:#ff69b4">Title</h1>', author=ME, at=ts(2)
        )
        assert older.spec_version == PAINT_SINCE

    def test_a_mixed_payload_upgrades_straight_to_the_newest_floor(self):
        older = _declared("0.2")
        older.modify_chunk(
            "t",
            '<h1 data-aim="t" style="color:#ff69b4; font-size:26pt">Title</h1>',
            author=ME,
            at=ts(2),
        )
        assert older.spec_version == TYPO_SINCE
        upgrades = [e for e in older.history if e.target == "aim:version"]
        assert len(upgrades) == 1
        assert (upgrades[0].get("before"), upgrades[0].get("after")) == ("0.2", TYPO_SINCE)

    def test_retained_typography_prevents_undoing_the_upgrade(self):
        older = _declared("0.3")
        older.modify_chunk(
            "t", '<h1 data-aim="t" style="font-size:26pt">Title</h1>', author=ME, at=ts(2)
        )
        older.undo(author=ME, at=ts(3))
        assert older.chunk("t").html == '<h1 data-aim="t">Title</h1>'
        with pytest.raises(
            aim.InvalidOperation,
            match="retained document state or history contains literal typography",
        ):
            older.undo(author=ME, at=ts(4))
        assert older.spec_version == TYPO_SINCE
        assert older.verify() == []

    def test_the_gates_are_per_floor_not_one_lump(self):
        """A paint edit under 0.3 needs no upgrade at all — typography's
        newer floor must not drag paint's floor upward with it."""
        older = _declared("0.3")
        older.modify_chunk(
            "t", '<h1 data-aim="t" style="color:#ff69b4">Title</h1>', author=ME, at=ts(2)
        )
        assert older.spec_version == "0.3"
        assert [e for e in older.history if e.target == "aim:version"] == []
