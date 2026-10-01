"""The DOCX round-trip convention: identity markers and the export manifest.

Documented informatively in ``docs/interop/docx-roundtrip.md`` and versioned
by :data:`NS`. Both directions read it from here, so the exporter that writes
a mark and the importer that reads it can never disagree about its spelling.

**Markers** are collapsed (point) bookmarks, one per exported paragraph of a
unit (a top-level chunk, a container item, or a container):

- ``_aim_<id>`` on the unit's first paragraph;
- ``_aim<k>_<id>`` on its k-th paragraph (k >= 2) — only used to regroup a
  chunk the exporter flattened into several paragraphs;
- ``_aimh_<12 hex>`` / ``_aimh<k>_<12 hex>`` when the id is not
  bookmark-safe; readers resolve the hash against the ids they know.

Leading-underscore bookmarks are Word's *hidden* bookmarks: invisible unless
the reader ticks "Hidden bookmarks". Markers are hints, never identity: the
import declares identity through the events or cards it writes (spec §4.5).

**The manifest** is one custom XML part whose root is
``<aim:roundtrip xmlns:aim="urn:aimformat:docx-roundtrip:1">``. It carries no
document text: the base ``doc_hash`` and ``seq``, the export's pending mode,
the ids of the pending cards at export, a random salt, and one
``<aim:u id scope shell x>`` per unit whose ``x`` is a salted, truncated
SHA-256 of the exported unit. What that still discloses is stated in the
interop document.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, field

from .canonical import escape_attr

__all__ = [
    "MANIFEST_MAX_BYTES",
    "NS",
    "Manifest",
    "bookmark_name",
    "manifest_xml",
    "parse_bookmark_name",
    "parse_manifest",
    "resolve_marker",
    "unit_hash",
]

#: The convention's namespace; its trailing number is its version.
NS = "urn:aimformat:docx-roundtrip:1"
#: Readers refuse a larger manifest part (it is data from an untrusted file).
MANIFEST_MAX_BYTES = 1 << 20

#: Ids that can ride a bookmark name verbatim. Word allows 40 characters from
#: ``[A-Za-z0-9_]``; ``_aim<k>_`` + 32 leaves room for a three-digit ordinal
#: (a longer name, k >= 1000 on a 32-character id, falls back to the hash).
_SAFE_ID = re.compile(r"^[a-z0-9_]{1,32}$")
_NAME_MAX = 40
#: One anchored grammar for every marker: digits before the separator are the
#: ordinal, everything after it is the id (or its hash). LibreOffice's
#: "<name> Copy 1" duplicates fail the id class and are ignored.
_MARKER = re.compile(r"^_aim(h?)([0-9]*)_([A-Za-z0-9_-]{1,64})$")
_HASH_LEN = 12


def _hashed(uid: str) -> str:
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:_HASH_LEN]


def bookmark_name(uid: str, ordinal: int = 1) -> str:
    """The marker for the *ordinal*-th paragraph of unit *uid*."""
    k = "" if ordinal <= 1 else str(ordinal)
    if _SAFE_ID.match(uid) and len(name := f"_aim{k}_{uid}") <= _NAME_MAX:
        return name
    return f"_aimh{k}_{_hashed(uid)}"


@dataclass(frozen=True)
class Marker:
    """A parsed marker: which paragraph (``ordinal``) of which unit."""

    key: str  # the id, or its 12-hex hash when ``hashed``
    ordinal: int
    hashed: bool


def parse_bookmark_name(name: str | None) -> Marker | None:
    """Parse a bookmark name; ``None`` for anything that is not a marker."""
    m = _MARKER.match(name or "")
    if m is None:
        return None
    hashed, digits, key = m.groups()
    ordinal = int(digits) if digits else 1
    if ordinal < 1 or (digits and ordinal < 2):
        return None  # "_aim1_x" / "_aim0_x" are not spellings we write
    if hashed and not re.fullmatch(r"[0-9a-f]{12}", key):
        return None
    return Marker(key, ordinal, bool(hashed))


def resolve_marker(marker: Marker, known: dict[str, str]) -> str | None:
    """The unit id *marker* names, given ``known`` = {hash: id} for the ids
    the reader expects (hashed markers resolve only through it)."""
    if marker.hashed:
        return known.get(marker.key)
    return marker.key


def hash_index(ids) -> dict[str, str]:
    """``{hash: id}`` for resolving hashed markers."""
    return {_hashed(uid): uid for uid in ids}


def unit_hash(salt: str, serial: str) -> str:
    """64-bit salted fingerprint of one exported unit's serialization."""
    return hashlib.sha256((salt + "\x00" + serial).encode("utf-8")).hexdigest()[:16]


def new_salt() -> str:
    return secrets.token_hex(16)


@dataclass
class ManifestUnit:
    id: str
    scope: str
    shell: str | None
    x: str


@dataclass
class Manifest:
    exporter: str
    base_doc_hash: str
    base_seq: int
    pending: str
    salt: str
    exported_at: str
    units: list[ManifestUnit] = field(default_factory=list)
    cards: list[str] = field(default_factory=list)


def manifest_xml(m: Manifest) -> bytes:
    """Serialize *m* as the custom XML part's bytes."""
    a = escape_attr
    head = (
        f'<aim:roundtrip xmlns:aim="{NS}" exporter="{a(m.exporter)}" '
        f'base-doc-hash="{a(m.base_doc_hash)}" base-seq="{m.base_seq}" '
        f'pending="{a(m.pending)}" salt="{a(m.salt)}" exported-at="{a(m.exported_at)}">'
    )
    parts = [head]
    for u in m.units:
        shell = f' shell="{a(u.shell)}"' if u.shell else ""
        parts.append(f'<aim:u id="{a(u.id)}" scope="{a(u.scope)}"{shell} x="{a(u.x)}"/>')
    parts.extend(f'<aim:card id="{a(pid)}"/>' for pid in m.cards)
    parts.append("</aim:roundtrip>")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + "".join(parts)).encode(
        "utf-8"
    )


def parse_manifest(blob: bytes) -> Manifest | None:
    """Parse one custom XML part; ``None`` unless it is a manifest in this
    namespace version. Untrusted input: size-capped, entities and network off,
    and every field type-checked rather than trusted."""
    if len(blob) > MANIFEST_MAX_BYTES or NS.encode() not in blob:
        return None
    if b"<!DOCTYPE" in blob or b"<!ENTITY" in blob:
        return None  # we never write a DTD; entities are an attack surface
    from lxml import etree

    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, huge_tree=False, load_dtd=False
    )
    try:
        root = etree.fromstring(blob, parser)
    except etree.XMLSyntaxError:
        return None
    if root.tag != f"{{{NS}}}roundtrip":
        return None
    try:
        seq = int(root.get("base-seq") or "")
    except ValueError:
        return None
    m = Manifest(
        exporter=root.get("exporter") or "",
        base_doc_hash=root.get("base-doc-hash") or "",
        base_seq=seq,
        pending=root.get("pending") or "",
        salt=root.get("salt") or "",
        exported_at=root.get("exported-at") or "",
    )
    for child in root:
        if not isinstance(child.tag, str):
            continue
        if child.tag == f"{{{NS}}}u" and child.get("id") and child.get("x"):
            m.units.append(
                ManifestUnit(
                    id=child.get("id") or "",
                    scope=child.get("scope") or "body",
                    shell=child.get("shell"),
                    x=child.get("x") or "",
                )
            )
        elif child.tag == f"{{{NS}}}card" and child.get("id"):
            m.cards.append(child.get("id") or "")
    return m
