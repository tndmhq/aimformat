/**
 * @aimformat/reader — official TypeScript read library for the .aim format.
 *
 * Read model only, writes never: parse a canonical `.aim` document string
 * into a read-only projection (chunks, containers, proposals, theme, page
 * setup, doc hash). Writes go through the Python SDK (`pip install
 * aimformat`), whose read surface this mirrors 1-to-1.
 */

export { AimDocument, parse, renderNote } from "./document.ts";
export type {
  AimNode,
  Author,
  Chunk,
  Container,
  PageSetup,
  Proposal,
  Stylesheet,
} from "./document.ts";
export { AimError, AimParseError } from "./errors.ts";
export { parseHtml, parseFragment } from "./parser.ts";
export { Comment, Element, Fragment, Text, type Nodeish } from "./dom.ts";
export {
  canonicalAttrs,
  docHash,
  escapeAttr,
  escapeText,
  normalizeStyle,
  serialize,
  serializeRun,
  sortClassTokens,
} from "./canonical.ts";
export { sha256Hex, sha256Prefixed } from "./sha256.ts";
export {
  SPEC_VERSION,
  STYLE_PROP_ORDER,
  STYLE_PROP_PAINT,
  STYLE_PROP_PATTERNS,
} from "./registry.data.ts";
