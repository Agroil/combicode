const { test } = require("node:test");
const assert = require("node:assert/strict");
const { parseCodeStructure, nestElements } = require("../lib/parsers");

test("braces in strings and comments do not terminate functions", () => {
  const content =
    'function example() {\n  const brace = "}";\n  /* } */\n  // }\n  return brace;\n}\n';
  const fn = parseCodeStructure("a.js", content).find((el) => el.label.startsWith("fn example"));
  assert.equal(fn.endLine, 6);
  assert.equal(fn.size, Buffer.byteLength(content));
});

test("Python heuristic ranges stay inside the source and sizes include only real newlines", () => {
  for (const ending of ["", "\n"]) {
    const content = "def example():\n    pass" + ending;
    const fn = parseCodeStructure("a.py", content)[0];
    assert.equal(fn.endLine, 2);
    assert.equal(fn.size, Buffer.byteLength(content));
  }
});

test("equal ranges are siblings rather than false parent-child relationships", () => {
  const nodes = nestElements([
    { startLine: 1, endLine: 3 },
    { startLine: 1, endLine: 3 },
  ]);
  assert.equal(nodes.length, 2);
});
