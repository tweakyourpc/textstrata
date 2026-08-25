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
        # Contract §4 pins this message; see tests/test_frontmatter_merge_types.py
        # for the shared helper that builds it.
        self.assertEqual(
            fm.conflicts,
            ["id: kept 'first' from block 1, rejected 'second' in block 2"],
        )

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



class LeadingBlockToleratesAByteOrderMarkTests(unittest.TestCase):
    """A leading BOM must not stop the first block being recognised.

    This is a load-bearing dependency that was undeclared until now. The web
    item-save route sends BOM-prefixed documents down the whole-document branch
    and passes the text to ``build_item`` with the BOM still attached; nothing
    strips it first. It works only because ``_LEADING_BLOCK_RE`` carries an
    optional BOM in its prefix.

    If that tolerance is removed, the first key parses mangled, the id
    slugifies to something else, and a web save creates a sibling item instead
    of overwriting its target. The web tests in
    tests/test_original_bytes.py::WebSaveOriginalBytesTests catch that, but only
    through their identity assertions, and only for one route. The property
    belongs here, next to the parser that provides it.
    """

    BOM = "﻿"
    DOCUMENT = "---\nid: note.bom\ntitle: BOM\ntype: note\ntags: [x]\n---\n\nbody text\n"

    def test_a_leading_bom_still_yields_one_block(self):
        fm = frontmatter.parse(self.BOM + self.DOCUMENT)
        self.assertEqual(fm.block_count, 1)

    def test_a_leading_bom_does_not_corrupt_the_first_key(self):
        fm = frontmatter.parse(self.BOM + self.DOCUMENT)
        self.assertEqual(fm.data["id"], "note.bom")
        self.assertNotIn("﻿", "".join(str(key) for key in fm.data))

    def test_a_bom_prefixed_document_parses_identically_to_one_without(self):
        with_bom = frontmatter.parse(self.BOM + self.DOCUMENT)
        without = frontmatter.parse(self.DOCUMENT)
        self.assertEqual(with_bom.data, without.data)
        self.assertEqual(with_bom.body, without.body)
        self.assertEqual(with_bom.block_count, without.block_count)

    def test_a_bom_prefixed_document_is_not_rejected_as_prose(self):
        fm = frontmatter.parse(self.BOM + self.DOCUMENT)
        self.assertEqual(fm.warnings, [])

    def test_a_bom_before_stacked_blocks_still_merges_both(self):
        stacked = (
            "---\ncreated_via: textstrata-mcp\n---\n"
            "---\nid: note.bom\ntitle: BOM\ntype: note\n---\n\nbody\n"
        )
        fm = frontmatter.parse(self.BOM + stacked)
        self.assertEqual(fm.block_count, 2)
        self.assertEqual(fm.data["created_via"], "textstrata-mcp")
        self.assertEqual(fm.data["id"], "note.bom")


if __name__ == "__main__":
    unittest.main()
