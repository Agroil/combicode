"""Length-framed UTF-8 archives. File bodies are opaque bytes during extraction."""

import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote


def encode_path(name):
    return quote(name, safe="/!~*'()")


def record(name, metadata, content):
    validate_path(name)
    omitted = content is None
    body = "(Content omitted)" if omitted else content
    size = len(body.encode("utf-8"))
    separator = "" if body.endswith("\n") else "\n"
    return (
        f"# FILE: {encode_path(name)} [{metadata}] [BYTES: {size} | OMITTED: {int(omitted)}]\n"
        f"````\n{body}{separator}````\n\n"
    )


def validate_path(name):
    if (
        not name
        or re.search(r"[\\:\x00-\x1f\x7f]", name)
        or any(
            part in ("", ".", "..")
            or part.endswith((".", " "))
            or re.match(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", part)
            for part in name.split("/")
        )
    ):
        raise ValueError(f"Unsafe archive path: {name!r}")


def parse_archive(data):
    opening = b"<merged_code>\n"
    offset = data.find(opening)
    if offset < 0 or (offset > 0 and data[offset - 1] != 10):
        raise ValueError("Missing merged_code section")
    offset += len(opening)
    files, seen = [], set()
    closing = b"</merged_code>\n"
    while not data.startswith(closing, offset):
        end = data.find(b"\n", offset)
        if end < 0:
            raise ValueError("Incomplete archive")
        match = re.fullmatch(
            rb"# FILE: (\S+) \[OL: \d+-\d+ \| ML: \d+-\d+ \| [^\]]+\] "
            rb"\[BYTES: (\d+) \| OMITTED: ([01])\]",
            data[offset:end],
        )
        if not match:
            raise ValueError("Invalid archive record (old formats are unsupported)")
        if re.search(rb"%(?![0-9a-fA-F]{2})", match[1]):
            raise ValueError("Invalid percent-encoded path")
        name = unquote(match[1].decode("ascii"), errors="strict")
        validate_path(name)
        if name in seen:
            raise ValueError(f"Duplicate archive path: {name}")
        seen.add(name)
        length = int(match[2])
        offset = end + 1
        if data[offset : offset + 5] != b"````\n":
            raise ValueError("Missing opening fence")
        offset += 5
        content = data[offset : offset + length]
        if len(content) != length:
            raise ValueError("Truncated archive record")
        offset += length
        footer = b"````\n\n" if content.endswith(b"\n") else b"\n````\n\n"
        if data[offset : offset + len(footer)] != footer:
            raise ValueError("Invalid record ending")
        offset += len(footer)
        if match[3] == b"0":
            files.append((name, content))
    if offset + len(closing) != len(data):
        raise ValueError("Unexpected data after merged_code section")
    for name in seen:
        parts = name.split("/")
        if any("/".join(parts[:i]) in seen for i in range(1, len(parts))):
            raise ValueError(f"Conflicting archive paths: {name}")
    if not seen:
        raise ValueError("No files found in the input file")
    return files


def safe_target(root, name):
    validate_path(name)
    target = root / name
    components = [root]
    for part in name.split("/"):
        components.append(components[-1] / part)
    for component in components:
        if component.is_symlink():
            raise ValueError(f"Symlink destination: {component}")
    return target


def restore(files, output_dir, dry_run, overwrite):
    root = Path(os.path.abspath(output_dir))
    targets = [(safe_target(root, name), name, content) for name, content in files]
    count = size = 0
    for target, name, content in targets:
        if target.exists() and not overwrite:
            continue
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            safe_target(root, name)
            atomic_write(target, content, overwrite)

        count += 1
        size += len(content)
    return count, size


def atomic_write(target, content, overwrite=True):
    with tempfile.TemporaryDirectory(prefix=".combicode-", dir=target.parent) as directory:
        staged = Path(directory) / "content"
        staged.write_bytes(content)
        if overwrite:
            os.replace(staged, target)
        else:
            os.link(staged, target)
