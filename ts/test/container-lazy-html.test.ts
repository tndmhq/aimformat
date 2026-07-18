import { describe, expect, it, vi } from "vitest";
import { AimDocument } from "../src/document.ts";

// Count serialize calls to prove Container.html is lazy: parsing must not
// serialize container subtrees (O(depth²) bytes on nested outlines), only
// first access does — and the memoized value stays canonical.
const serializeCalls = vi.hoisted(() => ({ count: 0 }));

vi.mock("../src/canonical.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/canonical.ts")>();
  return {
    ...actual,
    serialize: (...args: Parameters<typeof actual.serialize>) => {
      serializeCalls.count += 1;
      return actual.serialize(...args);
    },
  };
});

const DEPTH = 40;

/** DEPTH nested lists (c1 ⊃ c2 ⊃ … ⊃ cDEPTH) around one leaf chunk. */
const nestedDoc = (): string => {
  let inner = '<li data-aim="leaf">x</li>';
  for (let i = DEPTH; i >= 1; i--) {
    inner = `<ul data-aim-container="c${i}">${inner}</ul>`;
  }
  return `<!doctype html>
<html data-aim-version="0.2" lang="en">
<head>
<meta charset="utf-8">
<title>Test</title>
</head>
<body>
${inner}</body>
</html>
`;
};

describe("Container.html laziness", () => {
  it("does not serialize container subtrees at parse time", () => {
    serializeCalls.count = 0;
    const doc = AimDocument.parse(nestedDoc());
    expect(doc.containers).toHaveLength(DEPTH);
    // the only parse-time serialization is the leaf chunk's single member
    // (Chunk.html/memberHtmls stay eager — members are shallow); none of the
    // DEPTH container subtrees is serialized until html is read
    expect(serializeCalls.count).toBe(1);
  });

  it("serializes on first access, memoizes, and stays canonical", () => {
    const doc = AimDocument.parse(nestedDoc());
    const innermost = doc.get(`c${DEPTH}`);
    if (innermost === undefined || innermost.kind !== "container")
      throw new Error("expected container");
    serializeCalls.count = 0;
    expect(innermost.html).toBe(
      `<ul data-aim-container="c${DEPTH}"><li data-aim="leaf">x</li></ul>`,
    );
    expect(serializeCalls.count).toBe(1);
    // memoized: a second read serializes nothing
    expect(innermost.html).toBe(innermost.html);
    expect(serializeCalls.count).toBe(1);
    // the outermost html embeds the full subtree, computed independently
    const outermost = doc.get("c1");
    if (outermost === undefined || outermost.kind !== "container")
      throw new Error("expected container");
    expect(outermost.html.startsWith('<ul data-aim-container="c1">')).toBe(
      true,
    );
    expect(outermost.html).toContain('<li data-aim="leaf">x</li>');
    expect(serializeCalls.count).toBe(2);
  });
});
