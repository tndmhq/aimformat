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

const NAMED_REFS: Readonly<Record<string, string>> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
};

/** Decode character references the way canonical .aim spells text: the five
 * core named references plus numeric forms. Any other named reference is a
 * parse error — canonical form writes non-ASCII as raw UTF-8, so `&eacute;`
 * and friends never appear in a conforming file. */
function decodeRefs(s: string): string {
  if (!s.includes("&")) return s;
  return s.replace(
    /&(#[xX]?[0-9a-fA-F]*|[a-zA-Z][a-zA-Z0-9]*);?/g,
    (match, body: string) => {
      if (!match.endsWith(";")) return match; // bare "&": literal, like html.parser
      if (body.startsWith("#")) {
        const hex = body[1] === "x" || body[1] === "X";
        const digits = body.slice(hex ? 2 : 1);
        if (!digits || (!hex && /[^0-9]/.test(digits))) return match;
        const cp = parseInt(digits, hex ? 16 : 10);
        if (!Number.isFinite(cp) || cp < 0 || cp > 0x10ffff) {
          throw new AimParseError(
            `invalid numeric character reference ${match}`,
          );
        }
        return String.fromCodePoint(cp);
      }
      const named = NAMED_REFS[body];
      if (named === undefined) {
        throw new AimParseError(
          `unsupported character reference ${match} — canonical .aim uses raw UTF-8`,
        );
      }
      return named;
    },
  );
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
    const close = `</${el.tag}`;
    const at = this.text.indexOf(close, this.pos);
    if (at < 0) this.fail(`unterminated <${el.tag}> block`);
    const tail = /^\s*>/.exec(this.text.slice(at + close.length));
    if (tail === null) {
      this.pos = at;
      this.fail(`raw <${el.tag}> content contains a stray "${close}"`);
    }
    el.raw = (el.raw ?? "") + this.text.slice(this.pos, at);
    this.pos = at + close.length + tail[0].length;
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
      attrs.push([name, decodeRefs(this.text.slice(i + 1, close))]);
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
