import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const CLI = resolve(__dirname, "..", "bin", "claude-replay.mjs");
const FIXTURE = resolve(__dirname, "e2e", "fixture.jsonl");

function run(args, timeout = 5000) {
  return new Promise((res, rej) => {
    const timer = setTimeout(() => rej(new Error("CLI timed out — may have launched server")), timeout);
    execFile(process.execPath, [CLI, ...args], (err, stdout, stderr) => {
      clearTimeout(timer);
      res({ code: err ? err.code : 0, stdout, stderr });
    });
  });
}

describe("CLI flags", () => {
  it("--version prints version and exits", async () => {
    const { code, stdout } = await run(["--version"]);
    assert.equal(code, 0);
    assert.match(stdout.trim(), /^\d+\.\d+\.\d+/);
  });

  it("-v prints version and exits", async () => {
    const { code, stdout } = await run(["-v"]);
    assert.equal(code, 0);
    assert.match(stdout.trim(), /^\d+\.\d+\.\d+/);
  });

  it("--list-themes prints themes and exits", async () => {
    const { code, stdout } = await run(["--list-themes"]);
    assert.equal(code, 0);
    const lines = stdout.trim().split("\n");
    assert.ok(lines.length >= 3);
    assert.ok(lines.includes("tokyo-night"));
    assert.ok(lines.includes("dracula"));
  });

  it("--help prints usage and exits", async () => {
    const { code, stdout } = await run(["--help"]);
    assert.equal(code, 0);
    assert.match(stdout, /Usage:/);
    assert.match(stdout, /--list-themes/);
  });

  it("-h prints usage and exits", async () => {
    const { code, stdout } = await run(["-h"]);
    assert.equal(code, 0);
    assert.match(stdout, /Usage:/);
  });

  it("extract without file shows error", async () => {
    const { code, stderr } = await run(["extract"]);
    assert.notEqual(code, 0);
    assert.match(stderr, /input file is required/);
  });

  it("nonexistent file shows error", async () => {
    const { code, stderr } = await run(["nonexistent-file.jsonl"]);
    assert.notEqual(code, 0);
    assert.match(stderr, /file not found/);
  });

  it("generates HTML to stdout with fixture input", async () => {
    const { code, stdout } = await run([FIXTURE]);
    assert.equal(code, 0);
    assert.match(stdout, /<!DOCTYPE html>/);
  });

  it("extract outputs JSONL by default", async () => {
    const { code: genCode } = await run([FIXTURE, "--mark", "1:BM", "-o", "/tmp/cli-extract-test.html", "--no-minify"]);
    assert.equal(genCode, 0);
    const { code, stdout } = await run(["extract", "/tmp/cli-extract-test.html"]);
    assert.equal(code, 0);
    const lines = stdout.trim().split("\n");
    assert.ok(lines.length >= 3, "should have at least 3 turn lines");
    const first = JSON.parse(lines[0]);
    assert.ok(first.user_text, "first turn should have user_text");
    // Bookmark should be embedded
    const withBm = lines.map((l) => JSON.parse(l)).find((t) => t.bookmark);
    assert.ok(withBm, "one turn should have a bookmark field");
    assert.equal(withBm.bookmark, "BM");
  });

  it("extract --format json outputs legacy JSON", async () => {
    const { code: genCode } = await run([FIXTURE, "-o", "/tmp/cli-extract-json.html", "--no-minify"]);
    assert.equal(genCode, 0);
    const { code, stdout } = await run(["extract", "/tmp/cli-extract-json.html", "--format", "json"]);
    assert.equal(code, 0);
    const data = JSON.parse(stdout);
    assert.ok(Array.isArray(data.turns));
    assert.ok(Array.isArray(data.bookmarks));
  });

  it("round-trip: extract JSONL then render preserves turns and bookmarks", async () => {
    // Generate with bookmarks
    const { code: c1 } = await run([FIXTURE, "--mark", "1:Start", "--mark", "3:End", "-o", "/tmp/cli-rt-source.html", "--no-minify"]);
    assert.equal(c1, 0);
    // Extract
    const { code: c2 } = await run(["extract", "/tmp/cli-rt-source.html", "-o", "/tmp/cli-rt.jsonl"]);
    assert.equal(c2, 0);
    // Re-render
    const { code: c3, stdout } = await run(["/tmp/cli-rt.jsonl", "--no-compress"]);
    assert.equal(c3, 0);
    assert.match(stdout, /<!DOCTYPE html>/);
    assert.ok(stdout.includes('\\"label\\":\\"Start\\"'), "Start bookmark preserved");
    assert.ok(stdout.includes('\\"label\\":\\"End\\"'), "End bookmark preserved");
  });

  it("remaps bookmark indices when excluding turns", async () => {
    // Exclude turn 2, bookmark on original turn 3 → should become turn 2
    const { code, stdout } = await run([
      FIXTURE, "--exclude-turns", "2", "--mark", "3:TestBookmark", "--no-compress",
    ]);
    assert.equal(code, 0);
    assert.ok(stdout.includes("TestBookmark"), "bookmark label in output");
    assert.ok(stdout.includes('\\"turn\\":2'), "bookmark should reference remapped turn 2");
    assert.ok(!stdout.includes('\\"turn\\":3'), "bookmark should not reference original turn 3");
  });
});
