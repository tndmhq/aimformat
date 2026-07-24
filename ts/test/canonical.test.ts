import { describe, expect, it } from "vitest";
import { Element } from "../src/dom.ts";
import {
  compareCodePoints,
  normalizeStyle,
  serialize,
  sortClassTokens,
} from "../src/canonical.ts";
import { parseFragment } from "../src/parser.ts";
import {
  STYLE_PROP_ORDER,
  STYLE_PROP_PAINT,
  STYLE_PROP_PAINT_SINCE,
  STYLE_PROP_PATTERNS,
} from "../src/registry.data.ts";

const first = (markup: string): Element => {
  const el = parseFragment(markup).find(
    (n): n is Element => n instanceof Element,
  );
  if (el === undefined) throw new Error("no element");
  return el;
};

describe("canonical serialization", () => {
  it("sorts and de-duplicates class tokens", () => {
    expect(sortClassTokens("text-lg  font-bold text-lg bg-white")).toBe(
      "bg-white font-bold text-lg",
    );
  });

  it("normalizes inline style to whitelist order, later duplicates winning", () => {
    expect(normalizeStyle("top:10px; left:5px; top:20px;")).toBe(
      "left:5px; top:20px",
    );
  });

  it("keeps unknown style properties visible at the end", () => {
    // `opacity`, not `color`: colour is a registered paint property since 0.3
    expect(normalizeStyle("opacity:.5; left:5px")).toBe("left:5px; opacity:.5");
  });

  it("orders paint after geometry, in registry order", () => {
    expect(
      normalizeStyle(
        "border-color:#ff69b4; color:#ff69b4; top:32px; background-color:#fff1f7; left:48px",
      ),
    ).toBe(
      "left:48px; top:32px; color:#ff69b4; background-color:#fff1f7; border-color:#ff69b4",
    );
  });

  it("keeps the last of duplicate paint declarations", () => {
    expect(normalizeStyle("color:#111111; left:2px; color:#ff69b4")).toBe(
      "left:2px; color:#ff69b4",
    );
  });

  it("projects the paint grammar so consumers validate against registry data", () => {
    expect(STYLE_PROP_PAINT_SINCE).toBe("0.3");
    const ok = (prop: string, value: string) => {
      const pattern = STYLE_PROP_PATTERNS[prop];
      if (pattern === undefined) throw new Error(`no grammar for ${prop}`);
      return pattern.test(value);
    };
    expect(STYLE_PROP_PAINT).toEqual(["color", "background-color", "border-color"]);
    // v0.4 appends literal typography after paint
    expect(STYLE_PROP_ORDER.slice(-5)).toEqual([
      "color",
      "background-color",
      "border-color",
      "font-size",
      "font-family",
    ]);
    expect(ok("font-size", "12pt")).toBe(true);
    expect(ok("font-size", "12px")).toBe(false); // the grammar is pt-only
    expect(ok("font-family", "Georgia, serif")).toBe(true);
    expect(ok("color", "#ff69b4")).toBe(true);
    for (const bad of [
      "red",
      "#fff",
      "#FF69B4",
      "rgb(255,105,180)",
      "var(--aim-brand-1)",
      "transparent",
    ]) {
      expect(ok("color", bad)).toBe(false);
    }
    expect(ok("background-color", "#fff1f7")).toBe(true);
    expect(ok("border-color", "#ff69b4")).toBe(true);
  });

  it("compares by code point, not UTF-16 code unit", () => {
    // U+F8FF (BMP private use) < U+1F600 (astral) by code point, but the
    // astral char's high surrogate 0xD83D sorts first by code unit
    expect(compareCodePoints("", "\u{1f600}")).toBeLessThan(0);
    expect(compareCodePoints("a", "ab")).toBeLessThan(0);
    expect(compareCodePoints("ab", "a")).toBeGreaterThan(0);
    expect(compareCodePoints("\u{1f600}", "\u{1f600}")).toBe(0);
  });

  it("sorts astral-vs-BMP attribute names in code-point order, like Python", () => {
    // pinned against the Python SDK: sorted() orders str by code point
    const p = first(
      '<p data-aim="p1" data-x-\u{1f600}="emoji" data-x-="pua">x</p>',
    );
    expect(serialize(p)).toBe(
      '<p data-aim="p1" data-x-="pua" data-x-\u{1f600}="emoji">x</p>',
    );
  });

  it("sorts astral-vs-BMP class tokens in code-point order, like Python", () => {
    expect(sortClassTokens("z-\u{1f600} z- plain")).toBe(
      "plain z- z-\u{1f600}",
    );
  });

  it("orders attributes: markers first, alphabetical middle, src/href last", () => {
    const img = first(
      '<img src="https://x/y.png" alt="y" data-aim="c1" class="rounded">',
    );
    expect(serialize(img)).toBe(
      '<img data-aim="c1" class="rounded" alt="y" src="https://x/y.png">',
    );
  });

  it("writes void elements without a slash, however they were authored", () => {
    expect(serialize(first("<br/>"))).toBe("<br>");
  });

  it("keeps the FIRST duplicate attribute value, matching Element.get", () => {
    // pinned against Python: canonical_attrs uses setdefault, so the id a
    // reader resolves and the id the serializer writes never diverge
    const p = first(
      '<p data-aim="x" data-aim="y" class="b a" class="zz" title="one" title="two">t</p>',
    );
    expect(p.get("data-aim")).toBe("x");
    expect(serialize(p)).toBe('<p data-aim="x" class="a b" title="one">t</p>');
  });

  it("serializes self-closed non-void elements open+close, like Python", () => {
    // <p/> is invalid input; the normal form has exactly one spelling
    expect(serialize(first('<p data-aim="x"/>'))).toBe('<p data-aim="x"></p>');
    expect(serialize(first('<p>a <span class="x"/> b</p>'))).toBe(
      '<p>a <span class="x"></span> b</p>',
    );
  });

  it("self-closes empty foreign elements and restores SVG attribute case", () => {
    const svg = first(
      '<svg role="img" aria-label="chart"><use href="#asset-ab"/></svg>',
    );
    expect(serialize(svg)).toBe(
      '<svg aria-label="chart" role="img"><use href="#asset-ab"/></svg>',
    );
    const sym = first(
      '<svg aria-hidden="true"><symbol id="asset-ab" viewbox="0 0 1 1"></symbol></svg>',
    );
    expect(serialize(sym)).toContain('viewBox="0 0 1 1"');
  });

  it("escapes text and attribute values minimally", () => {
    const p = first('<p data-x-q="say &quot;hi&quot; > now">1 &lt; 2</p>');
    expect(serialize(p)).toBe(
      '<p data-x-q="say &quot;hi&quot; > now">1 &lt; 2</p>',
    );
  });
});
