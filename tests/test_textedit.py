"""Markup-preserving text replacement inside one chunk (READS-D13):
``AimDocument.replace_text`` / ``propose_replace_text`` and the pure
``aimformat.textedit.replace_in_markup`` they share."""

from __future__ import annotations

import pytest

import aimformat as aim
from aimformat.errors import AimError, TargetNotFound
from aimformat.textedit import TextReplaceError, replace_in_markup
from conftest import BOT

RICH = (
    '<p data-aim="p1" class="text-center">Hello <strong>big</strong> world, '
    'see <a href="https://example.com">the site</a>.</p>'
)


# -- the pure function --------------------------------------------------------------
class TestReplaceInMarkup:
    def test_plain_run_keeps_every_attribute_and_inline_element(self):
        out = replace_in_markup(RICH, "world", "planet")
        assert out == RICH.replace("world", "planet")

    def test_inside_an_inline_element_keeps_the_element(self):
        out = replace_in_markup(RICH, "big", "huge")
        assert "<strong>huge</strong>" in out and 'class="text-center"' in out

    def test_context_across_markup_is_fine_when_the_change_sits_in_one_run(self):
        # the agent may quote surrounding words for uniqueness; only the
        # changed span has to sit inside one text run
        out = replace_in_markup(RICH, "Hello big world", "Hello huge world")
        assert "Hello <strong>huge</strong> world" in out

    def test_link_text_edit_keeps_the_href(self):
        out = replace_in_markup(RICH, "the site", "our website")
        assert '<a href="https://example.com">our website</a>' in out

    def test_a_change_spanning_a_markup_boundary_is_refused(self):
        with pytest.raises(TextReplaceError, match="strong"):
            replace_in_markup(RICH, "Hello big", "Goodbye large")

    def test_not_found_is_refused(self):
        with pytest.raises(TextReplaceError, match="not found"):
            replace_in_markup(RICH, "absent words", "x")

    def test_two_occurrences_are_refused_with_the_count(self):
        markup = '<p data-aim="p1">the cat and the dog</p>'
        with pytest.raises(TextReplaceError, match="2 times"):
            replace_in_markup(markup, "the", "a")
        assert "a cat" in replace_in_markup(markup, "the cat", "a cat")

    def test_overlapping_occurrences_count(self):
        with pytest.raises(TextReplaceError, match="2 times"):
            replace_in_markup('<p data-aim="p1">aaa</p>', "aa", "b")

    def test_empty_old_text_and_no_op_are_refused(self):
        with pytest.raises(TextReplaceError, match="old_text"):
            replace_in_markup(RICH, "", "x")
        with pytest.raises(TextReplaceError, match="same"):
            replace_in_markup(RICH, "world", "world")

    def test_entities_are_text_not_markup(self):
        markup = '<p data-aim="p1">R&amp;D budget &lt;draft&gt;</p>'
        out = replace_in_markup(markup, "R&D budget <draft>", "R&D budget <final>")
        assert out == '<p data-aim="p1">R&amp;D budget &lt;final&gt;</p>'

    def test_deleting_all_of_an_inline_element_drops_the_empty_element(self):
        out = replace_in_markup(RICH, "big ", "")
        assert "<strong>" not in out and ">Hello world, see <a" in out

    def test_a_pure_deletion_may_span_runs(self):
        # deleted text has no formatting to decide, unlike inserted text
        out = replace_in_markup(RICH, "Hello big world", "Hello world")
        assert out.startswith('<p data-aim="p1" class="text-center">Hello world, see <a')

    def test_whitespace_runs_in_the_document_match_a_single_space(self):
        markup = '<p data-aim="p1">within\n  thirty days</p>'
        assert "within\n  sixty days" in replace_in_markup(markup, "within thirty", "within sixty")

    def test_insertion_at_a_boundary_extends_the_run_before_it(self):
        out = replace_in_markup(RICH, "big world", "big new world")
        assert "<strong>big</strong> new world" in out

    def test_a_multi_member_run_chunk(self):
        run = '<li data-aim="r">first item</li><li data-aim="r">second item</li>'
        out = replace_in_markup(run, "second", "2nd")
        assert out == '<li data-aim="r">first item</li><li data-aim="r">2nd item</li>'
        with pytest.raises(TextReplaceError):
            replace_in_markup(run, "first itemsecond", "x")

    # -- substitute review round 1: block and line boundaries inside a chunk ---------
    def test_an_insertion_at_the_start_of_a_cell_stays_in_that_cell(self):
        row = '<tr data-aim="r2"><td>Implementation</td><td>16</td></tr>'
        out = replace_in_markup(row, "16", "about 16")
        assert out == '<tr data-aim="r2"><td>Implementation</td><td>about 16</td></tr>'
        # appending to the end of a cell still lands in that cell
        out = replace_in_markup(row, "Implementation", "Implementation phase")
        assert "<td>Implementation phase</td><td>16</td>" in out

    def test_an_insertion_at_the_start_of_a_list_item_stays_in_that_item(self):
        flat = '<ul data-aim="l"><li>Discovery workshop</li><li>Rollout</li></ul>'
        out = replace_in_markup(flat, "Rollout", "Final Rollout")
        assert "<li>Discovery workshop</li><li>Final Rollout</li>" in out
        # with the source's newlines between items (a text run in the <ul>)
        spaced = '<ul data-aim="l">\n<li>Discovery workshop</li>\n<li>Rollout</li>\n</ul>'
        out = replace_in_markup(spaced, "Rollout", "Final Rollout")
        assert "\n<li>Final Rollout</li>\n" in out
        out = replace_in_markup(spaced, "workshop", "workshop day")
        assert "<li>Discovery workshop day</li>\n" in out

    def test_an_insertion_at_the_start_of_a_line_stays_on_that_line(self):
        markup = '<p data-aim="x">Line one<br>Line two</p>'
        out = replace_in_markup(markup, "Line two", "New Line two")
        assert out == '<p data-aim="x">Line one<br>New Line two</p>'

    def test_an_insertion_between_two_cells_with_context_on_both_sides_is_refused(self):
        row = '<tr data-aim="r2"><td>Price</td><td>100</td></tr>'
        with pytest.raises(TextReplaceError, match="ends and the next begins"):
            replace_in_markup(row, "Price100", "Price: 100")

    # -- substitute review round 1: which characters a deletion removes ---------------
    def test_a_deletion_removes_the_quoted_label_not_a_shifted_span(self):
        markup = '<p data-aim="x"><strong>Note:</strong> Notice period is 30 days.</p>'
        out = replace_in_markup(markup, "Note: Notice", "Notice")
        assert out == '<p data-aim="x">Notice period is 30 days.</p>'
        out = replace_in_markup('<p data-aim="x"><b>bar</b> baz</p>', "bar baz", "baz")
        assert out == '<p data-aim="x">baz</p>'

    def test_a_deletion_that_reads_two_ways_across_markup_is_refused(self):
        # "ab" -> "a" deletes either the bold b or the plain one; neither
        # reading sits on a word edge, so the caller must say which
        with pytest.raises(TextReplaceError, match="more than one place"):
            replace_in_markup('<p data-aim="x">xa<b>b</b>by</p>', "abb", "ab")


# -- the SDK -----------------------------------------------------------------------------
def _doc() -> aim.AimDocument:
    doc = aim.new_document(title="T")
    doc.add_chunk(RICH, author=BOT)
    doc.add_chunk('<p data-aim="p2">Second.</p>', author=BOT)
    return doc


class TestSdk:
    def test_replace_text_is_a_recorded_modify_on_the_same_id(self):
        doc = _doc()
        chunk = doc.replace_text("p1", "world", "planet", author=BOT, explanation="Wording.")
        assert chunk.id == "p1" and "planet" in chunk.text
        assert "<strong>big</strong>" in doc.chunk("p1").html
        ev = doc.history[-1]
        assert (ev.action, ev.target, ev.get("explanation")) == ("modify", "p1", "Wording.")
        assert not doc.verify()

    def test_propose_replace_text_is_a_modify_card_with_the_markup_kept(self):
        doc = _doc()
        card = doc.propose_replace_text("p1", "big", "huge", author=BOT, explanation="Tone.")
        assert card.action == "modify" and card.target == "p1"
        assert "<strong>huge</strong>" in (card.payload_html or "")
        assert "big" in doc.chunk("p1").text  # live content untouched until accepted
        doc.accept(card.id, decided_by=aim.human("ada"))
        assert "<strong>huge</strong>" in doc.chunk("p1").html

    def test_refusals_leave_the_document_unchanged(self):
        doc = _doc()
        before = doc.dumps()
        with pytest.raises(AimError):
            doc.replace_text("p1", "absent", "x", author=BOT)
        with pytest.raises(TargetNotFound):
            doc.replace_text("ghost", "a", "b", author=BOT)
        assert doc.dumps() == before


def test_a_long_deletion_inside_one_run_is_not_capped_by_its_readings():
    # 300 equal readings, all inside one text run: no placement question
    markup = '<p data-aim="x">' + "-" * 600 + "</p>"
    out = replace_in_markup(markup, "-" * 600, "-" * 300)
    assert out == '<p data-aim="x">' + "-" * 300 + "</p>"
