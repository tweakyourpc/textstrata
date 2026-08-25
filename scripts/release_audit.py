#!/usr/bin/env python3
"""Audit a checkout or sanitized source snapshot before private GitHub release."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

EXCLUDED_PARTS = {".workspace", "textstrata-store", ".codex", ".worktrees", ".venv", "__pycache__", ".git"}
PRIVATE_NAMES = {"AGENTS.md", "MASTER_PROMPT.md", "textstrata-server.service"}
# Agent-run documents under docs/: the contract, its ledgers, per-task rulings, blast-radius
# audits and inter-agent handoffs. Named families rather than fixed names because rulings,
# ledgers and handoffs are added as the run proceeds, and a fixed list goes stale silently.
# baseline-summary.md is a standing exception: a one-off review document that belongs to no
# family, so no glob can reach it. It was caught only incidentally while it still carried
# /home paths, and began passing both gates silently once those were cleaned.
# scripts/create_release_snapshot.py carries the same families; keep the two in step.
PRIVATE_NAME_PATTERNS = (
    re.compile(r"\AAGENT-CONTRACT\.md\Z"),
    re.compile(r"\A(?:hardening-)?ledger[a-z0-9_.-]*\.md\Z"),
    re.compile(r"\Aruling-[a-z0-9_.-]+\.md\Z"),
    re.compile(r"\Ahandoff-[a-z0-9_.-]+\.md\Z"),
    re.compile(r"\Atask\d+[a-z]?-blast-radius\.md\Z"),
    re.compile(r"\Abaseline-summary\.md\Z"),
)
PRIVATE_PATTERNS = (
    re.compile(r"/home/[A-Za-z0-9_.-]+/"),
    re.compile(r"\b192\.168\.(?:\d{1,3}\.){1}\d{1,3}\b"),
)
SOURCE_ROOTS = {"src", "tests", "docs", "seed", "scripts", "config"}
SOURCE_FILES = {"README.md", "LICENSE", "pyproject.toml", "Dockerfile", "docker-compose.yml", ".quality-gate", ".dockerignore", ".gitignore"}


def candidate_paths(root: Path) -> list[Path]:
    if (root / ".git").exists():
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root, check=True, capture_output=True, text=True,
        )
        return [root / line for line in result.stdout.splitlines() if line]
    return [path for path in root.rglob("*") if not any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts)]


def audit(root: Path, *, strict_source_only: bool = False) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in candidate_paths(root):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            findings.append({"path": str(relative), "reason": "private or generated directory"})
            continue
        if strict_source_only and not (relative.parts and relative.parts[0] in SOURCE_ROOTS) and str(relative) not in SOURCE_FILES:
            findings.append({"path": str(relative), "reason": "outside strict source-only release boundary"})
            continue
        if path.name in PRIVATE_NAMES:
            findings.append({"path": str(relative), "reason": "machine-specific instruction/deployment file"})
            continue
        if any(pattern.match(path.name) for pattern in PRIVATE_NAME_PATTERNS):
            findings.append({"path": str(relative), "reason": "private agent-run document"})
            continue
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if relative.parts and relative.parts[0] == "tests":
            continue
        for pattern in PRIVATE_PATTERNS:
            if pattern.search(text):
                findings.append({"path": str(relative), "reason": f"matches private pattern: {pattern.pattern}"})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--strict-source-only", action="store_true", help="allow only the sanitized release boundary")
    args = parser.parse_args()
    findings = audit(args.root.resolve(), strict_source_only=args.strict_source_only)
    if not findings:
        print("release audit: clean")
        return 0
    print(f"release audit: {len(findings)} review item(s)")
    for finding in findings:
        print(f"  REVIEW {finding['path']}: {finding['reason']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
