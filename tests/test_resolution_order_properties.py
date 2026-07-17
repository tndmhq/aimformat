"""Family-wide properties for pending move/container-modify ordering.

The examples are deliberately small enough for an independent permutation
oracle.  This keeps the property about observable semantics, rather than about
the implementation's chosen ranks: every returned order must accept atomically,
preserve one logical copy of every surviving moved target, land each target in
its final requested container (or intentionally delete it), and leave
lint/verify clean.  If no permutation has those properties, resolution_order
must refuse the lane before the real document is mutated.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

import aimformat as aim
from aimformat.document import Proposal, resolution_order
from conftest import BOT, ME, ts


@dataclass(frozen=True)
class LaneSpec:
    family: str
    keep_anchor: bool = True
    retain_target: bool = False
    modify_kind: str = "ul"
    delete_ancestor: bool = False
    mutual_anchor: bool = False
    use_anchor: bool = False


@st.composite
def lane_specs(draw) -> LaneSpec:
    family = draw(
        st.sampled_from(
            [
                "retained_intermediate",
                "repeated_transit",
                "inbound",
                "outbound",
                "two_targets",
                "reconciled_destination",
                "move_delete",
                "cross_target_anchor",
            ]
        )
    )
    if family == "retained_intermediate":
        return LaneSpec(family, keep_anchor=False, retain_target=True)
    if family == "repeated_transit":
        return LaneSpec(family, keep_anchor=draw(st.booleans()))
    if family == "inbound":
        return LaneSpec(
            family,
            keep_anchor=draw(st.booleans()),
            modify_kind=draw(st.sampled_from(["ul", "table"])),
        )
    if family == "outbound":
        return LaneSpec(
            family,
            retain_target=draw(st.booleans()),
            modify_kind=draw(st.sampled_from(["ul", "table"])),
        )
    if family == "two_targets":
        return LaneSpec(family, keep_anchor=draw(st.booleans()))
    if family == "move_delete":
        return LaneSpec(family, delete_ancestor=draw(st.booleans()))
    if family == "cross_target_anchor":
        return LaneSpec(
            family,
            retain_target=draw(st.booleans()),
            mutual_anchor=draw(st.booleans()),
        )
    use_anchor = draw(st.booleans())
    return LaneSpec(
        family,
        keep_anchor=draw(st.booleans()) if use_anchor else True,
        modify_kind=draw(st.sampled_from(["ul", "table"])),
        use_anchor=use_anchor,
    )


def _ul_l2(*ids: str) -> str:
    items = "".join(
        f'<li data-aim="{cid}">{"TARGET-" + cid if cid in {"x", "z"} else cid.upper()}</li>'
        for cid in ids
    )
    return f'<ul data-aim-container="l2">{items}</ul>'


def _table_l2() -> str:
    return '<table data-aim-container="l2"><tbody><tr data-aim="a"><td>A</td></tr></tbody></table>'


def _build_lane(spec: LaneSpec) -> tuple[aim.AimDocument, dict[str, str | None], set[str]]:
    doc = aim.new_document(title="ordering property")
    requested: dict[str, str | None] = {}
    sentinels: set[str] = set()

    if spec.family == "retained_intermediate":
        doc.add_chunk(_ul_l2("x", "a", "b"), author=ME, at=ts(0))
        doc.add_chunk(
            '<ul data-aim-container="l3"><li data-aim="c">C</li></ul>',
            author=ME,
            at=ts(1),
        )
        doc.propose_move("x", author=BOT, container="l2", after="b", at=ts(2))
        doc.propose_move("x", author=BOT, container="l3", after="c", at=ts(3))
        doc.propose_modify("l2", _ul_l2("x", "a"), author=BOT, at=ts(4))
        requested["x"] = "l3"
        sentinels.add("TARGET-x")
    elif spec.family == "repeated_transit":
        doc.add_chunk(
            '<ul data-aim-container="l1"><li data-aim="x">TARGET-x</li></ul>',
            author=ME,
            at=ts(0),
        )
        doc.add_chunk(_ul_l2("a", "b"), author=ME, at=ts(1))
        doc.add_chunk(
            '<ul data-aim-container="l3"><li data-aim="c">C</li></ul>',
            author=ME,
            at=ts(2),
        )
        doc.propose_move("x", author=BOT, container="l2", after="b", at=ts(3))
        doc.propose_move("x", author=BOT, container="l3", after="c", at=ts(4))
        payload_ids = ("a", "b") if spec.keep_anchor else ("a",)
        doc.propose_modify("l2", _ul_l2(*payload_ids), author=BOT, at=ts(5))
        requested["x"] = "l3"
        sentinels.add("TARGET-x")
    elif spec.family == "inbound":
        doc.add_chunk(
            '<ul data-aim-container="l1"><li data-aim="x">TARGET-x</li></ul>',
            author=ME,
            at=ts(0),
        )
        doc.add_chunk(_ul_l2("a", "b"), author=ME, at=ts(1))
        doc.propose_move("x", author=BOT, container="l2", after="b", at=ts(2))
        payload = (
            _table_l2()
            if spec.modify_kind == "table"
            else _ul_l2(*(("a", "b") if spec.keep_anchor else ("a",)))
        )
        doc.propose_modify("l2", payload, author=BOT, at=ts(3))
        requested["x"] = "l2"
        sentinels.add("TARGET-x")
    elif spec.family == "outbound":
        doc.add_chunk(_ul_l2("x", "a"), author=ME, at=ts(0))
        doc.add_chunk(
            '<ul data-aim-container="l3"><li data-aim="c">C</li></ul>',
            author=ME,
            at=ts(1),
        )
        doc.propose_move("x", author=BOT, container="l3", after="c", at=ts(2))
        if spec.modify_kind == "table":
            payload = _table_l2()
        else:
            payload = _ul_l2(*(("x", "a") if spec.retain_target else ("a",)))
        doc.propose_modify("l2", payload, author=BOT, at=ts(3))
        requested["x"] = "l3"
        sentinels.add("TARGET-x")
    elif spec.family == "two_targets":
        doc.add_chunk(
            '<ul data-aim-container="l1"><li data-aim="x">TARGET-x</li></ul>',
            author=ME,
            at=ts(0),
        )
        doc.add_chunk(_ul_l2("z", "a", "b"), author=ME, at=ts(1))
        doc.add_chunk(
            '<ul data-aim-container="l3"><li data-aim="c">C</li></ul>',
            author=ME,
            at=ts(2),
        )
        doc.propose_move("x", author=BOT, container="l2", after="a", at=ts(3))
        doc.propose_move("z", author=BOT, container="l3", after="c", at=ts(4))
        payload_ids = ("a", "b") if spec.keep_anchor else ("a",)
        doc.propose_modify("l2", _ul_l2(*payload_ids), author=BOT, at=ts(5))
        requested.update(x="l2", z="l3")
        sentinels.update(["TARGET-x", "TARGET-z"])
    elif spec.family == "reconciled_destination":
        doc.add_chunk(
            '<ul data-aim-container="l1"><li data-aim="x">TARGET-x</li></ul>',
            author=ME,
            at=ts(0),
        )
        doc.add_chunk(_ul_l2("a", "b"), author=ME, at=ts(1))
        doc.propose_move(
            "x",
            author=BOT,
            container="l2",
            after="a" if spec.use_anchor else None,
            at=ts(2),
        )
        if spec.modify_kind == "ul":
            payload = _ul_l2(*(("a", "b") if spec.keep_anchor else ("b",)))
        else:
            payload = _table_l2()
        doc.propose_modify("l2", payload, author=BOT, at=ts(3))
        requested["x"] = "l2"
        sentinels.add("TARGET-x")
        edited = doc.dumps().replace(_ul_l2("a", "b"), _table_l2(), 1)
        doc = aim.loads(edited)
        report = doc.reconcile(at=ts(4))
        move_is_pending = any(p.action == "move" and p.target == "x" for p in doc.proposals)
        candidate_is_viable = spec.modify_kind == "ul" and (not spec.use_anchor or spec.keep_anchor)
        assert move_is_pending is candidate_is_viable, report.rejected_proposals
        if not move_is_pending:
            requested.clear()
    elif spec.family == "move_delete":
        doc.add_chunk(
            '<ul data-aim-container="l1"><li data-aim="x">TARGET-x</li></ul>',
            author=ME,
            at=ts(0),
        )
        doc.add_chunk(_ul_l2("a"), author=ME, at=ts(1))
        doc.propose_move("x", author=BOT, container="l2", after="a", at=ts(2))
        doc.propose_delete("l2" if spec.delete_ancestor else "x", author=BOT, at=ts(3))
        requested["x"] = None
    else:
        doc.add_chunk(
            '<ul data-aim-container="l1"><li data-aim="a">A</li></ul>',
            author=ME,
            at=ts(0),
        )
        doc.add_chunk(
            '<ul data-aim-container="l2"><li data-aim="b">TARGET-b</li>'
            '<li data-aim="c">C</li></ul>',
            author=ME,
            at=ts(1),
        )
        doc.add_chunk(
            '<ul data-aim-container="l3"><li data-aim="d">TARGET-d</li></ul>',
            author=ME,
            at=ts(2),
        )
        doc.propose_move("b", author=BOT, container="l3", after="d", at=ts(3))
        retained = '<li data-aim="b">TARGET-b</li>' if spec.retain_target else ""
        doc.propose_modify(
            "l2",
            f'<ul data-aim-container="l2">{retained}<li data-aim="c">C changed</li></ul>',
            author=BOT,
            at=ts(4),
        )
        doc.propose_move(
            "d",
            author=BOT,
            container="l2" if spec.mutual_anchor else "l1",
            after="b" if spec.mutual_anchor else "a",
            at=ts(5),
        )
        requested.update(b="l3", d="l2" if spec.mutual_anchor else "l1")
        sentinels.update(["TARGET-b", "TARGET-d"])

    return doc, requested, sentinels


def _moves_keep_card_order(order: tuple[Proposal, ...], original: list[Proposal]) -> bool:
    for target in {p.target for p in original if p.action == "move"}:
        wanted = [p.id for p in original if p.action == "move" and p.target == target]
        actual = [p.id for p in order if p.action == "move" and p.target == target]
        if actual != wanted:
            return False
    return True


def _resolved_invariants(
    doc: aim.AimDocument, requested: dict[str, str | None], sentinels: set[str]
) -> bool:
    try:
        for target, container in requested.items():
            if container is None:
                if doc._state.exists(target):
                    return False
            elif doc.chunk(target).container != container:
                return False
    except aim.AimError:
        if any(container is not None for container in requested.values()):
            return False
    texts = [chunk.text for chunk in doc.chunks]
    if any(texts.count(sentinel) != 1 for sentinel in sentinels):
        return False
    if doc.verify():
        return False
    return not any(finding.level == "error" for finding in aim.lint(doc))


def _valid_order_exists(
    doc: aim.AimDocument, requested: dict[str, str | None], sentinels: set[str]
) -> bool:
    original = doc.proposals
    for order in permutations(original):
        if not _moves_keep_card_order(order, original):
            continue
        work = aim.loads(doc.dumps())
        try:
            for proposal in order:
                work.accept(proposal.id, decided_by=ME, at=ts(20))
        except aim.AimError:
            continue
        if _resolved_invariants(work, requested, sentinels):
            return True
    return False


@example(spec=LaneSpec("retained_intermediate", keep_anchor=False, retain_target=True))
@example(spec=LaneSpec("reconciled_destination", modify_kind="ul"))
@example(
    spec=LaneSpec(
        "reconciled_destination",
        keep_anchor=False,
        modify_kind="ul",
        use_anchor=True,
    )
)
@example(spec=LaneSpec("move_delete", delete_ancestor=False))
@example(spec=LaneSpec("move_delete", delete_ancestor=True))
@example(spec=LaneSpec("cross_target_anchor", retain_target=True))
@example(spec=LaneSpec("cross_target_anchor", mutual_anchor=True))
@given(spec=lane_specs())
@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_move_modify_resolution_is_atomic_complete_and_content_preserving(spec: LaneSpec) -> None:
    doc, requested, sentinels = _build_lane(spec)
    valid = _valid_order_exists(doc, requested, sentinels)
    before = doc.dumps()

    if not valid:
        with pytest.raises(aim.InvalidOperation):
            resolution_order(doc.proposals, doc)
        assert doc.dumps() == before
        return

    order = resolution_order(doc.proposals, doc)
    try:
        for proposal in order:
            doc.accept(proposal.id, decided_by=ME, at=ts(30))
    except aim.TargetNotFound as exc:  # make the forbidden partial failure explicit
        pytest.fail(f"resolution_order returned a lane with a dangling hop: {exc}")
    assert _resolved_invariants(doc, requested, sentinels)
