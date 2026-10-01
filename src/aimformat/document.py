"""AimDocument — load, read, edit, propose, resolve, verify .aim documents.

The document tree is the single live state; every state-changing operation
mutates the tree *and* appends the matching history event, so the invariant
"the body is the accepted document, history explains it" holds by
construction. Verification (:meth:`AimDocument.verify`) replays the log
backwards over a deep copy and checks payload byte-equality plus checkpoint
hashes — the same walk that powers :meth:`AimDocument.state_at`.
"""

from __future__ import annotations

import base64
import contextlib
import datetime as _dt
import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Literal, TypeVar, overload

from . import canonical, ids
from .canonical import canonical_json, serialize, serialize_run
from .css import generate_aim_css
from .dom import Comment, Element, Fragment, Text, parse_fragment, parse_html
from .errors import AimError, HistoryError, InvalidOperation, ParseError, TargetNotFound
from .events import Actor, Event, snapshot_problems
from .note import find_note, is_canonical, render_note
from .pagesetup import (
    PageSetup,
    doc_settings_element,
    page_setup_from_obj,
    page_setup_from_settings,
    parse_doc_settings,
)
from .registry import REGISTRY, version_key
from .review import POLICY, REQUEST, AutoAcceptOutcome, ReviewPolicy

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from .reconcile import ReconcileReport
    from .revision_import import RevisionImportReport

__all__ = ["AimDocument", "Chunk", "Proposal", "Anchor", "LAST", "load", "loads", "new_document"]


class _Last:
    def __repr__(self) -> str:  # pragma: no cover - repr only
        return "LAST"


#: Sentinel: insert at the end of the target container (the default).
LAST = _Last()

AnchorAfter = str | None | _Last
_BODY_SECTIONS = ("aim-proposals", "aim-assets", "script")
#: Reserved singleton targets (spec §3.5/§3.6/§3.3): they can be modified but
#: never deleted or moved — they have no body anchor to restore them at.
#: ``aim:version`` is toolkit-managed on top of that: it is not a proposal
#: target and has no direct-edit entry point, only the recorded upgrade the
#: SDK writes when a feature the document's declared version lacks first
#: appears in it.
_RESERVED_TARGETS = ("aim:theme", "aim:doc", "aim:version")
VERSION_TARGET = "aim:version"
_PAYLOAD_ID_RE = re.compile(r'data-aim(?:-container)?="([^"]+)"')
_T = TypeVar("_T")


class _NoOpEdit(InvalidOperation):
    """A direct edit that would leave the document unchanged.

    Distinct from structural illegality: propose-time validation judges
    meaningfulness against the CURRENT document only, while the pending
    projection answers structural questions. A proposal that becomes a
    no-op once earlier pendings resolve stays legal — accepting a no-op
    modify/move is harmless."""


def _no_delete_move(target: str, action: str) -> None:
    if target in _RESERVED_TARGETS:
        raise InvalidOperation(
            f"{target} is a reserved singleton and cannot be the target of "
            f"a {action} — modify it instead"
        )


def _settings_floor(el: Element) -> str | None:
    """The floor an ``aim:doc`` settings script's JSON requires (§3.7):
    ``review`` (the review policy, §5.6) is a 0.6 construct. Malformed JSON
    sets no floor — D001 reports it."""
    raw = el.raw or ""
    if '"review"' not in raw:
        return None
    try:
        obj = json.loads(raw.strip())
    except ValueError:
        return None
    return REGISTRY.review_since if isinstance(obj, dict) and "review" in obj else None


FloorPart = Literal["all", "review", "other"]


def _payload_floors(markup: str | None, part: FloorPart = "all") -> set[str]:
    """The spec-version floors *markup*'s constructs require (spec §3.3):
    literal paint needs the paint era, literal typography (inline
    font-size/font-family and the since-gated classes) the typography era,
    and an ``aim:doc`` settings block carrying ``review`` the review-policy
    era (§5.6). *part* narrows the scan to the review policy alone
    (``"review"``) or to everything else (``"other"``): two features can
    share an era, and lint names them separately (S035 vs S034).

    Parsed rather than pattern-matched: a text node may legitimately contain
    the characters ``style="color:#ff69b4"`` (a code sample), and a regex
    over the payload would read that as paint.
    """
    floors: set[str] = set()
    if not markup:
        return floors
    gated_classes = REGISTRY.class_floors
    gated_attrs = REGISTRY.attr_floors
    settings = part != "other" and REGISTRY.script_types["doc"] in markup and '"review"' in markup
    if part == "review" and not settings:
        return floors
    if not (
        settings
        or any(p in markup for p in REGISTRY.paint_props)
        or any(p in markup for p in REGISTRY.typography_props)
        or any(c in markup for c in gated_classes)
        or any(a in markup for a in gated_attrs)
    ):
        return floors
    for node in parse_fragment(markup):
        if not isinstance(node, Element):
            continue
        for el in node.iter():
            if settings and el.tag == "script" and el.get("type") == REGISTRY.script_types["doc"]:
                floor = _settings_floor(el)
                if floor is not None:
                    floors.add(floor)
            if part == "review":
                continue
            for piece in (el.get("style") or "").split(";"):
                prop, sep, _ = piece.partition(":")
                if not sep:
                    continue
                name = prop.strip()
                if name in REGISTRY.paint_props:
                    floors.add(REGISTRY.paint_since)
                elif name in REGISTRY.typography_props:
                    floors.add(REGISTRY.typography_since)
            for token in (el.get("class") or "").split():
                floor = gated_classes.get(token)
                if floor is not None:
                    floors.add(floor)
            for name, _value in el.attrs:
                floor = gated_attrs.get(name)
                if floor is not None:
                    floors.add(floor)
    return floors


def _floor_of(markup: str | None) -> str | None:
    """The binding (newest) version floor of *markup*, or None."""
    floors = _payload_floors(markup)
    if not floors:
        return None
    return max(floors, key=lambda f: version_key(f) or ())


def _payload_has_paint(markup: str | None) -> bool:
    """Whether *markup* declares any literal paint property (spec §3.3)."""
    return REGISTRY.paint_since in _payload_floors(markup)


def _floor_label(floor: str) -> str:
    """Human name of the construct family a version floor gates."""
    if floor == REGISTRY.typography_since:
        return "literal typography"
    if floor == REGISTRY.paint_since:
        return "literal paint"
    if floor == REGISTRY.review_since:
        return "a review policy or auto-accepted resolution"
    return "a newer-spec construct"


def _binding_payload(*payloads: str | None) -> str | None:
    """The retained payload whose constructs set the newest version floor."""
    best: str | None = None
    best_key: tuple[int, ...] = ()
    for payload in payloads:
        floor = _floor_of(payload)
        if floor is None:
            continue
        key = version_key(floor) or ()
        if best is None or key > best_key:
            best, best_key = payload, key
    return best


def _payload_marker(el: Element) -> str:
    """The identity marker an unmarked payload root receives.

    ``aim-slide`` is the one tag that can only ever be a container (§4.3),
    so a bare slide payload always takes the container path — otherwise it
    would be silently demoted to an opaque chunk with unaddressable
    children. A bare ``ul``/``ol``/``table`` stays an atomic chunk by
    default (the vocabulary deliberately allows both readings)."""
    if el.tag == "aim-slide":
        return "data-aim-container"
    return "data-aim-container" if el.container_id is not None else "data-aim"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Anchor:
    """A position: *container* (``body``, a slide, a list/table container id)
    plus the id to sit *after* (``None`` = first position).

    For rows in table containers the anchor also carries the *shell*
    (``thead``/``tbody``/``tfoot``) the position resolves in — without it,
    "first position" is ambiguous across row sections and a delete of the
    first body row would un-delete into the header (spec §6.4)."""

    container: str
    after: str | None = None
    shell: str | None = None

    def to_obj(self) -> dict:
        obj: dict = {"container": self.container, "after": self.after}
        if self.shell is not None:
            obj["shell"] = self.shell
        return obj

    @classmethod
    def from_obj(cls, obj: dict) -> Anchor:
        return cls(container=obj["container"], after=obj.get("after"), shell=obj.get("shell"))


@dataclass(frozen=True)
class Chunk:
    """Read-only view of one chunk (possibly a multi-element run)."""

    id: str
    container: str  # "body", a container id, or a slide id
    tags: tuple[str, ...]  # member tags, in order
    html: str  # canonical serialization (run concatenated)
    text: str

    @property
    def tag(self) -> str:
        return self.tags[0]

    @property
    def is_run(self) -> bool:
        return len(self.tags) > 1


@dataclass(frozen=True)
class Proposal:
    """Read-only view of one pending proposal card."""

    id: str
    action: str
    target: str | None  # data-for (None for add)
    author: Actor
    at: str
    explanation: str | None
    payload_html: str | None  # canonical payload serialization
    anchor_container: str | None
    anchor_after: str | None  # None = first position OR n/a (see action)
    depends_on: str | None
    batch: str | None
    anchor_shell: str | None = None  # thead/tbody/tfoot for table rows
    #: The ``accepted`` resolution event when the ``propose_*`` call that
    #: returned this view also auto-accepted it (spec §5.6); else None.
    resolution: Event | None = None


@dataclass(frozen=True)
class _BatchCard:
    """A card created inside the open batch, with its per-call accept choice."""

    pid: str
    accept: bool | None
    accept_by: Actor | None
    at: str | None


@dataclass(frozen=True)
class _CardSpec:
    """One card for :meth:`AimDocument._propose_lane` — the internal
    batch-propose primitive (importers today, public batch operations later).

    ``after`` names a CURRENT chunk to sit after (``None`` = first position);
    ``after_card`` instead chains an add onto an earlier spec of the same
    call (its index), exactly as ``propose_add(after=<pending add id>)``
    does. ``batch_key`` groups cards into one ``data-batch``: every distinct
    key gets its own fresh batch id.
    """

    action: str  # modify | add | delete | move
    author: Actor
    target: str | None = None
    markup: str | None = None
    container: str = "body"
    after: str | None = None
    after_card: int | None = None
    shell: str | None = None
    explanation: str | None = None
    at: str | None = None
    batch_key: str | None = None


class _ChainedAddCycle(InvalidOperation):
    """An unorderable pending-add cycle, with its actual participants."""

    def __init__(self, proposal_ids: Sequence[str]):
        self.proposal_ids = tuple(proposal_ids)
        super().__init__(
            "pending adds anchor on each other in a cycle — the file is "
            "corrupt (aim lint reports P015)"
        )


@dataclass
class _HistoryIndex:
    """Derived history/id state for one :class:`AimDocument` instance.

    The history JSONL and pending lane remain authoritative. ``raw`` is the
    exact string object this index was built from, so an internal replacement
    outside the normal writers invalidates the cache on the next read. Burned
    ids additionally carry the instance-lifetime AF-05 tombstones that survive
    prune/flatten; they are deliberately not persisted.
    """

    raw: str | None
    events: list[Event]
    burned_ids: set[str]
    recorded_ids: set[str]
    next_seq: int | None
    next_batch: str
    _pending_id_counts: Counter[str]
    _proposal_payload_ids: dict[str, Counter[str]]
    _batch_counts: Counter[str]

    @classmethod
    def build(
        cls,
        raw: str | None,
        proposals: Iterable[Proposal],
        *,
        burned_seed: Iterable[str] = (),
    ) -> _HistoryIndex:
        events = [Event.from_json(line) for line in (raw or "").split("\n") if line.strip()]
        index = cls(
            raw=raw,
            events=[],
            burned_ids=set(burned_seed),
            recorded_ids=set(burned_seed),
            next_seq=1,
            next_batch="b1",
            _pending_id_counts=Counter(),
            _proposal_payload_ids={},
            _batch_counts=Counter(),
        )
        for event in events:
            index._index_event(event)
        for proposal in proposals:
            index.add_proposal(proposal)
        index._reset_next_batch()
        return index

    @staticmethod
    def _payload_ids(payload: str | None) -> set[str]:
        return set(_PAYLOAD_ID_RE.findall(payload or ""))

    @classmethod
    def _event_ids(cls, event: Event) -> set[str]:
        found: set[str] = set()
        for key in ("target", "proposal"):
            value = event.get(key)
            if isinstance(value, str):
                found.add(value)
        for key in ("before", "after", "proposed", "applied"):
            value = event.get(key)
            if isinstance(value, str):
                found.update(cls._payload_ids(value))
        return found

    @staticmethod
    def _batch_number(batch: str | None) -> int | None:
        match = re.fullmatch(r"b([1-9]\d*)", batch or "")
        return int(match.group(1)) if match else None

    def _reset_next_batch(self) -> None:
        number = 1
        while self._batch_counts[f"b{number}"]:
            number += 1
        self.next_batch = f"b{number}"

    def _add_batch(self, batch: str | None) -> None:
        if not batch:
            return
        self._batch_counts[batch] += 1
        if batch == self.next_batch:
            self._reset_next_batch()

    def _remove_batch(self, batch: str | None) -> None:
        if not batch or not self._batch_counts[batch]:
            return
        self._batch_counts[batch] -= 1
        if not self._batch_counts[batch]:
            del self._batch_counts[batch]
            number = self._batch_number(batch)
            current = self._batch_number(self.next_batch)
            if number is not None and current is not None and number < current:
                self.next_batch = batch

    def _burn(self, values: Iterable[str]) -> None:
        for value in values:
            self.burned_ids.add(value)
            self.recorded_ids.add(value)

    def _index_event(self, event: Event) -> None:
        self.events.append(event)
        self._burn(self._event_ids(event))
        self._add_batch(event.batch)
        seq = event.data.get("seq")
        self.next_seq = seq + 1 if isinstance(seq, int) else None

    def append_event(self, event: Event, raw: str) -> None:
        self._index_event(event)
        self.raw = raw

    def replace_events(self, events: Iterable[Event], raw: str | None) -> None:
        """Replace retained events while preserving lifetime-burned ids."""
        for event in self.events:
            self._remove_batch(event.batch)
        self.events = []
        self.next_seq = 1
        for event in events:
            self.events.append(event)
            self._add_batch(event.batch)
            seq = event.data.get("seq")
            self.next_seq = seq + 1 if isinstance(seq, int) else None
        self.raw = raw
        self._reset_next_batch()

    def _add_pending_id(self, value: str) -> None:
        self._pending_id_counts[value] += 1
        self.recorded_ids.add(value)

    def _remove_pending_id(self, value: str) -> None:
        if not self._pending_id_counts[value]:
            return
        self._pending_id_counts[value] -= 1
        if not self._pending_id_counts[value]:
            del self._pending_id_counts[value]
            if value not in self.burned_ids:
                self.recorded_ids.discard(value)

    def add_proposal(self, proposal: Proposal) -> None:
        self._add_pending_id(proposal.id)
        payload_ids = self._payload_ids(proposal.payload_html)
        for value in payload_ids:
            self._add_pending_id(value)
            by_proposal = self._proposal_payload_ids.setdefault(proposal.id, Counter())
            by_proposal[value] += 1
        self._add_batch(proposal.batch)

    def remove_proposal(self, proposal: Proposal) -> None:
        self._remove_pending_id(proposal.id)
        payload_ids = self._payload_ids(proposal.payload_html)
        for value in payload_ids:
            self._remove_pending_id(value)
            by_proposal = self._proposal_payload_ids.get(proposal.id)
            if by_proposal is not None and by_proposal[value]:
                by_proposal[value] -= 1
                if not by_proposal[value]:
                    del by_proposal[value]
                if not by_proposal:
                    del self._proposal_payload_ids[proposal.id]
        self._remove_batch(proposal.batch)

    def replace_proposal(self, before: Proposal, after: Proposal) -> None:
        self.remove_proposal(before)
        self.add_proposal(after)

    def recorded(self, *, skip_payload_of: str | None = None) -> set[str]:
        recorded = set(self.recorded_ids)
        if skip_payload_of is None:
            return recorded
        skipped = self._proposal_payload_ids.get(skip_payload_of, Counter())
        for value, count in skipped.items():
            if self._pending_id_counts[value] == count and value not in self.burned_ids:
                recorded.discard(value)
        return recorded


def _chained_add_cycle_ids(proposals: Sequence[Proposal]) -> tuple[str, ...]:
    """Return cycle members, excluding non-cyclic paths that lead into one."""
    proposal_ids = {proposal.id for proposal in proposals}
    dependencies: dict[str, str] = {}
    for proposal in proposals:
        after = proposal.anchor_after
        if proposal.action == "add" and after is not None and after in proposal_ids:
            dependencies[proposal.id] = after

    cycle_ids: set[str] = set()
    finished: set[str] = set()
    for start in dependencies:
        path: list[str] = []
        positions: dict[str, int] = {}
        current = start
        while current in dependencies and current not in finished:
            if current in positions:
                cycle_ids.update(path[positions[current] :])
                break
            positions[current] = len(path)
            path.append(current)
            current = dependencies[current]
        finished.update(path)
    return tuple(proposal.id for proposal in proposals if proposal.id in cycle_ids)


def _creation_order(proposals: Sequence[Proposal]) -> list[Proposal]:
    """Return card creation order, moving only chained adds behind anchors.

    SDK-created cards are appended, so their file order is their creation
    order. A foreign-authored file may place a chained add before the pending
    add it names; resolve that one format-level dependency without otherwise
    reordering the lane.
    """
    pending = list(proposals)
    order: list[Proposal] = []
    while pending:
        pending_ids = {proposal.id for proposal in pending}
        ready_index = next(
            (
                index
                for index, proposal in enumerate(pending)
                if not (
                    proposal.action == "add"
                    and proposal.anchor_after is not None
                    and proposal.anchor_after in pending_ids
                )
            ),
            None,
        )
        if ready_index is None:
            raise _ChainedAddCycle(_chained_add_cycle_ids(pending))
        order.append(pending.pop(ready_index))
    return order


def _apply_move_data(state: DocState, data: dict) -> None:
    """Apply a spec move using only its recorded destination anchor."""
    state.move(data["target"], Anchor.from_obj(data["to"]))


def _set_card_payload(card: Element, payload: str) -> None:
    """Swap a proposal card's <template> payload in place."""
    tmpl = next((c for c in card.elements() if c.tag == "template"), None)
    if tmpl is None:  # defensive: modify/add cards always carry one
        tmpl = Element("template")
        card.children.append(tmpl)
    tmpl.children = list(parse_fragment(payload))


def snapshot_hash(snap: dict) -> str:
    """The ``doc_hash`` a baseline snapshot's lines hash to (§11.3)."""
    return canonical.doc_hash(
        snap["html"],
        snap.get("theme"),
        snap.get("body", []),
        doc_settings_line=snap.get("doc"),
    )


def _recorded_html_line(snapshot_html: str, state: DocState) -> str:
    """The reconstruction's ``<html>`` line as a baseline compares it (§6.7):
    of the open tag only the declared version is recorded state (§3.7).
    Nothing records ``lang`` or ``dir`` and reconcile keeps the file's own,
    so those are taken from the snapshot; compared as the file has them, an
    out-of-band ``lang`` edit would be a mismatch no event can explain."""
    shell = next(
        (n for n in parse_fragment(snapshot_html + "</html>") if isinstance(n, Element)), None
    )
    if shell is None or shell.tag != "html":
        return state.html_open_line()
    declared = state.html.get("data-aim-version")
    if declared is None:
        shell.remove_attr("data-aim-version")
    else:
        shell.set("data-aim-version", declared)
    return f"<html{canonical.canonical_attrs(shell, in_svg=False)}>"


def _snapshot_lines(snap: dict) -> list[str]:
    lines = [snap["html"]]
    if snap.get("doc"):
        lines.append(snap["doc"])
    if snap.get("theme"):
        lines.append(snap["theme"])
    return lines + list(snap.get("body", []))


def resolution_order(
    proposals: Sequence[Proposal],
    doc: AimDocument | None = None,
    *,
    accepting: bool = True,
) -> list[Proposal]:
    """Return pending cards in creation order.

    The only ordering exception is the format-level chained-add dependency:
    an add anchored on another pending add resolves after its anchor so normal
    accept/reject rebinding can make the concrete position available. The
    *doc* and *accepting* parameters remain for API compatibility; acceptance
    safety is enforced by projected proposal validation and the accept-all
    clone replay, never by reordering interacting proposals.
    """
    del doc, accepting
    return _creation_order(proposals)


# ===========================================================================
class DocState:
    """Structural operations over one document tree.

    Used in two modes: *live* (AimDocument mutating its real tree) and
    *replay* (verify/state_at mutating a deep copy). All content amounts to
    body constructs (chunks + containers) plus the theme block.
    """

    def __init__(self, html_el: Element):
        self.html = html_el
        body = html_el.find(lambda e: e.tag == "body")
        head = html_el.find(lambda e: e.tag == "head")
        if body is None or head is None:
            raise ParseError("document has no <head>/<body>")
        self.body = body
        self.head = head

    # -- sections ------------------------------------------------------------
    def constructs(self) -> list[Element]:
        return [e for e in self.body.elements() if e.tag not in _BODY_SECTIONS]

    def section(self, tag: str) -> Element | None:
        return next((e for e in self.body.elements() if e.tag == tag), None)

    def script(self, kind: str) -> Element | None:
        want = REGISTRY.script_types[kind]
        where = self.head if kind in ("meta", "doc") else self.body
        return next(
            (e for e in where.elements() if e.tag == "script" and e.get("type") == want), None
        )

    def theme_el(self) -> Element | None:
        return next(
            (e for e in self.head.elements() if e.tag == "style" and e.has("data-aim-theme")), None
        )

    def css_el(self) -> Element | None:
        return next(
            (e for e in self.head.elements() if e.tag == "style" and e.has("data-aim-css")), None
        )

    # -- lookup ----------------------------------------------------------------
    def top_index(self, target: str) -> int | None:
        for i, e in enumerate(self.constructs()):
            if e.chunk_id == target or e.container_id == target:
                return i
        return None

    def container_node(self, cid: str) -> Element | None:
        if cid == "body":
            return self.body
        for e in self.constructs():
            hit = e.find(lambda x: x.container_id == cid)
            if hit is not None:
                return hit
        return None

    def find_chunk(self, cid: str) -> tuple[Element | None, list[Element]]:
        """-> (parent element or None-for-top, member elements of the run)."""
        hits: list[tuple[Element, Element]] = []

        def walk(parent: Element) -> None:
            for child in parent.elements():
                if child.tag == "template":
                    continue
                if child.chunk_id == cid:
                    hits.append((parent, child))
                walk(child)

        for top in self.constructs():
            if top.chunk_id == cid:
                hits.append((self.body, top))
            walk(top)
        if not hits:
            return None, []
        parent = hits[0][0]
        return parent, [el for p, el in hits if p is parent]

    def exists(self, target: str) -> bool:
        if target == "aim:theme":
            return self.theme_el() is not None
        if target == "aim:doc":
            return self.script("doc") is not None
        if self.top_index(target) is not None:
            return True
        if self.container_node(target) is not None and target != "body":
            return True
        return bool(self.find_chunk(target)[1])

    def all_ids(self) -> set[str]:
        out: set[str] = set()
        for top in self.constructs():
            for el in top.iter():
                if el.chunk_id:
                    out.add(el.chunk_id)
                if el.container_id:
                    out.add(el.container_id)
        return out

    # -- serialization ---------------------------------------------------------
    def serial(self, target: str) -> str | None:
        if target == "aim:theme":
            t = self.theme_el()
            return serialize(t) if t is not None else None
        if target == "aim:doc":
            d = self.script("doc")
            return serialize(d) if d is not None else None
        i = self.top_index(target)
        if i is not None:
            return serialize(self.constructs()[i])
        cont = self.container_node(target)
        if cont is not None and cont is not self.body:
            return serialize(cont)
        parent, members = self.find_chunk(target)
        return serialize_run(members) if members else None

    def spec_version(self) -> str | None:
        return self.html.get("data-aim-version")

    def set_spec_version(self, value: str) -> None:
        """The declared spec version — hashed state, so every write goes
        through a recorded event (see ``_record_version_upgrade``)."""
        self.html.set("data-aim-version", value)

    def html_open_line(self) -> str:
        return f"<html{canonical.canonical_attrs(self.html, in_svg=False)}>"

    def doc_hash(self) -> str:
        theme = self.theme_el()
        settings = self.script("doc")
        return canonical.doc_hash(
            self.html_open_line(),
            serialize(theme) if theme is not None else None,
            (serialize(c) for c in self.constructs()),
            doc_settings_line=(serialize(settings) if settings is not None else None),
        )

    def snapshot(self) -> dict:
        """The reduced projection (§11.3) written out as a baseline
        ``snapshot``: exactly the lines ``doc_hash`` hashes, structured."""
        snap: dict = {
            "html": self.html_open_line(),
            "body": [serialize(c) for c in self.constructs()],
        }
        settings = self.script("doc")
        if settings is not None:
            snap["doc"] = serialize(settings)
        theme = self.theme_el()
        if theme is not None:
            snap["theme"] = serialize(theme)
        return snap

    def load_snapshot(self, snap: dict) -> None:
        """Replace the hashed projection (declared version and the rest of the
        ``<html>`` open tag, settings, theme, body constructs) with a
        baseline snapshot's. Sections, caches and the head stay as they are."""
        shell = next(
            (n for n in parse_fragment(snap["html"] + "</html>") if isinstance(n, Element)), None
        )
        if shell is None or shell.tag != "html":
            raise HistoryError("baseline snapshot 'html' is not an <html> open tag")
        self.html.attrs = list(shell.attrs)
        self.set_doc_settings_markup(snap.get("doc"))
        self.set_theme_markup(snap.get("theme"))
        for el in self.constructs():
            self.body.children.remove(el)
        insert_at = next(
            (
                i
                for i, c in enumerate(self.body.children)
                if isinstance(c, Element) and c.tag in _BODY_SECTIONS
            ),
            len(self.body.children),
        )
        for line in snap.get("body", []):
            nodes = [n for n in parse_fragment(line) if isinstance(n, Element)]
            if len(nodes) != 1:
                raise HistoryError("a baseline snapshot entry is not exactly one construct")
            self.body.children.insert(insert_at, nodes[0])
            insert_at += 1

    def snapshot_lines(self) -> list[str]:
        """The projection as comparable lines, in snapshot order."""
        snap = self.snapshot()
        return _snapshot_lines(snap)

    # -- mutation ---------------------------------------------------------------
    def resolve_insert_point(self, anchor: Anchor) -> tuple[Element, int]:
        """Resolve an anchor to a concrete (parent, index) — validating,
        never mutating. Anchors resolve strictly *within* their stated
        container: an `after` id that exists elsewhere in the document is an
        error, not a silent cross-container insert."""
        if anchor.container == "body":
            if anchor.after is None:
                return self.body, 0
            el = next(
                (
                    e
                    for e in self.constructs()
                    if e.chunk_id == anchor.after or e.container_id == anchor.after
                ),
                None,
            )
            if el is None:
                raise TargetNotFound(f"anchor {anchor.after!r} not found at body level")
            return self.body, self.body.children.index(el) + 1
        cont = self.container_node(anchor.container)
        if cont is None:
            raise TargetNotFound(f"container {anchor.container!r} not found")
        if anchor.after is None:
            if anchor.shell is not None:
                shell = next((e for e in cont.elements() if e.tag == anchor.shell), None)
                if shell is None:
                    raise TargetNotFound(
                        f"shell <{anchor.shell}> not found in {anchor.container!r}"
                    )
                return shell, 0
            return cont, 0
        # the anchor construct must be a direct member of this container
        members = [
            el
            for el in cont.iter()
            if el is not cont and (el.chunk_id == anchor.after or el.container_id == anchor.after)
        ]
        if not members:
            raise TargetNotFound(f"anchor {anchor.after!r} not found in {anchor.container!r}")
        parent = self._parent_of(members[-1])
        direct = parent is cont or (
            cont.tag == "table"
            and parent.tag in REGISTRY.table_shells
            and self._parent_of(parent) is cont
        )
        if not direct:
            raise TargetNotFound(
                f"anchor {anchor.after!r} is nested content, not a direct "
                f"member of {anchor.container!r}"
            )
        if anchor.shell is not None:
            # §6.4: the shell names the section where the position resolves —
            # a concrete anchor whose recorded shell contradicts where the
            # anchor row actually sits must fail, not silently follow the id
            got = parent.tag if parent.tag in REGISTRY.table_shells else None
            if got != anchor.shell:
                raise TargetNotFound(
                    f"anchor {anchor.after!r} sits in <{got}>, not the recorded <{anchor.shell}>"
                )
        return parent, parent.children.index(members[-1]) + 1

    def _guard_item_members(self, parent: Element, nodes: list[Element]) -> None:
        """List/table containers hold only their item carriers (S022): any
        other member is invisible to item-aware consumers — an editor hides
        it, then the next container-level write destroys it."""
        cont = parent
        if cont.tag in REGISTRY.table_shells:
            cont = self._parent_of(cont)
        legal = [t for t, cs in REGISTRY.item_carriers.items() if cont.tag in cs]
        if not legal:
            return
        bad = next((n for n in nodes if n.tag not in legal), None)
        if bad is not None:
            raise InvalidOperation(
                f"<{bad.tag}> cannot be a direct member of <{cont.tag}> "
                f"container {cont.container_id!r} (expects <{'>/<'.join(legal)}>)"
            )

    def _guard_payload_container_members(self, nodes: list[Element]) -> None:
        """Validate direct members of every list/table container a payload
        carries — the roots themselves and marked descendant containers at
        any depth: a slide or grouping root must not smuggle in a nested
        container whose members the next lint rejects (S022)."""
        for root in nodes:
            for el in root.iter():
                if el.tag not in REGISTRY.containers or el.tag == "aim-slide":
                    continue
                if el is not root and el.container_id is None:
                    # an unmarked nested list/table is chunk content, not a
                    # container — lint holds only marked ones to S022
                    continue
                members: list[Element] = []
                for child in el.elements():
                    if el.tag == "table" and child.tag in REGISTRY.table_shells:
                        members.extend(child.elements())
                    else:
                        members.append(child)
                self._guard_item_members(el, members)

    def insert(self, markup: str, anchor: Anchor) -> None:
        nodes = [n for n in parse_fragment(markup) if isinstance(n, Element)]
        if not nodes:
            raise InvalidOperation("empty insert payload")
        parent, idx = self.resolve_insert_point(anchor)
        self._guard_item_members(parent, nodes)
        parent.children[idx:idx] = nodes

    def remove(self, target: str) -> str:
        i = self.top_index(target)
        if i is not None:
            el = self.constructs()[i]
            self.body.children.remove(el)
            return serialize(el)
        cont = self.container_node(target)
        if cont is not None and cont is not self.body:
            parent = self._parent_of(cont)
            parent.children.remove(cont)
            return serialize(cont)
        parent, members = self.find_chunk(target)
        if not members:
            raise TargetNotFound(f"cannot remove {target!r}: not found")
        for m in members:
            parent.children.remove(m)
        return serialize_run(members)

    def replace(self, target: str, markup: str) -> None:
        if target == "aim:doc":
            self.set_doc_settings_markup(markup)
            return
        if target == "aim:theme":
            self.set_theme_markup(markup)
            return
        i = self.top_index(target)
        if i is not None:
            el = self.constructs()[i]
            idx = self.body.children.index(el)
            self.body.children[idx : idx + 1] = parse_fragment(markup)
            return
        cont = self.container_node(target)
        if cont is not None and cont is not self.body:
            parent = self._parent_of(cont)
            nodes = parse_fragment(markup)
            self._guard_item_members(parent, [n for n in nodes if isinstance(n, Element)])
            idx = parent.children.index(cont)
            parent.children[idx : idx + 1] = nodes
            return
        parent, members = self.find_chunk(target)
        if not members:
            raise TargetNotFound(f"cannot replace {target!r}: not found")
        nodes = parse_fragment(markup)
        self._guard_item_members(parent, [n for n in nodes if isinstance(n, Element)])
        idx = parent.children.index(members[0])
        for m in members:
            parent.children.remove(m)
        parent.children[idx:idx] = nodes

    def _target_elements(self, target: str) -> list[tuple[Element, Element]]:
        """(parent, element) pairs for a target, in remove()'s lookup order."""
        i = self.top_index(target)
        if i is not None:
            return [(self.body, self.constructs()[i])]
        cont = self.container_node(target)
        if cont is not None and cont is not self.body:
            return [(self._parent_of(cont), cont)]
        parent, members = self.find_chunk(target)
        if not members or parent is None:
            raise TargetNotFound(f"cannot move {target!r}: not found")
        return [(parent, m) for m in members]

    def move(self, target: str, to: Anchor) -> None:
        if to.after == target:
            raise InvalidOperation(f"cannot move {target!r} after itself")
        originals = self._target_elements(target)
        # a destination equal to or inside the moved subtree vanishes with
        # the removal — reject while the tree is still untouched
        moved_ids: set[str] = set()
        for _, el in originals:
            for node in el.iter():
                moved_ids.update(filter(None, (node.chunk_id, node.container_id)))
        if to.container in moved_ids or (to.after is not None and to.after in moved_ids):
            raise InvalidOperation(f"cannot move {target!r} into itself or its own subtree")
        markup = self.serial(target)
        if markup is None:
            raise TargetNotFound(f"cannot move {target!r}: not found")
        # insert first, then remove the originals by identity: any anchor
        # failure raises before the first mutation, so the chunk is never
        # left removed with no event to show for it
        self.insert(markup, to)
        for parent, el in originals:
            parent.children.remove(el)

    def _parent_of(self, el: Element) -> Element:
        for top in [self.body] + self.constructs():
            for cand in top.iter():
                if el in cand.children:
                    return cand
        raise TargetNotFound("element has no parent (corrupt tree)")

    # -- theme --------------------------------------------------------------------
    def set_theme_markup(self, markup: str | None) -> None:
        current = self.theme_el()
        if markup is None:
            if current is not None:
                self.head.children.remove(current)
            return
        nodes = parse_fragment(markup)
        el = next((n for n in nodes if isinstance(n, Element)), None)
        if el is None or el.tag != "style" or not el.has("data-aim-theme"):
            raise InvalidOperation("theme payload must be a <style data-aim-theme> block")
        if current is not None:
            idx = self.head.children.index(current)
            self.head.children[idx] = el
        else:
            css = self.css_el()
            if css is not None:
                idx = self.head.children.index(css) + 1
                self.head.children.insert(idx, el)
            else:
                self.head.children.append(el)

    # -- document settings (aim:doc) -------------------------------------------
    def set_doc_settings_markup(self, markup: str | None) -> None:
        """Replace (or with ``None`` remove) the head settings block.

        Canonical head position: after the aim-meta cache, before the
        embedded stylesheet and the theme block (§2.1)."""
        current = self.script("doc")
        if markup is None:
            if current is not None:
                self.head.children.remove(current)
            return
        el = doc_settings_element(markup)
        if current is not None:
            idx = self.head.children.index(current)
            self.head.children[idx] = el
            return
        meta = self.script("meta")
        css = self.css_el()
        theme = self.theme_el()
        if meta is not None:
            idx = self.head.children.index(meta) + 1
        elif css is not None:
            idx = self.head.children.index(css)
        elif theme is not None:
            idx = self.head.children.index(theme)
        else:
            idx = len(self.head.children)
        self.head.children.insert(idx, el)

    def kind_of(self, target: str) -> str | None:
        """``"chunk"`` / ``"container"`` / None for an id in this document."""
        if target == "aim:theme":
            return "theme"
        if target == "aim:doc":
            return "doc"
        if self.find_chunk(target)[1]:
            return "chunk"
        cont = self.container_node(target)
        if cont is not None and cont is not self.body:
            return "container"
        return None

    def container_of_chunk(self, cid: str) -> str:
        i = self.top_index(cid)
        if i is not None:
            return "body"
        parent, members = self.find_chunk(cid)
        if not members:
            raise TargetNotFound(f"chunk {cid!r} not found")
        node: Element | None = parent
        while node is not None and node is not self.body:
            if node.container_id:
                return node.container_id
            node = self._parent_of(node)
        return "body"


# ===========================================================================
class AimDocument:
    """One .aim document: the artifact, the pending lane, and the history."""

    def __init__(self, fragment: Fragment):
        html = next((e for e in fragment.elements() if e.tag == "html"), None)
        if html is None:
            raise ParseError("not an .aim document (no <html> element)")
        self._fragment = fragment
        self._state = DocState(html)
        self._batch: str | None = None
        self._history_index: _HistoryIndex | None = None
        # auto-accept bookkeeping for the open (outermost) batch, spec §5.6
        self._batch_auto: bool | None = None
        self._batch_cards: list[_BatchCard] = []
        #: What the last batch close (or :meth:`auto_accept` call) accepted
        #: and deferred; None when the last batch had no card in scope.
        self.last_auto_accept: AutoAcceptOutcome | None = None

    # -- constructors ---------------------------------------------------------
    @classmethod
    def loads(cls, text: str) -> AimDocument:
        return cls(parse_html(text))

    @classmethod
    def load(cls, path: str | Path) -> AimDocument:
        return cls.loads(Path(path).read_text("utf-8"))

    # -- io ----------------------------------------------------------------------
    def dumps(self) -> str:
        """Canonical serialization (refreshes the machine-managed stylesheet).

        The declared ``data-aim-version`` is deliberately NOT touched: the
        ``<html …>`` open line is hashed state (§11.3), so an implicit
        upgrade would invalidate every checkpoint recorded under the old
        line. Documents carry their birth version; only :func:`new_document`
        stamps the current one. (The stylesheet is safe to refresh — it is
        machine-managed and excluded from hashing — and so is a present TOC
        cache, §8.1: it is rederived when its ``toc_doc_hash`` is stale.)"""
        css = self._state.css_el()
        if css is not None:
            css.raw = "\n" + generate_aim_css()
            css.set("data-aim-css", REGISTRY.spec_version)
        self._refresh_toc()
        return canonical.document_text(self._fragment)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.dumps(), "utf-8")

    def _clone(self) -> AimDocument:
        """A structural copy suitable for validation and replay."""
        burned = set(self._get_history_index().burned_ids)
        clone = AimDocument(parse_html(canonical.document_text(self._fragment)))
        clone._rebuild_history_index(burned_seed=burned)
        return clone

    def _projected_operation(
        self,
        label: str,
        operation: Callable[[AimDocument], _T],
        *,
        exclude: Sequence[str] = (),
    ) -> _T:
        """Run *operation* against current state plus the pending lane.

        Proposals created by the SDK must be legal in the state in which they
        will be accepted: every earlier pending card has been applied in
        creation order. Validation happens only on clones. If a previously
        valid operation becomes invalid after one card, name that conflicting
        proposal so an agent can repair its sequencing immediately.

        *exclude* names cards the caller is about to supersede (§5.4): they
        will never be applied, so the projection drops them up front —
        otherwise a pending delete would make its own replacement look
        illegal. Their payload ids stay burned because supersession records
        those payloads in history before the replacement can be accepted.
        """
        projection = self._clone()
        index = projection._get_history_index()
        for pid in exclude:
            proposal = projection.proposal(pid)
            if proposal.payload_html:
                index._burn(index._payload_ids(proposal.payload_html))
            card = projection._card_el(pid)
            sec = projection._state.section("aim-proposals")
            assert sec is not None
            sec.children.remove(card)
            index.remove_proposal(proposal)
            # excluding IS superseding: dependents (e.g. a card a dissolve
            # chained onto this move) must rebind exactly as the live
            # supersession will, or the projection dangles and every
            # replacement proposal is refused
            projection._rebind_chained(proposal, "superseded")
        order = _creation_order(projection.proposals)
        decider = Actor("external", id="pending-projection")

        try:
            operation(projection._clone())
            was_valid = True
        except AimError:
            was_valid = False
        conflict: str | None = None

        for proposal in order:
            try:
                projection.accept(proposal.id, decided_by=decider, at=proposal.at)
            except AimError as exc:
                raise InvalidOperation(
                    f"pending proposal {proposal.id!r} cannot be projected: {exc}"
                ) from exc
            try:
                operation(projection._clone())
            except AimError:
                if was_valid:
                    conflict = proposal.id
                was_valid = False
            else:
                conflict = None
                was_valid = True

        try:
            return operation(projection._clone())
        except AimError as exc:
            if conflict is not None:
                raise InvalidOperation(
                    f"pending proposal {conflict!r} conflicts with {label}: {exc}"
                ) from exc
            raise

    def _noop_guard(self, operation: Callable[[AimDocument], object]) -> None:
        """Reject a proposal that is a no-op against the CURRENT document.

        Meaningfulness is not the projection's question: a card that only
        becomes a no-op after earlier pendings resolve stays legal (accepting
        it is harmless), but one that changes nothing right now is an
        authoring error. Structural failures here are ignored — legality is
        judged by :meth:`_projected_operation`."""
        try:
            operation(self._clone())
        except _NoOpEdit:
            raise
        except AimError:
            pass

    # -- basic accessors ------------------------------------------------------------
    @property
    def spec_version(self) -> str | None:
        return self._state.html.get("data-aim-version")

    @property
    def lang(self) -> str | None:
        return self._state.html.get("lang")

    @property
    def title(self) -> str:
        el = self._state.head.find(lambda e: e.tag == "title")
        return el.text() if el is not None else ""

    @property
    def doc_hash(self) -> str:
        return self._state.doc_hash()

    @property
    def seq(self) -> int:
        index = self._get_history_index()
        if index.next_seq is not None:
            return index.next_seq - 1
        # Preserve the pre-index behavior on malformed history: reading the
        # events remains possible for lint's field diagnostics, while asking
        # for seq still raises/returns exactly through Event.seq.
        return index.events[-1].seq if index.events else 0

    @property
    def theme(self) -> dict[str, str]:
        """Theme slot assignments (empty dict when no theme block)."""
        el = self._state.theme_el()
        if el is None or not el.raw:
            return {}
        m = re.fullmatch(r":root\{(.*)\}", el.raw.strip(), re.S)
        if not m:
            return {}
        out: dict[str, str] = {}
        for piece in m.group(1).split(";"):
            if ":" in piece:
                k, v = piece.split(":", 1)
                out[k.strip()] = v.strip()
        return out

    @property
    def meta(self) -> dict | None:
        """The parsed metadata cache, or None when absent.

        Raises :class:`ParseError` when the block exists but is not a JSON
        object — a malformed cache is corrupt data, not a missing one.
        """
        el = self._state.script("meta")
        if el is None or not (el.raw or "").strip():
            return None
        import json

        try:
            obj = json.loads(el.raw.strip())
        except json.JSONDecodeError as exc:
            raise ParseError(f"aim-meta cache is not valid JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise ParseError("aim-meta cache is not a JSON object")
        return obj

    @property
    def doc_settings(self) -> dict:
        """The parsed aim:doc settings object (``{}`` when absent).

        Raises :class:`ParseError` when the block exists but is not a JSON
        object — a malformed settings block is corrupt data, not a missing
        one (D001)."""
        el = self._state.script("doc")
        try:
            return parse_doc_settings(el.raw if el is not None else None)
        except InvalidOperation as exc:
            raise ParseError(str(exc)) from exc

    @property
    def page_setup(self) -> PageSetup:
        """The document's resolved page setup (registry defaults when the
        settings block is absent or carries no ``page`` field)."""
        return page_setup_from_settings(self.doc_settings)

    @property
    def note(self) -> str | None:
        """The agent note's raw comment text, or None (spec §2.5)."""
        c = find_note(self._state.head)
        return c.data if c else None

    def has_canonical_note(self) -> bool:
        """Whether the note is byte-exactly canonical for this spec version."""
        data = self.note
        return data is not None and is_canonical(data, self.spec_version)

    def set_note(self) -> None:
        """Insert or refresh the canonical agent note (spec §2.5).

        Not an edit: no event is appended and ``doc_hash`` is unaffected —
        the note has the same standing as the derived caches (§7). A stale
        or foreign aim-note is replaced in place; otherwise the note lands
        immediately after ``<meta charset>``.
        """
        head = self._state.head
        data = render_note(self.spec_version)
        existing = find_note(head)
        if existing is not None:
            existing.data = data
            return
        anchor = 0
        for i, node in enumerate(head.children):
            if isinstance(node, Element) and node.tag == "meta" and node.get("charset") is not None:
                anchor = i + 1
                break
        head.children.insert(anchor, Comment(data))

    def remove_note(self) -> None:
        """Strip the agent note, if present. Not an edit (see set_note).

        Removes every matching comment: a document may carry duplicate
        notes (the S030 warning case) and "remove the note" must not leave
        one behind.
        """
        head = self._state.head
        while (c := find_note(head)) is not None:
            head.children.remove(c)

    # -- chunk views -------------------------------------------------------------
    @property
    def chunks(self) -> list[Chunk]:
        """Every chunk in document order — one tree walk.

        Semantics are those of :meth:`DocState.find_chunk` +
        :meth:`DocState.container_of_chunk` per id (pinned by
        ``tests/test_chunk_lookup.py``): an id's members are its hits under
        the parent of its first hit (template subtrees skipped), and its
        container is the nearest ``data-aim-container`` at or above that
        parent — ``body`` for top-level constructs."""
        state = self._state
        order: list[str] = []
        seen: set[str] = set()
        # id -> [(parent, element, container-at-parent)] in pre-order
        hits: dict[str, list[tuple[Element, Element, str]]] = {}
        top_ids: set[str] = set()

        def walk(parent: Element, container: str, in_template: bool) -> None:
            inner = parent.container_id or container
            for child in parent.elements():
                skipped = in_template or child.tag == "template"
                cid = child.chunk_id
                if cid:
                    if cid not in seen:
                        seen.add(cid)
                        order.append(cid)
                    if not skipped:
                        hits.setdefault(cid, []).append((parent, child, inner))
                walk(child, inner, skipped)

        for top in state.constructs():
            for marker in (top.chunk_id, top.container_id):
                if marker:
                    top_ids.add(marker)
            if top.chunk_id:
                if top.chunk_id not in seen:
                    seen.add(top.chunk_id)
                    order.append(top.chunk_id)
                hits.setdefault(top.chunk_id, []).append((state.body, top, "body"))
            walk(top, "body", False)

        out: list[Chunk] = []
        for cid in order:
            found = hits.get(cid, [])
            members = [el for p, el, _ in found if p is found[0][0]] if found else []
            if cid in top_ids:
                container = "body"
            elif not found:
                raise TargetNotFound(f"chunk {cid!r} not found")
            else:
                container = found[0][2]
            out.append(
                Chunk(
                    id=cid,
                    container=container,
                    tags=tuple(m.tag for m in members),
                    html=serialize_run(members),
                    text="".join(m.text() for m in members),
                )
            )
        return out

    def chunk(self, cid: str) -> Chunk:
        """One chunk view by id — a direct lookup, not a scan of ``chunks``."""
        _parent, members = self._state.find_chunk(cid)
        if not members:
            raise TargetNotFound(f"no chunk {cid!r}")
        return Chunk(
            id=cid,
            container=self._state.container_of_chunk(cid),
            tags=tuple(m.tag for m in members),
            html=serialize_run(members),
            text="".join(m.text() for m in members),
        )

    @property
    def containers(self) -> list[str]:
        out = []
        for top in self._state.constructs():
            for el in top.iter():
                if el.container_id:
                    out.append(el.container_id)
        return out

    @property
    def body_ids(self) -> list[str]:
        return [e.chunk_id or e.container_id or "" for e in self._state.constructs()]

    # -- history ---------------------------------------------------------------------
    def _history_raw(self) -> str | None:
        el = self._state.script("history")
        return el.raw if el is not None else None

    def _rebuild_history_index(self, *, burned_seed: Iterable[str] = ()) -> _HistoryIndex:
        self._history_index = _HistoryIndex.build(
            self._history_raw(), self.proposals, burned_seed=burned_seed
        )
        return self._history_index

    def _get_history_index(self) -> _HistoryIndex:
        raw = self._history_raw()
        if self._history_index is None:
            return self._rebuild_history_index()
        if self._history_index.raw is not raw:
            # The JSONL is authoritative even if an internal caller replaced
            # it without using one of the three normal writers. Preserve only
            # the instance-lifetime tombstones that cannot be reconstructed
            # after prune/flatten, then derive everything else afresh.
            burned = set(self._history_index.burned_ids)
            return self._rebuild_history_index(burned_seed=burned)
        return self._history_index

    def _history_events(self) -> list[Event]:
        return self._get_history_index().events

    @property
    def history(self) -> list[Event]:
        # Event.data is intentionally an open dict for forward-compatible
        # x_* fields. Return defensive copies so mutating a read result cannot
        # corrupt the cache or diverge it from the authoritative JSONL.
        return [Event(deepcopy(event.data)) for event in self._history_events()]

    def _history_script(self) -> Element:
        """The history block, created in its section slot when absent."""
        el = self._state.script("history")
        if el is None:
            el = Element("script", [("type", REGISTRY.script_types["history"])])
            el.raw = "\n"
            emb = self._state.script("embeddings")
            if emb is not None:
                idx = self._state.body.children.index(emb)
                self._state.body.children.insert(idx, el)
            else:
                self._state.body.children.append(el)
        return el

    def _append_event(self, data: dict) -> Event:
        index = self._get_history_index()
        el = self._history_script()
        body = (el.raw or "").rstrip("\n")
        line = canonical_json(data)
        el.raw = "\n" + (body + "\n" if body else "") + line + "\n"
        index.append_event(Event(deepcopy(data)), el.raw)
        return Event(data)

    # -- declared spec version ------------------------------------------------
    def _ensure_feature_version(
        self, markup: str | None, *, author: Actor, at: str | None = None
    ) -> str | None:
        """Record the version upgrade a gated construct requires (spec §3.3).

        Literal paint is a 0.3 feature and literal typography a 0.4 feature,
        so putting one into an older document changes what version that
        document conforms to. ``data-aim-version`` rides the ``<html>`` open
        tag, which ``doc_hash`` covers, so bumping it in place would break
        every checkpoint recorded under the old line — while leaving it
        declares a version the document no longer conforms to. Neither is
        allowed, and this repo already has the third option: every state
        change mutates the tree AND appends the matching event, so replay
        restores the old version and the old hashes verify again.

        The document is upgraded to the construct's own floor, not to this
        build's version: a 0.2 document gaining paint declares 0.3 and stays
        readable by every 0.3 tool — declaring the newest version the build
        happens to implement would shrink its audience for no reason.

        Call this BEFORE mutating, from every write path that can put a
        payload into the document. It returns the upgrade event's batch so
        the first gated edit can share that editing intention. The check is
        a dict lookup for the common case (a document already at a
        sufficient version) and costs a history replay only on the single
        edit that first puts a gated construct into a legacy document.
        """
        floor = _floor_of(markup)
        if floor is None:
            return None
        return self._ensure_version_floor(floor, author=author, at=at)

    def _ensure_version_floor(
        self, floor: str, *, author: Actor, at: str | None = None, label: str | None = None
    ) -> str | None:
        """Record the upgrade to *floor* when the declared version is older
        (spec §3.7) — the construct-agnostic core of
        :meth:`_ensure_feature_version`, also used for the JSON constructs
        (the review policy and the ``auto`` resolution marker, §5.6) that
        live in the settings block and history rather than in markup.

        The upgrade event is authored by *author*: the author of the event
        that needs it, never a stand-in."""
        declared = self._state.spec_version()
        if REGISTRY.version_includes(declared, floor):
            return None
        label = label or _floor_label(floor)
        if declared is None:
            raise InvalidOperation(
                f"cannot add {label}: <html> declares no data-aim-version (S001)"
            )
        if not REGISTRY.implements(declared):
            return None  # already ahead of this build; its version is not ours to set
        problems = self.verify()
        if problems:
            raise InvalidOperation(
                f"cannot add {label}: the version upgrade it requires must be recorded, and "
                f"this history cannot account for the document as it stands ({problems[0]}). "
                "Repair or flatten the history first."
            )
        batch = self._batch_id()
        self._state.set_spec_version(floor)
        self._append_event(
            {
                "seq": self.seq + 1,
                "kind": "direct_edit",
                "t": at or _now_iso(),
                "target": VERSION_TARGET,
                "action": "modify",
                "before": declared,
                "after": floor,
                "author": author.to_obj(),
                "batch": batch,
                "explanation": f"{label} requires spec {floor}",
            }
        )
        return batch

    def _preflight_feature_upgrade(
        self, markup: str | None, operation: Callable[[AimDocument], object]
    ) -> None:
        """Prove *operation* can finish before recording a version upgrade.

        The upgrade event must precede the edit that needs it, but a failed
        edit must not leave an older document upgraded with no gated
        construct added. Only the once-per-document older-version path pays
        for the clone.
        """
        floor = _floor_of(markup)
        if floor is not None:
            self._preflight_version_floor(floor, operation)

    def _preflight_version_floor(
        self, floor: str, operation: Callable[[AimDocument], object]
    ) -> None:
        """:meth:`_preflight_feature_upgrade` for an explicit floor."""
        declared = self._state.spec_version()
        if (
            declared is not None
            and not REGISTRY.version_includes(declared, floor)
            and REGISTRY.implements(declared)
        ):
            operation(self._clone())

    def _retained_floors(self, part: FloorPart = "all") -> set[str]:
        """Every gated-construct floor retained by live, pending, or
        historical state. The body serialization covers live constructs and
        pending templates; the head settings block (where the review policy
        lives, §5.6) sits outside the body and is inspected on its own; raw
        history scripts are inert DOM text, so every markup field history
        retains is inspected separately, along with the since-gated event
        kinds (``baseline``) and fields (the ``auto`` resolution marker).
        *part* as for :func:`_payload_floors`: the review policy and the
        ``auto`` marker are the ``"review"`` part, everything else ``"other"``."""
        review, other = part != "other", part != "review"
        floors = _payload_floors(serialize(self._state.body), part)
        settings = self._state.script("doc")
        if review and settings is not None:
            floor = _settings_floor(settings)
            if floor is not None:
                floors.add(floor)
        field_floors = REGISTRY.event_field_floors
        for event in self._history_events():
            for key in ("before", "after", "proposed", "applied"):
                value = event.get(key)
                if isinstance(value, str):
                    floors |= _payload_floors(value, part)
            if other:
                since = REGISTRY.event_since.get(event.kind if "kind" in event.data else "")
                if since is not None:
                    floors.add(since)  # the event kind itself is newer markup
            snap = event.get("snapshot")
            if isinstance(snap, dict) and isinstance(snap.get("body"), list):
                for line in snap["body"]:
                    if isinstance(line, str):
                        floors |= _payload_floors(line, part)
            if review:
                for key, floor in field_floors.items():
                    if key in event.data:
                        floors.add(floor)
        return floors

    def _retains_literal_paint(self) -> bool:
        """Whether live, pending, or historical state retains paint syntax."""
        return REGISTRY.paint_since in self._retained_floors()

    # -- batching -----------------------------------------------------------------
    def _next_batch(self) -> str:
        return self._get_history_index().next_batch

    @contextlib.contextmanager
    def batch(self, *, auto_accept: bool | None = None):
        """Group the edits made inside the ``with`` into one batch id.

        The batch is also the unit of auto-accept (spec §5.6): when the
        OUTERMOST batch exits normally, the proposals created inside it that
        are in scope are accepted in the same batch, in creation order, all or
        nothing (see :meth:`auto_accept`). Cards stay pending while the block
        runs, so chained adds and :meth:`amend_proposal` keep working, and one
        batch (one AI turn) becomes one undo. Nothing is accepted when the
        block raises.

        *auto_accept*: ``None`` honours the document's review policy, ``True``
        requests acceptance of every card created inside (``auto:
        "request"``), ``False`` keeps every card pending even under the
        policy (a tool-level override for preview copies and fallback
        replays; a host that persists an interrupted agent turn should use
        it too). A nested ``batch()`` inherits the outermost setting.
        """
        if self._batch is not None:
            yield self._batch  # nested: reuse the open batch (and its knob)
            return
        self._batch = self._next_batch()
        self._batch_auto = auto_accept
        self._batch_cards = []
        self.last_auto_accept = None
        try:
            yield self._batch
            if self._batch_cards:
                self._auto_accept_at_close()
        finally:
            self._batch = None
            self._batch_auto = None
            self._batch_cards = []

    def _batch_id(self) -> str:
        return self._batch or self._next_batch()

    # -- payload plumbing ------------------------------------------------------------
    def _recorded_ids(self, *, skip_payload_of: str | None = None) -> set[str]:
        """Ids mentioned by history or the pending lane (live or burned),
        plus ids whose burn record was pruned/flattened away this session.

        ``skip_payload_of`` leaves one pending card's payload ids out: at
        resolution time they are the write's own reservations (minted when
        the card was created), not competing claims."""
        return self._get_history_index().recorded(skip_payload_of=skip_payload_of)

    def _taken_ids(self, *, skip_payload_of: str | None = None) -> set[str]:
        return self._state.all_ids() | self._recorded_ids(skip_payload_of=skip_payload_of)

    def _guard_replacement_kind(self, target: str, first: Element, kind: str | None = None) -> None:
        """A replacement keeps the target's kind (§4.3): an ``aim-slide``
        root can only ever be a container, and a container target can only
        take a container-capable root — otherwise the write demotes one into
        the other and the document fails V003/S031 on the very next lint."""
        kind = kind or self._state.kind_of(target)
        if kind == "chunk" and first.tag == "aim-slide":
            raise InvalidOperation(
                f"an aim-slide payload cannot replace chunk {target!r} "
                "(slides are containers; delete the chunk and add the slide)"
            )
        if kind == "container" and first.tag not in REGISTRY.containers:
            raise InvalidOperation(
                f"payload root <{first.tag}> cannot replace container {target!r}"
            )

    def _normalize_payload(
        self,
        markup: str,
        *,
        expect_id: str | None = None,
        expect_marker: str | None = None,
        assign: bool = True,
        skip_payload_of: str | None = None,
    ) -> tuple[str, str]:
        """Parse, validate and canonicalize an edit payload.

        Returns ``(chunk_id, canonical_markup)``. New chunks get fresh ids
        assigned (a valid, unused id already present in the payload is
        honored so callers can pick deterministic ids). ``skip_payload_of``
        names a pending card whose payload is being written: the ids that
        card reserved for itself stay honored instead of reading as
        collisions with the card's own record.
        """
        nodes = [n for n in parse_fragment(markup) if isinstance(n, Element)]
        if not nodes:
            raise InvalidOperation("payload contains no element")
        self._state._guard_payload_container_members(nodes)
        marker_ids = {n.chunk_id or n.container_id for n in nodes}
        if len(marker_ids) != 1:
            if not all(n.chunk_id is None and n.container_id is None for n in nodes):
                raise InvalidOperation("payload run must share one data-aim value")
        if len(nodes) > 1 and any(n.tag not in REGISTRY.item_carriers for n in nodes):
            raise InvalidOperation(
                "multi-element payloads (runs) are only legal for list/table items"
            )
        first = nodes[0]
        payload_id = first.chunk_id or first.container_id
        taken = self._taken_ids(skip_payload_of=skip_payload_of)
        owned: set[str] = set()
        if expect_id is not None:
            # the target's live kind decides the marker; a mismatched marker
            # would silently demote a container into a chunk (or vice versa).
            # For accept-with-tweaks on adds the target isn't live yet — the
            # caller passes the proposed root's marker via expect_marker.
            live_kind = self._state.kind_of(expect_id)
            if live_kind in ("chunk", "container"):
                self._guard_replacement_kind(expect_id, first, live_kind)
            if live_kind == "container":
                marker = "data-aim-container"
            elif live_kind == "chunk":
                marker = "data-aim"
            else:
                marker = expect_marker or _payload_marker(first)
            wrong = "data-aim" if marker == "data-aim-container" else "data-aim-container"
            # every run root, not just the first: a wrong marker on a later
            # root would survive normalization and be written into the body
            for n in nodes:
                if n.get(wrong) is not None:
                    raise InvalidOperation(
                        f"payload marks a root with {wrong}, but target "
                        f"{expect_id!r} is a {self._state.kind_of(expect_id)}"
                    )
            if payload_id is None:
                for n in nodes:
                    n.set(marker, expect_id)
                payload_id = expect_id
            elif payload_id != expect_id:
                raise InvalidOperation(
                    f"payload id {payload_id!r} does not match target {expect_id!r}"
                )
            # ids currently living inside the target's own subtree may be
            # reused by the replacement; everything else stays off-limits
            _, members = self._state.find_chunk(expect_id)
            roots = members or (
                [self._state.container_node(expect_id)]
                if self._state.container_node(expect_id) is not None
                else []
            )
            for root in roots:
                for el in root.iter():
                    if el is root:
                        continue
                    owned.update(filter(None, (el.chunk_id, el.container_id)))
        elif assign:
            # whether the id is re-minted or honored, it lands on the
            # tag-derived marker ONLY: an aim-slide root arriving as data-aim
            # would otherwise be written as an S031-failing chunk (§4.3 —
            # slides can only ever be containers), and a stale wrong-marker
            # attribute surviving a re-mint would double-mark the root
            marker = _payload_marker(first)
            wrong = "data-aim" if marker == "data-aim-container" else "data-aim-container"
            if not payload_id or payload_id in taken or not ids.is_valid_chunk_id(payload_id):
                new = ids.new_id(taken)
                for n in nodes:
                    n.remove_attr(wrong)
                    n.set(marker, new)
                payload_id = new
            else:
                # ANY member carrying the wrong marker taints the run — the
                # first member alone would let a dual-marked second member
                # write a document the linter rejects (S025/S019/V003/H006)
                if any(n.get(wrong) is not None for n in nodes):
                    for n in nodes:
                        n.remove_attr(wrong)
                        n.set(marker, payload_id)
                taken.add(payload_id)
        if expect_id is not None or assign:
            # item chunks / nested containers inside a container payload:
            # honor valid unused (or target-owned) ids, assign fresh ones to
            # the rest; members of one run keep sharing one id. Direct items
            # that carry no marker at all are covered with fresh ids too —
            # a container payload must never introduce unaddressable rows.
            taken -= owned
            # the roots' own id stays reserved: for a pending add,
            # ``skip_payload_of`` dropped it from *taken* along with the
            # card's nested ids, and a descendant reusing it must be
            # reminted, never honored — the write would land an S019
            # duplicate that verify() cannot see
            if payload_id:
                taken.add(payload_id)
            remap: dict[str, str] = {}
            for n in nodes:
                if n.container_id is not None:
                    for item in self._direct_payload_items(n):
                        if item.chunk_id is None and item.container_id is None:
                            item.set("data-aim", ids.new_id(taken))
                for el in n.iter():
                    if el is n:
                        continue
                    for marker in ("data-aim", "data-aim-container"):
                        val = el.get(marker)
                        if val is None:
                            continue
                        if val in remap:
                            el.set(marker, remap[val])
                        elif not val or val in taken or not ids.is_valid_chunk_id(val):
                            fresh = ids.new_id(taken)
                            if val:
                                remap[val] = fresh
                            el.set(marker, fresh)
                        else:
                            taken.add(val)
                            remap[val] = val
        assert payload_id is not None
        return payload_id, "".join(serialize(n) for n in nodes)

    @staticmethod
    def _direct_payload_items(root: Element) -> list[Element]:
        """Direct members of a payload container root that must carry ids:
        li/tr items of list/table shells, and — since every slide child is a
        positioned chunk (or nested container) — all element children of an
        aim-slide."""
        if root.tag == "aim-slide":
            return root.elements()
        out: list[Element] = []
        for child in root.elements():
            if child.tag in REGISTRY.table_shells and root.tag == "table":
                out += [r for r in child.elements() if r.tag == "tr"]
            elif child.tag in REGISTRY.item_carriers:
                out.append(child)
        return out

    def _direct_members(self, cont: Element) -> list[Element]:
        """A container's direct member constructs (rows seen through their
        table shells; nested containers count as members, their items do not)."""
        out: list[Element] = []
        for child in cont.elements():
            if child.tag in REGISTRY.table_shells and cont.tag == "table":
                out += [r for r in child.elements() if r.chunk_id]
            elif child.chunk_id or child.container_id:
                out.append(child)
        return out

    def _resolve_end_anchor(
        self,
        container: str,
        after: AnchorAfter,
        *,
        exclude: str | None = None,
        shell: str | None = None,
    ) -> Anchor:
        if shell is not None and shell not in REGISTRY.table_shells:
            raise InvalidOperation(f"unknown table shell {shell!r}")
        if isinstance(after, _Last):
            if container == "body":
                pool = self._state.constructs()
            else:
                cont = self._state.container_node(container)
                if cont is None:
                    raise TargetNotFound(f"container {container!r} not found")
                pool = self._direct_members(cont)
            last: str | None = None
            for el in pool:
                cid = el.chunk_id or el.container_id
                if cid and cid != exclude:
                    last = cid
            anchor = Anchor(container, last)
        else:
            if exclude is not None and after == exclude:
                raise InvalidOperation(f"cannot anchor {exclude!r} after itself")
            anchor = Anchor(container, after)
        table = None
        if container != "body":
            cont = self._state.container_node(container)
            if cont is not None and cont.tag == "table":
                table = cont
        if table is None:
            if shell is not None:
                raise InvalidOperation("shell anchors apply only to table containers")
            return anchor
        if anchor.after is None:
            if shell is not None:  # a caller-chosen first-of-shell position
                if not any(s.tag == shell for s in table.elements()):
                    raise TargetNotFound(f"shell <{shell}> not found in {container!r}")
                return Anchor(container, None, shell=shell)
            shells = [s.tag for s in table.elements() if s.tag in REGISTRY.table_shells]
            # data rows default into the body section, not the header
            default = "tbody" if "tbody" in shells else (shells[0] if shells else None)
            return Anchor(container, None, shell=default)
        # every table anchor carries its shell: distinct first positions in
        # thead/tbody/tfoot must not collapse into one "same position"
        row = next(
            (
                el
                for el in table.iter()
                if el is not table
                and (el.chunk_id == anchor.after or el.container_id == anchor.after)
            ),
            None,
        )
        if row is not None:
            parent = self._state._parent_of(row)
            row_shell = parent.tag if parent.tag in REGISTRY.table_shells else None
            if shell is not None and row_shell != shell:
                raise InvalidOperation(
                    f"anchor {anchor.after!r} sits in <{row_shell}>, not <{shell}>"
                )
            return Anchor(container, anchor.after, shell=row_shell)
        return anchor

    # -- direct edits -------------------------------------------------------------------
    def add_chunk(
        self,
        markup: str,
        *,
        author: Actor,
        container: str = "body",
        after: AnchorAfter = LAST,
        explanation: str | None = None,
        at: str | None = None,
    ) -> Chunk:
        """Add a chunk (direct edit). ``after=None`` inserts at first position."""
        cid, payload = self._normalize_payload(markup)
        anchor = self._resolve_end_anchor(container, after)
        self._preflight_feature_upgrade(payload, lambda trial: trial._state.insert(payload, anchor))
        upgrade_batch = self._ensure_feature_version(payload, author=author, at=at)
        self._state.insert(payload, anchor)
        data = {
            "seq": self.seq + 1,
            "kind": "direct_edit",
            "t": at or _now_iso(),
            "target": cid,
            "action": "add",
            "anchor": anchor.to_obj(),
            "after": payload,
            "author": author.to_obj(),
            "batch": upgrade_batch or self._batch_id(),
        }
        if explanation:
            data["explanation"] = explanation
        self._append_event(data)
        # The normalized payload already is the canonical view of the unit we
        # inserted. Re-discovering it through chunk() would rebuild every
        # chunk view in the growing tree after every bulk-import add; no tree
        # cache is needed to return information this method already owns.
        roots = [node for node in parse_fragment(payload) if isinstance(node, Element)]
        return Chunk(
            id=cid,
            container=container,
            tags=tuple(root.tag for root in roots),
            html=payload,
            text="".join(root.text() for root in roots),
        )

    def modify_chunk(
        self,
        cid: str,
        markup: str,
        *,
        author: Actor,
        explanation: str | None = None,
        at: str | None = None,
    ) -> Chunk:
        before = self._state.serial(cid)
        if before is None:
            raise TargetNotFound(f"no chunk {cid!r}")
        if cid == "aim:theme":
            # reserved heads have their own grammar: the generic funnel
            # would stamp data-aim onto the <style>/<script> block
            payload = self._validated_theme_markup(markup)
        elif cid == "aim:doc":
            payload = self._validated_doc_markup(markup)
        else:
            _, payload = self._normalize_payload(markup, expect_id=cid)
        if payload == before:
            raise _NoOpEdit("modify with identical content")
        self._preflight_feature_upgrade(payload, lambda trial: trial._state.replace(cid, payload))
        upgrade_batch = self._ensure_feature_version(payload, author=author, at=at)
        self._state.replace(cid, payload)
        data = {
            "seq": self.seq + 1,
            "kind": "direct_edit",
            "t": at or _now_iso(),
            "target": cid,
            "action": "modify",
            "before": before,
            "after": payload,
            "author": author.to_obj(),
            "batch": upgrade_batch or self._batch_id(),
        }
        if explanation:
            data["explanation"] = explanation
        self._append_event(data)
        if self._state.kind_of(cid) == "chunk":
            # The normalized payload IS the chunk's canonical view (as in
            # add_chunk): rebuilding every chunk view to find this one made
            # each modify O(document), and bulk writers quadratic.
            roots = [node for node in parse_fragment(payload) if isinstance(node, Element)]
            return Chunk(
                id=cid,
                container=self._state.container_of_chunk(cid),
                tags=tuple(root.tag for root in roots),
                html=payload,
                text="".join(root.text() for root in roots),
            )
        try:
            return self.chunk(cid)
        except TargetNotFound:  # container target: synthesize the view
            root = parse_fragment(payload)[0]
            assert isinstance(root, Element)
            node = self._state.container_node(cid)
            parent_container = "body"
            walk = self._state._parent_of(node) if node is not None else None
            while walk is not None and walk is not self._state.body:
                if walk.container_id:
                    parent_container = walk.container_id
                    break
                walk = self._state._parent_of(walk)
            return Chunk(
                id=cid, container=parent_container, tags=(root.tag,), html=payload, text=root.text()
            )

    def replace_text(
        self,
        cid: str,
        old_text: str,
        new_text: str,
        *,
        author: Actor,
        explanation: str | None = None,
        at: str | None = None,
    ) -> Chunk:
        """Direct edit: replace the one occurrence of *old_text* in chunk
        *cid*'s text with *new_text*, keeping the id and the inline markup
        around it — a recorded ``modify`` like :meth:`modify_chunk`.
        Matching and refusal rules: :mod:`aimformat.textedit` (decision
        READS-D13)."""
        from .textedit import replace_in_markup

        markup = replace_in_markup(self.chunk(cid).html, old_text, new_text)
        return self.modify_chunk(cid, markup, author=author, explanation=explanation, at=at)

    def delete_chunk(
        self,
        cid: str,
        *,
        author: Actor,
        explanation: str | None = None,
        at: str | None = None,
    ) -> None:
        _no_delete_move(cid, "delete")
        before = self._state.serial(cid)
        if before is None:
            raise TargetNotFound(f"no chunk {cid!r}")
        anchor = self._anchor_of(cid)
        self._guard_geometry_dependents(None, vacated_block=cid, incoming_dst=None)
        anchored = self._cards_anchored_on(cid)
        if anchored:
            # a dissolve mutates pending cards, and a direct edit's event
            # records only the body change — undo() could restore the block
            # but not the cards. Direct deletes therefore refuse; resolve or
            # re-anchor the cards first (accepted delete PROPOSALS dissolve:
            # resolutions are not undoable)
            names = ", ".join(repr(c.get("id") or "") for c in anchored)
            raise InvalidOperation(
                f"cannot delete {cid!r}: pending cards ({names}) anchor on it — "
                "resolve or re-anchor them first"
            )
        self._state.remove(cid)
        self._rebind_removed_anchor(cid, anchor)
        data = {
            "seq": self.seq + 1,
            "kind": "direct_edit",
            "t": at or _now_iso(),
            "target": cid,
            "action": "delete",
            "before": before,
            "anchor": anchor.to_obj(),
            "author": author.to_obj(),
            "batch": self._batch_id(),
        }
        if explanation:
            data["explanation"] = explanation
        self._append_event(data)

    def move_chunk(
        self,
        cid: str,
        *,
        author: Actor,
        container: str = "body",
        after: AnchorAfter = LAST,
        shell: str | None = None,
        explanation: str | None = None,
        at: str | None = None,
    ) -> None:
        """Move a chunk (direct edit). ``shell`` picks the row section
        (thead/tbody/tfoot) for a first-position move in a table container."""
        _no_delete_move(cid, "move")
        if not self._state.exists(cid):
            raise TargetNotFound(f"no chunk {cid!r}")
        nested_parent = self._nested_move_parent(cid)
        if nested_parent is not None:
            raise InvalidOperation(
                f"cannot move {cid!r} out of nested markup in {nested_parent!r}; "
                "modify the enclosing target instead"
            )
        src = self._anchor_of(cid)
        dst = self._resolve_end_anchor(container, after, exclude=cid, shell=shell)
        if src == dst:  # full anchors: first-of-thead ≠ first-of-tbody
            raise _NoOpEdit(f"move of {cid!r} is a no-op (already at that position)")
        self._state.move(cid, dst)
        data = {
            "seq": self.seq + 1,
            "kind": "direct_edit",
            "t": at or _now_iso(),
            "target": cid,
            "action": "move",
            "from": src.to_obj(),
            "to": dst.to_obj(),
            "author": author.to_obj(),
            "batch": self._batch_id(),
        }
        if explanation:
            data["explanation"] = explanation
        self._append_event(data)

    @staticmethod
    def _check_theme_slots(slots: dict[str, str]) -> None:
        """Slot names AND values against the registry grammars — the write
        path must not produce documents its own linter rejects (V011/V012)."""
        for name, value in slots.items():
            slot = REGISTRY.theme_slots.get(name)
            if slot is None:
                raise InvalidOperation(f"unknown theme slot {name!r}")
            pattern = REGISTRY.theme_patterns.get(slot["type"])
            if pattern and not pattern.match(value):
                raise InvalidOperation(
                    f"theme slot {name} value {value!r} does not match the {slot['type']} grammar"
                )

    def set_theme(
        self,
        slots: dict[str, str],
        *,
        author: Actor,
        explanation: str | None = None,
        at: str | None = None,
    ) -> None:
        """Replace the theme block (aim:theme modify; whole-block payload)."""
        self._check_theme_slots(slots)
        before = self._state.serial("aim:theme")
        body = "; ".join(f"{k}:{v}" for k, v in sorted(slots.items()))
        markup = f"<style data-aim-theme>:root{{{body}}}</style>"
        if markup == before:
            raise InvalidOperation("theme unchanged")
        self._state.set_theme_markup(markup)
        data = {
            "seq": self.seq + 1,
            "kind": "direct_edit",
            "t": at or _now_iso(),
            "target": "aim:theme",
            "action": "modify",
            "after": markup,
            "author": author.to_obj(),
            "batch": self._batch_id(),
        }
        if before is not None:
            data["before"] = before
        if explanation:
            data["explanation"] = explanation
        self._append_event(data)

    def _doc_settings_markup(self, page: PageSetup | dict) -> str:
        """The whole settings block with ``page`` replaced — unknown fields
        an aim:doc block already carries are preserved (forward compat)."""
        setup = page if isinstance(page, PageSetup) else page_setup_from_obj(page)
        settings = dict(self.doc_settings)
        settings["page"] = setup.to_obj()
        return self._settings_script(settings)

    def set_page_setup(
        self,
        page: PageSetup | dict,
        *,
        author: Actor,
        explanation: str | None = None,
        at: str | None = None,
    ) -> PageSetup:
        """Set the page setup (aim:doc modify; whole-block payload).

        ``page`` is a :class:`PageSetup` or its object form (``size``,
        ``orientation``, ``margins``); values are validated against the
        registry grammars before anything mutates."""
        markup = self._doc_settings_markup(page)
        before = self._state.serial("aim:doc")
        if markup == before:
            raise InvalidOperation("page setup unchanged")
        self._state.set_doc_settings_markup(markup)
        data = {
            "seq": self.seq + 1,
            "kind": "direct_edit",
            "t": at or _now_iso(),
            "target": "aim:doc",
            "action": "modify",
            "after": markup,
            "author": author.to_obj(),
            "batch": self._batch_id(),
        }
        if before is not None:
            data["before"] = before
        if explanation:
            data["explanation"] = explanation
        self._append_event(data)
        return self.page_setup

    def propose_page_setup(
        self,
        page: PageSetup | dict,
        *,
        author: Actor,
        explanation: str | None = None,
        depends_on: str | None = None,
        at: str | None = None,
        accept: bool | None = None,
        accept_by: Actor | None = None,
    ) -> Proposal:
        """Propose a page setup (pending aim:doc modify, like a theme swap).

        ``accept``/``accept_by`` as for :meth:`propose_modify`."""
        self._check_accept_args(accept, accept_by)
        markup = self._doc_settings_markup(page)
        pid = self._new_proposal_id()
        with self.batch():
            self._supersede_if_pending("aim:doc", pid, author, at)
            proposal = self._new_card(
                action="modify",
                author=author,
                target="aim:doc",
                payload=markup,
                anchor=None,
                explanation=explanation,
                depends_on=depends_on,
                at=at,
                pid=pid,
            )
            self._register_card(proposal, accept, accept_by, at)
        return self._returned(proposal)

    def _anchor_of(self, target: str) -> Anchor:
        """The position *target* currently occupies (works for chunks and
        nested containers alike)."""
        i = self._state.top_index(target)
        if i is not None:
            constructs = self._state.constructs()
            prev = constructs[i - 1] if i > 0 else None
            return Anchor("body", (prev.chunk_id or prev.container_id) if prev else None)
        parent, members = self._state.find_chunk(target)
        if members:
            first = members[0]
        else:
            node = self._state.container_node(target)
            if node is None:
                raise TargetNotFound(f"no chunk or container {target!r}")
            first = node
            parent = self._state._parent_of(node)
        prev_id: str | None = None
        for sib in parent.elements():
            if sib is first:
                break
            sid = sib.chunk_id or sib.container_id  # containers anchor too
            if sid and sid != target:
                prev_id = sid
        shell = parent.tag if parent.tag in REGISTRY.table_shells else None
        walk: Element | None = parent
        container = "body"
        while walk is not None and walk is not self._state.body:
            if walk.container_id and walk.container_id != target:
                container = walk.container_id
                break
            walk = self._state._parent_of(walk)
        return Anchor(container, prev_id, shell=shell)

    def _nested_move_parent(self, target: str) -> str | None:
        """Addressable parent payload needed to invert a nested extraction.

        Direct body/container/table-shell members have a normal ``Anchor``.
        Anything deeper is private chunk/container markup, so the smallest
        enclosing addressable payload must be retained for exact replay.
        """
        pairs = self._state._target_elements(target)
        parent = pairs[0][0]
        if (
            parent is self._state.body
            or parent.container_id is not None
            or parent.tag in REGISTRY.table_shells
        ):
            return None
        walk: Element | None = parent
        while walk is not None and walk is not self._state.body:
            parent_id = walk.chunk_id or walk.container_id
            if parent_id is not None and parent_id != target:
                return parent_id
            walk = self._state._parent_of(walk)
        return None

    # -- checkpoints / undo ----------------------------------------------------------------
    def checkpoint(self, label: str, *, at: str | None = None) -> str:
        h = self.doc_hash
        self._append_event(
            {
                "seq": self.seq + 1,
                "kind": "checkpoint",
                "t": at or _now_iso(),
                "label": label,
                "doc_hash": h,
            }
        )
        return h

    @overload
    def undo(
        self,
        *,
        author: Actor,
        at: str | None = ...,
        whole_batch: Literal[False] = ...,
        batch: None = ...,
    ) -> Event: ...

    @overload
    def undo(
        self,
        *,
        author: Actor,
        at: str | None = ...,
        whole_batch: Literal[True],
        batch: str | None = ...,
    ) -> list[Event]: ...

    def undo(
        self,
        *,
        author: Actor,
        at: str | None = None,
        whole_batch: bool = False,
        batch: str | None = None,
    ) -> Event | list[Event]:
        """Append the inverse of the most recent not-yet-undone edit.

        ``aim:version`` upgrades are never inverted (spec §6.6): the gated
        construct stays in history, so the inverse could never apply; the
        walk steps over them to the edits below.

        ``whole_batch=True`` undoes the top edit and every further undo
        candidate sharing its ``batch`` (contiguous from the top of the
        stack), newest first, one ``origin: "undo"`` event per target, all in
        one new batch, atomically (dry run on a clone first). It returns the
        list of events. ``batch=`` names the batch the caller means; when the
        top of the stack belongs to another batch (newer edits came after)
        it raises :class:`InvalidOperation`. See :meth:`revert_batch` for
        batches that are no longer on top."""
        if not whole_batch:
            if batch is not None:
                raise InvalidOperation("batch= needs whole_batch=True")
            target_ev = self._undo_candidate()
            if target_ev is None:
                raise InvalidOperation("nothing to undo")
            event = self._append_undo(target_ev, origin="undo", author=author, at=at)
            self._after_settings_inverse([target_ev])
            return event
        targets = self._top_batch(self._undo_candidates(), batch, verb="undo")
        trial = self._clone()
        for ev in targets:
            trial._append_undo(ev, origin="undo", author=author, at=at)
        events: list[Event] = []
        with self.batch():
            for ev in targets:
                events.append(self._append_undo(ev, origin="undo", author=author, at=at))
            self._after_settings_inverse(targets)
        return events

    @overload
    def redo(
        self,
        *,
        author: Actor,
        at: str | None = ...,
        whole_batch: Literal[False] = ...,
        batch: None = ...,
    ) -> Event: ...

    @overload
    def redo(
        self,
        *,
        author: Actor,
        at: str | None = ...,
        whole_batch: Literal[True],
        batch: str | None = ...,
    ) -> list[Event]: ...

    def redo(
        self,
        *,
        author: Actor,
        at: str | None = None,
        whole_batch: bool = False,
        batch: str | None = None,
    ) -> Event | list[Event]:
        """Re-apply the most recent not-yet-redone undo.

        Walking back through the trailing undo/redo zone, each redo cancels
        the nearest earlier undo (stack semantics); the first uncancelled
        undo is the redo target. Any original edit ends the zone.

        ``whole_batch=True`` redoes every uncancelled undo of the newest undo
        batch (``batch=`` must match it when given) in one new batch and
        returns the list of events."""
        if not whole_batch:
            if batch is not None:
                raise InvalidOperation("batch= needs whole_batch=True")
            candidate = next(self._redo_candidates(), None)
            if candidate is None:
                raise InvalidOperation("nothing to redo")
            event = self._append_undo(candidate, origin="redo", author=author, at=at)
            self._after_settings_inverse([candidate])
            return event
        targets = self._top_batch(self._redo_candidates(), batch, verb="redo")
        trial = self._clone()
        for ev in targets:
            trial._append_undo(ev, origin="redo", author=author, at=at)
        events: list[Event] = []
        with self.batch():
            for ev in targets:
                events.append(self._append_undo(ev, origin="redo", author=author, at=at))
            self._after_settings_inverse(targets)
        return events

    def _append_undo(self, ev: Event, *, origin: str, author: Actor, at: str | None) -> Event:
        data = self._inverse_data(ev)
        self._guard_removal_dependents(data)
        data.update(
            {
                "seq": self.seq + 1,
                "kind": "direct_edit",
                "t": at or _now_iso(),
                "origin": origin,
                "author": author.to_obj(),
                "batch": self._batch_id(),
            }
        )
        self._apply_data(data)
        return self._append_event(data)

    def _after_settings_inverse(self, events: Iterable[Event]) -> None:
        """A live ``review`` change re-syncs pending ``aim:doc`` cards (§5.6)."""
        if any(ev.target == "aim:doc" for ev in events):
            self._sync_pending_doc_cards_review()

    @staticmethod
    def _top_batch(candidates: Iterable[Event], batch: str | None, *, verb: str) -> list[Event]:
        """The leading run of *candidates* that shares the first one's batch."""
        out: list[Event] = []
        for ev in candidates:
            if not out:
                if batch is not None and ev.batch != batch:
                    raise InvalidOperation(
                        f"newer edits came after batch {batch!r}; {verb} those first "
                        "(or use revert_batch)"
                    )
            elif ev.batch != out[0].batch:
                break
            out.append(ev)
        if not out:
            raise InvalidOperation(f"nothing to {verb}")
        return out

    def _undo_candidates(self) -> Iterator[Event]:
        """Undo targets from the top of the stack down: the edit ``undo()``
        would invert now, then the one after that, and so on.

        Walk the history backwards. Each undo cancels one earlier event (an
        original edit, or a redo's re-application); each redo cancels one
        earlier undo, so ``pending`` may dip negative while a redo waits for
        the undo it cancelled. ``aim:version`` upgrades are never candidates
        (spec §6.6)."""
        pending_undos = 0
        for ev in reversed(self._history_events()):
            if not ev.state_changing or ev.target == VERSION_TARGET:
                continue
            if ev.origin == "undo":
                pending_undos += 1
            elif ev.origin == "redo":
                pending_undos -= 1
            elif pending_undos > 0:
                pending_undos -= 1  # this edit is already undone; skip it
            else:
                yield ev

    def _undo_candidate(self) -> Event | None:
        """The most recent edit that is not currently undone."""
        return next(self._undo_candidates(), None)

    def _redo_candidates(self) -> Iterator[Event]:
        """Uncancelled undos in the trailing undo/redo zone, newest first."""
        redos_pending = 0
        for ev in reversed(self._history_events()):
            if not ev.state_changing or ev.target == VERSION_TARGET:
                continue
            if ev.origin == "redo":
                redos_pending += 1
            elif ev.origin == "undo":
                if redos_pending > 0:
                    redos_pending -= 1
                else:
                    yield ev
            else:
                return

    @staticmethod
    def _undone_seqs(events: Sequence[Event]) -> set[int]:
        """Seqs of the original edits in *events* (a history suffix) that the
        undo stack currently holds undone."""
        undone: set[int] = set()
        pending_undos = 0
        for ev in reversed(events):
            if not ev.state_changing or ev.target == VERSION_TARGET:
                continue
            if ev.origin == "undo":
                pending_undos += 1
            elif ev.origin == "redo":
                pending_undos -= 1
            elif pending_undos > 0:
                pending_undos -= 1
                undone.add(ev.seq)
        return undone

    def _guard_removal_dependents(self, data: dict) -> None:
        """Refuse an inverse that would pull a block out from under pending
        cards: removing a chunk that cards target or anchor on would leave
        them dangling (P008/P011), the same rule a direct delete follows."""
        target = data.get("target")
        if data.get("action") != "delete" or not target:
            return
        dependents = [p.id for p in self.proposals if p.target == target]
        dependents += [c.get("id") or "" for c in self._cards_anchored_on(target)]
        if dependents:
            raise InvalidOperation(
                f"pending suggestions ({', '.join(sorted(set(dependents)))}) depend on "
                f"{target!r}; resolve them first"
            )

    def _batch_events(self, batch: str) -> list[Event]:
        """The state-changing events of *batch*, oldest first, without its
        ``aim:version`` upgrade (never inverted, §6.6)."""
        return [
            ev
            for ev in self._history_events()
            if ev.batch == batch and ev.state_changing and ev.target != VERSION_TARGET
        ]

    def _batch_still_applies(self, events: Sequence[Event]) -> bool:
        """Cheap check that every target still holds what *events* left there
        (the newest event per target decides)."""
        latest: dict[str, Event] = {}
        for ev in events:
            if ev.target:
                latest[ev.target] = ev
        for ev in latest.values():
            if self._revert_conflict(ev) is not None:
                return False
        return True

    def _revert_conflict(self, ev: Event) -> str | None:
        """Why the inverse of *ev* cannot apply to the current state, or None."""
        target = ev.target or ""
        state = self._state
        if ev.action == "modify":
            current = state.serial(target)
            want = ev.applied_payload
            if target == "aim:doc" and current is not None and want is not None:
                try:
                    current = self._settings_script(
                        {k: v for k, v in self._payload_review(current)[0].items() if k != "review"}
                    )
                    want = self._settings_script(
                        {k: v for k, v in self._payload_review(want)[0].items() if k != "review"}
                    )
                except AimError:
                    return f"the settings of {target!r} are malformed"
            if current != want:
                return f"{target!r} has changed since"
            return None
        if ev.action == "add":
            if state.serial(target) != ev.applied_payload:
                return f"{target!r} has changed since"
            return None
        if ev.action == "delete":
            if state.exists(target):
                return f"{target!r} is back in the document"
            anchor = ev.get("anchor")
            try:
                state.resolve_insert_point(Anchor.from_obj(anchor))
            except (AimError, KeyError, TypeError):
                return f"the place {target!r} was deleted from is gone"
            return None
        if ev.action == "move":
            to = ev.get("to")
            frm = ev.get("from")
            if not state.exists(target) or frm is None or to is None:
                return f"{target!r} has changed since"
            try:
                here = self._anchor_of(target)
            except AimError:
                return f"{target!r} has changed since"
            if here != Anchor.from_obj(to):
                return f"{target!r} has moved since"
            return None
        return f"cannot revert a {ev.action!r} event"

    def _revert_data(self, ev: Event) -> dict:
        """The inverse of *ev* as an ordinary edit against the CURRENT state:
        positions and ``before`` values are read live, and an ``aim:doc``
        inverse keeps the live review policy (§5.6)."""
        target = ev.target or ""
        if ev.action == "modify":
            data: dict = {"target": target, "action": "modify"}
            current = self._state.serial(target)
            if current is not None:
                data["before"] = current
            prior = ev.get("before")
            live_review = self._live_review() if target == "aim:doc" else None
            if prior is None and live_review is not None:
                # the batch introduced the settings block; reverting it must
                # not take the live review policy down with it
                data["after"] = self._settings_script({"review": deepcopy(live_review)})
            elif prior is None:
                data["x_remove"] = True
            elif target == "aim:doc":
                data["after"] = self._keep_live_review(prior)
            else:
                data["after"] = prior
            return data
        if ev.action == "add":
            return {
                "target": target,
                "action": "delete",
                "before": self._state.serial(target),
                "anchor": self._anchor_of(target).to_obj(),
            }
        if ev.action == "delete":
            return {
                "target": target,
                "action": "add",
                "after": ev.get("before"),
                "anchor": deepcopy(ev.get("anchor")),
            }
        return {
            "target": target,
            "action": "move",
            "from": self._anchor_of(target).to_obj(),
            "to": deepcopy(ev.get("from")),
        }

    def revert_batch(self, batch: str, *, author: Actor, at: str | None = None) -> list[Event]:
        """Revert every change of *batch* in one step (spec §6.6, informative).

        When the batch is the top of the undo stack this is a true undo
        (``undo(whole_batch=True, batch=batch)``): ``origin: "undo"`` events,
        and :meth:`redo` brings it back. Otherwise the batch's inverse is
        written as ordinary direct edits (``origin: "user"``) in one new
        batch, newest change first, whose ``source`` names the reverted batch
        (``[{"reverts": batch}]``); undoing that new batch brings the changes
        back (see :meth:`unrevert_batch`). All or nothing after a dry run.

        Refuses only on a genuine conflict: a target the batch touched that
        has changed since (modify: different content; add: the chunk is not
        what was added; delete: the id is back or its place is gone; move:
        the chunk left the destination), or pending suggestions that depend
        on a block the revert would remove. An ``aim:doc`` change is reverted
        to its page setup and keeps the live review policy; ``aim:version``
        upgrades stay."""
        events = self._batch_events(batch)
        if not events:
            raise InvalidOperation(f"batch {batch!r} has no changes to revert")
        undone = self._undone_seqs(self._history_events())
        if all(ev.seq in undone for ev in events):
            raise InvalidOperation(f"batch {batch!r} is already undone")
        top = list(self._top_batch_or_empty())
        if top and top[0].batch == batch and {e.seq for e in top} == {e.seq for e in events}:
            return self.undo(author=author, at=at, whole_batch=True, batch=batch)
        live = [ev for ev in events if ev.seq not in undone]
        reverted_auto = any(ev.kind == "resolution" and ev.get("auto") for ev in live)
        explanation = (
            f"Reverted auto-accepted changes from batch {batch}"
            if reverted_auto
            else f"Reverted the changes from batch {batch}"
        )
        trial = self._clone()
        wrote = trial._write_revert(
            live, author=author, at=at, explanation=explanation, batch=batch
        )
        if not wrote:
            # every change was a review-policy switch, which a revert keeps
            # (§5.6): refuse rather than report a no-op as a revert, so an
            # agent asked to "undo" it never tells the person it is off
            raise InvalidOperation(
                f"batch {batch!r} has no changes to revert: it only switched the "
                "review policy, which a revert keeps; switch it with set_review_policy "
                "(aim review / aim_review)"
            )
        with self.batch():
            return self._write_revert(
                live, author=author, at=at, explanation=explanation, batch=batch
            )

    def _top_batch_or_empty(self) -> list[Event]:
        try:
            return self._top_batch(self._undo_candidates(), None, verb="undo")
        except InvalidOperation:
            return []

    def _write_revert(
        self,
        events: Sequence[Event],
        *,
        author: Actor,
        at: str | None,
        explanation: str,
        batch: str,
    ) -> list[Event]:
        out: list[Event] = []
        for ev in reversed(events):
            problem = self._revert_conflict(ev)
            if problem is not None:
                raise InvalidOperation(
                    f"cannot revert batch {batch!r}: {problem}; use undo to step back instead"
                )
            data = self._revert_data(ev)
            self._guard_removal_dependents(data)
            data.update(
                {
                    "seq": self.seq + 1,
                    "kind": "direct_edit",
                    "t": at or _now_iso(),
                    "origin": "user",
                    "author": author.to_obj(),
                    "batch": self._batch_id(),
                    "explanation": explanation,
                    "source": [{"reverts": batch}],
                }
            )
            if data.get("action") == "modify" and data.get("before") == data.get("after"):
                continue  # nothing left to change on this target
            self._apply_data(data)
            out.append(self._append_event(data))
        self._after_settings_inverse(events)
        return out

    def unrevert_batch(self, batch: str, *, author: Actor, at: str | None = None) -> list[Event]:
        """Bring back the changes a :meth:`revert_batch` call took away.

        *batch* is the batch the revert wrote (its events' ``batch``). A true
        undo is redone (``redo(whole_batch=True)``); a revert written as
        ordinary edits is undone (``undo(whole_batch=True, batch=batch)``).
        Either way only that batch, and only while it is on top."""
        events = [ev for ev in self._history_events() if ev.batch == batch and ev.state_changing]
        if not events:
            raise InvalidOperation(f"batch {batch!r} has no changes")
        if all(ev.origin == "undo" for ev in events):
            return self.redo(author=author, at=at, whole_batch=True, batch=batch)
        if all(
            any(isinstance(s, dict) and s.get("reverts") for s in (ev.get("source") or []))
            for ev in events
        ):
            return self.undo(author=author, at=at, whole_batch=True, batch=batch)
        raise InvalidOperation(f"batch {batch!r} is not a revert")

    def _inverse_data(self, ev: Event) -> dict:
        # *ev* comes from the cached history index; nested objects lifted
        # into the inverse are copied so the Event undo/redo hand back never
        # aliases the cached log (the caller owns its return value).
        action, target = ev.action, ev.target
        if action == "modify":
            inv: dict = {"target": target, "action": "modify"}
            if ev.applied_payload is not None:
                inv["before"] = ev.applied_payload
            if ev.get("before") is not None:
                inv["after"] = ev.get("before")
            else:
                # introduction of addressable state (aim:theme with no
                # `before`): the inverse removes the block. x_remove is an
                # apply-time flag only — _apply_data pops it before the
                # event is appended, so it never reaches the log.
                inv["x_remove"] = True
            return inv
        if action == "add":
            return {
                "target": target,
                "action": "delete",
                "before": ev.applied_payload,
                "anchor": deepcopy(ev.get("anchor")),
            }
        if action == "delete":
            return {
                "target": target,
                "action": "add",
                "after": ev.get("before"),
                "anchor": deepcopy(ev.get("anchor")),
            }
        if action == "move":
            return {
                "target": target,
                "action": "move",
                "from": deepcopy(ev.get("to")),
                "to": deepcopy(ev.get("from")),
            }
        raise HistoryError(f"cannot invert action {action!r}")

    def _apply_data(self, data: dict) -> None:
        action, target = data["action"], data["target"]
        if target == VERSION_TARGET:
            declared = data["after"]
            blocking = {
                floor
                for floor in self._retained_floors()
                if not REGISTRY.version_includes(declared, floor)
            }
            if blocking:
                floor = max(blocking, key=lambda f: version_key(f) or ())
                raise InvalidOperation(
                    f"cannot set the declared version to {declared}: retained document state "
                    f"or history contains {_floor_label(floor)} requiring spec {floor}"
                )
            self._state.set_spec_version(declared)
            return
        if target in ("aim:theme", "aim:doc"):
            setter = (
                self._state.set_theme_markup
                if target == "aim:theme"
                else self._state.set_doc_settings_markup
            )
            if data.get("x_remove"):
                setter(None)
                data.pop("x_remove")
                data.pop("after", None)
            else:
                setter(data["after"])
            return
        if action == "modify":
            self._state.replace(target, data["after"])
        elif action == "add":
            self._state.insert(data["after"], Anchor.from_obj(data["anchor"]))
        elif action == "delete":
            self._state.remove(target)
        elif action == "move":
            _apply_move_data(self._state, data)

    # -- proposals (the pending lane) --------------------------------------------------------
    @property
    def proposals(self) -> list[Proposal]:
        sec = self._state.section("aim-proposals")
        if sec is None:
            return []
        return [self._proposal_of(card) for card in sec.elements() if card.tag == "aim-proposal"]

    @staticmethod
    def _proposal_of(card: Element) -> Proposal:
        tmpl = next((c for c in card.elements() if c.tag == "template"), None)
        payload = None
        if tmpl is not None and tmpl.elements():
            payload = "".join(serialize(e) for e in tmpl.elements())
        author = Actor(
            card.get("data-author") or "human",
            id=card.get("data-author-id"),
            model=card.get("data-author-model"),
        )
        return Proposal(
            id=card.get("id") or "",
            action=card.get("data-action") or "",
            target=card.get("data-for"),
            author=author,
            at=card.get("data-at") or "",
            explanation=card.get("data-explanation"),
            payload_html=payload,
            anchor_container=card.get("data-anchor-container"),
            anchor_after=card.get("data-anchor-after"),
            anchor_shell=card.get("data-anchor-shell"),
            depends_on=card.get("data-depends-on"),
            batch=card.get("data-batch"),
        )

    def proposal(self, pid: str) -> Proposal:
        for p in self.proposals:
            if p.id == pid:
                return p
        raise TargetNotFound(f"no pending proposal {pid!r}")

    def _proposals_section(self) -> Element:
        sec = self._state.section("aim-proposals")
        if sec is None:
            sec = Element("aim-proposals")
            insert_at = len(self._state.body.children)
            for i, child in enumerate(self._state.body.children):
                if isinstance(child, Element) and child.tag in ("aim-assets", "script"):
                    insert_at = i
                    break
            self._state.body.children.insert(insert_at, sec)
        return sec

    def _card_el(self, pid: str) -> Element:
        sec = self._state.section("aim-proposals")
        cards = [] if sec is None else [c for c in sec.elements() if c.get("id") == pid]
        if not cards:
            raise TargetNotFound(f"no pending proposal {pid!r}")
        # duplicate ids make resolution ambiguous — refuse rather than
        # silently picking (and shadowing) the first match (AIM-04)
        if len(cards) > 1:
            raise InvalidOperation(
                f"duplicate proposal id {pid!r}: document integrity error, "
                "cannot resolve ambiguously"
            )
        return cards[0]

    def _new_proposal_id(self) -> str:
        return ids.new_proposal_id(self._taken_ids())

    def _new_card(
        self,
        *,
        action: str,
        author: Actor,
        target: str | None,
        payload: str | None,
        anchor: Anchor | None,
        explanation: str | None,
        depends_on: str | None,
        at: str | None,
        pid: str | None = None,
    ) -> Proposal:
        if target == "aim:doc":
            self._guard_card_review(payload)
        # a gated payload sitting in the pending lane is already markup an
        # older validator rejects, so the proposal — not only its acceptance
        # — is what needs the version
        upgrade_batch = self._ensure_feature_version(payload, author=author, at=at)
        pid = pid or self._new_proposal_id()
        attrs: list[tuple[str, str | None]] = [("id", pid), ("data-action", action)]
        if anchor is not None:
            if anchor.after is not None:
                attrs.append(("data-anchor-after", anchor.after))
            attrs.append(("data-anchor-container", anchor.container))
            if anchor.shell is not None:
                attrs.append(("data-anchor-shell", anchor.shell))
        attrs.append(("data-at", at or _now_iso()))
        attrs.append(("data-author", author.type))
        if author.id:
            attrs.append(("data-author-id", author.id))
        if author.model:
            attrs.append(("data-author-model", author.model))
        attrs.append(("data-batch", upgrade_batch or self._batch_id()))
        if depends_on:
            attrs.append(("data-depends-on", depends_on))
        if explanation:
            attrs.append(("data-explanation", explanation))
        if target:
            attrs.append(("data-for", target))
        card = Element("aim-proposal", attrs)
        if payload is not None:
            tmpl = Element("template")
            tmpl.children = list(parse_fragment(payload))
            card.children.append(tmpl)
        self._proposals_section().children.append(card)
        proposal = self._proposal_of(card)
        self._get_history_index().add_proposal(proposal)
        return proposal

    def _supersede_if_pending(
        self,
        target: str,
        new_pid: str,
        author: Actor,
        at: str | None,
        *,
        actions: tuple[str, ...] = ("modify", "delete"),
    ) -> None:
        for p in self._superseded_by_new(target, actions=actions):
            self._resolve_retaining_paint(
                p,
                decision="superseded",
                decided_by=author,
                superseded_by=new_pid,
                at=at,
            )

    def _superseded_by_new(
        self, target: str, *, actions: tuple[str, ...] = ("modify", "delete")
    ) -> list[Proposal]:
        """The pending cards a new proposal on *target* replaces (§5.4):
        modify/delete replace each other; a move replaces a pending move."""
        return [p for p in self.proposals if p.target == target and p.action in actions]

    def _require_current_target(self, target: str) -> None:
        """Proposal targets must exist in the CURRENT document (lint P008).

        The projection answers how the pending lane composes; it cannot
        mint targets. A card aimed at a chunk only a pending add would
        create lints P008 and cannot reliably resolve."""
        if not self._state.exists(target):
            raise TargetNotFound(f"no chunk {target!r}")

    def propose_modify(
        self,
        target: str,
        markup: str,
        *,
        author: Actor,
        explanation: str | None = None,
        depends_on: str | None = None,
        at: str | None = None,
        accept: bool | None = None,
        accept_by: Actor | None = None,
    ) -> Proposal:
        """Propose replacing *target*'s markup (a pending ``modify`` card).

        Auto-accept (spec §5.6): with ``accept=None`` (the default) the card
        honours the document's review policy: under ``review.agents="auto"``
        an agent- or external-authored card is accepted when the outermost
        batch closes (immediately, when this call owns the batch; the
        returned view then carries ``resolution``). ``accept=True`` asks for
        acceptance of this card regardless of policy or author (``auto:
        "request"``, ``decided_by`` = *accept_by*, a human, else the policy's
        ``by``, else ``Actor("human")``). ``accept=False`` keeps it pending
        even under the policy (a tool-level override). A refused acceptance
        leaves the card pending and reports through ``last_auto_accept``;
        it never raises. The same keywords exist on every ``propose_*``."""
        self._check_accept_args(accept, accept_by)

        def validate(projected: AimDocument) -> str:
            try:
                projected.modify_chunk(target, markup, author=author, at=at)
            except _NoOpEdit:
                pass  # no-op only after earlier pendings resolve — harmless
            payload = projected._state.serial(target)
            assert payload is not None
            return payload

        self._require_current_target(target)
        self._noop_guard(lambda current: current.modify_chunk(target, markup, author=author, at=at))
        payload = self._projected_operation(
            f"new modify of {target!r}",
            validate,
            exclude=[p.id for p in self._superseded_by_new(target)],
        )
        pid = self._new_proposal_id()
        with self.batch():  # the supersede + the new card are one intention
            self._supersede_if_pending(target, pid, author, at)
            proposal = self._new_card(
                action="modify",
                author=author,
                target=target,
                payload=payload,
                anchor=None,
                explanation=explanation,
                depends_on=depends_on,
                at=at,
                pid=pid,
            )
            self._register_card(proposal, accept, accept_by, at)
        return self._returned(proposal)

    def propose_theme(
        self,
        slots: dict[str, str],
        *,
        author: Actor,
        explanation: str | None = None,
        depends_on: str | None = None,
        at: str | None = None,
        accept: bool | None = None,
        accept_by: Actor | None = None,
    ) -> Proposal:
        self._check_accept_args(accept, accept_by)
        self._check_theme_slots(slots)
        body = "; ".join(f"{k}:{v}" for k, v in sorted(slots.items()))
        markup = f"<style data-aim-theme>:root{{{body}}}</style>"
        pid = self._new_proposal_id()
        with self.batch():
            self._supersede_if_pending("aim:theme", pid, author, at)
            proposal = self._new_card(
                action="modify",
                author=author,
                target="aim:theme",
                payload=markup,
                anchor=None,
                explanation=explanation,
                depends_on=depends_on,
                at=at,
                pid=pid,
            )
            self._register_card(proposal, accept, accept_by, at)
        return self._returned(proposal)

    def propose_replace_text(
        self,
        target: str,
        old_text: str,
        new_text: str,
        *,
        author: Actor,
        explanation: str | None = None,
        at: str | None = None,
        accept: bool | None = None,
        accept_by: Actor | None = None,
    ) -> Proposal:
        """:meth:`propose_modify` whose payload is chunk *target*'s markup
        with the one occurrence of *old_text* replaced by *new_text*, inline
        markup kept (see :meth:`replace_text`).

        A new modify card supersedes the pending modify/delete on its target
        (§5.4), so a payload built from the live markup would silently drop
        that card's change. When *author*'s own modify card is pending on
        *target*, the replacement applies to that card's payload instead
        (successive word fixes compose; the new card supersedes it and keeps
        its ``depends_on``, and its explanation when none is given). Any
        other pending modify/delete on *target* refuses: superseding someone
        else's change, or a pending delete, takes an explicit
        :meth:`propose_modify`. ``accept``/``accept_by`` as for
        :meth:`propose_modify`."""
        from .textedit import TextReplaceError, replace_in_markup

        self._require_current_target(target)
        pending = self._superseded_by_new(target)
        if not pending:
            markup = replace_in_markup(self.chunk(target).html, old_text, new_text)
            return self.propose_modify(
                target,
                markup,
                author=author,
                explanation=explanation,
                at=at,
                accept=accept,
                accept_by=accept_by,
            )
        own = pending[0]
        if len(pending) > 1 or own.action != "modify" or own.author != author:
            names = ", ".join(f"{p.id} ({p.action} by {p.author.type})" for p in pending)
            raise InvalidOperation(
                f"replace_text on {target!r} would supersede pending {names}: resolve it "
                "first, or propose a full modify to replace it deliberately"
            )
        try:
            markup = replace_in_markup(own.payload_html or "", old_text, new_text)
        except TextReplaceError as exc:
            raise TextReplaceError(
                f"{exc} (matched against your pending proposal {own.id} on {target!r}, "
                "which this change composes with)"
            ) from None
        return self.propose_modify(
            target,
            markup,
            author=author,
            explanation=explanation if explanation is not None else own.explanation,
            depends_on=own.depends_on,
            at=at,
            accept=accept,
            accept_by=accept_by,
        )

    def propose_add(
        self,
        markup: str,
        *,
        author: Actor,
        container: str = "body",
        after: AnchorAfter = LAST,
        explanation: str | None = None,
        depends_on: str | None = None,
        at: str | None = None,
        accept: bool | None = None,
        accept_by: Actor | None = None,
    ) -> Proposal:
        self._check_accept_args(accept, accept_by)
        concrete_after = after
        if isinstance(after, str) and ids.is_valid_proposal_id(after):
            pending = {p.id: p for p in self.proposals if p.action == "add"}
            if after not in pending:
                raise TargetNotFound(f"anchor proposal {after!r} is not a pending add")
            # a chained add resolves into the container of the add it anchors
            # on; anchoring across containers can never be accepted (AIM-03)
            anchored = pending[after].anchor_container or "body"
            if anchored != container:
                raise InvalidOperation(
                    f"add into {container!r} cannot anchor on pending proposal "
                    f"{after!r} in {anchored!r}"
                )
            concrete_after = self._payload_root_id(pending[after].payload_html or "")

        def validate(projected: AimDocument) -> tuple[str, Anchor]:
            chunk = projected.add_chunk(
                markup,
                author=author,
                container=container,
                after=concrete_after,
                at=at,
            )
            anchor_obj = projected.history[-1].get("anchor")
            assert anchor_obj is not None
            return chunk.html, Anchor.from_obj(anchor_obj)

        payload, projected_anchor = self._projected_operation(
            f"new add into {container!r}", validate
        )
        anchor = self._card_position_anchor("add", projected_anchor)
        with self.batch():
            proposal = self._new_card(
                action="add",
                author=author,
                target=None,
                payload=payload,
                anchor=anchor,
                explanation=explanation,
                depends_on=depends_on,
                at=at,
            )
            self._register_card(proposal, accept, accept_by, at)
        return self._returned(proposal)

    def _card_position_anchor(self, action: str, projected: Anchor) -> Anchor:
        """Record a position that remains valid without pending projection.

        The destination container (and table shell) must exist in the CURRENT
        body. A projected anchor on a pending add's payload root is encoded as
        the proposal-id chain so normal rejection rebinding keeps the dependent
        card viable; every other projected-only position is refused.
        """
        try:
            self._state.resolve_insert_point(
                Anchor(projected.container, None, shell=projected.shell)
            )
        except AimError as exc:
            raise InvalidOperation(
                f"{action} would use container {projected.container!r} (or its "
                "table shell), which is not a valid position in the current document"
            ) from exc

        if projected.after is None:
            return projected
        owner = next(
            (
                p
                for p in self.proposals
                if p.action == "add"
                and (p.anchor_container or "body") == projected.container
                and self._payload_root_id(p.payload_html or "") == projected.after
            ),
            None,
        )
        if owner is not None:
            return Anchor(projected.container, owner.id, projected.shell)
        try:
            self._state.resolve_insert_point(projected)
        except AimError as exc:
            raise InvalidOperation(
                f"{action} would anchor on {projected.after!r}, which is not a valid "
                f"position in {projected.container!r} until the pending lane resolves — "
                "anchor on a pending add's proposal id or on a current chunk"
            ) from exc
        return projected

    def propose_delete(
        self,
        target: str,
        *,
        author: Actor,
        explanation: str | None = None,
        depends_on: str | None = None,
        at: str | None = None,
        accept: bool | None = None,
        accept_by: Actor | None = None,
    ) -> Proposal:
        self._check_accept_args(accept, accept_by)
        # reject reserved targets at propose time: the card would lint clean
        # but explode at accept (reserved heads have no body anchor)
        _no_delete_move(target, "delete proposal")
        self._require_current_target(target)
        self._projected_operation(
            f"new delete of {target!r}",
            lambda projected: projected.delete_chunk(target, author=author, at=at),
            exclude=[p.id for p in self._superseded_by_new(target)],
        )
        pid = self._new_proposal_id()
        with self.batch():
            self._supersede_if_pending(target, pid, author, at)
            proposal = self._new_card(
                action="delete",
                author=author,
                target=target,
                payload=None,
                anchor=None,
                explanation=explanation,
                depends_on=depends_on,
                at=at,
                pid=pid,
            )
            self._register_card(proposal, accept, accept_by, at)
        return self._returned(proposal)

    def propose_move(
        self,
        target: str,
        *,
        author: Actor,
        container: str,
        after: AnchorAfter = LAST,
        shell: str | None = None,
        explanation: str | None = None,
        at: str | None = None,
        accept: bool | None = None,
        accept_by: Actor | None = None,
    ) -> Proposal:
        self._check_accept_args(accept, accept_by)
        _no_delete_move(target, "move proposal")

        def validate(projected: AimDocument) -> Anchor:
            try:
                projected.move_chunk(
                    target,
                    author=author,
                    container=container,
                    after=after,
                    shell=shell,
                    at=at,
                )
            except _NoOpEdit:
                # no-op only after earlier pendings resolve — harmless at
                # accept; record the anchor the move would have used
                return projected._resolve_end_anchor(container, after, exclude=target, shell=shell)
            to = projected.history[-1].get("to")
            assert to is not None
            return Anchor.from_obj(to)

        self._require_current_target(target)
        self._noop_guard(
            lambda current: current.move_chunk(
                target, author=author, container=container, after=after, shell=shell, at=at
            )
        )
        projected_anchor = self._projected_operation(
            f"new move of {target!r}",
            validate,
            exclude=[p.id for p in self._superseded_by_new(target, actions=("move",))],
        )
        anchor = self._card_position_anchor("move", projected_anchor)
        pid = self._new_proposal_id()
        with self.batch():  # the supersede + the new card are one intention
            self._supersede_if_pending(target, pid, author, at, actions=("move",))
            proposal = self._new_card(
                action="move",
                author=author,
                target=target,
                payload=None,
                anchor=anchor,
                explanation=explanation,
                depends_on=None,
                at=at,
                pid=pid,
            )
            self._register_card(proposal, accept, accept_by, at)
        return self._returned(proposal)

    def _propose_lane(self, specs: Sequence[_CardSpec]) -> list[Proposal]:
        """Write many pending cards in one pass — the batch-propose primitive.

        Semantically identical to calling ``propose_*`` once per spec, in
        order: the same id minting, card serialization and P-rule checks
        (targets exist in the current body, reserved targets refused, no-op
        edits refused, recorded anchors resolve in the current body or chain
        onto a pending add). The difference is cost. ``propose_*`` replays
        every earlier pending card on a fresh clone for each new card, which
        is quadratic in the lane; this keeps ONE projection — the current
        document with the existing lane applied in creation order — and
        advances it by each new card as it is written.

        Supersession is not part of a batch: a spec aiming at a target that
        already carries a pending card of the same family (modify/delete, or
        move) raises :class:`InvalidOperation`, as does a second such spec in
        the same call. Nothing is written when a spec fails validation before
        any card exists; callers that need all-or-nothing run this on a clone.
        """
        projection = self._clone()
        decider = Actor("external", id="pending-projection")
        for proposal in _creation_order(projection.proposals):
            try:
                projection.accept(proposal.id, decided_by=decider, at=proposal.at)
            except AimError as exc:
                raise InvalidOperation(
                    f"pending proposal {proposal.id!r} cannot be projected: {exc}"
                ) from exc
        busy: set[tuple[str, str]] = set()
        for p in self.proposals:
            if p.target:
                busy.add((p.target, "move" if p.action == "move" else "edit"))
        batches: dict[str | None, str] = {}
        made: list[Proposal] = []
        saved_batch = self._batch
        try:
            for i, spec in enumerate(specs):
                where = f"card {i} ({spec.action})"
                payload: str | None = None
                anchor: Anchor | None = None
                target = spec.target
                if spec.action in ("modify", "delete", "move"):
                    if not target:
                        raise InvalidOperation(f"{where}: needs a target")
                    if spec.action != "modify":
                        _no_delete_move(target, f"{spec.action} proposal")
                    if target not in ("aim:doc", "aim:theme"):
                        self._require_current_target(target)
                    family = (target, "move" if spec.action == "move" else "edit")
                    if family in busy:
                        raise InvalidOperation(
                            f"{where}: {target!r} already carries a pending "
                            f"{'move' if family[1] == 'move' else 'modify/delete'}"
                        )
                    busy.add(family)
                after: str | None = spec.after
                if spec.after_card is not None:
                    chained = made[spec.after_card]
                    if chained.action != "add":
                        raise InvalidOperation(f"{where}: can only chain onto an add")
                    if (chained.anchor_container or "body") != spec.container:
                        raise InvalidOperation(f"{where}: cannot chain across containers")
                    after = self._payload_root_id(chained.payload_html or "")
                if spec.action == "modify" and target == "aim:doc":
                    payload = self._validated_doc_markup(spec.markup or "")
                    if payload == self._state.serial("aim:doc"):
                        raise _NoOpEdit(f"{where}: page setup is unchanged")
                elif spec.action == "modify":
                    assert target is not None
                    _, normalized = self._normalize_payload(spec.markup or "", expect_id=target)
                    if normalized == self._state.serial(target):
                        raise _NoOpEdit(f"{where}: modify with identical content")
                    try:
                        projection.modify_chunk(
                            target, spec.markup or "", author=spec.author, at=spec.at
                        )
                    except _NoOpEdit:
                        pass  # a no-op only after earlier cards resolve — harmless
                    payload = projection._state.serial(target)
                elif spec.action == "add":
                    chunk = projection.add_chunk(
                        spec.markup or "",
                        author=spec.author,
                        container=spec.container,
                        after=after,
                        at=spec.at,
                    )
                    payload = chunk.html
                    recorded = projection._history_events()[-1].get("anchor")
                    anchor = self._card_position_anchor("add", Anchor.from_obj(recorded))
                elif spec.action == "delete":
                    assert target is not None
                    projection.delete_chunk(target, author=spec.author, at=spec.at)
                elif spec.action == "move":
                    assert target is not None
                    here = self._anchor_of(target)
                    concrete = self._resolve_end_anchor(
                        spec.container, after, exclude=target, shell=spec.shell
                    )
                    if spec.after_card is None and here == concrete:
                        raise _NoOpEdit(f"{where}: move of {target!r} is a no-op")
                    try:
                        projection.move_chunk(
                            target,
                            author=spec.author,
                            container=spec.container,
                            after=after,
                            shell=spec.shell,
                            at=spec.at,
                        )
                        to = Anchor.from_obj(projection._history_events()[-1].get("to"))
                    except _NoOpEdit:
                        # a no-op only after earlier cards resolve — harmless
                        to = projection._resolve_end_anchor(
                            spec.container, after, exclude=target, shell=spec.shell
                        )
                    anchor = self._card_position_anchor("move", to)
                else:
                    raise InvalidOperation(f"{where}: unknown action {spec.action!r}")
                if spec.batch_key not in batches:
                    self._batch = None
                    batches[spec.batch_key] = self._next_batch()
                self._batch = batches[spec.batch_key]
                made.append(
                    self._new_card(
                        action=spec.action,
                        author=spec.author,
                        target=target if spec.action != "add" else None,
                        payload=payload,
                        anchor=anchor,
                        explanation=spec.explanation,
                        depends_on=None,
                        at=spec.at,
                    )
                )
        finally:
            self._batch = saved_batch
        return made

    def amend_proposal(
        self,
        pid: str,
        markup: str | None = None,
        *,
        explanation: str | None = None,
        at: str | None = None,
    ) -> Proposal:
        """Replace a pending proposal's payload and/or explanation IN PLACE.

        Spec §5.4 sanctions this: editing a pending payload is allowed and
        unrecorded — no history event is appended (provenance is preserved
        at resolution via ``proposed`` vs ``applied``). The proposal keeps
        its id, action, target, anchor, author, and dependencies. Its batch
        also stays unless the amendment first introduces newer-version
        syntax, in which case the card moves into the recorded upgrade batch.
        Re-anchoring or re-targeting is a reject + new propose, not an amend.
        Payloads are validated exactly like the original propose call
        (modify: against the live target; add: keeping the proposed root id
        and marker kind, so chained anchors stay stable).
        delete/move proposals carry no payload — only their explanation
        can be amended. ``explanation=""`` clears it.
        """
        card = self._card_el(pid)
        prop = self.proposal(pid)
        if markup is None and explanation is None:
            raise InvalidOperation("amend_proposal needs a new payload and/or explanation")
        if markup is not None:
            if prop.action == "modify":
                target = prop.target or ""
                if target == "aim:theme":
                    payload = self._validated_theme_markup(markup)
                elif target == "aim:doc":
                    payload = self._validated_doc_markup(markup)
                    self._guard_card_review(payload)
                else:
                    # fail fast on a dangling proposal (target deleted out
                    # from under it) — mirroring propose_modify; otherwise
                    # the amend silently rewrites a card that can only
                    # explode later, at accept time (review finding)
                    if not self._state.exists(target):
                        raise TargetNotFound(f"no chunk {target!r}")
                    _, payload = self._normalize_payload(markup, expect_id=target)
            elif prop.action == "add":
                _, payload = self._payload_like(
                    prop.payload_html or "", markup, skip_payload_of=pid
                )
            else:
                raise InvalidOperation(f"a {prop.action} proposal carries no payload to amend")
            # the amended lane must keep the creation-order invariant: a
            # payload that breaks a later pending card fails HERE, not as a
            # whole-lane rejection at accept-all
            trial = self._clone()
            trial_prop = trial.proposal(pid)
            _set_card_payload(trial._card_el(pid), payload)
            trial._get_history_index().replace_proposal(trial_prop, trial.proposal(pid))
            decider = Actor("external", id="pending-projection")
            for proposal in resolution_order(trial.proposals):
                try:
                    trial.accept(proposal.id, decided_by=decider, at=proposal.at)
                except AimError as exc:
                    raise InvalidOperation(
                        f"amended payload for {pid!r} breaks the pending lane at "
                        f"proposal {proposal.id!r}: {exc}"
                    ) from exc
            # §5.4 makes the amend itself unrecorded, but the version the
            # document conforms to is not the amend's to change silently
            upgrade_batch = self._ensure_feature_version(payload, author=prop.author, at=at)
            _set_card_payload(card, payload)
            if upgrade_batch is not None:
                card.set("data-batch", upgrade_batch)
        if explanation is not None:
            if explanation:
                card.set("data-explanation", explanation)
            else:
                card.remove_attr("data-explanation")
        if at is not None:
            card.set("data-at", at)
        amended = self.proposal(pid)
        if self._history_index is not None:
            self._get_history_index().replace_proposal(prop, amended)
        return amended

    # -- review policy and auto-accept (spec §5.6) -------------------------------------------
    @property
    def review_policy(self) -> ReviewPolicy | None:
        """The document's review policy from the ``aim:doc`` block (None when
        off). Raises :class:`InvalidOperation` (D007) on a malformed policy and
        :class:`ParseError` (D001) on a malformed settings block."""
        return ReviewPolicy.from_settings(
            self.doc_settings, lenient=not REGISTRY.implements(self.spec_version)
        )

    def _auto_policy(self) -> ReviewPolicy | None:
        """The policy as auto-accept honours it: None when it is off,
        malformed, or a value this build does not implement (fail closed)."""
        try:
            policy = self.review_policy
        except AimError:
            return None
        return policy if policy is not None and policy.auto else None

    @staticmethod
    def _settings_script(settings: dict) -> str:
        """The canonical whole-block ``aim:doc`` serialization of *settings*."""
        return (
            f'<script type="{REGISTRY.script_types["doc"]}">\n{canonical_json(settings)}\n</script>'
        )

    def _live_review(self) -> object:
        """The live block's raw ``review`` value (None when absent)."""
        try:
            return self.doc_settings.get("review")
        except AimError:
            return None

    def _payload_review(self, markup: str) -> tuple[dict, object]:
        """(settings, review) of an ``aim:doc`` whole-block payload."""
        settings = parse_doc_settings(doc_settings_element(markup).raw)
        return settings, settings.get("review")

    def _keep_live_review(self, markup: str) -> str:
        """*markup* (an ``aim:doc`` payload) with the live ``review`` value.

        A proposal never changes the review policy (spec §5.6): resolving an
        ``aim:doc`` card applies its page setup and keeps the live policy, so a
        card that would flip it records the honest ``applied`` payload.
        Returned unchanged when the payload already agrees."""
        settings, review = self._payload_review(markup)
        live = self._live_review()
        if review == live:
            return markup
        if live is None:
            settings.pop("review", None)
        else:
            settings["review"] = deepcopy(live)
        return self._settings_script(settings)

    def _guard_card_review(self, markup: str | None) -> None:
        """Refuse an ``aim:doc`` card whose ``review`` differs from the live
        block: the review policy is changed only by a direct edit (§5.6)."""
        if markup is None:
            return
        _, review = self._payload_review(markup)
        if review != self._live_review():
            raise InvalidOperation(
                "a proposal cannot change the review policy; switch it with "
                "set_review_policy (a recorded direct edit)"
            )

    def _sync_pending_doc_cards_review(self) -> None:
        """Re-sync every pending ``aim:doc`` card's ``review`` with the live
        block, as an unrecorded payload amendment (§5.4).

        Called after every live ``review`` change. A 0.6 tool keeps the live
        policy on accept anyway; this protects older tools, which would
        otherwise flip the policy back by accepting a stale page-setup card
        (whole-block payload)."""
        live = self._live_review()
        for prop in self.proposals:
            if prop.target != "aim:doc" or prop.action != "modify" or not prop.payload_html:
                continue
            try:
                settings, review = self._payload_review(prop.payload_html)
            except AimError:
                continue  # malformed card: D001's to report, not ours to repair
            if review == live:
                continue
            if live is None:
                settings.pop("review", None)
            else:
                settings["review"] = deepcopy(live)
            _set_card_payload(self._card_el(prop.id), self._settings_script(settings))
            self._get_history_index().replace_proposal(prop, self.proposal(prop.id))

    def set_review_policy(
        self,
        agents: str | None,
        *,
        by: Actor | None = None,
        author: Actor,
        explanation: str | None = None,
        at: str | None = None,
    ) -> ReviewPolicy | None:
        """Switch the document's review policy (spec §5.6).

        ``agents="auto"`` turns auto-accept on: from then on proposals
        authored by an agent or an external tool are accepted in the batch
        that created them, with ``decided_by`` = *by* (a human actor, the
        person whose standing consent this records; ``Actor("human")`` when
        the name is unknown). ``agents=None`` switches it off (removes
        ``review``). Proposals authored by humans always wait for review.

        Recorded as an ordinary ``aim:doc`` modify direct edit authored by
        *author* (whoever wrote the change: the person in an editor, or the
        agent or tool acting on their instruction), so it is in history and
        undoable. Turning it on in a pre-0.6 document records the version
        upgrade in the same batch. Cards already pending are not swept;
        pending ``aim:doc`` cards are re-synced to carry the new value.
        Raises ``InvalidOperation("review policy unchanged")`` when the block
        would not change.
        """
        if agents is not None:
            if agents not in REGISTRY.review_agents:
                if agents in REGISTRY.review_reserved_agents:
                    raise InvalidOperation(
                        f"review policy {agents!r} is reserved by the spec and not defined yet"
                    )
                raise InvalidOperation(
                    f"unknown review policy {agents!r} (use 'auto', or None to switch it off)"
                )
            if by is None or by.type != "human":
                raise InvalidOperation(
                    "a review policy records a person's consent: by must be a human actor"
                )
        settings = dict(self.doc_settings)
        if agents is None:
            if "review" not in settings:
                raise InvalidOperation("review policy unchanged")
            del settings["review"]
        else:
            assert by is not None
            current = settings.get("review")
            review = dict(current) if isinstance(current, dict) else {}
            review["agents"] = agents
            review["by"] = by.to_obj()
            settings["review"] = review
        markup = self._settings_script(settings)
        before = self._state.serial("aim:doc")
        if markup == before:
            raise InvalidOperation("review policy unchanged")
        with self.batch():
            if agents is not None:
                self._ensure_version_floor(
                    REGISTRY.review_since, author=author, at=at, label="a review policy"
                )
            self._state.set_doc_settings_markup(markup)
            data = {
                "seq": self.seq + 1,
                "kind": "direct_edit",
                "t": at or _now_iso(),
                "target": "aim:doc",
                "action": "modify",
                "after": markup,
                "author": author.to_obj(),
                "batch": self._batch_id(),
            }
            if before is not None:
                data["before"] = before
            if explanation:
                data["explanation"] = explanation
            self._append_event(data)
            self._sync_pending_doc_cards_review()
        return self.review_policy

    @staticmethod
    def _check_accept_args(accept: bool | None, accept_by: Actor | None) -> None:
        if accept_by is None:
            return
        if accept is not True:
            raise InvalidOperation("accept_by names who asked for acceptance: it needs accept=True")
        if accept_by.type != "human":
            raise InvalidOperation("accept_by must be a human actor (the person who asked)")

    def _register_card(
        self, proposal: Proposal, accept: bool | None, accept_by: Actor | None, at: str | None
    ) -> None:
        """Note a card created inside the open batch for the close-time pass."""
        if self._batch is not None:
            self._batch_cards.append(_BatchCard(proposal.id, accept, accept_by, at))

    def _returned(self, proposal: Proposal) -> Proposal:
        """The view a ``propose_*`` call returns: with its resolution event
        when the call owned the batch and the card was auto-accepted."""
        if self._batch is not None:
            return proposal  # the caller's batch is still open
        outcome = self.last_auto_accept
        if outcome is not None and proposal.id in outcome.accepted:
            return _dc_replace(proposal, resolution=self.resolution_of(proposal.id))
        return proposal

    def _auto_accept_at_close(self) -> None:
        """The outermost batch's close: accept the in-scope cards it created.

        In scope: still pending, and either asked for (``accept=True`` or the
        batch knob ``True``: ``auto: "request"``) or covered by the policy
        (``accept`` left ``None``, batch knob not ``False``, policy ``auto``,
        author an agent or external tool: ``auto: "policy"``)."""
        pending = {p.id: p for p in self.proposals}
        knob = self._batch_auto
        policy: ReviewPolicy | None = None
        policy_read = False
        plan: list[tuple[str, str, Actor, str | None]] = []
        for card in self._batch_cards:
            prop = pending.get(card.pid)
            if prop is None or card.accept is False:
                continue  # resolved/superseded inside the batch, or opted out
            if not policy_read:
                policy, policy_read = self._auto_policy(), True
            if card.accept is True or (card.accept is None and knob is True):
                decider = card.accept_by or (policy.by if policy else Actor("human"))
                plan.append((card.pid, REQUEST, decider, card.at))
            elif (
                card.accept is None
                and knob is None
                and policy is not None
                and prop.author.type in ("agent", "external")
            ):
                plan.append((card.pid, POLICY, policy.by, card.at))
        if plan:
            assert self._batch is not None
            self.last_auto_accept = self._run_auto_accept(plan, self._batch, own_batch=True)

    def auto_accept(
        self,
        pids: Iterable[str],
        *,
        via: str = POLICY,
        decided_by: Actor | None = None,
        at: str | None = None,
    ) -> AutoAcceptOutcome:
        """Auto-accept cards that already exist (spec §5.6).

        For hosts that enforce the policy on behalf of writers that do not
        honour it themselves (an editor watching a file that an older tool
        wrote). Same rule as the batch close: all or nothing after a dry run
        on a clone, in creation order; on failure nothing changes and the
        outcome lists the cards as deferred with the reason. Never raises
        for a refused acceptance; raises for a bad call (unknown id, no
        policy for ``via="policy"``, a non-human *decided_by*).

        ``via="policy"`` needs the document's policy to be ``auto`` and
        accepts only agent- and external-authored cards (human-authored ones
        are deferred); ``decided_by`` defaults to ``review.by``.
        ``via="request"`` accepts any card; ``decided_by`` defaults to
        ``review.by`` when a policy exists, else ``Actor("human")``.
        The events join the open batch if any, else the cards' own
        ``data-batch`` when they all share one, else a new batch.
        """
        if via not in (POLICY, REQUEST):
            raise InvalidOperation(f"auto_accept via must be 'policy' or 'request', got {via!r}")
        if decided_by is not None and decided_by.type != "human":
            raise InvalidOperation("an auto acceptance is decided by a human actor")
        wanted = list(dict.fromkeys(pids))
        pending = {p.id: p for p in self.proposals}
        for pid in wanted:
            if pid not in pending:
                raise TargetNotFound(f"no pending proposal {pid!r}")
        policy = self._auto_policy()
        if via == POLICY and policy is None:
            raise InvalidOperation("the document has no auto review policy to apply")
        decider = decided_by or (policy.by if policy is not None else Actor("human"))
        plan: list[tuple[str, str, Actor, str | None]] = []
        refused: list[str] = []
        for pid in wanted:
            if via == POLICY and pending[pid].author.type not in ("agent", "external"):
                refused.append(pid)
            else:
                plan.append((pid, via, decider, at))
        batches = {pending[pid].batch for pid in wanted}
        batch = self._batch or (
            next(iter(batches)) if len(batches) == 1 and None not in batches else None
        )
        batch = batch or self._next_batch()
        with self._using_batch(batch):
            outcome = self._run_auto_accept(
                plan,
                batch,
                own_batch=False,
                refused=refused,
                refused_reason="proposed by a person" if refused else None,
            )
        self.last_auto_accept = outcome
        return outcome

    @contextlib.contextmanager
    def _using_batch(self, batch: str):
        """Write under an explicit batch id without a batch close pass."""
        if self._batch is not None:
            yield self._batch
            return
        self._batch = batch
        try:
            yield batch
        finally:
            self._batch = None

    def _replaced_human_ids(self, pids: set[str], *, own_batch: bool) -> set[str]:
        """Cards among *pids* whose creation superseded a human-authored card
        (§5.4 supersession records ``superseded_by``). With *own_batch* only
        the open batch's trailing events are read: supersession happens
        inside the creating ``propose_*`` call."""
        found: set[str] = set()
        current = self._batch
        for ev in reversed(self._history_events()):
            if own_batch and ev.batch != current:
                break
            if (
                ev.kind == "resolution"
                and ev.decision == "superseded"
                and ev.get("superseded_by") in pids
                and (ev.get("proposed_by") or {}).get("type") == "human"
            ):
                found.add(ev.get("superseded_by"))
        return found

    def _run_auto_accept(
        self,
        plan: list[tuple[str, str, Actor, str | None]],
        batch: str,
        *,
        own_batch: bool,
        refused: Sequence[str] = (),
        refused_reason: str | None = None,
    ) -> AutoAcceptOutcome:
        """Accept *plan* (pid, via, decided_by, at) all or nothing.

        Out of scope first: a card that replaced a person's pending
        suggestion (§5.6), and an ``aim:doc`` card whose ``review`` differs
        from the live block. The rest is accepted in creation order after a
        clean dry run on a clone; any failure leaves every card pending."""
        deferred: list[str] = list(refused)
        reason = refused_reason
        replaced = self._replaced_human_ids({pid for pid, *_ in plan}, own_batch=own_batch)
        scoped: list[tuple[str, str, Actor, str | None]] = []
        for item in plan:
            pid = item[0]
            if pid in replaced:
                deferred.append(pid)
                reason = reason or f"{pid} replaces a suggestion from a person"
                continue
            prop = self.proposal(pid)
            if prop.target == "aim:doc" and prop.payload_html:
                try:
                    self._guard_card_review(prop.payload_html)
                except AimError:
                    deferred.append(pid)
                    reason = reason or f"{pid} would change the review policy"
                    continue
            scoped.append(item)
        if scoped:
            try:
                order = {p.id: i for i, p in enumerate(resolution_order(self.proposals))}
            except _ChainedAddCycle as exc:
                order = {}
                failure: str | None = str(exc)
            else:
                failure = None
                scoped.sort(key=lambda item: order[item[0]])
            if failure is None:
                trial = self._clone()
                try:
                    with trial._using_batch(batch):
                        events = trial._apply_auto(scoped)
                    odd = next((e for e in events if "applied" in e.data), None)
                    if odd is not None:
                        failure = (
                            f"{odd.get('proposal')} is not in canonical form, so accepting "
                            "it would record a tweak no one reviewed"
                        )
                except Exception as exc:  # foreign cards may fail outside AimError
                    failure = str(exc) or type(exc).__name__
            if failure is not None:
                deferred.extend(pid for pid, *_ in scoped)
                reason = f"auto-accept refused, cards left pending: {failure}"
                scoped = []
            else:
                self._apply_auto(scoped)
        vias = {via for _, via, _, _ in plan}
        decider = next(
            (d for _, via, d, _ in plan if via == POLICY),
            plan[0][2] if plan else None,
        )
        return AutoAcceptOutcome(
            batch=batch,
            via=vias.pop() if len(vias) == 1 else ("mixed" if vias else POLICY),
            decided_by=decider,
            accepted=tuple(pid for pid, *_ in scoped),
            deferred=tuple(dict.fromkeys(deferred)),
            reason=reason if deferred else None,
        )

    def _apply_auto(self, scoped: Sequence[tuple[str, str, Actor, str | None]]) -> list[Event]:
        events: list[Event] = []
        for pid, via, decided_by, at in scoped:
            prop = self.proposal(pid)
            payload = prop.payload_html if prop.action in ("modify", "add") else None
            events.append(
                self._resolve_retaining_paint(
                    prop,
                    decision="accepted",
                    decided_by=decided_by,
                    applied=payload,
                    at=at,
                    auto=via,
                )
            )
        return events

    def resolution_of(self, pid: str) -> Event | None:
        """The resolution event of proposal *pid* (newest first), or None."""
        for ev in reversed(self._history_events()):
            if ev.kind == "resolution" and ev.get("proposal") == pid:
                return Event(deepcopy(ev.data))
        return None

    def auto_accepted_batches(self, *, scan_limit: int = 400, max_batches: int = 5) -> list[dict]:
        """Recent batches holding auto-accepted resolutions, newest first.

        A display helper over the trailing *scan_limit* events (never the
        whole log), at most *max_batches* entries. Each entry::

            {"batch", "t" (the batch's newest event time), "via",
             "decided_by", "proposed_by" (first card's, actor objects),
             "targets": [{"target", "action"}], "changes",
             "undone", "undoable", "revertable"}

        ``undone``: the batch was undone or reverted (and that was not itself
        undone). ``undoable``: it is the top of the undo stack, so
        ``undo(whole_batch=True, batch=...)`` works. ``revertable``: every
        target still holds what the batch left there, so
        :meth:`revert_batch` works. ``t`` is for display age only; history is
        ordered by ``seq`` (§6.3)."""
        events = self._history_events()[-scan_limit:] if scan_limit > 0 else []
        undone = self._undone_seqs(events)
        reverted: set[str] = set()
        for ev in events:
            if ev.seq in undone:
                continue
            for src in ev.get("source") or []:
                if isinstance(src, dict) and src.get("reverts"):
                    reverted.add(src["reverts"])
        top = next(self._undo_candidates(), None)
        out: list[dict] = []
        seen: set[str] = set()
        for ev in reversed(events):
            batch = ev.batch
            if not batch or batch in seen or ev.kind != "resolution" or not ev.get("auto"):
                continue
            seen.add(batch)
            members = [e for e in events if e.batch == batch]
            changes = [e for e in members if e.state_changing and e.target != VERSION_TARGET]
            autos = [e for e in members if e.kind == "resolution" and e.get("auto")]
            vias = {e.get("auto") for e in autos}
            is_undone = batch in reverted or all(e.seq in undone for e in changes)
            times = [e.data["t"] for e in members if isinstance(e.data.get("t"), str)]
            out.append(
                {
                    "batch": batch,
                    "t": max(times, default=None),
                    "via": vias.pop() if len(vias) == 1 else "mixed",
                    "decided_by": deepcopy(autos[0].get("decided_by")),
                    "proposed_by": deepcopy(autos[0].get("proposed_by")),
                    "targets": [{"target": e.target, "action": e.action} for e in changes],
                    "changes": len(changes),
                    "undone": is_undone,
                    "undoable": not is_undone and top is not None and top.batch == batch,
                    "revertable": not is_undone and self._batch_still_applies(changes),
                }
            )
            if len(out) >= max_batches:
                break
        return out

    # -- resolution ---------------------------------------------------------------------------
    def accept_all(
        self,
        *,
        decided_by: Actor,
        explanation: str | None = None,
        at: str | None = None,
    ) -> list[Event]:
        """Atomically accept the pending lane in creation order.

        Invariant: a lane whose every card passed projected validation at
        creation applies cleanly in creation order. The clone replay is the
        universal gate for foreign-authored cards, out-of-band edits, and
        partial manual resolutions that broke that projection. A failing dry
        run never mutates this document.
        """
        pids = [proposal.id for proposal in resolution_order(self.proposals)]
        dry_run = self._clone()
        for pid in pids:
            try:
                dry_run.accept(
                    pid,
                    decided_by=decided_by,
                    explanation=explanation,
                    at=at,
                )
            except Exception as exc:  # foreign cards may fail outside AimError
                raise InvalidOperation(
                    f"cannot accept all: proposal {pid!r} failed: {exc}; "
                    "the document is unchanged. Accept remaining proposals "
                    "individually to repair the lane"
                ) from exc

        return [
            self.accept(
                pid,
                decided_by=decided_by,
                explanation=explanation,
                at=at,
            )
            for pid in pids
        ]

    def reject_all(
        self,
        *,
        decided_by: Actor,
        explanation: str | None = None,
        at: str | None = None,
    ) -> list[Event]:
        """Reject the pending lane, preserving chained-add rebinding."""
        pids = [proposal.id for proposal in resolution_order(self.proposals)]
        return [
            self.reject(
                pid,
                decided_by=decided_by,
                explanation=explanation,
                at=at,
            )
            for pid in pids
        ]

    def accept(
        self,
        pid: str,
        *,
        decided_by: Actor,
        applied: str | None = None,
        explanation: str | None = None,
        at: str | None = None,
    ) -> Event:
        """Accept a pending proposal; ``applied`` overrides the payload
        (accept-with-tweaks)."""
        prop = self.proposal(pid)
        applied_payload: str | None = None
        if prop.action in ("modify", "add"):
            if applied is not None:
                expect = prop.target if prop.action == "modify" else None
                if prop.target == "aim:theme":
                    applied_payload = self._validated_theme_markup(applied)
                elif prop.target == "aim:doc":
                    applied_payload = self._validated_doc_markup(applied)
                else:
                    _, applied_payload = (
                        self._normalize_payload(applied, expect_id=expect, assign=False)
                        if expect
                        else self._payload_like(
                            prop.payload_html or "", applied, skip_payload_of=prop.id
                        )
                    )
            else:
                applied_payload = prop.payload_html
            if prop.target == "aim:doc" and applied_payload is not None:
                # the review policy changes only by a direct edit (§5.6): a
                # stale or hostile settings card applies its page setup and
                # keeps the live policy, recorded as the honest `applied`
                applied_payload = self._keep_live_review(applied_payload)
        return self._resolve_retaining_paint(
            prop,
            decision="accepted",
            decided_by=decided_by,
            applied=applied_payload,
            explanation=explanation,
            at=at,
        )

    def _resolve_retaining_paint(
        self,
        prop: Proposal,
        *,
        decision: str,
        decided_by: Actor,
        applied: str | None = None,
        superseded_by: str | None = None,
        explanation: str | None = None,
        at: str | None = None,
        auto: str | None = None,
    ) -> Event:
        """Resolve a card after versioning every gated construct the event
        retains: payload markup, and the ``auto`` marker itself (§5.6)."""
        floors = [
            f
            for f in (
                _floor_of(_binding_payload(prop.payload_html, applied)),
                REGISTRY.auto_since if auto else None,
            )
            if f is not None
        ]
        floor = max(floors, key=lambda f: version_key(f) or ()) if floors else None
        upgrade_batch: str | None = None
        if floor is not None:
            self._preflight_version_floor(
                floor,
                lambda trial: trial._resolve(
                    trial.proposal(prop.id),
                    decision=decision,
                    decided_by=decided_by,
                    applied=applied,
                    superseded_by=superseded_by,
                    explanation=explanation,
                    at=at,
                    auto=auto,
                ),
            )
            upgrade_batch = self._ensure_version_floor(floor, author=decided_by, at=at)
        return self._resolve(
            prop,
            decision=decision,
            decided_by=decided_by,
            applied=applied,
            superseded_by=superseded_by,
            explanation=explanation,
            at=at,
            batch=upgrade_batch,
            auto=auto,
        )

    def _payload_like(
        self, original: str, replacement: str, *, skip_payload_of: str | None = None
    ) -> tuple[str, str]:
        """Canonicalize an add-tweak/amend payload, keeping the proposed
        chunk id and marker kind. The replacement must also keep the
        proposed root's KIND (§4.3): flipping container↔chunk would mint a
        card whose marker contradicts its tag (V003) or accept an
        ``aim-slide`` marked as a chunk (S031) — review finding.
        ``skip_payload_of`` names the card being rewritten so the nested
        ids it reserved for itself stay honored instead of being reminted
        as collisions with the card's own record."""
        orig_nodes = [n for n in parse_fragment(original) if isinstance(n, Element)]
        if not orig_nodes:  # a template-less card (P006): a real error the
            # MCP boundary can catch, not a bare IndexError escaping it
            raise InvalidOperation(
                "the pending add carries no payload template to tweak/amend against"
            )
        keep = orig_nodes[0].chunk_id or orig_nodes[0].container_id
        marker = "data-aim-container" if orig_nodes[0].container_id is not None else "data-aim"
        new_nodes = [n for n in parse_fragment(replacement) if isinstance(n, Element)]
        if new_nodes:  # empty payloads fail in _normalize_payload below
            self._guard_replacement_kind(
                keep or orig_nodes[0].tag,
                new_nodes[0],
                kind="container" if marker == "data-aim-container" else "chunk",
            )
        return self._normalize_payload(
            replacement,
            expect_id=keep,
            expect_marker=marker,
            skip_payload_of=skip_payload_of,
        )

    def _validated_theme_markup(self, markup: str) -> str:
        """Validate + canonicalize a whole-theme-block payload."""
        nodes = [n for n in parse_fragment(markup) if isinstance(n, Element)]
        el = nodes[0] if len(nodes) == 1 else None
        if el is None or el.tag != "style" or not el.has("data-aim-theme"):
            raise InvalidOperation("theme payload must be a single <style data-aim-theme> block")
        m = re.fullmatch(r"\s*:root\{([^{}]*)\}\s*", el.raw or "")
        if m is None:
            raise InvalidOperation("theme payload must be one :root{…} rule")
        slots: dict[str, str] = {}
        for piece in filter(None, (p.strip() for p in m.group(1).split(";"))):
            name, _, value = (s.strip() for s in piece.partition(":"))
            slots[name] = value
        self._check_theme_slots(slots)
        return serialize(el)

    def _validated_doc_markup(self, markup: str) -> str:
        """Validate + canonicalize a whole-settings-block payload.

        Registered page fields go through the shared PageSetup grammar,
        while unknown nested fields remain intact for forward compatibility.
        """
        el = doc_settings_element(markup)
        settings = parse_doc_settings(el.raw)
        if "page" in settings:
            page_setup_from_obj(settings["page"])
        if "review" in settings:
            ReviewPolicy.from_obj(
                settings["review"], lenient=not REGISTRY.implements(self.spec_version)
            )
        return self._settings_script(settings)

    def reject(
        self,
        pid: str,
        *,
        decided_by: Actor,
        explanation: str | None = None,
        at: str | None = None,
    ) -> Event:
        return self._resolve_retaining_paint(
            self.proposal(pid),
            decision="rejected",
            decided_by=decided_by,
            explanation=explanation,
            at=at,
        )

    def _resolve(
        self,
        prop: Proposal,
        *,
        decision: str,
        decided_by: Actor,
        applied: str | None = None,
        superseded_by: str | None = None,
        explanation: str | None = None,
        at: str | None = None,
        batch: str | None = None,
        auto: str | None = None,
    ) -> Event:
        if auto is not None and (decision != "accepted" or decided_by.type != "human"):
            raise InvalidOperation(
                "an auto acceptance must be an accepted resolution decided by a human"
            )
        card = self._card_el(prop.id)
        data: dict = {
            "seq": self.seq + 1,
            "kind": "resolution",
            "t": at or _now_iso(),
            "proposal": prop.id,
            "action": prop.action,
            "decision": decision,
            "proposed_by": prop.author.to_obj(),
            "proposed_at": prop.at,
            "decided_by": decided_by.to_obj(),
            "batch": batch or self._batch_id(),
        }
        if superseded_by:
            data["superseded_by"] = superseded_by
        if auto is not None:
            data["auto"] = auto
        if explanation:
            data["explanation"] = explanation
        elif prop.explanation:
            data["explanation"] = prop.explanation

        effective_anchor: Anchor | None = None
        move_source: Anchor | None = None
        noop_move = False
        try:
            ordered_ids = [p.id for p in resolution_order(self.proposals)]
        except _ChainedAddCycle:
            ordered_ids = [p.id for p in self.proposals]
        card_pos = {pid: i for i, pid in enumerate(ordered_ids)}
        later_ids = (
            frozenset(ordered_ids[ordered_ids.index(prop.id) + 1 :])
            if prop.id in ordered_ids
            else frozenset()
        )
        if prop.action == "add":
            anchor = Anchor(
                prop.anchor_container or "body", prop.anchor_after, shell=prop.anchor_shell
            )
            data["target"] = self._payload_root_id(prop.payload_html or "")
            data["proposed"] = prop.payload_html
            if decision == "accepted":
                # a card anchored on a still-pending card may be accepted
                # first: it lands at the chain's zone, and the parent —
                # inserting directly after the same zone anchor — lands in
                # front of it later. Record the anchor actually used.
                anchor = effective_anchor = self._effective_anchor(prop)
            data["anchor"] = anchor.to_obj()
            if decision == "accepted":
                payload = applied if applied is not None else prop.payload_html
                # externally-authored cards bypass creation-time
                # normalization: re-validate the FULL payload (root marker
                # and id, run shape, nested ids) before the write, exactly
                # like the modify branch below — the resolution event's
                # target must be the payload's real, unused root id or the
                # add can never replay
                root = data["target"]
                if not ids.is_valid_chunk_id(root):
                    raise InvalidOperation(
                        "add payload root carries no valid data-aim/data-aim-container id"
                    )
                if root in self._taken_ids(skip_payload_of=prop.id):
                    raise InvalidOperation(f"add payload id {root!r} is already in use")
                _, payload = self._normalize_payload(
                    payload or "",
                    expect_id=root,
                    skip_payload_of=prop.id,
                )
                if payload != prop.payload_html:
                    data["applied"] = payload
                self._state.insert(payload, anchor)
        else:
            data["target"] = prop.target
            before = self._state.serial(prop.target or "")
            if before is not None:
                data["before"] = before
            if prop.action == "modify":
                data["proposed"] = prop.payload_html
                if decision == "accepted":
                    payload = applied if applied is not None else prop.payload_html
                    if prop.target not in _RESERVED_TARGETS:
                        # externally-authored proposals bypass creation-time
                        # normalization: re-validate the FULL payload (every
                        # root's id and kind, run shape, nested ids) before
                        # the write — guarding only the first root lets a
                        # second one smuggle arbitrary structure past accept.
                        # aim:theme/aim:doc payloads have their own grammar,
                        # enforced by _state.replace.
                        _, payload = self._normalize_payload(
                            payload or "",
                            expect_id=prop.target,
                            assign=False,
                            skip_payload_of=prop.id,
                        )
                    if payload != prop.payload_html:
                        # the written form diverged from the card (a tweak,
                        # or a hand-authored card in non-canonical form) —
                        # record what actually landed, so verify() replays
                        # against the true result
                        data["applied"] = payload
                    self._state.replace(prop.target or "", payload or "")
            elif prop.action == "delete" and decision == "accepted":
                # a hand-authored card can still carry a reserved target;
                # fail with intent (reject/supersede stay available)
                _no_delete_move(prop.target or "", "delete proposal")
                removed_anchor = self._anchor_of(prop.target or "")
                self._guard_geometry_dependents(
                    prop, vacated_block=prop.target or "", incoming_dst=None
                )
                if self._cards_anchored_on(prop.target or ""):
                    self._guard_dissolve_target(prop.target or "", removed_anchor, prop)
                    self._guard_dissolve_stacking(prop.target or "", removed_anchor, prop)
                data["anchor"] = removed_anchor.to_obj()
                self._state.remove(prop.target or "")
                self._rebind_removed_anchor(prop.target or "", removed_anchor)
            elif prop.action == "move":
                dst = Anchor(
                    prop.anchor_container or "body", prop.anchor_after, shell=prop.anchor_shell
                )
                if decision == "accepted":
                    dst = effective_anchor = self._effective_anchor(prop)
                data["to"] = dst.to_obj()
                if decision == "accepted":
                    _no_delete_move(prop.target or "", "move proposal")
                    nested_parent = self._nested_move_parent(prop.target or "")
                    if nested_parent is not None:
                        raise InvalidOperation(
                            f"cannot move {prop.target!r} out of nested markup in "
                            f"{nested_parent!r}; reject the proposal or modify the "
                            "enclosing target instead"
                        )
                    src = self._anchor_of(prop.target or "")
                    if src.after is not None:
                        hazard = self._earlier_pending_move_of(
                            src.after, prop, actions=("move", "delete")
                        )
                        if hazard is not None:
                            # whether this move is a real relocation or a
                            # positional no-op depends on the fate of its
                            # source predecessor — undecided until that
                            # earlier card resolves
                            raise InvalidOperation(
                                f"move {prop.id!r} cannot resolve while pending "
                                f"{hazard.action} {hazard.id!r} of its source "
                                f"predecessor {src.after!r} is undecided — "
                                "resolve that card first"
                            )
                    if dst.after == prop.target:
                        if prop.anchor_after is not None and ids.is_valid_proposal_id(
                            prop.anchor_after
                        ):
                            # the self-anchor came from CHASING a pending
                            # chain whose zone is this move's own target:
                            # whether the block ends up before or after the
                            # chain's payloads depends on those cards —
                            # undecided until they resolve
                            raise InvalidOperation(
                                f"move {prop.id!r} chains onto pending "
                                f"{prop.anchor_after!r} in the zone of its own "
                                f"target {prop.target!r} — resolve that card first"
                            )
                        # a REJECT fallback can point a move at its own target
                        # ("after where the rejected parent would have been"):
                        # the block is already there — a harmless no-op, not
                        # an error. Nothing lands and nothing vacates, so no
                        # rebinds fire either.
                        dst = src
                        data["to"] = dst.to_obj()
                        effective_anchor = None
                        noop_move = True
                    elif (src.container, src.after, src.shell) == (
                        dst.container,
                        dst.after,
                        dst.shell,
                    ):
                        # destination == current position: the block never
                        # leaves, so nothing vacates and nothing lands anew
                        effective_anchor = None
                        noop_move = True
                    else:
                        self._guard_geometry_dependents(
                            prop, vacated_block=prop.target or "", incoming_dst=dst
                        )
                        vacated = [
                            c
                            for c in self._cards_anchored_on(prop.target or "")
                            if (c.get("id") or "") not in later_ids and c.get("id") != prop.id
                        ]
                        if vacated:
                            self._guard_dissolve_target(prop.target or "", src, prop)
                            self._guard_dissolve_stacking(prop.target or "", src, prop)
                            self._guard_vacated_descendants(vacated, later_ids, prop)
                        move_source = src
                    data["anchor"] = dst.to_obj()
                    data["from"] = src.to_obj()
                    self._state.move(prop.target or "", dst)

        # drop the card; rebind dependent position cards anchored on this proposal
        sec = self._state.section("aim-proposals")
        assert sec is not None
        sec.children.remove(card)
        if not sec.elements():
            self._state.body.children.remove(sec)
        self._get_history_index().remove_proposal(prop)
        self._rebind_chained(
            prop,
            decision,
            later_ids=later_ids,
            pos=card_pos,
            effective=effective_anchor,
            move_source=move_source,
            noop_move=noop_move,
        )
        return self._append_event(data)

    def _effective_anchor(self, prop: Proposal) -> Anchor:
        """The concrete anchor *prop* resolves to, chasing any pending-card
        chain (§5.4). Position cards accept in any order: a card anchored on
        a still-pending card lands at the chain's zone, and the parent —
        inserting directly after the same zone anchor — lands in front of it
        when it arrives. Two positions genuinely do not resolve and refuse:
        a dangling/cyclic foreign chain, and an anchor block whose position
        is itself undecided — an EARLIER-proposed pending move of that block
        means this card was validated against the block at the move's
        destination, and accept cannot know whether the move will be
        accepted (land there) or rejected (stay). Resolve the move first."""
        container = prop.anchor_container or "body"
        after = prop.anchor_after
        shell = prop.anchor_shell
        seen: set[str] = set()
        while after is not None and ids.is_valid_proposal_id(after):
            if after in seen:
                raise InvalidOperation(
                    f"anchor chain of proposal {prop.id!r} is cyclic at {after!r}"
                )
            seen.add(after)
            try:
                parent = self.proposal(after)
            except AimError as exc:
                raise InvalidOperation(
                    f"anchor proposal {after!r} of {prop.id!r} is not pending"
                ) from exc
            if parent.action not in ("add", "move"):
                # §5.2: only position cards may be chain anchors — a modify/
                # delete card carries no position to chain through
                raise InvalidOperation(
                    f"anchor proposal {after!r} of {prop.id!r} is a "
                    f"{parent.action} card, not a position card"
                )
            container = parent.anchor_container or "body"
            after = parent.anchor_after
            shell = parent.anchor_shell
        if after is not None:
            earlier = self._earlier_pending_move_of(after, prop)
            if earlier is not None:
                raise InvalidOperation(
                    f"anchor {after!r} of proposal {prop.id!r} has a pending move "
                    f"({earlier.id!r}) proposed before it — the anchor's position "
                    "is undecided; resolve that move first"
                )
        # one lane scan powers both remaining guards: (a) a zone split (a
        # PENDING move of the anchor block between two cards) orders the
        # later generation closer to the block, so a later-generation card
        # must not land while an earlier-generation zone-mate is pending;
        # (b) an EARLIER-proposed pending move landing in this zone means
        # whether its block becomes this card's neighbor is unknown until
        # that move resolves
        sec0 = self._state.section("aim-proposals")
        if sec0 is not None:
            cards0 = list(sec0.elements())
            by_id0 = {c.get("id"): c for c in cards0}
            holder0, zone0 = self._zone_maps(cards0, by_id0)
            try:
                ordered0 = [p.id for p in resolution_order(self.proposals)]
            except _ChainedAddCycle:
                ordered0 = [p.id for p in self.proposals]
            pos0 = {pid: i for i, pid in enumerate(ordered0)}
            big0 = len(pos0) + 1
            own_pos = pos0.get(prop.id, big0)
            key = (container, after, shell)
            move_pos = [
                pos0.get(c.get("id") or "", big0)
                for c in cards0
                if c.get("data-action") == "move" and c.get("data-for") == after
            ]
            for c in cards0:
                cid0 = c.get("id") or ""
                if cid0 == prop.id or c.get("data-action") not in ("add", "move"):
                    continue
                if c.get("data-action") == "move" and pos0.get(cid0, big0) < own_pos:
                    if zone0.get(cid0) == key:
                        raise InvalidOperation(
                            f"anchor zone of proposal {prop.id!r} is the destination "
                            f"of pending move {cid0!r} proposed earlier — the "
                            "zone's content is undecided; resolve that move first"
                        )
                if after is None or zone0.get(cid0) != key:
                    continue
                holder = holder0.get(cid0)
                other_el = holder if holder is not None else c
                other_pos = pos0.get(other_el.get("id") or "", big0)
                if other_pos < own_pos and any(other_pos < mp < own_pos for mp in move_pos):
                    raise InvalidOperation(
                        f"proposal {prop.id!r} sits on the later side of a zone "
                        f"split of {after!r}, while {cid0!r} from the "
                        "earlier side is still pending — resolve that card first"
                    )
        return Anchor(container, after, shell=shell)

    def _earlier_pending_move_of(
        self,
        target: str,
        prop: Proposal | None,
        *,
        actions: tuple[str, ...] = ("move",),
    ) -> Proposal | None:
        """The pending *actions* card on *target* proposed before *prop*
        (``None`` = "now", i.e. any pending one counts)."""
        move = next(
            (p for p in self.proposals if p.action in actions and p.target == target),
            None,
        )
        if move is None or prop is None:
            return move
        if move.id == prop.id:
            return None
        try:
            ordered = [p.id for p in resolution_order(self.proposals)]
        except _ChainedAddCycle:
            ordered = [p.id for p in self.proposals]
        if prop.id not in ordered or move.id not in ordered:
            return None
        return move if ordered.index(move.id) < ordered.index(prop.id) else None

    def _guard_vacated_descendants(
        self, vacated: list[Element], later_ids: frozenset[str], prop: Proposal
    ) -> None:
        """Refuse a vacation whose dissolved cards still carry chain
        descendants created AFTER the move: the descendants track the
        block's destination while the dissolve tracks its source, so a
        later reject of the dissolved card would hand them the wrong side.
        Creation order resolves the dissolved card before the move, so an
        in-order resolution never hits this."""
        sec = self._state.section("aim-proposals")
        assert sec is not None
        children: dict[str, list[Element]] = {}
        for c in sec.elements():
            after = c.get("data-anchor-after")
            if after is not None and ids.is_valid_proposal_id(after):
                children.setdefault(after, []).append(c)
        queue = [c.get("id") or "" for c in vacated]
        seen: set[str] = set()
        while queue:
            pid = queue.pop()
            if pid in seen:
                continue
            seen.add(pid)
            for child in children.get(pid, ()):
                cid = child.get("id") or ""
                if cid in later_ids:
                    raise InvalidOperation(
                        f"accepting {prop.id!r} vacates the anchor of {pid!r}, "
                        f"which still has a dependent card ({cid!r}) proposed "
                        "after this move — resolve the anchored cards first"
                    )
                queue.append(cid)

    def _guard_geometry_dependents(
        self, prop: Proposal | None, *, vacated_block: str, incoming_dst: Anchor | None
    ) -> None:
        """Refuse resolving a move/delete while an EARLIER-proposed pending
        move's source geometry hangs on it: a move whose target currently
        sits directly after the vacated block (its source predecessor is
        about to vanish), or directly after the incoming destination (its
        source predecessor is about to become the moved block). Creation
        order resolves the earlier move first, so an in-order resolution
        never hits this; out of order, the geometry the earlier move was
        proposed against would be silently destroyed."""
        for p in self.proposals:
            if p.action != "move" or not p.target:
                continue
            if prop is not None and p.id == prop.id:
                continue
            if (
                self._earlier_pending_move_of(p.target, prop, actions=("move",)) is None
                and prop is not None
            ):
                continue
            if not self._state.exists(p.target):
                continue
            pred = self._anchor_of(p.target).after
            hit = pred == vacated_block or (
                incoming_dst is not None
                and pred == incoming_dst.after
                and p.target != vacated_block
            )
            if hit:
                who = f"accepting {prop.id!r}" if prop is not None else "this edit"
                raise InvalidOperation(
                    f"{who} would change the source geometry of pending move "
                    f"{p.id!r} (its target {p.target!r} sits directly after the "
                    "affected position) — resolve that move first"
                )

    def _cards_anchored_on(self, block: str) -> list[Element]:
        """Pending position cards whose recorded anchor is *block*."""
        sec = self._state.section("aim-proposals")
        if sec is None:
            return []
        return [
            c
            for c in sec.elements()
            if c.get("data-anchor-after") == block and c.get("data-action") in ("add", "move")
        ]

    def _guard_dissolve_target(self, block: str, anchor: Anchor, prop: Proposal | None) -> None:
        """Refuse a removal/vacation whose dissolve would merge cards onto a
        block whose own position is undecided — an earlier pending move of
        the merge target means the merged cards' landing depends on that
        move's outcome. Creation order resolves the move first, so an
        accept only trips this out of order; a direct edit trips it while
        any such move is pending."""
        if anchor.after is None:
            return
        mv = self._earlier_pending_move_of(anchor.after, prop)
        if mv is not None:
            who = f"accepting {prop.id!r}" if prop is not None else f"removing {block!r}"
            raise InvalidOperation(
                f"{who} would rebind cards anchored on {block!r} onto "
                f"{anchor.after!r}, whose position is undecided (pending move "
                f"{mv.id!r}) — resolve that move first"
            )

    def _payload_root_id(self, payload: str) -> str:
        nodes = [n for n in parse_fragment(payload) if isinstance(n, Element)]
        return (nodes[0].chunk_id or nodes[0].container_id or "") if nodes else ""

    @staticmethod
    def _zone_maps(
        cards: list[Element], by_id: dict[str | None, Element]
    ) -> tuple[
        dict[str, Element | None],
        dict[str, tuple[str, str | None, str | None] | None],
    ]:
        """Holder and zone for every card, path-compressed: each walked
        chain memoizes its result onto every member, so a long chain costs
        one traversal instead of one per member (a per-card chase made
        accept_all cubic — review finding)."""
        holder_of: dict[str, Element | None] = {}
        zone_of: dict[str, tuple[str, str | None, str | None] | None] = {}
        for c in cards:
            if (c.get("id") or "") in holder_of:
                continue
            path: list[str] = []
            walk_seen: set[str] = set()
            cur = c
            holder: Element | None = None
            zone: tuple[str, str | None, str | None] | None = None
            while True:
                ccid = cur.get("id") or ""
                if ccid in holder_of:  # memoized suffix from an earlier walk
                    holder = holder_of[ccid]
                    zone = zone_of[ccid]
                    break
                after = cur.get("data-anchor-after")
                if after is None or not ids.is_valid_proposal_id(after):
                    path.append(ccid)
                    holder = cur
                    zone = (
                        cur.get("data-anchor-container") or "body",
                        after,
                        cur.get("data-anchor-shell"),
                    )
                    break
                if ccid in walk_seen:  # foreign cycle: the whole path is dead
                    break
                walk_seen.add(ccid)
                path.append(ccid)
                parent = by_id.get(after)
                if parent is None or parent.get("data-action") not in ("add", "move"):
                    break  # dangling or non-position parent: path is dead
                cur = parent
            for walked in path:
                holder_of[walked] = holder
                zone_of[walked] = zone
        return holder_of, zone_of

    @staticmethod
    def _zone_holder(card: Element, by_id: dict[str | None, Element]) -> Element | None:
        """The card in the chain that carries the concrete zone anchor (the
        card itself when directly anchored); None on a dangling or cyclic
        foreign chain."""
        seen: set[str] = set()
        while True:
            after = card.get("data-anchor-after")
            if after is None or not ids.is_valid_proposal_id(after):
                return card
            cid = card.get("id") or ""
            parent = by_id.get(after)
            if parent is None or cid in seen or parent.get("data-action") not in ("add", "move"):
                return None
            seen.add(cid)
            card = parent

    @staticmethod
    def _card_zone(
        card: Element, by_id: dict[str | None, Element]
    ) -> tuple[str, str | None, str | None] | None:
        """Concrete anchor tuple this card's position chain bottoms out at;
        None for a dangling or cyclic foreign chain (reconcile's territory,
        never rebound here)."""
        seen: set[str] = set()
        while True:
            after = card.get("data-anchor-after")
            if after is None or not ids.is_valid_proposal_id(after):
                return (
                    card.get("data-anchor-container") or "body",
                    after,
                    card.get("data-anchor-shell"),
                )
            cid = card.get("id") or ""
            parent = by_id.get(after)
            if parent is None or cid in seen or parent.get("data-action") not in ("add", "move"):
                return None
            seen.add(cid)
            card = parent

    def _dissolve_tail(
        self,
        cards: list[Element],
        dissolved_ids: set[str | None],
        anchor: Anchor,
    ) -> str | None:
        """The merged zone's last pending position card (dissolve chains
        attach here), or None when the zone has no pending cards."""
        by_id = {c.get("id"): c for c in cards}
        key = (anchor.container, anchor.after, anchor.shell)
        tail: str | None = None
        for c in cards:
            if (
                c.get("id") not in dissolved_ids
                and c.get("data-action") in ("add", "move")
                and self._card_zone(c, by_id) == key
            ):
                tail = c.get("id")
        return tail

    def _guard_dissolve_stacking(self, removed: str, anchor: Anchor, prop: Proposal) -> None:
        """Refuse a dissolve whose tail already carries dissolve-chained
        dependents: stacking a second dissolved zone onto the same tail
        loses which zone each card came from, and with it their geometric
        order. In creation order a dissolve never fires at all (cards
        anchored on the target were proposed before it and have already
        resolved), so an in-order resolution never hits this."""
        sec = self._state.section("aim-proposals")
        if sec is None:
            return
        cards = list(sec.elements())
        dissolved_ids = {c.get("id") for c in cards if c.get("data-anchor-after") == removed}
        if not dissolved_ids:
            return
        tail = self._dissolve_tail(cards, dissolved_ids, anchor)
        if tail is None:
            return
        dependents = [c for c in cards if c.get("data-anchor-after") == tail]
        if dependents:
            names = ", ".join(repr(c.get("id") or "") for c in dependents)
            raise InvalidOperation(
                f"accepting {prop.id!r} would stack a second dissolved zone onto "
                f"{tail!r}, which already carries chained cards ({names}) — "
                "resolve those first"
            )

    def _rebind_removed_anchor(
        self, removed: str, anchor: Anchor, *, only: set[str | None] | None = None
    ) -> None:
        """A block leaving its position takes the zone behind it along.

        Pending position cards anchored on it rebind onto the LAST pending
        position card of the zone they merge into — a proposal-id chain, so
        their blocks keep landing after that whole zone, preserving the
        geometric order the removal dissolved — or directly onto the block's
        own anchor when the zone has no pending cards. Runs for deletes
        (every anchored card; without a rebind one delete leaves the whole
        lane unprojectable) and for accepted moves (*only* the cards created
        before the move: they meant "after the block where it was"; cards
        created after it were proposed against the moved projection and
        follow the block)."""
        sec = self._state.section("aim-proposals")
        if sec is None:
            return
        cards = list(sec.elements())
        dissolved_ids = {
            c.get("id")
            for c in cards
            if c.get("data-anchor-after") == removed and (only is None or c.get("id") in only)
        }
        if not dissolved_ids:
            return
        tail = self._dissolve_tail(cards, dissolved_ids, anchor)
        for card in cards:
            if card.get("id") not in dissolved_ids:
                continue
            # a dissolved MOVE whose own target IS the merge-zone block must
            # not chain into that zone (its chase would bottom at its own
            # target — an undecidable state creation order could then hit):
            # bind it concretely instead, where it accepts as a harmless
            # no-op ("after where the removed block was" = stay put)
            if tail is not None and not (
                card.get("data-action") == "move" and card.get("data-for") == anchor.after
            ):
                # the tail shares the exact zone (shell included): the card
                # KEEPS its shell, so a later chain bypass can restore a
                # concrete in-shell anchor instead of a bare container slot
                card.set("data-anchor-after", tail)
                continue
            if anchor.after is None:
                card.remove_attr("data-anchor-after")
            else:
                card.set("data-anchor-after", anchor.after)
            if anchor.shell is None:
                card.remove_attr("data-anchor-shell")
            else:
                card.set("data-anchor-shell", anchor.shell)

    def _rebind_chained(
        self,
        resolved: Proposal,
        decision: str,
        *,
        later_ids: frozenset[str] = frozenset(),
        pos: dict[str, int] | None = None,
        effective: Anchor | None = None,
        move_source: Anchor | None = None,
        noop_move: bool = False,
    ) -> None:
        """Materialize or bypass a pending-add position dependency.

        Acceptance also rebinds every LATER-created pending position card
        whose anchor — followed through any pending-add chain — bottoms out
        at *resolved*'s exact anchor onto the block that just landed: each
        accept inserts directly after its anchor, so without the rebind the
        later card's block would land BEFORE the earlier card's, reversing
        creation order. Earlier-created cards keep their anchor — they land
        before the resolved card's block under any acceptance order, which
        already is creation order. Following chains keeps that true when a
        later sibling is accepted while an earlier one (with chained
        children proposed after the sibling) is still pending: the children
        detach onto the landed block, which their creation order demands
        they follow anyway, whatever becomes of their parent.
        """
        sec = self._state.section("aim-proposals")
        if sec is None:
            return
        landed: str | None = None
        if decision == "accepted" and not noop_move:
            if resolved.action == "add":
                landed = self._payload_root_id(resolved.payload_html or "") or None
            elif resolved.action == "move":
                landed = resolved.target
        # the key is where the block actually landed: for a card accepted
        # ahead of its chain parent that is the chain's zone, not the raw
        # proposal-id anchor still on the card
        anchor_key = (
            (effective.container, effective.after, effective.shell)
            if effective is not None
            else (
                resolved.anchor_container or "body",
                resolved.anchor_after,
                resolved.anchor_shell,
            )
        )
        cards = list(sec.elements())
        by_id = {c.get("id"): c for c in cards}

        # a chained card inherits its chain HOLDER's view of the zone block
        # (the pending card that carries the concrete anchor): its zone rank
        # is (holder position, own position) in the dependency-adjusted lane
        # order — the canonical creation order; data-at is advisory and may
        # tie (one-second precision) or run backwards in a foreign lane.
        # Rank primary = max(holder, own): a normal forward chain (parent
        # proposed first) ranks by the card itself, while a dissolve-created
        # BACKWARD chain (parent proposed later) ranks by the holder it now
        # lands behind; own position breaks ties among siblings under one
        # parent.
        pos = pos or {}
        big = len(pos) + 1

        def pos_of(pid: str | None) -> int:
            return pos.get(pid or "", big)

        resolved_pos = pos_of(resolved.id)
        resolved_holder_pos = resolved_pos
        if resolved.anchor_after is not None and ids.is_valid_proposal_id(resolved.anchor_after):
            parent = by_id.get(resolved.anchor_after)
            rh = self._zone_holder(parent, by_id) if parent is not None else None
            if rh is not None:
                resolved_holder_pos = pos_of(rh.get("id"))
        resolved_rank = (max(resolved_holder_pos, resolved_pos), resolved_pos)

        # one pass over the lane: holders, zones, and pending-move positions
        # by target — same_zone_side and the rebind scan below stay O(lane)
        # per acceptance, with chain walks path-compressed in _zone_maps
        holder_of, zone_of = self._zone_maps(cards, by_id)
        pending_move_pos: dict[str, list[int]] = {}
        for c in cards:
            cid = c.get("id") or ""
            if c.get("data-action") == "move" and c.get("data-for"):
                pending_move_pos.setdefault(c.get("data-for") or "", []).append(pos_of(cid))

        def zone_rank(card: Element) -> tuple[int, int]:
            own = pos_of(card.get("id"))
            holder = holder_of.get(card.get("id") or "")
            if holder is None:
                return (own, own)
            return (max(pos_of(holder.get("id")), own), own)

        def same_zone_side(card: Element) -> bool:
            """A PENDING move of the zone's anchor block sitting between the
            two cards' chain holders splits the zone: the holders made their
            claims against potentially different geometries and must not
            group as siblings. Once that move resolves the zone re-unifies —
            an accepted move relocates content per the vacation rule, and a
            rejected move leaves plain creation order among the cards still
            pending (orders interleaved around the pending move fall under
            §5.4's documented move-space limitation)."""
            block = anchor_key[1]
            if block is None:
                return True
            holder = holder_of.get(card.get("id") or "")
            if holder is None:
                return True
            hpos = pos_of(holder.get("id"))
            lo, hi = min(resolved_holder_pos, hpos), max(resolved_holder_pos, hpos)
            return not any(lo < mp < hi for mp in pending_move_pos.get(block, ()))

        # the resolved card's own chain ancestors must never be captured by
        # the same-zone rebind: a child accepted first lands at the zone
        # precisely so its parents can land IN FRONT of it later — and a
        # dissolve-chain can make an ancestor LATER-created, so later_ids
        # alone does not exclude it
        ancestors: set[str] = set()
        walk = resolved.anchor_after
        while walk is not None and ids.is_valid_proposal_id(walk) and walk not in ancestors:
            ancestors.add(walk)
            parent_el = by_id.get(walk)
            if parent_el is None:
                break
            walk = parent_el.get("data-anchor-after")

        prelim = [
            card
            for card in cards
            if card.get("data-anchor-after") != resolved.id
            and landed is not None
            and zone_rank(card) > resolved_rank
            and (card.get("id") or "") not in ancestors
            and card.get("data-action") in ("add", "move")
            # a move whose own target just landed must keep its anchor:
            # rebinding it onto `landed` would read "move X after X",
            # turning it into an unresolvable card
            and card.get("data-for") != landed
            and zone_of.get(card.get("id") or "") == anchor_key
            and same_zone_side(card)
        ]
        # a chained candidate with ANY chain ancestor also being rebound
        # must not be rewritten too: it follows that ancestor (rebinding
        # both would flatten the chain and reverse their order); it
        # detaches only when its whole chain stays put. The ultimate
        # holder alone is not enough: in a foreign-reordered chain the
        # direct parent can be a candidate while the holder is not
        # (round-8 review finding)
        prelim_ids = {c.get("id") for c in prelim}
        rebound = []
        for card in prelim:
            follows = False
            seen_walk: set[str] = set()
            walk_el = card
            while True:
                nxt = walk_el.get("data-anchor-after")
                if nxt is None or not ids.is_valid_proposal_id(nxt) or nxt in seen_walk:
                    break
                if nxt in prelim_ids:
                    follows = True
                    break
                seen_walk.add(nxt)
                parent_el = by_id.get(nxt)
                if parent_el is None:
                    break
                walk_el = parent_el
            if not follows:
                rebound.append(card)
        # an accepted move vacates a position: a card created BEFORE the move
        # and anchored on its target meant "after the block where it was" —
        # in creation order it lands before the move applies — so its zone
        # dissolves into the source zone, exactly like a deleted anchor.
        # Later-created cards keep the anchor: they were proposed against
        # the projection with the block already at its destination, and
        # follow it there.
        vacated_ids: set[str | None] = set()
        if move_source is not None and resolved.target:
            vacated_ids = {
                card.get("id")
                for card in cards
                if card.get("data-anchor-after") == resolved.target
                and (card.get("id") or "") not in later_ids
                and card.get("data-action") in ("add", "move")
            }
        # rejecting a card that a dissolve made a zone TAIL must hand its
        # dependents to the REMAINING tail of that zone — binding them to the
        # raw anchor would cut them in front of zone-mates that creation
        # order puts first (round-7 review finding)
        reselected: str | None = None
        if decision != "accepted":
            if resolved.anchor_after is not None and ids.is_valid_proposal_id(
                resolved.anchor_after
            ):
                parent = by_id.get(resolved.anchor_after)
                rzone = zone_of.get(parent.get("id") or "") if parent is not None else None
            else:
                rzone = (
                    resolved.anchor_container or "body",
                    resolved.anchor_after,
                    resolved.anchor_shell,
                )
            if rzone is not None:
                best = -1
                for c in cards:
                    cid = c.get("id") or ""
                    if (
                        c.get("data-action") in ("add", "move")
                        and zone_of.get(cid) == rzone
                        and best < pos_of(cid) < resolved_pos
                    ):
                        best = pos_of(cid)
                        reselected = cid

        for card in cards:
            if card.get("data-anchor-after") == resolved.id:
                if decision == "accepted":
                    new_after = (
                        resolved.target
                        if resolved.action == "move"
                        else self._payload_root_id(resolved.payload_html or "")
                    )
                    card.set("data-anchor-after", new_after or "")
                elif reselected is not None:
                    card.set("data-anchor-after", reselected)
                else:  # no remaining tail: rebind to the resolved card's
                    # anchor, shell included — a dissolved thead row must
                    # stay a thead row when its chain is bypassed
                    if resolved.anchor_after is None:
                        card.remove_attr("data-anchor-after")
                    else:
                        card.set("data-anchor-after", resolved.anchor_after)
                    if resolved.anchor_shell is None:
                        card.remove_attr("data-anchor-shell")
                    else:
                        card.set("data-anchor-shell", resolved.anchor_shell)
        for card in rebound:
            card.set("data-anchor-after", landed)
        if vacated_ids:
            assert move_source is not None and resolved.target
            self._rebind_removed_anchor(resolved.target, move_source, only=vacated_ids)

    # -- verification & time travel ----------------------------------------------------------------
    def verify(self) -> list[str]:
        """Replay the history backwards over a copy; report chain problems."""
        return self._verify(check_snapshot=True)

    def _verify(self, *, check_snapshot: bool) -> list[str]:
        """``check_snapshot=False`` leaves a baseline snapshot's own
        consistency (it hashes to its doc_hash) to the caller — the linter
        reports that as H008, the reconstruction mismatch as H006."""
        problems: list[str] = []
        events = self._history_events()
        if any(not isinstance(e.data.get("seq"), int) for e in events):
            problems.append("history has an event with a missing or non-integer seq")
            return problems
        seqs = [e.seq for e in events]
        if seqs != sorted(seqs) or len(set(seqs)) != len(seqs):
            problems.append("history seq is not strictly ascending")
            return problems
        gaps = [(a, b) for a, b in zip(seqs, seqs[1:], strict=False) if b != a + 1]
        if gaps:
            problems.append(f"history has internal seq gaps: {gaps}")
            return problems
        baselines = [i for i, e in enumerate(events) if e.kind == "baseline"]
        if baselines and baselines != [0]:
            problems.append(
                "a baseline must be the first retained event and occur once "
                f"(found at seq {', '.join(str(events[i].seq) for i in baselines)})"
            )
            return problems
        clone = AimDocument(parse_html(canonical.document_text(self._fragment)))
        state = clone._state
        for ev in reversed(events):
            if ev.kind == "checkpoint":
                got = state.doc_hash()
                want = ev.get("doc_hash")
                if want != got:
                    problems.append(
                        f"checkpoint seq {ev.seq} ({ev.get('label')!r}): doc_hash "
                        f"mismatch — recorded {want}, reconstructed {got}"
                    )
                continue
            if ev.kind == "baseline":
                problems += self._baseline_problems(ev, state, check_snapshot=check_snapshot)
                continue
            if not ev.state_changing:
                continue
            try:
                self._invert_on(state, ev, problems)
            except (TargetNotFound, InvalidOperation, HistoryError) as exc:
                problems.append(f"seq {ev.seq}: replay failed — {exc}")
            except Exception as exc:  # a malformed payload/anchor is a chain
                # problem to report, never a verifier crash → S000 (AIM-05)
                problems.append(f"seq {ev.seq}: replay failed — {type(exc).__name__}: {exc}")
        return problems

    @staticmethod
    def _baseline_problems(ev: Event, state: DocState, *, check_snapshot: bool) -> list[str]:
        """The reconstruction at a baseline must equal its snapshot line for
        line — which names the construct that diverged — and its doc_hash;
        the snapshot must hash to the recorded doc_hash itself."""
        snap = ev.get("snapshot")
        shape = snapshot_problems(snap)
        if shape:
            return [f"baseline seq {ev.seq}: {shape[0]}"]
        assert isinstance(snap, dict)
        out: list[str] = []
        recorded = ev.get("doc_hash")
        if check_snapshot and snapshot_hash(snap) != recorded:
            out.append(
                f"baseline seq {ev.seq} ({ev.get('label')!r}): the snapshot does not hash "
                f"to the recorded doc_hash {recorded}"
            )
        reconstructed = state.snapshot()
        reconstructed["html"] = _recorded_html_line(snap["html"], state)
        want, got = _snapshot_lines(snap), _snapshot_lines(reconstructed)
        if want != got:
            index = next(
                (i for i, (a, b) in enumerate(zip(want, got, strict=False)) if a != b),
                min(len(want), len(got)),
            )
            body_at = index - (len(want) - len(snap.get("body", [])))
            where = (
                f"body construct {body_at + 1}"
                if body_at >= 0
                else "the <html>/settings/theme lines"
            )
            out.append(
                f"baseline seq {ev.seq} ({ev.get('label')!r}): the document does not match "
                f"its snapshot at {where} (external edit?)"
            )
        elif check_snapshot and snapshot_hash(reconstructed) != recorded:
            out.append(
                f"baseline seq {ev.seq} ({ev.get('label')!r}): doc_hash mismatch — recorded "
                f"{recorded}, reconstructed {snapshot_hash(reconstructed)}"
            )
        return out

    def _invert_on(self, state: DocState, ev: Event, problems: list[str]) -> None:
        action, target = ev.action, ev.target or ""
        applied = ev.applied_payload
        if target == VERSION_TARGET:
            # the declared version is a scalar on the <html> open tag, not a
            # construct the generic target machinery can address
            current = state.spec_version()
            if current != applied:
                problems.append(
                    f"seq {ev.seq}: version mismatch — recorded {applied!r}, found {current!r}"
                )
            before = ev.get("before")
            if before is None:
                raise HistoryError("version event carries no 'before' — not invertible")
            state.set_spec_version(before)
            return
        if action == "modify":
            current = state.serial(target)
            if current != applied:
                problems.append(
                    f"seq {ev.seq}: payload mismatch on {target!r} — the document "
                    f"does not match this event's result (external edit?)"
                )
            if ev.get("before") is not None:
                state.replace(target, ev.get("before"))
            elif target == "aim:theme":
                state.set_theme_markup(None)
            elif target == "aim:doc":
                state.set_doc_settings_markup(None)
        elif action == "add":
            current = state.serial(target)
            if current != applied:
                problems.append(f"seq {ev.seq}: add payload mismatch on {target!r}")
            state.remove(target)
        elif action == "delete":
            anchor = ev.get("anchor")
            if anchor is None:
                raise HistoryError("delete event carries no anchor — not invertible")
            state.insert(ev.get("before") or "", Anchor.from_obj(anchor))
        elif action == "move":
            frm = ev.get("from")
            if frm is None:
                raise HistoryError("move event carries no 'from' — not invertible")
            to = ev.get("to")
            if to is not None:
                # at this point the state IS the post-move state, so the
                # recorded destination must resolve — shell included (§6.4);
                # silently edited destination metadata is a chain problem,
                # not a clean replay
                state.resolve_insert_point(Anchor.from_obj(to))
            state.move(target, Anchor.from_obj(frm))

    def state_at(self, seq: int) -> AimDocument:
        """Reconstruct the document as of *seq* (pending lane + caches dropped)."""
        events = self._history_events()
        if events and events[0].kind == "baseline" and seq < events[0].seq:
            # nothing before a baseline is recorded in the file: the state
            # "before the import" is not the imported state, it is unknown
            raise HistoryError(
                f"cannot reconstruct below seq {events[0].seq}: the history begins with "
                f"a baseline ({events[0].get('label')!r}) there"
            )
        if events and seq < min(e.seq for e in events) - 1:
            raise HistoryError(
                f"cannot reconstruct below seq {min(e.seq for e in events) - 1} (history pruned)"
            )
        clone = AimDocument(parse_html(canonical.document_text(self._fragment)))
        state = clone._state
        for ev in reversed(events):
            if ev.seq <= seq:
                break
            if not ev.state_changing:
                continue
            problems: list[str] = []
            clone._invert_on(state, ev, problems)
            if problems:
                raise HistoryError("; ".join(problems))
        # drop pending lane, caches, and future history
        sec = state.section("aim-proposals")
        if sec is not None:
            state.body.children.remove(sec)
        for kind in ("embeddings",):
            s = state.script(kind)
            if s is not None:
                state.body.children.remove(s)
        meta = state.script("meta")
        if meta is not None:
            state.head.children.remove(meta)
        hist = state.script("history")
        if hist is not None and hist.raw:
            kept = [event.to_json() for event in events if event.seq <= seq]
            hist.raw = "\n" + "\n".join(kept) + "\n" if kept else "\n"
        return clone

    # -- lifecycle operations --------------------------------------------------------------------
    def flatten(
        self, *, drop_embeddings: bool = True, label: str = "flatten", at: str | None = None
    ) -> None:
        """Collapse the history to one checkpoint (and by default drop the
        embeddings) — a clean file (§6.8).

        A document with history keeps exactly one event: a checkpoint at the
        next seq, anchoring the current state's ``doc_hash``. The file is a
        pruned log, valid at any version: ``verify()`` still detects a later
        hand edit, and ``reconcile()`` refuses with the pruned-log message
        instead of replaying from a wrong origin; :meth:`baseline` accepts
        such a file as the new starting point. Seq never goes backwards. A
        document without history stays without it.

        Ids the dropped log had burned stay burned on this instance (§4.4:
        an id is never reused within a document lifetime), so an id seen
        before the flatten is never re-honored by a later write. The saved
        file carries no burn ledger — reloading it starts a fresh lifetime.
        """
        index = self._get_history_index()
        if drop_embeddings:
            emb = self._state.script("embeddings")
            if emb is not None:
                self._state.body.children.remove(emb)
        hist = self._state.script("history")
        if index.events and hist is not None:
            data = {
                "seq": self.seq + 1,
                "kind": "checkpoint",
                "t": at or _now_iso(),
                "label": label,
                "doc_hash": self.doc_hash,
            }
            hist.raw = "\n" + canonical_json(data) + "\n"
            index.replace_events([Event(deepcopy(data))], hist.raw)
        else:
            if hist is not None:
                self._state.body.children.remove(hist)
            index.replace_events([], None)
        # §9.3: gc is the final pass — a "clean file" must not ship the
        # dead blobs its dropped history kept alive
        self.gc_assets()

    def _drop_history(self, *, drop_embeddings: bool = True) -> None:
        """Remove the history block entirely (and the embeddings) — for
        exports that are pages, not documents (``to_html``). Burned ids stay
        burned on this instance."""
        index = self._get_history_index()
        for kind in ("history",) + (("embeddings",) if drop_embeddings else ()):
            el = self._state.script(kind)
            if el is not None:
                self._state.body.children.remove(el)
        index.replace_events([], None)
        self.gc_assets()

    def baseline(
        self,
        label: str,
        *,
        author: Actor | None = None,
        explanation: str | None = None,
        source: Sequence[str] | None = None,
        at: str | None = None,
    ) -> Event:
        """Make the current state the origin of the history (§6.9, since 0.6).

        Replaces the retained log with ONE ``baseline`` event at the next seq
        (1 for a document without history) whose ``snapshot`` writes out the
        current reduced projection. Time travel, verification and
        reconciliation keep working from the file alone, starting here;
        nothing before it can be undone or reconstructed. Pending proposals
        and caches stay. Importers record one instead of an ``add`` per
        construct, and it is the recovery for a file whose history cannot
        explain its body ("accept the file as it is").

        Recording a baseline raises the declared version to at least the one
        that defines it (no version event: no earlier state is retained to
        record it against, §3.7). Destructive to undo and provenance, so it
        is an SDK/CLI verb only — never an MCP tool.
        """
        if not label:
            raise InvalidOperation("a baseline needs a label")
        from .reconcile import _fixup_ids  # lazy: reconcile imports this module

        probe = self._clone()
        if _fixup_ids(probe, probe._state.all_ids()):
            # a snapshot with a missing, duplicated or invalid id would be an
            # origin no later reconcile can converge from
            raise InvalidOperation(
                "cannot record a baseline: the body has units without a usable id "
                "(missing, duplicated or invalid); reconcile() assigns them"
            )
        problems = self._unsnapshottable()
        if problems:
            raise InvalidOperation(
                f"cannot record a baseline: {problems[0]} — a baseline is never "
                "undone, so the error could never be fixed; correct the body first"
            )
        index = self._get_history_index()
        seq = self.seq + 1
        if not REGISTRY.version_includes(self.spec_version, REGISTRY.baseline_since):
            self._state.set_spec_version(REGISTRY.baseline_since)
        snap = self._state.snapshot()
        data: dict = {
            "seq": seq,
            "kind": "baseline",
            "t": at or _now_iso(),
            "label": label,
            "doc_hash": snapshot_hash(snap),
            "snapshot": snap,
        }
        if author is not None:
            data["author"] = author.to_obj()
        if explanation:
            data["explanation"] = explanation
        if source:
            data["source"] = list(source)
        hist = self._history_script()
        hist.raw = "\n" + canonical_json(data) + "\n"
        index.replace_events([Event(deepcopy(data))], hist.raw)
        self.gc_assets()  # §9.3: the dropped events may have kept blobs alive
        return Event(deepcopy(data))

    def _unsnapshottable(self) -> list[str]:
        """Why the current body cannot become a baseline snapshot (H008):
        each non-conforming construct, numbered as the snapshot would hold
        it. Empty when a baseline may be recorded."""
        from .lint import snapshot_entry_problems  # lazy: lint imports this module

        return [
            f"construct {i + 1}: {problem}"
            for i, line in enumerate(self._state.snapshot()["body"])
            for problem in snapshot_entry_problems(self, line)
        ]

    def prune(self, *, before: int | str) -> int:
        """Truncate history before a seq or checkpoint label; returns dropped count.

        Ids burned by the dropped prefix stay burned on this instance (§4.4)
        so they are never re-honored; the saved file's ledger shrinks to
        what the retained log records."""
        index = self._get_history_index()
        events = index.events
        if isinstance(before, str):
            match = next(
                (
                    e
                    for e in events
                    if e.kind in ("checkpoint", "baseline") and e.get("label") == before
                ),
                None,
            )
            if match is None:
                raise TargetNotFound(f"no checkpoint or baseline labeled {before!r}")
            cut = match.seq
        else:
            cut = before
        kept = [e for e in events if e.seq >= cut]
        if events and not kept:
            raise InvalidOperation(
                "prune would drop the entire log — seq/batch identities must "
                "stay anchored; use flatten() to drop history wholesale"
            )
        dropped = len(events) - len(kept)
        el = self._state.script("history")
        if el is not None:
            el.raw = "\n" + "\n".join(e.to_json() for e in kept) + "\n" if kept else "\n"
        index.replace_events(kept, el.raw if el is not None else None)
        self.gc_assets()  # §9.3: gc is the final pass of prune
        return dropped

    def reconcile(
        self, *, author: Actor | None = None, at: str | None = None, dry_run: bool = False
    ) -> ReconcileReport:
        """Detect out-of-band edits and repair the history (spec §6.8).

        Compares the body against the state the full log reconstructs and
        appends the difference as ``direct_edit`` events with
        ``origin: "reconcile"`` (*author* defaults to ``{type: external}``),
        declaring the current body truth going forward. Unmarked or
        conflicting ids are fixed up first (ids are tooling's job), and
        pending proposals that no longer apply in creation order are rejected.
        Also the adoption path for hand-written files with no history at all.

        With ``dry_run=True`` nothing is mutated — the returned
        :class:`ReconcileReport` describes what *would* be done. Raises
        :class:`HistoryError` when the log itself is damaged or pruned
        (reconcile repairs bodies, not histories). See
        :mod:`aimformat.reconcile` for the full contract.
        """
        from .reconcile import reconcile_document

        return reconcile_document(self, author=author, at=at, dry_run=dry_run)

    def import_revision(
        self,
        source: str | Path | bytes | BinaryIO,
        *,
        author: Actor | None = None,
        changes: Literal["proposals", "edits"] = "proposals",
        conflicts: Literal["report", "propose"] = "report",
        base_seq: int | None = None,
        at: str | None = None,
        dry_run: bool = False,
    ) -> RevisionImportReport:
        """Import a DOCX that came back from a colleague as a revision of
        THIS document (needs the ``docx`` extra).

        Export with ``to_docx(doc, path, roundtrip_marks=True)``; the hidden
        bookmarks and manifest it writes let the returned file's paragraphs be
        matched to this document's chunk ids (by marker, then by content), so
        only what the colleague changed is written — as one batch of pending
        proposals by the colleague (``changes="proposals"``, default) or as
        ``direct_edit`` events with ``origin: "reconcile"``
        (``changes="edits"``). Conversion noise (formatting DOCX could not
        carry) is never reported as a change, and text edits are replayed onto
        this document's own markup.

        *author* defaults to ``human("docx:<lastModifiedBy>")`` — an
        unverified name read from the file — or ``external("docx-import")``.
        Colleague changes to units that also changed here since the export, or
        that carry a pending proposal the colleague did not see as applied,
        are reported in ``report.conflicts`` and not written
        (``conflicts="propose"`` proposes them anyway, flagged; proposals
        mode only). Re-importing the same file writes nothing new while its
        proposals are pending or after they were accepted (a change that was
        rejected is proposed again); edits mode refuses a repeat.

        Refuses (``InvalidOperation``) when the history does not verify or the
        file is clearly not a revision of this document. With
        ``dry_run=True`` nothing is mutated. Returns a
        :class:`~aimformat.revision_import.RevisionImportReport`.
        """
        from .revision_import import import_revision

        return import_revision(
            self,
            source,
            author=author,
            changes=changes,
            conflicts=conflicts,
            base_seq=base_seq,
            at=at,
            dry_run=dry_run,
        )

    # -- caches: summary / toc / embeddings ------------------------------------------------------
    def set_summary(self, text: str, *, model: str) -> None:
        meta = self.meta or {}
        meta["summary"] = {
            "text": text,
            "model": model,
            "as_of_seq": self.seq,
            "doc_hash": self.doc_hash,
        }
        self._write_meta(meta)

    def generate_toc(self) -> list[dict]:
        """Derive the TOC cache from heading chunks (deterministic) and store
        it with ``toc_doc_hash``, the ``doc_hash`` it was derived from (§8.1).
        Once present, :meth:`dumps` keeps it fresh."""
        toc = self.outline()
        meta = self.meta or {}
        meta["toc"] = toc
        meta["toc_doc_hash"] = self.doc_hash
        self._write_meta(meta)
        return toc

    def outline(self) -> list[dict]:
        """The TOC derived live from the body (headings and slides), without
        touching the cache — what :meth:`generate_toc` stores."""
        toc: list[dict] = []
        current: dict | None = None
        for top in self._state.constructs():
            cid = top.chunk_id or top.container_id
            if top.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                current = {"title": top.text(), "level": int(top.tag[1]), "chunks": [cid]}
                toc.append(current)
            elif top.tag == "aim-slide":
                heading = top.find(lambda e: e.tag in ("h1", "h2", "h3", "h4", "h5", "h6"))
                entry = {"title": heading.text() if heading else "", "level": 1, "chunks": [cid]}
                entry["chunks"] += [
                    e.chunk_id or e.container_id
                    for e in top.elements()
                    if (e.chunk_id or e.container_id)
                ]
                toc.append(entry)
                current = None
            elif current is not None:
                current["chunks"].append(cid)
            else:
                current = {"title": "", "level": 1, "chunks": [cid]}
                toc.append(current)
        return toc

    def _has_outline(self) -> bool:
        """Whether the body has anything to outline (a heading or a slide).
        Without one, :meth:`outline` is a single untitled entry listing every
        id — it costs tokens and outlines nothing."""
        return any(
            top.tag in ("h1", "h2", "h3", "h4", "h5", "h6", "aim-slide")
            for top in self._state.constructs()
        )

    def toc_is_fresh(self) -> bool:
        """Whether the stored TOC carries the current ``doc_hash`` (a TOC
        without ``toc_doc_hash`` — written before v0.6 — cannot be judged
        and counts as not fresh)."""
        try:
            meta = self.meta
        except ParseError:
            return False
        return (
            meta is not None
            and isinstance(meta.get("toc"), list)
            and meta.get("toc_doc_hash") == self.doc_hash
        )

    def _refresh_toc(self) -> None:
        """dumps(): a present TOC is a machine-managed cache like aim.css —
        rederived deterministically, outside doc_hash, never evented."""
        try:
            meta = self.meta
        except ParseError:
            return  # a malformed cache is lint's to report (M003), not ours
        if meta is None or not isinstance(meta.get("toc"), list):
            return
        if meta.get("toc_doc_hash") == self.doc_hash:
            return
        meta["toc"] = self.outline()
        meta["toc_doc_hash"] = self.doc_hash
        self._write_meta(meta)

    def _write_meta(self, meta: dict) -> None:
        el = self._state.script("meta")
        if el is None:
            el = Element("script", [("type", REGISTRY.script_types["meta"])])
            title = self._state.head.find(lambda e: e.tag == "title")
            idx = (
                self._state.head.children.index(title) + 1
                if title is not None
                else len(self._state.head.children)
            )
            self._state.head.children.insert(idx, el)
        el.raw = "\n" + canonical_json(meta) + "\n"

    def set_embedding(
        self, chunk_id: str, *, model: str, vec: Sequence[float], **extra: object
    ) -> None:
        payload = self._state.serial(chunk_id)
        if payload is None:
            raise TargetNotFound(f"no chunk {chunk_id!r}")
        line = {
            "chunk": chunk_id,
            "model": model,
            "text_hash": canonical.sha256_prefixed(payload),
            "vec": list(vec),
            **extra,
        }
        el = self._state.script("embeddings")
        if el is None:
            el = Element("script", [("type", REGISTRY.script_types["embeddings"])])
            el.raw = "\n"
            self._state.body.children.append(el)
        lines = [ln for ln in (el.raw or "").split("\n") if ln.strip()]
        lines = [ln for ln in lines if not (self._emb_key(ln) == (chunk_id, model))]
        lines.append(canonical_json(line))
        el.raw = "\n" + "\n".join(lines) + "\n"

    @staticmethod
    def _emb_key(line: str) -> tuple[str, str]:
        import json

        try:
            obj = json.loads(line)
            return obj.get("chunk", ""), obj.get("model", "")
        except Exception:
            return "", ""

    @property
    def embeddings(self) -> list[dict]:
        """Parsed embedding lines. Raises :class:`ParseError` on lines that
        are not JSON objects."""
        import json

        el = self._state.script("embeddings")
        if el is None or not el.raw:
            return []
        out = []
        for line in el.raw.split("\n"):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ParseError(f"embeddings line is not valid JSON: {exc}") from exc
            if not isinstance(obj, dict):
                raise ParseError(f"embeddings line is not a JSON object: {line[:60]!r}")
            out.append(obj)
        return out

    def stale_embeddings(self) -> list[dict]:
        out = []
        for emb in self.embeddings:
            payload = self._state.serial(emb.get("chunk", ""))
            if payload is None or canonical.sha256_prefixed(payload) != emb.get("text_hash"):
                out.append(emb)
        return out

    # -- assets ----------------------------------------------------------------------------------
    def pack_assets(self, *, author: Actor, at: str | None = None) -> int:
        """Hoist ``data:image`` payloads into the asset registry (spec §9).

        Each affected chunk is rewritten through an ordinary modify event —
        packing is a content edit like any other. Returns assets packed.
        """
        with self.batch():  # one packing run = one editing intention
            packed = self._pack_assets_inner(author, at)
            self.gc_assets()  # §9.3: gc is the final pass of pack
            return packed

    def _pack_assets_inner(self, author: Actor, at: str | None) -> int:
        packed = 0
        for chunk in self.chunks:
            parent, members = self._state.find_chunk(chunk.id)
            imgs = [
                el
                for m in members
                for el in m.iter()
                if el.tag == "img" and (el.get("src") or "").startswith("data:image/")
            ]
            if not imgs:
                continue
            # every image of the chunk must decode BEFORE the first swap —
            # a mid-chunk failure would otherwise leave mutations with no
            # event to account for them
            for img in imgs:
                self._decode_asset_datauri(img.get("src") or "")
            before = serialize_run(members)
            for img in imgs:
                blob = self._decode_asset_datauri(img.get("src") or "")
                asset_id = self._register_asset_datauri(img.get("src") or "", img.get("alt") or "")
                svg = Element("svg", [("role", "img"), ("aria-label", img.get("alt") or "")])
                w, h = self._image_dimensions(blob) or (100, 100)
                svg.set("viewBox", f"0 0 {w} {h}")  # the intrinsic aspect ratio
                styled = self._styled_props(img.get("style") or "")
                one_axis = ("width" in styled) != ("height" in styled)
                # unstyled display size = the img's intrinsic size, not the
                # svg default; with exactly ONE styled axis the other must
                # stay auto (the viewBox ratio scales it, as it did on the
                # img) — pinning it to the intrinsic value would distort
                if not one_axis or "width" in styled:
                    svg.set("width", str(w))
                if not one_axis or "height" in styled:
                    svg.set("height", str(h))
                for attr in ("class", "style", "dir", "lang", "title"):
                    if img.get(attr):  # packing is a storage-form change:
                        svg.set(attr, img.get(attr))  # presentation survives
                use = Element("use", [("href", f"#{asset_id}")], self_closing=True)
                svg.children.append(use)
                for m in members:
                    self._swap_node(m, img, svg)
            after = serialize_run(members)
            data = {
                "seq": self.seq + 1,
                "kind": "direct_edit",
                "t": at or _now_iso(),
                "target": chunk.id,
                "action": "modify",
                "before": before,
                "after": after,
                "author": author.to_obj(),
                "batch": self._batch_id(),
                "explanation": "aim pack: hoist embedded images into the asset registry",
            }
            self._append_event(data)
            packed += len(imgs)
        return packed

    @staticmethod
    def _styled_props(style: str) -> set[str]:
        """Property names declared in an inline style attribute."""
        return {p.split(":", 1)[0].strip() for p in style.split(";") if ":" in p}

    @staticmethod
    def _image_dimensions(blob: bytes) -> tuple[int, int] | None:
        """Intrinsic (width, height) of a PNG/GIF/JPEG blob, stdlib-only."""
        if blob.startswith(b"\x89PNG\r\n\x1a\n") and len(blob) >= 24 and blob[12:16] == b"IHDR":
            w = int.from_bytes(blob[16:20], "big")
            h = int.from_bytes(blob[20:24], "big")
            return (w, h) if w and h else None
        if blob[:6] in (b"GIF87a", b"GIF89a") and len(blob) >= 10:
            w = int.from_bytes(blob[6:8], "little")
            h = int.from_bytes(blob[8:10], "little")
            return (w, h) if w and h else None
        if blob[:2] == b"\xff\xd8":  # JPEG: walk the markers to the first SOF
            i = 2
            while i + 9 < len(blob):
                if blob[i] != 0xFF:
                    i += 1
                    continue
                marker = blob[i + 1]
                if marker in (0xFF, 0x01) or 0xD0 <= marker <= 0xD9:
                    i += 2
                    continue
                length = int.from_bytes(blob[i + 2 : i + 4], "big")
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h = int.from_bytes(blob[i + 5 : i + 7], "big")
                    w = int.from_bytes(blob[i + 7 : i + 9], "big")
                    return (w, h) if w and h else None
                i += 2 + length
        return None

    @staticmethod
    def _decode_asset_datauri(data_uri: str) -> bytes:
        m = re.match(r"^data:(image/[a-z+.-]+);base64,(.*)$", data_uri, re.S)
        if not m:
            raise InvalidOperation("only base64 data:image/* payloads can be packed")
        try:
            return base64.b64decode(m.group(2), validate=True)
        except Exception as exc:
            raise InvalidOperation(f"undecodable image payload: {exc}") from exc

    def _swap_node(self, root: Element, old: Element, new: Element) -> bool:
        for el in root.iter():
            if old in el.children:
                el.children[el.children.index(old)] = new
                return True
        return False

    def _assets_section(self) -> Element:
        sec = self._state.section("aim-assets")
        if sec is None:
            sec = Element("aim-assets")
            svg = Element("svg", [("aria-hidden", "true"), ("height", "0"), ("width", "0")])
            sec.children.append(svg)
            insert_at = len(self._state.body.children)
            for i, child in enumerate(self._state.body.children):
                if isinstance(child, Element) and child.tag == "script":
                    insert_at = i
                    break
            self._state.body.children.insert(insert_at, sec)
        return sec

    def _register_asset_datauri(self, data_uri: str, label: str) -> str:
        blob = self._decode_asset_datauri(data_uri)
        asset_id = "asset-" + hashlib.sha256(blob).hexdigest()[:12]
        svg = self._assets_section().elements()[0]
        if any(s.get("id") == asset_id for s in svg.elements()):
            return asset_id
        # the symbol's grid is the image's intrinsic geometry: a hardcoded
        # square viewBox letterboxed every non-square image
        w, h = self._image_dimensions(blob) or (100, 100)
        symbol = Element("symbol", [("id", asset_id), ("viewBox", f"0 0 {w} {h}")])
        image = Element(
            "image",
            [("height", str(h)), ("width", str(w)), ("href", data_uri)],
            self_closing=True,
        )
        symbol.children.append(image)
        svg.children.append(symbol)
        return asset_id

    def gc_assets(self) -> int:
        """Remove asset symbols referenced neither by the body nor by any
        retained history payload. Returns the number collected."""
        sec = self._state.section("aim-assets")
        if sec is None:
            return 0
        live: set[str] = set()
        hay = [serialize(c) for c in self._state.constructs()]
        hay += [
            ev.get(k) or ""
            for ev in self._history_events()
            for k in ("before", "after", "proposed", "applied")
        ]
        hay += [p.payload_html or "" for p in self.proposals]
        text = "\n".join(hay)
        for m in re.finditer(r'href="#(asset-[0-9a-f]{12})"', text):
            live.add(m.group(1))
        svg = sec.elements()[0] if sec.elements() else None
        if svg is None:
            return 0
        dead = [s for s in svg.elements() if s.get("id") not in live]
        for s in dead:
            svg.children.remove(s)
        if not svg.elements():
            self._state.body.children.remove(sec)
        return len(dead)


# ===========================================================================
def new_document(
    *, title: str, lang: str = "en", theme: dict[str, str] | None = None
) -> AimDocument:
    """A minimal valid, empty .aim document."""
    frag = Fragment()
    frag.doctype = "doctype html"
    html = Element("html", [("data-aim-version", REGISTRY.spec_version), ("lang", lang)])
    head = Element("head")
    head.children.append(Element("meta", [("charset", "utf-8")]))
    head.children.append(Comment(render_note()))
    title_el = Element("title")
    title_el.children.append(Text(title))
    head.children.append(title_el)
    css = Element("style", [("data-aim-css", REGISTRY.spec_version)])
    css.raw = "\n" + generate_aim_css()
    head.children.append(css)
    if theme:
        # names AND values, like every other write path — the constructor
        # must not emit a document its own linter rejects (V012) (AIM-07)
        AimDocument._check_theme_slots(theme)
        body_css = "; ".join(f"{k}:{v}" for k, v in sorted(theme.items()))
        theme_el = Element("style", [("data-aim-theme", None)])
        theme_el.raw = f":root{{{body_css}}}"
        head.children.append(theme_el)
    body = Element("body")
    hist = Element("script", [("type", REGISTRY.script_types["history"])])
    hist.raw = "\n"
    body.children.append(hist)
    html.children.append(head)
    html.children.append(body)
    frag.children.append(html)
    return AimDocument(frag)


def load(path: str | Path) -> AimDocument:
    return AimDocument.load(path)


def loads(text: str) -> AimDocument:
    return AimDocument.loads(text)
