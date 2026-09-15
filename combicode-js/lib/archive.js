const fs = require("node:fs");
const path = require("node:path");

function encodePath(name) {
  return name.split("/").map(encodeURIComponent).join("/");
}

function record(name, metadata, content) {
  validatePath(name);
  const omitted = content === null;
  const body = omitted ? "(Content omitted)" : content;
  const bytes = Buffer.byteLength(body);
  return `# FILE: ${encodePath(name)} [${metadata}] [BYTES: ${bytes} | OMITTED: ${omitted ? 1 : 0}]\n\`\`\`\`\n${body}${body.endsWith("\n") ? "" : "\n"}\`\`\`\`\n\n`;
}

function parseArchive(data) {
  const opening = Buffer.from("<merged_code>\n");
  let offset = data.indexOf(opening);
  if (offset < 0 || (offset > 0 && data[offset - 1] !== 10))
    throw new Error("Missing merged_code section");
  offset += opening.length;
  const files = [];
  const seen = new Set();
  while (!data.subarray(offset).equals(Buffer.from("</merged_code>\n"))) {
    const end = data.indexOf(10, offset);
    if (end < 0) throw new Error("Incomplete archive");
    const header = data.subarray(offset, end).toString("utf8");
    const match =
      /^# FILE: (\S+) \[OL: \d+-\d+ \| ML: \d+-\d+ \| [^\]]+\] \[BYTES: (\d+) \| OMITTED: ([01])\]$/.exec(
        header,
      );
    if (!match) throw new Error("Invalid archive record (old formats are unsupported)");
    const name = decodeURIComponent(match[1]);
    validatePath(name);
    if (seen.has(name)) throw new Error(`Duplicate archive path: ${name}`);
    seen.add(name);
    const length = Number(match[2]);
    if (!Number.isSafeInteger(length) || length > data.length)
      throw new Error("Invalid record length");
    offset = end + 1;
    if (data.subarray(offset, offset + 5).toString() !== "````\n")
      throw new Error("Missing opening fence");
    offset += 5;
    const content = data.subarray(offset, offset + length);
    if (content.length !== length) throw new Error("Truncated archive record");
    offset += length;
    const footer = Buffer.from(content.at(-1) === 10 ? "````\n\n" : "\n````\n\n");
    if (!data.subarray(offset, offset + footer.length).equals(footer))
      throw new Error("Invalid record ending");
    offset += footer.length;
    if (match[3] === "0") files.push({ path: name, content });
  }
  for (const name of seen) {
    const parts = name.split("/");
    for (let i = 1; i < parts.length; i++) {
      if (seen.has(parts.slice(0, i).join("/")))
        throw new Error(`Conflicting archive paths: ${name}`);
    }
  }
  if (!seen.size) throw new Error("No files found in the input file");
  return files;
}

function validatePath(name) {
  if (
    !name ||
    /[\\:\x00-\x1f\x7f]/.test(name) ||
    name
      .split("/")
      .some(
        (part) =>
          !part ||
          part === "." ||
          part === ".." ||
          /[. ]$/.test(part) ||
          /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(part),
      )
  ) {
    throw new Error(`Unsafe archive path: ${JSON.stringify(name)}`);
  }
}

function safeTarget(root, name) {
  validatePath(name);
  const target = path.resolve(root, name);
  // Reject symlinks in every existing component, including the destination root.
  let current = root;
  for (const part of ["", ...name.split("/")]) {
    current = path.join(current, part);
    try {
      if (fs.lstatSync(current).isSymbolicLink())
        throw new Error(`Symlink destination: ${current}`);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  return target;
}

function restore(files, outputDir, dryRun, overwrite) {
  const root = path.resolve(outputDir);
  const targets = files.map((file) => ({
    ...file,
    target: safeTarget(root, file.path),
  }));
  let count = 0;
  let bytes = 0;
  for (const file of targets) {
    if (fs.existsSync(file.target) && !overwrite) continue;
    if (!dryRun) {
      fs.mkdirSync(path.dirname(file.target), { recursive: true });
      safeTarget(root, file.path);
      atomicWrite(file.target, file.content, overwrite);
    }
    count++;
    bytes += file.content.length;
  }
  return { count, bytes };
}

function atomicWrite(target, content, overwrite = true) {
  const temporary = fs.mkdtempSync(path.join(path.dirname(target), ".combicode-"));
  const staged = path.join(temporary, "content");
  try {
    fs.writeFileSync(staged, content);
    if (overwrite) fs.renameSync(staged, target);
    else fs.linkSync(staged, target);
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

module.exports = { record, parseArchive, restore, encodePath, atomicWrite };
