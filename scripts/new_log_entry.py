#!/usr/bin/env python3
"""
Scaffold a new episodic memory entry in ``docs/log/``.

Creates ``docs/log/YYYY-MM-DD_HHMM_<type>_<slug>.md`` (time of day in
UTC — per the convention in ``docs/README.md``) with the
frontmatter skeleton, and inserts the matching row at the top of
``docs/log/index.md``. Refuses to overwrite an existing entry.

Stdlib only — runs in any Python ≥ 3.9, no env needed::

    python3 scripts/new_log_entry.py plan spec-v01 \\
        --what "Draft the v0.1 spec structure."

Then open the created file and write the content. The log in this repo is
kept light (durable decisions, major plans/reviews only — see
``docs/README.md``). Ported from the bluegov-it-hr scaffolder.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "docs" / "log"
INDEX = LOG_DIR / "index.md"

TYPES = ("plan", "review", "report", "status", "decision", "event")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

ENTRY_TEMPLATE = """\
---
date: {date} {time}
type: {type}
status: active
related: []
---

# {title}

<!-- Write the entry here. Keep repo-relative links. When the work completes:
     flip `status:` above to done (or superseded), update this entry's row in
     index.md, and promote durable lessons into docs/knowledge/
     (see docs/README.md, rule 4). -->
"""


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Scaffold a docs/log/ entry + index row (see docs/README.md)."
    )
    ap.add_argument("type", choices=TYPES)
    ap.add_argument("slug", help="kebab-case slug, e.g. spec-v01")
    ap.add_argument("--title", help="entry title (default: derived from type + slug)")
    ap.add_argument(
        "--what",
        default="TODO — one-line summary.",
        help="one-line summary for the index 'What' column",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="print what would be written without touching files"
    )
    args = ap.parse_args()

    if not SLUG_RE.match(args.slug):
        ap.error(f"slug {args.slug!r} is not kebab-case ([a-z0-9-])")

    now = datetime.now(timezone.utc)
    date, hhmm, time_col = now.strftime("%Y-%m-%d"), now.strftime("%H%M"), now.strftime("%H:%M")
    name = f"{date}_{hhmm}_{args.type}_{args.slug}.md"
    path = LOG_DIR / name
    if path.exists():
        print(f"refusing to overwrite existing {path}", file=sys.stderr)
        return 1

    title = args.title or f"{args.type.capitalize()}: {args.slug.replace('-', ' ')}"
    entry = ENTRY_TEMPLATE.format(date=date, time=time_col, type=args.type, title=title)
    row = f"| {date} {time_col} | {args.type} | [{args.slug}]({name}) | active | {args.what} |\n"

    index_text = INDEX.read_text(encoding="utf-8")
    sep = re.search(r"^\|---.*\|\n", index_text, flags=re.M)
    if not sep:
        print(f"could not find the table separator row in {INDEX}", file=sys.stderr)
        return 1
    new_index = index_text[: sep.end()] + row + index_text[sep.end() :]

    if args.dry_run:
        print(f"would create {path.relative_to(REPO_ROOT)}:\n{entry}")
        print(f"would insert into {INDEX.relative_to(REPO_ROOT)}:\n{row}", end="")
        return 0

    path.write_text(entry, encoding="utf-8")
    INDEX.write_text(new_index, encoding="utf-8")
    print(f"created {path.relative_to(REPO_ROOT)}")
    print(f"indexed in {INDEX.relative_to(REPO_ROOT)} — now write the entry content")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
