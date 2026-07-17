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
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import canonical, ids
from .canonical import canonical_json, serialize, serialize_run
from .css import generate_aim_css
from .dom import Comment, Element, Fragment, Text, parse_fragment, parse_html
from .errors import HistoryError, InvalidOperation, ParseError, TargetNotFound
from .events import Actor, Event
from .note import find_note, is_canonical, render_note
from .pagesetup import (
    PageSetup,
    doc_settings_element,
    page_setup_from_obj,
    page_setup_from_settings,
    parse_doc_settings,
)
from .registry import REGISTRY

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from .reconcile import ReconcileReport

__all__ = ["AimDocument", "Chunk", "Proposal", "Anchor", "LAST", "load", "loads", "new_document"]


class _Last:
    def __repr__(self) -> str:  # pragma: no cover - repr only
        return "LAST"


#: Sentinel: insert at the end of the target container (the default).
LAST = _Last()

AnchorAfter = str | None | _Last
_BODY_SECTIONS = ("aim-proposals", "aim-assets", "script")
#: Reserved singleton targets (spec §3.5/§3.6): they can be modified but
#: never deleted or moved — they have no body anchor to restore them at.
_RESERVED_TARGETS = ("aim:theme", "aim:doc")


def _no_delete_move(target: str, action: str) -> None:
    if target in _RESERVED_TARGETS:
        raise InvalidOperation(
            f"{target} is a reserved singleton and cannot be the target of "
            f"a {action} — modify it instead"
        )


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


def resolution_order(proposals: Sequence[Proposal]) -> list[Proposal]:
    """A dependency-safe order for resolving a whole pending lane.

    Card order in the file carries no dependency meaning (a manual reorder
    is legal), so resolve in rounds: an add anchored on another pending add
    waits for its anchor; within each round adds/modifies go first, then
    moves, then deletes — an add anchored on a chunk that a sibling card
    moves away or deletes lands while the anchor is still in place, and a
    move whose destination anchors on a to-be-deleted chunk resolves before
    the delete. Shared by ``aim accept/reject --all`` and the exporters'
    resolve-a-copy paths.
    """
    rank = {"move": 1, "delete": 2}
    pending = list(proposals)
    order: list[Proposal] = []
    while pending:
        pending_ids = {p.id for p in pending}
        ready = [p for p in pending if not (p.action == "add" and p.anchor_after in pending_ids)]
        if not ready:
            raise InvalidOperation(
                "pending adds anchor on each other in a cycle — the file is "
                "corrupt (aim lint reports P015)"
            )
        ready.sort(key=lambda p: rank.get(p.action, 0))
        order.extend(ready)
        done = {p.id for p in ready}
        pending = [p for p in pending if p.id not in done]
    return order


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
        # burned ids whose burn record left the retained log (prune/flatten)
        # — kept for this instance's lifetime so they are never re-honored
        self._burned: set[str] = set()

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
        machine-managed and excluded from hashing.)"""
        css = self._state.css_el()
        if css is not None:
            css.raw = "\n" + generate_aim_css()
            css.set("data-aim-css", REGISTRY.spec_version)
        return canonical.document_text(self._fragment)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.dumps(), "utf-8")

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
        events = self.history
        return events[-1].seq if events else 0

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
        out: list[Chunk] = []
        seen: set[str] = set()

        def emit(cid: str) -> None:
            if cid in seen:
                return
            seen.add(cid)
            parent, members = self._state.find_chunk(cid)
            out.append(
                Chunk(
                    id=cid,
                    container=self._state.container_of_chunk(cid),
                    tags=tuple(m.tag for m in members),
                    html=serialize_run(members),
                    text="".join(m.text() for m in members),
                )
            )

        for top in self._state.constructs():
            for el in top.iter():
                if el.chunk_id:
                    emit(el.chunk_id)
        return out

    def chunk(self, cid: str) -> Chunk:
        for c in self.chunks:
            if c.id == cid:
                return c
        raise TargetNotFound(f"no chunk {cid!r}")

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
    @property
    def history(self) -> list[Event]:
        el = self._state.script("history")
        if el is None or not el.raw:
            return []
        return [Event.from_json(line) for line in el.raw.split("\n") if line.strip()]

    def _append_event(self, data: dict) -> Event:
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
        body = (el.raw or "").rstrip("\n")
        line = canonical_json(data)
        el.raw = "\n" + (body + "\n" if body else "") + line + "\n"
        return Event(data)

    # -- batching -----------------------------------------------------------------
    def _next_batch(self) -> str:
        used = {e.batch for e in self.history if e.batch}
        for p in self.proposals:
            if p.batch:
                used.add(p.batch)
        n = 1
        while f"b{n}" in used:
            n += 1
        return f"b{n}"

    @contextlib.contextmanager
    def batch(self):
        """Group the edits made inside the ``with`` into one batch id."""
        if self._batch is not None:
            yield self._batch  # nested: reuse the open batch
            return
        self._batch = self._next_batch()
        try:
            yield self._batch
        finally:
            self._batch = None

    def _batch_id(self) -> str:
        return self._batch or self._next_batch()

    # -- payload plumbing ------------------------------------------------------------
    _PAYLOAD_ID_RE = re.compile(r'data-aim(?:-container)?="([^"]+)"')

    def _history_burned_ids(self) -> set[str]:
        """Every id the retained log burns: event targets, proposal ids,
        and ids that only ever existed inside recorded payloads (items of a
        deleted container, replaced-away members)."""
        taken: set[str] = set()
        for ev in self.history:  # ids are never reused, deleted ones stay burned
            for key in ("target", "proposal"):
                v = ev.get(key)
                if isinstance(v, str):
                    taken.add(v)
            for key in ("before", "after", "proposed", "applied"):
                v = ev.get(key)
                if isinstance(v, str):
                    taken.update(self._PAYLOAD_ID_RE.findall(v))
        return taken

    def _recorded_ids(self, *, skip_payload_of: str | None = None) -> set[str]:
        """Ids mentioned by history or the pending lane (live or burned),
        plus ids whose burn record was pruned/flattened away this session.

        ``skip_payload_of`` leaves one pending card's payload ids out: at
        resolution time they are the write's own reservations (minted when
        the card was created), not competing claims."""
        taken = self._history_burned_ids() | self._burned
        for p in self.proposals:
            taken.add(p.id)
            if p.payload_html and p.id != skip_payload_of:
                taken.update(self._PAYLOAD_ID_RE.findall(p.payload_html))
        return taken

    def _taken_ids(self, *, skip_payload_of: str | None = None) -> set[str]:
        return self._state.all_ids() | self._recorded_ids(skip_payload_of=skip_payload_of)

    def _guard_replacement_kind(self, target: str, first: Element, kind: str | None = None) -> None:
        """A replacement keeps the target's kind (§4.3): an ``aim-slide``
        root can only ever be a container, and a container target can only
        take a container-capable root — otherwise the write demotes one into
        the other and the document fails S030/S031 on the very next lint."""
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
                if first.get(wrong) is not None:
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
        self, container: str, after: AnchorAfter, *, exclude: str | None = None
    ) -> Anchor:
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
        if anchor.after is None and container != "body":
            cont = self._state.container_node(container)
            if cont is not None and cont.tag == "table":
                shells = [s.tag for s in cont.elements() if s.tag in REGISTRY.table_shells]
                shell = "tbody" if "tbody" in shells else (shells[0] if shells else None)
                # data rows default into the body section, not the header
                anchor = Anchor(container, None, shell=shell)
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
            "batch": self._batch_id(),
        }
        if explanation:
            data["explanation"] = explanation
        self._append_event(data)
        try:
            return self.chunk(cid)
        except TargetNotFound:  # container payload: synthesize the view
            root = parse_fragment(payload)[0]
            assert isinstance(root, Element)
            return Chunk(
                id=cid, container=container, tags=(root.tag,), html=payload, text=root.text()
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
            raise InvalidOperation("modify with identical content")
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
            "batch": self._batch_id(),
        }
        if explanation:
            data["explanation"] = explanation
        self._append_event(data)
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
        self._state.remove(cid)
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
        explanation: str | None = None,
        at: str | None = None,
    ) -> None:
        _no_delete_move(cid, "move")
        if not self._state.exists(cid):
            raise TargetNotFound(f"no chunk {cid!r}")
        src = self._anchor_of(cid)
        dst = self._resolve_end_anchor(container, after, exclude=cid)
        if (src.container, src.after) == (dst.container, dst.after):
            raise InvalidOperation(f"move of {cid!r} is a no-op (already at that position)")
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
        return (
            f'<script type="{REGISTRY.script_types["doc"]}">\n{canonical_json(settings)}\n</script>'
        )

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
    ) -> Proposal:
        """Propose a page setup (pending aim:doc modify, like a theme swap)."""
        markup = self._doc_settings_markup(page)
        pid = self._new_proposal_id()
        with self.batch():
            self._supersede_if_pending("aim:doc", pid, author, at)
            return self._new_card(
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

    def undo(self, *, author: Actor, at: str | None = None) -> Event:
        """Append the inverse of the most recent not-yet-undone edit."""
        target_ev = self._undo_candidate()
        if target_ev is None:
            raise InvalidOperation("nothing to undo")
        inverse = self._inverse_data(target_ev)
        inverse.update(
            {
                "seq": self.seq + 1,
                "kind": "direct_edit",
                "t": at or _now_iso(),
                "origin": "undo",
                "author": author.to_obj(),
                "batch": self._batch_id(),
            }
        )
        self._apply_data(inverse)
        return self._append_event(inverse)

    def redo(self, *, author: Actor, at: str | None = None) -> Event:
        """Re-apply the most recent not-yet-redone undo.

        Walking back through the trailing undo/redo zone, each redo cancels
        the nearest earlier undo (stack semantics); the first uncancelled
        undo is the redo target. Any original edit ends the zone.
        """
        redos_pending = 0
        candidate: Event | None = None
        for ev in reversed(self.history):
            if not ev.state_changing:
                continue
            if ev.origin == "redo":
                redos_pending += 1
            elif ev.origin == "undo":
                if redos_pending > 0:
                    redos_pending -= 1
                else:
                    candidate = ev
                    break
            else:
                break
        if candidate is None:
            raise InvalidOperation("nothing to redo")
        redo_data = self._inverse_data(candidate)
        redo_data.update(
            {
                "seq": self.seq + 1,
                "kind": "direct_edit",
                "t": at or _now_iso(),
                "origin": "redo",
                "author": author.to_obj(),
                "batch": self._batch_id(),
            }
        )
        self._apply_data(redo_data)
        return self._append_event(redo_data)

    def _undo_candidate(self) -> Event | None:
        """The most recent edit that is not currently undone.

        Walk the trailing undo/redo zone backwards. Each undo cancels one
        earlier event (an original edit, or a redo's re-application); each
        redo cancels one earlier undo — so `pending` may dip negative while
        a redo waits for the undo it cancelled.
        """
        pending_undos = 0
        for ev in reversed(self.history):
            if not ev.state_changing:
                continue
            if ev.origin == "undo":
                pending_undos += 1
            elif ev.origin == "redo":
                pending_undos -= 1
            elif pending_undos > 0:
                pending_undos -= 1  # this edit is already undone; skip it
            else:
                return ev
        return None

    def _inverse_data(self, ev: Event) -> dict:
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
                "anchor": ev.get("anchor"),
            }
        if action == "delete":
            return {
                "target": target,
                "action": "add",
                "after": ev.get("before"),
                "anchor": ev.get("anchor"),
            }
        if action == "move":
            return {"target": target, "action": "move", "from": ev.get("to"), "to": ev.get("from")}
        raise HistoryError(f"cannot invert action {action!r}")

    def _apply_data(self, data: dict) -> None:
        action, target = data["action"], data["target"]
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
            self._state.move(target, Anchor.from_obj(data["to"]))

    # -- proposals (the pending lane) --------------------------------------------------------
    @property
    def proposals(self) -> list[Proposal]:
        sec = self._state.section("aim-proposals")
        if sec is None:
            return []
        out = []
        for card in sec.elements():
            if card.tag != "aim-proposal":
                continue
            tmpl = next((c for c in card.elements() if c.tag == "template"), None)
            payload = None
            if tmpl is not None and tmpl.elements():
                payload = "".join(serialize(e) for e in tmpl.elements())
            author = Actor(
                card.get("data-author") or "human",
                id=card.get("data-author-id"),
                model=card.get("data-author-model"),
            )
            out.append(
                Proposal(
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
            )
        return out

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
        taken = self._taken_ids() | {p.id for p in self.proposals}
        return ids.new_proposal_id(taken)

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
        attrs.append(("data-batch", self._batch_id()))
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
        return self.proposal(pid)

    def _supersede_if_pending(
        self, target: str, new_pid: str, author: Actor, at: str | None
    ) -> None:
        for p in self.proposals:
            if p.target == target and p.action in ("modify", "delete"):
                self._resolve(
                    p, decision="superseded", decided_by=author, superseded_by=new_pid, at=at
                )

    def propose_modify(
        self,
        target: str,
        markup: str,
        *,
        author: Actor,
        explanation: str | None = None,
        depends_on: str | None = None,
        at: str | None = None,
    ) -> Proposal:
        if not self._state.exists(target):
            raise TargetNotFound(f"no chunk {target!r}")
        if target == "aim:theme":
            payload = self._validated_theme_markup(markup)
        elif target == "aim:doc":
            payload = self._validated_doc_markup(markup)
        else:
            _, payload = self._normalize_payload(markup, expect_id=target)
        pid = self._new_proposal_id()
        with self.batch():  # the supersede + the new card are one intention
            self._supersede_if_pending(target, pid, author, at)
            return self._new_card(
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

    def propose_theme(
        self,
        slots: dict[str, str],
        *,
        author: Actor,
        explanation: str | None = None,
        depends_on: str | None = None,
        at: str | None = None,
    ) -> Proposal:
        self._check_theme_slots(slots)
        body = "; ".join(f"{k}:{v}" for k, v in sorted(slots.items()))
        markup = f"<style data-aim-theme>:root{{{body}}}</style>"
        pid = self._new_proposal_id()
        with self.batch():
            self._supersede_if_pending("aim:theme", pid, author, at)
            return self._new_card(
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
    ) -> Proposal:
        _, payload = self._normalize_payload(markup)
        # container-membership check at creation time, so the card is not
        # doomed to fail at accept (same contract as the direct add)
        cont_el = self._state.container_node(container)
        if cont_el is not None:
            self._state._guard_item_members(
                cont_el, [n for n in parse_fragment(payload) if isinstance(n, Element)]
            )
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
            anchor = Anchor(container, after)
        else:
            anchor = self._resolve_end_anchor(container, after)
            # container-scoped validation, exactly like the direct add: the
            # anchor must be a legal insertion point in `container`, not merely
            # exist somewhere in the document (AIM-03)
            self._state.resolve_insert_point(anchor)
        return self._new_card(
            action="add",
            author=author,
            target=None,
            payload=payload,
            anchor=anchor,
            explanation=explanation,
            depends_on=depends_on,
            at=at,
        )

    def propose_delete(
        self,
        target: str,
        *,
        author: Actor,
        explanation: str | None = None,
        at: str | None = None,
    ) -> Proposal:
        # reject reserved targets at propose time: the card would lint clean
        # but explode at accept (reserved heads have no body anchor)
        _no_delete_move(target, "delete proposal")
        if not self._state.exists(target):
            raise TargetNotFound(f"no chunk {target!r}")
        pid = self._new_proposal_id()
        with self.batch():
            self._supersede_if_pending(target, pid, author, at)
            return self._new_card(
                action="delete",
                author=author,
                target=target,
                payload=None,
                anchor=None,
                explanation=explanation,
                depends_on=None,
                at=at,
                pid=pid,
            )

    def propose_move(
        self,
        target: str,
        *,
        author: Actor,
        container: str,
        after: AnchorAfter = LAST,
        explanation: str | None = None,
        at: str | None = None,
    ) -> Proposal:
        _no_delete_move(target, "move proposal")
        if not self._state.exists(target):
            raise TargetNotFound(f"no chunk {target!r}")
        src = self._anchor_of(target)
        anchor = self._resolve_end_anchor(container, after, exclude=target)
        # mirror the direct move: reject no-ops (AIM-08) and validate the
        # destination container-scoped so the proposal can actually be
        # accepted (AIM-03)
        if (src.container, src.after) == (anchor.container, anchor.after):
            raise InvalidOperation(f"move of {target!r} is a no-op (already at that position)")
        self._state.resolve_insert_point(anchor)
        cont_el = self._state.container_node(anchor.container)
        if cont_el is not None:
            self._state._guard_item_members(
                cont_el, [el for _, el in self._state._target_elements(target)]
            )
        return self._new_card(
            action="move",
            author=author,
            target=target,
            payload=None,
            anchor=anchor,
            explanation=explanation,
            depends_on=None,
            at=at,
        )

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
        its id, action, target, anchor, author, batch, and dependencies;
        re-anchoring or re-targeting is a reject + new propose, not an
        amend. Payloads are validated exactly like the original propose
        call (modify: against the live target; add: keeping the proposed
        root id and marker kind, so chained anchors stay stable).
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
                else:
                    # fail fast on a dangling proposal (target deleted out
                    # from under it) — mirroring propose_modify; otherwise
                    # the amend silently rewrites a card that can only
                    # explode later, at accept time (review finding)
                    if not self._state.exists(target):
                        raise TargetNotFound(f"no chunk {target!r}")
                    _, payload = self._normalize_payload(markup, expect_id=target)
            elif prop.action == "add":
                _, payload = self._payload_like(prop.payload_html or "", markup)
            else:
                raise InvalidOperation(f"a {prop.action} proposal carries no payload to amend")
            tmpl = next((c for c in card.elements() if c.tag == "template"), None)
            if tmpl is None:  # defensive: modify/add cards always carry one
                tmpl = Element("template")
                card.children.append(tmpl)
            tmpl.children = list(parse_fragment(payload))
        if explanation is not None:
            if explanation:
                card.set("data-explanation", explanation)
            else:
                card.remove_attr("data-explanation")
        if at is not None:
            card.set("data-at", at)
        return self.proposal(pid)

    # -- resolution ---------------------------------------------------------------------------
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
                        else self._payload_like(prop.payload_html or "", applied)
                    )
            else:
                applied_payload = prop.payload_html
        return self._resolve(
            prop,
            decision="accepted",
            decided_by=decided_by,
            applied=applied_payload,
            explanation=explanation,
            at=at,
        )

    def _payload_like(self, original: str, replacement: str) -> tuple[str, str]:
        """Canonicalize an add-tweak/amend payload, keeping the proposed
        chunk id and marker kind. The replacement must also keep the
        proposed root's KIND (§4.3): flipping container↔chunk would mint a
        card whose marker contradicts its tag (V003) or accept an
        ``aim-slide`` marked as a chunk (S031) — review finding."""
        orig_nodes = [n for n in parse_fragment(original) if isinstance(n, Element)]
        keep = orig_nodes[0].chunk_id or orig_nodes[0].container_id
        marker = "data-aim-container" if orig_nodes[0].container_id is not None else "data-aim"
        new_nodes = [n for n in parse_fragment(replacement) if isinstance(n, Element)]
        if new_nodes:  # empty payloads fail in _normalize_payload below
            self._guard_replacement_kind(
                keep or orig_nodes[0].tag,
                new_nodes[0],
                kind="container" if marker == "data-aim-container" else "chunk",
            )
        return self._normalize_payload(replacement, expect_id=keep, expect_marker=marker)

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
        """Validate + canonicalize a whole-settings-block payload to the
        exact form ``set_page_setup`` writes (resolved page, canonical
        JSON) — a raw-authored spelling would replay as a verify
        mismatch."""
        el = doc_settings_element(markup)
        settings = parse_doc_settings(el.raw)
        if "page" in settings:
            settings["page"] = page_setup_from_obj(settings["page"]).to_obj()
        return (
            f'<script type="{REGISTRY.script_types["doc"]}">\n{canonical_json(settings)}\n</script>'
        )

    def reject(
        self,
        pid: str,
        *,
        decided_by: Actor,
        explanation: str | None = None,
        at: str | None = None,
    ) -> Event:
        return self._resolve(
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
    ) -> Event:
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
            "batch": self._batch_id(),
        }
        if superseded_by:
            data["superseded_by"] = superseded_by
        if explanation:
            data["explanation"] = explanation
        elif prop.explanation:
            data["explanation"] = prop.explanation

        if prop.action == "add":
            anchor = Anchor(
                prop.anchor_container or "body", prop.anchor_after, shell=prop.anchor_shell
            )
            data["target"] = self._payload_root_id(prop.payload_html or "")
            data["proposed"] = prop.payload_html
            data["anchor"] = anchor.to_obj()
            if decision == "accepted":
                if anchor.after and ids.is_valid_proposal_id(anchor.after):
                    raise InvalidOperation(
                        f"anchor proposal {anchor.after!r} is still pending — resolve it first"
                    )
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
                data["anchor"] = self._anchor_of(prop.target or "").to_obj()
                self._state.remove(prop.target or "")
            elif prop.action == "move" and decision == "accepted":
                _no_delete_move(prop.target or "", "move proposal")
                src = self._anchor_of(prop.target or "")
                dst = Anchor(
                    prop.anchor_container or "body", prop.anchor_after, shell=prop.anchor_shell
                )
                data["anchor"] = dst.to_obj()
                data["from"] = src.to_obj()
                data["to"] = dst.to_obj()
                self._state.move(prop.target or "", dst)

        # drop the card; rebind chained adds that anchored on this proposal
        sec = self._state.section("aim-proposals")
        assert sec is not None
        sec.children.remove(card)
        if not sec.elements():
            self._state.body.children.remove(sec)
        self._rebind_chained(prop, decision)
        return self._append_event(data)

    def _payload_root_id(self, payload: str) -> str:
        nodes = [n for n in parse_fragment(payload) if isinstance(n, Element)]
        return (nodes[0].chunk_id or nodes[0].container_id or "") if nodes else ""

    def _rebind_chained(self, resolved: Proposal, decision: str) -> None:
        sec = self._state.section("aim-proposals")
        if sec is None:
            return
        for card in sec.elements():
            if card.get("data-anchor-after") == resolved.id:
                if decision == "accepted":
                    new_after = self._payload_root_id(resolved.payload_html or "")
                    card.set("data-anchor-after", new_after)
                else:  # rejected/superseded: rebind to the resolved card's anchor
                    if resolved.anchor_after is None:
                        card.remove_attr("data-anchor-after")
                    else:
                        card.set("data-anchor-after", resolved.anchor_after)

    # -- verification & time travel ----------------------------------------------------------------
    def verify(self) -> list[str]:
        """Replay the history backwards over a copy; report chain problems."""
        problems: list[str] = []
        events = self.history
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

    def _invert_on(self, state: DocState, ev: Event, problems: list[str]) -> None:
        action, target = ev.action, ev.target or ""
        applied = ev.applied_payload
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
            state.move(target, Anchor.from_obj(frm))

    def state_at(self, seq: int) -> AimDocument:
        """Reconstruct the document as of *seq* (pending lane + caches dropped)."""
        events = self.history
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
            kept = [
                line
                for line in hist.raw.split("\n")
                if line.strip() and Event.from_json(line).seq <= seq
            ]
            hist.raw = "\n" + "\n".join(kept) + "\n" if kept else "\n"
        return clone

    # -- lifecycle operations --------------------------------------------------------------------
    def flatten(self, *, drop_embeddings: bool = True) -> None:
        """Drop the history (and by default the embeddings) — a clean file.

        Ids the dropped log had burned stay burned on this instance (§4.4:
        an id is never reused within a document lifetime), so an id seen
        before the flatten is never re-honored by a later write. The saved
        file carries no burn ledger — reloading it starts a fresh lifetime.
        """
        self._burned |= self._history_burned_ids()
        for kind in ("history",) + (("embeddings",) if drop_embeddings else ()):
            s = self._state.script(kind)
            if s is not None:
                self._state.body.children.remove(s)

    def prune(self, *, before: int | str) -> int:
        """Truncate history before a seq or checkpoint label; returns dropped count.

        Ids burned by the dropped prefix stay burned on this instance (§4.4)
        so they are never re-honored; the saved file's ledger shrinks to
        what the retained log records."""
        self._burned |= self._history_burned_ids()
        events = self.history
        if isinstance(before, str):
            match = next(
                (e for e in events if e.kind == "checkpoint" and e.get("label") == before), None
            )
            if match is None:
                raise TargetNotFound(f"no checkpoint labeled {before!r}")
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
        pending proposals whose target vanished are rejected. Also the
        adoption path for hand-written files with no history at all.

        With ``dry_run=True`` nothing is mutated — the returned
        :class:`ReconcileReport` describes what *would* be done. Raises
        :class:`HistoryError` when the log itself is damaged or pruned
        (reconcile repairs bodies, not histories). See
        :mod:`aimformat.reconcile` for the full contract.
        """
        from .reconcile import reconcile_document

        return reconcile_document(self, author=author, at=at, dry_run=dry_run)

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
        """Derive the TOC cache from heading chunks (deterministic)."""
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
        meta = self.meta or {}
        meta["toc"] = toc
        self._write_meta(meta)
        return toc

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
            return self._pack_assets_inner(author, at)

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
                asset_id = self._register_asset_datauri(img.get("src") or "", img.get("alt") or "")
                svg = Element("svg", [("role", "img"), ("aria-label", img.get("alt") or "")])
                if img.get("style"):
                    svg.set("style", img.get("style"))
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
                "explanation": "aim pack --inline: hoist embedded images into the asset registry",
            }
            self._append_event(data)
            packed += len(imgs)
        return packed

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
        symbol = Element("symbol", [("id", asset_id), ("viewBox", "0 0 100 100")])
        image = Element(
            "image", [("height", "100"), ("width", "100"), ("href", data_uri)], self_closing=True
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
            for ev in self.history
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
