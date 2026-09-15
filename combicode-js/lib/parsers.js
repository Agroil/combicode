const path = require("node:path");

function parseCodeStructure(filePath, content) {
  const ext = path.extname(filePath).toLowerCase();
  const lines = content.split("\n");

  switch (ext) {
    case ".py":
      return parsePython(lines);
    case ".js":
    case ".jsx":
    case ".mjs":
    case ".cjs":
      return parseJavaScript(lines);
    case ".ts":
    case ".tsx":
    case ".mts":
    case ".cts":
      return parseTypeScript(lines);
    case ".go":
      return parseGo(lines);
    case ".rs":
      return parseRust(lines);
    case ".java":
      return parseJava(lines);
    case ".c":
    case ".h":
    case ".cpp":
    case ".hpp":
    case ".cc":
    case ".cxx":
      return parseCCpp(lines);
    case ".cs":
      return parseCSharp(lines);
    case ".php":
      return parsePHP(lines);
    case ".rb":
      return parseRuby(lines);
    case ".swift":
      return parseSwift(lines);
    case ".kt":
    case ".kts":
      return parseKotlin(lines);
    case ".scala":
    case ".sc":
      return parseScala(lines);
    case ".lua":
      return parseLua(lines);
    case ".pl":
    case ".pm":
      return parsePerl(lines);
    case ".sh":
    case ".bash":
    case ".zsh":
      return parseBash(lines);
    default:
      return [];
  }
}

/**
 * Find the end of a block that starts at `startLine` using brace/indent counting.
 * For brace-based languages.
 */
function findBraceBlockEnd(lines, startLine) {
  let depth = 0;
  let foundOpen = false;
  let quote = null;
  let blockComment = false;
  for (let i = startLine; i < lines.length; i++) {
    const line = lines[i];
    for (let j = 0; j < line.length; j++) {
      const ch = line[j];
      const next = line[j + 1];
      if (blockComment) {
        if (ch === "*" && next === "/") {
          blockComment = false;
          j++;
        }
      } else if (quote) {
        if (ch === "\\") j++;
        else if (ch === quote) quote = null;
      } else if (ch === "/" && next === "/") {
        break;
      } else if (ch === "/" && next === "*") {
        blockComment = true;
        j++;
      } else if (ch === '"' || ch === "`" || (ch === "'" && line.indexOf("'", j + 1) >= 0)) {
        quote = ch;
      } else if (ch === "{") {
        depth++;
        foundOpen = true;
      } else if (ch === "}" && foundOpen) {
        if (--depth === 0) return i;
      }
    }
  }
  return lines.length - 1;
}

/**
 * Find block end for Python (indent-based).
 */
function findIndentBlockEnd(lines, startLine) {
  if (startLine >= lines.length) return startLine;
  const defLine = lines[startLine];
  const baseIndent = defLine.match(/^(\s*)/)[1].length;

  for (let i = startLine + 1; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() === "") continue; // skip blank lines
    const indent = line.match(/^(\s*)/)[1].length;
    if (indent <= baseIndent) {
      return i - 1;
    }
  }
  return lines.length - 1;
}

/**
 * Find block end for Ruby-like (def/end, class/end, module/end).
 */
function findRubyBlockEnd(lines, startLine) {
  let depth = 0;
  for (let i = startLine; i < lines.length; i++) {
    const trimmed = lines[i].trim();
    // Keywords that open blocks
    if (
      /^(class|module|def|do|if|unless|case|while|until|for|begin)\b/.test(trimmed) ||
      /\bdo\s*(\|[^|]*\|)?\s*$/.test(trimmed)
    ) {
      depth++;
    }
    if (/^end\b/.test(trimmed)) {
      depth--;
      if (depth === 0) return i;
    }
  }
  return lines.length - 1;
}

/**
 * Find block end for Lua (function/end).
 */
function findLuaBlockEnd(lines, startLine) {
  let depth = 0;
  for (let i = startLine; i < lines.length; i++) {
    const trimmed = lines[i].trim();
    if (/\b(function|if|for|while|repeat)\b/.test(trimmed)) depth++;
    if (/^end\b/.test(trimmed) || /\bend\s*[,)\]]/.test(trimmed) || trimmed === "end") {
      depth--;
      if (depth === 0) return i;
    }
  }
  return lines.length - 1;
}

function computeByteSize(lines, startLine, endLine) {
  let size = 0;
  for (let i = startLine; i <= endLine && i < lines.length; i++) {
    size += Buffer.byteLength(lines[i], "utf8") + (i < lines.length - 1 ? 1 : 0);
  }
  return size;
}

function buildElement(type, label, startLine, endLine, lines) {
  endLine = Math.max(startLine, Math.min(endLine, lines.length - (lines.at(-1) === "" ? 2 : 1)));
  return {
    type,
    label,
    startLine: startLine + 1, // 1-indexed
    endLine: endLine + 1,
    size: computeByteSize(lines, startLine, endLine),
  };
}

// --- Language Parsers ---

function parsePython(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    // class
    let m = trimmed.match(/^class\s+(\w+)(\(.*?\))?\s*:/);
    if (m) {
      const end = findIndentBlockEnd(lines, i);
      elements.push(buildElement("class", `class ${m[1]}`, i, end, lines));
      continue;
    }

    // async def
    m = trimmed.match(/^async\s+def\s+(\w+)\s*\((.*?)\)(\s*->.*?)?\s*:/);
    if (m) {
      const end = findIndentBlockEnd(lines, i);
      const sig = `${m[1]}(${m[2]})${m[3] || ""}`;
      const type = m[1] === "__init__" ? "ctor" : "async";
      elements.push(
        buildElement(type, `${type === "ctor" ? "ctor" : "async"} ${sig}`, i, end, lines),
      );
      continue;
    }

    // def
    m = trimmed.match(/^def\s+(\w+)\s*\((.*?)\)(\s*->.*?)?\s*:/);
    if (m) {
      const end = findIndentBlockEnd(lines, i);
      const sig = `${m[1]}(${m[2]})${m[3] || ""}`;
      let type = "fn";
      if (m[1] === "__init__") type = "ctor";
      else if (m[1].startsWith("test_")) type = "test";
      const label = type === "ctor" ? `ctor ${sig}` : type === "test" ? `test ${sig}` : `fn ${sig}`;
      elements.push(buildElement(type, label, i, end, lines));
      continue;
    }

    // for/while loops (> 5 lines)
    m = trimmed.match(/^(for|while)\s+(.+):\s*$/);
    if (m) {
      const end = findIndentBlockEnd(lines, i);
      if (end - i + 1 > 5) {
        elements.push(buildElement("loop", `loop ${m[1]} ${m[2]}`, i, end, lines));
      }
      continue;
    }
  }
  return elements;
}

function parseJavaScript(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    // class
    let m = trimmed.match(/^(export\s+)?(default\s+)?class\s+(\w+)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `class ${m[3]}`, i, end, lines));
      continue;
    }

    // describe (test suite)
    m = trimmed.match(/^describe\s*\(\s*['"`]([^'"`]+)['"`]/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("describe", `describe ${m[1]}`, i, end, lines));
      continue;
    }

    // test/it blocks
    m = trimmed.match(/^(it|test)\s*\(\s*['"`]([^'"`]+)['"`]/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("test", `test ${m[2]}`, i, end, lines));
      continue;
    }

    // async function
    m = trimmed.match(/^(export\s+)?(default\s+)?async\s+function\s+(\w+)\s*\((.*?)\)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("async", `async ${m[3]}(${m[4]})`, i, end, lines));
      continue;
    }

    // function
    m = trimmed.match(/^(export\s+)?(default\s+)?function\s+(\w+)\s*\((.*?)\)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      const type = m[3] === "constructor" ? "ctor" : "fn";
      elements.push(buildElement(type, `${type} ${m[3]}(${m[4]})`, i, end, lines));
      continue;
    }

    // arrow functions assigned to const/let/var (with explicit function body)
    m = trimmed.match(/^(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(async\s+)?\(?(.*?)\)?\s*=>/);
    if (m && (trimmed.includes("{") || i + 1 < lines.length)) {
      // Only include if it has a block body
      if (trimmed.includes("{") || (i + 1 < lines.length && lines[i + 1].trim().startsWith("{"))) {
        const end = findBraceBlockEnd(lines, i);
        if (end > i) {
          const isAsync = !!m[4];
          const type = isAsync ? "async" : "fn";
          elements.push(buildElement(type, `${type} ${m[3]}(${m[5] || ""})`, i, end, lines));
        }
      }
      continue;
    }

    // for/while loops (> 5 lines)
    m = trimmed.match(/^(for|while)\s*\((.+)\)\s*\{?/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      if (end - i + 1 > 5) {
        elements.push(buildElement("loop", `loop ${m[1]} ${m[2]}`, i, end, lines));
      }
      continue;
    }
  }
  return elements;
}

function parseTypeScript(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    // interface
    let m = trimmed.match(/^(export\s+)?(default\s+)?interface\s+(\w+)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `interface ${m[3]}`, i, end, lines));
      continue;
    }

    // class
    m = trimmed.match(/^(export\s+)?(default\s+)?(abstract\s+)?class\s+(\w+)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `class ${m[4]}`, i, end, lines));
      continue;
    }

    // describe
    m = trimmed.match(/^describe\s*\(\s*['"`]([^'"`]+)['"`]/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("describe", `describe ${m[1]}`, i, end, lines));
      continue;
    }

    // test/it
    m = trimmed.match(/^(it|test)\s*\(\s*['"`]([^'"`]+)['"`]/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("test", `test ${m[2]}`, i, end, lines));
      continue;
    }

    // async function
    m = trimmed.match(/^(export\s+)?(default\s+)?async\s+function\s+(\w+)\s*\((.*?)\)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("async", `async ${m[3]}(${m[4]})`, i, end, lines));
      continue;
    }

    // function
    m = trimmed.match(/^(export\s+)?(default\s+)?function\s+(\w+)\s*\((.*?)\)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("fn", `fn ${m[3]}(${m[4]})`, i, end, lines));
      continue;
    }

    // arrow functions
    m = trimmed.match(/^(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(async\s+)?\(?(.*?)\)?\s*=>/);
    if (m && trimmed.includes("{")) {
      const end = findBraceBlockEnd(lines, i);
      if (end > i) {
        const isAsync = !!m[4];
        const type = isAsync ? "async" : "fn";
        elements.push(buildElement(type, `${type} ${m[3]}(${m[5] || ""})`, i, end, lines));
      }
      continue;
    }

    // for/while loops (> 5 lines)
    m = trimmed.match(/^(for|while)\s*\((.+)\)\s*\{?/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      if (end - i + 1 > 5) {
        elements.push(buildElement("loop", `loop ${m[1]} ${m[2]}`, i, end, lines));
      }
    }
  }
  return elements;
}

function parseGo(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // struct
    let m = trimmed.match(/^type\s+(\w+)\s+struct\b/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `struct ${m[1]}`, i, end, lines));
      continue;
    }

    // interface
    m = trimmed.match(/^type\s+(\w+)\s+interface\b/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `interface ${m[1]}`, i, end, lines));
      continue;
    }

    // func
    m = trimmed.match(/^func\s+(\(.*?\)\s*)?(\w+)\s*\((.*?)\)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      const receiver = m[1] ? m[1].trim() + " " : "";
      const name = m[2];
      const type = name.startsWith("Test") ? "test" : "fn";
      elements.push(
        buildElement(
          type,
          `${type === "test" ? "test" : "fn"} ${receiver}${name}(${m[3]})`,
          i,
          end,
          lines,
        ),
      );
      continue;
    }

    // for loops (> 5 lines) - Go only has for
    m = trimmed.match(/^for\s+(.+)\s*\{/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      if (end - i + 1 > 5) {
        elements.push(buildElement("loop", `loop for ${m[1]}`, i, end, lines));
      }
    }
  }
  return elements;
}

function parseRust(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // struct
    let m = trimmed.match(/^(pub\s+)?struct\s+(\w+)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `struct ${m[2]}`, i, end, lines));
      continue;
    }

    // enum
    m = trimmed.match(/^(pub\s+)?enum\s+(\w+)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `enum ${m[2]}`, i, end, lines));
      continue;
    }

    // trait
    m = trimmed.match(/^(pub\s+)?trait\s+(\w+)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `trait ${m[2]}`, i, end, lines));
      continue;
    }

    // impl
    m = trimmed.match(/^impl\s+(.+?)\s*\{/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("impl", `impl ${m[1]}`, i, end, lines));
      continue;
    }

    // fn
    m = trimmed.match(/^(pub\s+)?(async\s+)?fn\s+(\w+)\s*\((.*?)\)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      const isAsync = !!m[2];
      const isTest = m[3].startsWith("test_");
      const type = isTest ? "test" : isAsync ? "async" : "fn";
      elements.push(buildElement(type, `${type} ${m[3]}(${m[4]})`, i, end, lines));
      continue;
    }

    // loop/for/while (> 5 lines)
    m = trimmed.match(/^(for|while|loop)\b(.*)?\{/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      if (end - i + 1 > 5) {
        elements.push(
          buildElement("loop", `loop ${m[1]}${m[2] ? " " + m[2].trim() : ""}`, i, end, lines),
        );
      }
    }
  }
  return elements;
}

function parseJava(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // class
    let m = trimmed.match(
      /^(public\s+|private\s+|protected\s+)?(static\s+)?(abstract\s+)?(final\s+)?class\s+(\w+)/,
    );
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `class ${m[5]}`, i, end, lines));
      continue;
    }

    // interface
    m = trimmed.match(/^(public\s+|private\s+|protected\s+)?interface\s+(\w+)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `interface ${m[2]}`, i, end, lines));
      continue;
    }

    // enum
    m = trimmed.match(/^(public\s+|private\s+|protected\s+)?enum\s+(\w+)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `enum ${m[2]}`, i, end, lines));
      continue;
    }

    // method (including constructors)
    m = trimmed.match(
      /^(public\s+|private\s+|protected\s+)?(static\s+)?(abstract\s+)?(final\s+)?(synchronized\s+)?(\w+\s+)?(\w+)\s*\((.*?)\)\s*(\{|throws)/,
    );
    if (m && !["if", "for", "while", "switch", "catch", "return"].includes(m[7])) {
      const end = findBraceBlockEnd(lines, i);
      const name = m[7];
      // Constructor: return type is absent and name matches class-like pattern
      const hasReturnType = m[6] && m[6].trim();
      const type = !hasReturnType ? "ctor" : name.startsWith("test") ? "test" : "fn";
      elements.push(buildElement(type, `${type} ${name}(${m[8]})`, i, end, lines));
      continue;
    }

    // for/while loops (> 5 lines)
    m = trimmed.match(/^(for|while)\s*\((.+)\)\s*\{?/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      if (end - i + 1 > 5) {
        elements.push(buildElement("loop", `loop ${m[1]} ${m[2]}`, i, end, lines));
      }
    }
  }
  return elements;
}

function parseCCpp(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // class
    let m = trimmed.match(/^class\s+(\w+)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `class ${m[1]}`, i, end, lines));
      continue;
    }

    // struct
    m = trimmed.match(/^(typedef\s+)?struct\s+(\w+)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `struct ${m[2]}`, i, end, lines));
      continue;
    }

    // function (C-style: return_type name(...))
    m = trimmed.match(/^(\w[\w\s*&]+?)\s+(\w+)\s*\(([^)]*)\)\s*(\{|$)/);
    if (
      m &&
      !["if", "for", "while", "switch", "return", "typedef", "struct", "class", "enum"].includes(
        m[2],
      )
    ) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("fn", `fn ${m[2]}(${m[3]})`, i, end, lines));
      continue;
    }

    // for/while loops (> 5 lines)
    m = trimmed.match(/^(for|while)\s*\((.+)\)\s*\{?/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      if (end - i + 1 > 5) {
        elements.push(buildElement("loop", `loop ${m[1]} ${m[2]}`, i, end, lines));
      }
    }
  }
  return elements;
}

function parseCSharp(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // class / struct / interface / enum / record
    let m = trimmed.match(
      /^(public\s+|private\s+|protected\s+|internal\s+)?(static\s+)?(abstract\s+|sealed\s+)?(partial\s+)?(class|struct|interface|enum|record)\s+(\w+)/,
    );
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `${m[5]} ${m[6]}`, i, end, lines));
      continue;
    }

    // method
    m = trimmed.match(
      /^(public\s+|private\s+|protected\s+|internal\s+)?(static\s+)?(async\s+)?(virtual\s+|override\s+|abstract\s+)?(\w[\w<>\[\],\s]*?)\s+(\w+)\s*\((.*?)\)\s*\{?/,
    );
    if (
      m &&
      ![
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "return",
        "class",
        "struct",
        "interface",
        "enum",
      ].includes(m[6])
    ) {
      const end = findBraceBlockEnd(lines, i);
      const isAsync = !!m[3];
      const type = isAsync ? "async" : "fn";
      elements.push(buildElement(type, `${type} ${m[6]}(${m[7]})`, i, end, lines));
      continue;
    }

    // for/while loops (> 5 lines)
    m = trimmed.match(/^(for|foreach|while)\s*\((.+)\)\s*\{?/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      if (end - i + 1 > 5) {
        elements.push(buildElement("loop", `loop ${m[1]} ${m[2]}`, i, end, lines));
      }
    }
  }
  return elements;
}

function parsePHP(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // class / interface / trait
    let m = trimmed.match(/^(abstract\s+)?(final\s+)?(class|interface|trait)\s+(\w+)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `${m[3]} ${m[4]}`, i, end, lines));
      continue;
    }

    // function
    m = trimmed.match(
      /^(public\s+|private\s+|protected\s+)?(static\s+)?function\s+(\w+)\s*\((.*?)\)/,
    );
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      const type = m[3] === "__construct" ? "ctor" : m[3].startsWith("test") ? "test" : "fn";
      elements.push(buildElement(type, `${type} ${m[3]}(${m[4]})`, i, end, lines));
      continue;
    }

    // for/while loops (> 5 lines)
    m = trimmed.match(/^(for|foreach|while)\s*\((.+)\)\s*\{?/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      if (end - i + 1 > 5) {
        elements.push(buildElement("loop", `loop ${m[1]} ${m[2]}`, i, end, lines));
      }
    }
  }
  return elements;
}

function parseRuby(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // class
    let m = trimmed.match(/^class\s+(\w+)/);
    if (m) {
      const end = findRubyBlockEnd(lines, i);
      elements.push(buildElement("class", `class ${m[1]}`, i, end, lines));
      continue;
    }

    // module
    m = trimmed.match(/^module\s+(\w+)/);
    if (m) {
      const end = findRubyBlockEnd(lines, i);
      elements.push(buildElement("class", `module ${m[1]}`, i, end, lines));
      continue;
    }

    // def
    m = trimmed.match(/^def\s+(self\.)?(\w+[?!=]?)\s*(\(.*?\))?/);
    if (m) {
      const end = findRubyBlockEnd(lines, i);
      const prefix = m[1] || "";
      const type = m[2] === "initialize" ? "ctor" : m[2].startsWith("test_") ? "test" : "fn";
      elements.push(buildElement(type, `${type} ${prefix}${m[2]}${m[3] || ""}`, i, end, lines));
      continue;
    }
  }
  return elements;
}

function parseSwift(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // class / struct / enum / protocol
    let m = trimmed.match(
      /^(public\s+|private\s+|internal\s+|open\s+|fileprivate\s+)?(final\s+)?(class|struct|enum|protocol)\s+(\w+)/,
    );
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `${m[3]} ${m[4]}`, i, end, lines));
      continue;
    }

    // func
    m = trimmed.match(
      /^(public\s+|private\s+|internal\s+|open\s+)?(static\s+|class\s+)?(override\s+)?func\s+(\w+)\s*\((.*?)\)/,
    );
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      const name = m[4];
      const type = name === "init" ? "ctor" : name.startsWith("test") ? "test" : "fn";
      elements.push(buildElement(type, `${type} ${name}(${m[5]})`, i, end, lines));
      continue;
    }

    // for/while loops (> 5 lines)
    m = trimmed.match(/^(for|while)\s+(.+)\s*\{/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      if (end - i + 1 > 5) {
        elements.push(buildElement("loop", `loop ${m[1]} ${m[2]}`, i, end, lines));
      }
    }
  }
  return elements;
}

function parseKotlin(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // class / interface / object
    let m = trimmed.match(
      /^(open\s+|abstract\s+|data\s+|sealed\s+)?(class|interface|object)\s+(\w+)/,
    );
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `${m[2]} ${m[3]}`, i, end, lines));
      continue;
    }

    // fun
    m = trimmed.match(
      /^(public\s+|private\s+|protected\s+|internal\s+)?(override\s+)?(suspend\s+)?fun\s+(\w+)\s*\((.*?)\)/,
    );
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      const isSuspend = !!m[3];
      const type = m[4].startsWith("test") ? "test" : isSuspend ? "async" : "fn";
      elements.push(buildElement(type, `${type} ${m[4]}(${m[5]})`, i, end, lines));
      continue;
    }

    // for/while loops (> 5 lines)
    m = trimmed.match(/^(for|while)\s*\((.+)\)\s*\{?/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      if (end - i + 1 > 5) {
        elements.push(buildElement("loop", `loop ${m[1]} ${m[2]}`, i, end, lines));
      }
    }
  }
  return elements;
}

function parseScala(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // class / object / trait
    let m = trimmed.match(/^(case\s+)?(class|object|trait)\s+(\w+)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("class", `${m[1] || ""}${m[2]} ${m[3]}`, i, end, lines));
      continue;
    }

    // def
    m = trimmed.match(/^(override\s+)?def\s+(\w+)\s*(\(.*?\))?/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      const type = m[2].startsWith("test") ? "test" : "fn";
      elements.push(buildElement(type, `${type} ${m[2]}${m[3] || ""}`, i, end, lines));
      continue;
    }
  }
  return elements;
}

function parseLua(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // function / local function
    let m = trimmed.match(/^(local\s+)?function\s+([\w.:]+)\s*\((.*?)\)/);
    if (m) {
      const end = findLuaBlockEnd(lines, i);
      elements.push(buildElement("fn", `fn ${m[2]}(${m[3]})`, i, end, lines));
      continue;
    }

    // for/while loops (> 5 lines)
    m = trimmed.match(/^(for|while)\s+(.+)\s+do/);
    if (m) {
      const end = findLuaBlockEnd(lines, i);
      if (end - i + 1 > 5) {
        elements.push(buildElement("loop", `loop ${m[1]} ${m[2]}`, i, end, lines));
      }
    }
  }
  return elements;
}

function parsePerl(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // package
    let m = trimmed.match(/^package\s+([\w:]+)/);
    if (m) {
      elements.push(buildElement("class", `package ${m[1]}`, i, i, lines));
      continue;
    }

    // sub
    m = trimmed.match(/^sub\s+(\w+)/);
    if (m) {
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("fn", `fn ${m[1]}`, i, end, lines));
      continue;
    }
  }
  return elements;
}

function parseBash(lines) {
  const elements = [];
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // function keyword or name()
    let m = trimmed.match(/^(function\s+)?(\w+)\s*\(\s*\)\s*\{?/);
    if (m && m[1]) {
      // function keyword form
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("fn", `fn ${m[2]}`, i, end, lines));
      continue;
    }
    if (m && !m[1] && trimmed.includes("()")) {
      // name() form
      const end = findBraceBlockEnd(lines, i);
      elements.push(buildElement("fn", `fn ${m[2]}`, i, end, lines));
      continue;
    }

    // for/while loops (> 5 lines)
    m = trimmed.match(/^(for|while)\s+(.+?);\s*do/);
    if (!m) m = trimmed.match(/^(for|while)\s+(.+)/);
    if (m) {
      // Look for done
      let end = i;
      for (let j = i + 1; j < lines.length; j++) {
        if (lines[j].trim() === "done") {
          end = j;
          break;
        }
      }
      if (end - i + 1 > 5) {
        elements.push(buildElement("loop", `loop ${m[1]} ${m[2]}`, i, end, lines));
      }
    }
  }
  return elements;
}

// ---------------------------------------------------------------------------
// Nesting + Tree Building
// ---------------------------------------------------------------------------

/**
 * Nest flat elements into a tree based on line ranges.
 * Elements that fall within the range of a parent become children.
 */
function nestElements(elements) {
  if (!elements.length) return [];

  // Sort by start line, then by larger range first (parents before children)
  const sorted = [...elements].sort((a, b) => {
    if (a.startLine !== b.startLine) return a.startLine - b.startLine;
    return b.endLine - b.startLine - (a.endLine - a.startLine);
  });

  const root = [];
  const stack = []; // stack of { element, children }

  for (const el of sorted) {
    const node = { ...el, children: [] };

    // Pop from stack if current element is outside of parent's range
    while (stack.length > 0) {
      const parent = stack[stack.length - 1];
      if (
        el.startLine >= parent.startLine &&
        el.endLine <= parent.endLine &&
        (el.startLine > parent.startLine || el.endLine < parent.endLine)
      ) {
        break;
      }
      stack.pop();
    }

    if (stack.length > 0) {
      stack[stack.length - 1].children.push(node);
    } else {
      root.push(node);
    }

    stack.push(node);
  }

  return root;
}

module.exports = { parseCodeStructure, nestElements };
