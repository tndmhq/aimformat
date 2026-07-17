"""Property model for creation-order pending-lane acceptance.

The generator builds interacting move/modify/add/delete lanes, applies both
reconciled and unreconciled out-of-band mutations, and sometimes simulates a
foreign editor reordering proposal cards. The oracle is intentionally simple:
replay the lane in creation order on a clone. accept_all must either match that
complete replay or reject before mutating the source document.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

import aimformat as aim
from aimformat.canonical import serialize
from aimformat.document import resolution_order
from conftest import BOT, ME, ts


@dataclass(frozen=True)
class LaneSpec:
    second_hop: bool
    chained_adds: bool
    delete_body_anchor: bool
    drift: str
    reverse_cards: bool


@st.composite
def lane_specs(draw: st.DrawFn) -> LaneSpec:
    return LaneSpec(
        second_hop=draw(st.booleans()),
        chained_adds=draw(st.booleans()),
        delete_body_anchor=draw(st.booleans()),
        drift=draw(
            st.sampled_from(
                (
                    "none",
                    "reconcile_text",
                    "reconcile_remove_move_anchor",
                    "unreconciled_remove_move_anchor",
                )
            )
        ),
        reverse_cards=draw(st.booleans()),
    )


def _build_lane(spec: LaneSpec) -> aim.AimDocument:
    doc = aim.new_document(title="creation-order property")
    doc.add_chunk(
        '<ul data-aim-container="l1"><li data-aim="x">TOKEN-X</li></ul>',
        author=ME,
        at=ts(0),
    )
    doc.add_chunk(
        '<ul data-aim-container="l2"><li data-aim="z">TOKEN-Z</li>'
        '<li data-aim="a">TOKEN-A</li><li data-aim="b">TOKEN-B</li></ul>',
        author=ME,
        at=ts(1),
    )
    doc.add_chunk(
        '<ul data-aim-container="l3"><li data-aim="c">TOKEN-C</li></ul>',
        author=ME,
        at=ts(2),
    )
    doc.add_chunk('<p data-aim="p">TOKEN-P</p>', author=ME, at=ts(3))

    # The load-bearing sequence: rescue z before replacing l2, then move x
    # into the projected replacement. This is rich but unambiguous.
    doc.propose_move("z", author=BOT, container="body", at=ts(4))
    doc.propose_modify(
        "l2",
        '<ul data-aim-container="l2"><li data-aim="a">TOKEN-A2</li>'
        '<li data-aim="b">TOKEN-B</li></ul>',
        author=BOT,
        at=ts(5),
    )
    doc.propose_move("x", author=BOT, container="l2", after="a", at=ts(6))
    if spec.second_hop:
        doc.propose_move("x", author=BOT, container="l3", after="c", at=ts(7))

    first_add = doc.propose_add(
        '<p data-aim="n1">TOKEN-N1</p>',
        author=BOT,
        container="body",
        after="p",
        at=ts(8),
    )
    if spec.chained_adds:
        doc.propose_add(
            '<p data-aim="n2">TOKEN-N2</p>',
            author=BOT,
            container="body",
            after=first_add.id,
            at=ts(9),
        )
    if spec.delete_body_anchor:
        doc.propose_delete("p", author=BOT, at=ts(10))

    if spec.drift != "none":
        source = doc.dumps()
        if spec.drift == "reconcile_text":
            source = source.replace(
                '<li data-aim="b">TOKEN-B</li>',
                '<li data-aim="b">TOKEN-B-EXTERNAL</li>',
                1,
            )
        else:
            source = source.replace('<li data-aim="a">TOKEN-A</li>', "", 1)
        doc = aim.loads(source)
        if spec.drift.startswith("reconcile_"):
            doc.reconcile(at=ts(20))

    if spec.reverse_cards and doc.proposals:
        section = doc._state.section("aim-proposals")
        assert section is not None
        section.children = list(reversed(section.children))
    return doc


def _replay_oracle(
    doc: aim.AimDocument,
) -> tuple[aim.AimDocument, str | None, Exception | None]:
    oracle = aim.loads(doc.dumps())
    for proposal in resolution_order(oracle.proposals):
        try:
            oracle.accept(proposal.id, decided_by=ME, at=ts(30))
        except Exception as exc:  # the oracle records the first bad foreign card
            return oracle, proposal.id, exc
    return oracle, None, None


def _all_ids(doc: aim.AimDocument) -> list[str]:
    out: list[str] = []
    for construct in doc._state.constructs():
        for element in construct.iter():
            out.extend(
                value for value in (element.chunk_id, element.container_id) if value is not None
            )
    return out


@settings(max_examples=100, deadline=None)
@given(spec=lane_specs())
def test_accept_all_matches_creation_order_or_rejects_atomically(spec: LaneSpec) -> None:
    doc = _build_lane(spec)
    oracle, offending, oracle_error = _replay_oracle(doc)
    before = doc.dumps()

    if offending is not None:
        with pytest.raises(
            aim.InvalidOperation,
            match=rf"{re.escape(offending)}.*individually",
        ) as exc_info:
            doc.accept_all(decided_by=ME, at=ts(30))

        assert str(oracle_error) in str(exc_info.value)
        assert doc.dumps() == before
        return

    events = doc.accept_all(decided_by=ME, at=ts(30))

    assert doc.proposals == []
    assert doc.verify() == []
    assert aim.lint(doc) == []

    ids = _all_ids(doc)
    assert len(ids) == len(set(ids))

    body = "".join(serialize(construct) for construct in doc._state.constructs())
    oracle_body = "".join(serialize(construct) for construct in oracle._state.constructs())
    tokens = Counter(re.findall(r"TOKEN-[A-Z0-9-]+", body))
    oracle_tokens = Counter(re.findall(r"TOKEN-[A-Z0-9-]+", oracle_body))
    assert tokens == oracle_tokens
    assert all(count == 1 for count in tokens.values())

    final_move_destinations = {
        event.target: event.get("to")["container"]
        for event in events
        if event.action == "move" and event.target is not None
    }
    for target, container in final_move_destinations.items():
        assert doc.chunk(target).container == container
