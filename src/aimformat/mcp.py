"""The aimformat MCP server — the SDK's workflows as six typed tools.

Local stdio only: tools operate on ``.aim`` files by absolute path and touch
nothing else. Set ``AIMFORMAT_MCP_ROOT`` to confine every path argument
(including export destinations) to one directory tree; unset means unscoped —
the local trusted-client default. Run via ``aim mcp`` (the CLI lazy-imports
this module) after ``pip install 'aimformat[mcp]'``. Tool surface mirrors
``docs/for-agents.md``: read projected, edit or propose, resolve, lint,
export — few workflow-shaped tools, not a 1:1 SDK mapping.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .document import LAST, AimDocument
from .errors import AimError
from .events import parse_actor
from .lint import lint_path

_INSTRUCTIONS = """\
aimformat: read and edit .aim documents (the open HTML-based format with
stable chunk ids, an in-file suggestions lane, and append-only history).
Workflow: aim_read first — it returns the summary (with a staleness flag),
TOC, chunks with their data-aim ids, and pending proposals. To change a
document, prefer aim_propose (a pending suggestion a human accepts or
rejects later) for reviewable or unsolicited changes; use aim_edit only for
changes the user explicitly commanded. Resolve pending proposals with
aim_resolve. After writes the tools save and re-lint automatically; a
nonzero lint_errors means fix before moving on. Keep every data-aim id
stable; new content needs a fresh unique id (the tools mint them for you).
Set author to "agent:<your-model-id>" so edits are attributed. Docs:
https://aimformat.com/llms.txt — human review happens in AIM editors
(https://aimformat.com/editors)."""

# any long data: URI — the payload alphabet varies (base64 commas, percent
# escapes), so match every non-delimiter run rather than a fixed alphabet
_DATA_URI = re.compile(r"data:[^\"'\s]{64,}")


def _guard(path: str) -> Path:
    """Resolve *path* (canonicalizing symlinks) and, when the
    ``AIMFORMAT_MCP_ROOT`` environment variable is set, reject anything
    escaping that root. The env var is read per call so it can be set or
    changed without reimporting; unset keeps the unscoped local default."""
    p = Path(path).resolve()
    root = os.environ.get("AIMFORMAT_MCP_ROOT")
    if root and not p.is_relative_to(Path(root).resolve()):
        raise ValueError(f"aim: path escapes workspace root: {path}")
    return p


def _load(path: str) -> AimDocument:
    p = _guard(path)
    if not p.is_file():
        raise ValueError(f"aim: not a file: {path}")
    return AimDocument.load(p)


def _actor(spec: str | None):
    return parse_actor(spec or "external:aim-mcp")


def _actor_str(actor) -> str:
    value = actor.model or actor.id
    return f"{actor.type}:{value}" if value else actor.type


def _elide(html: str) -> str:
    return _DATA_URI.sub("[data-uri elided]", html)


def _anchor(after: str | None):
    if after is None or after == "":
        return LAST
    return None if after == "first" else after


def _require(
    action: str,
    target: str | None,
    html: str | None,
    theme_slots: dict | None,
    *,
    targeted: tuple[str, ...],
    payload: tuple[str, ...],
    themed: tuple[str, ...],
) -> None:
    """Reject underspecified calls before any mutation — an omitted target
    must never fall through to id resolution (where None can match the
    first construct) or reach the disk as a broken card."""
    if action in targeted and not target:
        raise ValueError(f"aim: {action} requires target (a chunk or container id)")
    if action in payload and html is None:
        raise ValueError(f"aim: {action} requires html (the payload markup)")
    if action in themed and not theme_slots:
        raise ValueError(f"aim: {action} requires theme_slots")


def _save_and_lint(doc: AimDocument, path: str) -> dict[str, Any]:
    doc.save(path)
    errors = [f for f in lint_path(path) if f.level == "error"]
    return {"ok": not errors, "seq": doc.seq, "doc_hash": doc.doc_hash, "lint_errors": len(errors)}


def create_server() -> FastMCP:
    server = FastMCP("aimformat", instructions=_INSTRUCTIONS)

    @server.tool()
    def aim_read(path: str, include_history: bool = False) -> dict[str, Any]:
        """Read an .aim document as a projected, token-cheap view: title,
        summary (with staleness flag), table of contents, every chunk with
        its stable data-aim id, and the pending proposals awaiting a
        decision. Long data: URIs are elided; the stylesheet is never
        included. Start here before editing. Operates on any absolute path
        on the host; intended for local, trusted stdio use only."""
        doc = _load(path)
        summary = None
        meta = doc.meta
        if meta and isinstance(meta.get("summary"), dict):
            summary = {
                "text": meta["summary"].get("text"),
                "stale": meta["summary"].get("doc_hash") != doc.doc_hash,
            }
        out: dict[str, Any] = {
            "title": doc.title,
            "lang": doc.lang,
            "spec_version": doc.spec_version,
            "seq": doc.seq,
            "doc_hash": doc.doc_hash,
            "summary": summary,
            "toc": (meta or {}).get("toc"),
            "chunks": [
                {"id": c.id, "container": c.container, "html": _elide(c.html)} for c in doc.chunks
            ],
            "proposals": [
                {
                    "id": p.id,
                    "action": p.action,
                    "target": p.target,
                    "author": _actor_str(p.author),
                    "explanation": p.explanation,
                    "payload_html": _elide(p.payload_html) if p.payload_html else None,
                    "batch": p.batch,
                }
                for p in doc.proposals
            ],
        }
        if include_history:
            out["history"] = [ev.data for ev in doc.history]
        return out

    @server.tool()
    def aim_edit(
        path: str,
        action: str,
        target: str | None = None,
        html: str | None = None,
        container: str = "body",
        after: str | None = None,
        theme_slots: dict[str, str] | None = None,
        explanation: str | None = None,
        author: str | None = None,
    ) -> dict[str, Any]:
        """Apply a direct edit (recorded in history) and save. Only for
        changes the user explicitly commanded — otherwise use aim_propose.
        action: add | modify | delete | move | set_theme. add/modify take
        html; add/move take container and after ('first' = first position,
        omitted = end); set_theme takes theme_slots. Set author to
        'agent:<model-id>' for attribution. Operates on any absolute path
        on the host; intended for local, trusted stdio use only."""
        _require(
            action,
            target,
            html,
            theme_slots,
            targeted=("modify", "delete", "move"),
            payload=("add", "modify"),
            themed=("set_theme",),
        )
        doc = _load(path)
        who = _actor(author)
        try:
            if action == "add":
                assert html is not None  # _require
                doc.add_chunk(
                    html,
                    author=who,
                    container=container,
                    after=_anchor(after),
                    explanation=explanation,
                )
            elif action == "modify":
                assert target is not None and html is not None  # _require
                doc.modify_chunk(target, html, author=who, explanation=explanation)
            elif action == "delete":
                assert target is not None  # _require
                doc.delete_chunk(target, author=who, explanation=explanation)
            elif action == "move":
                assert target is not None  # _require
                doc.move_chunk(
                    target,
                    author=who,
                    container=container,
                    after=_anchor(after),
                    explanation=explanation,
                )
            elif action == "set_theme":
                doc.set_theme(theme_slots or {}, author=who, explanation=explanation)
            else:
                raise ValueError(
                    f"aim: unknown edit action {action!r} (use "
                    "add | modify | delete | move | set_theme)"
                )
        except AimError as exc:
            raise ValueError(f"aim: {exc}") from exc
        return _save_and_lint(doc, path)

    @server.tool()
    def aim_propose(
        path: str,
        action: str,
        target: str | None = None,
        html: str | None = None,
        container: str = "body",
        after: str | None = None,
        theme_slots: dict[str, str] | None = None,
        explanation: str | None = None,
        author: str | None = None,
    ) -> dict[str, Any]:
        """Append a suggestion card to the pending lane instead of editing —
        the right tool for reviewable or unsolicited changes; a human
        accepts or rejects it later (in an AIM editor, or via aim_resolve).
        action: modify | add | delete | move | theme. Write an explanation
        that stands alone: raw-tier readers see it without the payload.
        Operates on any absolute path on the host; intended for local,
        trusted stdio use only."""
        _require(
            action,
            target,
            html,
            theme_slots,
            targeted=("modify", "delete", "move"),
            payload=("add", "modify"),
            themed=("theme",),
        )
        doc = _load(path)
        who = _actor(author)
        try:
            if action == "modify":
                assert target is not None and html is not None  # _require
                p = doc.propose_modify(target, html, author=who, explanation=explanation)
            elif action == "add":
                assert html is not None  # _require
                p = doc.propose_add(
                    html,
                    author=who,
                    container=container,
                    after=_anchor(after),
                    explanation=explanation,
                )
            elif action == "delete":
                assert target is not None  # _require
                p = doc.propose_delete(target, author=who, explanation=explanation)
            elif action == "move":
                assert target is not None  # _require
                p = doc.propose_move(
                    target,
                    author=who,
                    container=container,
                    after=_anchor(after),
                    explanation=explanation,
                )
            elif action == "theme":
                p = doc.propose_theme(theme_slots or {}, author=who, explanation=explanation)
            else:
                raise ValueError(
                    f"aim: unknown proposal action {action!r} "
                    "(use modify | add | delete | move | theme)"
                )
        except AimError as exc:
            raise ValueError(f"aim: {exc}") from exc
        result = _save_and_lint(doc, path)
        result["proposal"] = p.id
        return result

    @server.tool()
    def aim_resolve(
        path: str,
        decision: str,
        proposal_ids: list[str],
        applied: str | None = None,
        explanation: str | None = None,
        author: str | None = None,
    ) -> dict[str, Any]:
        """Accept or reject pending proposals by id and save. decision:
        accept | reject. applied (accept only, single id) records
        accept-with-tweaks: the payload as actually applied. Resolution is
        all-or-nothing: on any bad id nothing is saved. Operates on any
        absolute path on the host; intended for local, trusted stdio use
        only."""
        if decision not in ("accept", "reject"):
            raise ValueError(f"aim: unknown decision {decision!r} (use accept | reject)")
        if applied and (decision != "accept" or len(proposal_ids) != 1):
            raise ValueError("aim: applied= needs decision='accept' and exactly one proposal id")
        doc = _load(path)
        who = _actor(author)
        try:
            for pid in proposal_ids:
                if decision == "accept":
                    doc.accept(pid, decided_by=who, applied=applied, explanation=explanation)
                else:
                    doc.reject(pid, decided_by=who, explanation=explanation)
        except AimError as exc:
            raise ValueError(f"aim: {exc}") from exc
        result = _save_and_lint(doc, path)
        result["resolved"] = list(proposal_ids)
        result["decision"] = decision
        return result

    @server.tool()
    def aim_lint(path: str) -> dict[str, Any]:
        """Run the conformance verifier: structure, vocabulary, security,
        pending lane, history chain, caches, canonical form. Returns every
        finding; level 'error' means non-conforming. Works on broken files
        too — that is what it is for. Operates on any absolute path on the
        host; intended for local, trusted stdio use only."""
        if not _guard(path).is_file():
            raise ValueError(f"aim: not a file: {path}")
        findings = lint_path(path)
        return {
            "errors": sum(f.level == "error" for f in findings),
            "warnings": sum(f.level == "warning" for f in findings),
            "findings": [f.__dict__ for f in findings],
        }

    @server.tool()
    def aim_export(path: str, out_path: str, pending: str | None = None) -> dict[str, Any]:
        """Convert an .aim document to another format, chosen by out_path
        extension: .docx (pending: tracked | accept-all | reject-all),
        .md (drop | criticmarkup), .html (keep | accept-all | reject-all),
        .pdf (keep | accept-all | reject-all). Heavier formats need extras:
        pip install 'aimformat[docx]' (or [convert], [pdf]). Reads from and
        writes to any absolute path on the host; intended for local,
        trusted stdio use only."""
        from .cli import _EXPORT_PENDING

        doc = _load(path)
        _guard(out_path)
        out = Path(out_path)
        suffix = out.suffix.lower()
        if suffix not in _EXPORT_PENDING:
            raise ValueError(
                f"aim: unsupported export format {suffix!r} "
                f"(supported: "
                f"{', '.join(sorted(_EXPORT_PENDING))})"
            )
        default, allowed = _EXPORT_PENDING[suffix]
        fate = pending or default
        if fate not in allowed:
            raise ValueError(
                f"aim: pending={fate!r} not valid for {suffix} (allowed: {', '.join(allowed)})"
            )
        try:
            if suffix == ".docx":
                from .export_docx import to_docx

                to_docx(doc, out, pending=fate)
            elif suffix == ".md":
                from .convert import to_markdown

                out.write_text(to_markdown(doc, pending=fate), "utf-8")
            elif suffix == ".html":
                from .convert import to_html

                out.write_text(to_html(doc, pending=fate), "utf-8")
            else:
                from .convert import to_pdf

                to_pdf(doc, out, pending=fate)
        except ImportError as exc:
            extra = {".docx": "docx", ".pdf": "pdf"}.get(suffix, "convert")
            return {
                "ok": False,
                "error": f"aim: {suffix} export needs an optional "
                f"extra ({exc}); pip install "
                f"'aimformat[{extra}]'",
            }
        return {"ok": True, "wrote": str(out), "pending": fate}

    return server


def main(args: Any = None) -> int:
    """Entry point for ``aim mcp``: serve on stdio until the client hangs up."""
    create_server().run(transport="stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
