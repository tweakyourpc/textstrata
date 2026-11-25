"""The About and System info menu items on note pages use the one working dialog function."""

from __future__ import annotations

import re
import unittest

from textstrata.presentation.client_scripts import item_page_script


class ItemAboutDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = item_page_script("example.note")

    def test_menu_actions_call_open_about_dialog(self) -> None:
        self.assertIn("async function openAboutDialog(mode)", self.script)
        self.assertIn("openAboutDialog(a)", self.script)

    def test_path_table_is_only_used_where_it_is_defined(self) -> None:
        start = self.script.index("async function openAboutDialog(mode)")
        end = self.script.index("d.showModal();", start)
        body = self.script[start:end]
        outside = self.script[:start] + self.script[end:]
        self.assertIn("var pathTable=", body)
        self.assertIsNone(re.search(r"\bpathTable\b", outside))


if __name__ == "__main__":
    unittest.main()
