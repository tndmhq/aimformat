import { describe, expect, it } from "vitest";
import { Element, Text } from "../src/dom.ts";
import { AimParseError } from "../src/errors.ts";
import { parseFragment, parseHtml } from "../src/parser.ts";
import { serialize } from "../src/canonical.ts";

const el = (nodes: ReturnType<typeof parseFragment>): Element => {
  const first = nodes.find((n): n is Element => n instanceof Element);
  if (first === undefined) throw new Error("no element parsed");
  return first;
};

describe("parser", () => {
  it("parses elements, attributes, and text", () => {
    const p = el(parseFragment('<p data-aim="c1" class="text-lg">Hello</p>'));
    expect(p.tag).toBe("p");
    expect(p.get("data-aim")).toBe("c1");
    expect(p.chunkId).toBe("c1");
    expect(p.text()).toBe("Hello");
  });

  it("decodes the canonical escapes in text and attributes", () => {
    const p = el(
      parseFragment(
        '<p data-x-note="a &quot;b&quot; &amp; c">1 &lt; 2 &amp; 3 &gt; 2</p>',
      ),
    );
    expect(p.text()).toBe("1 < 2 & 3 > 2");
    expect(p.get("data-x-note")).toBe('a "b" & c');
  });

  it("decodes numeric character references", () => {
    expect(el(parseFragment("<p>&#65;&#x1F389;</p>")).text()).toBe("A🎉");
  });

  it("applies HTML's numeric-reference replacement table, like html.parser", () => {
    // pinned against Python: html._replace_charref remaps the windows-1252
    // range, turns NUL/surrogates/out-of-range into U+FFFD, and drops
    // control and noncharacter code points outright
    expect(
      el(
        parseFragment("<p>&#128; &#0; &#xD800; &#x110000; &#1; &#13;</p>"),
      ).text(),
    ).toBe("€ � � �  \r");
    expect(el(parseFragment("<p>&#x99;&#x9F;</p>")).text()).toBe("™Ÿ");
    expect(
      el(parseFragment("<p>a&#xFDD0;&#xFFFE;&#x10FFFF;b</p>")).text(),
    ).toBe("ab");
    const attr = el(
      parseFragment('<p data-x-note="&#146;90s &#151; ok">x</p>'),
    );
    expect(attr.get("data-x-note")).toBe("’90s — ok");
  });

  it("rejects exotic named references (canonical .aim is raw UTF-8)", () => {
    expect(() => parseFragment("<p>caf&eacute;</p>")).toThrow(AimParseError);
  });

  it("keeps a bare ampersand literal, like html.parser", () => {
    expect(el(parseFragment("<p>fish & chips</p>")).text()).toBe(
      "fish & chips",
    );
  });

  it("keeps raw < and > inside attribute values", () => {
    const p = el(parseFragment('<p data-x-note="a < b > c">x</p>'));
    expect(p.get("data-x-note")).toBe("a < b > c");
  });

  it("treats script/style content as raw text, never markup", () => {
    const s = el(
      parseFragment(
        '<script type="application/aim-history+jsonl">\n{"a":"<\\/p>"}\n</script>',
      ),
    );
    expect(s.raw).toBe('\n{"a":"<\\/p>"}\n');
    expect(s.children).toHaveLength(0);
  });

  it("parses bare attributes", () => {
    const s = el(parseFragment("<style data-aim-theme>:root{}</style>"));
    expect(s.get("data-aim-theme")).toBe("");
    expect(s.has("data-aim-theme")).toBe(true);
  });

  it("parses void elements without a stack push", () => {
    const p = el(
      parseFragment('<p><img src="https://x/y.png" alt="y"><br>tail</p>'),
    );
    expect(p.elements().map((e) => e.tag)).toEqual(["img", "br"]);
    expect(p.text()).toBe("tail");
  });

  it("parses self-closing SVG elements", () => {
    const svg = el(
      parseFragment('<svg role="img"><use href="#asset-a"/></svg>'),
    );
    const use = svg.elements()[0]!;
    expect(use.tag).toBe("use");
    expect(use.selfClosing).toBe(true);
  });

  it("captures the doctype and comments", () => {
    const frag = parseHtml(
      "<!doctype html>\n<html><head><!-- note --></head><body></body></html>",
    );
    expect(frag.doctype).toBe("doctype html");
    const head = frag.elements()[0]!.elements()[0]!;
    expect(
      head.children.some(
        (c) => !(c instanceof Element) && !(c instanceof Text),
      ),
    ).toBe(true);
  });

  it("round-trips canonical markup byte-exactly through serialize", () => {
    const canonical =
      '<section data-aim="b31f" class="bg-gray-50"><h2>Scope</h2><p>1 &lt; 2 &amp; &gt;</p></section>';
    expect(serialize(el(parseFragment(canonical)))).toBe(canonical);
  });

  it("rejects an unmatched closing tag", () => {
    expect(() => parseFragment("<p>one</div>")).toThrow(
      /unmatched closing tag/,
    );
  });

  it("rejects unclosed elements at EOF", () => {
    expect(() => parseFragment("<section><p>text</p>")).toThrow(
      /unclosed <section>/,
    );
  });

  it("rejects an unterminated raw block", () => {
    expect(() => parseFragment("<style>p{}")).toThrow(/unterminated <style>/);
  });

  it("rejects single-quoted attributes", () => {
    expect(() => parseFragment("<p data-aim='c1'>x</p>")).toThrow(
      /not double-quoted/,
    );
  });

  it("rejects a stray < in text", () => {
    expect(() => parseFragment("<p>1 < 2</p>")).toThrow(/stray "<"/);
  });
});
