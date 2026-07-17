import { describe, expect, it } from "vitest";
import { AimDocument } from "../src/document.ts";
import { AimError, AimParseError } from "../src/errors.ts";

const wrap = (body: string, head = ""): string =>
  `<!doctype html>
<html data-aim-version="0.2" lang="en">
<head>
<meta charset="utf-8">
<title>Test</title>
${head}</head>
<body>
${body}</body>
</html>
`;

describe("AimDocument", () => {
  it("reads basic head state", () => {
    const doc = AimDocument.parse(wrap('<p data-aim="c1">x</p>'));
    expect(doc.specVersion).toBe("0.2");
    expect(doc.lang).toBe("en");
    expect(doc.title).toBe("Test");
    expect(doc.note).toBeNull();
    expect(doc.stylesheet).toBeNull();
    expect(doc.meta).toBeNull();
    expect(doc.docSettings).toEqual({});
  });

  it("groups runs into one chunk with ordered member tags", () => {
    const doc = AimDocument.parse(
      wrap(
        '<ul data-aim-container="l1"><li data-aim="a">1</li><li data-aim="b">2</li><li data-aim="b">3</li></ul>',
      ),
    );
    expect(doc.chunks.map((c) => c.id)).toEqual(["a", "b"]);
    const run = doc.chunks[1]!;
    expect(run.isRun).toBe(true);
    expect(run.tags).toEqual(["li", "li"]);
    expect(run.html).toBe('<li data-aim="b">2</li><li data-aim="b">3</li>');
    expect(run.text).toBe("23");
    expect(run.container).toBe("l1");
  });

  it("builds the recursive node tree: slide → list → items", () => {
    const doc = AimDocument.parse(
      wrap(
        '<aim-slide data-aim-container="s1" style="width:960px; height:540px">' +
          '<h2 data-aim="t1" style="left:10px; top:10px">T</h2>' +
          '<ul data-aim-container="l1"><li data-aim="i1">one</li></ul>' +
          "</aim-slide>",
      ),
    );
    expect(doc.nodes).toHaveLength(1);
    const slide = doc.nodes[0]!;
    if (slide.kind !== "container") throw new Error("expected container");
    expect(slide.tag).toBe("aim-slide");
    expect(slide.container).toBe("body");
    expect(slide.members.map((m) => m.id)).toEqual(["t1", "l1"]);
    const list = slide.members[1]!;
    if (list.kind !== "container") throw new Error("expected nested container");
    expect(list.container).toBe("s1");
    expect(list.members.map((m) => m.id)).toEqual(["i1"]);
    expect(doc.chunks.find((c) => c.id === "i1")?.container).toBe("l1");
  });

  it("flattens table shells into ordered row members", () => {
    const doc = AimDocument.parse(
      wrap(
        '<table data-aim-container="p1"><thead><tr data-aim="r0"><th>H</th></tr></thead>' +
          '<tbody><tr data-aim="r1"><td>1</td></tr><tr data-aim="r2"><td>2</td></tr></tbody></table>',
      ),
    );
    const table = doc.nodes[0]!;
    if (table.kind !== "container") throw new Error("expected container");
    expect(table.members.map((m) => m.id)).toEqual(["r0", "r1", "r2"]);
    expect(doc.chunks.find((c) => c.id === "r1")?.container).toBe("p1");
  });

  it("indexes ids as a multimap and never overwrites repeats", () => {
    // one id under two parents is invalid (S016), but the reader must not
    // silently drop either occurrence — each parent group is an entry
    const doc = AimDocument.parse(
      wrap(
        '<ul data-aim-container="l1"><li data-aim="dup">one</li></ul>\n<p data-aim="dup">two</p>',
      ),
    );
    const all = doc.getAll("dup");
    expect(all).toHaveLength(2);
    expect(all.map((n) => (n.kind === "chunk" ? n.tag : ""))).toEqual([
      "li",
      "p",
    ]);
    expect(doc.get("dup")?.kind).toBe("chunk");
    // the flat chunks view mirrors Python: one chunk per id (first group)
    expect(doc.chunks.filter((c) => c.id === "dup")).toHaveLength(1);
  });

  it("treats sibling elements sharing an id as one run, one lookup entry", () => {
    const doc = AimDocument.parse(
      wrap(
        '<ul data-aim-container="l1"><li data-aim="r">1</li><li data-aim="r">2</li></ul>',
      ),
    );
    expect(doc.getAll("r")).toHaveLength(1);
    expect(doc.get("r")?.kind === "chunk" && doc.get("r")).toMatchObject({
      isRun: true,
    });
  });

  it("reads proposals with Python-identical defaults", () => {
    const doc = AimDocument.parse(
      wrap(
        '<p data-aim="c1">x</p>\n<aim-proposals>\n' +
          '<aim-proposal id="p-1" data-action="modify" data-at="2026-07-07T12:00:00Z" data-author="agent" data-author-model="m" data-batch="b1" data-explanation="why" data-for="c1"><template><p data-aim="c1">y</p></template></aim-proposal>\n' +
          '<aim-proposal id="p-2" data-action="delete" data-at="2026-07-07T12:00:01Z" data-author="human" data-author-id="ada" data-batch="b1" data-for="c1"></aim-proposal>\n' +
          "</aim-proposals>\n",
      ),
    );
    const [modify, del] = doc.proposals;
    expect(modify).toMatchObject({
      id: "p-1",
      action: "modify",
      target: "c1",
      payloadHtml: '<p data-aim="c1">y</p>',
      explanation: "why",
      anchorContainer: null,
      anchorAfter: null,
      anchorShell: null,
      dependsOn: null,
      batch: "b1",
    });
    expect(modify!.author).toEqual({ type: "agent", id: null, model: "m" });
    expect(del).toMatchObject({ action: "delete", payloadHtml: null });
    expect(del!.author).toEqual({ type: "human", id: "ada", model: null });
  });

  it("parses the theme block", () => {
    const doc = AimDocument.parse(
      wrap(
        '<p data-aim="c1">x</p>',
        "<style data-aim-theme>:root{--aim-brand-1:#1a73e8; --aim-font-body:system-ui, sans-serif}</style>\n",
      ),
    );
    expect(doc.theme).toEqual({
      "--aim-brand-1": "#1a73e8",
      "--aim-font-body": "system-ui, sans-serif",
    });
  });

  it("resolves page setup from defaults and from aim:doc", () => {
    const plain = AimDocument.parse(wrap('<p data-aim="c1">x</p>'));
    expect(plain.pageSetup).toMatchObject({
      size: "A4",
      orientation: "portrait",
      pageWidthMm: 210,
      contentWidthMm: 180,
    });
    const doc = AimDocument.parse(
      wrap(
        '<p data-aim="c1">x</p>',
        '<script type="application/aim-doc+json">\n{"page":{"margins":{"bottom":"10mm","left":"10mm","right":"10mm","top":"10mm"},"orientation":"landscape","size":"A5"}}\n</script>\n',
      ),
    );
    expect(doc.pageSetup).toMatchObject({
      size: "A5",
      orientation: "landscape",
      pageWidthMm: 210,
      pageHeightMm: 148,
      contentWidthMm: 190,
      contentHeightMm: 128,
    });
  });

  it("rejects explicit null page fields — only a missing property defaults", () => {
    // Python parity: dict.get(key, default) falls back only on absence, so
    // {"size": null} fails the type check (D003/D004) instead of silently
    // reading as the registry default
    const withDoc = (json: string): AimDocument =>
      AimDocument.parse(
        wrap(
          '<p data-aim="c1">x</p>',
          `<script type="application/aim-doc+json">\n${json}\n</script>\n`,
        ),
      );
    expect(() => withDoc('{"page":{"size":null}}').pageSetup).toThrow(AimError);
    expect(() => withDoc('{"page":{"orientation":null}}').pageSetup).toThrow(
      AimError,
    );
    expect(() => withDoc('{"page":{"margins":null}}').pageSetup).toThrow(
      AimError,
    );
    expect(
      () => withDoc('{"page":{"margins":{"top":null}}}').pageSetup,
    ).toThrow(AimError);
    // but a null page object as a whole is "unset" in both implementations
    expect(withDoc('{"page":null}').pageSetup.size).toBe("A4");
  });

  it("throws on a malformed meta cache only when accessed", () => {
    const doc = AimDocument.parse(
      wrap(
        '<p data-aim="c1">x</p>',
        '<script type="application/aim-meta+json">\nnot json\n</script>\n',
      ),
    );
    expect(doc.title).toBe("Test");
    expect(() => doc.meta).toThrow(AimParseError);
  });

  it("exposes history and embeddings as opaque blocks", () => {
    const doc = AimDocument.parse(
      wrap(
        '<p data-aim="c1">x</p>\n<script type="application/aim-history+jsonl">\n{"seq":1}\n</script>\n',
      ),
    );
    expect(doc.historyJsonl).toBe('\n{"seq":1}\n');
    expect(doc.embeddingsJsonl).toBeNull();
  });

  it("builds views for a large document without per-id rescans", () => {
    // perf guard: view construction must stay one traversal. The per-id
    // findChunk rescan this replaces was O(n²) — at this size several
    // seconds of work; a single walk is tens of milliseconds. The bound is
    // deliberately loose so only a complexity regression can trip it.
    const n = 6000;
    const parts: string[] = [];
    for (let i = 0; i < n / 3; i += 1) {
      parts.push(`<p data-aim="p${i}">paragraph ${i}</p>`);
      parts.push(
        `<ul data-aim-container="l${i}"><li data-aim="a${i}">one</li>` +
          `<li data-aim="b${i}">run</li><li data-aim="b${i}">run</li></ul>`,
      );
    }
    const started = performance.now();
    const doc = AimDocument.parse(wrap(parts.join("\n")));
    expect(doc.chunks).toHaveLength(n);
    expect(doc.containers).toHaveLength(n / 3);
    expect(doc.get(`b${n / 3 - 1}`)).toMatchObject({ isRun: true });
    expect(performance.now() - started).toBeLessThan(2000);
  });

  it("lists packed asset ids", () => {
    const doc = AimDocument.parse(
      wrap(
        '<p data-aim="c1">x</p>\n<aim-assets>\n<svg aria-hidden="true" height="0" width="0">\n<symbol id="asset-0123456789ab" viewBox="0 0 1 1"><rect height="1" width="1"/></symbol>\n</svg>\n</aim-assets>\n',
      ),
    );
    expect(doc.assetIds).toEqual(["asset-0123456789ab"]);
  });
});
