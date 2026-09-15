const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { record, parseArchive, restore } = require("../lib/archive");
const fixtures = require("../../tests/fixtures/archive.json");
const metadata = "OL: 1-1 | ML: 4-4 | 1B";
const archive = (body) => Buffer.from(`<merged_code>\n${body}</merged_code>\n`);

for (const [name, content] of Object.entries(fixtures)) {
  test(`exact archive round trip: ${name}`, () => {
    const files = parseArchive(archive(record(name, metadata, content)));
    assert.deepEqual(files, [{ path: name, content: Buffer.from(content) }]);
  });
}

for (const name of [
  "../escape",
  "/absolute",
  "C:/escape",
  "a\\b",
  "a/../b",
  "a//b",
  "a\nheader",
  ".. /escape",
  "NUL",
  "name.",
]) {
  test(`reject unsafe archive path ${JSON.stringify(name)}`, () => {
    const body = record("safe", metadata, "text").replace(
      "# FILE: safe ",
      `# FILE: ${encodeURIComponent(name)} `,
    );
    assert.throws(() => parseArchive(archive(body)), /Unsafe/);
  });
}

test("omitted records are distinguished from real source text", () => {
  assert.deepEqual(parseArchive(archive(record("skip.txt", metadata, null))), []);
});

test("reject duplicate paths, truncation, and old records", () => {
  const body = record("a", metadata, "hello");
  assert.throws(() => parseArchive(archive(body + body)), /Duplicate/);
  assert.throws(
    () => parseArchive(archive(body + record("a/b", metadata, "child"))),
    /Conflicting/,
  );
  assert.throws(() => parseArchive(archive(body).subarray(0, -5)), /archive/i);
  assert.throws(() => parseArchive(archive(body.replace("BYTES: 5", "BYTES: 50"))));
  assert.throws(
    () => parseArchive(archive(`# FILE: a [${metadata}]\n\`\`\`\`\nhello\n\`\`\`\`\n\n`)),
    /unsupported/,
  );
});

test("restore rejects symlinks before writing any files", () => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "combicode-security-")));
  try {
    fs.mkdirSync(path.join(root, "out"));
    fs.mkdirSync(path.join(root, "outside"));
    fs.symlinkSync(path.join(root, "outside"), path.join(root, "out/link"), "dir");
    const files = [
      { path: "safe.txt", content: Buffer.from("safe") },
      { path: "link/escape", content: Buffer.from("bad") },
    ];
    for (const dryRun of [true, false]) {
      assert.throws(() => restore(files, path.join(root, "out"), dryRun, true), /Symlink/);
      assert.equal(fs.existsSync(path.join(root, "out/safe.txt")), false);
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("overwrite replaces hardlinks and summary excludes existing files", () => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "combicode-restore-")));
  try {
    fs.writeFileSync(path.join(root, "outside"), "old");
    fs.mkdirSync(path.join(root, "out"));
    fs.linkSync(path.join(root, "outside"), path.join(root, "out/a"));
    const files = [{ path: "a", content: Buffer.from("new") }];
    assert.deepEqual(restore(files, path.join(root, "out"), false, false), {
      count: 0,
      bytes: 0,
    });
    assert.deepEqual(restore(files, path.join(root, "out"), false, true), {
      count: 1,
      bytes: 3,
    });
    assert.equal(fs.readFileSync(path.join(root, "outside"), "utf8"), "old");
    assert.equal(fs.readFileSync(path.join(root, "out/a"), "utf8"), "new");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
