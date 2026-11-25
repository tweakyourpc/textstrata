"""Dated corpus audits and agent validation runs never reach a release snapshot."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PRIVATE = ("knowledge-audit-2026-10-03.md", "cold-agent-validation-2026-10-04.md")


class ReleasePrivateDocsTests(unittest.TestCase):
    def test_snapshot_leaves_out_private_docs(self) -> None:
        snapshot = _load("create_release_snapshot")
        with tempfile.TemporaryDirectory() as temp:
            source, dest = Path(temp) / "src", Path(temp) / "out"
            (source / "docs").mkdir(parents=True)
            for name in (*PRIVATE, "setup-capabilities.md"):
                (source / "docs" / name).write_text("# doc\n", encoding="utf-8")
            snapshot.create(source, dest)
            shipped = sorted(p.name for p in (dest / "docs").iterdir())
        self.assertEqual(shipped, ["setup-capabilities.md"])

    def test_audit_flags_private_docs_by_name(self) -> None:
        audit = _load("release_audit")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "docs").mkdir()
            for name in PRIVATE:
                (root / "docs" / name).write_text("# clean text\n", encoding="utf-8")
            findings = {f["path"]: f["reason"] for f in audit.audit(root, strict_source_only=True)}
        for name in PRIVATE:
            self.assertEqual(findings.get(f"docs/{name}"), "private agent-run document")


if __name__ == "__main__":
    unittest.main()
