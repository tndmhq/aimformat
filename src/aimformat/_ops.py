"""Shared op executor for the agent write surfaces (MCP ``aim_edit`` /
``aim_propose`` and the CLI ``aim edit`` / ``aim propose`` verbs).

One code path for one op and for a batch: the single-op arguments run as a
one-element batch. A batch is all-or-nothing — ops apply in order to one
in-memory document inside ``doc.batch()`` (one history batch, or one
proposal batch), and the first op error aborts before anything is written.
Later ops can refer back to earlier ones with ``$N``.

Payloads may carry the elision stubs the read surface hands out
(``[elided: <size>, sha256:<16 hex>]``, see :func:`aimformat.views.elide`);
they are restored to the data URI they stand for, or the op is refused —
a stub written into a document destroys the asset it replaced.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .document import LAST, AimDocument, AnchorAfter
from .dom import Element, parse_fragment
from .errors import AimError
from .events import Actor
from .views import DATA_URI, STUB

Kind = Literal["edit", "propose"]

EDIT_ACTIONS = ("add", "modify", "replace_text", "delete", "move", "set_theme")
PROPOSE_ACTIONS = ("add", "modify", "replace_text", "delete", "move", "theme")
OP_FIELDS = frozenset(
    {
        "action",
        "target",
        "html",
        "old_text",
        "new_text",
        "container",
        "after",
        "theme_slots",
        "explanation",
    }
)
MAX_OPS: dict[Kind, int] = {"edit": 100, "propose": 25}
_TARGETED = ("modify", "replace_text", "delete", "move")
_PAYLOAD = ("add", "modify")
_BACKREF = re.compile(r"^\$(\d+)$")


class OpError(ValueError):
    """An op could not be applied; nothing was written. The message is the
    agent-facing text (``aim: …``)."""


class _Fail(Exception):
    """Internal: one op's failure, before the batch prefix is added."""


@dataclass
class _Done:
    action: str
    id: str  # what the op created (chunk id / proposal id) or its target
    target: str | None  # the document unit the op addresses (None: proposed add)
    container: str


@dataclass
class OpsResult:
    batch: str | None
    results: list[dict[str, Any]] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)


def ops_from_args(
    *,
    action: str | None,
    target: str | None,
    html: str | None,
    container: str | None,
    old_text: str | None = None,
    new_text: str | None = None,
    after: str | None,
    theme_slots: dict[str, str] | None,
    explanation: str | None,
    ops: list[dict[str, Any]] | None,
    kind: Kind,
) -> tuple[list[dict[str, Any]], bool]:
    """Normalize the two calling forms to ``(ops, single)``."""
    if action is not None and ops is not None:
        raise OpError("aim: pass either action (one op) or ops (a batch), not both")
    if ops is not None:
        stray = [
            name
            for name, value in (
                ("target", target),
                ("html", html),
                ("old_text", old_text),
                ("new_text", new_text),
                ("container", container),
                ("after", after),
                ("theme_slots", theme_slots),
            )
            if value is not None
        ]
        if stray:
            raise OpError(f"aim: with ops, put {', '.join(stray)} inside each op")
        if not isinstance(ops, list) or not ops:
            raise OpError("aim: ops must be a non-empty list")
        cap = MAX_OPS[kind]
        if len(ops) > cap:
            raise OpError(
                f"aim: at most {cap} ops per {'aim_' + kind} call (got {len(ops)}); split the batch"
            )
        return ops, False
    if action is None:
        raise OpError("aim: pass action (one op) or ops (a batch)")
    op: dict[str, Any] = {"action": action}
    for key, value in (
        ("target", target),
        ("html", html),
        ("old_text", old_text),
        ("new_text", new_text),
        ("container", container),
        ("after", after),
        ("theme_slots", theme_slots),
        ("explanation", explanation),
    ):
        if value is not None:
            op[key] = value
    return [op], True


def _anchor(after: str | None) -> AnchorAfter:
    if after is None or after == "":
        return LAST
    return None if after == "first" else after


def _root_id(markup: str) -> str | None:
    try:
        nodes = [n for n in parse_fragment(markup) if isinstance(n, Element)]
    except AimError:
        return None
    if not nodes:
        return None
    return nodes[0].chunk_id or nodes[0].container_id


class _Stubs:
    """Lazily built ``hash prefix -> data URI`` map over the document."""

    def __init__(self, doc: AimDocument):
        self.doc = doc
        self.map: dict[str, str] | None = None

    def _build(self) -> dict[str, str]:
        found: dict[str, str] = {}
        state = self.doc._state
        sources = [c.html for c in self.doc.chunks]
        sources += [p.payload_html or "" for p in self.doc.proposals]
        sources += [state.serial("aim:theme") or ""]
        # history payloads too: aim_read(include_history=True) elides them
        # the same way, and re-adding deleted content copies from there
        sources += [v for ev in self.doc.history for v in ev.data.values() if isinstance(v, str)]
        for text in sources:
            for m in DATA_URI.finditer(text):
                uri = m.group(0)
                digest = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:16]
                found.setdefault(digest, uri)
        return found

    def restore(self, html: str) -> str:
        if not STUB.search(html):
            return html
        if self.map is None:
            self.map = self._build()
        known = self.map

        def swap(m: re.Match[str]) -> str:
            uri = known.get(m.group(1))
            if uri is None:
                raise _Fail(
                    "payload contains an elided data URI that matches nothing in this "
                    "document or its history; copy the stub exactly from a fresh read"
                )
            return uri

        return STUB.sub(swap, html)


def _require(op: dict[str, Any], *, themed: str) -> None:
    action = op["action"]
    if action in _TARGETED and not op.get("target"):
        raise _Fail(f"{action} requires target (a chunk or container id)")
    if action in _PAYLOAD and op.get("html") is None:
        raise _Fail(f"{action} requires html (the payload markup)")
    if action == "replace_text":
        if op.get("html") is not None:
            raise _Fail("replace_text takes old_text and new_text, not html")
        if op.get("old_text") is None or op.get("new_text") is None:
            raise _Fail("replace_text requires old_text and new_text ('' deletes)")
    elif op.get("old_text") is not None or op.get("new_text") is not None:
        raise _Fail(f"old_text/new_text belong to replace_text, not {action}")
    if action == themed and not op.get("theme_slots"):
        raise _Fail(f"{action} requires theme_slots")


def apply_ops(
    doc: AimDocument,
    ops: list[dict[str, Any]],
    *,
    kind: Kind,
    author: Actor,
    explanation: str | None = None,
    single: bool = False,
) -> OpsResult:
    """Apply *ops* to *doc* in memory, all-or-nothing, in one batch.

    Raises :class:`OpError` naming the failing op; the caller must then
    discard *doc* (it may hold the earlier ops' changes) and write nothing.
    """
    actions = EDIT_ACTIONS if kind == "edit" else PROPOSE_ACTIONS
    themed = "set_theme" if kind == "edit" else "theme"
    stubs = _Stubs(doc)
    done: list[_Done] = []
    created: set[str] = set()  # ids created by earlier edit adds in this batch
    reminted: dict[str, int] = {}  # agent-chosen id that was taken -> op index
    cards: dict[tuple[str, str], int] = {}  # (target, lane) -> op index (proposals)
    before = {p.id for p in doc.proposals} if kind == "propose" else set()
    result = OpsResult(batch=None)

    def fail(i: int, op: dict[str, Any], msg: str) -> OpError:
        if single:
            return OpError(f"aim: {msg}")
        what = " ".join(str(x) for x in (op.get("action"), op.get("target")) if x)
        where = f"ops[{i}] ({what})" if what else f"ops[{i}]"
        return OpError(f"aim: {where}: {msg}; nothing was written")

    with doc.batch() as batch:
        result.batch = batch
        for i, raw in enumerate(ops):
            op: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
            try:
                if not isinstance(raw, dict):
                    raise _Fail("each op must be an object")
                unknown = sorted(set(op) - OP_FIELDS)
                if unknown:
                    raise _Fail(f"unknown op field(s) {', '.join(unknown)}")
                action = op.get("action")
                if action not in actions:
                    raise _Fail(
                        f"unknown edit action {action!r} (use {' | '.join(EDIT_ACTIONS)})"
                        if kind == "edit"
                        else f"unknown proposal action {action!r} "
                        f"(use {' | '.join(PROPOSE_ACTIONS)})"
                    )
                _require(op, themed=themed)
                container = (
                    _resolve(doc, op, "container", i, done, kind, "body", reminted) or "body"
                )
                target = _resolve(doc, op, "target", i, done, kind, container, reminted)
                after = _resolve(doc, op, "after", i, done, kind, container, reminted)
                if target is not None and target.startswith("p-"):
                    raise _Fail(f"a proposal id ({target}) cannot be a {action} target")
                if (
                    after is not None
                    and after.startswith("p-")
                    and not (kind == "propose" and action == "add")
                ):
                    raise _Fail(f"after={after}: only a proposed add can anchor on a pending add")
                html = op.get("html")
                if html is not None:
                    html = stubs.restore(html)
                chosen = _root_id(html) if action == "add" and html is not None else None
                if chosen is not None and chosen in created:
                    raise _Fail(f"id {chosen!r} was already created by an earlier op in this batch")
                why = op.get("explanation") or explanation
                if kind == "edit":
                    item = _edit(doc, action, target, html, container, after, op, author, why)
                else:
                    if target is not None or action == "theme":
                        lane = "move" if action == "move" else "md"
                        key = (target or "aim:theme", lane)
                        if key in cards:
                            raise _Fail(
                                f"ops[{cards[key]}] already proposes a "
                                f"{'move' if lane == 'move' else 'modify/delete'} card for "
                                f"{key[0]!r}; one modify-or-delete and one move per target"
                            )
                        cards[key] = i
                    item = _propose(doc, action, target, html, container, after, op, author, why)
                if action == "add":
                    new_id = item.id if kind == "edit" else _proposed_root(doc, item.id)
                    if new_id:
                        created.add(new_id)
                    if chosen is not None and new_id != chosen:
                        reminted[chosen] = i
            except _Fail as exc:
                raise fail(i, op, str(exc)) from None
            except AimError as exc:
                raise fail(i, op, str(exc)) from exc
            done.append(item)
            entry: dict[str, Any] = {"op": i, "id": item.id}
            if item.target is not None:
                entry["target"] = item.target
            result.results.append(entry)
    if kind == "propose":
        after_ids = {p.id for p in doc.proposals}
        result.superseded = sorted(before - after_ids)
    return result


def _proposed_root(doc: AimDocument, pid: str) -> str | None:
    p = doc.proposal(pid)
    return _root_id(p.payload_html or "")


def _resolve(
    doc: AimDocument,
    op: dict[str, Any],
    key: str,
    i: int,
    done: list[_Done],
    kind: Kind,
    container: str,
    reminted: dict[str, int],
) -> str | None:
    value = op.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise _Fail(f"{key} must be a string")
    if value in reminted:
        j = reminted[value]
        if doc._state.exists(value):
            # the literal id names live content too: either reading is
            # plausible, so neither is guessed
            raise _Fail(
                f"{key}={value!r} is ambiguous: it names existing content, and ops[{j}] "
                f"also chose it for new content (which got a fresh id); write ${j} for the "
                f"new content, or drop data-aim from ops[{j}]'s payload to mean the "
                "existing one"
            )
        raise _Fail(
            f"{key}={value!r}: ops[{j}] chose that id for its new content, but it was "
            f"taken and a fresh id was assigned; refer to it as ${j}"
        )
    if not value.startswith("$"):
        return value
    m = _BACKREF.match(value)
    if not m:
        raise _Fail(f"{key}={value!r} is not a back-reference ($N)")
    n = int(m.group(1))
    if n >= i:
        raise _Fail(f"{key}={value}: back-references must point at an earlier op (N < {i})")
    ref = done[n]
    if ref.action in ("set_theme", "theme"):
        raise _Fail(f"{key}={value}: ops[{n}] is a theme change; it has no id to refer to")
    if kind == "propose" and ref.action == "add":
        if key != "after" or op.get("action") != "add":
            raise _Fail(
                f"{key}={value}: ops[{n}] is a proposed add ({ref.id}); it can only be "
                "the after of a later add into the same container"
            )
        if ref.container != container:
            raise _Fail(
                f"{key}={value}: ops[{n}] proposes an add into {ref.container!r}, not "
                f"{container!r}; a pending add can only anchor adds in its own container"
            )
        return ref.id  # the p- id: chains onto the pending add (§5.2)
    return ref.target


def _edit(
    doc: AimDocument,
    action: str,
    target: str | None,
    html: str | None,
    container: str,
    after: str | None,
    op: dict[str, Any],
    author: Actor,
    why: str | None,
) -> _Done:
    if action == "add":
        assert html is not None  # _require
        chunk = doc.add_chunk(
            html, author=author, container=container, after=_anchor(after), explanation=why
        )
        return _Done(action, chunk.id, chunk.id, container)
    assert target is not None or action == "set_theme"
    if action == "modify":
        assert target is not None and html is not None
        doc.modify_chunk(target, html, author=author, explanation=why)
    elif action == "replace_text":
        assert target is not None
        doc.replace_text(target, op["old_text"], op["new_text"], author=author, explanation=why)
    elif action == "delete":
        assert target is not None
        doc.delete_chunk(target, author=author, explanation=why)
    elif action == "move":
        assert target is not None
        doc.move_chunk(
            target, author=author, container=container, after=_anchor(after), explanation=why
        )
    else:
        doc.set_theme(op.get("theme_slots") or {}, author=author, explanation=why)
        return _Done(action, "aim:theme", "aim:theme", container)
    assert target is not None
    return _Done(action, target, target, container)


def _propose(
    doc: AimDocument,
    action: str,
    target: str | None,
    html: str | None,
    container: str,
    after: str | None,
    op: dict[str, Any],
    author: Actor,
    why: str | None,
) -> _Done:
    if action == "modify":
        assert target is not None and html is not None
        p = doc.propose_modify(target, html, author=author, explanation=why)
    elif action == "replace_text":
        assert target is not None
        p = doc.propose_replace_text(
            target, op["old_text"], op["new_text"], author=author, explanation=why
        )
    elif action == "add":
        assert html is not None
        p = doc.propose_add(
            html, author=author, container=container, after=_anchor(after), explanation=why
        )
        return _Done(action, p.id, None, container)
    elif action == "delete":
        assert target is not None
        p = doc.propose_delete(target, author=author, explanation=why)
    elif action == "move":
        assert target is not None
        p = doc.propose_move(
            target, author=author, container=container, after=_anchor(after), explanation=why
        )
    else:
        p = doc.propose_theme(op.get("theme_slots") or {}, author=author, explanation=why)
        return _Done(action, p.id, "aim:theme", container)
    return _Done(action, p.id, target, container)
