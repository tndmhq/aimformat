/**
 * Strict parser for the canonical .aim HTML subset (spec §11).
 *
 * This is NOT a general HTML5 parser. Canonical form has exactly one
 * spelling per construct — explicit open/close tags, double-quoted
 * attributes, raw UTF-8 with only `&amp; &lt; &gt; &quot;` escapes — and the
 * linter (C001) enforces it, so a small transparent scanner covers every
 * conforming document. Non-canonical input is rejected with a clear error
 * rather than repaired; that mirrors the Python SDK's `dom.py`, which also
 * refuses tag soup instead of guessing.
 *
 * One code path everywhere: pure string scanning, no DOMParser, no Node
 * APIs — identical behavior in browsers, Node, and workers.
 */

import { Comment, Element, Fragment, Text, type Nodeish } from "./dom.ts";
import { AimParseError } from "./errors.ts";
import { VOID_ELEMENTS } from "./registry.data.ts";

/** The core named references, keyed exactly as they appear in Python's
 * `html.entities.html5` (trailing semicolon included): the five canonical
 * .aim escapes plus their HTML-legacy uppercase spellings (`&APOS;` does
 * not exist in HTML and is not here). */
const NAMED_REFS: Readonly<Record<string, string>> = {
  "amp;": "&",
  "AMP;": "&",
  "lt;": "<",
  "LT;": "<",
  "gt;": ">",
  "GT;": ">",
  "quot;": '"',
  "QUOT;": '"',
  "apos;": "'",
};

/** The named references HTML resolves *without* a trailing semicolon: the
 * no-semicolon keys of Python's `html.entities.html5` (the HTML4 legacy
 * set). `html.unescape` — and so the Python reader — decodes these bare and
 * as the longest matching prefix of a longer name, so byte parity on
 * hand-edited input requires the same table here. */
const SEMICOLON_OPTIONAL_REFS: Readonly<Record<string, string>> = {
  AElig: "Æ",
  AMP: "&",
  Aacute: "Á",
  Acirc: "Â",
  Agrave: "À",
  Aring: "Å",
  Atilde: "Ã",
  Auml: "Ä",
  COPY: "©",
  Ccedil: "Ç",
  ETH: "Ð",
  Eacute: "É",
  Ecirc: "Ê",
  Egrave: "È",
  Euml: "Ë",
  GT: ">",
  Iacute: "Í",
  Icirc: "Î",
  Igrave: "Ì",
  Iuml: "Ï",
  LT: "<",
  Ntilde: "Ñ",
  Oacute: "Ó",
  Ocirc: "Ô",
  Ograve: "Ò",
  Oslash: "Ø",
  Otilde: "Õ",
  Ouml: "Ö",
  QUOT: '"',
  REG: "®",
  THORN: "Þ",
  Uacute: "Ú",
  Ucirc: "Û",
  Ugrave: "Ù",
  Uuml: "Ü",
  Yacute: "Ý",
  aacute: "á",
  acirc: "â",
  acute: "´",
  aelig: "æ",
  agrave: "à",
  amp: "&",
  aring: "å",
  atilde: "ã",
  auml: "ä",
  brvbar: "¦",
  ccedil: "ç",
  cedil: "¸",
  cent: "¢",
  copy: "©",
  curren: "¤",
  deg: "°",
  divide: "÷",
  eacute: "é",
  ecirc: "ê",
  egrave: "è",
  eth: "ð",
  euml: "ë",
  frac12: "½",
  frac14: "¼",
  frac34: "¾",
  gt: ">",
  iacute: "í",
  icirc: "î",
  iexcl: "¡",
  igrave: "ì",
  iquest: "¿",
  iuml: "ï",
  laquo: "«",
  lt: "<",
  macr: "¯",
  micro: "µ",
  middot: "·",
  nbsp: "\u00a0",
  not: "¬",
  ntilde: "ñ",
  oacute: "ó",
  ocirc: "ô",
  ograve: "ò",
  ordf: "ª",
  ordm: "º",
  oslash: "ø",
  otilde: "õ",
  ouml: "ö",
  para: "¶",
  plusmn: "±",
  pound: "£",
  quot: '"',
  raquo: "»",
  reg: "®",
  sect: "§",
  shy: "\u00ad",
  sup1: "¹",
  sup2: "²",
  sup3: "³",
  szlig: "ß",
  thorn: "þ",
  times: "×",
  uacute: "ú",
  ucirc: "û",
  ugrave: "ù",
  uml: "¨",
  uuml: "ü",
  yacute: "ý",
  yen: "¥",
  yuml: "ÿ",
};

/** HTML's numeric-reference replacement table (the windows-1252 remapping
 * plus NUL and CR), as Python's `html._invalid_charrefs` — html.parser
 * applies it, so byte parity requires the same decoded text here. */
const NUMERIC_REF_OVERRIDES: ReadonlyMap<number, string> = new Map([
  [0x00, "�"],
  [0x0d, "\r"],
  [0x80, "€"],
  [0x81, "\x81"],
  [0x82, "‚"],
  [0x83, "ƒ"],
  [0x84, "„"],
  [0x85, "…"],
  [0x86, "†"],
  [0x87, "‡"],
  [0x88, "ˆ"],
  [0x89, "‰"],
  [0x8a, "Š"],
  [0x8b, "‹"],
  [0x8c, "Œ"],
  [0x8d, "\x8d"],
  [0x8e, "Ž"],
  [0x8f, "\x8f"],
  [0x90, "\x90"],
  [0x91, "‘"],
  [0x92, "’"],
  [0x93, "“"],
  [0x94, "”"],
  [0x95, "•"],
  [0x96, "–"],
  [0x97, "—"],
  [0x98, "˜"],
  [0x99, "™"],
  [0x9a, "š"],
  [0x9b, "›"],
  [0x9c, "œ"],
  [0x9d, "\x9d"],
  [0x9e, "ž"],
  [0x9f, "Ÿ"],
]);

/** Control and noncharacter code points a numeric reference decodes to
 * nothing at all (Python's `html._invalid_codepoints`). */
function isDroppedCodePoint(cp: number): boolean {
  return (
    (cp >= 0x01 && cp <= 0x08) ||
    cp === 0x0b ||
    (cp >= 0x0e && cp <= 0x1f) ||
    (cp >= 0x7f && cp <= 0x9f) ||
    (cp >= 0xfdd0 && cp <= 0xfdef) ||
    (cp & 0xfffe) === 0xfffe // U+xFFFE/U+xFFFF of every plane
  );
}

/** One numeric reference's decoded text, per `html._replace_charref`. */
function decodeNumericRef(cp: number): string {
  const override = NUMERIC_REF_OVERRIDES.get(cp);
  if (override !== undefined) return override;
  if ((cp >= 0xd800 && cp <= 0xdfff) || cp > 0x10ffff) return "�";
  if (isDroppedCodePoint(cp)) return "";
  return String.fromCodePoint(cp);
}

/** Python's `html._charref` regex: what `html.unescape` — and therefore the
 * Python reader, in BODY TEXT (attribute values follow the stricter
 * `decodeAttrRefs` below) — treats as one character reference, semicolon
 * optional. */
const CHARREF_RE = /&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)/g;

/** Decode character references in body text the way the Python reader does
 * (`html.unescape` semantics), restricted to what canonical .aim can spell:
 * numeric forms plus the core named references, with the HTML legacy
 * semicolon-optional set for hand-edited input (`&#65b` → `Ab`,
 * `&amp b` → `& b`, `&copy 1` → `© 1`, longest-prefix `&ampx` → `&x`).
 *
 * The one deliberate divergence stays: an unknown *name-shaped* `&name;`
 * (e.g. `&eacute;`) is a parse error, not a full-table lookup — canonical
 * form writes non-ASCII as raw UTF-8, and silently guessing against a table
 * this library does not ship could contradict Python (`&ltcc;` is `⪦`,
 * not `<cc;`). */
function decodeRefs(s: string): string {
  if (!s.includes("&")) return s;
  return s.replace(CHARREF_RE, (match, body: string) => {
    if (body.startsWith("#")) {
      const hex = body[1] === "x" || body[1] === "X";
      const end = body.endsWith(";") ? -1 : body.length;
      return decodeNumericRef(
        parseInt(body.slice(hex ? 2 : 1, end), hex ? 16 : 10),
      );
    }
    const named = NAMED_REFS[body] ?? SEMICOLON_OPTIONAL_REFS[body];
    if (named !== undefined) return named;
    if (/^[a-zA-Z][a-zA-Z0-9]*;$/.test(body)) {
      throw new AimParseError(
        `unsupported character reference ${match} — canonical .aim uses raw UTF-8`,
      );
    }
    // no exact hit: longest legacy-name prefix decodes, the rest stays
    // literal (html._replace_charref's fallback loop)
    for (let cut = body.length - 1; cut >= 2; cut -= 1) {
      const prefix = SEMICOLON_OPTIONAL_REFS[body.slice(0, cut)];
      if (prefix !== undefined) return prefix + body.slice(cut);
    }
    return match;
  });
}

/** Python 3.13+'s `html.parser.attr_charref`: what one character reference
 * looks like inside an attribute value — a strict name (no dots/dashes)
 * with an optional trailing `;` or `=` captured into the match. */
const ATTR_CHARREF_RE =
  /&(#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*)[;=]?/g;

/** Decode character references in ATTRIBUTE values the way Python 3.13+
 * does (`html.parser._unescape_attrvalue`, the HTML5 attribute rule):
 * numeric references always decode, but a named reference only decodes on
 * an EXACT entity match that does not end with `=` — so `title="&quothi"`
 * and `href="?a=1&ampb=2"` stay literal while `&amp ` still becomes `& `.
 * (Python ≤3.12 ran full `html.unescape` here, decoding by longest prefix;
 * that is the version-fragile legacy behavior, not the spec.)
 *
 * The reader's deliberate strictness carries over from `decodeRefs`: an
 * unknown name-shaped `&name;` is a parse error rather than a silent
 * guess against a table this library does not ship. */
function decodeAttrRefs(s: string): string {
  if (!s.includes("&")) return s;
  return s.replace(ATTR_CHARREF_RE, (match, body: string) => {
    if (body.startsWith("#")) {
      const hex = body[1] === "x" || body[1] === "X";
      const decoded = decodeNumericRef(
        parseInt(body.slice(hex ? 2 : 1), hex ? 16 : 10),
      );
      // the regex eats a trailing `;` but a trailing `=` stays literal text
      return match.endsWith("=") ? decoded + "=" : decoded;
    }
    if (match.endsWith("=")) return match;
    const named = match.endsWith(";")
      ? (NAMED_REFS[`${body};`] ?? SEMICOLON_OPTIONAL_REFS[body])
      : SEMICOLON_OPTIONAL_REFS[body];
    if (named !== undefined) return named;
    if (match.endsWith(";")) {
      throw new AimParseError(
        `unsupported character reference ${match} — canonical .aim uses raw UTF-8`,
      );
    }
    return match;
  });
}

const TAG_NAME_RE = /[a-zA-Z][^\s/>]*/y;
const ATTR_NAME_RE = /[^\s=/>]+/y;

class Scanner {
  private readonly text: string;
  private pos: number = 0;
  readonly fragment = new Fragment();
  private readonly stack: Element[] = [];
  private rawEl: Element | null = null;

  constructor(text: string) {
    this.text = text;
  }

  private fail(message: string): never {
    throw new AimParseError(`${message} (offset ${this.pos})`);
  }

  private append(node: Nodeish): void {
    const top = this.stack[this.stack.length - 1];
    if (top !== undefined) top.children.push(node);
    else this.fragment.children.push(node);
  }

  parse(): Fragment {
    while (this.pos < this.text.length) {
      if (this.rawEl !== null) this.rawText();
      else this.markup();
    }
    if (this.rawEl !== null)
      this.fail(`unterminated <${this.rawEl.tag}> block`);
    if (this.stack.length > 0)
      this.fail(`unclosed <${this.stack[this.stack.length - 1]!.tag}>`);
    return this.fragment;
  }

  /** Inside script/style: everything up to the element's own close tag is
   * raw data — never entity-decoded, never treated as markup. */
  private rawText(): void {
    const el = this.rawEl!;
    // Python 3.13+ / HTML5 script-data scanning: the block ends at the first
    // case-insensitive `</tag` whose next character is a tag boundary
    // ([\t\n\r\f />]); anything else — `</scriptx`, `</ script` — is data.
    // (Python ≤3.12 also accepted `</ script`; that laxness is the
    // version-fragile side and is deliberately not matched.)
    const close = new RegExp(`</${el.tag}[\\t\\n\\r\\f />]`, "gi");
    close.lastIndex = this.pos;
    const m = close.exec(this.text);
    if (m === null) this.fail(`unterminated <${el.tag}> block`);
    // the end tag then consumes through its closing ">" (html.parser's
    // tolerant end-tag scan), so `</SCRIPT >` and `</script/>` both close
    const bound = m.index + m[0].length - 1;
    const gt = this.text[bound] === ">" ? bound : this.text.indexOf(">", bound);
    if (gt < 0) this.fail(`unterminated <${el.tag}> block`);
    el.raw = (el.raw ?? "") + this.text.slice(this.pos, m.index);
    this.pos = gt + 1;
    this.rawEl = null;
    this.stack.pop();
  }

  private markup(): void {
    const lt = this.text.indexOf("<", this.pos);
    if (lt !== this.pos) {
      const end = lt < 0 ? this.text.length : lt;
      this.append(new Text(decodeRefs(this.text.slice(this.pos, end))));
      this.pos = end;
      return;
    }
    if (this.text.startsWith("<!--", this.pos)) {
      const end = this.text.indexOf("-->", this.pos + 4);
      if (end < 0) this.fail("unterminated comment");
      this.append(new Comment(this.text.slice(this.pos + 4, end)));
      this.pos = end + 3;
      return;
    }
    if (this.text.startsWith("<!", this.pos)) {
      const end = this.text.indexOf(">", this.pos + 2);
      if (end < 0) this.fail("unterminated declaration");
      this.fragment.doctype = this.text.slice(this.pos + 2, end);
      this.pos = end + 1;
      return;
    }
    if (this.text.startsWith("</", this.pos)) {
      this.closeTag();
      return;
    }
    const next = this.text[this.pos + 1] ?? "";
    if (/[a-zA-Z]/.test(next)) {
      this.openTag();
      return;
    }
    this.fail(`stray "<" in content — canonical .aim escapes it as &lt;`);
  }

  private closeTag(): void {
    TAG_NAME_RE.lastIndex = this.pos + 2;
    const m = TAG_NAME_RE.exec(this.text);
    if (m === null) this.fail("malformed closing tag");
    const tag = m[0].toLowerCase();
    let i = TAG_NAME_RE.lastIndex;
    while (/\s/.test(this.text[i] ?? "")) i += 1;
    if (this.text[i] !== ">") this.fail(`malformed closing tag </${tag}`);
    this.pos = i + 1;
    for (let s = this.stack.length - 1; s >= 0; s -= 1) {
      if (this.stack[s]!.tag === tag) {
        this.stack.length = s; // implicitly closes anything still open above
        return;
      }
    }
    this.fail(`unmatched closing tag </${tag}>`);
  }

  private openTag(): void {
    TAG_NAME_RE.lastIndex = this.pos + 1;
    const m = TAG_NAME_RE.exec(this.text);
    if (m === null) this.fail("malformed start tag");
    const tag = m[0].toLowerCase();
    let i = TAG_NAME_RE.lastIndex;
    const attrs: Array<readonly [string, string | null]> = [];
    for (;;) {
      while (/\s/.test(this.text[i] ?? "")) i += 1;
      const ch = this.text[i];
      if (ch === undefined) this.fail(`unterminated <${tag}> tag`);
      if (ch === ">" || ch === "/") break;
      ATTR_NAME_RE.lastIndex = i;
      const nm = ATTR_NAME_RE.exec(this.text);
      if (nm === null) this.fail(`malformed attribute in <${tag}>`);
      const name = nm[0].toLowerCase();
      i = ATTR_NAME_RE.lastIndex;
      if (this.text[i] !== "=") {
        attrs.push([name, null]); // bare attribute (e.g. data-aim-theme)
        continue;
      }
      i += 1;
      if (this.text[i] !== '"') {
        this.pos = i;
        this.fail(`attribute ${name} in <${tag}> is not double-quoted`);
      }
      const close = this.text.indexOf('"', i + 1);
      if (close < 0) this.fail(`unterminated attribute value in <${tag}>`);
      attrs.push([name, decodeAttrRefs(this.text.slice(i + 1, close))]);
      i = close + 1;
    }
    let selfClosing = false;
    if (this.text[i] === "/") {
      if (this.text[i + 1] !== ">") this.fail(`malformed tag <${tag}>`);
      selfClosing = true;
      i += 1;
    }
    this.pos = i + 1;
    const el = new Element(tag, attrs, selfClosing);
    this.append(el);
    if (selfClosing) return;
    if (tag === "script" || tag === "style") {
      el.raw = "";
      this.rawEl = el;
      this.stack.push(el);
    } else if (!VOID_ELEMENTS.has(tag)) {
      this.stack.push(el);
    }
  }
}

/** Parse a document or fragment into a transparent tree. */
export function parseHtml(text: string): Fragment {
  return new Scanner(text).parse();
}

/** Parse a body-context fragment; returns its top-level nodes. */
export function parseFragment(markup: string): Nodeish[] {
  return parseHtml(markup).children;
}
