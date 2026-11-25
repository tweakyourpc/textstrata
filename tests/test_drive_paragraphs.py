"""Google Docs imports keep one paragraph per line; other notes keep Markdown paragraphs."""

from __future__ import annotations

import unittest

from textstrata.presentation.markdown import markdown_to_html

DOC = "Goal\nRemove the repetitive workflow.\n\n## Version 1\nAccept one file.\n- a bullet\n- another\nLast line."


class DriveParagraphTests(unittest.TestCase):
    def test_default_markdown_merges_soft_line_breaks(self) -> None:
        html = markdown_to_html("one\ntwo\n\nthree")
        self.assertEqual(html, "<p>one two</p>\n<p>three</p>")

    def test_line_paragraphs_keeps_each_doc_paragraph(self) -> None:
        html = markdown_to_html(DOC, line_paragraphs=True)
        self.assertIn("<p>Goal</p>", html)
        self.assertIn("<p>Remove the repetitive workflow.</p>", html)
        self.assertIn("<h2", html)
        self.assertIn("<p>Accept one file.</p>", html)
        self.assertIn("<ul><li>a bullet</li><li>another</li></ul>", html)
        self.assertIn("<p>Last line.</p>", html)

    def test_code_blocks_are_unchanged_in_line_mode(self) -> None:
        src = "```\nline one\nline two\n```"
        self.assertEqual(markdown_to_html(src, line_paragraphs=True), markdown_to_html(src))


if __name__ == "__main__":
    unittest.main()
