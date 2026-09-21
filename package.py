#!/usr/bin/env python3
"""Validate and package the installable Decky plugin ZIP."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def fail(message: str) -> None:
    print(f"ERROR: {message}")
    raise SystemExit(1)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"cannot parse {path.name}: {exc}")


def validate() -> tuple[str, str]:
    plugin = load_json(ROOT / "plugin.json")
    package = load_json(ROOT / "package.json")

    name = plugin.get("name")
    version = package.get("version")
    if not name:
        fail("plugin.json: missing name")
    if not version:
        fail("package.json: missing version")

    if plugin.get("flags") != ["root"]:
        fail('plugin.json: flags must be ["root"]')

    if package.get("type") != "module":
        fail('package.json: type must be "module"')

    dist = ROOT / "dist" / "index.js"
    if not dist.is_file():
        fail("dist/index.js is missing; run npm run build first")

    text = dist.read_text(encoding="utf-8", errors="replace")
    if re.search(r'(^|[\s;])import\s+(?!\()', text):
        fail("dist/index.js contains a bare import statement")
    if "export" not in text:
        fail("dist/index.js does not look like an ESM bundle")

    # Keep the repository honest about the obsolete root flag spelling.
    for path in [ROOT / "plugin.json", ROOT / "README.md"]:
        if path.exists():
            data = path.read_text(encoding="utf-8", errors="replace")
            if '"_root"' in data and 'not' not in data.lower():
                fail(f"{path.name}: contains obsolete _root flag wording")

    print(f"OK: {name} v{version}")
    print("OK: root flag")
    print("OK: ESM package type")
    print(f"OK: {dist.relative_to(ROOT)}")
    return name, version


def package_release(name: str, version: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    out = ROOT / f"{safe_name}-v{version}.zip"

    # Match Decky ZIP installation expectations: one top-level plugin folder.
    with tempfile.TemporaryDirectory() as td:
        top = Path(td) / name
        top.mkdir()

        runtime_files = [
            "dist/index.js",
            "dist/index.js.map",
            "main.py",
            "plugin.json",
            "package.json",
            "README.md",
            "LICENSE",
        ]
        for rel in runtime_files:
            src = ROOT / rel
            if not src.exists():
                fail(f"release file missing: {rel}")
            dest = top / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

        if out.exists():
            out.unlink()

        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(top.rglob("*")):
                if file.is_file():
                    zf.write(file, file.relative_to(Path(td)))

    print(f"Created: {out.name}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate only")
    args = parser.parse_args()

    name, version = validate()
    if not args.check:
        package_release(name, version)


if __name__ == "__main__":
    main()
