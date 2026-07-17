"""Reconcile out-of-band edits (spec §6.8).

A document is *consistent* when replaying the history reproduces the body.
When someone edits the file without appending events — a hand edit in a text
editor, a lossy pipeline, partial corruption — the chain no longer verifies.
Reconcile repairs that by declaring the current body truth: it reconstructs
the **expected state E** (forward replay of the full log over an empty body),
takes the **actual body A** as found, and appends the edit script E → A as
ordinary ``direct_edit`` events with ``origin: "reconcile"`` and an
``external`` author. Verification then passes by construction: walking the
new events' inverses leads from A back to E, and the old chain explains E.

This is also the adoption path for hand-written files: with no history at
all, E is empty and every construct becomes an ``add`` event (ids assigned
where missing — ids are tooling's job, spec §4.4).

Scope and honesty:

- **The log must be intact and complete.** Reconcile repairs bodies, not
  histories: unparseable lines, seq gaps, unknown kinds, or events that do
  not replay raise :class:`HistoryError`. A *pruned* history also raises —
  the baseline below the prune floor is unrecoverable, so a repair guarantee
  would be a false promise.
- **Untracked state stays untracked.** A theme block that no event ever
  touched (e.g. set at document creation) has no recoverable baseline; it is
  left as-is rather than guessed at.
- **Some damage is detectable but not repairable** append-only: a tampered
  checkpoint ``doc_hash`` line, for instance. Whatever :meth:`verify` still
  reports after reconciliation is returned as :attr:`ReconcileReport.residual`.
- Reconcile makes the *history* consistent with the body; it does not make an
  invalid body valid — vocabulary or structure violations the hand edit
  introduced are declared as-is and remain the linter's to flag.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from . import ids
from .canonical import document_text, serialize
from .document import AimDocument, Anchor, DocState, _now_iso
from .dom import Element, parse_fragment, parse_html
from .errors import AimError, HistoryError
from .events import Actor, Event, external
from .registry import REGISTRY

__all__ = ["ReconcileReport", "reconcile_document"]


@dataclass
class ReconcileReport:
    """What reconcile detected and synthesized.

    ``events`` are the appended history events (direct edits plus rejection
    resolutions); ``assigned_ids`` records id fix-ups as ``(old, new)`` pairs
    (``old`` is ``None`` for elements that carried no marker at all);
    ``rejected_proposals`` lists pending proposals resolved because their
    target or anchor vanished; ``residual`` is whatever ``verify()`` still
    reports after reconciliation (damage that is detectable but not
    repairable append-only). On ``dry_run`` all of it is hypothetical.
    """

    events: list[Event] = field(default_factory=list)
    assigned_ids: list[tuple[str | None, str]] = field(default_factory=list)
    rejected_proposals: list[str] = field(default_factory=list)
    residual: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.events or self.assigned_ids or self.rejected_proposals)

    def summary(self) -> str:
        if not self.changed:
            return "no out-of-band changes detected"
        counts = Counter(e.action or e.kind for e in self.events if e.kind == "direct_edit")
        bits = ", ".join(f"{n} {a}" for a, n in sorted(counts.items()))
        out = f"reconciled {sum(counts.values())} out-of-band change(s): {bits}"
        if self.assigned_ids:
            out += f"; {len(self.assigned_ids)} id(s) assigned"
        if self.rejected_proposals:
            out += f"; {len(self.rejected_proposals)} pending proposal(s) rejected"
        return out


# ===========================================================================
# expected state: forward replay of the full log


def _check_log(events: list[Event]) -> None:
    if not events:
        return
    seqs = [e.data.get("seq") for e in events]
    if not all(isinstance(s, int) for s in seqs):
        raise HistoryError("cannot reconcile: history has a non-integer seq")
    if seqs != sorted(seqs) or len(set(seqs)) != len(seqs):
        raise HistoryError("cannot reconcile: history seq is not strictly ascending")
    if any(b != a + 1 for a, b in zip(seqs, seqs[1:], strict=False)):
        raise HistoryError("cannot reconcile: history has internal seq gaps")
    if seqs[0] != 1:
        raise HistoryError(
            f"cannot reconcile a pruned history (log starts at seq "
            f"{seqs[0]}): the baseline below the prune floor is "
            "unrecoverable — reconcile before pruning"
        )
    for ev in events:
        if ev.kind not in REGISTRY.event_fields:
            raise HistoryError(
                f"cannot reconcile: unknown event kind {ev.kind!r} at seq {ev.data.get('seq')}"
            )


def _apply_event(state: DocState, ev: Event) -> None:
    target, action = ev.target or "", ev.action
    payload = ev.applied_payload
    if target == "aim:theme":
        state.set_theme_markup(payload)  # None removes the block
        return
    if target == "aim:doc":
        state.set_doc_settings_markup(payload)  # None removes the block
        return
    if payload is None and action in ("modify", "add"):
        raise HistoryError(f"{action} event carries no payload")
    if action == "modify":
        state.replace(target, payload or "")
    elif action == "add":
        anchor = ev.get("anchor")
        if anchor is None:
            raise HistoryError("add event carries no anchor")
        state.insert(payload or "", Anchor.from_obj(anchor))
    elif action == "delete":
        state.remove(target)
    elif action == "move":
        to = ev.get("to")
        if to is None:
            raise HistoryError("move event carries no destination")
        state.move(target, Anchor.from_obj(to))
    else:
        raise HistoryError(f"unknown action {action!r}")


def _replay(S: AimDocument, events: list[Event]) -> None:
    for ev in events:
        if not ev.state_changing:
            continue
        try:
            _apply_event(S._state, ev)
        except (AimError, KeyError, TypeError) as exc:
            raise HistoryError(
                f"cannot reconcile: the log does not replay cleanly (seq "
                f"{ev.data.get('seq')}: {exc}) — reconcile repairs the body "
                "against an intact history, not the history itself"
            ) from exc


def _clone(doc: AimDocument) -> AimDocument:
    return doc.__class__(parse_html(document_text(doc._fragment)))


def _strip_body_state(S: AimDocument) -> None:
    state = S._state
    for el in state.constructs():
        state.body.children.remove(el)
    state.set_theme_markup(None)
    state.set_doc_settings_markup(None)


def _align_theme_baseline(S: AimDocument, events: list[Event], work: AimDocument) -> None:
    """A theme or settings block no event ever touched (constructor-set /
    imported) has no recoverable baseline: expected := actual, so it is
    left untracked."""
    if not any(e.state_changing and e.target == "aim:theme" for e in events):
        S._state.set_theme_markup(work._state.serial("aim:theme"))
    if not any(e.state_changing and e.target == "aim:doc" for e in events):
        S._state.set_doc_settings_markup(work._state.serial("aim:doc"))


# ===========================================================================
# the unit model: everything the format addresses by id


@dataclass
class _Unit:
    id: str
    scope: str  # "body" or the containing container's id
    shell: str | None  # thead/tbody/tfoot for table rows
    serial: str  # canonical serialization (runs concatenated)
    is_container: bool


def _units(state: DocState) -> dict[str, _Unit]:
    """Addressable units in document order: top constructs, container items
    (runs grouped), nested containers. Unmarked elements yield no unit."""
    out: dict[str, _Unit] = {}

    def descend(cont: Element, cid: str) -> None:
        if cont.tag == "table":
            bare = [c for c in cont.elements() if c.tag not in REGISTRY.table_shells]
            if bare:
                visit(bare, cid, None)
            for child in cont.elements():
                if child.tag in REGISTRY.table_shells:
                    visit(child.elements(), cid, child.tag)
        else:
            visit(cont.elements(), cid, None)

    def visit(members: list[Element], scope: str, shell: str | None) -> None:
        i = 0
        while i < len(members):
            el = members[i]
            cid = el.container_id
            if cid:
                out[cid] = _Unit(cid, scope, shell, serialize(el), True)
                descend(el, cid)
                i += 1
                continue
            kid = el.chunk_id
            group = [el]
            j = i + 1
            if kid:
                while j < len(members) and members[j].chunk_id == kid:
                    group.append(members[j])
                    j += 1
                out[kid] = _Unit(kid, scope, shell, "".join(serialize(m) for m in group), False)
            i = j

    visit(state.constructs(), "body", None)
    return out


def _skeleton(el: Element) -> str:
    """A container's serialization with its member elements dropped — what
    remains is the open tag (attrs), table shells, and any stray interior
    text. Equal skeletons mean item-level events can explain the diff."""
    from .dom import deep_copy

    clone = deep_copy(el)

    def scrub(node: Element, keep_shells: bool) -> None:
        kept = []
        for c in node.children:
            if isinstance(c, Element):
                if keep_shells and c.tag in REGISTRY.table_shells:
                    scrub(c, False)
                    kept.append(c)
                continue
            kept.append(c)
        node.children = kept

    scrub(clone, clone.tag == "table")
    return serialize(clone)


# ===========================================================================
# id fix-up: ids are tooling's job (spec §4.4)


def _fixup_ids(work: AimDocument, expected_alive: set[str]) -> list[tuple[str | None, str]]:
    """Give every unit in *work*'s body a usable id: assign fresh ids to
    unmarked constructs/items, to ids burned by history or the pending lane,
    to duplicates (first occurrence in document order keeps the id), and to
    invalid/reserved spellings. Mutates *work* only."""
    state = work._state
    recorded = work._recorded_ids()
    pool = work._taken_ids() | expected_alive  # fresh draws avoid all of it
    assigned: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    member_els: set[int] = set()

    def usable(val: str | None) -> bool:
        return (
            bool(val)
            and ids.is_valid_chunk_id(val)
            and val not in seen
            and (val in expected_alive or val not in recorded)
        )

    def assign(group: list[Element], marker: str, old: str | None) -> None:
        nid = ids.new_id(pool)
        for el in group:
            el.set(marker, nid)
        seen.add(nid)
        assigned.append((old or None, nid))

    def descend(cont: Element) -> None:
        if cont.tag == "table":
            bare = [c for c in cont.elements() if c.tag not in REGISTRY.table_shells]
            if bare:
                visit(bare)
            for child in cont.elements():
                if child.tag in REGISTRY.table_shells:
                    visit(child.elements())
        else:
            visit(cont.elements())

    def visit(members: list[Element]) -> None:
        i = 0
        while i < len(members):
            el = members[i]
            member_els.add(id(el))
            # aim-slide can only be a container; anything else is one iff marked
            if el.container_id is not None or el.tag == "aim-slide":
                if el.container_id is not None and el.chunk_id is not None:
                    el.remove_attr("data-aim")  # S012: container wins
                val = el.container_id
                if not usable(val):
                    assign([el], "data-aim-container", val)
                else:
                    seen.add(val)
                descend(el)
                i += 1
                continue
            val = el.chunk_id
            group = [el]
            j = i + 1
            if val:  # a run shares one id across consecutive members
                while (
                    j < len(members)
                    and members[j].chunk_id == val
                    and members[j].container_id is None
                ):
                    member_els.add(id(members[j]))
                    group.append(members[j])
                    j += 1
            if not usable(val):
                assign(group, "data-aim", val)
            else:
                seen.add(val)
            i = j

    visit(state.constructs())

    # markers nested inside chunk subtrees: rename only real collisions
    # (with a unit id, or with an id history/pending burned); other nested
    # oddities are the linter's to flag (S024), not reconcile's to rewrite.
    for top in state.constructs():
        for el in top.iter():
            if id(el) in member_els:
                continue
            for marker in ("data-aim", "data-aim-container"):
                val = el.get(marker)
                if not val:
                    continue
                if val in seen or (val in recorded and val not in expected_alive):
                    assign([el], marker, val)
    return assigned


# ===========================================================================
# event builders: mutate the scratch state AND append the matching event —
# the same data shapes the SDK operations write, plus origin: "reconcile"


def _base(S: AimDocument, author: Actor, at: str | None) -> dict:
    return {
        "seq": S.seq + 1,
        "kind": "direct_edit",
        "t": at or _now_iso(),
        "author": author.to_obj(),
        "batch": S._batch_id(),
        "origin": "reconcile",
    }


def _ev_modify(S: AimDocument, target: str, after: str, author: Actor, at: str | None) -> None:
    before = S._state.serial(target)
    S._state.replace(target, after)
    data = _base(S, author, at)
    data.update({"target": target, "action": "modify", "before": before, "after": after})
    S._append_event(data)


def _ev_delete(S: AimDocument, target: str, author: Actor, at: str | None) -> None:
    before = S._state.serial(target)
    anchor = S._anchor_of(target)
    S._state.remove(target)
    data = _base(S, author, at)
    data.update({"target": target, "action": "delete", "before": before, "anchor": anchor.to_obj()})
    S._append_event(data)


def _ev_add(S: AimDocument, serial: str, anchor: Anchor, author: Actor, at: str | None) -> None:
    nodes = [n for n in parse_fragment(serial) if isinstance(n, Element)]
    target = nodes[0].chunk_id or nodes[0].container_id or ""
    S._state.insert(serial, anchor)
    data = _base(S, author, at)
    data.update({"target": target, "action": "add", "anchor": anchor.to_obj(), "after": serial})
    S._append_event(data)


def _ev_move(S: AimDocument, target: str, to: Anchor, author: Actor, at: str | None) -> None:
    src = S._anchor_of(target)
    S._state.move(target, to)
    data = _base(S, author, at)
    data.update({"target": target, "action": "move", "from": src.to_obj(), "to": to.to_obj()})
    S._append_event(data)


def _ev_theme(S: AimDocument, after: str | None, author: Actor, at: str | None) -> None:
    before = S._state.serial("aim:theme")
    S._state.set_theme_markup(after)
    data = _base(S, author, at)
    data.update({"target": "aim:theme", "action": "modify"})
    if before is not None:
        data["before"] = before
    if after is not None:  # absent after = removal (mirrors undo's shape)
        data["after"] = after
    S._append_event(data)


def _ev_doc_settings(S: AimDocument, after: str | None, author: Actor, at: str | None) -> None:
    before = S._state.serial("aim:doc")
    S._state.set_doc_settings_markup(after)
    data = _base(S, author, at)
    data.update({"target": "aim:doc", "action": "modify"})
    if before is not None:
        data["before"] = before
    if after is not None:  # absent after = removal (mirrors undo's shape)
        data["after"] = after
    S._append_event(data)


# ===========================================================================
# the drive: append the edit script E -> A


def _drive(S: AimDocument, work: AimDocument, author: Actor, at: str | None) -> None:
    E = _units(S._state)
    A = _units(work._state)

    e_theme = S._state.serial("aim:theme")
    a_theme = work._state.serial("aim:theme")
    if e_theme != a_theme:
        _ev_theme(S, a_theme, author, at)

    e_doc = S._state.serial("aim:doc")
    a_doc = work._state.serial("aim:doc")
    if e_doc != a_doc:
        _ev_doc_settings(S, a_doc, author, at)

    gone = {uid for uid in E if uid not in A}
    both = [uid for uid in E if uid in A]
    # containers that exist only in A — a hand edit wrapped existing units
    # into a new container. The order walk adds the wrapper whole, which
    # materializes its interior: units inside count as covered on the A
    # side, so their E-side copies are doomed before the add (otherwise
    # they survive as duplicates and the doc_hash check cannot converge)
    added = {uid for uid, u in A.items() if uid not in E and u.is_container}

    def chain(units: dict[str, _Unit], uid: str) -> set[str]:
        out: set[str] = set()
        cur = units[uid].scope
        while cur != "body" and cur in units:
            out.add(cur)
            cur = units[cur].scope
        return out

    # containers whose diff needs a whole-serialization modify: attrs/shell
    # skeleton changed, or chunk<->container kind flips. Item-level events
    # cannot express those; the whole modify subsumes every item diff inside.
    whole: set[str] = set()
    for uid in both:
        eu, au = E[uid], A[uid]
        if not (eu.is_container or au.is_container) or eu.serial == au.serial:
            continue
        if eu.is_container and au.is_container:
            e_el = S._state.container_node(uid)
            a_el = work._state.container_node(uid)
            if e_el is not None and a_el is not None and _skeleton(e_el) == _skeleton(a_el):
                continue  # item-level events suffice
        whole.add(uid)
    # a whole-modified/deleted (or A-only added) ancestor governs
    # everything inside it
    whole = {
        uid
        for uid in whole
        if not (chain(E, uid) & (whole | gone)) and not (chain(A, uid) & (whole | added))
    }

    def covered_e(uid: str) -> bool:
        return bool(chain(E, uid) & (whole | gone))

    def covered_a(uid: str) -> bool:
        return bool(chain(A, uid) & (whole | added))

    # deletes — including units whose A-side landed inside a whole-modified
    # container (the modify materializes them there; their old spot empties)
    doomed = [
        uid
        for uid in E
        if (uid in gone and not covered_e(uid))
        or (uid in A and covered_a(uid) and not covered_e(uid))
    ]
    for uid in reversed(doomed):
        _ev_delete(S, uid, author, at)

    for uid in both:
        if uid in whole:
            _ev_modify(S, uid, A[uid].serial, author, at)

    for uid in both:
        if E[uid].is_container or A[uid].is_container:
            continue  # containers converge via items or their whole modify
        if covered_e(uid) or covered_a(uid):
            continue
        if E[uid].serial != A[uid].serial:
            _ev_modify(S, uid, A[uid].serial, author, at)

    # order walk: per scope in A document order, place every unit — add what
    # is missing, move what sits elsewhere. Interiors already materialized by
    # a container add/modify come out as no-ops.
    scopes: dict[str, list[str]] = {}
    for uid, u in A.items():
        scopes.setdefault(u.scope if u.shell is None else f"{u.scope}\x00{u.shell}", []).append(uid)
    for key, uids in scopes.items():
        scope, _, shell = key.partition("\x00")
        prev: str | None = None
        for uid in uids:
            want = Anchor(scope, prev, shell=(shell or None) if prev is None else None)
            if not S._state.exists(uid):
                _ev_add(S, A[uid].serial, want, author, at)
            else:
                cur = S._anchor_of(uid)
                if (cur.container, cur.after) != (scope, prev) or (
                    prev is None and (shell or None) is not None and cur.shell != shell
                ):
                    _ev_move(S, uid, want, author, at)
            prev = uid


def _reject_dangling(
    S: AimDocument, author: Actor, at: str | None, report: ReconcileReport
) -> None:
    """Pending proposals whose target or anchor vanished can never resolve;
    reject them so the reconciled document lints clean."""
    pending_adds = {p.id for p in S.proposals if p.action == "add"}
    for p in list(S.proposals):
        dangling = False
        if (
            p.action in ("modify", "delete", "move")
            and p.target
            and p.target not in ("aim:theme", "aim:doc")
        ):
            dangling = not S._state.exists(p.target)
        if not dangling and p.action in ("add", "move"):
            cont = p.anchor_container
            if cont and cont != "body" and S._state.container_node(cont) is None:
                dangling = True
            after = p.anchor_after
            if after and not S._state.exists(after) and after not in pending_adds:
                dangling = True
        if dangling:
            S.reject(
                p.id,
                decided_by=author,
                at=at,
                explanation="reconcile: the proposal's target or anchor "
                "was removed by an out-of-band edit",
            )
            report.rejected_proposals.append(p.id)


# ===========================================================================
def reconcile_document(
    doc: AimDocument,
    *,
    author: Actor | None = None,
    at: str | None = None,
    dry_run: bool = False,
) -> ReconcileReport:
    """Detect out-of-band edits and repair *doc* (see the module docstring).

    Returns a :class:`ReconcileReport`; mutates *doc* only when something
    changed and ``dry_run`` is false.
    """
    actor = author if author is not None else external()
    events = doc.history
    _check_log(events)

    S = _clone(doc)  # becomes E, then is driven to A
    _strip_body_state(S)
    _replay(S, events)
    expected_alive = S._state.all_ids()

    work = _clone(doc)  # A: the actual body, ids fixed up
    report = ReconcileReport()
    report.assigned_ids = _fixup_ids(work, expected_alive)
    _align_theme_baseline(S, events, work)

    n0 = len(events)
    with S.batch():  # one reconcile = one editing intention
        _drive(S, work, actor, at)
        _reject_dangling(S, actor, at, report)
    if S._state.doc_hash() != work._state.doc_hash():
        raise HistoryError(
            "reconcile did not converge — this is a bug in aimformat, please report it"
        )
    report.events = S.history[n0:]
    report.residual = S.verify()

    if report.changed and not dry_run:
        doc._fragment = S._fragment
        doc._state = S._state
        doc._batch = None
    return report
