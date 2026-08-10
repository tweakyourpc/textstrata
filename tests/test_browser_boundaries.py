import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from textstrata.presentation.browser_common import browser_common_script
from textstrata.presentation.library_client import library_page_script
from textstrata.presentation.library_navigation import library_navigation_script
from textstrata.presentation.library_operations import library_operations_script
from textstrata.presentation.library_preferences import library_preferences_script
from textstrata.presentation.library_review import library_review_script
from textstrata.presentation.new_note_client import new_note_page_script


class BrowserBoundaryTests(unittest.TestCase):
    def test_shared_primitives_are_composed_once_per_page(self):
        common = browser_common_script()
        library = library_page_script()
        new_note = new_note_page_script()
        self.assertIn("async function api(url", common)
        self.assertIn("async function api(url", library)
        self.assertIn("async function api(url", new_note)
        self.assertEqual(library.count("async function api(url"), 1)
        self.assertEqual(new_note.count("async function api(url"), 1)
        self.assertIn("createConfirmationController", library)
        self.assertEqual(library.count("function bindDialogDismissals()"), 1)

    def test_library_navigation_has_one_owner(self):
        script = library_navigation_script()
        self.assertIn("function filterEntries()", script)
        self.assertIn('data-workspace-view="${activeView}"', script)
        self.assertNotIn("createConfirmationController", script)
        self.assertEqual(script.count("function filterEntries()"), 1)

    def test_library_operations_has_one_owner(self):
        fragment = library_operations_script()
        library = library_page_script()
        self.assertIn("async function openImportHistory()", fragment)
        self.assertIn("async function openMaintenanceDialog()", fragment)
        self.assertIn("async function openAboutDialog(mode)", fragment)
        self.assertNotIn("async function loadSettings()", fragment)
        self.assertEqual(library.count("async function openImportHistory()"), 1)

    def test_library_preferences_has_one_owner(self):
        fragment = library_preferences_script()
        library = library_page_script()
        self.assertIn("async function loadSettings()", fragment)
        self.assertIn("/api/textstrata/settings", fragment)
        self.assertNotIn("openReviewDialog", fragment)
        self.assertEqual(library.count("async function loadSettings()"), 1)

    def test_library_review_has_one_owner(self):
        fragment = library_review_script()
        library = library_page_script()
        self.assertIn("async function openReviewDialog()", fragment)
        self.assertIn("/api/textstrata/review/confirm", fragment)
        self.assertNotIn("function filterEntries()", fragment)
        self.assertEqual(library.count("async function openReviewDialog()"), 1)

    def test_composed_scripts_pass_node_check(self):
        for index, html in enumerate((library_page_script(), new_note_page_script())):
            scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
            self.assertEqual(len(scripts), 1)
            path = Path(tempfile.mkdtemp()) / f"browser-{index}.js"
            path.write_text(scripts[0], encoding="utf-8")
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
