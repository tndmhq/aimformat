"""Complete bounded reference model for pending-lane acceptance ordering.

The generator spans single/repeated moves; direct, cross-target, carried, and
separated anchors; retaining/dropping/kind-changing source replacements;
compatible/incompatible and anchor-keeping/removing destination replacements;
target/source/destination-ancestor deletes; reconciled nesting, wrapping, and
kind changes; and pending destination-rescue moves. The composed family varies
the core move/modify/delete axes together instead of testing only named cases.

Every lane stays small enough for an independent permutation oracle. The only
permutations excluded are repeated moves that reverse their target's mandated
card order. Every remaining permutation is executed on a fresh clone through
the public accept path. A valid order must land every target at its terminal
requested container (or delete it), preserve exact target id/content counts,
raise no dangling-target or membership error, and leave both ``verify()`` and
lint clean. ``resolution_order`` must return a member of that exact valid-order
set, or reject before mutating the real document iff the set is empty.
"""

from __future__ import annotations

import os
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
    carry_anchor: bool = True
    rescue_destination: bool = True
    hop_count: int = 1
    source_mode: str = "none"
    destination_mode: str = "none"
    delete_mode: str = "none"


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
                "anchor_container_move",
                "source_member_shape",
                "source_ancestor_delete",
                "reconciled_rescue",
                "reconciled_wrap",
                "composed",
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
    if family == "anchor_container_move":
        return LaneSpec(family, carry_anchor=draw(st.booleans()))
    if family == "source_member_shape":
        return LaneSpec(
            family,
            retain_target=draw(st.booleans()),
            modify_kind=draw(st.sampled_from(["ul", "table"])),
        )
    if family == "reconciled_rescue":
        return LaneSpec(family, rescue_destination=True)
    if family in ("source_ancestor_delete", "reconciled_wrap"):
        return LaneSpec(family)
    if family == "composed":
        return LaneSpec(
            family,
            use_anchor=draw(st.booleans()),
            hop_count=draw(st.integers(min_value=1, max_value=2)),
            source_mode=draw(st.sampled_from(["none", "drop", "retain_li", "retain_tr"])),
            destination_mode=draw(st.sampled_from(["none", "keep", "remove_anchor", "table"])),
            delete_mode=draw(
                st.sampled_from(["none", "target", "source_ancestor", "destination_ancestor"])
            ),
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


def _table_l2(*ids: str) -> str:
    ids = ids or ("a",)
    rows = "".join(f'<tr data-aim="{cid}"><td>{cid.upper()}</td></tr>' for cid in ids)
    return f'<table data-aim-container="l2"><tbody>{rows}</tbody></table>'


def _build_lane(
    spec: LaneSpec,
) -> tuple[aim.AimDocument, dict[str, str | None], dict[str, int]]:
    doc = aim.new_document(title="ordering property")
    requested: dict[str, str | None] = {}
    marker_counts: dict[str, int] = {}

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
        marker_counts["TARGET-x"] = 1
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
        marker_counts["TARGET-x"] = 1
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
        marker_counts["TARGET-x"] = 1
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
        marker_counts["TARGET-x"] = 1
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
        marker_counts.update({"TARGET-x": 1, "TARGET-z": 1})
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
        marker_counts["TARGET-x"] = 1
        edited = doc.dumps().replace(_ul_l2("a", "b"), _table_l2(), 1)
        doc = aim.loads(edited)
        report = doc.reconcile(at=ts(4))
        move_is_pending = any(p.action == "move" and p.target == "x" for p in doc.proposals)
        candidate_is_viable = spec.modify_kind == "ul" and (not spec.use_anchor or spec.keep_anchor)
        assert move_is_pending is candidate_is_viable, report.rejected_proposals
        if not move_is_pending:
            requested["x"] = "l1"
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
        marker_counts["TARGET-x"] = 0
    elif spec.family == "cross_target_anchor":
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
        marker_counts.update({"TARGET-b": 1, "TARGET-d": 1})
    elif spec.family == "anchor_container_move":
        doc.add_chunk(
            '<aim-slide data-aim-container="s"><p data-aim="y">TARGET-y</p></aim-slide>',
            author=ME,
            at=ts(0),
        )
        doc.add_chunk('<p data-aim="x">TARGET-x</p>', author=ME, at=ts(1))
        if spec.carry_anchor:
            doc.propose_move("s", author=BOT, container="body", after="x", at=ts(2))
            doc.propose_move("x", author=BOT, container="s", after="y", at=ts(3))
            requested.update(s="body", x="s")
        else:
            doc.add_chunk('<p data-aim="z">Z</p>', author=ME, at=ts(2))
            doc.propose_move("x", author=BOT, container="s", after="y", at=ts(3))
            doc.propose_move("y", author=BOT, container="body", after="z", at=ts(4))
            requested.update(x="s", y="body")
        marker_counts.update({"TARGET-x": 1, "TARGET-y": 1})
    elif spec.family == "source_member_shape":
        doc.add_chunk(
            '<ul data-aim-container="l1"><li data-aim="a">TARGET-a</li>'
            '<li data-aim="k">K</li></ul>',
            author=ME,
            at=ts(0),
        )
        doc.add_chunk(
            '<ul data-aim-container="l2"><li data-aim="b">B</li></ul>',
            author=ME,
            at=ts(1),
        )
        doc.propose_move("a", author=BOT, container="l2", after="b", at=ts(2))
        retained = (
            '<tr data-aim="a"><td>TARGET-a</td></tr>'
            if spec.retain_target and spec.modify_kind == "table"
            else '<li data-aim="a">TARGET-a</li>'
            if spec.retain_target
            else ""
        )
        if spec.modify_kind == "table":
            payload = (
                '<table data-aim-container="l1"><tbody>'
                f'{retained}<tr data-aim="k"><td>K changed</td></tr>'
                "</tbody></table>"
            )
        else:
            payload = f'<ul data-aim-container="l1">{retained}<li data-aim="k">K changed</li></ul>'
        doc.propose_modify("l1", payload, author=BOT, at=ts(3))
        requested["a"] = "l2"
        marker_counts["TARGET-a"] = 1
    elif spec.family == "source_ancestor_delete":
        doc.add_chunk(
            '<ul data-aim-container="l1"><li data-aim="x">TARGET-x</li></ul>',
            author=ME,
            at=ts(0),
        )
        doc.add_chunk(_ul_l2("a"), author=ME, at=ts(1))
        doc.propose_move("x", author=BOT, container="l2", after="a", at=ts(2))
        doc.propose_delete("l1", author=BOT, at=ts(3))
        requested["x"] = "l2"
        marker_counts["TARGET-x"] = 1
    elif spec.family == "reconciled_rescue":
        doc.add_chunk(
            '<ul data-aim-container="l1"><li data-aim="x">TARGET-x</li></ul>',
            author=ME,
            at=ts(0),
        )
        l2 = '<ul data-aim-container="l2"><li data-aim="y">TARGET-y</li></ul>'
        doc.add_chunk(l2, author=ME, at=ts(1))
        rescue = None
        if spec.rescue_destination:
            rescue = doc.propose_move("l2", author=BOT, container="body", after=None, at=ts(2))
        inbound = doc.propose_move("x", author=BOT, container="l2", after="y", at=ts(3))
        edited = (
            doc.dumps()
            .replace(l2, "", 1)
            .replace(
                '<li data-aim="x">TARGET-x</li>',
                f'<li data-aim="x">TARGET-x{l2}</li>',
                1,
            )
        )
        doc = aim.loads(edited)
        report = doc.reconcile(at=ts(4))
        if spec.rescue_destination:
            assert rescue is not None
            assert report.rejected_proposals == []
            assert [p.id for p in doc.proposals] == [rescue.id, inbound.id]
            requested.update(l2="body", x="l2")
        else:
            assert report.rejected_proposals == [inbound.id]
            requested["x"] = "l1"
        marker_counts.update({"TARGET-x": 1, "TARGET-y": 1})
    elif spec.family == "reconciled_wrap":
        doc.add_chunk('<h1 data-aim="h1">TARGET-h1</h1>', author=ME, at=ts(0))
        doc.add_chunk('<p data-aim="c1">TARGET-c1</p>', author=ME, at=ts(1))
        move = doc.propose_move("h1", author=BOT, container="body", after="c1", at=ts(2))
        edited = doc.dumps().replace(
            '<p data-aim="c1">TARGET-c1</p>',
            '<aim-slide data-aim-container="wrap"><p data-aim="c1">TARGET-c1</p></aim-slide>',
            1,
        )
        doc = aim.loads(edited)
        report = doc.reconcile(at=ts(3))
        assert report.rejected_proposals == [move.id]
        requested["h1"] = "body"
        marker_counts.update({"TARGET-h1": 1, "TARGET-c1": 1})
    else:
        doc.add_chunk(
            '<ul data-aim-container="l1"><li data-aim="x">TARGET-x</li>'
            '<li data-aim="k">K</li></ul>',
            author=ME,
            at=ts(0),
        )
        doc.add_chunk(_ul_l2("a", "b"), author=ME, at=ts(1))
        doc.add_chunk(
            '<ul data-aim-container="l3"><li data-aim="c">C</li></ul>',
            author=ME,
            at=ts(2),
        )
        doc.propose_move(
            "x",
            author=BOT,
            container="l2",
            after="b" if spec.use_anchor else None,
            at=ts(3),
        )
        if spec.hop_count == 2:
            doc.propose_move("x", author=BOT, container="l3", after="c", at=ts(4))

        if spec.source_mode != "none":
            if spec.source_mode == "retain_tr":
                source_payload = (
                    '<table data-aim-container="l1"><tbody>'
                    '<tr data-aim="x"><td>TARGET-x</td></tr>'
                    '<tr data-aim="k"><td>K changed</td></tr></tbody></table>'
                )
            else:
                retained = (
                    '<li data-aim="x">TARGET-x</li>' if spec.source_mode == "retain_li" else ""
                )
                source_payload = (
                    f'<ul data-aim-container="l1">{retained}<li data-aim="k">K changed</li></ul>'
                )
            doc.propose_modify("l1", source_payload, author=BOT, at=ts(5))

        if spec.destination_mode != "none":
            if spec.destination_mode == "table":
                destination_payload = _table_l2("a", "b")
            else:
                destination_payload = _ul_l2(
                    *("a",) if spec.destination_mode == "remove_anchor" else ("a", "b")
                )
            doc.propose_modify("l2", destination_payload, author=BOT, at=ts(6))

        final_container = "l3" if spec.hop_count == 2 else "l2"
        if spec.delete_mode == "target":
            doc.propose_delete("x", author=BOT, at=ts(7))
            requested["x"] = None
            marker_counts["TARGET-x"] = 0
        elif spec.delete_mode == "source_ancestor":
            doc.propose_delete("l1", author=BOT, at=ts(7))
            requested["x"] = final_container
            marker_counts["TARGET-x"] = 1
        elif spec.delete_mode == "destination_ancestor":
            doc.propose_delete(final_container, author=BOT, at=ts(7))
            requested["x"] = None
            marker_counts["TARGET-x"] = 0
        else:
            requested["x"] = final_container
            marker_counts["TARGET-x"] = 1

    return doc, requested, marker_counts


def _moves_keep_card_order(order: tuple[Proposal, ...], original: list[Proposal]) -> bool:
    for target in {p.target for p in original if p.action == "move"}:
        wanted = [p.id for p in original if p.action == "move" and p.target == target]
        actual = [p.id for p in order if p.action == "move" and p.target == target]
        if actual != wanted:
            return False
    return True


def _target_container(doc: aim.AimDocument, target: str) -> str:
    if doc._state.kind_of(target) == "chunk":
        return doc.chunk(target).container
    node = doc._state.container_node(target)
    if node is None or node is doc._state.body:
        raise aim.TargetNotFound(f"container target {target!r} is missing")
    parent = doc._state._parent_of(node)
    while parent is not doc._state.body:
        if parent.container_id is not None:
            return parent.container_id
        parent = doc._state._parent_of(parent)
    return "body"


def _live_id_count(doc: aim.AimDocument, target: str) -> int:
    return sum(
        value == target
        for element in doc._state.body.iter()
        for value in (element.chunk_id, element.container_id)
    )


def _resolved_invariants(
    doc: aim.AimDocument,
    requested: dict[str, str | None],
    marker_counts: dict[str, int],
) -> bool:
    try:
        for target, container in requested.items():
            if container is None:
                if doc._state.exists(target):
                    return False
                if _live_id_count(doc, target) != 0:
                    return False
            elif _target_container(doc, target) != container or _live_id_count(doc, target) != 1:
                return False
    except aim.AimError:
        if any(container is not None for container in requested.values()):
            return False
    live_html = "".join(doc._state.serial(target) or "" for target in doc.body_ids)
    if any(live_html.count(marker) != count for marker, count in marker_counts.items()):
        return False
    if doc.verify():
        return False
    return not any(finding.level == "error" for finding in aim.lint(doc))


def _valid_orders(
    doc: aim.AimDocument,
    requested: dict[str, str | None],
    marker_counts: dict[str, int],
) -> set[tuple[str, ...]]:
    original = doc.proposals
    valid: set[tuple[str, ...]] = set()
    for order in permutations(original):
        if not _moves_keep_card_order(order, original):
            continue
        work = aim.loads(doc.dumps())
        try:
            for proposal in order:
                work.accept(proposal.id, decided_by=ME, at=ts(20))
        except aim.AimError:
            continue
        if _resolved_invariants(work, requested, marker_counts):
            valid.add(tuple(proposal.id for proposal in order))
    return valid


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
@example(spec=LaneSpec("anchor_container_move", carry_anchor=True))
@example(spec=LaneSpec("anchor_container_move", carry_anchor=False))
@example(spec=LaneSpec("source_member_shape", retain_target=True, modify_kind="table"))
@example(spec=LaneSpec("source_member_shape", retain_target=False, modify_kind="table"))
@example(spec=LaneSpec("source_ancestor_delete"))
@example(spec=LaneSpec("reconciled_rescue", rescue_destination=True))
@example(spec=LaneSpec("reconciled_wrap"))
@example(
    spec=LaneSpec(
        "composed",
        use_anchor=True,
        hop_count=2,
        source_mode="retain_li",
        destination_mode="remove_anchor",
    )
)
@example(
    spec=LaneSpec(
        "composed",
        use_anchor=True,
        source_mode="retain_tr",
        destination_mode="table",
    )
)
@example(
    spec=LaneSpec(
        "composed",
        hop_count=2,
        source_mode="drop",
        destination_mode="remove_anchor",
    )
)
@example(spec=LaneSpec("composed", delete_mode="source_ancestor"))
@example(spec=LaneSpec("composed", delete_mode="target"))
@example(spec=LaneSpec("composed", delete_mode="destination_ancestor"))
@given(spec=lane_specs())
@settings(
    max_examples=int(os.environ.get("AIM_ORDERING_PROPERTY_EXAMPLES", "180")),
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_move_modify_resolution_is_atomic_complete_and_content_preserving(spec: LaneSpec) -> None:
    doc, requested, marker_counts = _build_lane(spec)
    valid = _valid_orders(doc, requested, marker_counts)
    before = doc.dumps()

    if not valid:
        with pytest.raises(aim.InvalidOperation):
            resolution_order(doc.proposals, doc)
        assert doc.dumps() == before
        return

    order = resolution_order(doc.proposals, doc)
    assert tuple(proposal.id for proposal in order) in valid
    try:
        for proposal in order:
            doc.accept(proposal.id, decided_by=ME, at=ts(30))
    except aim.AimError as exc:
        pytest.fail(
            "resolution_order returned a lane with a dangling target, "
            f"invalid membership, or other partial failure: {exc}"
        )
    assert _resolved_invariants(doc, requested, marker_counts)
