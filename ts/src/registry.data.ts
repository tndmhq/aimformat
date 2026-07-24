// GENERATED FILE — do not edit by hand.
// Source: src/aimformat/registry.json (+ the aim-note template in
// src/aimformat/note.py). Regenerate: python3 scripts/gen_ts_registry.py

export const SPEC_VERSION = "0.4";

export const VOID_ELEMENTS: ReadonlySet<string> = new Set(["meta", "img", "br", "hr", "link", "input"]);

export const TABLE_SHELLS: ReadonlySet<string> = new Set(["thead", "tbody", "tfoot"]);

export const ATTR_FIRST: readonly string[] = ["data-aim", "data-aim-container", "id", "class", "style"];

export const ATTR_LAST: readonly string[] = ["src", "href"];

export const SVG_CASE_ADJUST: Readonly<Record<string, string>> = {"viewbox": "viewBox", "preserveaspectratio": "preserveAspectRatio"};

export const STYLE_PROP_ORDER: readonly string[] = ["left", "top", "width", "height", "transform", "z-index", "color", "background-color", "border-color", "font-size", "font-family"];

// Per-property value grammars, so a consumer validates against registry
// data instead of maintaining a third copy of the grammar.
export const STYLE_PROP_PATTERNS: Readonly<Record<string, RegExp>> = {
  "left": new RegExp("^-?\\p{Nd}+(\\.\\p{Nd}+)?px$", "u"),
  "top": new RegExp("^-?\\p{Nd}+(\\.\\p{Nd}+)?px$", "u"),
  "width": new RegExp("^\\p{Nd}+(\\.\\p{Nd}+)?px$", "u"),
  "height": new RegExp("^\\p{Nd}+(\\.\\p{Nd}+)?px$", "u"),
  "transform": new RegExp("^(rotate\\(-?\\p{Nd}+(\\.\\p{Nd}+)?deg\\)|translate\\(-?\\p{Nd}+(\\.\\p{Nd}+)?px, ?-?\\p{Nd}+(\\.\\p{Nd}+)?px\\)|scale\\(\\p{Nd}+(\\.\\p{Nd}+)?\\))( (rotate\\(-?\\p{Nd}+(\\.\\p{Nd}+)?deg\\)|translate\\(-?\\p{Nd}+(\\.\\p{Nd}+)?px, ?-?\\p{Nd}+(\\.\\p{Nd}+)?px\\)|scale\\(\\p{Nd}+(\\.\\p{Nd}+)?\\)))*$", "u"),
  "z-index": new RegExp("^-?\\p{Nd}+$", "u"),
  "color": new RegExp("^#[0-9a-f]{6}$", "u"),
  "background-color": new RegExp("^#[0-9a-f]{6}$", "u"),
  "border-color": new RegExp("^#[0-9a-f]{6}$", "u"),
  "font-size": new RegExp("^\\p{Nd}+(\\.\\p{Nd}+)?pt$", "u"),
  "font-family": new RegExp("^[A-Za-z0-9 ,'\\-]+$", "u"),
};

// The subset of STYLE_PROP_ORDER that carries literal paint (spec §3.3);
// the rest is slide geometry and literal typography.
export const STYLE_PROP_PAINT: readonly string[] = ["color", "background-color", "border-color"];

// First spec version whose style grammar includes literal paint.
export const STYLE_PROP_PAINT_SINCE = "0.3";

export const SCRIPT_TYPES = {"meta": "application/aim-meta+json", "doc": "application/aim-doc+json", "history": "application/aim-history+jsonl", "embeddings": "application/aim-embeddings+jsonl"} as const;

export const PAGE_SIZES_MM: Readonly<Record<string, readonly number[]>> = {"A3": [297, 420], "A4": [210, 297], "A5": [148, 210], "Letter": [215.9, 279.4], "Legal": [215.9, 355.6], "Tabloid": [279.4, 431.8]};

export const PAGE_ORIENTATIONS: readonly string[] = ["portrait", "landscape"];

export const PAGE_DEFAULT: { size: string; orientation: string; margins: Readonly<Record<string, string>> } = {"size": "A4", "orientation": "portrait", "margins": {"top": "15mm", "right": "15mm", "bottom": "15mm", "left": "15mm"}};

export const MARGIN_PATTERN = new RegExp("^\\p{Nd}+(\\.\\p{Nd}+)?mm$", "u");

export const MARGIN_MAX_MM = 100;

// The canonical aim-note body (spec §2.5); `{version}` is interpolated.
export const NOTE_TEMPLATE = "aim-note: This file is an AIM document (open format, v{version}) — valid HTML plus\nchunk identity, a pending-suggestions lane, and an edit history.\nAgent docs: https://aimformat.com/llms.txt\nThe reliable way to edit this file is the `aimformat` tooling, which manages\nids, suggestions, and history for you: `pip install aimformat` for the `aim`\nCLI (`aim --help`); `pip install 'aimformat[mcp]'` adds its MCP server\n(`aim mcp`). An Agent Skill exists: `npx skills add tndmhq/aimformat`.\nHand-editing as plain text is the fallback; if you do: keep every data-aim id\nstable (never renumber or reuse; give new content a fresh id), treat the\naim-proposals appendix and the history script as append-only tool lanes, and\nvalidate with `aim lint`. Humans review in AIM editors:\nhttps://aimformat.com/editors";
