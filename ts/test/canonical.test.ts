import { describe, expect, it } from "vitest";
import { Element } from "../src/dom.ts";
import {
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
