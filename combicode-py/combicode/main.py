import json
import math
import os
import sys
from pathlib import Path

import click
import pathspec

from . import __version__
from .archive import atomic_write, encode_path, parse_archive, record, restore
from .parsers import nest_elements, parse_code_structure

# ---------------------------------------------------------------------------
# System Prompts
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = """\
You are an expert software architect. The user is providing you with the complete source code for a project, contained in a single file. Your task is to meticulously analyze the provided codebase to gain a comprehensive understanding of its structure, functionality, dependencies, and overall architecture.

A code map with expanded tree structure `<code_index>` is provided below to give you a high-level overview. The subsequent section `<merged_code>` contains the full content of each file (read the ML line range from this output file), clearly marked with a file header.

Your instructions are:
1.  Analyze Thoroughly: Read through every file to understand its purpose and how it interacts with other files.
2.  Identify Key Components: Pay close attention to configuration files (like package.json, pyproject.toml), entry points (like index.js, main.py), and core logic.
3.  Use the Code Map: The code map shows classes, functions, loops with their line numbers (OL = Original Line, ML = Merged Line) and sizes for precise navigation."""

LLMS_TXT_SYSTEM_PROMPT = """\
You are an expert software architect. The user is providing you with the full documentation for a project. This file contains the complete context needed to understand the project's features, APIs, and usage for a specific version. Your task is to act as a definitive source of truth based *only* on this provided documentation.

When answering questions or writing code, adhere strictly to the functions, variables, and methods described in this context. Do not use or suggest any deprecated or older functionalities that are not present here.

A code map with expanded tree structure is provided below for a high-level overview."""

# Minimal safety ignores
DEFAULT_IGNORES = json.loads(Path(__file__).with_name("ignore.json").read_text())


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def is_likely_binary(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return b"\0" in f.read(1024)
    except OSError as error:
        raise click.ClickException(f"Cannot read {path}: {error}") from error


def format_bytes(size: int) -> str:
    if size == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = min(len(size_name) - 1, math.floor(math.log(size, 1024)))
    p = math.pow(1024, i)
    # Round half up to one decimal, matching the JavaScript package, and drop
    # a trailing ".0" (e.g. 2048 bytes is "2KB", not "2.0KB").
    value = math.floor(size / p * 10 + 0.5) / 10
    return f"{value:g}{size_name[i]}"


# ---------------------------------------------------------------------------
# Code Parsers
# ---------------------------------------------------------------------------


def generate_code_index(files_meta, root: Path, skip_content_set: set, no_parse: bool) -> str:
    tree_lines = [f"{encode_path(root.name)}/"]

    # Build directory structure
    structure = {"children": {}}
    for f in files_meta:
        parts = f["relative_path"].parts
        current = structure
        for idx, part in enumerate(parts):
            is_file = idx == len(parts) - 1
            if is_file:
                current["children"][part] = {"file": f}
            else:
                current = current["children"].setdefault(part, {"children": {}})

    def render_tree(level, prefix):
        keys = sorted(level["children"])
        for idx, key in enumerate(keys):
            is_last = idx == len(keys) - 1
            connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
            child_prefix = prefix + ("    " if is_last else "\u2502   ")
            value = level["children"][key]

            if "file" in value:
                f = value["file"]
                ol_range = f"OL: {1 if f['line_count'] else 0}-{f['line_count']}"
                ml_range = f"ML: {f['ml_start']}-{f['ml_end']}"
                size_str = f["formatted_size"]
                rel_str = f["relative_path"].as_posix()
                is_skipped = rel_str in skip_content_set

                tree_lines.append(
                    f"{prefix}{connector}{encode_path(key)} [{ol_range} | {ml_range} | {size_str}]"
                )

                if is_skipped:
                    tree_lines.append(f"{child_prefix}(Content omitted - file size: {size_str})")
                elif not no_parse and f.get("code_elements"):
                    render_code_elements(f["code_elements"], child_prefix, f["ml_start"])
            else:
                tree_lines.append(f"{prefix}{connector}{encode_path(key)}/")
                render_tree(value, child_prefix)

    def render_code_elements(elements, prefix, ml_offset):
        for idx, el in enumerate(elements):
            is_last = idx == len(elements) - 1
            connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
            child_prefix = prefix + ("    " if is_last else "\u2502   ")

            ol_range = f"OL: {el['start_line']}-{el['end_line']}"
            ml_start = ml_offset + el["start_line"] - 1
            ml_end = ml_offset + el["end_line"] - 1
            ml_range = f"ML: {ml_start}-{ml_end}"
            size_str = format_bytes(el["size"])

            tree_lines.append(
                f"{prefix}{connector}{el['label']} [{ol_range} | {ml_range} | {size_str}]"
            )

            if el.get("children"):
                render_code_elements(el["children"], child_prefix, ml_offset)

    render_tree(structure, "")
    return "\n".join(tree_lines) + "\n"


# ---------------------------------------------------------------------------
# Recreate
# ---------------------------------------------------------------------------


def recreate_from_file(input_file: str, output_dir: str, dry_run: bool, overwrite: bool):
    try:
        files = parse_archive(Path(input_file).read_bytes())
        count, size = restore(files, output_dir, dry_run, overwrite)
        for name, _ in files:
            click.echo(f"   {encode_path(name)}")
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    label = "Files to recreate" if dry_run else "Files recreated"
    click.echo(f"{label}: {count} ({format_bytes(size)})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.command(context_settings=dict(help_option_names=["-h", "--help"]))
@click.option(
    "-o",
    "--output",
    default="combicode.txt",
    help="Output file (combine) or directory (recreate).",
    show_default=True,
)
@click.option("-d", "--dry-run", is_flag=True, help="Preview without making changes.")
@click.option(
    "-i",
    "--include-ext",
    help="Comma-separated list of extensions to exclusively include (e.g., .py,.js).",
)
@click.option(
    "-e", "--exclude", help="Comma-separated list of additional glob patterns to exclude."
)
@click.option("-l", "--llms-txt", is_flag=True, help="Use the system prompt for llms.txt context.")
@click.option(
    "--no-gitignore", is_flag=True, help="Do not use patterns from the project's .gitignore file."
)
@click.option(
    "--no-header", is_flag=True, help="Omit the introductory prompt and file tree from the output."
)
@click.option(
    "--skip-content",
    help="Comma-separated glob patterns for files to include in tree but omit content.",
)
@click.option(
    "--no-parse", is_flag=True, help="Disable code structure parsing (show only file tree)."
)
@click.option("-r", "--recreate", is_flag=True, help="Recreate project from a combicode.txt file.")
@click.option(
    "--input",
    default="combicode.txt",
    help="Input combicode.txt file for recreate.",
    show_default=True,
)
@click.option("--overwrite", is_flag=True, help="Overwrite existing files when recreating.")
@click.version_option(
    __version__,
    "-v",
    "--version",
    prog_name="Combicode",
    message="%(prog)s (Python), version %(version)s",
)
def cli(
    output,
    dry_run,
    include_ext,
    exclude,
    llms_txt,
    no_gitignore,
    no_header,
    skip_content,
    no_parse,
    recreate,
    input,
    overwrite,
):
    """Combicode combines your project's code into a single file for LLM context."""

    project_root = Path.cwd().resolve()
    click.echo(f"\u2728 Combicode v{__version__}")
    click.echo(f"\U0001f4c2 Root: {project_root}")

    # --- Recreate mode ---
    if recreate:
        input_file = str((project_root / input).resolve())
        output_dir = output if output != "combicode.txt" else str(project_root)
        recreate_from_file(input_file, output_dir, dry_run, overwrite)
        return

    # --- Combine mode ---
    default_ignore_patterns = list(DEFAULT_IGNORES)
    if exclude:
        default_ignore_patterns.extend(exclude.split(","))

    # Parse .gitmodules
    gitmodules_path = project_root / ".gitmodules"
    if gitmodules_path.exists():
        try:
            with gitmodules_path.open("r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("path") and "=" in stripped:
                        key, value = stripped.split("=", 1)
                        if key.strip() == "path":
                            default_ignore_patterns.append(value.strip())
        except (OSError, UnicodeError) as error:
            raise click.ClickException(f"Cannot read .gitmodules: {error}") from error

    root_spec = pathspec.GitIgnoreSpec.from_lines(default_ignore_patterns)

    skip_content_spec = None
    if skip_content:
        skip_content_patterns = skip_content.split(",")
        skip_content_spec = pathspec.GitIgnoreSpec.from_lines(skip_content_patterns)

    output_path = (project_root / output).resolve()

    included_files_data = []
    allowed_extensions = (
        {f".{ext.strip().lstrip('.').lower()}" for ext in include_ext.split(",") if ext.strip()}
        if include_ext
        else None
    )

    spec_map = {project_root: []}
    stats_ignored = 0

    def walk_error(error):
        raise click.ClickException(f"Cannot scan directory: {error}") from error

    for dirpath, dirnames, filenames in os.walk(project_root, topdown=True, onerror=walk_error):
        current_dir = Path(dirpath)

        if current_dir == project_root:
            current_chain = []
        else:
            current_chain = spec_map.get(current_dir, [])

        my_chain = list(current_chain)
        if not no_gitignore:
            gitignore_path = current_dir / ".gitignore"
            if gitignore_path.exists():
                try:
                    with gitignore_path.open("r", encoding="utf-8") as f:
                        lines = f.read().splitlines()
                        new_spec = pathspec.GitIgnoreSpec.from_lines(lines)
                        my_chain.append((current_dir, new_spec))
                except (OSError, UnicodeError) as error:
                    raise click.ClickException(f"Cannot read {gitignore_path}: {error}") from error

        for d in dirnames:
            spec_map[current_dir / d] = my_chain

        def is_ignored(name, is_dir=False, current_dir=current_dir, my_chain=my_chain):
            full_path = current_dir / name
            try:
                rel_to_project = full_path.relative_to(project_root).as_posix()
            except ValueError:
                return False
            if root_spec.match_file(rel_to_project + ("/" if is_dir else "")):
                return True
            ignored = False
            for spec_root, spec in my_chain:
                rel_to_spec = full_path.relative_to(spec_root).as_posix()
                result = spec.check_file(rel_to_spec + ("/" if is_dir else ""))
                if result.include is not None:
                    ignored = result.include
            return ignored

        i = 0
        while i < len(dirnames):
            if (current_dir / dirnames[i]).is_symlink() or is_ignored(dirnames[i], is_dir=True):
                del dirnames[i]
                stats_ignored += 1
            else:
                i += 1

        for fname in filenames:
            f_path = current_dir / fname
            if f_path.is_symlink() or not f_path.is_file():
                continue
            if output_path and f_path.resolve() == output_path:
                continue
            if is_ignored(fname):
                stats_ignored += 1
                continue
            if is_likely_binary(f_path):
                stats_ignored += 1
                continue
            if allowed_extensions and f_path.suffix.lower() not in allowed_extensions:
                stats_ignored += 1
                continue
            try:
                size = f_path.stat().st_size
                included_files_data.append(
                    {
                        "path": f_path,
                        "relative_path": f_path.relative_to(project_root),
                        "size": size,
                        "formatted_size": format_bytes(size),
                    }
                )
            except OSError as error:
                raise click.ClickException(f"Cannot stat {f_path}: {error}") from error

    if not included_files_data:
        click.echo("\u274c No files to include. Check your path or filters.", err=True)
        sys.exit(1)

    included_files_data.sort(key=lambda x: x["path"])

    # Determine skip-content set
    skip_content_set = set()
    if skip_content_spec:
        for item in included_files_data:
            rel_path_str = item["relative_path"].as_posix()
            if skip_content_spec.match_file(rel_path_str):
                skip_content_set.add(rel_path_str)

    # Read contents & parse code structure
    for item in included_files_data:
        rel_str = item["relative_path"].as_posix()
        is_skipped = rel_str in skip_content_set

        if is_skipped:
            item["content"] = None
            item["line_count"] = 0
            item["code_elements"] = []
        else:
            try:
                content = item["path"].read_bytes().decode("utf-8")
                item["content"] = content
                item["size"] = len(content.encode("utf-8"))
                item["formatted_size"] = format_bytes(item["size"])
                # Count actual lines: for content ending with \n, count("\n") gives the right number
                item["line_count"] = (
                    0
                    if not content
                    else (
                        content.count("\n") if content.endswith("\n") else content.count("\n") + 1
                    )
                )
                if not no_parse:
                    flat = parse_code_structure(str(item["relative_path"]), content)
                    item["code_elements"] = nest_elements(flat)
                else:
                    item["code_elements"] = []
            except (OSError, UnicodeError) as e:
                raise click.ClickException(f"Cannot read {rel_str}: {e}") from e

    system_prompt = LLMS_TXT_SYSTEM_PROMPT if llms_txt else DEFAULT_SYSTEM_PROMPT
    for item in included_files_data:
        item["ml_start"] = item["ml_end"] = 1

    def make_index():
        return generate_code_index(included_files_data, project_root, skip_content_set, no_parse)

    def make_header():
        if no_header:
            return "<merged_code>\n"
        return f"{system_prompt.rstrip()}\n\n<code_index>\n{make_index()}</code_index>\n\n<merged_code>\n"

    current_ml = make_header().count("\n") + 1
    records = []
    for item in included_files_data:
        item["ml_start"] = current_ml + 2
        lines = max(1, item["line_count"])
        item["ml_end"] = item["ml_start"] + lines - 1
        metadata = (
            f"OL: {1 if item['line_count'] else 0}-{item['line_count']} | ML: {item['ml_start']}-{item['ml_end']} | "
            f"{item['formatted_size']}"
        )
        serialized = record(item["relative_path"].as_posix(), metadata, item["content"])
        records.append(serialized)
        current_ml += serialized.count("\n")
    code_index = make_index()

    # Total content size
    total_size_bytes = sum(
        item["size"]
        for item in included_files_data
        if item["relative_path"].as_posix() not in skip_content_set
    )

    # --- Dry run ---
    if dry_run:
        click.echo("\n\U0001f4cb Files to include (dry run):\n")
        click.echo(code_index)
        click.echo("\n\U0001f4ca Summary:")
        click.echo(f"   \u2022 Total files: {len(included_files_data)}")
        click.echo(f"   \u2022 Total size: {format_bytes(total_size_bytes)}")
        if skip_content_set:
            click.echo(f"   \u2022 Content omitted: {len(skip_content_set)} files")
        click.echo("\n\u2705 Done!")
        return

    try:
        combined = make_header() + "".join(records) + "</merged_code>\n"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(output_path, combined.encode("utf-8"))
        total_lines = combined.count("\n")

        click.echo("\n\U0001f4ca Summary:")
        click.echo(
            f"   \u2022 Included: {len(included_files_data)} files ({format_bytes(total_size_bytes)})"
        )
        if skip_content_set:
            click.echo(f"   \u2022 Content omitted: {len(skip_content_set)} files")
        click.echo(f"   \u2022 Ignored:  {stats_ignored} files/dirs")
        click.echo(f"   \u2022 Output:   {output} (~{total_lines} lines)")
        click.echo("\n\u2705 Done!")
    except OSError as e:
        click.echo(f"\n\u274c Error writing to output file: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
