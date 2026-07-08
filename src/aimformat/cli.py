"""The ``aim`` command-line tool.

    aim lint FILE...        verify documents (all findings in one run)
    aim hash FILE           print the current doc_hash
    aim new -o FILE         scaffold a minimal valid document
    aim show FILE           human-readable history / pending-lane overview
    aim flatten FILE        drop history (+embeddings) -> clean file
    aim reconcile FILE      detect out-of-band edits; append reconcile events
    aim css                 print the generated aim.css for this spec version
    aim import IN -o F.aim  convert md/txt/docx/pdf to .aim
    aim export F.aim -o OUT convert .aim to docx/md/html/pdf (by extension)

Exit codes: 0 ok · 1 lint errors / verification failure · 2 usage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .css import css_stats, generate_aim_css
from .document import AimDocument, new_document
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
            payload.append({"file": str(path),
                            "errors": len(errors), "warnings": len(warnings),
                            "findings": [f.__dict__ for f in findings]})
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


def _cmd_show(args: argparse.Namespace) -> int:
    doc = AimDocument.load(args.file)
    print(f"{doc.title!r} — spec {doc.spec_version}, seq {doc.seq}, "
          f"{len(doc.chunks)} chunks, {len(doc.proposals)} pending")
    print(f"doc_hash {doc.doc_hash}")
    if doc.proposals:
        print("pending:")
        for p in doc.proposals:
            tgt = p.target or f"into {p.anchor_container}"
            print(f"  {p.id:>10}  {p.action:<7} {tgt:<12} "
                  f"{p.author.type:<6} {p.explanation or ''}")
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


def _cmd_reconcile(args: argparse.Namespace) -> int:
    from .events import external
    doc = AimDocument.load(args.file)
    report = doc.reconcile(author=external("aim-reconcile"),
                           dry_run=args.check)
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
        print(f"rules {s['rules']}  raw {s['raw_bytes'] / 1024:.1f} KB  "
              f"gzip {s['gzip_bytes'] / 1024:.1f} KB")
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
    ".md": ("drop", ("drop", "criticmarkup")),
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
        print(f"aim: unsupported export format {suffix!r} "
              f"(supported: {', '.join(sorted(_EXPORT_PENDING))})",
              file=sys.stderr)
        return 2
    default, allowed = _EXPORT_PENDING[suffix]
    pending = args.pending or default
    if pending not in allowed:
        print(f"aim: --pending {pending!r} not valid for {suffix} "
              f"(allowed: {', '.join(allowed)})", file=sys.stderr)
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
                    f"(spec {REGISTRY.spec_version}).")
    parser.add_argument("--version", action="version",
                        version=f"aimformat {__version__} "
                                f"(spec {REGISTRY.spec_version})")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("lint", help="verify .aim documents")
    p.add_argument("files", nargs="+")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--quiet", action="store_true",
                   help="print errors only")
    p.set_defaults(func=_cmd_lint)

    p = sub.add_parser("hash", help="print a document's doc_hash")
    p.add_argument("file")
    p.set_defaults(func=_cmd_hash)

    p = sub.add_parser("new", help="scaffold a minimal valid document")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--title", default="Untitled")
    p.add_argument("--lang", default="en")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing file")
    p.set_defaults(func=_cmd_new)

    p = sub.add_parser("show", help="overview: chunks, pending lane, history")
    p.add_argument("file")
    p.set_defaults(func=_cmd_show)

    p = sub.add_parser("flatten",
                       help="drop history (and embeddings); modifies the "
                            "file in place unless -o is given")
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.add_argument("--keep-embeddings", action="store_true")
    p.set_defaults(func=_cmd_flatten)

    p = sub.add_parser("reconcile",
                       help="detect out-of-band edits and append reconcile "
                            "events; modifies the file in place unless -o "
                            "is given")
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.add_argument("--check", action="store_true",
                   help="report drift without modifying anything; "
                        "exit 1 when drift is found")
    p.set_defaults(func=_cmd_reconcile)

    p = sub.add_parser("css", help="print the generated aim.css")
    p.add_argument("--stats", action="store_true")
    p.set_defaults(func=_cmd_css)

    p = sub.add_parser("import",
                       help="convert md/txt/docx/pdf to .aim (by extension)")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--title", help="document title (default: derived from "
                                   "content or filename)")
    p.add_argument("--lang", default="en")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing file")
    p.set_defaults(func=_cmd_import)

    p = sub.add_parser("export",
                       help="convert .aim to docx/md/html/pdf (by extension)")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--pending",
                   help="pending-lane fate; per-format default: "
                        "docx=tracked, md=drop, html/pdf=keep")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing file")
    p.set_defaults(func=_cmd_export)
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
