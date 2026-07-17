"""History events and actors (spec §7).

Events are stored as canonical JSON lines inside the history script block.
:class:`Event` is a thin typed view over the underlying dict: parsers MUST
ignore unknown fields (``x_*`` is reserved for vendor extensions), so the
dict — not a closed dataclass — is the truth.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass
from typing import Any

from .canonical import canonical_json
from .errors import AimError, HistoryError
from .registry import REGISTRY

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def _valid_utc(t: str) -> bool:
    """True for a real ISO-8601 UTC instant — the digit-shape regex alone
    accepts impossible dates like 2026-99-99T99:99:99Z."""
    if not isinstance(t, str) or not _ISO_RE.match(t):
        return False
    try:
        _dt.datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class Actor:
    """Who did something: a human, an agent (model), or an external tool."""

    type: str  # "human" | "agent" | "external"
    id: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if self.type not in REGISTRY.raw["events"]["actor_types"]:
            raise ValueError(f"unknown actor type {self.type!r}")

    def to_obj(self) -> dict:
        out: dict[str, str] = {"type": self.type}
        if self.id is not None:
            out["id"] = self.id
        if self.model is not None:
            out["model"] = self.model
        return out

    @classmethod
    def from_obj(cls, obj: dict) -> Actor:
        return cls(type=obj["type"], id=obj.get("id"), model=obj.get("model"))


def human(id: str) -> Actor:
    """Convenience constructor: a human actor."""
    return Actor("human", id=id)


def agent(model: str, id: str | None = None) -> Actor:
    """Convenience constructor: an AI-agent actor (exact model id)."""
    return Actor("agent", id=id, model=model)


def external(id: str | None = None) -> Actor:
    """Convenience constructor: an external tool (e.g. ``aim reconcile``)."""
    return Actor("external", id=id)


def parse_actor(spec: str) -> Actor:
    """Parse an actor string: ``human:ID`` | ``agent:MODEL`` | ``external:ID``.

    Bare ``external`` (no id) is allowed; humans need an id and agents a
    model id, so those forms without a value are usage errors.
    """
    kind, sep, value = spec.partition(":")
    kind, value = kind.strip(), value.strip()
    if kind == "external":
        return external(value or None)
    if kind in ("human", "agent") and sep and value:
        return human(value) if kind == "human" else agent(value)
    raise AimError(f"invalid actor {spec!r} (use human:ID, agent:MODEL, or external:ID)")


class Event:
    """One history event. Attribute access over the canonical dict."""

    __slots__ = ("data",)

    def __init__(self, data: dict):
        self.data = data

    # -- common fields -------------------------------------------------------
    @property
    def seq(self) -> int:
        return self.data["seq"]

    @property
    def kind(self) -> str:
        return self.data["kind"]

    @property
    def t(self) -> str:
        return self.data["t"]

    @property
    def target(self) -> str | None:
        return self.data.get("target")

    @property
    def action(self) -> str | None:
        return self.data.get("action")

    @property
    def decision(self) -> str | None:
        return self.data.get("decision")

    @property
    def origin(self) -> str | None:
        return self.data.get("origin")

    @property
    def batch(self) -> str | None:
        return self.data.get("batch")

    @property
    def author(self) -> Actor | None:
        obj = self.data.get("author")
        return Actor.from_obj(obj) if obj else None

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    # -- semantics -----------------------------------------------------------
    @property
    def state_changing(self) -> bool:
        if self.kind == "direct_edit":
            return True
        if self.kind == "resolution":
            return self.decision == "accepted"
        return False

    @property
    def applied_payload(self) -> str | None:
        """The serialization this event put into the document (if any)."""
        if self.kind == "resolution":
            return self.data.get("applied", self.data.get("proposed"))
        return self.data.get("after")

    # -- io --------------------------------------------------------------------
    def to_json(self) -> str:
        return canonical_json(self.data)

    @classmethod
    def from_json(cls, line: str) -> Event:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HistoryError(f"unparseable history line: {exc}") from exc
        if not isinstance(obj, dict):
            raise HistoryError(f"history line is not a JSON object: {line[:60]!r}")
        return cls(obj)

    def validate(self) -> list[str]:
        """Field-level problems with this event (empty when well-formed)."""
        problems: list[str] = []
        kind = self.data.get("kind")
        schema = REGISTRY.event_fields.get(kind or "")
        if schema is None:
            return [f"unknown event kind {kind!r}"]
        for field in schema["required"]:
            if field not in self.data:
                problems.append(f"{kind} event missing required field {field!r}")
        known = set(schema["required"]) | set(schema["optional"])
        for field in self.data:
            if field not in known and not field.startswith("x_"):
                problems.append(
                    f"{kind} event has unknown field {field!r} (vendor extensions must use x_*)"
                )
        seq = self.data.get("seq")
        if type(seq) is not int or seq < 1:  # bool ⊂ int: "seq": true must fail
            problems.append("seq must be a positive integer")
        t = self.data.get("t")
        if t is not None and not _valid_utc(t):
            problems.append(f"t is not ISO-8601 UTC (…Z): {t!r}")
        act = self.data.get("action")
        if act is not None and act not in REGISTRY.raw["events"]["actions"]:
            problems.append(f"unknown action {act!r}")
        dec = self.data.get("decision")
        if dec is not None and dec not in REGISTRY.raw["events"]["decisions"]:
            problems.append(f"unknown decision {dec!r}")
        org = self.data.get("origin")
        if org is not None and org not in REGISTRY.raw["events"]["origins"]:
            problems.append(f"unknown origin {org!r}")
        for role in ("author", "proposed_by", "decided_by"):
            obj = self.data.get(role)
            if obj is not None:
                if (
                    not isinstance(obj, dict)
                    or obj.get("type") not in REGISTRY.raw["events"]["actor_types"]
                ):
                    problems.append(f"{role} is not a valid actor object")
        problems += self._replay_field_problems(kind, act)
        return problems

    def _replay_field_problems(self, kind: str | None, act: str | None) -> list[str]:
        """Action-specific fields that forward replay / inverse verification
        need. Without these an event can be well-typed yet non-replayable —
        a latent break the chain verifier would otherwise hit as a crash
        (spec §7 event payloads; review finding AIM-05)."""
        if not act:
            return []
        out: list[str] = []

        def require(field: str, label: str) -> None:
            if self.data.get(field) is None:
                out.append(f"{label} missing {field!r}")

        if kind == "direct_edit":
            if act == "add":
                require("anchor", "add direct edit")
                require("after", "add direct edit")
            elif act == "modify":
                # a theme/settings removal (undo of the block's introduction)
                # legitimately carries no 'after'; every other modify must
                if self.data.get("target") not in ("aim:theme", "aim:doc"):
                    require("after", "modify direct edit")
            elif act == "delete":
                require("before", "delete direct edit")
                require("anchor", "delete direct edit")
            elif act == "move":
                require("from", "move direct edit")
                require("to", "move direct edit")
        elif kind == "resolution" and self.data.get("decision") == "accepted":
            if act == "add":
                require("anchor", "accepted add resolution")
            elif act == "delete":
                require("anchor", "accepted delete resolution")
            elif act == "move":
                require("from", "accepted move resolution")
                require("to", "accepted move resolution")
        return out

    def __repr__(self) -> str:
        bits = [f"seq={self.data.get('seq')}", self.data.get("kind", "?")]
        if self.action:
            bits.append(self.action)
        if self.target:
            bits.append(self.target)
        if self.decision:
            bits.append(self.decision)
        return f"Event({' '.join(str(b) for b in bits)})"
