// GENERATED FILE — do not edit by hand.
// Source: src/aimformat/registry.json (+ the aim-note template in
// src/aimformat/note.py). Regenerate: python3 scripts/gen_ts_registry.py

export const SPEC_VERSION = "0.2";

export const VOID_ELEMENTS: ReadonlySet<string> = new Set(["meta", "img", "br", "hr", "link", "input"]);

export const TABLE_SHELLS: ReadonlySet<string> = new Set(["thead", "tbody", "tfoot"]);

export const ATTR_FIRST: readonly string[] = ["data-aim", "data-aim-container", "id", "class", "style"];

export const ATTR_LAST: readonly string[] = ["src", "href"];

export const SVG_CASE_ADJUST: Readonly<Record<string, string>> = {"viewbox": "viewBox", "preserveaspectratio": "preserveAspectRatio"};

export const STYLE_PROP_ORDER: readonly string[] = ["left", "top", "width", "height", "transform", "z-index"];

export const SCRIPT_TYPES = {"meta": "application/aim-meta+json", "doc": "application/aim-doc+json", "history": "application/aim-history+jsonl", "embeddings": "application/aim-embeddings+jsonl"} as const;

export const PAGE_SIZES_MM: Readonly<Record<string, readonly number[]>> = {"A3": [297, 420], "A4": [210, 297], "A5": [148, 210], "Letter": [215.9, 279.4], "Legal": [215.9, 355.6], "Tabloid": [279.4, 431.8]};

export const PAGE_ORIENTATIONS: readonly string[] = ["portrait", "landscape"];

export const PAGE_DEFAULT: { size: string; orientation: string; margins: Readonly<Record<string, string>> } = {"size": "A4", "orientation": "portrait", "margins": {"top": "15mm", "right": "15mm", "bottom": "15mm", "left": "15mm"}};

export const MARGIN_PATTERN = new RegExp("^\\p{Nd}+(\\.\\p{Nd}+)?mm$", "u");

export const MARGIN_MAX_MM = 100;

// The canonical aim-note body (spec §2.5); `{version}` is interpolated.
export const NOTE_TEMPLATE = "aim-note: This file is an AIM document (open format, v{version}) — valid HTML plus\nchunk identity, a pending-suggestions lane, and an edit history.\nAgent docs: https://aimformat.com/llms.txt\nThe reliable way to edit this file is the `aimformat` tooling, which manages\nids, suggestions, and history for you: `pip install aimformat` for the `aim`\nCLI (`aim --help`); `pip install 'aimformat[mcp]'` adds its MCP server\n(`aim mcp`). An Agent Skill exists: `npx skills add tndmhq/aimformat`.\nHand-editing as plain text is the fallback; if you do: keep every data-aim id\nstable (never renumber or reuse; give new content a fresh id), treat the\naim-proposals appendix and the history script as append-only tool lanes, and\nvalidate with `aim lint`. Humans review in AIM editors:\nhttps://aimformat.com/editors";
