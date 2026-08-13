"""Unit-level diff and reload-divergence classification between versions.

Any consumer that follows a ``.aim`` file on disk — an editor pane, a
watcher, a CI bot — hits the same two questions when the file changes under
it: *what* changed, at the format's own granularity (addressable units, not
text lines), and *whether the file's own history explains it*. The second
answer decides what "undo" means after a reload: new proposals leave the
body untouched, new events make :meth:`AimDocument.undo` correct, and a raw
text write (legal, spec §6.8) must first be adopted into history via
:meth:`AimDocument.reconcile` — undoing before adopting would revert the
wrong, older event.

Unit identity and serialization reuse the reconcile machinery
(:func:`aimformat.reconcile._units`), so diff and reconcile can never
disagree about what a unit is. Containers compare by *skeleton* (open tag +
table shells + stray interior text): a member edit marks the member, not
every ancestor container.
"""

from __future__ import annotations

import bisect
from copy import deepcopy
from dataclasses import dataclass

from .canonical import document_text, serialize
from .document import AimDocument
from .dom import Element
from .errors import AimError
from .events import Event
from .reconcile import _skeleton, _units
from .registry import REGISTRY

__all__ = ["Divergence", "DocumentDiff", "classify_divergence", "diff_documents"]


@dataclass(frozen=True)
class DocumentDiff:
    """Unit-level differences between two parsed versions of a document.

    Ids are addressable units (chunks, container items grouped as runs,
    containers). ``added`` / ``modified`` / ``moved`` follow *new*'s document
    order; ``deleted`` follows *old*'s. A unit whose container moved is not
    itself moved — it rode along, and the container's id carries the change.
    """

    added: tuple[str, ...]
    deleted: tuple[str, ...]
    modified: tuple[str, ...]
    moved: tuple[str, ...]
    theme_changed: bool
    doc_settings_changed: bool
    version_changed: bool
    # Ids present in *new* that a viewer should mark: added ∪ modified ∪
    # moved, in *new*'s DOCUMENT order (a unit can be in more than one
    # category). A field, not a property: the categories are each ordered
    # subsequences, but their concatenation is not — only the constructor
    # holds the full new-order to merge against (codex #35).
    changed_ids: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(
            self.added
            or self.deleted
            or self.modified
            or self.moved
            or self.theme_changed
            or self.doc_settings_changed
            or self.version_changed
        )

    def to_obj(self) -> dict:
        """A JSON-ready projection (CLI ``--format json``, wire payloads)."""
        return {
            "added": list(self.added),
            "deleted": list(self.deleted),
            "modified": list(self.modified),
            "moved": list(self.moved),
            # the merged new-document ordering is not recoverable from the
            # category arrays alone — wire consumers highlighting in order
            # need it carried, not re-derived (codex #35 round 2)
            "changed_ids": list(self.changed_ids),
            "theme_changed": self.theme_changed,
            "doc_settings_changed": self.doc_settings_changed,
            "version_changed": self.version_changed,
        }


@dataclass(frozen=True)
class Divergence:
    """How *new* departs from *old*, for a consumer that held *old*.

    ``new_events`` is the appended suffix when old's log is a prefix of
    new's; ``history_rewritten`` says it is not (flatten, prune, or a hand
    rewrite — adopt the new file wholesale, no undo mapping survives).
    ``content_drift`` says new's hashed content is not explained by its own
    history relative to old — the raw-write case reconcile adopts.
    """

    changed: bool
    new_events: tuple[Event, ...]
    history_rewritten: bool
    new_proposals: tuple[str, ...]
    removed_proposals: tuple[str, ...]
    content_drift: bool

    @property
    def explained(self) -> bool:
        """Whether history accounts for every body change (no adoption
        needed): nothing drifted and the log was only appended to."""
        return not (self.content_drift or self.history_rewritten)


def _stable_ids(old_order: list[str], new_order: list[str]) -> set[str]:
    """The longest subsequence present in the same relative order in both.

    Ids are unique within a scope, so the LCS reduces to the longest
    increasing subsequence of old positions in new order (patience sorting,
    O(n log n)). Everything outside it moved.
    """
    pos = {uid: i for i, uid in enumerate(old_order)}
    seq = [pos[uid] for uid in new_order]
    tail_vals: list[int] = []  # smallest tail value of an LIS of each length
    tail_idx: list[int] = []  # index into seq of that tail
    parent: list[int] = [-1] * len(seq)
    for i, value in enumerate(seq):
        j = bisect.bisect_left(tail_vals, value)
        if j == len(tail_vals):
            tail_vals.append(value)
            tail_idx.append(i)
        else:
            tail_vals[j] = value
            tail_idx[j] = i
        parent[i] = tail_idx[j - 1] if j > 0 else -1
    stable: set[str] = set()
    if tail_idx:
        i = tail_idx[-1]
        while i != -1:
            stable.add(new_order[i])
            i = parent[i]
    return stable


def _container_skeletons(doc: AimDocument) -> dict[str, str]:
    """Every container's skeleton, collected in ONE tree walk.

    A per-uid ``container_node`` lookup restarts the walk from the top, so
    comparing N containers cost O(N * tree) — ~2.8 s just to say "no
    changes" on a flat 1,200-container document (codex #35). One pass over
    the constructs makes the whole comparison linear.
    """
    out: dict[str, str] = {}
    for construct in doc._state.constructs():
        for el in construct.iter():
            if isinstance(el, Element) and el.container_id is not None:
                out[el.container_id] = _skeleton(el)
    return out


def _optional_serial(el: Element | None) -> str | None:
    return serialize(el) if el is not None else None


#: appended events must carry a registry-known kind to count as explained
_KNOWN_KINDS = frozenset(REGISTRY.raw["events"]["kinds"])


def _lane_diff(
    old_pids: list[str] | None, new_pids: list[str] | None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """new/removed proposal-id tuples; an UNREADABLE side makes no claims
    in either direction (unreadable is not empty — codex #35 round 4)."""
    if old_pids is None or new_pids is None:
        return (), ()
    old_set, new_set = set(old_pids), set(new_pids)
    return (
        tuple(p for p in new_pids if p not in old_set),
        tuple(p for p in old_pids if p not in new_set),
    )


def _proposal_ids(doc: AimDocument) -> list[str] | None:
    """Pending-lane ids, hostile-input-safe; ``None`` when the lane cannot
    be read at all. The lane of an EXTERNALLY edited file is untrusted: a
    malformed card (bogus ``data-author``, broken attributes) raises from
    the lazy proposal parse, and a reload classifier must degrade rather
    than crash the consumer (codex #35 round 3). Unreadable is NOT empty:
    collapsing it to [] reported every still-valid proposal as removed,
    and an editor acting on removed_proposals dismissed real pending cards
    (round 4). The content comparison still drives drift either way."""
    try:
        return [p.id for p in doc.proposals]
    except Exception:
        return None


def diff_documents(old: AimDocument, new: AimDocument) -> DocumentDiff:
    """Unit-level diff between two parsed versions of a document.

    The versions need not share a history — this compares bodies (plus the
    theme, settings, and declared-version scalars), never the log or the
    pending lane. Use :func:`classify_divergence` for those.
    """
    old_units = _units(old._state)
    new_units = _units(new._state)
    survivors = {uid for uid in new_units if uid in old_units}

    added = tuple(uid for uid in new_units if uid not in old_units)
    deleted = tuple(uid for uid in old_units if uid not in new_units)

    old_skeletons = _container_skeletons(old)
    new_skeletons = _container_skeletons(new)
    modified: list[str] = []
    for uid in new_units:
        if uid not in survivors:
            continue
        ou, nu = old_units[uid], new_units[uid]
        if ou.is_container != nu.is_container:
            modified.append(uid)  # a kind flip is a rewrite of the unit
        elif nu.is_container:
            if old_skeletons.get(uid) != new_skeletons.get(uid):
                modified.append(uid)
        elif ou.serial != nu.serial:
            modified.append(uid)

    moved: set[str] = {
        uid
        for uid in survivors
        if (old_units[uid].scope, old_units[uid].shell)
        != (new_units[uid].scope, new_units[uid].shell)
    }
    # Within-scope reorders: survivors that kept their scope, per scope, in
    # document order on both sides; ids outside the longest stable
    # subsequence moved. Cross-scope movers are excluded so membership of
    # the per-scope sequences matches on both sides.
    old_by_scope: dict[tuple[str, str | None], list[str]] = {}
    new_by_scope: dict[tuple[str, str | None], list[str]] = {}
    for units, by_scope in ((old_units, old_by_scope), (new_units, new_by_scope)):
        for uid, unit in units.items():
            if uid in survivors and uid not in moved:
                by_scope.setdefault((unit.scope, unit.shell), []).append(uid)
    for key, new_order in new_by_scope.items():
        old_order = old_by_scope.get(key, [])
        if old_order != new_order:
            moved |= set(new_order) - _stable_ids(old_order, new_order)

    changed_set = set(added) | set(modified) | moved
    return DocumentDiff(
        added=added,
        deleted=deleted,
        modified=tuple(modified),
        moved=tuple(uid for uid in new_units if uid in moved),
        changed_ids=tuple(uid for uid in new_units if uid in changed_set),
        theme_changed=(
            _optional_serial(old._state.theme_el()) != _optional_serial(new._state.theme_el())
        ),
        doc_settings_changed=(
            _optional_serial(old._state.script("doc")) != _optional_serial(new._state.script("doc"))
        ),
        version_changed=old._state.spec_version() != new._state.spec_version(),
    )


def classify_divergence(old: AimDocument, new: AimDocument) -> Divergence:
    """Classify how *new* departs from *old* (two parses of one document).

    Precondition: *old* is a version the caller held consistent (its body
    matched its history — the state a well-behaved writer saves). The
    classification then answers, without guessing: did the pending lane
    change, did the log grow (or get rewritten), and is the body explained
    by the log (``content_drift`` when not — the reconcile case)?

    Drift detection compares hashed content (spec §11.3: the ``<html>`` open
    line, theme, body constructs, settings): with no new events it is a
    doc_hash comparison, with new events the new log is replayed back to
    old's seq and must reproduce old's hash. A replay that fails counts as
    drift — the log cannot account for the body.
    """
    try:
        old_events = old._history_events()
        new_events_all = new._history_events()
    except Exception:
        # History parsing is lazy: loads() succeeds on a file whose appended
        # log line is not even JSON (HistoryError), or whose lane carries a
        # mangled actor the index build trips over (ValueError) — hostile
        # bytes surface HERE, not at parse (codex #35 rounds 2+3). Same
        # categorical boundary as the replay below: an unreadable log
        # cannot account for the body — drift, never a crash in the reload
        # consumer.
        new_p, removed_p = _lane_diff(_proposal_ids(old), _proposal_ids(new))
        return Divergence(
            changed=document_text(old._fragment) != document_text(new._fragment),
            new_events=(),
            history_rewritten=False,
            new_proposals=new_p,
            removed_proposals=removed_p,
            content_drift=True,
        )
    old_lines = [event.to_json() for event in old_events]
    new_lines = [event.to_json() for event in new_events_all]
    history_rewritten = new_lines[: len(old_lines)] != old_lines
    appended = (
        tuple(Event(deepcopy(event.data)) for event in new_events_all[len(old_lines) :])
        if not history_rewritten
        else ()
    )
    # Every appended event must FULLY validate, not merely carry a known
    # kind: a registry-known `checkpoint` missing its required doc_hash
    # still passed a kind check, and state_at skipped it as
    # non-state-changing, so the replay reproduced old's hash and marked
    # the corrupt suffix explained (codex #35 rounds 4+5). Run the SDK's own
    # Event.validate() — any failure means the suffix is untrustworthy:
    # drift, and its events are never surfaced.
    def _event_valid(e: Event) -> bool:
        if not isinstance(e.data, dict) or e.data.get("kind") not in _KNOWN_KINDS:
            return False
        try:
            return not e.validate()
        except Exception:
            return False

    suffix_invalid = any(not _event_valid(e) for e in appended)
    if suffix_invalid:
        appended = ()

    new_p, removed_p = _lane_diff(_proposal_ids(old), _proposal_ids(new))

    if suffix_invalid:
        content_drift = True
    elif history_rewritten:
        # No shared log to judge against; the caller adopts wholesale.
        content_drift = False
    elif not appended:
        content_drift = old.doc_hash != new.doc_hash
    else:
        try:
            content_drift = new.state_at(old.seq).doc_hash != old.doc_hash
        except Exception:
            # Replaying HOSTILE appended events (a hand-edited log) can
            # raise nearly anything — missing "kind" (KeyError), a bool
            # where markup belongs (AttributeError), and whatever shape
            # comes next; two rounds of enumerating exception types each
            # missed one (codex #35 rounds 1+3). The boundary is
            # categorical: a replay that does not complete is a log that
            # cannot account for the body — drift, never a crash. A
            # genuine SDK bug lands on the safe side (adopt-and-attribute)
            # rather than taking the reload consumer down.
            content_drift = True

    return Divergence(
        changed=document_text(old._fragment) != document_text(new._fragment),
        new_events=appended,
        history_rewritten=history_rewritten,
        new_proposals=new_p,
        removed_proposals=removed_p,
        content_drift=content_drift,
    )
