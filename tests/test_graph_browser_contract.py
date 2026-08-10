from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from textstrata.presentation.pages.graph import render_graph_html
from textstrata.presentation.skin import CONSOLE_SKIN, PAPER_SKIN


class GraphBrowserContractTests(unittest.TestCase):
    def test_graph_uses_the_active_textstrata_skin(self):
        paper = render_graph_html(PAPER_SKIN)
        console = render_graph_html(CONSOLE_SKIN)
        self.assertIn("--bg:#f4f5f6", paper)
        self.assertIn("--bg:#09111f", console)
        self.assertNotIn("background:#1a1a2e", paper)

    def test_inline_graph_script_parses_and_uses_d3_v7_events(self):
        html = render_graph_html()
        scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
        self.assertEqual(len(scripts), 1)

        path = Path(tempfile.mkdtemp()) / "graph.js"
        path.write_text(scripts[0], encoding="utf-8")
        result = subprocess.run(
            ["node", "--check", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("d3.event", scripts[0])
        self.assertIn("showLinks=this.classList.toggle('active')", scripts[0])
        self.assertIn("showSim=this.classList.toggle('active')", scripts[0])


if __name__ == "__main__":
    unittest.main()
