"""Recompute parity for the incremental history/id index (R-AF-1).

The production index is updated in place. This test deliberately keeps a
slower reference implementation that reparses the authoritative JSONL and
rescans the pending lane after every generated operation. A separate oracle
accumulates ids seen in events before prune/flatten so AF-05's instance-lifetime
tombstones remain independently checkable after their JSONL records disappear.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import pytest

pytest.importorskip("hypothesis")

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

import aimformat as aim

BOT = aim.agent("history-index-property")
ME = aim.human("history-index-reviewer")
_PAYLOAD_ID_RE = re.compile(r'data-aim(?:-container)?="([^"]+)"')


def _ts(step: int) -> str:
    return f"2026-07-17T00:{step // 60 % 60:02d}:{step % 60:02d}Z"


@dataclass(frozen=True)
class _ReferenceIndex:
    events: list[dict]
    burned_ids: set[str]
    next_seq: int
    next_batch: str
    recorded_ids: set[str]


def _parse_jsonl(doc: aim.AimDocument) -> list[dict]:
    history = doc._state.script("history")
    raw = history.raw if history is not None else None
    return [json.loads(line) for line in (raw or "").split("\n") if line.strip()]


def _event_ids(events: list[dict]) -> set[str]:
    burned: set[str] = set()
    for event in events:
        for key in ("target", "proposal"):
            value = event.get(key)
            if isinstance(value, str):
                burned.add(value)
        for key in ("before", "after", "proposed", "applied"):
            value = event.get(key)
            if isinstance(value, str):
                burned.update(_PAYLOAD_ID_RE.findall(value))
    return burned


def _recompute(doc: aim.AimDocument, lifetime_burned: set[str]) -> _ReferenceIndex:
    """Reference implementation: JSONL + pending lane, no live-index reads."""
    events = _parse_jsonl(doc)
    burned = set(lifetime_burned) | _event_ids(events)
    recorded = set(burned)
    used_batches = {event.get("batch") for event in events if event.get("batch")}
    for proposal in doc.proposals:
        recorded.add(proposal.id)
        recorded.update(_PAYLOAD_ID_RE.findall(proposal.payload_html or ""))
        if proposal.batch:
            used_batches.add(proposal.batch)

    batch_number = 1
    while f"b{batch_number}" in used_batches:
        batch_number += 1
    next_seq = events[-1]["seq"] + 1 if events else 1
    return _ReferenceIndex(
        events=events,
        burned_ids=burned,
        next_seq=next_seq,
        next_batch=f"b{batch_number}",
        recorded_ids=recorded,
    )


def _assert_recompute_parity(doc: aim.AimDocument, lifetime_burned: set[str]) -> None:
    expected = _recompute(doc, lifetime_burned)
    live = doc._get_history_index()
    assert [event.data for event in live.events] == expected.events
    assert live.burned_ids == expected.burned_ids
    assert live.next_seq == expected.next_seq
    assert live.next_batch == expected.next_batch
    assert live.recorded_ids == expected.recorded_ids
    assert doc._recorded_ids() == expected.recorded_ids
    assert doc.seq == expected.next_seq - 1
    assert doc.verify() == []


_OP_NAMES = (
    "add",
    "modify",
    "delete",
    "move",
    "propose",
    "accept",
    "reject",
    "prune",
    "flatten",
)
operation_sequences = st.lists(
    st.tuples(st.sampled_from(_OP_NAMES), st.integers(min_value=0, max_value=100)),
    min_size=1,
    max_size=30,
)


def _apply_operation(doc: aim.AimDocument, operation: str, choice: int, step: int) -> None:
    live = doc.body_ids
    if operation == "add":
        after = None if not live or choice % 3 == 0 else live[choice % len(live)]
        doc.add_chunk(
            f'<p data-aim="add-{step}">added {step}</p>',
            author=BOT,
            after=after,
            at=_ts(step),
        )
    elif operation == "modify" and live:
        target = live[choice % len(live)]
        doc.modify_chunk(
            target,
            f'<p data-aim="{target}">modified {step}</p>',
            author=ME,
            at=_ts(step),
        )
    elif operation == "delete" and live:
        doc.delete_chunk(live[choice % len(live)], author=ME, at=_ts(step))
    elif operation == "move" and len(live) >= 2:
        target = live[choice % len(live)]
        after = live[-1] if target == live[0] else None
        doc.move_chunk(target, author=ME, after=after, at=_ts(step))
    elif operation == "propose":
        doc.propose_add(
            f'<p data-aim="proposal-{step}">proposed {step}</p>',
            author=BOT,
            after=None,
            at=_ts(step),
        )
    elif operation in ("accept", "reject") and doc.proposals:
        proposal = doc.proposals[choice % len(doc.proposals)]
        if operation == "accept":
            doc.accept(proposal.id, decided_by=ME, at=_ts(step))
        else:
            doc.reject(proposal.id, decided_by=ME, at=_ts(step))
    elif operation == "prune":
        events = doc.history
        if len(events) >= 2:
            cut = events[1 + choice % (len(events) - 1)].seq
            doc.prune(before=cut)
    elif operation == "flatten":
        doc.flatten()


@given(operations=operation_sequences)
@example(
    operations=[
        ("add", 0),
        ("modify", 0),
        ("move", 0),
        ("propose", 0),
        ("accept", 0),
        ("propose", 1),
        ("reject", 0),
        ("delete", 0),
        ("prune", 0),
        ("flatten", 0),
        ("add", 1),
    ]
)
@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_live_history_index_matches_full_recompute(
    operations: list[tuple[str, int]],
) -> None:
    doc = aim.new_document(title="history index parity")
    doc.add_chunk('<p data-aim="seed-a">A</p>', author=BOT, at=_ts(0))
    doc.add_chunk('<p data-aim="seed-b">B</p>', author=BOT, at=_ts(1))
    lifetime_burned = _event_ids(_parse_jsonl(doc))
    _assert_recompute_parity(doc, lifetime_burned)

    for step, (operation, choice) in enumerate(operations, start=2):
        # Capture records that the upcoming prune/flatten may remove.
        lifetime_burned.update(_event_ids(_parse_jsonl(doc)))
        _apply_operation(doc, operation, choice, step)
        lifetime_burned.update(_event_ids(_parse_jsonl(doc)))
        _assert_recompute_parity(doc, lifetime_burned)
