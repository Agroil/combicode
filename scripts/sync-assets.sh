#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
for package in combicode-js combicode-py; do
    cp "$repo_root/README.md" "$repo_root/$package/README.md"
    cp "$repo_root/LICENSE" "$repo_root/$package/LICENSE"
done
cp "$repo_root/configs/ignore.json" "$repo_root/combicode-js/ignore.json"
cp "$repo_root/configs/ignore.json" "$repo_root/combicode-py/combicode/ignore.json"
