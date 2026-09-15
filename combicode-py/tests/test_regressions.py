import re
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from combicode.archive import parse_archive
from combicode.main import cli
from combicode.parsers import parse_code_structure

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(params=["python", "node"])
def run_cli(request, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    node = shutil.which("node")
    if request.param == "node" and not node:
        pytest.skip("Node.js is required for cross-runtime tests")

    def invoke(*args, success=True):
        if request.param == "python":
            result = CliRunner().invoke(cli, list(args))
            code, output = result.exit_code, result.output
        else:
            result = subprocess.run(
                [node, str(ROOT / "combicode-js/index.js"), *args], capture_output=True, text=True
            )
            code, output = result.returncode, result.stdout + result.stderr
        assert (code == 0) == success, output
        return output

    return invoke


def test_nested_negation_and_explicit_directory_exclusion(run_cli):
    Path(".gitignore").write_text("*.tmp\n")
    Path("nested").mkdir()
    Path("nested/.gitignore").write_text("!keep.tmp\n")
    Path("nested/keep.tmp").write_text("keep")
    Path("nested/drop.tmp").write_text("drop")
    run_cli("--exclude", "unused/")
    names = dict(parse_archive(Path("combicode.txt").read_bytes()))
    assert "nested/keep.tmp" in names
    assert "nested/drop.tmp" not in names
    run_cli("--exclude", "nested/")
    assert all(
        not name.startswith("nested/")
        for name, _ in parse_archive(Path("combicode.txt").read_bytes())
    )


def test_default_ignores_and_symlinks(run_cli):
    Path("main.py").write_text("pass")
    for name in ["node_modules/dependency.js", ".venv/dependency.py", "dist/bundle.js", ".env"]:
        target = Path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("excluded")
    Path("alias.py").symlink_to("main.py")
    run_cli("--no-gitignore")
    assert dict(parse_archive(Path("combicode.txt").read_bytes())) == {"main.py": b"pass"}


def test_special_tree_names_extension_normalization_and_output_parents(run_cli):
    for name in ["__proto__/main.PY", "__file/path/item.py", "constructor.py", "other.txt"]:
        target = Path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("pass")
    run_cli("-i", " PY, .py ", "-o", "generated/context.txt")
    names = dict(parse_archive(Path("generated/context.txt").read_bytes()))
    assert set(names) == {"__proto__/main.PY", "__file/path/item.py", "constructor.py"}


def test_failed_read_preserves_existing_output(run_cli):
    Path("invalid.txt").write_bytes(b"\xff\xfe")
    Path("combicode.txt").write_bytes(b"previous output")
    run_cli(success=False)
    assert Path("combicode.txt").read_bytes() == b"previous output"


def test_unknown_option_is_rejected(run_cli):
    run_cli("--not-an-option", success=False)


def test_ml_offsets_empty_skipped_and_crlf(run_cli):
    Path("a.txt").write_bytes(b"")
    Path("b.txt").write_bytes(b"one\r\ntwo\r\n")
    Path("c.txt").write_bytes(b"skipped")
    Path("d.txt").write_bytes(b"last")
    run_cli("--skip-content", "c.txt")
    data = Path("combicode.txt").read_bytes()
    lines = data.split(b"\n")
    headers = list(re.finditer(rb"# FILE: (\S+) \[OL: \d+-\d+ \| ML: (\d+)-(\d+) \|", data))
    assert len(headers) == 4
    for match in headers:
        start, end = int(match[2]), int(match[3])
        assert lines[start - 2] == b"````"
        assert lines[end] == b"````"
    assert dict(parse_archive(data)) == {"a.txt": b"", "b.txt": b"one\r\ntwo\r\n", "d.txt": b"last"}


def test_braces_in_comments_and_strings():
    content = 'function example() {\n  const brace = "}";\n  /* } */\n  // }\n  return brace;\n}\n'
    function = next(
        element
        for element in parse_code_structure("a.js", content)
        if element["label"].startswith("fn example")
    )
    assert function["end_line"] == 6
    assert function["size"] == len(content.encode())


def test_python_signature_preserves_all_parameters():
    content = "def example(a=1, /, *args: str, flag=True, **kwargs) -> None:\n    pass\n"
    element = parse_code_structure("a.py", content)[0]
    assert "a=1, /, *args: str, flag=True, **kwargs" in element["label"]
    assert element["end_line"] == 2
    assert element["size"] == len(content.encode())
