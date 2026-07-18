/**
 * The read-only .aim projection — the TypeScript twin of the Python SDK's
 * `AimDocument` read surface (`document.py`), plus an explicit ordered node
 * tree. Field semantics mirror Python 1-to-1 where the concepts map; the
 * parity goldens under `tests/parity/` pin the two implementations to each
 * other, with `spec.md` as the ground truth when they disagree.
 */

import { Comment, Element, Fragment, type Nodeish } from "./dom.ts";
import {
  canonicalAttrs,
  docHash,
  serialize,
  serializeRun,
} from "./canonical.ts";
import { AimError, AimParseError } from "./errors.ts";
import { parseHtml } from "./parser.ts";
import {
  MARGIN_MAX_MM,
  MARGIN_PATTERN,
  NOTE_TEMPLATE,
  PAGE_DEFAULT,
  PAGE_ORIENTATIONS,
  PAGE_SIZES_MM,
  SCRIPT_TYPES,
  SPEC_VERSION,
  TABLE_SHELLS,
} from "./registry.data.ts";

const BODY_SECTIONS = new Set(["aim-proposals", "aim-assets", "script"]);
const NOTE_SIGIL = "aim-note:";

// -- public value types ------------------------------------------------------

/** One chunk (possibly a multi-element run), read-only. */
export interface Chunk {
  readonly kind: "chunk";
  readonly id: string;
  /** `"body"`, a list/table container id, or a slide id. */
  readonly container: string;
  /** Member tags in document order; runs are first-class (length > 1). */
  readonly tags: readonly string[];
  /** Canonical serialization (run members concatenated). */
  readonly html: string;
  readonly text: string;
  readonly tag: string;
  readonly isRun: boolean;
}

/** One container (list, table, or slide) with its ordered members. */
export interface Container {
  readonly kind: "container";
  readonly id: string;
  readonly tag: string;
  /** `"body"` or the enclosing container's id (e.g. a list inside a slide). */
  readonly container: string;
  /** Attributes as written (bare attributes read as `""`). */
  readonly attrs: Readonly<Record<string, string>>;
  /** Ordered members: item chunks and nested containers (recursive). */
  readonly members: readonly AimNode[];
}

/** A top-level or nested content construct: chunk or container. */
export type AimNode = Chunk | Container;

/** Who authored a proposal: mirrors the event actor object. */
export interface Author {
  readonly type: string;
  readonly id: string | null;
  readonly model: string | null;
}

/** One pending proposal card (spec §5), read-only. */
export interface Proposal {
  readonly id: string;
  readonly action: string;
  /** `data-for` (null for add). */
  readonly target: string | null;
  readonly author: Author;
  readonly at: string;
  readonly explanation: string | null;
  /** Canonical payload serialization (null for payloadless cards). */
  readonly payloadHtml: string | null;
  readonly anchorContainer: string | null;
  /** null = first position OR n/a (see action). */
  readonly anchorAfter: string | null;
  /** thead/tbody/tfoot for rows in table containers. */
  readonly anchorShell: string | null;
  readonly dependsOn: string | null;
  readonly batch: string | null;
}

/** The resolved page setup (registry defaults fill absent fields). */
export interface PageSetup {
  readonly size: string;
  readonly orientation: string;
  readonly marginsMm: Readonly<
    Record<"top" | "right" | "bottom" | "left", number>
  >;
  readonly pageWidthMm: number;
  readonly pageHeightMm: number;
  readonly contentWidthMm: number;
  readonly contentHeightMm: number;
}

/** The embedded machine-managed stylesheet (spec §3.4). */
export interface Stylesheet {
  /** The `data-aim-css` version marker. */
  readonly version: string;
  readonly css: string;
}

type JsonObject = Record<string, unknown>;

// -- page setup (mirrors pagesetup.py) --------------------------------------

const SIDES = ["top", "right", "bottom", "left"] as const;

const DECIMAL_DIGIT = /^\p{Nd}$/u;

/** The numeric value of a Unicode decimal digit. Nd code points sit in
 * ascending 0-9 decades (adjacent decades exist, e.g. the mathematical
 * digits), so the value is the distance to the first non-digit below,
 * mod 10. */
function decimalDigitValue(cp: number): number {
  let steps = 0;
  while (
    cp - steps > 0 &&
    DECIMAL_DIGIT.test(String.fromCodePoint(cp - steps - 1))
  ) {
    steps += 1;
  }
  return steps % 10;
}

/** Parse a margin number the way Python ``float`` does: any Unicode decimal
 * digit maps to its digit value (``float("١٥")`` is 15.0), the rest of the
 * grammar — one optional ASCII "." — passes through. */
function marginNumber(text: string): number {
  let ascii = "";
  for (const ch of text) {
    const cp = ch.codePointAt(0)!;
    if (cp >= 0x30 && cp <= 0x39) ascii += ch;
    else if (DECIMAL_DIGIT.test(ch)) ascii += String(decimalDigitValue(cp));
    else ascii += ch;
  }
  return parseFloat(ascii);
}

function pageSetupFromObj(obj: unknown): PageSetup {
  if (typeof obj !== "object" || obj === null || Array.isArray(obj)) {
    throw new AimError("page setup must be a JSON object");
  }
  // only a MISSING property falls back to the registry default (Python
  // dict.get semantics); an explicit null is a grammar violation the type
  // checks below must see, not silently paper over
  const page = obj as JsonObject;
  const size = "size" in page ? page["size"] : PAGE_DEFAULT.size;
  const orientation =
    "orientation" in page ? page["orientation"] : PAGE_DEFAULT.orientation;
  if (typeof size !== "string" || !(size in PAGE_SIZES_MM)) {
    throw new AimError(`unknown page size ${JSON.stringify(size)}`);
  }
  if (
    typeof orientation !== "string" ||
    !PAGE_ORIENTATIONS.includes(orientation)
  ) {
    throw new AimError(`unknown orientation ${JSON.stringify(orientation)}`);
  }
  const rawMargins = "margins" in page ? page["margins"] : PAGE_DEFAULT.margins;
  if (
    typeof rawMargins !== "object" ||
    rawMargins === null ||
    Array.isArray(rawMargins)
  ) {
    throw new AimError("page margins must be an object");
  }
  const marginsMm = {} as Record<(typeof SIDES)[number], number>;
  for (const side of SIDES) {
    const margins = rawMargins as JsonObject;
    const value = side in margins ? margins[side] : PAGE_DEFAULT.margins[side];
    if (typeof value !== "string" || !MARGIN_PATTERN.test(value)) {
      throw new AimError(
        `page margin ${side} ${JSON.stringify(value)} does not match the margin grammar`,
      );
    }
    const mm = marginNumber(value.slice(0, -2));
    if (mm > MARGIN_MAX_MM) {
      throw new AimError(
        `page margin ${side} ${value} exceeds the maximum ${MARGIN_MAX_MM}mm`,
      );
    }
    marginsMm[side] = mm;
  }
  const [w, h] = PAGE_SIZES_MM[size]! as [number, number];
  const pageWidthMm = orientation === "landscape" ? h : w;
  const pageHeightMm = orientation === "landscape" ? w : h;
  const setup: PageSetup = {
    size,
    orientation,
    marginsMm,
    pageWidthMm,
    pageHeightMm,
    contentWidthMm: pageWidthMm - marginsMm.left - marginsMm.right,
    contentHeightMm: pageHeightMm - marginsMm.top - marginsMm.bottom,
  };
  if (setup.contentWidthMm <= 0 || setup.contentHeightMm <= 0) {
    throw new AimError(
      `margins leave no content area on ${size} ${orientation}`,
    );
  }
  return setup;
}

/** The canonical aim-note comment body for a spec version (spec §2.5). */
export function renderNote(version: string | null = null): string {
  return `\n${NOTE_TEMPLATE.replace("{version}", version ?? SPEC_VERSION)}\n`;
}

// -- the document ------------------------------------------------------------

export class AimDocument {
  private readonly html: Element;
  private readonly head: Element;
  private readonly body: Element;
  private readonly parents = new Map<Element, Element>();
  private readonly containerViews = new Map<Element, Container>();
  private readonly chunkViews = new Map<string, Chunk>();

  /** Top-level content constructs in document order (chunks and containers). */
  readonly nodes: readonly AimNode[];
  /** Every chunk, flat, in first-appearance document order. */
  readonly chunks: readonly Chunk[];
  /** Every container, flat, in document order (nested ones included). */
  readonly containers: readonly Container[];
  /** One entry per top-level construct: its chunk or container id. */
  readonly bodyIds: readonly string[];
  private readonly index = new Map<string, AimNode[]>();

  private constructor(fragment: Fragment) {
    const html = fragment.elements().find((e) => e.tag === "html");
    if (html === undefined)
      throw new AimParseError("not an .aim document (no <html> element)");
    this.html = html;
    const head = html.find((e) => e.tag === "head");
    const body = html.find((e) => e.tag === "body");
    if (head === null || body === null)
      throw new AimParseError("document has no <head>/<body>");
    this.head = head;
    this.body = body;
    for (const el of html.iter()) {
      for (const child of el.elements()) this.parents.set(child, el);
    }
    const built = this.buildViews();
    this.nodes = built.nodes;
    this.chunks = built.chunks;
    this.containers = built.containers;
    this.bodyIds = this.constructs().map(
      (e) => e.chunkId || e.containerId || "",
    );
  }

  /** Parse a full .aim document string. */
  static parse(text: string): AimDocument {
    return new AimDocument(parseHtml(text));
  }

  // -- lookups ---------------------------------------------------------------

  /** All nodes carrying this chunk/container id, in document order. Repeated
   * ids (invalid, but never silently dropped) yield every occurrence. */
  getAll(id: string): readonly AimNode[] {
    return this.index.get(id) ?? [];
  }

  /** The first node with this id, or undefined. O(1). */
  get(id: string): AimNode | undefined {
    return this.index.get(id)?.[0];
  }

  // -- sections ----------------------------------------------------------------

  private constructs(): Element[] {
    return this.body.elements().filter((e) => !BODY_SECTIONS.has(e.tag));
  }

  private section(tag: string): Element | null {
    return this.body.elements().find((e) => e.tag === tag) ?? null;
  }

  private script(kind: keyof typeof SCRIPT_TYPES): Element | null {
    const want = SCRIPT_TYPES[kind];
    const where = kind === "meta" || kind === "doc" ? this.head : this.body;
    return (
      where
        .elements()
        .find((e) => e.tag === "script" && e.get("type") === want) ?? null
    );
  }

  private themeEl(): Element | null {
    return (
      this.head
        .elements()
        .find((e) => e.tag === "style" && e.has("data-aim-theme")) ?? null
    );
  }

  private cssEl(): Element | null {
    return (
      this.head
        .elements()
        .find((e) => e.tag === "style" && e.has("data-aim-css")) ?? null
    );
  }

  // -- basic accessors ---------------------------------------------------------

  get specVersion(): string | null {
    return this.html.get("data-aim-version");
  }

  get lang(): string | null {
    return this.html.get("lang");
  }

  get title(): string {
    return this.head.find((e) => e.tag === "title")?.text() ?? "";
  }

  /** The agent note's raw comment text, or null (spec §2.5). */
  get note(): string | null {
    for (const node of this.head.children) {
      if (
        node instanceof Comment &&
        node.data.trimStart().startsWith(NOTE_SIGIL)
      ) {
        return node.data;
      }
    }
    return null;
  }

  /** Whether the note is byte-exactly canonical for this spec version. */
  hasCanonicalNote(): boolean {
    const data = this.note;
    return data !== null && data === renderNote(this.specVersion);
  }

  /** Theme slot assignments (empty when no theme block). */
  get theme(): Readonly<Record<string, string>> {
    const el = this.themeEl();
    const raw = el?.raw;
    if (el === null || raw === null || raw === undefined || raw.length === 0)
      return {};
    const m = /^:root\{([\s\S]*)\}$/.exec(raw.trim());
    if (m === null) return {};
    const out: Record<string, string> = {};
    for (const piece of m[1]!.split(";")) {
      const colon = piece.indexOf(":");
      if (colon < 0) continue;
      out[piece.slice(0, colon).trim()] = piece.slice(colon + 1).trim();
    }
    return out;
  }

  /** The parsed metadata cache, or null when absent. Throws when the block
   * exists but is malformed — corrupt data, not a missing cache. */
  get meta(): JsonObject | null {
    const el = this.script("meta");
    if (el === null || (el.raw ?? "").trim().length === 0) return null;
    let obj: unknown;
    try {
      obj = JSON.parse((el.raw ?? "").trim());
    } catch (exc) {
      throw new AimParseError(
        `aim-meta cache is not valid JSON: ${String(exc)}`,
      );
    }
    if (typeof obj !== "object" || obj === null || Array.isArray(obj)) {
      throw new AimParseError("aim-meta cache is not a JSON object");
    }
    return obj as JsonObject;
  }

  /** The parsed aim:doc settings object (`{}` when absent). */
  get docSettings(): JsonObject {
    const el = this.script("doc");
    const raw = el?.raw ?? null;
    if (raw === null || raw.trim().length === 0) return {};
    let obj: unknown;
    try {
      obj = JSON.parse(raw.trim());
    } catch (exc) {
      throw new AimParseError(
        `aim-doc settings block is not valid JSON: ${String(exc)}`,
      );
    }
    if (typeof obj !== "object" || obj === null || Array.isArray(obj)) {
      throw new AimParseError("aim-doc settings block is not a JSON object");
    }
    return obj as JsonObject;
  }

  /** The document's resolved page setup (registry defaults when unset). */
  get pageSetup(): PageSetup {
    const page = this.docSettings["page"];
    if (page === undefined || page === null) {
      return pageSetupFromObj(PAGE_DEFAULT as unknown as JsonObject);
    }
    return pageSetupFromObj(page);
  }

  /** The embedded stylesheet, or null when the document omits it. */
  get stylesheet(): Stylesheet | null {
    const el = this.cssEl();
    if (el === null) return null;
    return { version: el.get("data-aim-css") ?? "", css: el.raw ?? "" };
  }

  /** Raw content of the history script block (opaque JSONL; null = absent). */
  get historyJsonl(): string | null {
    const el = this.script("history");
    return el === null ? null : (el.raw ?? "");
  }

  /** Raw content of the embeddings script block (opaque JSONL; null = absent). */
  get embeddingsJsonl(): string | null {
    const el = this.script("embeddings");
    return el === null ? null : (el.raw ?? "");
  }

  /** Ids in the packed-asset registry, in document order (spec §9). */
  get assetIds(): readonly string[] {
    const sec = this.section("aim-assets");
    const svg = sec?.elements().find((e) => e.tag === "svg");
    if (svg === undefined) return [];
    return svg
      .elements()
      .filter((e) => e.tag === "symbol")
      .map((e) => e.get("id") ?? "");
  }

  /** The reduced-projection hash (spec §11.3). */
  get docHash(): string {
    const theme = this.themeEl();
    const settings = this.script("doc");
    return docHash(
      `<html${canonicalAttrs(this.html, false)}>`,
      theme === null ? null : serialize(theme),
      this.constructs().map((c) => serialize(c)),
      settings === null ? null : serialize(settings),
    );
  }

  // -- proposals ----------------------------------------------------------------

  /** The pending lane, in card order. */
  get proposals(): readonly Proposal[] {
    const sec = this.section("aim-proposals");
    if (sec === null) return [];
    const out: Proposal[] = [];
    for (const card of sec.elements()) {
      if (card.tag !== "aim-proposal") continue;
      const tmpl = card.elements().find((c) => c.tag === "template");
      const payloadEls = tmpl?.elements() ?? [];
      out.push({
        id: card.get("id") || "",
        action: card.get("data-action") || "",
        target: card.get("data-for"),
        author: {
          type: card.get("data-author") || "human",
          id: card.get("data-author-id"),
          model: card.get("data-author-model"),
        },
        at: card.get("data-at") || "",
        explanation: card.get("data-explanation"),
        payloadHtml:
          payloadEls.length > 0
            ? payloadEls.map((e) => serialize(e)).join("")
            : null,
        anchorContainer: card.get("data-anchor-container"),
        anchorAfter: card.get("data-anchor-after"),
        anchorShell: card.get("data-anchor-shell"),
        dependsOn: card.get("data-depends-on"),
        batch: card.get("data-batch"),
      });
    }
    return out;
  }

  // -- chunk/container views -----------------------------------------------------

  /** Each chunk id's member elements grouped by parent, in first-hit
   * pre-order — Python `DocState.find_chunk`'s hit list, precollected for
   * every id by one walk in `buildViews` (a per-id rescan is O(n²)). */
  private readonly chunkGroups = new Map<string, Map<Element, Element[]>>();

  /** The container a chunk lives in: nearest container ancestor or "body". */
  private containerOfChunk(parent: Element): string {
    let node: Element | undefined = parent;
    while (node !== undefined && node !== this.body) {
      const cid = node.containerId;
      if (cid !== null && cid.length > 0) return cid;
      node = this.parents.get(node);
    }
    return "body";
  }

  private makeChunkView(
    cid: string,
    parent: Element | null,
    members: Element[],
  ): Chunk {
    return {
      kind: "chunk",
      id: cid,
      container:
        parent === this.body || parent === null
          ? "body"
          : this.containerOfChunk(parent),
      tags: members.map((m) => m.tag),
      html: serializeRun(members),
      text: members.map((m) => m.text()).join(""),
      tag: members[0]?.tag ?? "",
      isRun: members.length > 1,
    };
  }

  /** One id's chunk view under one specific parent — the LOCAL group, so a
   * duplicated id (invalid, S016) still reads per-container: the second
   * container's member shows its own html/text, exactly like Python's
   * per-container primitives (`container.elements()`). */
  private readonly groupViews = new Map<string, Map<Element, Chunk>>();

  private groupChunkView(cid: string, parent: Element): Chunk {
    let byParent = this.groupViews.get(cid);
    if (byParent === undefined) {
      byParent = new Map();
      this.groupViews.set(cid, byParent);
    }
    const cached = byParent.get(parent);
    if (cached !== undefined) return cached;
    const members =
      this.chunkGroups.get(cid)?.get(parent) ??
      parent.elements().filter((e) => e.chunkId === cid);
    const view = this.makeChunkView(cid, parent, members);
    byParent.set(parent, view);
    return view;
  }

  private chunkView(cid: string): Chunk {
    const cached = this.chunkViews.get(cid);
    if (cached !== undefined) return cached;
    // the first collected group is find_chunk's answer: the members that
    // share the first hit's parent (no group = an id seen only inside a
    // template, which contributes no members)
    const first: [Element, Element[]] | undefined = this.chunkGroups
      .get(cid)
      ?.entries()
      .next().value;
    const view =
      first === undefined
        ? this.makeChunkView(cid, null, [])
        : this.groupChunkView(cid, first[0]);
    this.chunkViews.set(cid, view);
    return view;
  }

  private containerView(el: Element): Container {
    const cached = this.containerViews.get(el);
    if (cached !== undefined) return cached;
    const members: AimNode[] = [];
    const seen = new Set<string>();
    const addMember = (m: Element): void => {
      const containerId = m.containerId;
      const chunkId = m.chunkId;
      if (containerId !== null && containerId.length > 0) {
        members.push(this.containerView(m));
      } else if (chunkId !== null && chunkId.length > 0 && !seen.has(chunkId)) {
        seen.add(chunkId); // run members collapse into one chunk node
        members.push(
          this.groupChunkView(chunkId, this.parents.get(m) ?? this.body),
        );
      }
    };
    for (const child of el.elements()) {
      if (el.tag === "table" && TABLE_SHELLS.has(child.tag)) {
        for (const row of child.elements()) addMember(row);
      } else {
        addMember(child);
      }
    }
    const attrs: Record<string, string> = {};
    for (const [k, v] of el.attrs) {
      if (!(k in attrs)) attrs[k] = v ?? "";
    }
    const parent = this.parents.get(el);
    const view: Container = {
      kind: "container",
      id: el.containerId ?? "",
      tag: el.tag,
      container: parent === undefined ? "body" : this.containerOfChunk(parent),
      attrs,
      members,
    };
    this.containerViews.set(el, view);
    return view;
  }

  private buildViews(): {
    nodes: AimNode[];
    chunks: Chunk[];
    containers: Container[];
  } {
    // pass 1 — one template-skipping walk precollects every chunk id's
    // member groups in pre-order, so `chunkView` reads its group instead of
    // rescanning the tree per id. Semantics are Python `find_chunk`'s:
    // template subtrees never contribute members.
    const collect = (parent: Element, el: Element): void => {
      if (el.tag === "template") return;
      const cid = el.chunkId;
      if (cid !== null && cid.length > 0) {
        let groups = this.chunkGroups.get(cid);
        if (groups === undefined) {
          groups = new Map();
          this.chunkGroups.set(cid, groups);
        }
        const members = groups.get(parent);
        if (members === undefined) groups.set(parent, [el]);
        else members.push(el);
      }
      for (const child of el.elements()) collect(el, child);
    };
    for (const top of this.constructs()) collect(this.body, top);

    // pass 2 defines every ordering: chunks emit at first sight of their
    // id (Python `AimDocument.chunks`), containers at their element. The
    // index gets one entry per (id, parent) group, so an id repeated under
    // several parents — invalid (S016), but real files can be broken — is
    // a multimap hit, never a silent overwrite.
    const chunks: Chunk[] = [];
    const containers: Container[] = [];
    const emitted = new Map<string, Set<Element>>();
    for (const top of this.constructs()) {
      for (const el of top.iter()) {
        const chunkId = el.chunkId;
        const containerId = el.containerId;
        if (chunkId !== null && chunkId.length > 0) {
          const parent = this.parents.get(el) ?? this.body;
          let groupParents = emitted.get(chunkId);
          if (groupParents === undefined) {
            groupParents = new Set();
            emitted.set(chunkId, groupParents);
          }
          if (!groupParents.has(parent)) {
            const isFirstGroup = groupParents.size === 0;
            groupParents.add(parent);
            const view = isFirstGroup
              ? this.chunkView(chunkId) // walk order matches findChunk's first hit
              : this.groupChunkView(chunkId, parent);
            if (isFirstGroup) chunks.push(view);
            this.pushIndex(chunkId, view);
          }
        }
        if (containerId !== null && containerId.length > 0) {
          const view = this.containerView(el);
          containers.push(view);
          this.pushIndex(containerId, view);
        }
      }
    }
    const nodes: AimNode[] = [];
    const seenTop = new Set<string>();
    for (const top of this.constructs()) {
      const containerId = top.containerId;
      const chunkId = top.chunkId;
      if (containerId !== null && containerId.length > 0) {
        nodes.push(this.containerView(top));
      } else if (
        chunkId !== null &&
        chunkId.length > 0 &&
        !seenTop.has(chunkId)
      ) {
        seenTop.add(chunkId);
        nodes.push(this.chunkView(chunkId));
      }
    }
    return { nodes, chunks, containers };
  }

  private pushIndex(id: string, view: AimNode): void {
    const bucket = this.index.get(id);
    if (bucket === undefined) this.index.set(id, [view]);
    else bucket.push(view);
  }
}

/** Parse a `.aim` document string into its read-only projection. */
export function parse(text: string): AimDocument {
  return AimDocument.parse(text);
}
