"""Exception types for the aimformat package."""

from __future__ import annotations


class AimError(Exception):
    """Base class for all aimformat errors."""


class ParseError(AimError):
    """The input could not be parsed as an .aim document."""


class TargetNotFound(AimError, KeyError):
    """A chunk, container, or proposal id does not exist in the document."""

    def __str__(self) -> str:  # KeyError quotes its message; keep it readable
        return self.args[0] if self.args else ""


class InvalidOperation(AimError):
    """The requested edit violates a format invariant."""


class HistoryError(AimError):
    """The history chain is inconsistent or an event is malformed."""
