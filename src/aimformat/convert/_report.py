"""What an import carried, and what it did not (stdlib only).

Importer-agnostic on purpose: the DOCX importer fills every field today, and
the Markdown or PDF importers can report through the same types later
without a new API. Everything here is plain data — no document content
beyond short, capped excerpts the source file itself supplied.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..document import AimDocument

__all__ = [
    "AimImportWarning",
    "CommentNote",
    "ImportReport",
    "ImportResult",
    "RevisionNote",
]


class AimImportWarning(UserWarning):
    """Something in the source was not carried into the .aim document.

    Emitted once per kind of loss (comments, revisions that produced no
    proposal, parts the importer does not read), so a caller that only sees
    warnings still learns that the document is not the whole source."""


@dataclass(frozen=True)
class RevisionNote:
    """One tracked change in the source, and where it went."""

    kind: str  # the source's own name: ins, del, moveFrom, rPrChange, …
    author: str | None
    date: str | None
    excerpt: str  # the revision's own text, capped; "" for formatting
    #: the proposal ids carrying it (a split paragraph's mark feeds both the
    #: insertion and the shortened original); empty when none does
    cards: tuple[str, ...] = ()
    reason: str | None = None  # why no card carries it (None when one does)


@dataclass(frozen=True)
class CommentNote:
    """One review comment in the source. The format has no comment construct
    (spec §11.5), so comments are reported, never stored; the text they were
    anchored on stays intact in the document."""

    id: str
    author: str
    date: str | None
    text: str
    anchor_text: str
    chunk_id: str | None  # the chunk holding the anchor, or the pending card's id
    resolved: bool = False
    parent_id: str | None = None  # the comment this one replies to


@dataclass
class ImportReport:
    """What one import carried and what it did not, in words."""

    source: str  # base name only, never a directory path
    tracked: str | None = None  # "propose" | "accept" | "reject"; None = no revisions
    revisions: list[RevisionNote] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)  # card ids written
    comments: list[CommentNote] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # everything not carried

    def summary(self) -> str:
        """One paragraph for a human (the CLI prints it to stderr)."""
        bits: list[str] = []
        if self.tracked == "propose":
            bits.append(
                f"{len(self.revisions)} tracked change(s) became "
                f"{len(self.proposals)} pending proposal(s)"
            )
        elif self.tracked in ("accept", "reject"):
            verb = "accepted" if self.tracked == "accept" else "rejected"
            bits.append(f"{len(self.revisions)} tracked change(s) {verb} on import")
        bits.extend(self.warnings)
        return f"{self.source}: " + "; ".join(bits) + "." if bits else ""

    def emit_warnings(self, *, stacklevel: int = 3) -> None:
        for message in self.warnings:
            warnings.warn(f"{self.source}: {message}", AimImportWarning, stacklevel=stacklevel)


@dataclass
class ImportResult:
    """An imported document together with its import report."""

    document: AimDocument
    report: ImportReport
