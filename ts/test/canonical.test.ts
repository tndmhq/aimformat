import { describe, expect, it } from "vitest";
import { Element } from "../src/dom.ts";
import {
  compareCodePoints,
  normalizeStyle,
  serialize,
  sortClassTokens,
} from "../src/canonical.ts";
import { parseFragment } from "../src/parser.ts";

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
    expect(normalizeStyle("color:red; left:5px")).toBe("left:5px; color:red");
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
