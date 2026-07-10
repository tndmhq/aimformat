"""aimformat — reference tooling for the `.aim` document format.

A `.aim` file is a single valid HTML document that is simultaneously the
rendered artifact, the accepted current version, the pending-change lane
(AI/human proposals awaiting accept/reject), the full invertible edit
history, and a set of derived caches. This package is the reference SDK:
load, edit, propose, resolve, verify, and time-travel .aim documents.

Quickstart::

    import aimformat as aim

    doc = aim.new_document(title="Q3 Proposal")
    me, bot = aim.human("luca"), aim.agent("claude-opus-4-8")

    intro = doc.add_chunk("<p>We propose a three-year engagement.</p>",
                          author=bot)
    p = doc.propose_modify(intro.id, f'<p data-aim="{intro.id}">Acme saves '
                           "€2.1M over three years.</p>",
                           author=bot, explanation="Lead with the outcome.")
    doc.accept(p.id, decided_by=me)
    doc.checkpoint("sent-to-client")

    assert not aim.lint(doc)      # verifier: structure + vocabulary + history
    doc.save("proposal.aim")      # canonical, renders in any browser

The format specification lives in ``spec.md`` at the repository root.
"""

from .canonical import canonical_json, sha256_prefixed
from .convert import (
    from_docx,
    from_markdown,
    from_path,
    from_pdf,
    from_text,
    to_html,
    to_markdown,
    to_pdf,
)
from .css import css_stats, generate_aim_css
from .document import LAST, AimDocument, Anchor, Chunk, Proposal, load, loads, new_document
from .errors import AimError, HistoryError, InvalidOperation, ParseError, TargetNotFound
from .events import Actor, Event, agent, external, human, parse_actor
from .export_docx import to_docx
from .ingest import from_docling
from .lint import Finding, lint, lint_path, lint_text
from .note import render_note
from .pagesetup import PageSetup, default_page_setup, page_css
from .reconcile import ReconcileReport
from .registry import REGISTRY

__version__ = "0.2.0"
SPEC_VERSION = REGISTRY.spec_version

__all__ = [
    "AimDocument",
    "Anchor",
    "Chunk",
    "Proposal",
    "Event",
    "Actor",
    "Finding",
    "LAST",
    "PageSetup",
    "ReconcileReport",
    "SPEC_VERSION",
    "__version__",
    "load",
    "loads",
    "new_document",
    "lint",
    "lint_text",
    "lint_path",
    "human",
    "agent",
    "external",
    "parse_actor",
    "render_note",
    "from_docling",
    "to_docx",
    "from_path",
    "from_text",
    "from_markdown",
    "from_docx",
    "from_pdf",
    "to_markdown",
    "to_html",
    "to_pdf",
    "page_css",
    "default_page_setup",
    "generate_aim_css",
    "css_stats",
    "canonical_json",
    "sha256_prefixed",
    "AimError",
    "ParseError",
    "TargetNotFound",
    "InvalidOperation",
    "HistoryError",
    "REGISTRY",
]
