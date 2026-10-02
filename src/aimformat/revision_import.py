"""Import a returned DOCX as a revision of the document it was exported from.

The loop this serves: ``.aim`` → ``to_docx(..., roundtrip_marks=True)`` → a
colleague edits the file in Word → the DOCX comes back →
:meth:`AimDocument.import_revision`. Without it the returned file imports as
a brand-new document (fresh ids everywhere), and every id-keyed tool reports
"everything replaced".

The import is **id stamping followed by emission**:

1. **Read** the returned file with the ordinary DOCX importer, carrying the
   round-trip markers (:mod:`aimformat.docx_marks`) through as data.
2. **Reconstruct what was exported** (X): the manifest names the base
   ``doc_hash``/``seq``, the pending mode, and the pending cards at export.
3. **Recompute the null round trip** N = import(export(X)) with the SDK that
   is running *now*. A returned unit equal to its N counterpart was not edited
   by the colleague, whatever the converter did to it — importer changes
   between export and import can never surface as edits.
4. **Align** returned units to X units: validated markers first, then exact
   text (LCS), then budgeted fuzzy matching, then unique long-text moves.
5. **Classify** each unit (unchanged / modified / deleted / added / moved),
   replay text-only edits onto the base markup (so formatting DOCX could not
   carry survives), and refuse to overwrite structure DOCX could not show the
   colleague (lossy units: rebase or conflict, never a fallback payload).
6. **Check drift**: a unit the colleague changed that also changed in the
   document since export is a conflict, not an overwrite.
7. **Emit** one batch: proposals by the colleague (default; review-friendly)
   or ``origin: "reconcile"`` direct edits through the reconcile driver.

Markers are hints (spec §4.5): identity is declared by the cards or events
this import writes, never by the bookmark itself.
"""

from __future__ import annotations

import bisect
import datetime as _dt
import difflib
import hashlib
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Literal

from . import ids
from .canonical import serialize
from .docx_marks import (
    Manifest,
    hash_index,
    parse_bookmark_name,
    parse_manifest,
    resolve_marker,
    unit_hash,
)
from .dom import Element, Text, deep_copy, parse_fragment
from .errors import AimError, InvalidOperation, ParseError
from .events import Actor, Event, external, human

if TYPE_CHECKING:  # pragma: no cover
    from .document import AimDocument, Proposal

__all__ = ["Conflict", "RevisionImportReport", "import_revision"]

# -- tuning (module constants, not public API) ---------------------------------
_CROSS_CHECK_LOW = 0.35  # a marked unit this unlike its export unit is suspect
_CROSS_CHECK_HIGH = 0.8  # …and an unmarked neighbour this alike takes the id
_FUZZY_MIN = 0.55  # fuzzy alignment threshold (word tokens)
_MOVE_MIN = 0.9  # unmarked move threshold
_MOVE_MIN_CHARS = 40  # unmarked moves need this much text (no boilerplate moves)
_SCORE_CAP = 2000  # characters of a unit's text used for scoring
_FUZZY_BUDGET = 200_000  # scored pairs per import
_MISMATCH_RATIO = 0.5  # below this aligned share an unrelated file is refused
_EXPLAIN_MAX = 120
_QUOTE_MAX = 40
_AUTHOR_MAX = 64

_MARK_ATTR = "data-aim-mark"
_INLINE_FMT = frozenset(
    {"a", "abbr", "b", "br", "code", "em", "i", "mark", "s", "small", "span", "strong"}
    | {"sub", "sup", "u"}
)
_TEXT_FAMILY = frozenset(
    {"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "div", "section"}
)
_CORE_NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dcterms": "http://purl.org/dc/terms/",
}


# =============================================================================
# report


@dataclass
class Conflict:
    """A colleague change that was not applied (or applied as a flagged card)."""

    unit: str
    reason: str
    base_text: str = ""
    colleague_text: str = ""

    def to_obj(self) -> dict:
        return {
            "unit": self.unit,
            "reason": self.reason,
            "base_text": self.base_text,
            "colleague_text": self.colleague_text,
        }


@dataclass
class RevisionImportReport:
    """What :meth:`AimDocument.import_revision` found and wrote.

    ``proposals`` (proposals mode) or ``events`` (edits mode) are what was
    written; ``already_pending`` lists changes skipped because an identical
    pending card already exists (re-importing the same file is a no-op);
    ``conflicts`` are colleague changes that were not applied. On ``dry_run``
    everything is hypothetical and the document is untouched.
    """

    changes: str
    author: Actor
    base_match: str  # "exact" | "advanced" | "unknown"
    pending_mode: str
    units: int = 0  # exported units the returned file was aligned against
    aligned_by_marker: int = 0
    aligned_by_content: int = 0
    unchanged: int = 0
    noise_suppressed: int = 0
    rebased: int = 0
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    already_pending: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.proposals or self.events)

    def summary(self) -> str:
        bits = []
        for label, items in (
            ("modified", self.modified),
            ("added", self.added),
            ("deleted", self.deleted),
            ("moved", self.moved),
        ):
            if items:
                bits.append(f"{len(items)} {label}")
        what = ", ".join(bits) if bits else "no changes"
        written = (
            f"{len(self.proposals)} proposal(s)"
            if self.changes == "proposals"
            else f"{len(self.events)} event(s)"
        )
        out = (
            f"{what} by {self.author.id or self.author.type} -> {written}; "
            f"{self.aligned_by_marker + self.aligned_by_content}/{self.units} units aligned "
            f"({self.aligned_by_marker} by marker), base {self.base_match}"
        )
        if self.already_pending:
            out += f"; {len(self.already_pending)} already pending"
        if self.conflicts:
            out += f"; {len(self.conflicts)} conflict(s)"
        return out

    def to_obj(self) -> dict:
        return {
            "summary": self.summary(),
            "changes": self.changes,
            "author": self.author.to_obj(),
            "base_match": self.base_match,
            "pending_mode": self.pending_mode,
            "units": self.units,
            "aligned_by_marker": self.aligned_by_marker,
            "aligned_by_content": self.aligned_by_content,
            "unchanged": self.unchanged,
            "noise_suppressed": self.noise_suppressed,
            "rebased": self.rebased,
            "added": self.added,
            "deleted": self.deleted,
            "modified": self.modified,
            "moved": self.moved,
            "proposals": self.proposals,
            "events": [e.data for e in self.events],
            "already_pending": self.already_pending,
            "superseded": self.superseded,
            "conflicts": [c.to_obj() for c in self.conflicts],
            "warnings": self.warnings,
        }


# =============================================================================
# the exported side (X)


@dataclass
class _X:
    id: str
    scope: str
    shell: str | None
    serial: str
    els: list[Element]
    kind: str  # "chunk" | "item" | "container" | "slide"
    slide: str | None  # the enclosing slide's id, when flattened from one
    tag: str
    text: str = ""


def _x_units(doc: AimDocument) -> dict[str, _X]:
    from .reconcile import _units

    out: dict[str, _X] = {}
    for uid, unit in _units(doc._state).items():
        els = [n for n in parse_fragment(unit.serial) if isinstance(n, Element)]
        tag = els[0].tag if els else ""
        parent = out.get(unit.scope)
        slide = None
        if parent is not None:
            slide = parent.id if parent.kind == "slide" else parent.slide
        if unit.is_container:
            kind = "slide" if tag == "aim-slide" else "container"
        elif unit.scope == "body" or (parent is not None and parent.kind == "slide"):
            kind = "chunk"
        else:
            kind = "item"
        out[uid] = _X(uid, unit.scope, unit.shell, unit.serial, els, kind, slide, tag, _plain(els))
    return out


def _body_order(xs: dict[str, _X]) -> list[str]:
    """Body-level alignment targets in document order; slide children are
    flattened in place of their slide (DOCX linearises slides)."""
    return [
        x.id
        for x in xs.values()
        if (x.kind == "chunk") or (x.kind == "container" and (x.scope == "body" or x.slide))
    ]


# =============================================================================
# the returned side


@dataclass
class _Ret:
    els: list[Element]
    kind: str  # "chunk" | "container" | "item"
    marks: list[tuple[int, str]]  # (ordinal, unit id)
    items: list[_Ret] = field(default_factory=list)
    shell: str | None = None
    uid: str | None = None
    via: str | None = None
    merged: list[str] = field(default_factory=list)
    text: str = ""
    phantom: bool = False  # a block the export → import pipeline itself makes

    @property
    def firsts(self) -> list[str]:
        return [uid for k, uid in self.marks if k == 1]


def _take_marks(el: Element, known: dict[str, str]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in el.iter():
        raw = node.get(_MARK_ATTR)
        if raw is None:
            continue
        node.remove_attr(_MARK_ATTR)
        for name in raw.split():
            marker = parse_bookmark_name(name)
            if marker is None:
                continue
            uid = resolve_marker(marker, known)
            if uid:
                out.append((marker.ordinal, uid))
    return out


def _direct_items(el: Element) -> list[tuple[Element, str | None]]:
    if el.tag in ("ul", "ol"):
        return [(c, None) for c in el.elements() if c.tag == "li"]
    out: list[tuple[Element, str | None]] = []
    for c in el.elements():
        if c.tag == "tr":
            out.append((c, None))
        elif c.tag in ("thead", "tbody", "tfoot"):
            out.extend((r, c.tag) for r in c.elements() if r.tag == "tr")
    return out


def _structure(blocks: list[str], xs: dict[str, _X], known: dict[str, str]) -> list[_Ret]:
    rets: list[_Ret] = []
    for markup in blocks:
        for el in parse_fragment(markup):
            if not isinstance(el, Element):
                continue
            if el.container_id is not None:
                items: list[_Ret] = []
                for it, shell in _direct_items(el):
                    items.append(_Ret([it], "item", _take_marks(it, known), shell=shell))
                stray = _take_marks(el, known)  # anything left on the root/shells
                cmarks = [m for m in stray if xs.get(m[1]) and xs[m[1]].kind == "container"]
                for it_ret in items:
                    own = [m for m in it_ret.marks if xs.get(m[1]) and xs[m[1]].kind == "container"]
                    cmarks += own
                    it_ret.marks = [m for m in it_ret.marks if m not in own]
                # an atomic list/table CHUNK exported flat comes back as a
                # container: give it back its chunk shape when the markers say so
                firsts = [uid for it_ret in items for k, uid in it_ret.marks if k == 1]
                if (
                    not cmarks
                    and firsts
                    and all(xs.get(uid) is not None and xs[uid].kind == "chunk" for uid in firsts)
                ):
                    el.remove_attr("data-aim-container")
                    for it, _ in _direct_items(el):
                        it.remove_attr("data-aim")
                    marks = [m for it_ret in items for m in it_ret.marks]
                    rets.append(_Ret([el], "chunk", marks))
                    continue
                ret = _Ret([el], "container", cmarks, items=_regroup(items))
                rets.append(ret)
            else:
                rets.append(_Ret([el], "chunk", _take_marks(el, known)))
    return _regroup(rets)


def _regroup(seq: list[_Ret]) -> list[_Ret]:
    """Re-join units the exporter flattened into several paragraphs: a block
    carrying ``_aim<k>_<id>`` (k >= 2) joins the open group of ``<id>``,
    together with any unmarked blocks between them."""
    out: list[_Ret] = []
    open_at: dict[str, int] = {}
    # per kind: the last index in *out* that is marked or of another kind —
    # a group may only absorb a clean run after it (O(1) per block; a
    # crafted file of continuation markers must not cost a rescan each)
    last_bad: dict[str, int] = {"chunk": -1, "item": -1, "container": -1}
    for ret in seq:
        later = [uid for k, uid in ret.marks if k >= 2]
        if ret.kind != "container" and later and not ret.firsts:
            gi = open_at.get(later[0])
            if gi is not None and out[gi].kind == ret.kind and last_bad[ret.kind] <= gi:
                group = out[gi]
                for extra in out[gi + 1 :]:
                    group.els += extra.els
                group.els += ret.els
                group.marks += ret.marks
                del out[gi + 1 :]
                for kind in last_bad:  # out[gi] is marked: it bounds every run
                    last_bad[kind] = gi
                continue
        out.append(ret)
        for kind in last_bad:
            if ret.marks or ret.kind != kind:
                last_bad[kind] = len(out) - 1
        for uid in ret.firsts:
            open_at[uid] = len(out) - 1
    for ret in out:
        ret.text = _plain(ret.els)
        for item in ret.items:
            item.text = _plain(item.els)
    return out


# =============================================================================
# text helpers


def _plain(els: list[Element]) -> str:
    text = "".join(el.text() for el in els)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def _words(text: str) -> list[str]:
    return text[:_SCORE_CAP].split()


def _family(tag: str) -> str:
    if tag in _TEXT_FAMILY:
        return "text"
    if tag in ("ul", "ol"):
        return "list"
    return tag


def _merge_inline(el: Element) -> None:
    out: list[Any] = []
    for c in el.children:
        if isinstance(c, Element):
            _merge_inline(c)
        prev = out[-1] if out else None
        if (
            isinstance(c, Element)
            and isinstance(prev, Element)
            and c.tag in _INLINE_FMT
            and c.tag != "br"
            and c.tag == prev.tag
            and c.attrs == prev.attrs
        ):
            prev.children.extend(c.children)
            _merge_inline(prev)
            continue
        if isinstance(c, Text) and isinstance(prev, Text):
            prev.data += c.data
            continue
        out.append(c)
    el.children = out


def _scrub(el: Element) -> Element:
    clone = deep_copy(el)
    assert isinstance(clone, Element)
    for node in clone.iter():
        for attr in (_MARK_ATTR, "data-aim", "data-aim-container"):
            node.remove_attr(attr)
    return clone


def _norm(els: list[Element]) -> str:
    """Equality modulo what a word processor's re-save changes without
    meaning: run fragmentation, Unicode composition, whitespace runs."""
    parts = []
    for el in els:
        clone = _scrub(el)
        _merge_inline(clone)
        for node in clone.iter():
            for c in node.children:
                if isinstance(c, Text):
                    c.data = re.sub(r"\s+", " ", unicodedata.normalize("NFC", c.data))
        parts.append(serialize(clone))
    return "\n".join(parts)


def _carried(b_els: list[Element], n_els: list[Element]) -> bool:
    """B survives the export → import unchanged, up to its root class tokens
    (restored separately) and noise: nothing is lost by replacing it."""

    def unclassed(els: list[Element]) -> list[Element]:
        out = []
        for el in els:
            clone = _scrub(el)
            clone.remove_attr("class")
            out.append(clone)
        return out

    return len(b_els) == len(n_els) and _norm(unclassed(b_els)) == _norm(unclassed(n_els))


def _struct(els: list[Element]) -> list[str]:
    return [e.tag for el in els for e in el.iter() if e.tag not in _INLINE_FMT]


def _text_nodes(el: Element) -> list[Text]:
    out: list[Text] = []

    def walk(node: Element) -> None:
        for c in node.children:
            if isinstance(c, Text):
                out.append(c)
            elif isinstance(c, Element):
                walk(c)

    walk(el)
    return out


def _blank(el: Element) -> str:
    clone = _scrub(el)
    _merge_inline(clone)
    for t in _text_nodes(clone):
        t.data = ""
    return serialize(clone)


def _merged_texts(els: list[Element]) -> list[str]:
    out: list[str] = []
    for el in els:
        clone = _scrub(el)
        _merge_inline(clone)
        out += [t.data for t in _text_nodes(clone)]
    return out


def _rebase(
    b_els: list[Element], n_els: list[Element], r_els: list[Element]
) -> list[Element] | None:
    """Replay the colleague's text-only change (N → R) onto the base markup B.

    Preconditions: N and R have the same inline skeleton (only text changed),
    and every changed span of N's text is text B renders too — B may render
    text the export added or reshaped (a link's URL, a line break) only where
    the colleague did not edit. ``None`` when they fail."""
    if not b_els or len(n_els) != len(r_els):
        return None
    if [_blank(e) for e in n_els] != [_blank(e) for e in r_els]:
        return None
    n_texts, r_texts = _merged_texts(n_els), _merged_texts(r_els)
    if len(n_texts) != len(r_texts):
        return None
    copies: list[Element] = []
    for e in b_els:
        c = deep_copy(e)
        assert isinstance(c, Element)
        copies.append(c)
    nodes = [t for c in copies for t in _text_nodes(c)]
    if not nodes:
        return None
    pn = "".join(n_texts)
    pb = "".join(t.data for t in nodes)
    shared = (
        [(0, 0, len(pn))]
        if pb == pn
        else [
            blk
            for blk in difflib.SequenceMatcher(None, pn, pb, autojunk=False).get_matching_blocks()
            if blk.size
        ]
    )

    def to_b(i1: int, i2: int) -> tuple[int, int] | None:
        for a, b, size in shared:
            if a <= i1 and i2 <= a + size:
                return b + i1 - a, b + i2 - a
        return None

    # diff node by node (N and R share their skeleton), so each change stays
    # on its own side of every element boundary; an insertion at the end of
    # a text run belongs to that run (typing at a line's end, before <br>)
    edits: list[tuple[int, int, str, bool]] = []  # (b1, b2, new, leans back)
    base = 0
    for nt, rt in zip(n_texts, r_texts, strict=True):
        if nt != rt:
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                None, nt, rt, autojunk=False
            ).get_opcodes():
                if tag == "equal":
                    continue
                span = to_b(base + i1, base + i2)
                if span is None:
                    return None  # the edit touches text B does not render
                edits.append((span[0], span[1], rt[j1:j2], i1 == len(nt) and i1 > 0))
        base += len(nt)

    for b1, b2, new, back in reversed(edits):
        sp, off = [], 0
        for t in nodes:
            sp.append((t, off, off + len(t.data)))
            off += len(t.data)
        inside = next((s for s in sp if s[1] < b1 < s[2]), None)
        before = next((s for s in reversed(sp) if s[1] < b1 <= s[2]), None)
        after = next((s for s in sp if s[1] <= b1 < s[2]), None)
        if b1 < b2:
            host = after or before or sp[0]
        else:
            host = inside or ((before or after) if back else (after or before)) or sp[0]
        t, a, z = host
        if b2 > z:  # the replaced range spans later nodes: trim them first
            for t2, a2, z2 in sp:
                if a2 >= z and a2 < b2:
                    t2.data = t2.data[min(b2, z2) - a2 :]
            b2 = z
        t.data = t.data[: b1 - a] + new + t.data[b2 - a :]
    return copies


def _quote(text: str) -> str:
    text = text.replace('"', "'")
    return text if len(text) <= _QUOTE_MAX else text[: _QUOTE_MAX - 1] + "…"


# =============================================================================
# alignment (D7)


class _Aligner:
    def __init__(self, budget: int | None = None):
        self.budget = _FUZZY_BUDGET if budget is None else budget
        self.scored = 0
        self.exhausted = False

    def ratio(self, a: str, b: str, floor: float) -> float:
        if a == b:
            return 1.0
        if self.scored >= self.budget:
            self.exhausted = True
            return 0.0
        self.scored += 1
        wa, wb = _words(a), _words(b)
        sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
        if sm.real_quick_ratio() < floor or sm.quick_ratio() < floor:
            return 0.0
        return sm.ratio()

    @staticmethod
    def compatible(ret: _Ret, x: _X) -> bool:
        if ret.kind == "container":
            return x.kind == "container" and _family(ret.els[0].tag) == _family(x.tag)
        if ret.kind == "item":
            return x.kind == "item"
        return x.kind == "chunk"

    @staticmethod
    def content_compatible(ret: _Ret, x: _X) -> bool:
        if not _Aligner.compatible(ret, x):
            return False
        a, b = _family(ret.els[0].tag), _family(x.tag)
        return a == b or {a, b} <= {"text", "figure"}

    def align(
        self, rets: list[_Ret], order: list[str], xs: dict[str, _X], hidden: set[str]
    ) -> None:
        """Assign X ids to *rets* in place (scope = *order*)."""
        allowed = [u for u in order if u not in hidden]
        allowed_set = set(allowed)
        claimed: set[str] = set()
        # 1. markers. Several first-markers on one block are either a merge
        # (the colleague joined paragraphs: the block holds both texts) or
        # markers forwarded from paragraphs that came back empty (the block
        # holds only its own text) — told apart by the text itself.
        for ret in rets:
            cands = []
            for uid in ret.firsts:
                if uid in allowed_set and uid not in claimed and self.compatible(ret, xs[uid]):
                    if uid not in cands:
                        cands.append(uid)
            if not cands:
                continue
            primary, merged = cands[0], []
            if len(cands) > 1:
                singles = [
                    (self.ratio(ret.text, xs[u].text, 0.0), -i, u) for i, u in enumerate(cands)
                ]
                best, _, best_uid = max(singles)
                joined = " ".join(xs[u].text for u in cands if xs[u].text)
                if xs[cands[0]].text and self.ratio(ret.text, joined, 0.0) > best:
                    merged = [u for u in cands[1:] if xs[u].text]
                else:
                    primary = best_uid
            claimed.add(primary)
            ret.uid, ret.via = primary, "marker"
            for uid in merged:
                claimed.add(uid)
                ret.merged.append(uid)
        # 2. cross-check: a marker is a hint (Enter at a paragraph's start
        # leaves the bookmark on the new, empty-then-typed paragraph)
        for k, ret in enumerate(rets):
            if ret.uid is None or ret.via != "marker" or ret.merged:
                continue
            x = xs[ret.uid]
            if self.ratio(ret.text, x.text, 0.0) >= _CROSS_CHECK_LOW:
                continue
            for k2 in (k + 1, k - 1):
                if 0 <= k2 < len(rets):
                    other = rets[k2]
                    if (
                        other.uid is None
                        and self.compatible(other, x)
                        and self.ratio(other.text, x.text, _CROSS_CHECK_HIGH) >= _CROSS_CHECK_HIGH
                    ):
                        other.uid, other.via = ret.uid, "marker"
                        ret.uid, ret.via = None, None
                        break
        # 3 + 4. exact LCS then fuzzy, inside each gap between anchors
        pos = {u: i for i, u in enumerate(allowed)}
        gi = 0
        while gi < len(rets):
            if rets[gi].uid is not None:
                gi += 1
                continue
            gj = gi
            while gj < len(rets) and rets[gj].uid is None:
                gj += 1
            lo = pos.get(rets[gi - 1].uid or "", -1) + 1 if gi > 0 else 0
            hi = pos.get(rets[gj].uid or "", len(allowed)) if gj < len(rets) else len(allowed)
            cands = [u for u in allowed[lo:hi] if u not in claimed] if lo <= hi else []
            self._gap(rets[gi:gj], cands, xs, claimed)
            gi = gj
        # 5. unmarked moves: long, near-identical, unique on both sides
        rest = [r for r in rets if r.uid is None and len(r.text) >= _MOVE_MIN_CHARS]
        free = [u for u in allowed if u not in claimed and len(xs[u].text) >= _MOVE_MIN_CHARS]
        hits: dict[int, list[str]] = {}
        back: dict[str, list[int]] = {}
        for i, r in enumerate(rest):
            if self.exhausted:
                break  # the budget bounds the loop, not only the scoring
            for u in free:
                if (
                    self.content_compatible(r, xs[u])
                    and self.ratio(r.text, xs[u].text, _MOVE_MIN) >= _MOVE_MIN
                ):
                    hits.setdefault(i, []).append(u)
                    back.setdefault(u, []).append(i)
        for i, us in hits.items():
            if len(us) == 1 and len(back[us[0]]) == 1:
                rest[i].uid, rest[i].via = us[0], "content"
                claimed.add(us[0])

    def _gap(
        self, rets: list[_Ret], cands: list[str], xs: dict[str, _X], claimed: set[str]
    ) -> None:
        if not rets or not cands:
            return
        ka = [(_family(r.els[0].tag), r.text) for r in rets]
        kb = [(_family(xs[u].tag), xs[u].text) for u in cands]
        sm = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
        pa = pb = 0
        for a, b, size in sm.get_matching_blocks():
            self._fuzzy(rets[pa:a], cands[pb:b], xs, claimed)
            for i in range(size):
                r, u = rets[a + i], cands[b + i]
                if r.uid is None and u not in claimed and self.content_compatible(r, xs[u]):
                    r.uid, r.via = u, "content"
                    claimed.add(u)
            pa, pb = a + size, b + size

    def _fuzzy(
        self, rets: list[_Ret], cands: list[str], xs: dict[str, _X], claimed: set[str]
    ) -> None:
        if not rets or not cands:
            return
        pairs = []
        for k, r in enumerate(rets):
            if self.exhausted:
                break
            for j, u in enumerate(cands):
                if self.content_compatible(r, xs[u]):
                    score = self.ratio(r.text, xs[u].text, _FUZZY_MIN)
                    if score >= _FUZZY_MIN:
                        pairs.append((score, k, j))
        chosen: list[tuple[int, int]] = []
        for _score, k, j in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):
            r, u = rets[k], cands[j]
            if r.uid is not None or u in claimed:
                continue
            if any((k < k2) != (j < j2) for k2, j2 in chosen):
                continue  # order-preserving inside a gap
            chosen.append((k, j))
            r.uid, r.via = u, "content"
            claimed.add(u)


def _align_all(rets: list[_Ret], xs: dict[str, _X], hidden: set[str], aligner: _Aligner) -> None:
    aligner.align(rets, _body_order(xs), xs, hidden)
    for ret in rets:
        if ret.kind != "container":
            continue
        if ret.uid is None:  # majority vote of its items' markers
            votes: dict[str, int] = {}
            for item in ret.items:
                for _k, uid in item.marks:
                    x = xs.get(uid)
                    if x is not None and x.kind == "item":
                        votes[x.scope] = votes.get(x.scope, 0) + 1
            taken = {r.uid for r in rets if r.uid}
            for cid, _n in sorted(votes.items(), key=lambda kv: -kv[1]):
                x = xs.get(cid)
                if (
                    x is not None
                    and cid not in taken
                    and cid not in hidden
                    and _Aligner.compatible(ret, x)
                ):
                    ret.uid, ret.via = cid, "content"
                    break
        if ret.uid is not None:
            order = [u for u, x in xs.items() if x.kind == "item" and x.scope == ret.uid]
            aligner.align(ret.items, order, xs, hidden)


def _drop_phantoms(r_rets: list[_Ret], n_rets: list[_Ret]) -> None:
    """Unmarked blocks the pipeline produces on its own (a synthetic page
    break before a slide, say) appear in the null round trip too. Pair the
    returned file's unaligned blocks with the null round trip's, in order, by
    content: the paired ones are conversion artifacts, not additions."""

    def pair(rs: list[_Ret], ns: list[_Ret]) -> None:
        r_free = [r for r in rs if r.uid is None]
        n_free = [n for n in ns if n.uid is None]
        if not r_free or not n_free:
            return
        sm = difflib.SequenceMatcher(
            None, [_norm(r.els) for r in r_free], [_norm(n.els) for n in n_free], autojunk=False
        )
        for a, _b, size in sm.get_matching_blocks():
            for r in r_free[a : a + size]:
                r.phantom = True

    pair(r_rets, n_rets)
    n_containers = {n.uid: n for n in n_rets if n.kind == "container" and n.uid}
    for r in r_rets:
        if r.kind == "container" and r.uid in n_containers:
            pair(r.items, n_containers[r.uid].items)


def _walk(rets: list[_Ret]):
    for ret in rets:
        yield ret
        yield from ret.items


# =============================================================================
# reading the returned package


def _read_source(source: str | Path | bytes | BinaryIO) -> tuple[bytes, str]:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source), "returned.docx"
    if isinstance(source, (str, Path)):
        path = Path(source)
        return path.read_bytes(), path.name
    return source.read(), Path(getattr(source, "name", "returned.docx")).name


def _package_facts(data: bytes) -> tuple[Manifest | None, str | None, str | None, list[str]]:
    """(manifest, lastModifiedBy, dcterms:modified, warnings) — read from the
    raw package after the importer's archive guards."""
    from lxml import etree

    from .convert._docx_seam import _guard_archive

    warnings: list[str] = []
    zf = zipfile.ZipFile(io.BytesIO(data))
    _guard_archive(zf)
    names = zf.namelist()

    def item_no(name: str) -> int:
        m = re.search(r"(\d+)\.xml$", name)
        return int(m.group(1)) if m else 0

    manifest = None
    for name in sorted(
        (n for n in names if re.fullmatch(r"customXml/item\d+\.xml", n)), key=item_no
    ):
        manifest = parse_manifest(zf.read(name))
        if manifest is not None:
            break
    who = when = None
    if "docProps/core.xml" in names and b"<!DOCTYPE" not in zf.read("docProps/core.xml"):
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        try:
            core = etree.fromstring(zf.read("docProps/core.xml"), parser)
            node = core.find("cp:lastModifiedBy", _CORE_NS)
            who = (node.text or "").strip() if node is not None else None
            node = core.find("dcterms:modified", _CORE_NS)
            when = (node.text or "").strip() if node is not None else None
        except etree.XMLSyntaxError:
            pass
    dropped = {
        "footnotes": r"word/footnotes\.xml",
        "endnotes": r"word/endnotes\.xml",
        "comments": r"word/comments\.xml",
        "headers/footers": r"word/(header|footer)\d*\.xml",
    }
    for label, pattern in dropped.items():
        for n in names:
            if re.fullmatch(pattern, n):
                blob = zf.read(n)
                if len(re.findall(rb"<w:t[ >]", blob)) > 0:
                    warnings.append(
                        f"the returned file has {label}; they are not imported, so edits "
                        "there are not part of this revision"
                    )
                break
    return manifest, who, when, warnings


# -- tracked changes -----------------------------------------------------------------

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_REVISION_TAGS = ("ins", "del", "moveFrom", "moveTo")
_CHANGE_TAGS = (
    "rPrChange",
    "pPrChange",
    "sectPrChange",
    "tblPrChange",
    "trPrChange",
    "tcPrChange",
    "tblGridChange",
    "numberingChange",
)
_RANGE_TAGS = ("moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd")


def _w(tag: str) -> str:
    return f"{{{_W}}}{tag}"


def _revision_signature(document_xml: bytes) -> list[tuple[str, str, str, str]]:
    """(kind, author, date, text) per revision element, in document order."""
    from lxml import etree

    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )
    root = etree.fromstring(document_xml, parser)
    out = []
    for el in root.iter(*(_w(t) for t in _REVISION_TAGS)):
        text = "".join(t.text or "" for t in el.iter(_w("t"), _w("delText")))
        out.append(
            (etree.QName(el).localname, el.get(_w("author")) or "", el.get(_w("date")) or "", text)
        )
    return out


def _accept_foreign_revisions(data: bytes, ours: set[tuple[str, str]]) -> tuple[bytes, set[str]]:
    """Accept, in the returned package, every tracked change whose
    (author, date) is not one the export itself wrote — the colleague's own
    Track Changes. Returns the rewritten package and the revision authors.
    Exported pending cards (ours) are left for the importer exactly as the
    null round trip sees them."""
    from lxml import etree

    zf = zipfile.ZipFile(io.BytesIO(data))
    blob = zf.read("word/document.xml")
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )
    root = etree.fromstring(blob, parser)

    def foreign(el: Any) -> bool:
        return (el.get(_w("author")) or "", el.get(_w("date")) or "") not in ours

    authors: set[str] = set()
    touched = False
    joins: list[Any] = []  # paragraphs whose mark the colleague deleted
    for el in list(root.iter(*(_w(t) for t in (*_REVISION_TAGS, *_CHANGE_TAGS)))):
        if not foreign(el) or el.getparent() is None:
            continue
        authors.add(el.get(_w("author")) or "unknown")
        touched = True
        parent = el.getparent()
        name = etree.QName(el).localname
        ptag = etree.QName(parent).localname
        if name in _CHANGE_TAGS:
            parent.remove(el)  # accepting a formatting change keeps the current props
        elif ptag == "rPr" and name in ("ins", "del"):
            # a paragraph mark revision: inserted marks just stay; a deleted
            # mark joins this paragraph with the next one (below)
            parent.remove(el)
            para = next(parent.iterancestors(_w("p")), None)
            if name == "del" and para is not None:
                joins.append(para)
        elif ptag == "trPr":
            parent.remove(el)
            if name == "del":
                row = parent.getparent()
                if row is not None and row.getparent() is not None:
                    row.getparent().remove(row)
        elif name in ("del", "moveFrom"):
            parent.remove(el)
        else:  # ins / moveTo: keep the content, drop the wrapper
            index = parent.index(el)
            for offset, child in enumerate(list(el)):
                parent.insert(index + offset, child)
            parent.remove(el)
    # last first, so a run of deleted marks collapses into one paragraph; the
    # paragraph's properties live in its mark, so the surviving (next) mark's
    # pPr wins — deleting a whole heading leaves the body text a body paragraph
    for para in reversed(joins):
        nxt = para.getnext()
        if para.getparent() is None or nxt is None or nxt.tag != _w("p"):
            continue
        own = para.find(_w("pPr"))
        if own is not None:
            para.remove(own)
        for child in list(nxt):
            if child.tag == _w("pPr"):
                para.insert(0, child)
            else:
                para.append(child)
        nxt.getparent().remove(nxt)
    for el in list(root.iter(*(_w(t) for t in _RANGE_TAGS))):
        if el.getparent() is not None and foreign(el):
            el.getparent().remove(el)
            touched = True
    if not touched:
        return data, authors
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in zf.infolist():
            payload = zf.read(info.filename)
            if info.filename == "word/document.xml":
                payload = etree.tostring(
                    root, xml_declaration=True, encoding="UTF-8", standalone=True
                )
            dst.writestr(info.filename, payload)
    return out.getvalue(), authors


def _parse_iso(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    text = value.strip()
    try:  # W3CDTF: a UTC "Z" or an explicit offset (+02:00 is not UTC)
        when = _dt.datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        try:
            when = _dt.datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return when.astimezone(_dt.timezone.utc).replace(microsecond=0)


def _clamped_time(modified: str | None, exported_at: str | None) -> str:
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    when = _parse_iso(modified)
    floor = _parse_iso(exported_at)
    if when is None or when > now or (floor is not None and when < floor):
        when = now
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_author(name: str | None) -> Actor:
    if name:
        clean = re.sub(r"\s+", " ", name).strip()[:_AUTHOR_MAX]
        if clean:
            return human(f"docx:{clean}")
    return external("docx-import")


# =============================================================================
# reconstructing the export


def _with_lane(body_doc: AimDocument, source: AimDocument, keep: set[str] | None) -> AimDocument:
    """*body_doc* (no lane) plus *source*'s pending cards whose ids are in
    *keep* (all of them when ``keep`` is None)."""
    clone = body_doc._clone()
    sec = clone._state.section("aim-proposals")
    if sec is not None:
        clone._state.body.children.remove(sec)
    src = source._state.section("aim-proposals")
    cards = (
        [] if src is None else [c for c in src.elements() if keep is None or c.get("id") in keep]
    )
    if cards:
        target = clone._proposals_section()
        for card in cards:
            copy = deep_copy(card)
            target.children.append(copy)
        clone._rebuild_history_index(burned_seed=set(clone._get_history_index().burned_ids))
    return clone


def _restore_resolved_cards(x_doc: AimDocument, doc: AimDocument, wanted: set[str]) -> int:
    """Re-create on *x_doc* the cards that were pending at export (*wanted*)
    but have been accepted or rejected since. The exported file still shows
    them (as tracked changes, or applied), so the null round trip must too —
    otherwise an untouched file re-proposes a decided card as the
    colleague's. Rebuilt from the resolution events' ``proposed*`` fields;
    returns how many could not be (pruned history, an anchor that no longer
    replays)."""
    from .document import Anchor

    missing = wanted - {p.id for p in x_doc.proposals}
    if not missing:
        return 0
    events = sorted(
        (e for e in doc.history if e.kind == "resolution" and e.get("proposal") in missing),
        key=lambda e: (str(e.get("proposed_at") or ""), e.seq),
    )
    lost = len(missing) - len(events)
    for ev in events:
        try:
            actor = Actor.from_obj(ev.get("proposed_by") or {})
            at = ev.get("proposed_at")
            action, target = ev.action, ev.target or ""
            if action == "modify":
                x_doc.propose_modify(target, ev.get("proposed") or "", author=actor, at=at)
            elif action == "delete":
                x_doc.propose_delete(target, author=actor, at=at)
            elif action == "add":
                anchor = Anchor.from_obj(ev.get("anchor") or {})
                proposed = ev.get("proposed") or ""
                try:  # keep the payload's ids: an accepted add is live under them
                    x_doc.propose_add(
                        proposed,
                        author=actor,
                        container=anchor.container,
                        after=anchor.after,
                        at=at,
                    )
                except AimError:
                    x_doc.propose_add(
                        re.sub(r'( data-aim(?:-container)?=)"[^"]*"', r'\1""', proposed),
                        author=actor,
                        container=anchor.container,
                        after=anchor.after,
                        at=at,
                    )
            elif action == "move":
                to = Anchor.from_obj(ev.get("to") or {})
                x_doc.propose_move(
                    target,
                    author=actor,
                    container=to.container,
                    after=to.after,
                    shell=to.shell if to.after is None else None,
                    at=at,
                )
            else:
                lost += 1
        except (AimError, KeyError, TypeError, ValueError):
            lost += 1
    return lost


def _view(doc: AimDocument, mode: str) -> AimDocument:
    from .export_docx import _resolve_copy

    return doc if mode == "tracked" else _resolve_copy(doc, mode)


# =============================================================================
# the import


@dataclass
class _Op:
    kind: str  # "modify" | "delete" | "add" | "move"
    uid: str  # target, or the new root id for adds
    payload: str | None = None
    scope: str = "body"
    shell: str | None = None
    after: tuple[str, Any] | None = None  # ("unit", id) | ("new", op index)
    depends: int | None = None  # index of the op this one depends on
    explanation: str = ""


def import_revision(
    doc: AimDocument,
    source: str | Path | bytes | BinaryIO,
    *,
    author: Actor | None = None,
    changes: Literal["proposals", "edits"] = "proposals",
    conflicts: Literal["report", "propose"] = "report",
    base_seq: int | None = None,
    at: str | None = None,
    dry_run: bool = False,
) -> RevisionImportReport:
    """See :meth:`AimDocument.import_revision`."""
    if changes not in ("proposals", "edits"):
        raise InvalidOperation(f"changes must be 'proposals' or 'edits', got {changes!r}")
    if conflicts not in ("report", "propose"):
        raise InvalidOperation(f"conflicts must be 'report' or 'propose', got {conflicts!r}")
    if conflicts == "propose" and changes == "edits":
        # an edit cannot be "flagged": it would silently overwrite the
        # change made in the document since the export
        raise InvalidOperation(
            "conflicts='propose' needs changes='proposals' (a conflicting direct edit "
            "would overwrite the document's own change)"
        )
    problems = doc.verify()
    if problems:
        raise InvalidOperation(
            "cannot import a revision onto a document whose history does not verify "
            f"({problems[0]}); run `aim reconcile` first"
        )
    data, label = _read_source(source)
    from .convert._docx_in import convert_docx_marked
    from .export_docx import docx_bytes

    try:
        manifest, who, modified, warnings = _package_facts(data)
    except (zipfile.BadZipFile, ValueError) as exc:
        raise ParseError(f"not a readable .docx file: {label}: {exc}") from exc
    actor = author or _default_author(who)
    source_ref = "docx-sha256:" + hashlib.sha256(data).hexdigest()
    if changes == "edits" and any(source_ref in (e.get("source") or []) for e in doc.history):
        raise InvalidOperation(
            f"{label} was already imported onto this document (history records {source_ref})"
        )

    # -- X: what was exported --------------------------------------------------------
    mode = manifest.pending if manifest and manifest.pending in _MODES else "tracked"
    base_match = "unknown"
    found = False
    x_body: AimDocument = doc
    if manifest is None:
        warnings.append(
            "no round-trip manifest in the returned file (exported without marks, or the "
            "part was removed): aligning by content against "
            + (f"seq {base_seq}" if base_seq is not None else "the current document")
        )
        if base_seq is not None and base_seq != doc.seq:
            x_body = doc.state_at(base_seq)
    elif manifest.base_doc_hash == doc.doc_hash:
        base_match, found = "exact", True
    else:
        found = any(e.get("doc_hash") == manifest.base_doc_hash for e in doc.history)
        if 0 <= manifest.base_seq <= doc.seq:
            try:
                past = doc.state_at(manifest.base_seq)
            except AimError:
                past = None
            if past is not None and past.doc_hash == manifest.base_doc_hash:
                x_body, base_match, found = past, "advanced", True
        if base_match == "unknown":
            warnings.append(
                "the document this file was exported from cannot be reconstructed "
                "(history pruned or unrelated): changes are compared with the current document"
            )
    keep = set(manifest.cards) if manifest is not None else None
    try:
        x_doc = _with_lane(x_body, doc, keep)
        if keep:
            unrestored = _restore_resolved_cards(x_doc, doc, keep)
            if unrestored:
                warnings.append(
                    f"{unrestored} proposal(s) pending at export were resolved since and "
                    "could not be reconstructed; edits near them may be misread"
                )
        x_view = _view(x_doc, mode)
    except AimError:
        x_doc = _with_lane(x_body, doc, set())
        x_view = x_doc
        warnings.append("the pending lane at export could not be replayed; compared without it")
    xs = _x_units(x_view)
    known = hash_index(xs)

    # current view (drift check): the document now, under the same export rules
    try:
        c_view = _view(_with_lane(doc, doc, keep), mode)
    except AimError:
        c_view = _with_lane(doc, doc, set())
    c_units = _x_units(c_view)
    live = doc._state.all_ids()

    # -- N: the null round trip, recomputed now ---------------------------------
    aligner = _Aligner()
    n_bytes = docx_bytes(x_doc, pending=mode, roundtrip_marks=True)
    n_blocks = convert_docx_marked(n_bytes)
    n_rets = _structure(n_blocks, xs, known)
    _align_all(n_rets, xs, set(), aligner)
    n_of: dict[str, _Ret] = {}
    hidden: set[str] = set()
    for ret in _walk(n_rets):
        if ret.uid is not None and ret.via == "marker":
            n_of[ret.uid] = ret
        hidden.update(ret.merged)
    for uid, x in xs.items():
        if x.kind in ("chunk", "item", "container") and uid not in n_of:
            hidden.add(uid)  # the export → import pipeline itself loses it

    # -- R: the returned file ----------------------------------------------------
    n_xml = zipfile.ZipFile(io.BytesIO(n_bytes)).read("word/document.xml")
    ours_sig = _revision_signature(n_xml)
    ours = {(author, date) for _k, author, date, _t in ours_sig}
    try:
        data, rev_authors = _accept_foreign_revisions(data, ours)
    except (KeyError, ValueError, SyntaxError) as exc:  # no document part; XMLSyntaxError
        raise ParseError(f"not a readable .docx file: {label}: {exc}") from exc
    if rev_authors:
        warnings.append(
            "the returned file has tracked changes by "
            + ", ".join(sorted(rev_authors))
            + "; they were read as accepted and are attributed to this import's author "
            "(per-revision authors are not imported yet)"
        )
    r_xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml")
    if [sig for sig in _revision_signature(r_xml) if (sig[1], sig[2]) in ours] != ours_sig:
        warnings.append(
            "pending proposals exported as tracked changes were edited, accepted or rejected "
            "in Word; that is not imported (resolve them in the document instead)"
        )
    r_rets = _structure(convert_docx_marked(data), xs, known)
    _align_all(r_rets, xs, hidden, aligner)
    _drop_phantoms(r_rets, n_rets)
    if aligner.exhausted:
        warnings.append(
            "content alignment budget exhausted: some units were left unaligned and "
            "show as delete + add"
        )

    report = RevisionImportReport(
        changes=changes, author=actor, base_match=base_match, pending_mode=mode
    )
    visible = [
        u for u, x in xs.items() if x.kind in ("chunk", "item", "container") and u not in hidden
    ]
    report.units = len(visible)
    for ret in _walk(r_rets):
        if ret.uid is not None:
            if ret.via == "marker":
                report.aligned_by_marker += 1
            else:
                report.aligned_by_content += 1
    aligned = report.aligned_by_marker + report.aligned_by_content
    if not found and visible and aligned < _MISMATCH_RATIO * len(visible):
        raise InvalidOperation(
            f"{label} does not look like a revision of this document ({aligned} of "
            f"{len(visible)} units align and its manifest names another document); "
            "import it as a new document instead"
        )

    when = at or _clamped_time(modified, manifest.exported_at if manifest else None)
    name = (
        actor.id[5:] if actor.id and actor.id.startswith("docx:") else (actor.id or "a colleague")
    )
    where = f"{name} in Word ({Path(label).name})"
    plan = _Planner(doc, xs, c_units, live, n_of, manifest, mode, report, where, conflicts, changes)
    ops = plan.run(r_rets)

    if changes == "proposals":
        _emit_proposals(doc, ops, actor, when, report, dry_run)
    else:
        _emit_edits(doc, ops, actor, when, report, dry_run, source_ref)
    report.warnings = warnings
    return report


_MODES = ("tracked", "accept-all", "reject-all")


class _Planner:
    """Turns aligned returned units into ops against the CURRENT document."""

    def __init__(
        self,
        doc: AimDocument,
        xs: dict[str, _X],
        c_units: dict[str, _X],
        live: set[str],
        n_of: dict[str, _Ret],
        manifest: Manifest | None,
        mode: str,
        report: RevisionImportReport,
        where: str,
        conflicts: str,
        changes: str,
    ):
        self.doc = doc
        self.changes = changes
        self.xs = xs
        self.c = c_units
        self.live = live
        self.n_of = n_of
        self.manifest = manifest
        self.mode = mode
        self.report = report
        self.where = where
        self.conflict_mode = conflicts
        self.ops: list[_Op] = []
        self.modify_op: dict[str, int] = {}
        self.taken = doc._taken_ids()
        self.pending = {p.target: p for p in doc.proposals if p.target}
        self.pending_adds = {
            uid: p.id
            for p in doc.proposals
            if p.action == "add"
            for uid in re.findall(r' data-aim(?:-container)?="([^"]+)"', p.payload_html or "")
        }
        self.export_cards = set(manifest.cards) if manifest else set()
        self.x_hash = {u.id: u.x for u in manifest.units} if manifest else {}
        self.whole: set[str] = set()  # containers modified whole
        # units whose change is a conflict and was not planned: a merge's
        # delete or a split's addition coupled to one must not go out alone
        self.refused: set[str] = set()
        # units added to the document since the export, by content: a
        # returned addition equal to one of them is already in (re-import
        # after accepting the first import's cards)
        self.since: dict[tuple[str, str], list[str]] = {}
        for uid, unit in c_units.items():
            if uid not in xs and uid in live and unit.kind in ("chunk", "item", "container"):
                key = (unit.scope, _strip_ids(_canon(unit.serial)))
                self.since.setdefault(key, []).append(uid)

    # -- helpers ------------------------------------------------------------------------
    def conflict(self, uid: str, reason: str, colleague: str = "") -> None:
        x = self.xs.get(uid)
        self.report.conflicts.append(Conflict(uid, reason, x.text if x else "", colleague))

    def stable(self, uid: str) -> bool:
        """The unit is as it was at export (drift check, D9)."""
        if uid not in self.live:
            return False
        c = self.c.get(uid)
        if c is None:
            return False
        if self.manifest is None:
            return c.serial == self.xs[uid].serial
        x = self.x_hash.get(uid)
        if x is None:
            return c.serial == self.xs[uid].serial
        return unit_hash(self.manifest.salt, c.serial) == x

    def pending_rule(self, uid: str) -> str | None:
        """None when a change to *uid* may be proposed; else a conflict reason."""
        card = self.pending.get(uid)
        if card is None:
            return None
        if self.changes == "edits":
            return "a pending proposal targets this unit (resolve it, or import as proposals)"
        if card.id in self.export_cards and self.mode == "accept-all":
            return None  # the colleague saw it applied; their card supersedes it
        if card.id not in self.export_cards:
            return "a proposal on this unit was created after the export"
        if self.mode == "reject-all":
            return "a pending proposal on this unit was not in the exported file"
        return "a pending proposal on this unit was exported as a tracked change"

    def fresh(self, el: Element) -> str:
        if el.container_id is not None:
            cid = ids.new_id(self.taken)
            el.set("data-aim-container", cid)
            for it, _ in _direct_items(el):
                it.set("data-aim", ids.new_id(self.taken))
            return cid
        uid = ids.new_id(self.taken)
        el.set("data-aim", uid)
        return uid

    def explain(self, what: str) -> str:
        text = f"{self.where}: {what}"
        return text if len(text) <= _EXPLAIN_MAX else text[: _EXPLAIN_MAX - 1] + "…"

    # -- classification ---------------------------------------------------------------
    def payload_for(self, ret: _Ret) -> tuple[str | None, str | None]:
        """(payload serial, conflict reason) for a changed aligned unit."""
        uid = ret.uid
        assert uid is not None
        x, n = self.xs[uid], self.n_of[uid]
        lossy = x.slide is not None or _struct(x.els) != _struct(n.els)
        rebased = _rebase(x.els, n.els, ret.els)
        if rebased is not None:
            self.report.rebased += 1
            return "".join(serialize(e) for e in rebased), None
        if lossy:
            return (
                None,
                "change not representable through DOCX "
                "(the unit has structure DOCX does not carry)",
            )
        if ret.kind == "container":
            return None, None  # handled by the container logic
        if not _carried(x.els, n.els):
            # taking the returned markup would drop what the export could
            # not show the colleague (a link's target, a highlight colour)
            return (
                None,
                "change not representable through DOCX "
                "(the unit has markup DOCX does not carry; only text edits apply)",
            )
        out = []
        for i, el in enumerate(ret.els):
            clone = _scrub(el)
            clone.set("data-aim", uid)
            # restore root class tokens the export could not carry
            if i < len(x.els) and i < len(n.els):
                b_cls = set((x.els[i].get("class") or "").split())
                n_cls = set((n.els[i].get("class") or "").split())
                r_cls = set((clone.get("class") or "").split())
                restore = (b_cls - n_cls) - r_cls
                if restore:
                    clone.set("class", " ".join(sorted(r_cls | restore)))
            out.append(serialize(clone))
        return "".join(out), None

    def changed(self, ret: _Ret) -> bool:
        uid = ret.uid
        assert uid is not None
        n = self.n_of.get(uid)
        if n is None:
            return False
        if ret.kind == "container":
            return _skeleton_norm(ret.els[0]) != _skeleton_norm(n.els[0])
        same = _norm(ret.els) == _norm(n.els)
        if same and serialize(_scrub(ret.els[0])) != serialize(_scrub(n.els[0])):
            self.report.noise_suppressed += 1
        return not same

    # -- the walk -----------------------------------------------------------------------
    def run(self, rets: list[_Ret]) -> list[_Op]:
        seen_body = {r.uid for r in rets if r.uid}
        self._modifies(rets)
        self._deletes(rets, seen_body)
        self._order("body", rets, None)
        for ret in rets:
            if (
                ret.kind == "container"
                and ret.uid
                and ret.uid in self.live
                and ret.uid not in self.whole
            ):
                self._order(ret.uid, ret.items, ret)
        return self.ops

    def _modifies(self, rets: list[_Ret]) -> None:
        for ret in rets:
            if ret.uid is None:
                continue
            if ret.kind == "container":
                if self.changed(ret):
                    self._whole_container(ret)
                    continue
                for item in ret.items:
                    if item.uid is not None:
                        self._modify(item)
                continue
            self._modify(ret)

    def _modify(self, ret: _Ret) -> None:
        uid = ret.uid
        assert uid is not None
        if not self.changed(ret) and not ret.merged:
            self.report.unchanged += 1
            return
        payload, reason = self.payload_for(ret)
        if reason is None and payload is None:
            return
        current = self.doc._state.serial(uid) if uid in self.live else None
        if payload is not None and current is not None and _canon(current) == _canon(payload):
            self.report.unchanged += 1  # already in the document (accepted since, say)
            return
        card = self.pending.get(uid)
        if (
            payload is not None
            and card is not None
            and card.action == "modify"
            and _canon(card.payload_html or "") == _canon(payload)
        ):
            self.report.already_pending.append(card.id)  # re-import: nothing new
            return
        if reason is None and uid not in self.live:
            reason = "the unit is not in the current document (deleted, or a pending addition)"
        if reason is None and not self.stable(uid):
            reason = "the unit changed in the document since export"
        if reason is None:
            reason = self.pending_rule(uid)
        if reason is not None:
            self.conflict(uid, reason, ret.text)
            if self.conflict_mode != "propose" or payload is None or uid not in self.live:
                self.refused.add(uid)
                return
        assert payload is not None
        before = self.xs[uid].text
        what = f'"{_quote(before)}" → "{_quote(ret.text)}"'
        if ret.merged:
            what = f"merged {len(ret.merged)} paragraph(s) into this one; " + what
        if reason is not None:
            what = (
                "conflicts with an edit made after export; "
                if "since export" in reason
                else "conflicts with a pending proposal; "
            ) + what
        self.modify_op[uid] = len(self.ops)
        self.ops.append(_Op("modify", uid, payload, explanation=self.explain(what)))
        self.report.modified.append(uid)

    def _whole_container(self, ret: _Ret) -> None:
        uid = ret.uid
        assert uid is not None
        reason = None
        if uid not in self.live:
            reason = "the container is not in the current document"
        elif not self.stable(uid):
            reason = "the container changed in the document since export"
        else:
            reason = self.pending_rule(uid)
        if reason is not None:
            self.conflict(uid, reason, ret.text)
            return
        el = _scrub(ret.els[0])
        el.set("data-aim-container", uid)
        c_items = {u: x for u, x in self.c.items() if x.kind == "item" and x.scope == uid}
        owner = {id(member): item for item in ret.items for member in item.els}
        originals = [it for it, _ in _direct_items(ret.els[0])]
        for original, (it, _shell) in zip(originals, _direct_items(el), strict=False):
            item = owner.get(id(original))
            replacement: str | None = None
            if item is not None and item.uid in c_items and item.uid is not None:
                if not self.changed(item):
                    replacement = c_items[item.uid].serial  # keeps its exact markup
                else:
                    payload, reason = self.payload_for(item)
                    if reason is not None:
                        # taking the returned item would write the markup
                        # the item guard refuses (a link's target, lossy
                        # structure): the container change is a conflict
                        self.conflict(item.uid, reason, item.text)
                        self.conflict(uid, f"item {item.uid}: {reason}", ret.text)
                        return
                    replacement = payload
                if replacement is None:
                    it.set("data-aim", item.uid)
                else:
                    first = next(n for n in parse_fragment(replacement) if isinstance(n, Element))
                    it.tag, it.attrs, it.children = first.tag, first.attrs, first.children
            else:
                it.set("data-aim", ids.new_id(self.taken))
        self.whole.add(uid)
        self.modify_op[uid] = len(self.ops)
        what = f"changed the {self.xs[uid].tag} container"
        self.ops.append(_Op("modify", uid, serialize(el), explanation=self.explain(what)))
        self.report.modified.append(uid)

    def _deletes(self, rets: list[_Ret], seen_body: set[Any]) -> None:
        seen = {r.uid for r in _walk(rets) if r.uid}
        merged_into = {m: r.uid for r in _walk(rets) for m in r.merged if r.uid}
        for uid, x in self.xs.items():
            if x.kind not in ("chunk", "item", "container") or uid in seen:
                continue
            if uid not in self.n_of:
                continue  # hidden: the pipeline itself loses it
            if x.kind == "item" and (x.scope not in seen or x.scope in self.whole):
                continue  # covered by the container's own delete / whole modify
            if uid not in self.live:
                adder = self.pending_adds.get(uid)
                if adder is not None:  # the export showed a pending addition applied
                    self.conflict(
                        uid,
                        "the colleague deleted a pending addition; "
                        f"reject proposal {adder} instead",
                    )
                continue  # already gone
            reason = None
            if x.slide is not None:
                reason = "deleting part of a slide is not representable through DOCX"
            elif not self.stable(uid):
                reason = "the unit changed in the document since export"
            else:
                card = self.pending.get(uid)
                if card is not None and card.action == "delete":
                    self.report.already_pending.append(card.id)
                    continue
                reason = self.pending_rule(uid)
            if reason is not None:
                self.conflict(uid, reason)
                continue
            survivor = merged_into.get(uid)
            if survivor in self.refused:
                # deleting it alone would lose its text: the survivor that
                # was to carry it is a conflict and stays as it is
                self.conflict(
                    uid, f"merged into {survivor}, whose change is a conflict; not deleted"
                )
                continue
            depends = self.modify_op.get(survivor) if survivor else None
            what = (
                f'merged into the previous paragraph: "{_quote(x.text)}"'
                if survivor
                else f'deleted "{_quote(x.text)}"'
            )
            self.ops.append(_Op("delete", uid, depends=depends, explanation=self.explain(what)))
            self.report.deleted.append(uid)

    def _order(self, scope: str, seq: list[_Ret], container: _Ret | None) -> None:
        """Adds and moves within one scope, in returned order."""
        # three-way: a unit the COLLEAGUE moved is one out of order against
        # the EXPORTED order (x), not against the current one — a move made
        # in the document since the export is not theirs to undo
        x_order = {u: i for i, u in enumerate(self.xs)}
        stable_ids = [
            r.uid
            for r in seq
            if r.uid
            and r.uid in self.live
            and r.uid in self.c
            and self.xs[r.uid].slide is None
            and (self.c[r.uid].scope == scope)
        ]
        keep = _lis(stable_ids, x_order)
        prev: tuple[str, Any] | None = None
        prev_ret: _Ret | None = None
        # the slide the next aligned unit belongs to (adds between two units
        # of one slide would land inside a fixed canvas DOCX never showed)
        next_slide: list[str | None] = [None] * len(seq)
        # …and the table section (thead/tbody/tfoot) of the next placed row:
        # the export draws every row in one table, so a returned row's own
        # section says nothing; the document's rows around it do
        next_shell: list[str | None] = [None] * len(seq)
        upcoming: str | None = None
        upcoming_shell: str | None = None
        for k in range(len(seq) - 1, -1, -1):
            next_slide[k] = upcoming
            next_shell[k] = upcoming_shell
            uid_k = seq[k].uid
            if uid_k is not None:
                upcoming = self.xs[uid_k].slide
                if uid_k in self.c and uid_k in self.live and self.c[uid_k].scope == scope:
                    upcoming_shell = self.c[uid_k].shell
        shell_here: str | None = None
        for k, ret in enumerate(seq):
            if ret.phantom:
                continue
            if container is not None:
                uid_k = ret.uid
                placed = (
                    uid_k is not None
                    and uid_k in self.c
                    and uid_k in self.live
                    and self.c[uid_k].scope == scope
                )
                shell_k = self.c[uid_k].shell if placed and uid_k else next_shell[k]
                if shell_k is not None and shell_k != shell_here:
                    # a section boundary: a row before the first body row is
                    # the first body row, not "after" the last header row
                    # (that would land it in <thead>)
                    shell_here, prev = shell_k, None
                ret.shell = shell_here
            if ret.uid is None:
                if ret.kind == "container" and scope != "body":
                    prev_ret = ret
                    continue
                slide = (
                    self.xs[prev_ret.uid].slide
                    if prev_ret is not None and prev_ret.uid is not None
                    else None
                )
                if slide is not None and next_slide[k] == slide:
                    self.report.conflicts.append(
                        Conflict(
                            slide,
                            "an addition inside a slide is not representable through DOCX",
                            colleague_text=ret.text,
                        )
                    )
                    continue
                for el_src in ret.els:
                    prev = self._add(ret, el_src, scope, prev, prev_ret)
                prev_ret = ret
                continue
            uid = ret.uid
            x = self.xs[uid]
            if uid not in self.live or uid not in self.c:
                prev_ret = ret
                continue
            if x.slide is not None:
                prev = ("unit", x.slide) if scope == "body" else prev
                prev_ret = ret
                continue
            if self.c[uid].scope != scope:
                prev_ret = ret
                continue
            if uid not in keep and not self._already_at(uid, scope, prev):
                what = f'moved "{_quote(x.text)}"'
                self.ops.append(
                    _Op(
                        "move",
                        uid,
                        scope=scope,
                        shell=ret.shell if prev is None else None,
                        after=prev,
                        explanation=self.explain(what),
                    )
                )
                self.report.moved.append(uid)
            prev = ("unit", uid)
            prev_ret = ret

    def _already_at(self, uid: str, scope: str, prev: tuple[str, Any] | None) -> bool:
        """The unit already sits where the returned file puts it (a move
        accepted since, say): nothing to propose."""
        if prev is not None and prev[0] != "unit":
            return False
        try:
            cur = self.doc._anchor_of(uid)
        except AimError:
            return False
        return cur.container == scope and cur.after == (prev[1] if prev else None)

    def _add(
        self,
        ret: _Ret,
        source: Element,
        scope: str,
        prev: tuple[str, Any] | None,
        prev_ret: _Ret | None,
    ) -> tuple[str, Any] | None:
        el = _scrub(source)
        if el.tag in ("ul", "ol", "table") and ret.kind == "container":
            for it, _ in _direct_items(el):
                it.set("data-aim", "")
            el.set("data-aim-container", "")
        # already in only where the returned file puts it: the same content
        # added elsewhere since the export is someone else's paragraph
        twins = self.since.get((scope, _strip_ids(serialize(el)))) or []
        twin = next((t for t in twins if self._already_at(t, scope, prev)), None)
        if twin is not None:
            twins.remove(twin)
            self.report.unchanged += 1
            return ("unit", twin)
        text = _plain([source])
        head = (
            prev_ret.uid
            if len(ret.els) == 1
            and prev_ret is not None
            and prev_ret.uid is not None
            and prev_ret.uid in self.xs
            and _plain(self.xs[prev_ret.uid].els).replace(" ", "")
            == (prev_ret.text + ret.text).replace(" ", "")
            else None
        )
        if head is not None and head in self.refused:
            # the head keeps the whole text: adding the tail would repeat it
            self.conflict(
                head,
                "split, but the change to its first part is a conflict; "
                "the second part was not added",
                ret.text,
            )
            return prev
        new_id = self.fresh(el)
        depends = None
        what = f'added "{_quote(text)}"'
        if head is not None and head in self.modify_op:
            depends = self.modify_op[head]
            what = f'split of the previous paragraph: "{_quote(text)}"'
        self.ops.append(
            _Op(
                "add",
                new_id,
                serialize(el),
                scope=scope,
                shell=ret.shell if prev is None else None,
                after=prev,
                depends=depends,
                explanation=self.explain(what),
            )
        )
        self.report.added.append(new_id)
        return ("new", len(self.ops) - 1)


def _skeleton_norm(el: Element) -> str:
    from .reconcile import _skeleton

    return _skeleton(_scrub(el))


def _lis(seq: list[str], order: dict[str, int]) -> set[str]:
    vals = [order[u] for u in seq]
    tails: list[int] = []
    idx: list[int] = []
    prevs = [-1] * len(vals)
    for i, v in enumerate(vals):
        j = bisect.bisect_left(tails, v)
        if j == len(tails):
            tails.append(v)
            idx.append(i)
        else:
            tails[j] = v
            idx[j] = i
        prevs[i] = idx[j - 1] if j else -1
    out: set[str] = set()
    k = idx[-1] if idx else -1
    while k >= 0:
        out.add(seq[k])
        k = prevs[k]
    return out


# =============================================================================
# emission


def _canon(markup: str) -> str:
    return "".join(serialize(n) for n in parse_fragment(markup) if isinstance(n, Element))


def _strip_ids(markup: str) -> str:
    return re.sub(r' data-aim(?:-container)?="[^"]*"', "", markup)


def _commit(doc: AimDocument, work: AimDocument) -> None:
    burned = set(work._get_history_index().burned_ids)
    doc._fragment = work._fragment
    doc._state = work._state
    doc._batch = None
    doc._rebuild_history_index(burned_seed=burned)


def _emit_proposals(
    doc: AimDocument,
    ops: list[_Op],
    actor: Actor,
    when: str,
    report: RevisionImportReport,
    dry_run: bool,
) -> None:
    work = doc._clone()
    pending = list(work.proposals)
    card_of: dict[int, str] = {}

    def same_card(op: _Op, after: str | None) -> Proposal | None:
        for p in pending:
            if op.kind == "modify" and p.action == "modify" and p.target == op.uid:
                if _canon(p.payload_html or "") == _canon(op.payload or ""):
                    return p
            elif op.kind == "delete" and p.action == "delete" and p.target == op.uid:
                return p
            elif op.kind == "move" and p.action == "move" and p.target == op.uid:
                if (p.anchor_container or "body") == op.scope and p.anchor_after == after:
                    return p
            elif op.kind == "add" and p.action == "add":
                if (
                    (p.anchor_container or "body") == op.scope
                    and p.anchor_after == after
                    and _strip_ids(p.payload_html or "") == _strip_ids(op.payload or "")
                ):
                    return p
        return None

    def anchor(op: _Op) -> str | None:
        if op.after is None:
            return None
        kind, ref = op.after
        if kind == "unit":
            return str(ref)
        return card_of.get(ref)

    def drop(i: int, op: _Op, exc: Exception) -> None:
        report.conflicts.append(Conflict(op.uid, f"not proposed: {exc}"))
        for bucket in (report.modified, report.deleted, report.added, report.moved):
            if op.uid in bucket:
                bucket.remove(op.uid)

    # modifies first: a merge's delete and a split's add depend on them
    order = (
        [i for i, op in enumerate(ops) if op.kind == "modify"]
        + [i for i, op in enumerate(ops) if op.kind == "delete"][::-1]
        + [i for i, op in enumerate(ops) if op.kind in ("add", "move")]
    )
    with work.batch():
        for i in order:
            op = ops[i]
            after = anchor(op)
            if op.after is not None and op.after[0] == "new" and after is None:
                drop(i, op, InvalidOperation("its anchor was not proposed"))
                continue
            existing = same_card(op, after)
            if existing is not None:
                report.already_pending.append(existing.id)
                card_of[i] = existing.id
                for bucket in (report.modified, report.deleted, report.added, report.moved):
                    if op.uid in bucket:
                        bucket.remove(op.uid)
                continue
            if (
                op.kind == "add"
                and after is None
                and op.shell is not None
                and op.shell != _default_shell(work, op.scope)
            ):
                # propose_add places a first row in the table's default
                # section; a new first <thead>/<tfoot> row would land elsewhere
                drop(i, op, InvalidOperation(f"a new first row of <{op.shell}> cannot be proposed"))
                continue
            if op.depends is not None and op.depends not in card_of:
                # a merge's delete or a split's addition without its head
                # would lose or repeat text
                drop(i, op, InvalidOperation("the change it is part of was not proposed"))
                continue
            depends = card_of.get(op.depends) if op.depends is not None else None
            superseded = [
                p.id
                for p in work.proposals
                if p.target == op.uid
                and (p.action in ("modify", "delete") if op.kind != "move" else p.action == "move")
            ]
            try:
                if op.kind == "modify":
                    card = work.propose_modify(
                        op.uid,
                        op.payload or "",
                        author=actor,
                        explanation=op.explanation,
                        depends_on=depends,
                        at=when,
                    )
                elif op.kind == "delete":
                    card = work.propose_delete(
                        op.uid,
                        author=actor,
                        explanation=op.explanation,
                        depends_on=depends,
                        at=when,
                    )
                elif op.kind == "add":
                    card = work.propose_add(
                        op.payload or "",
                        author=actor,
                        container=op.scope,
                        after=after,
                        explanation=op.explanation,
                        depends_on=depends,
                        at=when,
                    )
                else:
                    card = work.propose_move(
                        op.uid,
                        author=actor,
                        container=op.scope,
                        after=after,
                        shell=op.shell if after is None else None,
                        explanation=op.explanation,
                        at=when,
                    )
            except AimError as exc:
                drop(i, op, exc)
                continue
            card_of[i] = card.id
            report.proposals.append(card.id)
            report.superseded += [p for p in superseded if p not in report.superseded]
    if report.proposals and not dry_run:
        _commit(doc, work)


def _default_shell(doc: AimDocument, container: str) -> str | None:
    from .registry import REGISTRY

    cont = doc._state.container_node(container) if container != "body" else None
    if cont is None or cont.tag != "table":
        return None
    shells = [s.tag for s in cont.elements() if s.tag in REGISTRY.table_shells]
    return "tbody" if "tbody" in shells else (shells[0] if shells else None)


def _emit_edits(
    doc: AimDocument,
    ops: list[_Op],
    actor: Actor,
    when: str,
    report: RevisionImportReport,
    dry_run: bool,
    source_ref: str,
) -> None:
    from .document import Anchor
    from .errors import HistoryError
    from .reconcile import _clone, _drive

    work = _clone(doc)  # becomes the target body A'
    state = work._state
    new_root: dict[int, str] = {}
    for op in ops:
        if op.kind == "delete" and state.exists(op.uid):
            state.remove(op.uid)
        elif op.kind == "modify":
            state.replace(op.uid, op.payload or "")
    for i, op in enumerate(ops):
        if op.kind not in ("add", "move"):
            continue
        after: str | None = None
        if op.after is not None:
            after = op.after[1] if op.after[0] == "unit" else new_root.get(op.after[1])
        to = Anchor(op.scope, after, shell=op.shell if after is None else None)
        if op.kind == "add":
            state.insert(op.payload or "", to)
            new_root[i] = op.uid
        else:
            state.move(op.uid, to)
    S = _clone(doc)
    n0 = len(S.history)
    with S.batch():
        _drive(S, work, actor, when, source=[source_ref])
    if S._state.doc_hash() != work._state.doc_hash():
        raise HistoryError("revision import did not converge — this is a bug in aimformat")
    if S.proposals:
        # direct edits must leave the pending lane resolvable (a pending add
        # anchored on a paragraph the colleague deleted, say)
        trial = S._clone()
        try:
            trial.accept_all(decided_by=actor, at=when)
        except AimError as exc:
            raise InvalidOperation(
                "applying these changes directly would break pending proposals "
                f"({exc}); import them as proposals instead"
            ) from exc
    report.events = S.history[n0:]
    if report.events and not dry_run:
        _commit(doc, S)
