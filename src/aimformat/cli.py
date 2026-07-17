"""The ``aim`` command-line tool.

    aim lint FILE...        verify documents (all findings in one run)
    aim hash FILE           print the current doc_hash
    aim new -o FILE         scaffold a minimal valid document
    aim note FILE...        add/refresh the agent note (--check verifies)
    aim show FILE           overview; --format json for machine reads
    aim propose ACTION ...  append a proposal card to the pending lane
    aim accept FILE PID...  accept pending proposals (or --all)
    aim reject FILE PID...  reject pending proposals (or --all)
    aim flatten FILE        drop history (+embeddings) -> clean file
    aim pack FILE           hoist embedded data images into the asset registry
    aim prune FILE BEFORE   truncate history before a seq/checkpoint label
    aim gc FILE             collect dead asset symbols
    aim normalize FILE      rewrite in canonical form (lossless, idempotent)
    aim reconcile FILE      detect out-of-band edits; append reconcile events
    aim css                 print the generated aim.css for this spec version
    aim import IN -o F.aim  convert md/txt/docx/pdf to .aim
    aim export F.aim -o OUT convert .aim to docx/md/html/pdf (by extension)
    aim mcp                 run the MCP server (pip install 'aimformat[mcp]')

Exit codes: 0 ok · 1 lint errors / verification failure · 2 usage.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .css import css_stats, generate_aim_css
from .document import LAST, AimDocument, AnchorAfter, new_document, resolution_order
from .errors import AimError
from .lint import lint_path
from .registry import REGISTRY


def _cmd_lint(args: argparse.Namespace) -> int:
    missing = [p for p in args.files if not Path(p).is_file()]
    if missing:
        for p in missing:
            print(f"aim: not a file: {p}", file=sys.stderr)
        return 2
    total_errors = 0
    payload = []
    for path in args.files:
        findings = lint_path(path)
        errors = [f for f in findings if f.level == "error"]
        warnings = [f for f in findings if f.level == "warning"]
        total_errors += len(errors)
        if args.format == "json":
            payload.append(
                {
                    "file": str(path),
                    "errors": len(errors),
                    "warnings": len(warnings),
                    "findings": [f.__dict__ for f in findings],
                }
            )
            continue
        if not args.quiet or errors:
            print(f"== {path}")
            for f in findings:
                if args.quiet and f.level != "error":
                    continue
                print(f"  {f}")
            status = "PASS" if not errors else "FAIL"
            print(f"  {status} ({len(errors)} errors, {len(warnings)} warnings)")
    if args.format == "json":
        print(json.dumps(payload, indent=2))
    return 1 if total_errors else 0


def _cmd_hash(args: argparse.Namespace) -> int:
    doc = AimDocument.load(args.file)
    print(doc.doc_hash)
    return 0


def _cmd_new(args: argparse.Namespace) -> int:
    out = Path(args.output)
    if out.exists() and not args.force:
        print(f"aim: {out} exists (use --force to overwrite)", file=sys.stderr)
        return 2
    doc = new_document(title=args.title, lang=args.lang)
    doc.save(out)
    print(f"wrote {out}")
    return 0


def _actor_str(actor) -> str:
    value = actor.model or actor.id
    return f"{actor.type}:{value}" if value else actor.type


def _cmd_note(args: argparse.Namespace) -> int:
    if args.check and args.remove:
        print("aim: --check and --remove are mutually exclusive", file=sys.stderr)
        return 2
    missing = [p for p in args.files if not Path(p).is_file()]
    if missing:
        for p in missing:
            print(f"aim: not a file: {p}", file=sys.stderr)
        return 2
    payload = []
    failures = 0
    for path in args.files:
        doc = AimDocument.load(path)
        if args.remove:
            if doc.note is not None:
                doc.remove_note()
                doc.save(path)
                status = "removed"
            else:
                status = "absent"
        elif args.check:
            status = (
                "ok" if doc.has_canonical_note() else "stale" if doc.note is not None else "missing"
            )
            failures += status != "ok"
        elif doc.has_canonical_note():
            status = "ok"
        else:
            status = "updated" if doc.note is not None else "added"
            doc.set_note()
            doc.save(path)
        payload.append({"file": str(path), "status": status})
        if args.format != "json":
            print(f"{path}: {status}")
    if args.format == "json":
        print(json.dumps(payload, indent=2))
    return 1 if failures else 0


def _cmd_propose(args: argparse.Namespace) -> int:
    from .events import parse_actor

    author = parse_actor(args.author)
    doc = AimDocument.load(args.file)
    markup = getattr(args, "html", None)
    if markup is None and getattr(args, "html_file", None):
        markup = Path(args.html_file).read_text("utf-8")
    after: AnchorAfter = LAST
    if getattr(args, "after", None) is not None:
        after = None if args.after == "first" else args.after
    if args.action == "modify":
        assert markup is not None  # argparse: --html/--html-file required
        p = doc.propose_modify(args.target, markup, author=author, explanation=args.explanation)
    elif args.action == "add":
        assert markup is not None  # argparse: --html/--html-file required
        p = doc.propose_add(
            markup,
            author=author,
            container=args.container,
            after=after,
            explanation=args.explanation,
        )
    elif args.action == "delete":
        p = doc.propose_delete(args.target, author=author, explanation=args.explanation)
    elif args.action == "move":
        p = doc.propose_move(
            args.target,
            author=author,
            container=args.container,
            after=after,
            explanation=args.explanation,
        )
    else:  # theme
        slots: dict[str, str] = {}
        for item in args.set:
            if "=" not in item:
                print(f"aim: --set expects SLOT=VALUE, got {item!r}", file=sys.stderr)
                return 2
            k, v = item.split("=", 1)
            k, v = k.strip(), v.strip()
            # slot names start "--aim-", which argparse would eat as an
            # option; accept the bare form and qualify it here
            if not k.startswith("--"):
                k = "--" + k if k.startswith("aim-") else "--aim-" + k
            slots[k] = v
        p = doc.propose_theme(slots, author=author, explanation=args.explanation)
    out = Path(args.output or args.file)
    doc.save(out)
    if args.format == "json":
        print(
            json.dumps(
                {
                    "proposal": p.id,
                    "action": p.action,
                    "target": p.target,
                    "author": _actor_str(p.author),
                    "explanation": p.explanation,
                    "file": str(out),
                },
                indent=2,
            )
        )
    else:
        print(p.id)
        print(f"wrote {out}")
    return 0


def _cmd_resolve(args: argparse.Namespace, decision: str) -> int:
    from .events import parse_actor

    if args.all and args.pids:
        print("aim: give proposal ids or --all, not both", file=sys.stderr)
        return 2
    if not args.all and not args.pids:
        print("aim: nothing to do (give proposal ids or --all)", file=sys.stderr)
        return 2
    doc = AimDocument.load(args.file)
    decided_by = parse_actor(args.author)
    if args.all:
        # dependency-safe order shared with the exporters: chained adds
        # resolve after the add they anchor on; per round adds/modifies go
        # first, then moves, then deletes, so nothing pulls an anchor out
        # from under a card that still needs it (and a container modify
        # waits for a move that rescues a member its payload drops)
        pids = [p.id for p in resolution_order(doc.proposals, doc, accepting=decision == "accept")]
    else:
        pids = args.pids
    if not pids:
        print("[]" if args.format == "json" else "no pending proposals")
        return 0
    events = []
    for pid in pids:
        if decision == "accept":
            events.append(doc.accept(pid, decided_by=decided_by, explanation=args.explanation))
        else:
            events.append(doc.reject(pid, decided_by=decided_by, explanation=args.explanation))
    out = Path(args.output or args.file)
    doc.save(out)
    if args.format == "json":
        print(
            json.dumps(
                [
                    {"proposal": pid, "decision": decision, "seq": ev.seq}
                    for pid, ev in zip(pids, events, strict=True)
                ],
                indent=2,
            )
        )
    else:
        for pid in pids:
            print(f"{decision}ed {pid}")
        print(f"wrote {out}")
    return 0


def _cmd_accept(args: argparse.Namespace) -> int:
    return _cmd_resolve(args, "accept")


def _cmd_reject(args: argparse.Namespace) -> int:
    return _cmd_resolve(args, "reject")


def _cmd_mcp(args: argparse.Namespace) -> int:
    try:
        from .mcp import main as _mcp_main
    except ImportError:
        print(
            "aim: MCP support requires the optional extra: pip install 'aimformat[mcp]'",
            file=sys.stderr,
        )
        return 2
    return _mcp_main(args)


def _cmd_show(args: argparse.Namespace) -> int:
    doc = AimDocument.load(args.file)
    if args.format == "json":
        summary_stale = None
        meta = doc.meta
        if meta and isinstance(meta.get("summary"), dict):
            summary_stale = meta["summary"].get("doc_hash") != doc.doc_hash
        print(
            json.dumps(
                {
                    "file": str(args.file),
                    "title": doc.title,
                    "lang": doc.lang,
                    "spec_version": doc.spec_version,
                    "seq": doc.seq,
                    "doc_hash": doc.doc_hash,
                    "summary_stale": summary_stale,
                    "canonical_note": doc.has_canonical_note(),
                    "chunk_ids": [c.id for c in doc.chunks],
                    "proposals": [
                        {
                            "id": p.id,
                            "action": p.action,
                            "target": p.target,
                            "author": _actor_str(p.author),
                            "explanation": p.explanation,
                        }
                        for p in doc.proposals
                    ],
                    "history_events": len(doc.history),
                },
                indent=2,
            )
        )
        return 0
    print(
        f"{doc.title!r} — spec {doc.spec_version}, seq {doc.seq}, "
        f"{len(doc.chunks)} chunks, {len(doc.proposals)} pending"
    )
    print(f"doc_hash {doc.doc_hash}")
    if doc.proposals:
        print("pending:")
        for p in doc.proposals:
            tgt = p.target or f"into {p.anchor_container}"
            print(f"  {p.id:>10}  {p.action:<7} {tgt:<12} {p.author.type:<6} {p.explanation or ''}")
    if doc.history:
        print("history:")
        for ev in doc.history:
            what = ev.kind if ev.kind != "direct_edit" else ev.action
            extra = ev.get("label") or ev.decision or ""
            print(f"  {ev.seq:>4}  {what:<10} {ev.target or '':<12} {extra}")
    return 0


def _cmd_flatten(args: argparse.Namespace) -> int:
    doc = AimDocument.load(args.file)
    doc.flatten(drop_embeddings=not args.keep_embeddings)
    out = Path(args.output or args.file)
    doc.save(out)
    print(f"wrote {out}")
    return 0


def _cmd_pack(args: argparse.Namespace) -> int:
    from .events import parse_actor

    doc = AimDocument.load(args.file)
    packed = doc.pack_assets(author=parse_actor(args.author))
    out = Path(args.output or args.file)
    doc.save(out)
    print(f"packed {packed} image{'s' if packed != 1 else ''}, wrote {out}")
    return 0


def _cmd_prune(args: argparse.Namespace) -> int:
    doc = AimDocument.load(args.file)
    # "seq number or checkpoint label": an exact label match wins, so a
    # checkpoint someone named "1" stays selectable; only an argument that
    # matches no label reads as a sequence number
    labels = {e.get("label") for e in doc.history if e.kind == "checkpoint"}
    before: int | str = (
        args.before if args.before in labels or not args.before.isdigit() else int(args.before)
    )
    dropped = doc.prune(before=before)
    out = Path(args.output or args.file)
    doc.save(out)
    print(f"dropped {dropped} event{'s' if dropped != 1 else ''}, wrote {out}")
    return 0


def _cmd_gc(args: argparse.Namespace) -> int:
    doc = AimDocument.load(args.file)
    collected = doc.gc_assets()
    out = Path(args.output or args.file)
    doc.save(out)
    print(f"collected {collected} dead asset{'s' if collected != 1 else ''}, wrote {out}")
    return 0


def _cmd_normalize(args: argparse.Namespace) -> int:
    """Tier-2 canonicalization: re-spell to the spec §11 normal form.

    Lossless and idempotent — attribute order, class-token order, style
    spelling and layout collapse to their canonical form; content (including
    out-of-vocabulary content, which stays the linter's to flag) is never
    coerced or dropped. `doc_hash` is computed over the canonical projection,
    so normalizing never changes it.

    Uses the same primitive as lint's C001 (`document_text` on the fragment
    as loaded — no machine-CSS refresh) so the two verbs can never disagree
    about what "canonical" means, and raw bytes end to end so newline
    translation can't mask or introduce differences.
    """
    from .canonical import document_text

    path = Path(args.file)
    original = path.read_bytes()
    doc = AimDocument.loads(original.decode("utf-8"))
    canonical_bytes = document_text(doc._fragment).encode("utf-8")
    changed = canonical_bytes != original
    if args.check:
        print(f"{path}: {'not canonical' if changed else 'canonical'}")
        return 1 if changed else 0
    out = Path(args.output or args.file)
    if not changed and out == path:
        print(f"{path}: already canonical")
        return 0
    out.write_bytes(canonical_bytes)
    print(f"wrote {out}")
    return 0


def _cmd_reconcile(args: argparse.Namespace) -> int:
    from .events import external

    doc = AimDocument.load(args.file)
    report = doc.reconcile(author=external("aim-reconcile"), dry_run=args.check)
    for old, new in report.assigned_ids:
        print(f"  id assigned: {old or '(unmarked)'} -> {new}")
    for pid in report.rejected_proposals:
        print(f"  proposal rejected (target gone): {pid}")
    print(report.summary())
    for problem in report.residual:
        print(f"aim: unrepairable: {problem}", file=sys.stderr)
    if args.check:
        return 1 if (report.changed or report.residual) else 0
    if report.changed or args.output:
        out = Path(args.output or args.file)
        doc.save(out)
        print(f"wrote {out}")
    return 1 if report.residual else 0


def _cmd_css(args: argparse.Namespace) -> int:
    if args.stats:
        s = css_stats()
        print(
            f"rules {s['rules']}  raw {s['raw_bytes'] / 1024:.1f} KB  "
            f"gzip {s['gzip_bytes'] / 1024:.1f} KB"
        )
        return 0
    sys.stdout.write(generate_aim_css())
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    from .convert import from_path

    out = Path(args.output)
    if out.exists() and not args.force:
        print(f"aim: {out} exists (use --force to overwrite)", file=sys.stderr)
        return 2
    try:
        doc = from_path(args.input, title=args.title, lang=args.lang)
    except UnicodeDecodeError:  # ValueError subclass: main() handles it
        raise
    except ValueError as exc:
        print(f"aim: {exc}", file=sys.stderr)
        return 2
    doc.save(out)
    print(f"wrote {out} ({len(doc.chunks)} chunks)")
    return 0


# per-format pending defaults and what each exporter understands
_EXPORT_PENDING = {
    ".docx": ("tracked", ("tracked", "accept-all", "reject-all")),
    ".md": ("drop", ("drop", "criticmarkup", "accept-all", "reject-all")),
    ".html": ("keep", ("keep", "accept-all", "reject-all")),
    ".pdf": ("keep", ("keep", "accept-all", "reject-all")),
}


def _cmd_export(args: argparse.Namespace) -> int:
    out = Path(args.output)
    if out.exists() and not args.force:
        print(f"aim: {out} exists (use --force to overwrite)", file=sys.stderr)
        return 2
    suffix = out.suffix.lower()
    if suffix not in _EXPORT_PENDING:
        print(
            f"aim: unsupported export format {suffix!r} "
            f"(supported: {', '.join(sorted(_EXPORT_PENDING))})",
            file=sys.stderr,
        )
        return 2
    default, allowed = _EXPORT_PENDING[suffix]
    pending = args.pending or default
    if pending not in allowed:
        print(
            f"aim: --pending {pending!r} not valid for {suffix} (allowed: {', '.join(allowed)})",
            file=sys.stderr,
        )
        return 2
    doc = AimDocument.load(args.input)
    if suffix == ".docx":
        from .export_docx import to_docx

        to_docx(doc, out, pending=pending)
    elif suffix == ".md":
        from .convert import to_markdown

        out.write_text(to_markdown(doc, pending=pending), "utf-8")
    elif suffix == ".html":
        from .convert import to_html

        out.write_text(to_html(doc, pending=pending), "utf-8")
    else:
        from .convert import to_pdf

        to_pdf(doc, out, pending=pending)
    print(f"wrote {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aim",
        description=f"Reference tooling for the .aim document format "
        f"(spec {REGISTRY.spec_version}).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"aimformat {__version__} (spec {REGISTRY.spec_version})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("lint", help="verify .aim documents")
    p.add_argument("files", nargs="+")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--quiet", action="store_true", help="print errors only")
    p.set_defaults(func=_cmd_lint)

    p = sub.add_parser("hash", help="print a document's doc_hash")
    p.add_argument("file")
    p.set_defaults(func=_cmd_hash)

    p = sub.add_parser("new", help="scaffold a minimal valid document")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--title", default="Untitled")
    p.add_argument("--lang", default="en")
    p.add_argument("--force", action="store_true", help="overwrite an existing file")
    p.set_defaults(func=_cmd_new)

    p = sub.add_parser(
        "note", help="add or refresh the agent note (spec §2.5); modifies files in place"
    )
    p.add_argument("files", nargs="+")
    p.add_argument(
        "--check",
        action="store_true",
        help="verify without writing; exit 1 when any file lacks a canonical note",
    )
    p.add_argument("--remove", action="store_true", help="strip the note instead of adding it")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.set_defaults(func=_cmd_note)

    p = sub.add_parser("show", help="overview: chunks, pending lane, history")
    p.add_argument("file")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.set_defaults(func=_cmd_show)

    p = sub.add_parser(
        "propose",
        help="append a proposal card to the pending lane; "
        "modifies the file in place unless -o is given",
    )
    actions = p.add_subparsers(dest="action", required=True)

    def _proposal_common(pp: argparse.ArgumentParser) -> None:
        pp.add_argument(
            "--author",
            default="external:aim-cli",
            help="human:ID | agent:MODEL | external:ID (default: external:aim-cli)",
        )
        pp.add_argument("--explanation", help="one-line why; raw-tier readers see only this")
        pp.add_argument("-o", "--output")
        pp.add_argument("--format", choices=["text", "json"], default="text")
        pp.set_defaults(func=_cmd_propose)

    def _payload_flags(pp: argparse.ArgumentParser) -> None:
        g = pp.add_mutually_exclusive_group(required=True)
        g.add_argument("--html", help="payload markup")
        g.add_argument("--html-file", help="read payload markup from a file")

    pa = actions.add_parser("modify", help="replace a chunk's markup")
    pa.add_argument("file")
    pa.add_argument("target", help="chunk id to modify")
    _payload_flags(pa)
    _proposal_common(pa)

    pa = actions.add_parser("add", help="insert new content")
    pa.add_argument("file")
    _payload_flags(pa)
    pa.add_argument("--container", default="body")
    pa.add_argument(
        "--after", help="anchor id ('first' = first position; default: end of container)"
    )
    _proposal_common(pa)

    pa = actions.add_parser("delete", help="remove a chunk")
    pa.add_argument("file")
    pa.add_argument("target", help="chunk id to delete")
    _proposal_common(pa)

    pa = actions.add_parser("move", help="move a chunk")
    pa.add_argument("file")
    pa.add_argument("target", help="chunk id to move")
    pa.add_argument("--container", default="body")
    pa.add_argument(
        "--after", help="anchor id ('first' = first position; default: end of container)"
    )
    _proposal_common(pa)

    pa = actions.add_parser("theme", help="change theme slots")
    pa.add_argument("file")
    pa.add_argument(
        "--set",
        action="append",
        required=True,
        metavar="SLOT=VALUE",
        help="e.g. brand-1=#333333 (the '--aim-' prefix is "
        "added for you; --set=--aim-brand-1=… also works)",
    )
    _proposal_common(pa)

    for verb, fn in (("accept", _cmd_accept), ("reject", _cmd_reject)):
        p = sub.add_parser(
            verb, help=f"{verb} pending proposals; modifies the file in place unless -o is given"
        )
        p.add_argument("file")
        p.add_argument("pids", nargs="*", metavar="PID")
        p.add_argument("--all", action="store_true", help=f"{verb} every pending proposal")
        p.add_argument(
            "--author", default="external:aim-cli", help="human:ID | agent:MODEL | external:ID"
        )
        p.add_argument("--explanation")
        p.add_argument("-o", "--output")
        p.add_argument("--format", choices=["text", "json"], default="text")
        p.set_defaults(func=fn)

    p = sub.add_parser(
        "flatten",
        help="drop history (and embeddings); modifies the file in place unless -o is given",
    )
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.add_argument("--keep-embeddings", action="store_true")
    p.set_defaults(func=_cmd_flatten)

    p = sub.add_parser(
        "pack",
        help="hoist embedded data images into the asset registry (spec §9); "
        "dead assets are collected as the final pass; modifies the file "
        "in place unless -o is given",
    )
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.add_argument(
        "--author", default="external:aim-cli", help="human:ID | agent:MODEL | external:ID"
    )
    p.set_defaults(func=_cmd_pack)

    p = sub.add_parser(
        "prune",
        help="truncate history before a seq number or checkpoint label; "
        "dead assets are collected as the final pass; modifies the file "
        "in place unless -o is given",
    )
    p.add_argument("file")
    p.add_argument(
        "before",
        help="seq number or checkpoint label to keep from; an exact label match wins",
    )
    p.add_argument("-o", "--output")
    p.set_defaults(func=_cmd_prune)

    p = sub.add_parser(
        "gc",
        help="collect asset symbols referenced by no body content, retained "
        "history payload, or pending card; modifies the file in place "
        "unless -o is given",
    )
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.set_defaults(func=_cmd_gc)

    p = sub.add_parser(
        "normalize",
        help="rewrite a document in canonical form (spec "
        "§11); lossless and idempotent — re-spells, "
        "never coerces; modifies the file in place "
        "unless -o is given",
    )
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.add_argument(
        "--check",
        action="store_true",
        help="report without writing; exit 1 when the file is not canonical",
    )
    p.set_defaults(func=_cmd_normalize)

    p = sub.add_parser(
        "reconcile",
        help="detect out-of-band edits and append reconcile "
        "events; modifies the file in place unless -o "
        "is given",
    )
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.add_argument(
        "--check",
        action="store_true",
        help="report drift without modifying anything; exit 1 when drift is found",
    )
    p.set_defaults(func=_cmd_reconcile)

    p = sub.add_parser("css", help="print the generated aim.css")
    p.add_argument("--stats", action="store_true")
    p.set_defaults(func=_cmd_css)

    p = sub.add_parser("import", help="convert md/txt/docx/pdf to .aim (by extension)")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--title", help="document title (default: derived from content or filename)")
    p.add_argument("--lang", default="en")
    p.add_argument("--force", action="store_true", help="overwrite an existing file")
    p.set_defaults(func=_cmd_import)

    p = sub.add_parser("export", help="convert .aim to docx/md/html/pdf (by extension)")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    p.add_argument(
        "--pending",
        help="pending-lane fate; per-format default: docx=tracked, md=drop, html/pdf=keep",
    )
    p.add_argument("--force", action="store_true", help="overwrite an existing file")
    p.set_defaults(func=_cmd_export)

    p = sub.add_parser("mcp", help="run the MCP server on stdio (pip install 'aimformat[mcp]')")
    p.set_defaults(func=_cmd_mcp)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # survive non-UTF8 pipes
        sys.stdout.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, IsADirectoryError, PermissionError) as exc:
        print(f"aim: {exc}", file=sys.stderr)
        return 2
    except UnicodeDecodeError as exc:
        print(f"aim: not a UTF-8 text file: {exc}", file=sys.stderr)
        return 1
    except AimError as exc:
        print(f"aim: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
