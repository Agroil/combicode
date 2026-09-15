import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_script_validates_before_writing_and_updates_lock(tmp_path):
    for name in [
        "scripts/prepare-release.py",
        "combicode-js/package.json",
        "combicode-js/package-lock.json",
        "combicode-py/combicode/__init__.py",
    ]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    package = tmp_path / "combicode-js/package.json"
    previous = package.read_bytes()
    script = tmp_path / "scripts/prepare-release.py"
    for version in ["1.2", "01.2.3", "1.2.3;echo bad"]:
        result = subprocess.run([sys.executable, str(script), version], capture_output=True)
        assert result.returncode != 0
        assert package.read_bytes() == previous
    result = subprocess.run(
        [sys.executable, str(script), "3.0.0"], cwd=tmp_path.parent, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(package.read_text())["version"] == "3.0.0"
    lock = json.loads((tmp_path / "combicode-js/package-lock.json").read_text())
    assert lock["version"] == lock["packages"][""]["version"] == "3.0.0"
    assert "3.0.0" in (tmp_path / "combicode-py/combicode/__init__.py").read_text()


def test_packaged_ignore_rules_match_canonical_source():
    expected = (ROOT / "configs/ignore.json").read_bytes()
    assert (ROOT / "combicode-js/ignore.json").read_bytes() == expected
    assert (ROOT / "combicode-py/combicode/ignore.json").read_bytes() == expected
