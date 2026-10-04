import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReleaseSnapshotPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="textstrata-release-policy-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def make_source(self) -> Path:
        source = self.tmp / "source"
        (source / "docs").mkdir(parents=True)
        (source / "docs" / "security-review-gate2.md").write_text(
            "private agent review notes\n",
            encoding="utf-8",
        )
        for name in ("cold-agent-validation-2026-10-04.md", "knowledge-audit-2026-10-03.md"):
            (source / "docs" / name).write_text("private /home/operator/ workspace evidence\n", encoding="utf-8")
        (source / "docs" / "security-multi-user-implementation.md").write_text(
            "public architecture notes\n",
            encoding="utf-8",
        )
        (source / "src").mkdir()
        (source / "src" / "placeholder.py").write_text("VALUE = 1\n", encoding="utf-8")
        return source

    def test_snapshot_excludes_private_security_review_log(self):
        create_release_snapshot = load_script("create_release_snapshot")
        source = self.make_source()
        snapshot = self.tmp / "snapshot"

        create_release_snapshot.create(source, snapshot)

        self.assertFalse((snapshot / "docs" / "security-review-gate2.md").exists())
        self.assertFalse((snapshot / "docs" / "cold-agent-validation-2026-10-04.md").exists())
        self.assertFalse((snapshot / "docs" / "knowledge-audit-2026-10-03.md").exists())
        self.assertTrue((snapshot / "docs" / "security-multi-user-implementation.md").exists())

    def test_release_audit_rejects_private_security_review_log(self):
        release_audit = load_script("release_audit")
        source = self.make_source()

        findings = release_audit.audit(source, strict_source_only=True)

        self.assertIn(
            {
                "path": "docs/security-review-gate2.md",
                "reason": "private agent-run document",
            },
            findings,
        )
        for name in ("cold-agent-validation-2026-10-04.md", "knowledge-audit-2026-10-03.md"):
            self.assertIn({"path": f"docs/{name}", "reason": "private agent-run document"}, findings)


if __name__ == "__main__":
    unittest.main()
