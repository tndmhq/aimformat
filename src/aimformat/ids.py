"""Chunk / container / proposal id assignment.

Ids are opaque strings, unique within one document, matching the registry
pattern (``[a-z0-9][a-z0-9_-]{0,63}``). The reference tooling assigns short
random ids (8 chars, lowercase base-32) and re-rolls on collision; longer
ids — including UUIDs — are equally valid. Models emitting placeholder ids
never win: tooling assigns the real id at write time, and ids are never
reused within a document's lifetime (deleted ids stay burned).
"""

from __future__ import annotations

import re
import secrets

from .registry import REGISTRY

_ALPHABET = "0123456789abcdefghijklmnopqrstuv"  # base-32, lowercase
CHUNK_ID_RE = re.compile(REGISTRY.raw["ids"]["chunk_pattern"])
PROPOSAL_ID_RE = re.compile(REGISTRY.raw["ids"]["proposal_pattern"])
_DEFAULT_LEN = REGISTRY.raw["ids"]["default_length"]
RESERVED_TARGETS = frozenset(REGISTRY.raw["ids"]["reserved_targets"])


def is_valid_chunk_id(value: str) -> bool:
    """True if *value* is a legal chunk/container id.

    Reserved targets and the ``p-`` prefix are excluded — proposal ids own
    that namespace, and sharing it would make anchor references ambiguous
    (a chained add's ``after`` must dispatch on the id alone).
    """
    return (
        bool(CHUNK_ID_RE.match(value))
        and value not in RESERVED_TARGETS
        and not value.startswith("p-")
    )


def is_valid_proposal_id(value: str) -> bool:
    return bool(PROPOSAL_ID_RE.match(value))


def new_id(taken: set[str], *, prefix: str = "", length: int = _DEFAULT_LEN) -> str:
    """Return a fresh random id not present in *taken* (which is updated)."""
    while True:
        candidate = prefix + "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if candidate not in taken and candidate not in RESERVED_TARGETS:
            taken.add(candidate)
            return candidate


def new_proposal_id(taken: set[str]) -> str:
    return new_id(taken, prefix="p-")
