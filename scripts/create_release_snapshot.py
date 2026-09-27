#!/usr/bin/env python3
"""Create a sanitized source snapshot from an existing checkout."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOTS = ("src", "tests", "docs", "seed", "scripts", "config")
# Agent-run documents that live under docs/ but are private operating instructions, not
# product documentation: the agent contract, its ledgers, per-task rulings, blast-radius
# audits and inter-agent handoffs. Globs rather than fixed names because rulings, ledgers
# and handoffs accumulate as a run proceeds. scripts/release_audit.py enforces the same
# families as a backstop; keep the two in step.
# Standalone private review logs are fixed-name exceptions to the glob-family rule:
# they do not belong to an accumulating family, so no glob reaches them.
PRIVATE_DOCS = (
    "AGENT-CONTRACT.md",
    "ledger*.md",
    "hardening-ledger*.md",
    "ruling-*.md",
    "handoff-*.md",
    "task*-blast-radius.md",
    "baseline-summary.md",
    "security-review-gate2.md",
)
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
                    *PRIVATE_DOCS,
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
