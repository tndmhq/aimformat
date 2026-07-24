#!/usr/bin/env python3
"""Generate ts/src/registry.data.ts from the machine-readable registry.

The TypeScript reader must never hand-maintain vocabulary tables:
``src/aimformat/registry.json`` stays the single source of truth and this
script derives the subset the read path needs (canonical-form tables, script
types, page geometry, the aim-note template). ``tests/test_ts_registry.py``
fails when the committed output is stale. Run from the repo root:
python3 scripts/gen_ts_registry.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from aimformat import note  # noqa: E402

OUT = ROOT / "ts" / "src" / "registry.data.ts"


def _ts(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=False)


def _ts_regex(pattern: str) -> str:
    """Render a Python ``re`` pattern as an equivalent JS RegExp: Python's
    ``\\d`` matches any Unicode decimal digit (Nd), JS's only ASCII, so it
    becomes ``\\p{Nd}`` compiled with the ``u`` flag."""
    translated = pattern.replace("\\d", "\\p{Nd}")
    return f'new RegExp({_ts(translated)}, "u")'


def render() -> str:
    registry = json.loads((ROOT / "src" / "aimformat" / "registry.json").read_text("utf-8"))
    lines = [
        "// GENERATED FILE — do not edit by hand.",
        "// Source: src/aimformat/registry.json (+ the aim-note template in",
        "// src/aimformat/note.py). Regenerate: python3 scripts/gen_ts_registry.py",
        "",
        f"export const SPEC_VERSION = {_ts(registry['spec_version'])};",
        "",
        "export const VOID_ELEMENTS: ReadonlySet<string> = new Set("
        f"{_ts(registry['elements']['void'])});",
        "",
        "export const TABLE_SHELLS: ReadonlySet<string> = new Set("
        f"{_ts(registry['elements']['table_shells'])});",
        "",
        f"export const ATTR_FIRST: readonly string[] = {_ts(registry['attr_order']['first'])};",
        "",
        f"export const ATTR_LAST: readonly string[] = {_ts(registry['attr_order']['last'])};",
        "",
        "export const SVG_CASE_ADJUST: Readonly<Record<string, string>> = "
        f"{_ts(registry['svg_case_adjust'])};",
        "",
        "export const STYLE_PROP_ORDER: readonly string[] = "
        f"{_ts(registry['style_props']['order'])};",
        "",
        "// Per-property value grammars, so a consumer validates against registry",
        "// data instead of maintaining a third copy of the grammar.",
        "export const STYLE_PROP_PATTERNS: Readonly<Record<string, RegExp>> = {",
        *[
            f"  {_ts(prop)}: {_ts_regex(pattern)},"
            for prop, pattern in registry["style_props"]["patterns"].items()
        ],
        "};",
        "",
        "// The subset of STYLE_PROP_ORDER that carries literal paint (spec §3.3);",
        "// the rest is slide geometry and literal typography.",
        "export const STYLE_PROP_PAINT: readonly string[] = "
        f"{_ts(registry['style_props']['paint'])};",
        "",
        "// First spec version whose style grammar includes literal paint.",
        f"export const STYLE_PROP_PAINT_SINCE = {_ts(registry['style_props']['paint_since'])};",
        "",
        f"export const SCRIPT_TYPES = {_ts(registry['sections']['script_types'])} as const;",
        "",
        "export const PAGE_SIZES_MM: Readonly<Record<string, readonly number[]>> = "
        f"{_ts(registry['page']['sizes_mm'])};",
        "",
        "export const PAGE_ORIENTATIONS: readonly string[] = "
        f"{_ts(registry['page']['orientations'])};",
        "",
        "export const PAGE_DEFAULT: { size: string; orientation: string; "
        "margins: Readonly<Record<string, string>> } = "
        f"{_ts(registry['page']['default'])};",
        "",
        f"export const MARGIN_PATTERN = {_ts_regex(registry['page']['margin_pattern'])};",
        "",
        f"export const MARGIN_MAX_MM = {_ts(registry['page']['margin_max_mm'])};",
        "",
        "// The canonical aim-note body (spec §2.5); `{version}` is interpolated.",
        f"export const NOTE_TEMPLATE = {_ts(note._TEMPLATE)};",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), "utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
