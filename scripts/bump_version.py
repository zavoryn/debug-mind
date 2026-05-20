"""Bump the project version in pyproject.toml and src/debug_mind/__init__.py.

Usage:
    python scripts/bump_version.py 0.2.0
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def bump(version: str) -> None:
    # pyproject.toml
    pyproject = ROOT / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    for line in content.splitlines():
        if line.startswith("version ="):
            old = line.split("=")[1].strip().strip('"')
            break
    content = content.replace(f'version = "{old}"', f'version = "{version}"')
    pyproject.write_text(content, encoding="utf-8")

    # __init__.py
    init_file = ROOT / "src" / "debug_mind" / "__init__.py"
    content = init_file.read_text(encoding="utf-8")
    content = content.replace(f'__version__ = "{old}"', f'__version__ = "{version}"')
    init_file.write_text(content, encoding="utf-8")

    print(f"Bumped {old} → {version}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/bump_version.py <new-version>")
        sys.exit(1)
    bump(sys.argv[1])
