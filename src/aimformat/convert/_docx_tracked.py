"""Word tracked changes → pending .aim proposals on the original body.

The importer converts two revision-free views of one ``document.xml``
(:mod:`._docx_revisions`): the **reject view** O becomes the body and the
**accept view** F the target. This module aligns the two conversions and
writes the difference as pending cards, so ``accept_all()`` gives Word's
*Accept All* and ``reject_all()`` gives Word's *Reject All*.

Alignment is by identity, never by position. Every block, list item and
table row carries the source key of the OOXML element it came from (the
paragraph whose MARK ends it, the row). Keys are stamped in document order,
so both conversions are sorted by key, and merging them by key interleaves
deletions and insertions exactly where the source has them.

- an O unit and an F unit with the same key are the same chunk; a changed
  one is a ``modify``. Equal keys in different carriers (an ``li`` in O, a
  body ``p`` in F) are a delete + add: a modify never moves a chunk between
  containers;
- an F container matches the first O container it shares an item key with;
  inside matched containers, items align the same way. When a list splits
  in F, the first F container keeps the identity, and the split-off part is
  a new container (its items deleted from the original) — carried only when
  the blocks that split it carry a revision. A join is the inverse: lists
  that a revision joined in F leave their O containers (deletes) and arrive
  as items of the first one (adds), citing the revisions that joined them;
- a merge or split follows the surviving paragraph mark: the views already
  joined the absorbed paragraph into the mark owner, so a merge is a delete
  plus a modify of the owner and a split is an add plus a modify;
- a paragraph whose mark is ``moveFrom`` named N and one whose mark is
  ``moveTo`` named N become one ``move`` card (plus a ``modify`` when the
  moved text was edited too); unnamed pairs stay delete + add;
- an O-only chunk immediately followed by an F-only chunk in the same
  container, both by one author at one date, is one ``modify`` — the shape
  ``to_docx`` writes for a modify.

**No card without a revision.** Some converter decisions are not local (list
grouping, heading inference, ``<ol start>``, baked number labels), so an
insertion can change how an untouched neighbour converts. A difference
becomes a card only when it cites a revision record whose source element
lies inside that chunk; any other difference is converter noise, F adopts
O's markup for it, and the report says so. Fabricated attribution would be
worse than a missing card.

The lane is written through the SDK's batch-propose primitive and validated
once on the result: accept-all must reproduce the adjusted F view, reject-all
the body, and the proposals lint pass must be clean. On a mismatch the
alignment degrades to whole containers; if that fails too, the import
refuses with the first diverging block named. Content is never dropped.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..canonical import serialize
from ..document import AimDocument, _CardSpec, resolution_order
from ..dom import Element, parse_fragment
from ..errors import AimError, ParseError
from ..events import Actor, external
from ..ingest import _containerize
from ._docx_revisions import EXCERPT_CAP, Revision, TrackedDocument, clean

_SRC = "data-aim-src"
_CAUSES = ("add", "delete", "modify")
_IDS = re.compile(r' data-aim(?:-container)?="[^"]*"')
_CONTAINERS = ("ul", "ol", "table")
_SHELLS = ("thead", "tbody", "tfoot")
_KEY = re.compile(r"^[a-z]+(\d+)(?:#([a-z]+)(\d*))?$")
#: where a derived key sorts relative to its paragraph's own block
_SUFFIX_ORDER = {"before": -2.0, "wrap": -1.0, "fig": 0.1, "after": 0.6, "pic": 0.7}
_PROP_KINDS = {
    "rPrChange",
    "pPrChange",
    "tblPrChange",
    "tblPrExChange",
    "trPrChange",
    "tcPrChange",
    "tblGridChange",
    "sectPrChange",
    "numberingChange",
}
_NOUNS = {
    "p": "paragraph",
    "li": "list item",
    "tr": "table row",
    "ul": "list",
    "ol": "list",
    "table": "table",
    "figure": "figure",
    "aim-page-break": "page break",
}
IMPORTER = "docx-import"


class TrackedImportError(AimError):
    """The two views could not be expressed as a validated pending lane."""


# -- units --------------------------------------------------------------------


@dataclass(eq=False)
class Unit:
    """One addressable thing of a conversion: a top-level block, a container,
    or a container's item — keyed by its source element."""

    key: str
    members: frozenset[str]  # every source key inside (joins included)
    el: Element  # provenance stripped
    markup: str  # canonical serialization, provenance stripped, no ids
    is_container: bool
    items: list[Unit] = field(default_factory=list)
    shell: str | None = None  # thead/tbody/tfoot for a table row
    order: tuple[float, float] = (0.0, 0.0)
    id: str | None = None  # O side: the live id once the body is built

    @property
    def tag(self) -> str:
        return self.el.tag

    def shell_sig(self) -> tuple:
        return (
            self.el.tag,
            tuple((k, v) for k, v in self.el.attrs if not k.startswith("data-aim")),
        )

    def signature(self) -> tuple:
        if self.is_container:
            return ("C", self.shell_sig(), tuple((i.shell, i.markup) for i in self.items))
        return ("K", self.markup)


def units_of(blocks: Iterable[str], side: str) -> list[Unit]:
    """Parse a provenance-mode conversion into units (provenance stripped)."""
    out: list[Unit] = []
    last = (0.0, 0.0)
    for n, markup in enumerate(blocks):
        for k, el in enumerate(x for x in parse_fragment(markup) if isinstance(x, Element)):
            unit = _unit(el, f"{side}anon{n}.{k}", last)
            last = unit.order
            out.append(unit)
    return out


def _order_of(key: str, last: tuple[float, float]) -> tuple[float, float]:
    m = _KEY.match(key)
    if not m:
        return (last[0], last[1] + 0.001)
    sub = 0.0
    if m.group(2):
        sub = _SUFFIX_ORDER.get(m.group(2), 0.5) + (int(m.group(3) or 0) * 0.01)
    return (float(m.group(1)), sub)


def _tokens(el: Element) -> list[str]:
    out: list[str] = []
    for e in el.iter():
        value = e.get(_SRC)
        if value:
            out.extend(value.split())
    return out


def _own_key(el: Element, fallback: str) -> str:
    value = el.get(_SRC)
    return value.split()[0] if value else fallback


def _direct_items(el: Element) -> list[tuple[Element, str | None]]:
    if el.tag in ("ul", "ol"):
        return [(c, None) for c in el.elements() if c.tag == "li"]
    out: list[tuple[Element, str | None]] = []
    for c in el.elements():
        if c.tag in _SHELLS:
            out += [(r, c.tag) for r in c.elements() if r.tag == "tr"]
        elif c.tag == "tr":
            out.append((c, None))
    return out


def _unit(el: Element, fallback: str, last: tuple[float, float]) -> Unit:
    container = el.tag in _CONTAINERS
    items: list[Unit] = []
    if container:
        prev = last
        for i, (item, shell) in enumerate(_direct_items(el)):
            key = _own_key(item, f"{fallback}/{i}")
            order = _order_of(key, prev)
            prev = order
            items.append(
                Unit(
                    key=key,
                    members=frozenset(_tokens(item)) | {key},
                    el=item,
                    markup="",
                    is_container=False,
                    shell=shell,
                    order=order,
                )
            )
    own = el.get(_SRC)
    key = own.split()[0] if own else (f"list:{items[0].key}" if items else fallback)
    order = _order_of(own.split()[0], last) if own else (items[0].order if items else last)
    members = frozenset(_tokens(el)) | {key}
    for node in el.iter():
        node.remove_attr(_SRC)
    for member in items:
        member.markup = serialize(member.el)
    return Unit(key, members, el, serialize(el), container, items, None, order)


def actual_signature(doc: AimDocument) -> list[tuple]:
    """The body as alignment compares it: ids stripped, containers by item."""
    out: list[tuple] = []
    for el in doc._state.constructs():
        if el.container_id is not None and el.tag in _CONTAINERS:
            shell = (el.tag, tuple((k, v) for k, v in el.attrs if not k.startswith("data-aim")))
            items = tuple((sh, _IDS.sub("", serialize(it))) for it, sh in _direct_items(el))
            out.append(("C", shell, items))
        else:
            out.append(("K", _IDS.sub("", serialize(el))))
    return out


def assign_ids(units: list[Unit], doc: AimDocument) -> None:
    """Map the built body's ids back onto the O units (same order, same shape)."""
    constructs = doc._state.constructs()
    if len(constructs) != len(units):  # pragma: no cover - a converter bug
        raise TrackedImportError("the imported body does not match its own conversion")
    for unit, el in zip(units, constructs, strict=True):
        unit.id = el.chunk_id or el.container_id
        if unit.is_container:
            live = _direct_items(el)
            for item, (item_el, _shell) in zip(unit.items, live, strict=False):
                item.id = item_el.chunk_id


# -- the plan -----------------------------------------------------------------


@dataclass(eq=False)
class _Entry:
    kind: str  # match | o | f
    o: Unit | None
    f: Unit | None
    seq: _Seq
    action: str = "keep"  # keep | modify | delete | add | drop | move | move_source
    # | replace | replaced
    revs: list[Revision] = field(default_factory=list)
    partner: _Entry | None = None
    noise: bool = False
    split_of: _Entry | None = None  # a split-off F container: the matched entry it left
    joined: bool = False  # an F unit that arrived in another container by a list merge
    cards: list[int] = field(default_factory=list)
    sub: _Seq | None = None  # matched container: its item sequence

    @property
    def order(self) -> tuple[float, float]:
        if self.kind == "match" and self.o is not None and self.f is not None:
            # A matched container starts at its first item on each side, and
            # the two differ when Accept All drops or adds leading items. The
            # later start lies inside BOTH spans (they share an item), so no
            # unit of either view sorts between it and the container: an
            # inserted paragraph that replaced the first item lands before
            # the list, as in Word. Matched chunks share one key.
            return max(self.o.order, self.f.order)
        unit = self.f if self.kind == "f" else self.o
        assert unit is not None
        return unit.order


@dataclass(eq=False)
class _Seq:
    container: str  # "body" or the live O container id
    family: str  # body | list | table
    entries: list[_Entry] = field(default_factory=list)


@dataclass
class Plan:
    specs: list[_CardSpec]
    spec_revs: list[list[Revision]]
    expected: list[tuple]
    noise: int  # blocks whose conversion differed with no revision behind it
    #: F-side unit key → the card id slot carrying it (for comment anchors)
    f_cards: dict[str, int]
    page_card: int | None = None


def _family(unit: Unit) -> str:
    if unit.tag in ("ul", "ol"):
        return "list"
    if unit.tag == "table":
        return "table"
    return "chunk"


class _Planner:
    def __init__(
        self,
        o_units: list[Unit],
        f_units: list[Unit],
        facts: TrackedDocument,
        *,
        fine: bool,
        importer: Actor,
    ):
        self.o_units = o_units
        self.f_units = f_units
        self.facts = facts
        self.fine = fine
        self.importer = importer
        self.by_src: dict[str, list[Revision]] = {}
        for rev in facts.revisions:
            for src in dict.fromkeys(rev.srcs):
                self.by_src.setdefault(src, []).append(rev)
        self.specs: list[_CardSpec] = []
        self.spec_revs: list[list[Revision]] = []
        self.noise = 0
        self.f_cards: dict[str, int] = {}

    # -- citation ------------------------------------------------------------
    def cites(self, *units: Unit | None) -> list[Revision]:
        seen: dict[int, Revision] = {}
        for unit in units:
            if unit is None:
                continue
            for member in unit.members:
                for rev in self.by_src.get(member, ()):
                    seen[rev.index] = rev
        return [seen[i] for i in sorted(seen)]

    # -- alignment -----------------------------------------------------------
    def build(self) -> _Seq:
        top = _Seq("body", "body")
        o_chunks = {u.key: u for u in self.o_units if not u.is_container}
        used_o: dict[int, _Entry] = {}  # id(O container) → its matched entry
        f_entries: list[_Entry] = []
        matched_o: set[int] = set()
        for f in self.f_units:
            if not f.is_container:
                o = o_chunks.get(f.key)
                if o is not None and id(o) not in matched_o:
                    matched_o.add(id(o))
                    f_entries.append(_Entry("match", o, f, top))
                else:
                    f_entries.append(_Entry("f", None, f, top))
                continue
            keys = {f.key} | {i.key for i in f.items}
            candidates = [
                o
                for o in self.o_units
                if o.is_container
                and _family(o) == _family(f)
                and ({o.key} | {i.key for i in o.items}) & keys
            ]
            fresh = next((o for o in candidates if id(o) not in used_o), None)
            if fresh is not None:
                entry = _Entry("match", fresh, f, top)
                used_o[id(fresh)] = entry
                matched_o.add(id(fresh))
                f_entries.append(entry)
            else:
                entry = _Entry("f", None, f, top)
                if candidates and self.fine:
                    entry.split_of = used_o.get(id(candidates[0]))
                f_entries.append(entry)
        o_entries = [_Entry("o", o, None, top) for o in self.o_units if id(o) not in matched_o]
        top.entries = _merge(o_entries, f_entries)
        for entry in top.entries:
            if entry.kind == "match" and entry.o is not None and entry.o.is_container:
                entry.sub = self._items(entry)
        return top

    def _items(self, entry: _Entry) -> _Seq:
        o, f = entry.o, entry.f
        assert o is not None and f is not None and o.id is not None
        seq = _Seq(o.id, _family(o))
        if not self.fine:
            return seq
        o_by_key = {i.key: i for i in o.items}
        taken: set[str] = set()
        f_entries: list[_Entry] = []
        for item in f.items:
            twin = o_by_key.get(item.key)
            if twin is not None and item.key not in taken:
                taken.add(item.key)
                f_entries.append(_Entry("match", twin, item, seq))
            else:
                f_entries.append(_Entry("f", None, item, seq))
        o_entries = [_Entry("o", i, None, seq) for i in o.items if i.key not in taken]
        seq.entries = _merge(o_entries, f_entries)
        return seq

    # -- decisions -------------------------------------------------------------
    def decide(self, top: _Seq) -> None:
        for seq in _walk(top):
            for entry in seq.entries:
                self._decide_entry(entry)
        self._splits(top)
        self._joins(top)
        if self.fine:
            self._pair_moves(top)
        for seq in _walk(top):
            self._pair_replacements(seq)

    def _decide_entry(self, entry: _Entry) -> None:
        o, f = entry.o, entry.f
        if entry.kind == "match":
            assert o is not None and f is not None
            if o.is_container:
                if not self.fine:
                    # coarse alignment: a changed container is one modify
                    if o.signature() != f.signature():
                        revs = self.cites(o, f)
                        if revs:
                            entry.action, entry.revs = "modify", revs
                        else:
                            entry.noise = True
                elif o.shell_sig() != f.shell_sig():
                    entry.noise = True  # the shell is the converter's (start, classes)
                return
            if o.markup != f.markup:
                revs = self.cites(o, f)
                if revs:
                    entry.action, entry.revs = "modify", revs
                else:
                    entry.noise = True
            return
        unit = o if entry.kind == "o" else f
        assert unit is not None
        revs = self.cites(unit)
        if revs:
            entry.action = "delete" if entry.kind == "o" else "add"
            entry.revs = revs
        else:
            entry.action = "keep" if entry.kind == "o" else "drop"
            entry.noise = True

    def _splits(self, top: _Seq) -> None:
        """A split-off F container is carried only when the blocks that split
        it from its original carry a revision; its items then leave the
        original container (deletes citing the same revisions)."""
        for i, entry in enumerate(top.entries):
            origin = entry.split_of
            if origin is None or entry.f is None or origin.sub is None:
                continue
            start = top.entries.index(origin) if origin in top.entries else -1
            between = top.entries[start + 1 : i]
            cause: list[Revision] = []
            for other in between:
                if other.action in ("add", "delete", "modify"):
                    cause += other.revs
            moved = {item.key for item in entry.f.items}
            if not cause:
                continue  # converter noise: the original container keeps them
            entry.action = "add"
            entry.noise = False
            entry.revs = _unique(entry.revs + cause)
            for item_entry in origin.sub.entries:
                if item_entry.kind == "o" and item_entry.o and item_entry.o.key in moved:
                    item_entry.action = "delete"
                    item_entry.noise = False
                    item_entry.revs = _unique(item_entry.revs + cause)

    def _joins(self, top: _Seq) -> None:
        """The inverse of a split: a revision removes what separated two lists
        (or tables), and the F view converts them as ONE container. The second
        O container is then deleted (its own revisions say why), and its
        untouched items arrive in the first F container with no revision of
        their own — which would make them converter noise and drop them, so
        accepting the lane would lose text Word keeps. An F-only unit whose
        key belongs to a deleted O unit is carried as an add instead, citing
        that deletion's revisions.

        A container in between that carries no revision of its own (three
        lists joined by two deleted headings) joins too, citing the revisions
        between it and the container that absorbed it — exactly as a split
        cites what split it. Kept in place instead, it would land AFTER the
        items carried past it, and the whole-container (coarse) alignment
        would carry its items twice."""
        absorbed_by: dict[str, _Entry] = {}  # O item key -> the matched entry holding it in F
        for entry in top.entries:
            if entry.kind == "match" and entry.f is not None and entry.f.is_container:
                for item in entry.f.items:
                    absorbed_by.setdefault(item.key, entry)
        for i, entry in enumerate(top.entries):
            if entry.kind != "o" or entry.o is None or not entry.o.is_container:
                continue
            if entry.action != "keep":
                continue
            home = next((absorbed_by[i.key] for i in entry.o.items if i.key in absorbed_by), None)
            if home is None:
                continue
            between = top.entries[top.entries.index(home) + 1 : i]
            cause = [r for other in between if other.action in _CAUSES for r in other.revs]
            if cause:
                entry.action, entry.noise, entry.revs = "delete", False, _unique(cause)
        # A matched list can lose items to ANOTHER matched list: a join that a
        # later revision splits again leaves the second list matched (it keeps
        # its tail) while its head ends the first one, and a split whose lower
        # part a join attaches to the next list matches that list instead of
        # the original. Each such item leaves its O list and arrives in the F
        # list holding it, citing what lies between the two lists.
        carried_down: set[str] = set()
        position = {id(entry): k for k, entry in enumerate(top.entries)}
        for i, entry in enumerate(top.entries):
            if entry.kind != "match" or entry.sub is None:
                continue
            for member in entry.sub.entries:
                if member.kind != "o" or member.action != "keep" or member.o is None:
                    continue
                home = absorbed_by.get(member.o.key)
                if home is None or home is entry:
                    continue
                j = position[id(home)]
                between = top.entries[min(i, j) + 1 : max(i, j)]
                cause = [r for other in between if other.action in _CAUSES for r in other.revs]
                if cause:
                    member.action, member.noise, member.revs = "delete", False, _unique(cause)
                    if j > i:
                        carried_down.add(member.o.key)
        deleted: dict[str, list[Revision]] = {}
        for seq in _walk(top):
            for entry in seq.entries:
                if entry.kind == "o" and entry.action == "delete" and entry.o is not None:
                    for key in {entry.o.key} | {i.key for i in entry.o.items}:
                        deleted.setdefault(key, entry.revs)
        if not deleted:
            return
        for seq in _walk(top):
            for entry in seq.entries:
                if entry.kind != "f" or entry.action != "drop" or entry.f is None:
                    continue
                keys = {entry.f.key} | {i.key for i in entry.f.items}
                cause = [rev for key in sorted(keys & deleted.keys()) for rev in deleted[key]]
                if cause:
                    entry.action, entry.noise = "add", False
                    entry.joined = not keys <= carried_down
                    entry.revs = _unique(cause)

    def _pair_moves(self, top: _Seq) -> None:
        sources: dict[str, list[_Entry]] = {}
        targets: dict[str, list[_Entry]] = {}
        for seq in _walk(top):
            for entry in seq.entries:
                unit = entry.o if entry.kind == "o" else entry.f if entry.kind == "f" else None
                if unit is None or unit.is_container or entry.split_of is not None:
                    continue
                mark = self.facts.move_marks.get(unit.key)
                if mark is None or not mark[1]:
                    continue
                kind, name = mark[0], str(mark[1])
                if entry.kind == "o" and kind == "moveFrom" and entry.action == "delete":
                    sources.setdefault(name, []).append(entry)
                elif entry.kind == "f" and kind == "moveTo" and entry.action == "add":
                    targets.setdefault(name, []).append(entry)
        for name, froms in sources.items():
            tos = targets.get(name, [])
            if len(froms) != len(tos):
                continue  # an unpairable range stays delete + add
            for src, dst in zip(froms, tos, strict=True):
                # Word tracks moves of paragraph text: body blocks among
                # themselves, list items among list items. Rows and whole
                # containers have no move markup at all.
                if src.seq.family != dst.seq.family or src.seq.family == "table":
                    continue
                src.action, dst.action = "move_source", "move"
                src.partner, dst.partner = dst, src
                dst.revs = _unique(src.revs + dst.revs)
                src.revs = []

    def _pair_replacements(self, seq: _Seq) -> None:
        entries = seq.entries
        for a, b in zip(entries, entries[1:], strict=False):
            if a.action != "delete" or b.action != "add" or a.kind != "o" or b.kind != "f":
                continue
            if a.o is None or b.f is None or a.o.is_container or b.f.is_container:
                continue
            if b.split_of is not None:
                continue
            ra, rb = _attribution_key(a.revs), _attribution_key(b.revs)
            if ra is None or ra != rb:
                continue
            a.action, b.action = "replaced", "replace"
            a.partner, b.partner = b, a
            b.revs = _unique(a.revs + b.revs)
            a.revs = []

    # -- emission ------------------------------------------------------------
    def emit(self, top: _Seq) -> list[tuple]:
        expected: list[tuple] = []
        self._emit_seq(top, expected)
        return expected

    def _emit_seq(self, seq: _Seq, expected: list) -> None:
        prev: tuple[str, str | int] | None = None
        for entry in seq.entries:
            o, f = entry.o, entry.f
            act = entry.action
            if entry.noise:
                self.noise += 1
            if entry.kind == "match":
                assert o is not None and f is not None
                if o.is_container and entry.sub is not None and self.fine:
                    items: list[tuple] = []
                    self._emit_seq(entry.sub, items)
                    expected.append(("C", o.shell_sig(), tuple(items)))
                elif act == "modify":
                    markup = _coarse_markup(o, f) if f.is_container else f.markup
                    self._card("modify", entry, noun=f, target=o.id, markup=markup)
                    expected.append(_sig(f, seq))
                else:
                    expected.append(_sig(o, seq))
                if f.key:
                    self._note_f(f, entry)
                prev = ("id", o.id or "")
                continue
            if entry.kind == "o":
                assert o is not None
                if act == "delete":
                    self._card("delete", entry, noun=o, target=o.id)
                elif act == "keep":
                    expected.append(_sig(o, seq))
                    prev = ("id", o.id or "")
                # move_source / replaced: nothing lands here
                continue
            assert f is not None
            if act == "drop":
                continue
            if act == "replace":
                partner = entry.partner
                assert partner is not None and partner.o is not None
                self._card("replace", entry, noun=f, target=partner.o.id, markup=f.markup)
                expected.append(_sig(f, seq))
                self._note_f(f, entry)
                prev = ("id", partner.o.id or "")
                continue
            if act == "move":
                partner = entry.partner
                assert partner is not None and partner.o is not None
                moved = partner.o
                expected.append(_sig(f if moved.markup != f.markup else moved, seq))
                self._move_cards(entry, seq, prev)
                if partner.seq.container == seq.container:
                    prev = ("id", moved.id or "")
                # A move from ANOTHER container: the moved chunk is not here
                # until the move is accepted, so nothing may anchor on it (a
                # recorded anchor names a current chunk or a pending add).
                # What follows keeps the move's own anchor, and same-anchor
                # position cards land in creation order (spec §5.4): after it.
                continue
            # add
            markup = _containerize(f.markup) if f.is_container else f.markup
            index = self._card(
                "add", entry, noun=f, markup=markup, after=prev, container=seq.container
            )
            expected.append(_sig(f, seq))
            self._note_f(f, entry)
            prev = ("card", index)

    def _move_cards(self, entry: _Entry, seq: _Seq, after: tuple[str, str | int] | None) -> None:
        """A move card (plus a modify when the moved text was edited too)."""
        partner = entry.partner
        assert partner is not None and partner.o is not None and entry.f is not None
        moved, f = partner.o, entry.f
        self._card("move", entry, noun=moved, target=moved.id, after=after, container=seq.container)
        if moved.markup != f.markup:
            self._card("modify", entry, noun=f, target=moved.id, markup=f.markup)
        self._note_f(f, entry)

    def _note_f(self, f: Unit, entry: _Entry) -> None:
        if entry.cards:
            for key in f.members:
                self.f_cards.setdefault(key, entry.cards[-1])

    def _card(
        self,
        action: str,
        entry: _Entry,
        *,
        noun: Unit | None,
        target: str | None = None,
        markup: str | None = None,
        after: tuple[str, str | int] | None = None,
        container: str = "body",
    ) -> int:
        revs = entry.revs
        if entry.action == "move":
            # a moved-and-edited paragraph: the move is the mover's, the
            # modify the editor's — each card cites its own revisions
            moving = [r for r in revs if r.kind in ("moveFrom", "moveTo")]
            editing = [r for r in revs if r.kind not in ("moveFrom", "moveTo")]
            revs = (moving or revs) if action == "move" else (editing or revs)
        actor, at, key = _attribution(revs, self.importer)
        chained = after is not None and after[0] == "card"
        spec = _CardSpec(
            action="modify" if action == "replace" else action,
            author=actor,
            target=target,
            markup=markup,
            container=container,
            after=None if after is None or chained else str(after[1]),
            after_card=int(after[1]) if after is not None and chained else None,
            explanation=_explanation("join" if entry.joined else action, revs, noun),
            at=at,
            batch_key=key,
        )
        self.specs.append(spec)
        self.spec_revs.append(revs)
        index = len(self.specs) - 1
        entry.cards.append(index)
        return index


def _coarse_markup(o: Unit, f: Unit) -> str:
    """The F container as a whole-container modify of its O twin: the O
    container's id on the root, and every item that exists on both sides
    keeps its O id (new items get fresh ones when the card is written)."""
    root = next(x for x in parse_fragment(f.markup) if isinstance(x, Element))
    root.set("data-aim-container", o.id or "")
    o_ids = {item.key: item.id for item in o.items if item.id}
    for (el, _shell), item in zip(_direct_items(root), f.items, strict=True):
        if item.key in o_ids:
            el.set("data-aim", o_ids[item.key] or "")
    return serialize(root)


def _sig(unit: Unit, seq: _Seq) -> tuple:
    if seq.family == "body":
        return unit.signature()
    return (unit.shell, unit.markup)


def _walk(top: _Seq) -> list[_Seq]:
    out = [top]
    for entry in top.entries:
        if entry.sub is not None:
            out.append(entry.sub)
    return out


def _merge(o_entries: list[_Entry], f_entries: list[_Entry]) -> list[_Entry]:
    """Both conversions are in source-key order; so is their merge. Matched
    entries appear once; at equal positions the O side (deletion) comes
    first, as Word lays a replacement out."""
    tagged = [(e.order, 0, i, e) for i, e in enumerate(o_entries)]
    tagged += [(e.order, 1, i, e) for i, e in enumerate(f_entries)]
    tagged.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in tagged]


def _unique(revs: list[Revision]) -> list[Revision]:
    seen: dict[int, Revision] = {}
    for rev in revs:
        seen[rev.index] = rev
    return [seen[i] for i in sorted(seen)]


# -- attribution -------------------------------------------------------------------


def actor_for(author: str | None) -> Actor:
    """A Word author name → an actor. ``agent:<model>`` is the exact inverse
    of the exporter's label; any other name is a human. Both are claims made
    by the file, not verified identities."""
    if not author:
        return external(IMPORTER)
    if author.startswith("agent:") and author[6:].strip():
        return Actor("agent", model=author[6:].strip())
    return Actor("human", id=author)


def _attribution_key(revs: list[Revision]) -> tuple | None:
    """(the single author, the single date) when a set of revisions has
    exactly one of each; None otherwise."""
    if not revs:
        return None
    authors = {r.author for r in revs}
    dates = {r.date for r in revs}
    if len(authors) != 1 or len(dates) != 1:
        return None
    return (next(iter(authors)), next(iter(dates)))


def normalize_date(value: str | None) -> str | None:
    """A ``w:date`` → ISO-8601 UTC (``…Z``), or None when absent/invalid. A
    timestamp without a timezone is read as UTC."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_dt.timezone.utc)
    stamp = stamp.astimezone(_dt.timezone.utc)
    if not (1900 <= stamp.year <= 9999):
        return None
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _attribution(revs: list[Revision], importer: Actor) -> tuple[Actor, str | None, str]:
    authors = list(dict.fromkeys(r.author for r in revs))
    if len(authors) == 1:
        actor = actor_for(authors[0])
    else:
        actor = importer
    dates = [d for d in (normalize_date(r.date) for r in revs) if d]
    at = max(dates) if dates else None
    key = f"{actor.type}:{actor.id or actor.model or ''}"
    return actor, at, key


def _explanation(action: str, revs: list[Revision], unit: Unit | None) -> str:
    authors = [a or "unknown author" for a in dict.fromkeys(r.author for r in revs)]
    who = ", ".join(authors) or "unknown author"
    noun = _NOUNS.get(unit.tag if unit is not None else "", "paragraph")
    if unit is not None and unit.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        noun = "heading"
    if action == "add":
        return clean(f"Word: {who} inserted this {noun}", EXCERPT_CAP)
    if action == "join":
        return clean(
            f"Word: {who} removed what separated this {noun} from the one above", EXCERPT_CAP
        )
    if action == "delete":
        return clean(f"Word: {who} deleted this {noun}", EXCERPT_CAP)
    if action == "move":
        return clean(f"Word: {who} moved this {noun}", EXCERPT_CAP)
    if action == "replace":
        return clean(f"Word: {who} replaced this {noun}", EXCERPT_CAP)
    if revs and all(r.kind in _PROP_KINDS for r in revs):
        return clean(f"Word: {who} changed formatting", EXCERPT_CAP)
    parts: list[str] = []
    for author in authors:
        mine = [r for r in revs if (r.author or "unknown author") == author]
        bits: list[str] = []
        i = 0
        while i < len(mine):
            rev = mine[i]
            nxt = mine[i + 1] if i + 1 < len(mine) else None
            if rev.where == "run" and rev.kind in ("del", "moveFrom") and nxt is not None:
                if nxt.where == "run" and nxt.kind in ("ins", "moveTo"):
                    bits.append(f"{_q(rev.text)} → {_q(nxt.text)}")
                    i += 2
                    continue
            bits.append(_describe(rev))
            i += 1
        parts.append(f"({author}): " + ", ".join(dict.fromkeys(bits)))
    return clean("Word tracked change " + "; ".join(parts), EXCERPT_CAP)


def _q(text: str) -> str:
    return '"' + clean(text, 60) + '"'


def _describe(rev: Revision) -> str:
    if rev.kind in _PROP_KINDS:
        return "formatting"
    if rev.where == "mark":
        return "paragraph break added" if rev.kind in ("ins", "moveTo") else "paragraphs joined"
    if rev.where == "row":
        return "row inserted" if rev.kind == "ins" else "row deleted"
    if rev.where == "cell":
        return "cell changed"
    if rev.kind in ("ins", "moveTo"):
        return f"+ {_q(rev.text)}"
    if rev.kind in ("del", "moveFrom"):
        return f"− {_q(rev.text)}"
    return rev.kind


# -- driving it ---------------------------------------------------------------------


@dataclass
class LaneResult:
    document: AimDocument
    plan: Plan
    card_ids: list[str | None]  # per plan spec: the card written, None when dropped
    coarse: bool


def plan_lane(
    o_units: list[Unit],
    f_units: list[Unit],
    facts: TrackedDocument,
    *,
    fine: bool,
    importer: Actor,
) -> Plan:
    planner = _Planner(o_units, f_units, facts, fine=fine, importer=importer)
    top = planner.build()
    planner.decide(top)
    expected = planner.emit(top)
    return Plan(planner.specs, planner.spec_revs, expected, planner.noise, planner.f_cards)


def write_lane(
    doc: AimDocument,
    o_units: list[Unit],
    f_units: list[Unit],
    facts: TrackedDocument,
    *,
    page_markup: str | None,
    page_revs: list[Revision],
    importer: Actor,
    max_proposals: int,
) -> LaneResult:
    """Plan, write and validate the pending lane on a clone of *doc*; degrade
    to whole-container alignment once; refuse rather than drop content.

    Writing and validating cost time quadratic in the card count (every
    resolution re-reads the lane), so a plan above *max_proposals* cards is
    refused before any card is written. Cards are not bounded by revisions:
    one deleted paragraph that joins two lists carries every item of the
    second list across."""
    failure: str | None = None
    for fine in (True, False):
        plan = plan_lane(o_units, f_units, facts, fine=fine, importer=importer)
        cards = len(plan.specs) + (page_markup is not None)
        if cards > max_proposals:
            raise ParseError(
                f"the tracked changes in this document would become {cards} proposals, "
                f"more than max_proposals={max_proposals}; import it with "
                "tracked='accept' or tracked='reject', or raise max_proposals"
            )
        if page_markup is not None:
            actor, at, key = _attribution(page_revs, importer)
            who = ", ".join(dict.fromkeys(r.author or "unknown author" for r in page_revs))
            plan.page_card = len(plan.specs)
            plan.specs.append(
                _CardSpec(
                    action="modify",
                    author=actor,
                    target="aim:doc",
                    markup=page_markup,
                    explanation=clean(f"Word: {who} changed the page setup", EXCERPT_CAP),
                    at=at,
                    batch_key=key,
                )
            )
            plan.spec_revs.append(page_revs)
        trial = doc._clone()
        specs, slots = _without_noop_moves(trial, plan.specs)
        try:
            made = trial._propose_lane(specs)
        except AimError as exc:
            failure = str(exc)
            continue
        problem = validate(trial, plan.expected)
        if problem is None:
            card_ids = [made[slot].id if slot is not None else None for slot in slots]
            return LaneResult(trial, plan, card_ids, coarse=not fine)
        failure = problem
    raise ParseError(
        "the tracked changes in this document could not be imported as proposals "
        f"({failure}); import it with tracked='accept' or tracked='reject' instead"
    )


def _without_noop_moves(
    doc: AimDocument, specs: list[_CardSpec]
) -> tuple[list[_CardSpec], list[int | None]]:
    """A move whose destination already IS its position in the current body
    changes nothing and would be refused as a no-op; it is left out of the
    lane. Returns the kept specs and, per original spec, its index among them
    (None when dropped) — chained adds are re-pointed accordingly."""
    kept: list[_CardSpec] = []
    slots: list[int | None] = []
    for spec in specs:
        if spec.action == "move" and spec.target and spec.after_card is None:
            try:
                here = doc._anchor_of(spec.target)
                there = doc._resolve_end_anchor(
                    spec.container, spec.after, exclude=spec.target, shell=spec.shell
                )
            except AimError:
                here, there = None, None
            if here is not None and here == there:
                slots.append(None)
                continue
        if spec.after_card is not None:
            remapped = slots[spec.after_card]
            if remapped is None:  # pragma: no cover - only adds are chained onto
                raise TrackedImportError("a chained add lost its anchor")
            spec = _replace(spec, after_card=remapped)
        slots.append(len(kept))
        kept.append(spec)
    return kept, slots


def _replace(spec: _CardSpec, **changes: object) -> _CardSpec:
    from dataclasses import replace

    return replace(spec, **changes)  # type: ignore[arg-type]


def validate(doc: AimDocument, expected: list[tuple]) -> str | None:
    """None when accept-all gives *expected*, reject-all the body, and the
    proposals lint pass is clean; otherwise what went wrong, in words."""
    from ..lint import ERROR, _Linter

    checker = external("docx-import-check")
    accepted = doc._clone()
    try:
        for proposal in resolution_order(accepted.proposals):
            accepted.accept(proposal.id, decided_by=checker)
    except AimError as exc:
        return f"accepting the lane failed: {exc}"
    got = actual_signature(accepted)
    if got != expected:
        return "accept-all differs from Word's Accept All at " + _first_difference(got, expected)
    rejected = doc._clone()
    try:
        for proposal in resolution_order(rejected.proposals):
            rejected.reject(proposal.id, decided_by=checker)
    except AimError as exc:
        return f"rejecting the lane failed: {exc}"
    body = actual_signature(doc)
    got = actual_signature(rejected)
    if got != body:
        return "reject-all differs from the original at " + _first_difference(got, body)
    linter = _Linter(doc, None)
    linter.proposals()
    errors = [f for f in linter.findings if f.level == ERROR]
    if errors:
        return f"the proposals do not lint: {errors[0]}"
    return None


def _first_difference(got: list[tuple], want: list[tuple]) -> str:
    for i, (a, b) in enumerate(zip(got, want, strict=False)):
        if a != b:
            return f"block {i + 1} ({_excerpt(b)!r})"
    if len(got) != len(want):
        i = min(len(got), len(want))
        longer = want if len(want) > len(got) else got
        return f"block {i + 1} ({_excerpt(longer[i])!r})"
    return "an unknown position"  # pragma: no cover


def _excerpt(sig: tuple) -> str:
    text = sig[1] if sig[0] == "K" else " ".join(m for _, m in sig[2])
    plain = re.sub(r"<[^>]+>", "", str(text))
    return clean(plain, 80)
