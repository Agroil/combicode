# Combicode

[![NPM Version](https://img.shields.io/npm/v/combicode.svg)](https://www.npmjs.com/package/combicode)
[![PyPI Version](https://img.shields.io/pypi/v/combicode.svg)](https://pypi.org/project/combicode/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Combine a project's UTF-8 source files into one readable context file, with a directory tree and code outline. Restore those files with either the JavaScript or Python CLI.

## Quick start

Requires **Node.js 22.12+** or **Python 3.11+**.

```sh
npx combicode
# or
pipx run combicode
```

Run from the project root. The default output is `combicode.txt`.

```sh
# Preview without writing
combicode --dry-run

# Include selected file types (case-insensitive)
combicode -i '.ts, .tsx, .css' -o output/context.txt

# Add exclusions
combicode -e '**/*.generated.*,fixtures/'

# Keep files in the tree but omit their contents
combicode --skip-content '**/*.test.ts,**/*.spec.ts'

# Disable code outline parsing
combicode --no-parse

# Use a documentation-oriented prompt
combicode --llms-txt -i .md -o documentation.txt

# Restore an archive into a directory
combicode --recreate --input documentation.txt -o restored
combicode --recreate --input documentation.txt -o restored --dry-run
combicode --recreate --input documentation.txt -o restored --overwrite
```

`-i` always means `--include-ext`; use `--input` for restoration. `--llms-txt` changes the introductory prompt only: it does not read links or fetch documentation.

## Options

| Option | Alias | Purpose | Default |
| --- | --- | --- | --- |
| `--output` | `-o` | Output file, or restore directory | `combicode.txt`; current directory in restore mode |
| `--dry-run` | `-d` | Preview without writing | Off |
| `--include-ext` | `-i` | Comma-separated extensions | All |
| `--exclude` | `-e` | Additional comma-separated gitignore patterns | None |
| `--skip-content` | | Show matching files in the tree but omit content | None |
| `--no-parse` | | Disable code outline parsing | Parsing enabled |
| `--no-gitignore` | | Ignore project `.gitignore` files | Respect `.gitignore` |
| `--no-header` | | Omit the prompt and code index | Header enabled |
| `--llms-txt` | `-l` | Use a documentation prompt | Off |
| `--recreate` | `-r` | Restore files from an archive | Off |
| `--input` | | Archive to restore | `combicode.txt` |
| `--overwrite` | | Replace existing files during restoration | Off |
| `--version` | `-v` | Print version | |
| `--help` | `-h` | Print help | |

Output parent directories are created automatically. Unknown options fail with a nonzero exit status.

## File selection

Both packages use the shared rules in [configs/ignore.json](configs/ignore.json). These exclude dependency folders, build outputs, common binary formats, `.git`, `.env`, and other generated files. Defaults and explicit `--exclude` patterns take precedence over project `.gitignore` files; `--no-gitignore` does not disable these defaults.

Nested `.gitignore` files can override patterns from parent files. An ignored directory is not traversed, so a child rule cannot re-include a file inside it. Submodule paths from `.gitmodules` are excluded. Symlinks and non-regular files are not collected. The output file excludes itself.

Files with NUL bytes in the initial sample are treated as binary. Other selected files must decode as UTF-8; read or decoding failures stop generation without replacing an existing output. Ignore rules are file-selection defaults, not secret detection; inspect the preview before sharing project content.

## Code outline and line references

The output consists of an optional prompt and `<code_index>`, followed by `<merged_code>` records. Entries show:

- **OL**: original source line numbers.
- **ML**: line numbers within the combined output.
- **Size**: UTF-8 byte size.

Empty files and omitted content use `OL: 0-0`; their ML range points to the blank or placeholder line in the output.

For example, `ML: 53-57` refers to lines 53 through 57 in the output:

```sh
sed -n '53,57p' combicode.txt
```

The Python package uses Python's built-in AST for valid Python sources. Other outlines, including Python outlines produced by the JavaScript package, use heuristics. They cover these language families:

| Languages | Typical outline elements |
| --- | --- |
| Python | Classes, functions, async functions, loops |
| JavaScript, TypeScript | Classes, functions, methods, interfaces, test suites |
| Go, Rust | Types, functions, implementation blocks |
| Java, C/C++, C# | Types and methods |
| PHP, Ruby | Classes, modules, functions |
| Swift, Kotlin, Scala | Types and functions |
| Lua, Perl, shell scripts | Functions and some control-flow blocks |

Configuration, markup, and other text files are included without a code outline. Heuristic outlines are approximate: unusual syntax, multiline declarations, regular-expression literals, and language-specific constructs can be missed or assigned imperfect ranges. File content remains intact. Use `--no-parse` when only the file tree is needed.

## Archive format and restoration

Each record includes a percent-encoded relative path, OL/ML information, exact UTF-8 byte length, and an explicit omission flag. Four-backtick fences keep the output readable; the byte length, rather than the fences, delimits the original content.

This preserves empty files, final newlines, CRLF, Unicode, and source text containing archive-like headers or Markdown fences. JavaScript and Python archives are interchangeable. Omitted records are never restored, and existing files are skipped unless `--overwrite` is supplied. Summaries count files actually written or eligible for writing in a dry run.

Restoration validates records and paths before writing. Absolute paths, traversal components, duplicate names, control characters, backslashes, colon-containing paths, trailing dots/spaces, and Windows device names are rejected. Symlinks at or below the destination are rejected. Each file is staged and atomically installed; overwriting a hardlink does not change its other links. This is not a whole-project transaction: an I/O failure can leave earlier files restored. Use a destination that is not being modified concurrently by another process.

**Old archive formats are unsupported. Regenerate existing context files before restoring them.** Archives preserve file content, not permissions, timestamps, symlinks, or binary files. Editing a record's content requires updating its byte length.

## Development

```sh
bash scripts/sync-assets.sh
npm ci --prefix combicode-js
npm test --prefix combicode-js
python3 -m venv .venv
.venv/bin/python -m pip install -e './combicode-py[test]' build
.venv/bin/python -m pytest -q combicode-py
.venv/bin/python -m ruff check --config combicode-py/pyproject.toml combicode-py scripts
.venv/bin/python -m build combicode-py
```

On Windows, use the corresponding executables under `.venv/Scripts`. The Python cross-runtime tests also require Node.js and installed JavaScript dependencies; they skip when Node.js is unavailable. CI installs both runtimes and tests minimum supported versions plus current runtimes on Linux, macOS, and Windows.

Structure:

- `combicode-js/index.js` and `combicode-py/combicode/main.py`: CLI, file selection, and index generation.
- `lib/parsers.js` and `combicode/parsers.py`: language outlines.
- `lib/archive.js` and `combicode/archive.py`: framing, validation, and restoration.
- `tests/fixtures`: shared round-trip cases.
- `configs/ignore.json`: canonical defaults, synchronized into both packages.

Run `python3 scripts/prepare-release.py MAJOR.MINOR.PATCH` to update both package versions and the npm lockfile. Update changelogs and review changes before tagging and publishing a release. Release workflows synchronize shared assets, run package tests, build distributions, and publish.

## License

[MIT](LICENSE).
