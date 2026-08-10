#!/usr/bin/env python3
"""Create a sanitized source snapshot from an existing checkout."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOTS = ("src", "tests", "docs", "seed", "scripts", "config")
FILES = ("pyproject.toml", "README.md", "LICENSE", "Dockerfile", "docker-compose.yml", ".quality-gate", ".dockerignore", ".gitignore")


def create(source: Path, destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    for name in ROOTS:
        source_path = source / name
        if source_path.is_dir():
            shutil.copytree(
                source_path,
                destination / name,
                ignore=shutil.ignore_patterns(
                    "__pycache__",
                    ".pytest_cache",
                    ".mypy_cache",
                    "*.pyc",
                    "*.orig",
                    "*.rej",
                    "*~",
                    "textstrata-readme-hero-v*.png",
                ),
            )
    for name in FILES:
        source_path = source / name
        if source_path.is_file():
            shutil.copy2(source_path, destination / name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    create(args.source.resolve(), args.destination.resolve())
    print(f"release snapshot: {args.destination.resolve()}")
    print("next: python scripts/release_audit.py --root <snapshot> --strict-source-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
