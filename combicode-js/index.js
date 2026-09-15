#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const ignore = require("ignore");
const { parseCodeStructure, nestElements } = require("./lib/parsers");
const { record, parseArchive, restore, encodePath, atomicWrite } = require("./lib/archive");

const { version } = require("./package.json");

// ---------------------------------------------------------------------------
// System Prompts
// ---------------------------------------------------------------------------

const DEFAULT_SYSTEM_PROMPT = `You are an expert software architect. The user is providing you with the complete source code for a project, contained in a single file. Your task is to meticulously analyze the provided codebase to gain a comprehensive understanding of its structure, functionality, dependencies, and overall architecture.

A code map with expanded tree structure \`<code_index>\` is provided below to give you a high-level overview. The subsequent section \`<merged_code>\` contains the full content of each file (read the ML line range from this output file), clearly marked with a file header.

Your instructions are:
1.  Analyze Thoroughly: Read through every file to understand its purpose and how it interacts with other files.
2.  Identify Key Components: Pay close attention to configuration files (like package.json, pyproject.toml), entry points (like index.js, main.py), and core logic.
3.  Use the Code Map: The code map shows classes, functions, loops with their line numbers (OL = Original Line, ML = Merged Line) and sizes for precise navigation.
`;

const LLMS_TXT_SYSTEM_PROMPT = `You are an expert software architect. The user is providing you with the full documentation for a project. This file contains the complete context needed to understand the project's features, APIs, and usage for a specific version. Your task is to act as a definitive source of truth based *only* on this provided documentation.

When answering questions or writing code, adhere strictly to the functions, variables, and methods described in this context. Do not use or suggest any deprecated or older functionalities that are not present here.

A code map with expanded tree structure is provided below for a high-level overview.
`;

// Packaged defaults, synchronized from configs/ignore.json.
const DEFAULT_IGNORES = require("./ignore.json");

// ---------------------------------------------------------------------------
// Utility helpers
// ---------------------------------------------------------------------------

function isLikelyBinary(filePath) {
  const buffer = Buffer.alloc(1024);
  let fd;
  try {
    fd = fs.openSync(filePath, "r");
    const bytesRead = fs.readSync(fd, buffer, 0, 1024, 0);
    return buffer.subarray(0, bytesRead).includes(0);
  } catch (e) {
    throw new Error(`Cannot read ${filePath}: ${e.message}`);
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

function formatBytes(bytes, decimals = 1) {
  if (bytes === 0) return "0B";
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(sizes.length - 1, Math.floor(Math.log(bytes) / Math.log(k)));
  // Round half up and drop a trailing ".0" (e.g. 2048 bytes is "2KB", not "2.0KB").
  const factor = 10 ** dm;
  const value = Math.round((bytes / Math.pow(k, i)) * factor) / factor;
  return `${value}${sizes[i]}`;
}

// ---------------------------------------------------------------------------
// Code Parsers (regex-based for all languages)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Directory Walker
// ---------------------------------------------------------------------------

function walkDirectory(
  currentDir,
  rootDir,
  ignoreChain,
  allowedExts,
  absoluteOutputPath,
  useGitIgnore,
  stats,
) {
  let results = [];
  let currentIgnoreManager = null;

  if (useGitIgnore) {
    const gitignorePath = path.join(currentDir, ".gitignore");
    if (fs.existsSync(gitignorePath)) {
      try {
        const content = fs.readFileSync(gitignorePath, "utf8");
        const ig = ignore().add(content);
        currentIgnoreManager = { manager: ig, root: currentDir };
      } catch (e) {
        throw new Error(`Cannot read ignore configuration: ${e.message}`);
      }
    }
  }

  const nextIgnoreChain = currentIgnoreManager
    ? [...ignoreChain, currentIgnoreManager]
    : ignoreChain;

  let entries;
  try {
    entries = fs.readdirSync(currentDir, { withFileTypes: true });
  } catch (e) {
    throw new Error(`Cannot scan ${currentDir}: ${e.message}`);
  }

  for (const entry of entries) {
    const fullPath = path.join(currentDir, entry.name);

    if (path.resolve(fullPath) === absoluteOutputPath) continue;

    let shouldIgnore = false;
    for (const [index, item] of nextIgnoreChain.entries()) {
      let relToIgnoreRoot = path.relative(item.root, fullPath);
      if (path.sep === "\\") {
        relToIgnoreRoot = relToIgnoreRoot.replace(/\\/g, "/");
      }
      if (entry.isDirectory() && !relToIgnoreRoot.endsWith("/")) {
        relToIgnoreRoot += "/";
      }
      const result = item.manager.test(relToIgnoreRoot);
      if (result.ignored) shouldIgnore = true;
      if (result.unignored) shouldIgnore = false;
      if (index === 0 && shouldIgnore) break;
    }

    if (shouldIgnore) {
      stats.ignored++;
      continue;
    }

    if (entry.isDirectory()) {
      results = results.concat(
        walkDirectory(
          fullPath,
          rootDir,
          nextIgnoreChain,
          allowedExts,
          absoluteOutputPath,
          useGitIgnore,
          stats,
        ),
      );
    } else if (entry.isFile()) {
      if (isLikelyBinary(fullPath)) {
        stats.ignored++;
        continue;
      }
      if (allowedExts && !allowedExts.has(path.extname(entry.name).toLowerCase())) {
        stats.ignored++;
        continue;
      }
      try {
        const fileStats = fs.statSync(fullPath);
        const relativeToRoot = path.relative(rootDir, fullPath);
        results.push({
          path: fullPath,
          relativePath: relativeToRoot,
          size: fileStats.size,
          formattedSize: formatBytes(fileStats.size),
        });
      } catch (e) {
        throw new Error(`Cannot read ignore configuration: ${e.message}`);
      }
    }
  }

  return results;
}

// ---------------------------------------------------------------------------
// Code Index Tree Generator
// ---------------------------------------------------------------------------

/**
 * Build the <code_index> tree with expanded code elements.
 */
function generateCodeIndex(filesWithMeta, root, skipContentSet, noParse) {
  let tree = `${encodePath(path.basename(root))}/\n`;

  // Build directory structure
  const structure = { children: Object.create(null) };
  for (const file of filesWithMeta) {
    const parts = file.relativePath.split(path.sep);
    let currentLevel = structure;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const isFile = i === parts.length - 1;
      if (isFile) {
        currentLevel.children[part] = { file };
      } else {
        currentLevel.children[part] ??= { children: Object.create(null) };
        currentLevel = currentLevel.children[part];
      }
    }
  }

  function renderTree(level, prefix) {
    const keys = Object.keys(level.children).sort();
    for (let i = 0; i < keys.length; i++) {
      const key = keys[i];
      const isLast = i === keys.length - 1;
      const connector = isLast ? "\u2514\u2500\u2500 " : "\u251c\u2500\u2500 ";
      const childPrefix = prefix + (isLast ? "    " : "\u2502   ");
      const value = level.children[key];

      if (value.file) {
        const f = value.file;
        const olRange = `OL: ${f.lineCount ? 1 : 0}-${f.lineCount}`;
        const mlRange = `ML: ${f.mlStart}-${f.mlEnd}`;
        const sizeStr = f.formattedSize;
        const isSkipped = skipContentSet && skipContentSet.has(f.relativePath);

        tree += `${prefix}${connector}${encodePath(key)} [${olRange} | ${mlRange} | ${sizeStr}]\n`;

        if (isSkipped) {
          tree += `${childPrefix}(Content omitted - file size: ${sizeStr})\n`;
        } else if (!noParse && f.codeElements && f.codeElements.length > 0) {
          renderCodeElements(f.codeElements, childPrefix, f.mlStart);
        }
      } else {
        // Directory
        tree += `${prefix}${connector}${encodePath(key)}/\n`;
        renderTree(value, childPrefix);
      }
    }
  }

  function renderCodeElements(elements, prefix, mlOffset) {
    for (let i = 0; i < elements.length; i++) {
      const el = elements[i];
      const isLast = i === elements.length - 1;
      const connector = isLast ? "\u2514\u2500\u2500 " : "\u251c\u2500\u2500 ";
      const childPrefix = prefix + (isLast ? "    " : "\u2502   ");

      const olRange = `OL: ${el.startLine}-${el.endLine}`;
      const mlStart = mlOffset + el.startLine - 1;
      const mlEnd = mlOffset + el.endLine - 1;
      const mlRange = `ML: ${mlStart}-${mlEnd}`;
      const sizeStr = formatBytes(el.size);

      tree += `${prefix}${connector}${el.label} [${olRange} | ${mlRange} | ${sizeStr}]\n`;

      if (el.children && el.children.length > 0) {
        renderCodeElements(el.children, childPrefix, mlOffset);
      }
    }
  }

  renderTree(structure, "");
  return tree;
}

// ---------------------------------------------------------------------------
// Recreate functionality
// ---------------------------------------------------------------------------

function recreateFromFile(inputFile, outputDir, dryRun, overwrite) {
  const files = parseArchive(fs.readFileSync(inputFile));
  const { count, bytes } = restore(files, outputDir, dryRun, overwrite);
  for (const file of files) console.log(`   ${encodePath(file.path)}`);
  console.log(
    `${dryRun ? "Files to recreate" : "Files recreated"}: ${count} (${formatBytes(bytes)})`,
  );
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  const { default: yargs } = await import("yargs/yargs");
  const { hideBin } = await import("yargs/helpers");
  const rawArgv = hideBin(process.argv);
  if (rawArgv.includes("--version") || rawArgv.includes("-v")) {
    console.log(`Combicode (JavaScript), version ${version}`);
    process.exit(0);
  }

  const argv = yargs(rawArgv)
    .scriptName("combicode")
    .strict()
    .usage("$0 [options]")
    .option("o", {
      alias: "output",
      describe: "Output file (combine) or directory (recreate)",
      type: "string",
      default: "combicode.txt",
    })
    .option("d", {
      alias: "dry-run",
      describe: "Preview without making changes",
      type: "boolean",
      default: false,
    })
    .option("i", {
      alias: "include-ext",
      describe: "Comma-separated extensions to include (e.g., .js,.ts)",
      type: "string",
    })
    .option("e", {
      alias: "exclude",
      describe: "Comma-separated glob patterns to exclude",
      type: "string",
    })
    .option("l", {
      alias: "llms-txt",
      describe: "Use the system prompt for llms.txt context",
      type: "boolean",
      default: false,
    })
    .option("gitignore", {
      describe: "Use patterns from the project's .gitignore file",
      type: "boolean",
      default: true,
    })
    .option("header", {
      describe: "Include the introductory prompt and code index in the output",
      type: "boolean",
      default: true,
    })
    .option("skip-content", {
      describe: "Comma-separated glob patterns for files to include in tree but omit content",
      type: "string",
    })
    .option("parse", {
      describe: "Enable code structure parsing",
      type: "boolean",
      default: true,
    })
    .option("r", {
      alias: "recreate",
      describe: "Recreate project from a combicode.txt file",
      type: "boolean",
      default: false,
    })
    .option("input", {
      describe: "Input combicode.txt file for recreate",
      type: "string",
      default: "combicode.txt",
    })
    .option("overwrite", {
      describe: "Overwrite existing files when recreating",
      type: "boolean",
      default: false,
    })
    .version(version)
    .alias("v", "version")
    .help()
    .alias("h", "help").argv;

  const projectRoot = process.cwd();
  console.log(`\u2728 Combicode v${version}`);
  console.log(`\ud83d\udcc2 Root: ${projectRoot}`);

  // --- Recreate mode ---
  if (argv.recreate) {
    const inputFile = path.resolve(projectRoot, argv.input);
    const outputDir = argv.output !== "combicode.txt" ? argv.output : projectRoot;
    recreateFromFile(inputFile, outputDir, argv.dryRun, argv.overwrite);
    return;
  }

  // --- Combine mode ---
  const rootIgnoreManager = ignore();
  rootIgnoreManager.add(DEFAULT_IGNORES);

  if (argv.exclude) {
    rootIgnoreManager.add(argv.exclude.split(","));
  }

  // Parse .gitmodules for submodule paths
  const gitModulesPath = path.join(projectRoot, ".gitmodules");
  if (fs.existsSync(gitModulesPath)) {
    try {
      const content = fs.readFileSync(gitModulesPath, "utf8");
      const lines = content.split(/\r?\n/);
      for (const line of lines) {
        const m = line.match(/^\s*path\s*=\s*(.+?)\s*$/);
        if (m) rootIgnoreManager.add([m[1]]);
      }
    } catch (e) {
      throw new Error(`Cannot read ignore configuration: ${e.message}`);
    }
  }

  const skipContentManager = ignore();
  if (argv.skipContent) {
    skipContentManager.add(argv.skipContent.split(","));
  }

  const absoluteOutputPath = path.resolve(projectRoot, argv.output);

  const allowedExtensions = argv.includeExt
    ? new Set(
        argv.includeExt
          .split(",")
          .map((ext) => ext.trim().toLowerCase())
          .filter(Boolean)
          .map((ext) => (ext.startsWith(".") ? ext : `.${ext}`)),
      )
    : null;

  const ignoreChain = [{ manager: rootIgnoreManager, root: projectRoot }];
  const stats = { ignored: 0 };

  const includedFiles = walkDirectory(
    projectRoot,
    projectRoot,
    ignoreChain,
    allowedExtensions,
    absoluteOutputPath,
    argv.gitignore,
    stats,
  );

  includedFiles.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));

  // Determine skip-content set
  const skipContentSet = new Set();
  if (argv.skipContent) {
    includedFiles.forEach((file) => {
      const relativePath = file.relativePath.replace(/\\/g, "/");
      if (skipContentManager.ignores(relativePath)) {
        skipContentSet.add(file.relativePath);
      }
    });
  }

  if (includedFiles.length === 0) {
    console.error("\n\u274c No files to include. Check your path, .gitignore, or filters.");
    process.exit(1);
  }

  // --- Read file contents & parse code structure ---
  for (const fileObj of includedFiles) {
    const isSkipped = skipContentSet.has(fileObj.relativePath);
    if (isSkipped) {
      fileObj.content = null;
      fileObj.lineCount = 0;
      fileObj.codeElements = [];
    } else {
      try {
        fileObj.content = new TextDecoder("utf-8", {
          fatal: true,
          ignoreBOM: true,
        }).decode(fs.readFileSync(fileObj.path));
        fileObj.size = Buffer.byteLength(fileObj.content);
        fileObj.formattedSize = formatBytes(fileObj.size);
        // Count actual lines: strings ending with \n get an extra empty element from split
        const parts = fileObj.content.split("\n");
        fileObj.lineCount =
          fileObj.content === ""
            ? 0
            : fileObj.content.endsWith("\n")
              ? parts.length - 1
              : parts.length;

        if (argv.parse) {
          const flat = parseCodeStructure(fileObj.relativePath, fileObj.content);
          fileObj.codeElements = nestElements(flat);
        } else {
          fileObj.codeElements = [];
        }
      } catch (e) {
        throw new Error(`Cannot read ${fileObj.relativePath}: ${e.message}`);
      }
    }
  }

  const systemPrompt = argv.llmsTxt ? LLMS_TXT_SYSTEM_PROMPT : DEFAULT_SYSTEM_PROMPT;
  for (const file of includedFiles) {
    file.mlStart = file.mlEnd = 1;
  }
  const makeIndex = () =>
    generateCodeIndex(includedFiles, projectRoot, skipContentSet, !argv.parse);
  const makeHeader = () =>
    argv.header
      ? `${systemPrompt.trimEnd()}\n\n<code_index>\n${makeIndex()}</code_index>\n\n<merged_code>\n`
      : "<merged_code>\n";
  let currentMl = makeHeader().split("\n").length;
  const records = includedFiles.map((file) => {
    file.mlStart = currentMl + 2;
    file.mlEnd = file.mlStart + Math.max(1, file.lineCount) - 1;
    const metadata = `OL: ${file.lineCount ? 1 : 0}-${file.lineCount} | ML: ${file.mlStart}-${file.mlEnd} | ${file.formattedSize}`;
    const serialized = record(file.relativePath.split(path.sep).join("/"), metadata, file.content);
    currentMl += serialized.split("\n").length - 1;
    return serialized;
  });
  const codeIndex = makeIndex();

  // Calculate total content size
  const totalSizeBytes = includedFiles.reduce((acc, file) => {
    if (skipContentSet.has(file.relativePath)) return acc;
    return acc + file.size;
  }, 0);

  // --- Dry run ---
  if (argv.dryRun) {
    console.log("\n\ud83d\udccb Files to include (dry run):\n");
    console.log(codeIndex);
    console.log(`\n\ud83d\udcca Summary:`);
    console.log(`   \u2022 Total files: ${includedFiles.length}`);
    console.log(`   \u2022 Total size: ${formatBytes(totalSizeBytes)}`);
    if (skipContentSet.size > 0) {
      console.log(`   \u2022 Content omitted: ${skipContentSet.size} files`);
    }
    console.log(`\n\u2705 Done!`);
    return;
  }

  const output = makeHeader() + records.join("") + "</merged_code>\n";
  fs.mkdirSync(path.dirname(absoluteOutputPath), { recursive: true });
  atomicWrite(absoluteOutputPath, output);
  const totalLines = output.split("\n").length - 1;

  console.log(`\n\ud83d\udcca Summary:`);
  console.log(`   \u2022 Included: ${includedFiles.length} files (${formatBytes(totalSizeBytes)})`);
  if (skipContentSet.size > 0) {
    console.log(`   \u2022 Content omitted: ${skipContentSet.size} files`);
  }
  console.log(`   \u2022 Ignored:  ${stats.ignored} files/dirs`);
  console.log(`   \u2022 Output:   ${argv.output} (~${totalLines} lines)`);
  console.log(`\n\u2705 Done!`);
}

main().catch((err) => {
  console.error(`An unexpected error occurred: ${err.message}`);
  process.exit(1);
});
