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

  it("decodes semicolonless references like html.unescape", () => {
    // pinned against Python: the charref regex stops at the first invalid
    // character and the rest stays literal
    expect(el(parseFragment("<p>A&#65b, hex &#x41z</p>")).text()).toBe(
      "AAb, hex Az",
    );
    // core names bare; the HTML4 legacy set; longest-prefix fallback
    expect(el(parseFragment("<p>&amp b &lt c &gt d &quot e</p>")).text()).toBe(
      '& b < c > d " e',
    );
    expect(el(parseFragment("<p>&copy 2026, &AMP x</p>")).text()).toBe(
      "© 2026, & x",
    );
    expect(el(parseFragment("<p>prefix &ampx and &quothi</p>")).text()).toBe(
      'prefix &x and "hi',
    );
    // &apos has no bare legacy form — literal, like Python
    expect(el(parseFragment("<p>&apos b</p>")).text()).toBe("&apos b");
    // dropped control code point, then literal remainder
    expect(el(parseFragment("<p>x&#6a;y</p>")).text()).toBe("xa;y");
    // bare exact legacy names still decode inside attribute values
    expect(
      el(parseFragment('<p data-x-note="1 &lt 2 &amp 3">t</p>')).get(
        "data-x-note",
      ),
    ).toBe("1 < 2 & 3");
  });

  it("keeps non-exact semicolonless references literal in attributes", () => {
    // pinned against Python 3.13+ (_unescape_attrvalue, the HTML5 attribute
    // rule): a named reference decodes only on an EXACT entity match, so a
    // name running into following text stays literal instead of decoding by
    // longest prefix — Python ≤3.12 ran full html.unescape here, which is
    // the version-fragile legacy behavior this test deliberately excludes
    const attr = (markup: string): string | null =>
      el(parseFragment(markup)).get("data-x-note");
    expect(attr('<p data-x-note="&quothi">t</p>')).toBe("&quothi");
    expect(attr('<p data-x-note="?a=1&ampb=2">t</p>')).toBe("?a=1&ampb=2");
    // ...while the same forms in body text keep decoding by longest prefix
    expect(el(parseFragment("<p>&quothi and &ampb</p>")).text()).toBe(
      '"hi and &b',
    );
    // a match ending in `=` never decodes, even an exact name
    expect(attr('<p data-x-note="&amp=1">t</p>')).toBe("&amp=1");
    // exact names still decode, bare or semicolon-terminated, and a
    // non-alphanumeric stops the name so the exact match resumes
    expect(attr('<p data-x-note="&amp b">t</p>')).toBe("& b");
    expect(attr('<p data-x-note="&amp">t</p>')).toBe("&");
    expect(attr('<p data-x-note="&quot;x">t</p>')).toBe('"x');
    expect(attr('<p data-x-note="&amp-x">t</p>')).toBe("&-x");
    expect(attr('<p data-x-note="&copy 2026">t</p>')).toBe("© 2026");
    // numeric references always decode, trailing text left literal
    expect(attr('<p data-x-note="&#65b">t</p>')).toBe("Ab");
    expect(attr('<p data-x-note="&#65=">t</p>')).toBe("A=");
    // a legacy name with a semicolon is an exact html5 entity — decodes
    expect(attr('<p data-x-note="caf&eacute;">t</p>')).toBe("café");
    // the deliberate strictness divergence carries over: a name-shaped
    // `&name;` outside the shipped tables is a parse error (Python decodes
    // `&hellip;` from the full table, keeps `&ampx;` literal; erring loud
    // beats guessing wrong with a partial table)
    expect(() => parseFragment('<p data-x-note="a&hellip;b">t</p>')).toThrow(
      AimParseError,
    );
    expect(() => parseFragment('<p data-x-note="&ampx;">t</p>')).toThrow(
      AimParseError,
    );
  });

  it("decodes the legacy uppercase core spellings with semicolons", () => {
    expect(el(parseFragment("<p>&AMP;&LT;&GT;&QUOT;</p>")).text()).toBe('&<>"');
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

  it("scans raw end tags case-insensitively with a proper boundary", () => {
    // pinned against Python html.parser: `</SCRIPT>` closes on every
    // version (the CDATA scan is case-insensitive), and a close-like run
    // whose tag name continues (`</scriptx>`) is data on every version
    expect(el(parseFragment('<script type="t">var a;</SCRIPT>')).raw).toBe(
      "var a;",
    );
    expect(el(parseFragment("<style>s</STYLE >")).raw).toBe("s");
    expect(
      el(parseFragment('<script type="t">a </scriptx> b</script>')).raw,
    ).toBe("a </scriptx> b");
    // version-dependent cases pin the spec-correct (HTML5 / Python 3.13+)
    // side: no boundary after `</` means data (≤3.12 closed on
    // `</ script>`), and the end tag consumes junk through its ">" (≤3.12
    // treated `</script/>` as data)
    expect(
      el(parseFragment('<script type="t">a </ script> b</script>')).raw,
    ).toBe("a </ script> b");
    expect(el(parseFragment('<script type="t">a</script/>')).raw).toBe("a");
    expect(el(parseFragment('<script type="t">a</script foo="b">')).raw).toBe(
      "a",
    );
    expect(el(parseFragment("<style>c</ScRiPt x>d</StYlE\t>")).raw).toBe(
      "c</ScRiPt x>d",
    );
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
