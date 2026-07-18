import { describe, expect, it } from "vitest";
import { sha256Hex, sha256Prefixed } from "../src/sha256.ts";

// FIPS 180-4 / NIST test vectors
describe("sha256", () => {
  it("hashes the empty string", () => {
    expect(sha256Hex("")).toBe(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
  });

  it('hashes "abc"', () => {
    expect(sha256Hex("abc")).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });

  it("hashes the two-block NIST message", () => {
    expect(
      sha256Hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
    ).toBe("248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
  });

  it("hashes long input spanning many blocks", () => {
    expect(sha256Hex("a".repeat(1000))).toBe(
      "41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3",
    );
  });

  it("hashes UTF-8 bytes, not UTF-16 code units", () => {
    // sha256 of the bytes "é🎉" (c3 a9 f0 9f 8e 89)
    expect(sha256Hex("é🎉")).toBe(
      sha256Hex(new Uint8Array([0xc3, 0xa9, 0xf0, 0x9f, 0x8e, 0x89])),
    );
  });

  it("prefixes with sha256:", () => {
    expect(sha256Prefixed("abc")).toBe(
      "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });
});
