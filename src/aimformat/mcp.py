"""The aimformat MCP server — the SDK's workflows as seven typed tools.

Local stdio only: tools operate on ``.aim`` files by absolute path and touch
nothing else. Set ``AIMFORMAT_MCP_ROOT`` to confine every path argument
(including export destinations) to one directory tree; unset means unscoped —
the local trusted-client default. Run via ``aim mcp`` (the CLI lazy-imports
this module) after ``pip install 'aimformat[mcp]'``. Tool surface mirrors
``docs/for-agents.md``: read (whole, outline, skeleton, lossy text, exact
chunks), search, edit or propose (one op or an atomic batch), resolve, lint,
export — few workflow-shaped tools, not a 1:1 SDK mapping.

Wire shape: every result is ONE compact text block (JSON for structured
results, plain text for the reading views) — no ``structuredContent`` and no
``outputSchema``, so a client never receives the payload twice. The tool
list is trimmed for the same reason (see :class:`_LeanFastMCP`).
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import ConfigDict, with_config
from typing_extensions import Required, TypedDict

from . import views
from ._ops import OpError, apply_ops, ops_from_args
from .document import AimDocument
from .errors import AimError
from .events import parse_actor
from .lint import lint_path

_INSTRUCTIONS = """\
aimformat: read and edit .aim documents (HTML with stable chunk ids, a \
pending-suggestions lane, and append-only history).
Read: aim_read mode=toc or text to orient (text is a lossy view), aim_search \
to locate, then mode=chunks for the exact HTML of anything you will change.
Write: aim_propose for reviewable or unsolicited changes (a human accepts or \
rejects them); aim_edit only for changes the user explicitly commanded. Batch \
related changes in one call with ops. To change words inside a chunk, use \
action replace_text (old_text, new_text) instead of resending its HTML. Keep \
data-aim ids stable; the tools mint ids for new content. Keep [elided: …] \
stubs as they are; they restore on write.
Set author to "agent:<your-model-id>". Writes save and re-lint; lint_errors > 0 \
means fix before moving on. If a write times out, check seq before retrying.
Paths are absolute host paths (local trusted stdio; AIMFORMAT_MCP_ROOT confines \
them). Guide: https://aimformat.com/llms.txt"""

ReadMode = Literal["full", "toc", "skeleton", "text", "chunks"]
EditAction = Literal["add", "modify", "replace_text", "delete", "move", "set_theme"]
ProposeAction = Literal["add", "modify", "replace_text", "delete", "move", "theme"]


# no docstrings on these: pydantic would ship them as schema descriptions.
# extra="forbid": a misspelt field ("anchor" for "after") must fail, not be
# dropped silently and land the op somewhere else
@with_config(ConfigDict(extra="forbid"))
class EditOp(TypedDict, total=False):
    action: Required[EditAction]
    target: str
    html: str
    old_text: str
    new_text: str
    container: str
    after: str
    theme_slots: dict[str, str]
    explanation: str


@with_config(ConfigDict(extra="forbid"))
class ProposeOp(TypedDict, total=False):
    action: Required[ProposeAction]
    target: str
    html: str
    old_text: str
    new_text: str
    container: str
    after: str
    theme_slots: dict[str, str]
    explanation: str


def _compact(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _lean_schema(node: Any, defs: dict[str, Any], *, in_properties: bool = False) -> Any:
    """Drop generated titles, collapse ``anyOf: [T, null]`` (+ ``default:
    null``) to ``T``, and inline ``$ref`` targets so clients that do not
    resolve references still see the whole shape. Validation is unaffected:
    FastMCP validates calls against its own pydantic model."""
    if isinstance(node, list):
        return [_lean_schema(x, defs) for x in node]
    if not isinstance(node, dict):
        return node
    if in_properties:  # keys are property names, not schema keywords
        return {k: _lean_schema(v, defs) for k, v in node.items()}
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        merged = {**defs.get(name, {}), **{k: v for k, v in node.items() if k != "$ref"}}
        return _lean_schema(merged, defs)
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k in ("title", "$defs"):
            continue
        out[k] = _lean_schema(v, defs, in_properties=k == "properties")
    any_of = out.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2 and {"type": "null"} in any_of:
        other = next(x for x in any_of if x != {"type": "null"})
        del out["anyOf"]
        out = {**other, **out}
        if "default" in out and out["default"] is None:
            del out["default"]
    return out


def _one_line(text: str | None) -> str | None:
    if text is None:
        return None
    return " ".join(line.strip() for line in inspect.cleandoc(text).splitlines() if line.strip())


class _LeanFastMCP(FastMCP):
    """FastMCP with a trimmed ``tools/list``: lean input schemas (see
    :func:`_lean_schema`), descriptions on one line, no output schemas."""

    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            schema = tool.inputSchema
            tool.inputSchema = _lean_schema(schema, schema.get("$defs", {}))
            tool.description = _one_line(tool.description)
            tool.outputSchema = None
        return tools


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


def _save_and_lint(doc: AimDocument, path: str) -> dict[str, Any]:
    doc.save(path)
    errors = [f for f in lint_path(path) if f.level == "error"]
    return {"ok": not errors, "seq": doc.seq, "doc_hash": doc.doc_hash, "lint_errors": len(errors)}


def _write(
    path: str,
    kind: Literal["edit", "propose"],
    author: str | None,
    explanation: str | None,
    ops: list[dict[str, Any]],
    single: bool,
) -> str:
    doc = _load(path)
    try:
        res = apply_ops(
            doc, ops, kind=kind, author=_actor(author), explanation=explanation, single=single
        )
    except OpError as exc:
        raise ValueError(str(exc)) from None
    out = _save_and_lint(doc, path)
    if single:
        first = res.results[0]
        if kind == "edit":
            out["id"] = first["id"]
        else:
            out["proposal"] = first["id"]
    else:
        out["batch"] = res.batch
        out["results"] = res.results
    if kind == "propose":
        out["superseded"] = res.superseded
    return _compact(out)


def create_server() -> FastMCP:
    server = _LeanFastMCP("aimformat", instructions=_INSTRUCTIONS)
    tool = server.tool(structured_output=False)

    @tool
    def aim_read(
        path: str,
        mode: ReadMode = "full",
        ids: list[str] | None = None,
        words: int | None = None,
        include_history: bool = False,
    ) -> str:
        """Read a document. mode: full (JSON: every chunk's HTML + pending lane), toc
        (outline with id ranges), skeleton (every id with tag, classes and first `words`
        words, default 8), text (every chunk as plain text with its id; lossy, never an edit
        payload), chunks (exact HTML for `ids`: chunk, container or proposal ids, or a range
        'a..b')."""
        if include_history and mode != "full":
            raise ValueError("aim: include_history works with mode=full only")
        if ids and mode != "chunks":
            raise ValueError("aim: ids works with mode=chunks only")
        if words is not None and mode != "skeleton":
            raise ValueError("aim: words works with mode=skeleton only")
        doc = _load(path)
        try:
            if mode == "full":
                return _compact(views.full_projection(doc, include_history=include_history))
            if mode == "toc":
                return views.render_toc(doc)
            if mode == "skeleton":
                return views.render_skeleton(doc, 8 if words is None else words)
            if mode == "text":
                return views.render_text(doc)
            if mode == "chunks":
                return views.render_chunks(doc, ids or [])
        except ValueError as exc:
            raise ValueError(f"aim: {exc}") from None
        raise ValueError(f"aim: unknown mode {mode!r} (use full | toc | skeleton | text | chunks)")

    @tool
    def aim_search(path: str, query: str, k: int = 8) -> str:
        """Rank chunks by lexical relevance to query (quote a phrase to require it). Returns
        id, section and a snippet per hit; fetch exact HTML with aim_read mode=chunks."""
        doc = _load(path)
        try:
            return views.render_search(doc, query, k)
        except ValueError as exc:
            raise ValueError(f"aim: {exc}") from None

    @tool
    def aim_edit(
        path: str,
        action: EditAction | None = None,
        target: str | None = None,
        html: str | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
        container: str | None = None,
        after: str | None = None,
        theme_slots: dict[str, str] | None = None,
        explanation: str | None = None,
        ops: list[EditOp] | None = None,
        author: str | None = None,
    ) -> str:
        """Apply direct edits recorded in history (only for changes the user commanded). One
        op via the arguments, or up to 100 via ops (same fields; all-or-nothing, one batch).
        add and modify take html; replace_text takes old_text (once in the chunk's text) and
        new_text, keeping the markup; add and move take container (default body) and after
        (an id, 'first', '$N' = the id ops[N] created or targeted, omitted = end); set_theme
        takes theme_slots."""
        try:
            batch, single = ops_from_args(
                action=action,
                target=target,
                html=html,
                old_text=old_text,
                new_text=new_text,
                container=container,
                after=after,
                theme_slots=theme_slots,
                explanation=None,
                ops=[dict(o) for o in ops] if ops is not None else None,
                kind="edit",
            )
        except OpError as exc:
            raise ValueError(str(exc)) from None
        return _write(path, "edit", author, explanation, batch, single)

    @tool
    def aim_propose(
        path: str,
        action: ProposeAction | None = None,
        target: str | None = None,
        html: str | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
        container: str | None = None,
        after: str | None = None,
        theme_slots: dict[str, str] | None = None,
        explanation: str | None = None,
        ops: list[ProposeOp] | None = None,
        author: str | None = None,
    ) -> str:
        """Add suggestion cards to the pending lane for a human to accept or reject. Same
        arguments as aim_edit, up to 25 ops (theme instead of set_theme; '$N' of a proposed
        add works only as after of a later add in the same container). Write explanations
        that stand alone."""
        try:
            batch, single = ops_from_args(
                action=action,
                target=target,
                html=html,
                old_text=old_text,
                new_text=new_text,
                container=container,
                after=after,
                theme_slots=theme_slots,
                explanation=None,
                ops=[dict(o) for o in ops] if ops is not None else None,
                kind="propose",
            )
        except OpError as exc:
            raise ValueError(str(exc)) from None
        return _write(path, "propose", author, explanation, batch, single)

    @tool
    def aim_resolve(
        path: str,
        decision: Literal["accept", "reject"],
        proposal_ids: list[str],
        applied: str | None = None,
        explanation: str | None = None,
        author: str | None = None,
    ) -> str:
        """Accept or reject pending proposals (all-or-nothing). applied (accept, one id)
        records accept-with-tweaks: the payload as actually applied."""
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
        return _compact(result)

    @tool
    def aim_lint(path: str) -> str:
        """Run the conformance verifier; level 'error' means non-conforming. Works on broken
        files."""
        if not _guard(path).is_file():
            raise ValueError(f"aim: not a file: {path}")
        findings = lint_path(path)
        return _compact(
            {
                "errors": sum(f.level == "error" for f in findings),
                "warnings": sum(f.level == "warning" for f in findings),
                "findings": [f.__dict__ for f in findings],
            }
        )

    @tool
    def aim_export(path: str, out_path: str, pending: str | None = None) -> str:
        """Convert by out_path extension: .docx (pending: tracked | accept-all | reject-all),
        .md (drop | criticmarkup), .html and .pdf (keep | accept-all | reject-all). .aim.html
        writes the document itself under the browser alias. docx and pdf need extras."""
        return _compact(_export(path, out_path, pending))

    return server


def _export(path: str, out_path: str, pending: str | None) -> dict[str, Any]:
    from .cli import _EXPORT_PENDING, _is_alias

    doc = _load(path)
    _guard(out_path)
    out = Path(out_path)
    if _is_alias(out):
        if pending not in (None, "keep"):
            raise ValueError(
                f"aim: pending={pending!r} not valid for .aim.html "
                "(the alias carries the file as-is)"
            )
        doc.save(out)
        return {"ok": True, "wrote": str(out), "pending": "keep"}
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


def _warn_if_unscoped() -> bool:
    """Warn (stderr) when the server is about to run with no filesystem
    confinement; returns True when unscoped. The unscoped default stays —
    it is the documented local-trusted-stdio contract — but it must be
    loud: wiring ``aim mcp`` to a hosted or semi-trusted client without a
    root grants that client arbitrary host filesystem access."""
    import sys

    if os.environ.get("AIMFORMAT_MCP_ROOT"):
        return False
    print(
        "aim mcp: AIMFORMAT_MCP_ROOT is not set — tools can read, write and "
        "export ANY path this process can reach. Fine for a local, trusted "
        "stdio client; for anything less trusted, set "
        "AIMFORMAT_MCP_ROOT=<dir> to confine every path argument to that "
        "directory tree.",
        file=sys.stderr,
    )
    return True


def main(args: Any = None) -> int:
    """Entry point for ``aim mcp``: serve on stdio until the client hangs up."""
    _warn_if_unscoped()
    create_server().run(transport="stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
