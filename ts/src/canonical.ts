/**
 * Canonical serialization and hashing (spec §11) — a direct port of the
 * Python SDK's `canonical.py`. Byte-determinism is load-bearing: `Chunk.html`
 * and `docHash` are defined as *these* bytes, and the parity goldens pin the
 * two implementations to each other field by field.
 */

import { Comment, Element, Text, type Nodeish } from "./dom.ts";
import {
  ATTR_FIRST,
  ATTR_LAST,
  STYLE_PROP_ORDER,
  SVG_CASE_ADJUST,
  VOID_ELEMENTS,
} from "./registry.data.ts";
import { sha256Prefixed } from "./sha256.ts";

export function escapeText(s: string): string {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

export function escapeAttr(s: string): string {
  return s.replaceAll("&", "&amp;").replaceAll('"', "&quot;");
}

/**
 * Compare strings by Unicode code point, matching Python's `str` ordering.
 * JS `<` compares UTF-16 code units, which ranks astral characters (encoded
 * as surrogate pairs, 0xD800–0xDFFF) below BMP characters above U+D7FF —
 * the opposite of their code-point order.
 */
export function compareCodePoints(a: string, b: string): number {
  let i = 0;
  while (i < a.length && i < b.length) {
    const ca = a.codePointAt(i)!;
    const cb = b.codePointAt(i)!;
    if (ca !== cb) return ca - cb;
    i += ca > 0xffff ? 2 : 1;
  }
  return a.length - i === 0 ? (b.length - i === 0 ? 0 : -1) : 1;
}

/** Class tokens sorted and de-duplicated (a set, canonically spelled). */
export function sortClassTokens(value: string): string {
  return [...new Set(value.split(/\s+/).filter((t) => t.length > 0))]
    .sort(compareCodePoints)
    .join(" ");
}

/**
 * Inline style as a normal form: whitelist properties in registry order,
 * later duplicates win, `; `-separated, no trailing semicolon. Unknown
 * properties (a lint error anyway) keep authored order at the end so the
 * violation stays visible rather than being reshuffled.
 */
export function normalizeStyle(value: string): string {
  const known = new Map<string, string>();
  const unknown: Array<[string, string]> = [];
  for (const piece of value.split(";")) {
    const trimmed = piece.trim();
    if (trimmed.length === 0 || !trimmed.includes(":")) continue;
    const colon = trimmed.indexOf(":");
    const prop = trimmed.slice(0, colon).trim();
    const val = trimmed.slice(colon + 1).trim();
    if (STYLE_PROP_ORDER.includes(prop)) known.set(prop, val);
    else unknown.push([prop, val]);
  }
  const ordered: Array<[string, string]> = [];
  for (const prop of STYLE_PROP_ORDER) {
    const val = known.get(prop);
    if (val !== undefined) ordered.push([prop, val]);
  }
  return [...ordered, ...unknown].map(([p, v]) => `${p}:${v}`).join("; ");
}

export function canonicalAttrs(el: Element, inSvg: boolean): string {
  const fix = (name: string): string =>
    inSvg ? (SVG_CASE_ADJUST[name] ?? name) : name;

  const remaining = new Map<string, string | null>();
  for (const [k, v] of el.attrs) {
    // HTML semantics: the FIRST duplicate wins, matching Element.get —
    // last-wins here would rename a chunk under `aim normalize` and
    // break the events targeting the id the reader resolved
    if (!remaining.has(k)) remaining.set(k, v);
  }
  const ordered: Array<[string, string | null]> = [];
  for (const k of ATTR_FIRST) {
    if (remaining.has(k)) {
      ordered.push([k, remaining.get(k)!]);
      remaining.delete(k);
    }
  }
  const tail: Array<[string, string | null]> = [];
  for (const k of ATTR_LAST) {
    if (remaining.has(k)) {
      tail.push([k, remaining.get(k)!]);
      remaining.delete(k);
    }
  }
  ordered.push(
    ...[...remaining.entries()].sort(([a], [b]) => compareCodePoints(a, b)),
  );
  ordered.push(...tail);

  const parts: string[] = [];
  for (const [k, rawValue] of ordered) {
    let v = rawValue;
    if (k === "class" && v !== null) {
      v = sortClassTokens(v);
      if (v.length === 0) continue; // empty class has no canonical spelling
    }
    if (k === "style" && v !== null) {
      v = normalizeStyle(v);
      if (v.length === 0) continue;
    }
    parts.push(v === null ? fix(k) : `${fix(k)}="${escapeAttr(v)}"`);
  }
  return parts.length > 0 ? ` ${parts.join(" ")}` : "";
}

/**
 * Inline canonical serialization of one node (no trailing newline).
 *
 * A normal form, not an echo: HTML void elements never carry a slash
 * however they were written, and foreign (SVG-context) elements with no
 * content always self-close (spec §11.1).
 */
export function serialize(node: Nodeish, inSvg = false): string {
  if (node instanceof Text) return escapeText(node.data);
  if (node instanceof Comment) return `<!--${node.data}-->`;
  const svgHere = inSvg || node.tag === "svg";
  const openTag = `<${node.tag}${canonicalAttrs(node, svgHere)}`;
  if (VOID_ELEMENTS.has(node.tag) && !svgHere) return `${openTag}>`;
  if (svgHere && node.children.length === 0 && node.raw === null)
    return `${openTag}/>`;
  if (node.selfClosing) return `${openTag}/>`;
  if (node.raw !== null) return `${openTag}>${node.raw}</${node.tag}>`;
  const inner = node.children.map((c) => serialize(c, svgHere)).join("");
  return `${openTag}>${inner}</${node.tag}>`;
}

/** A chunk's serialization: its member elements concatenated in order. */
export function serializeRun(members: readonly Element[]): string {
  return members.map((m) => serialize(m)).join("");
}

/**
 * The reduced-projection hash anchoring checkpoints (spec §11.3): the
 * `<html …>` open line, the settings-block line (when present), the theme
 * line (when present), and each body content construct line, LF-joined
 * with a trailing LF.
 */
export function docHash(
  htmlOpenLine: string,
  themeLine: string | null,
  constructLines: readonly string[],
  docSettingsLine: string | null = null,
): string {
  const lines = [htmlOpenLine];
  if (docSettingsLine !== null && docSettingsLine.length > 0)
    lines.push(docSettingsLine);
  if (themeLine !== null && themeLine.length > 0) lines.push(themeLine);
  lines.push(...constructLines);
  return sha256Prefixed(`${lines.join("\n")}\n`);
}
