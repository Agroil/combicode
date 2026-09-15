import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest
from click.testing import CliRunner
from combicode.archive import parse_archive, record, restore
from combicode.main import cli

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = json.loads((ROOT / "tests/fixtures/archive.json").read_text())
METADATA = "OL: 1-1 | ML: 4-4 | 1B"


def archive(body):
    return f"<merged_code>\n{body}</merged_code>\n".encode()


@pytest.mark.parametrize("name,content", FIXTURES.items())
def test_exact_archive_round_trip(name, content):
    assert parse_archive(archive(record(name, METADATA, content))) == [(name, content.encode())]


@pytest.mark.parametrize(
    "name",
    [
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
    ],
)
def test_unsafe_paths(name):
    body = record("safe", METADATA, "text").replace("# FILE: safe ", f"# FILE: {quote(name)} ")
    with pytest.raises(ValueError, match="Unsafe"):
        parse_archive(archive(body))


def test_invalid_and_omitted_records():
    assert parse_archive(archive(record("skip", METADATA, None))) == []
    body = record("a", METADATA, "hello")
    with pytest.raises(ValueError, match="Conflicting"):
        parse_archive(archive(body + record("a/b", METADATA, "child")))
    for data in [
        archive(body + body),
        archive(body)[:-5],
        archive(body.replace("BYTES: 5", "BYTES: 50")),
    ]:
        with pytest.raises(ValueError):
            parse_archive(data)
    with pytest.raises(ValueError, match="unsupported"):
        parse_archive(archive(f"# FILE: a [{METADATA}]\n````\nhello\n````\n\n"))


def test_restore_rejects_symlinks_before_writing(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (output / "link").symlink_to(outside, target_is_directory=True)
    files = [("safe.txt", b"safe"), ("link/escape", b"bad")]
    for dry_run in [True, False]:
        with pytest.raises(ValueError, match="Symlink"):
            restore(files, output, dry_run, True)
        assert not (output / "safe.txt").exists()


def test_overwrite_replaces_hardlinks_and_counts_written_files(tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"old")
    output = tmp_path / "out"
    output.mkdir()
    (output / "a").hardlink_to(outside)
    assert restore([("a", b"new")], output, False, False) == (0, 0)
    assert restore([("a", b"new")], output, False, True) == (1, 3)
    assert outside.read_bytes() == b"old"
    assert (output / "a").read_bytes() == b"new"


@pytest.mark.parametrize("writer", ["python", "node"])
@pytest.mark.parametrize("reader", ["python", "node"])
@pytest.mark.parametrize("no_header", [False, True])
def test_cross_runtime_round_trip(tmp_path, monkeypatch, writer, reader, no_header):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for cross-runtime tests")
    monkeypatch.chdir(tmp_path)
    for name, content in FIXTURES.items():
        source = tmp_path / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content.encode())

    def invoke(runtime, args):
        if runtime == "node":
            result = subprocess.run(
                [node, str(ROOT / "combicode-js/index.js"), *args], capture_output=True, text=True
            )
            assert result.returncode == 0, result.stderr
        else:
            result = CliRunner().invoke(cli, args)
            assert result.exit_code == 0, result.output

    invoke(writer, ["--no-parse"] + (["--no-header"] if no_header else []))
    invoke(reader, ["--recreate", "-o", "restored"])
    for name, content in FIXTURES.items():
        assert (tmp_path / "restored" / name).read_bytes() == content.encode()


def test_cross_runtime_archives_are_byte_identical(tmp_path, monkeypatch):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for cross-runtime tests")
    projects = {}
    for runtime in ["node", "python"]:
        # Both roots must share a basename because the code index names the root.
        project = tmp_path / runtime / "project"
        project.mkdir(parents=True)
        for name, content in FIXTURES.items():
            source = project / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(content.encode())
        # Cover single-decimal sizes and trailing-zero trimming.
        for name, size in [("512.txt", 512), ("1280.txt", 1280), ("2048.txt", 2048)]:
            (project / name).write_bytes(b"x" * size)
        projects[runtime] = project

    result = subprocess.run(
        [node, str(ROOT / "combicode-js/index.js"), "-o", "combicode.txt"],
        cwd=projects["node"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    monkeypatch.chdir(projects["python"])
    result = CliRunner().invoke(cli, ["-o", "combicode.txt"])
    assert result.exit_code == 0, result.output

    assert (projects["python"] / "combicode.txt").read_bytes() == (
        projects["node"] / "combicode.txt"
    ).read_bytes()
