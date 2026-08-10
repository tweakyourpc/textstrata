from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from textstrata.application.setup import initialize_workspace, setup_status
from textstrata.presentation import PAPER_SKIN, render_setup_html
from textstrata.workspace import resolve_workspace


class SetupUseCaseTests(unittest.TestCase):
    def test_workspace_alias_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(resolve_workspace(environ={"TEXTSTRATA_WORKSPACE": str(root / "textstrata")}), (root / "textstrata").resolve())
            self.assertEqual(resolve_workspace(environ={"FABRIC_ROOT": str(root / "legacy")}), (root / "legacy").resolve())
            self.assertEqual(resolve_workspace(environ={"FABRIC_ROOT": str(root / "legacy"), "MARKBASE_WORKSPACE": str(root / "canonical")}), (root / "canonical").resolve())

    def test_status_is_read_only_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = set(root.iterdir())
            first = setup_status(root)
            second = setup_status(root)
            self.assertFalse(first["initialized"])
            self.assertEqual(first, second)
            self.assertEqual(set(root.iterdir()), before)
            self.assertEqual([item["id"] for item in first["optional_capabilities"]], sorted(item["id"] for item in first["optional_capabilities"]))

    def test_initialize_is_idempotent_and_creates_no_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = initialize_workspace(root)
            second = initialize_workspace(root)
            self.assertTrue(first["initialized"])
            self.assertTrue(second["initialized"])
            self.assertTrue(first["created"])
            self.assertEqual(second["created"], [])
            self.assertEqual(list((root / "normalized").glob("*.md")), [])

    def test_setup_page_has_stable_contract_and_unique_ids(self):
        html = render_setup_html(setup_status(tempfile.mkdtemp()), PAPER_SKIN, version="test")
        self.assertIn("Setup &amp; capabilities", html)
        self.assertIn("/api/textstrata/setup/initialize", html)
        ids = re.findall(r'id="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
