"""The document review policy (spec §5.6) and the auto-accept outcome.

A document may carry a standing review policy in its ``aim:doc`` settings
block::

    {"page": {...}, "review": {"agents": "auto", "by": {"type": "human", "id": "Ada"}}}

``agents: "auto"`` means proposals authored by an agent or an external tool
are accepted in the batch that created them, recorded as ordinary
``accepted`` resolutions carrying ``auto: "policy"`` with ``decided_by`` set
to ``review.by``, the human whose standing consent the policy records.
Absent means off: there is no ``"off"`` value, so each state has exactly
one spelling.

The policy binds well-behaved tools. A file cannot authenticate actors, so
it is a record of consent, not an access control (spec §5.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import InvalidOperation
from .events import Actor
from .registry import REGISTRY

__all__ = ["ReviewPolicy", "AutoAcceptOutcome", "AUTO", "POLICY", "REQUEST"]

#: The one implemented ``review.agents`` value.
AUTO = "auto"
#: ``auto`` marker values on a resolution (spec §6.2).
POLICY = "policy"
REQUEST = "request"


def _d007(message: str) -> InvalidOperation:
    """An :class:`InvalidOperation` tagged with its verifier rule (D007), the
    same tagging :mod:`aimformat.pagesetup` uses for D003/D004."""
    exc = InvalidOperation(message)
    exc.lint_code = "D007"  # type: ignore[attr-defined]
    return exc


@dataclass(frozen=True)
class ReviewPolicy:
    """The parsed ``review`` field of the ``aim:doc`` settings block.

    ``agents`` is a string rather than a closed literal so a document from a
    newer spec (with a value this build does not implement) still reads; only
    :attr:`auto` is ever honoured.
    """

    agents: str
    by: Actor

    @property
    def auto(self) -> bool:
        """Whether agent and external proposals are accepted as they arrive."""
        return self.agents == AUTO

    def to_obj(self) -> dict:
        return {"agents": self.agents, "by": self.by.to_obj()}

    @classmethod
    def from_obj(cls, obj: Any, *, lenient: bool = False) -> ReviewPolicy:
        """Validate one ``review`` object (D007 on a bad shape).

        *lenient* is set when the document declares a spec version newer
        than this build implements: an ``agents`` value this build does not
        know is then read (and never honoured) instead of rejected, because
        a later spec may have registered it.
        """
        if not isinstance(obj, dict):
            raise _d007("review policy must be a JSON object")
        agents = obj.get("agents")
        if not isinstance(agents, str):
            raise _d007("review policy needs an 'agents' string")
        if agents not in REGISTRY.review_agents and not lenient:
            reserved = agents in REGISTRY.review_reserved_agents
            raise _d007(
                f"review policy agents {agents!r} is "
                + ("reserved and not defined yet" if reserved else "not a registered value")
                + f" (registered: {', '.join(sorted(REGISTRY.review_agents))})"
            )
        by = obj.get("by")
        if not isinstance(by, dict) or by.get("type") != "human":
            raise _d007("review policy 'by' must be a human actor object")
        for key in ("id", "model"):
            if key in by and not isinstance(by[key], str):
                raise _d007(f"review policy 'by.{key}' must be a string")
        return cls(agents=agents, by=Actor.from_obj(by))

    @classmethod
    def from_settings(cls, settings: dict, *, lenient: bool = False) -> ReviewPolicy | None:
        """The policy in a parsed settings object, or None when absent."""
        if "review" not in settings:
            return None
        return cls.from_obj(settings["review"], lenient=lenient)


@dataclass(frozen=True)
class AutoAcceptOutcome:
    """What one auto-accept pass did (``AimDocument.last_auto_accept``).

    ``via`` is ``"policy"``, ``"request"``, or ``"mixed"`` when one batch
    held both. ``decided_by`` is the decider of the policy group when there
    is one, else of the request group. ``accepted`` lists proposal ids in
    the order they were resolved; ``deferred`` lists in-scope cards left
    pending, and ``reason`` says why (None when nothing was deferred).
    """

    batch: str
    via: str
    decided_by: Actor | None
    accepted: tuple[str, ...]
    deferred: tuple[str, ...]
    reason: str | None
