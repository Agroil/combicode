#!/usr/bin/env python3
"""Update both package versions and the npm lockfile from any working directory."""

import argparse
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Stable version in MAJOR.MINOR.PATCH form")
    args = parser.parse_args()
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", args.version):
        parser.error("version must be a stable MAJOR.MINOR.PATCH version")

    root = Path(__file__).resolve().parent.parent
    updates = {}
    for name in ["package.json", "package-lock.json"]:
        target = root / "combicode-js" / name
        data = json.loads(target.read_text(encoding="utf-8"))
        data["version"] = args.version
        if name == "package-lock.json":
            data["packages"][""]["version"] = args.version
        updates[target] = json.dumps(data, indent=2) + "\n"
    updates[root / "combicode-py/combicode/__init__.py"] = f'__version__ = "{args.version}"\n'
    for target, content in updates.items():
        target.write_text(content, encoding="utf-8")
    print(
        f"Updated package versions to {args.version}. Review changelogs and the diff before release."
    )


if __name__ == "__main__":
    main()
