"""Computed paint: the resolver, the document lifecycle, the version upgrade.

`paint.py` answers one question — *what colour does a browser actually
render this element in?* — once per export, so no converter has to re-derive
it per leaf. The cases below are the ones that made the previous
class-matching approach disagree with browsers: shorthand resets, inheritance,
inline-over-class precedence, and grouping backgrounds.
"""

from __future__ import annotations

import pytest

import aimformat as aim
from aimformat.dom import parse_fragment
from aimformat.paint import PaintResolver
from conftest import BOT, ME, ts


def el(markup: str):
    node = parse_fragment(markup)[0]
    assert not isinstance(node, str)
    return node


def resolved(markup: str, **kw) -> tuple:
    """(root element, resolver) with the tree already resolved."""
    root = el(markup)
    r = PaintResolver(**kw)
    r.resolve(root)
    return root, r


class TestTextColour:
    def test_a_palette_class_resolves_to_its_literal(self):
        root, r = resolved('<p class="text-red-600">x</p>')
        assert r.of(root).color == "DC2626"

    def test_a_brand_class_resolves_through_the_theme(self):
        root, r = resolved('<p class="text-brand-1">x</p>', palette={"--aim-brand-1": "#ff1493"})
        assert r.of(root).color == "FF1493"

    def test_a_brand_class_falls_back_to_the_registry_default(self):
        root, r = resolved('<p class="text-brand-1">x</p>')
        assert r.of(root).color == "1D4ED8"

    def test_inline_paint_beats_every_class(self):
        root, r = resolved('<p class="text-brand-1 text-red-600" style="color:#ff69b4">x</p>')
        assert r.of(root).color == "FF69B4"

    def test_between_classes_the_alphabetically_last_wins(self):
        """generate_aim_css emits class rules sorted by name and CSS is
        last-wins, so the winner does not depend on markup order."""
        a, ra = resolved('<p class="text-brand-1 text-red-600">x</p>')
        b, rb = resolved('<p class="text-red-600 text-brand-1">x</p>')
        assert ra.of(a).color == rb.of(b).color == "DC2626"

    def test_colour_inherits_into_descendants(self):
        root, r = resolved('<div class="text-red-600"><p>child</p></div>')
        child = root.elements()[0]
        assert r.of(child).color == "DC2626"

    def test_a_descendants_own_colour_overrides_the_inherited_one(self):
        root, r = resolved('<div class="text-red-600"><p style="color:#ff69b4">c</p></div>')
        assert r.of(root.elements()[0]).color == "FF69B4"

    def test_a_base_layer_colour_stops_inheritance_and_paints_nothing(self):
        """`a{color:var(--aim-brand-1)}` is a base-layer rule, so a link inside
        a red block is NOT red in a browser. We emit nothing for it — the
        recipient's Word template owns hyperlink colour."""
        root, r = resolved('<p class="text-red-600">see <a href="#x">link</a></p>')
        link = root.elements()[0]
        assert r.of(root).color == "DC2626"
        assert r.of(link).color is None

    def test_an_unpainted_tree_declares_no_colour_anywhere(self):
        root, r = resolved("<p>plain <strong>bold</strong></p>")
        assert r.of(root).color is None
        assert r.of(root.elements()[0]).color is None

    def test_a_non_colour_text_utility_is_not_a_colour(self):
        root, r = resolved('<p class="text-xl text-right">x</p>')
        assert r.of(root).color is None


class TestBackground:
    def test_an_inline_background_is_the_elements_own(self):
        root, r = resolved('<p style="background-color:#fff1f7">x</p>')
        assert r.of(root).own_background == "FFF1F7"
        assert r.of(root).background == "FFF1F7"

    def test_background_does_not_inherit_but_the_box_shows_through(self):
        """CSS background does not inherit; the ancestor box is simply behind
        the descendant. Word has no box, so descendants carry it as shading."""
        root, r = resolved('<section style="background-color:#fff1f7"><p>c</p></section>')
        child = root.elements()[0]
        assert r.of(child).own_background is None
        assert r.of(child).background == "FFF1F7"

    def test_an_opaque_descendant_hides_the_ancestor_box(self):
        root, r = resolved(
            '<section style="background-color:#fff1f7">'
            '<p style="background-color:#eeeeee">c</p></section>'
        )
        assert r.of(root.elements()[0]).background == "EEEEEE"

    def test_a_base_layer_background_hides_the_box_and_paints_nothing(self):
        """`code{background:#f3f4f6}` is a base-layer rule: the ancestor tint
        does not show through it, and we leave Word's own Code look alone."""
        root, r = resolved('<p style="background-color:#fff1f7">t <code>c</code></p>')
        assert r.of(root.elements()[0]).background is None

    def test_a_bg_class_resolves(self):
        root, r = resolved('<p class="bg-amber-50">x</p>')
        assert r.of(root).own_background == "FFFBEB"


class TestBorders:
    def test_colour_alone_creates_no_border(self):
        root, r = resolved('<p style="border-color:#ff69b4">x</p>')
        assert r.of(root).borders == {}

    def test_a_border_utility_plus_colour_paints_every_side(self):
        root, r = resolved('<p class="border" style="border-color:#ff69b4">x</p>')
        assert {s: b.color for s, b in r.of(root).borders.items()} == {
            "top": "FF69B4",
            "right": "FF69B4",
            "bottom": "FF69B4",
            "left": "FF69B4",
        }

    def test_a_side_utility_paints_only_that_side(self):
        root, r = resolved('<p class="border-t" style="border-color:#ff69b4">x</p>')
        assert list(r.of(root).borders) == ["top"]

    def test_the_shorthand_reset_wins_exactly_as_the_browser_computes_it(self):
        """`.border-t{border-top:1px solid #e5e7eb}` is emitted AFTER
        `.border-red-600{border-color:#dc2626}` in the generated stylesheet, so
        the shorthand resets the top colour and the element renders GREY. A
        resolver matching only `border-color` would emit red and disagree with
        every renderer."""
        root, r = resolved('<p class="border-t border-red-600">x</p>')
        assert r.of(root).borders == {}  # the winning colour is base-layer grey

    def test_the_unreset_sides_of_a_shorthand_reset_keep_the_class_colour(self):
        root, r = resolved('<p class="border border-t border-red-600">x</p>')
        painted = {s: b.color for s, b in r.of(root).borders.items()}
        assert painted == {"right": "DC2626", "bottom": "DC2626", "left": "DC2626"}

    def test_inline_colour_outranks_the_shorthand_reset(self):
        """The trap above is exactly what an inline literal sidesteps."""
        root, r = resolved('<p class="border-t border-red-600" style="border-color:#ff69b4">x</p>')
        assert {s: b.color for s, b in r.of(root).borders.items()} == {"top": "FF69B4"}

    def test_a_base_stylesheet_border_is_recoloured(self):
        """blockquote and hr carry a border with no utility present."""
        quote, rq = resolved('<blockquote style="border-color:#ff69b4">q</blockquote>')
        assert {s: (b.color, b.width_px) for s, b in rq.of(quote).borders.items()} == {
            "left": ("FF69B4", 3.0)
        }
        rule, rr = resolved('<hr style="border-color:#ff69b4">')
        assert list(rr.of(rule).borders) == ["top"]

    def test_an_unpainted_blockquote_paints_nothing(self):
        root, r = resolved("<blockquote>q</blockquote>")
        assert r.of(root).borders == {}

    def test_borders_do_not_inherit(self):
        root, r = resolved('<div class="border" style="border-color:#ff69b4"><p>c</p></div>')
        assert r.of(root.elements()[0]).borders == {}


class TestResolverContract:
    def test_the_source_tree_is_never_mutated(self):
        from aimformat.canonical import serialize

        root = el('<div class="text-red-600" style="background-color:#fff1f7"><p>c</p></div>')
        before = serialize(root)
        r = PaintResolver()
        r.resolve(root)
        r.of(root.elements()[0])
        assert serialize(root) == before

    def test_records_are_keyed_by_object_identity_not_by_markup(self):
        one, two = el("<p>same</p>"), el("<p>same</p>")
        r = PaintResolver()
        r.resolve(one, inherited=r.context(color="DC2626"))
        r.resolve(two)
        assert (r.of(one).color, r.of(two).color) == ("DC2626", None)

    def test_an_unresolved_element_reads_as_unpainted(self):
        assert PaintResolver().of(el("<p>x</p>")).color is None

    def test_a_synthetic_block_can_adopt_its_source(self):
        from aimformat.dom import Element

        root, r = resolved('<blockquote class="text-red-600">loose text</blockquote>')
        wrapper = Element("p")
        wrapper.children = list(root.children)
        r.adopt(wrapper, root)
        assert r.of(wrapper).color == "DC2626"


class TestPaintLifecycle:
    """A painted chunk through the whole SDK: propose, accept, reject, verify,
    time travel, undo/redo — every path ending lint-clean."""

    @staticmethod
    def _clean(doc: aim.AimDocument) -> None:
        assert [f for f in aim.lint_text(doc.dumps()) if f.level == "error"] == []
        assert doc.verify() == []

    @pytest.fixture
    def painted(self) -> aim.AimDocument:
        doc = aim.new_document(title="Paint lifecycle")
        with doc.batch():
            doc.add_chunk(
                '<h1 data-aim="t" class="font-bold" style="color:#ff69b4">Pink</h1>',
                author=BOT,
                at=ts(0),
            )
            doc.add_chunk('<p data-aim="b">Body.</p>', author=BOT, at=ts(1))
        return doc

    def test_a_painted_chunk_is_readable_and_clean(self, painted):
        assert painted.chunk("t").html == (
            '<h1 data-aim="t" class="font-bold" style="color:#ff69b4">Pink</h1>'
        )
        self._clean(painted)

    def test_accepting_a_paint_only_modify(self, painted):
        p = painted.propose_modify(
            "b",
            '<p data-aim="b" style="color:#ff69b4">Body.</p>',
            author=BOT,
            explanation="Match the title.",
            at=ts(2),
        )
        clone = aim.loads(painted.dumps())
        clone.accept(p.id, decided_by=ME, at=ts(3))
        assert 'style="color:#ff69b4"' in clone.chunk("b").html
        self._clean(clone)

    def test_rejecting_a_paint_only_modify_leaves_the_chunk_alone(self, painted):
        p = painted.propose_modify(
            "b", '<p data-aim="b" style="color:#ff69b4">Body.</p>', author=BOT, at=ts(2)
        )
        clone = aim.loads(painted.dumps())
        clone.reject(p.id, decided_by=ME, at=ts(3))
        assert clone.chunk("b").html == '<p data-aim="b">Body.</p>'
        self._clean(clone)

    def test_time_travel_past_the_paint(self, painted):
        painted.modify_chunk(
            "b", '<p data-aim="b" style="color:#ff69b4">Body.</p>', author=ME, at=ts(4)
        )
        past = painted.state_at(painted.seq - 1)
        assert past.chunk("b").html == '<p data-aim="b">Body.</p>'
        self._clean(past)

    def test_undo_then_redo_a_recolour(self, painted):
        painted.modify_chunk(
            "b", '<p data-aim="b" style="color:#ff69b4">Body.</p>', author=ME, at=ts(4)
        )
        painted.undo(author=ME, at=ts(5))
        assert painted.chunk("b").html == '<p data-aim="b">Body.</p>'
        self._clean(painted)
        painted.redo(author=ME, at=ts(6))
        assert 'style="color:#ff69b4"' in painted.chunk("b").html
        self._clean(painted)

    def test_clearing_paint_is_removing_the_declaration(self, painted):
        painted.modify_chunk(
            "t", '<h1 data-aim="t" class="font-bold">Plain</h1>', author=ME, at=ts(4)
        )
        assert painted.chunk("t").html == '<h1 data-aim="t" class="font-bold">Plain</h1>'
        self._clean(painted)


class TestVersionUpgrade:
    """Adding paint to a 0.2 document is a recorded upgrade, never a silent
    edit: `data-aim-version` rides the `<html>` open tag, which doc_hash
    covers, so an in-place bump would break every earlier checkpoint."""

    @pytest.fixture
    def older(self) -> aim.AimDocument:
        """A genuine 0.2 file: the version marker is rewritten BEFORE the
        checkpoint, so the recorded hash covers the 0.2 `<html>` line — which
        is what an in-place bump would go on to break."""
        doc = aim.new_document(title="An older document")
        doc.add_chunk('<h1 data-aim="t">Title</h1>', author=BOT, at=ts(0))
        older = aim.loads(
            doc.dumps().replace(f'data-aim-version="{aim.SPEC_VERSION}"', 'data-aim-version="0.2"')
        )
        older.checkpoint("as-authored", at=ts(1))
        return older

    def test_an_older_document_starts_verifiable_and_clean(self, older):
        assert older.spec_version == "0.2"
        assert older.verify() == []
        assert "S002" not in {f.code for f in aim.lint(older)}

    def test_a_resave_leaves_the_body_and_the_version_byte_identical(self, older):
        """The registry gained three properties; a document using none of them
        must serialize exactly as before. Only the machine-managed stylesheet
        stamp is allowed to move, and it lives in the head."""
        text = older.dumps()
        again = aim.loads(text).dumps()
        assert again == text
        body = text[text.index("<body>") : text.index("</body>")]
        assert 'data-aim-version="0.2"' in text
        assert body == again[again.index("<body>") : again.index("</body>")]

    def test_adding_paint_bumps_the_version_and_records_the_event(self, older):
        older.modify_chunk(
            "t", '<h1 data-aim="t" style="color:#ff69b4">Title</h1>', author=ME, at=ts(2)
        )
        assert older.spec_version == aim.SPEC_VERSION
        upgrades = [e for e in older.history if e.target == "aim:version"]
        assert len(upgrades) == 1
        assert (upgrades[0].get("before"), upgrades[0].get("after")) == ("0.2", aim.SPEC_VERSION)

    def test_the_earlier_checkpoint_still_verifies_after_the_upgrade(self, older):
        older.modify_chunk(
            "t", '<h1 data-aim="t" style="color:#ff69b4">Title</h1>', author=ME, at=ts(2)
        )
        assert older.verify() == []
        assert [f for f in aim.lint_text(older.dumps()) if f.level == "error"] == []

    def test_a_second_paint_edit_records_no_further_upgrade(self, older):
        older.modify_chunk(
            "t", '<h1 data-aim="t" style="color:#ff69b4">Title</h1>', author=ME, at=ts(2)
        )
        older.modify_chunk(
            "t", '<h1 data-aim="t" style="color:#0f766e">Title</h1>', author=ME, at=ts(3)
        )
        assert len([e for e in older.history if e.target == "aim:version"]) == 1

    def test_a_pending_paint_payload_upgrades_too(self, older):
        """A 0.2 validator rejects the payload sitting in the pending lane, so
        the proposal — not only its acceptance — is what needs the version."""
        older.propose_modify(
            "t", '<h1 data-aim="t" style="color:#ff69b4">Title</h1>', author=BOT, at=ts(2)
        )
        assert older.spec_version == aim.SPEC_VERSION
        assert older.verify() == []

    def test_an_unpainted_edit_leaves_the_version_alone(self, older):
        older.modify_chunk("t", '<h1 data-aim="t">Retitled</h1>', author=ME, at=ts(2))
        assert older.spec_version == "0.2"
        assert [e for e in older.history if e.target == "aim:version"] == []

    def test_undoing_the_paint_and_then_the_upgrade_returns_a_02_document(self, older):
        older.modify_chunk(
            "t", '<h1 data-aim="t" style="color:#ff69b4">Title</h1>', author=ME, at=ts(2)
        )
        older.undo(author=ME, at=ts(3))
        assert older.chunk("t").html == '<h1 data-aim="t">Title</h1>'
        older.undo(author=ME, at=ts(4))
        assert older.spec_version == "0.2"
        assert older.verify() == []

    def test_time_travel_reconstructs_the_declared_version(self, older):
        before = older.seq
        older.modify_chunk(
            "t", '<h1 data-aim="t" style="color:#ff69b4">Title</h1>', author=ME, at=ts(2)
        )
        assert older.state_at(before).spec_version == "0.2"

    def test_a_history_that_cannot_take_the_event_refuses_paint(self, older):
        """A damaged chain cannot record the upgrade, and an unrecorded bump
        is an untracked change to hashed state — so paint is refused instead."""
        damaged = aim.loads(older.dumps().replace(">Title</h1>", ">Tampered</h1>", 1))
        assert damaged.verify() != []  # the recorded add no longer matches
        with pytest.raises(aim.InvalidOperation):
            damaged.modify_chunk(
                "t", '<h1 data-aim="t" style="color:#ff69b4">Tampered</h1>', author=ME, at=ts(2)
            )
        assert damaged.spec_version == "0.2"

    def test_a_document_from_the_future_keeps_its_own_version(self, older):
        """A version this build does not implement is not ours to set — we
        cannot know what else that version requires."""
        ahead = aim.loads(older.dumps().replace('data-aim-version="0.2"', 'data-aim-version="9.9"'))
        ahead.modify_chunk(
            "t", '<h1 data-aim="t" style="color:#ff69b4">Title</h1>', author=ME, at=ts(2)
        )
        assert ahead.spec_version == "9.9"
        assert [e for e in ahead.history if e.target == "aim:version"] == []

    def test_a_document_declaring_no_version_refuses_paint(self, older):
        """There is nothing to upgrade FROM, and inventing a `before` would
        put a fiction in the history."""
        headless = aim.loads(older.dumps().replace(' data-aim-version="0.2"', ""))
        with pytest.raises(aim.InvalidOperation):
            headless.modify_chunk(
                "t", '<h1 data-aim="t" style="color:#ff69b4">Title</h1>', author=ME, at=ts(2)
            )
