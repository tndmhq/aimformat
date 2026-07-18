/**
 * Cross-implementation parity: the TS projection of every parity source must
 * equal the committed golden produced by the Python SDK
 * (scripts/dump_projection.py), field for field. The spec — not either
 * implementation — is ground truth; a divergence here means one side is
 * wrong and spec.md decides which.
 */
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { AimDocument, type AimNode } from "../src/document.ts";
import { sha256Prefixed } from "../src/sha256.ts";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));
const GOLDEN_DIR = join(ROOT, "tests", "parity", "goldens");
const FIXTURE_DIR = join(ROOT, "tests", "parity", "fixtures");
const EXAMPLES_DIR = join(ROOT, "examples");

const goldens = readdirSync(GOLDEN_DIR)
  .filter((f) => f.endsWith(".json") && f !== "conformance-hashes.json")
  .sort();

const sourcePath = (stem: string): string => {
  for (const dir of [EXAMPLES_DIR, FIXTURE_DIR]) {
    const p = join(dir, `${stem}.aim`);
    if (existsSync(p)) return p;
  }
  throw new Error(`no source document for golden ${stem}`);
};

/** Plain-JSON clone of a node tree entry (chunks and containers are already
 * plain data; this just drops readonly typing for comparison). */
const nodeObj = (n: AimNode): unknown =>
  n.kind === "chunk"
    ? { ...n, tags: [...n.tags] }
    : { ...n, attrs: { ...n.attrs }, members: n.members.map(nodeObj) };

const opaque = (raw: string | null): unknown =>
  raw === null
    ? null
    : {
        sha256: sha256Prefixed(raw),
        lines: raw.split("\n").filter((l) => l.trim().length > 0).length,
      };

/** The TS projection in the golden schema (see scripts/dump_projection.py). */
const project = (doc: AimDocument): unknown => ({
  specVersion: doc.specVersion,
  lang: doc.lang,
  title: doc.title,
  docHash: doc.docHash,
  note: doc.note,
  hasCanonicalNote: doc.hasCanonicalNote(),
  stylesheet:
    doc.stylesheet === null
      ? null
      : {
          version: doc.stylesheet.version,
          sha256: sha256Prefixed(doc.stylesheet.css),
        },
  theme: { ...doc.theme },
  meta: doc.meta,
  docSettings: doc.docSettings,
  pageSetup: {
    size: doc.pageSetup.size,
    orientation: doc.pageSetup.orientation,
    marginsMm: { ...doc.pageSetup.marginsMm },
    pageWidthMm: doc.pageSetup.pageWidthMm,
    pageHeightMm: doc.pageSetup.pageHeightMm,
    contentWidthMm: doc.pageSetup.contentWidthMm,
    contentHeightMm: doc.pageSetup.contentHeightMm,
  },
  nodes: doc.nodes.map(nodeObj),
  chunks: doc.chunks.map(nodeObj),
  containers: doc.containers.map((c) => c.id),
  bodyIds: [...doc.bodyIds],
  proposals: doc.proposals.map((p) => ({ ...p, author: { ...p.author } })),
  assetIds: [...doc.assetIds],
  history: opaque(doc.historyJsonl),
  embeddings: opaque(doc.embeddingsJsonl),
});

describe("cross-implementation parity with the Python SDK", () => {
  it("has a corpus to test", () => {
    expect(goldens.length).toBeGreaterThanOrEqual(11);
  });

  for (const golden of goldens) {
    const stem = golden.replace(/\.json$/, "");
    it(`projects ${stem}.aim identically to Python`, () => {
      const expected = JSON.parse(
        readFileSync(join(GOLDEN_DIR, golden), "utf8"),
      );
      const doc = AimDocument.parse(readFileSync(sourcePath(stem), "utf8"));
      expect(project(doc)).toEqual(expected);
    });
  }

  it("hashes every ok_* conformance fixture identically to Python", () => {
    const hashes: Record<string, string> = JSON.parse(
      readFileSync(join(GOLDEN_DIR, "conformance-hashes.json"), "utf8"),
    );
    const names = Object.keys(hashes);
    expect(names.length).toBeGreaterThan(0);
    for (const name of names) {
      const doc = AimDocument.parse(
        readFileSync(join(ROOT, "tests", "fixtures", name), "utf8"),
      );
      expect(`${name} ${doc.docHash}`).toBe(`${name} ${hashes[name]}`);
    }
  });
});
