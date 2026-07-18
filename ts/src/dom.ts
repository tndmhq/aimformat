/**
 * Minimal DOM for .aim documents — the TypeScript twin of the Python SDK's
 * `dom.py`. A deliberately small tree (`Element` / `Text` / `Comment`) that
 * reports the file exactly as written: .aim canonical form (spec §11) has no
 * implied tags and no tag soup, so a transparent tree is both sufficient and
 * what byte-exact canonical round-trips require.
 *
 * Raw-text elements (`script` / `style`) keep their content in `raw`.
 */

export type Nodeish = Element | Text | Comment;

export class Text {
  data: string;
  constructor(data: string) {
    this.data = data;
  }
}

export class Comment {
  data: string;
  constructor(data: string) {
    this.data = data;
  }
}

export class Element {
  readonly tag: string;
  /** Attributes in document order; `null` value = bare attribute. */
  readonly attrs: Array<readonly [string, string | null]>;
  readonly children: Nodeish[] = [];
  readonly selfClosing: boolean;
  /** script/style raw content (never entity-decoded). */
  raw: string | null = null;

  constructor(
    tag: string,
    attrs: Array<readonly [string, string | null]> = [],
    selfClosing = false,
  ) {
    this.tag = tag;
    this.attrs = attrs;
    this.selfClosing = selfClosing;
  }

  /** Attribute value; `""` for a bare attribute, `null` when absent. */
  get(name: string): string | null {
    for (const [k, v] of this.attrs) {
      if (k === name) return v ?? "";
    }
    return null;
  }

  has(name: string): boolean {
    return this.attrs.some(([k]) => k === name);
  }

  get chunkId(): string | null {
    return this.get("data-aim");
  }

  get containerId(): string | null {
    return this.get("data-aim-container");
  }

  elements(): Element[] {
    return this.children.filter((c): c is Element => c instanceof Element);
  }

  /** Depth-first pre-order over this element and all element descendants. */
  *iter(): Generator<Element> {
    yield this;
    for (const child of this.elements()) yield* child.iter();
  }

  find(pred: (el: Element) => boolean): Element | null {
    for (const el of this.iter()) if (pred(el)) return el;
    return null;
  }

  /** Concatenated text content (raw script/style content excluded). */
  text(): string {
    let out = "";
    for (const child of this.children) {
      if (child instanceof Text) out += child.data;
      else if (child instanceof Element) out += child.text();
    }
    return out;
  }
}

export class Fragment {
  doctype: string | null = null;
  readonly children: Nodeish[] = [];

  elements(): Element[] {
    return this.children.filter((c): c is Element => c instanceof Element);
  }
}
