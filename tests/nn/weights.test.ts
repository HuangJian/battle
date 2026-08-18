import { describe, it, expect } from "bun:test";
import { resolveLatestWeights } from "../../src/nn/weights";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("resolveLatestWeights", () => {
  it("picks the newest versioned file by embedded timestamp", () => {
    const dir = mkdtempSync(join(tmpdir(), "nnw-"));
    try {
      writeFileSync(join(dir, "weights.20260818-170055_ep40_val1.2431.json"), "{}");
      writeFileSync(join(dir, "weights.20260901-120000_ep50_val1.1000.json"), "{}");
      writeFileSync(join(dir, "weights.json"), "{}");
      const got = resolveLatestWeights(dir);
      expect(got).toContain("weights.20260901-120000_ep50_val1.1000.json");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("falls back to weights.json when no versioned file exists", () => {
    const dir = mkdtempSync(join(tmpdir(), "nnw-"));
    try {
      writeFileSync(join(dir, "weights.json"), "{}");
      const got = resolveLatestWeights(dir);
      expect(got).toContain("weights.json");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("returns null when the directory has no weights", () => {
    const dir = mkdtempSync(join(tmpdir(), "nnw-"));
    try {
      expect(resolveLatestWeights(dir)).toBeNull();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("ignores unrelated files (WEIGHTS.md, notes.txt, etc.)", () => {
    const dir = mkdtempSync(join(tmpdir(), "nnw-"));
    try {
      writeFileSync(join(dir, "WEIGHTS.md"), "x");
      writeFileSync(join(dir, "notes.txt"), "x");
      expect(resolveLatestWeights(dir)).toBeNull();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
