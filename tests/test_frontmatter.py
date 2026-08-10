import unittest

from textstrata import frontmatter


class FrontmatterTests(unittest.TestCase):
    def test_single_block(self):
        text = "---\nid: a\ntitle: A\n---\n\nbody here\n"
        fm = frontmatter.parse(text)
        self.assertEqual(fm.data["id"], "a")
        self.assertEqual(fm.body.strip(), "body here")
        self.assertEqual(fm.block_count, 1)
        self.assertFalse(fm.had_stacked_blocks)

    def test_stacked_blocks_are_merged_not_dropped(self):
        """The regression case: provenance block stacked above a semantic
        block. A naive parser keeps only the first and loses id/title/tags."""
        text = (
            "---\n"
            'created_via: "textstrata-mcp"\n'
            'authorship: "Codex"\n'
            "---\n"
            "---\n"
            "id: x.y\n"
            "title: Stacked\n"
            "tags: [a, b]\n"
            "---\n\n"
            "# Stacked\n\nbody\n"
        )
        fm = frontmatter.parse(text)
        self.assertTrue(fm.had_stacked_blocks)
        self.assertEqual(fm.block_count, 2)
        # Both blocks' keys survive.
        self.assertEqual(fm.data["created_via"], "textstrata-mcp")
        self.assertEqual(fm.data["authorship"], "Codex")
        self.assertEqual(fm.data["id"], "x.y")
        self.assertEqual(fm.data["title"], "Stacked")
        self.assertEqual(fm.data["tags"], ["a", "b"])
        self.assertEqual(fm.body.strip().splitlines()[0], "# Stacked")
        self.assertEqual(fm.conflicts, [])

    def test_list_keys_union_across_blocks(self):
        text = "---\ntags: [a, b]\n---\n---\ntags: [b, c]\n---\nbody\n"
        fm = frontmatter.parse(text)
        self.assertEqual(fm.data["tags"], ["a", "b", "c"])

    def test_scalar_conflict_keeps_first_and_records_it(self):
        text = "---\nid: first\n---\n---\nid: second\n---\nbody\n"
        fm = frontmatter.parse(text)
        self.assertEqual(fm.data["id"], "first")
        self.assertEqual(len(fm.conflicts), 1)
        self.assertIn("first", fm.conflicts[0])
        self.assertIn("second", fm.conflicts[0])

    def test_no_frontmatter(self):
        fm = frontmatter.parse("# just a heading\n\ntext\n")
        self.assertEqual(fm.data, {})
        self.assertEqual(fm.block_count, 0)
        self.assertTrue(fm.body.startswith("# just a heading"))

    def test_render_roundtrip(self):
        data = {"id": "a", "tags": ["x", "y"]}
        rendered = frontmatter.render(data, "the body")
        fm = frontmatter.parse(rendered)
        self.assertEqual(fm.data["id"], "a")
        self.assertEqual(fm.data["tags"], ["x", "y"])
        self.assertEqual(fm.body.strip(), "the body")

    def test_salvage_preserves_empty_and_continued_values(self):
        text = "---\ntitle: Project: Subtitle\nempty:\ndescription: first line\n  continued line\n---\nbody\n"
        fm = frontmatter.parse(text)
        self.assertEqual(fm.data["title"], "Project: Subtitle")
        self.assertIsNone(fm.data["empty"])
        self.assertEqual(fm.data["description"], "first line continued line")


if __name__ == "__main__":
    unittest.main()
