"""Task 5 — ``_salvage_block`` must retain block-style lists.

Salvage runs whenever a block fails strict YAML parsing, which real files trigger
routinely with an unquoted colon in a title. Inside salvage a bare ``tags:`` sets
the key to ``None``; the continuation guard then requires
``data.get(last_key) not in (None, "")``, so it refuses to attach anything; and
each following ``- item`` line fails ``_SIMPLE_KV_RE`` and is dropped on the
floor. A block-style list in a salvaged block therefore disappears entirely.
"""

import unittest

from textstrata import frontmatter


# An unquoted colon in the title makes the whole block invalid YAML, which is
# what forces the salvage path. Everything else here is ordinary front matter.
BAD_LINE = "title: Project: Subtitle"


def salvaged(*lines):
    body = "\n".join((BAD_LINE,) + lines)
    return f"---\n{body}\n---\n\nbody\n"


class SalvagedBlockListTests(unittest.TestCase):
    def test_block_style_list_survives_salvage(self):
        fm = frontmatter.parse(salvaged("tags:", "  - alpha", "  - beta"))
        self.assertEqual(fm.data["title"], "Project: Subtitle")
        self.assertEqual(fm.data["tags"], ["alpha", "beta"])

    def test_unindented_block_style_list_survives_salvage(self):
        fm = frontmatter.parse(salvaged("tags:", "- alpha", "- beta"))
        self.assertEqual(fm.data["tags"], ["alpha", "beta"])

    def test_list_items_are_not_leaked_into_a_neighbouring_key(self):
        fm = frontmatter.parse(
            salvaged("tags:", "  - alpha", "  - beta", "author: Ada")
        )
        self.assertEqual(fm.data["tags"], ["alpha", "beta"])
        self.assertEqual(fm.data["author"], "Ada")

    def test_two_block_style_lists_stay_separate(self):
        fm = frontmatter.parse(
            salvaged("tags:", "  - alpha", "aliases:", "  - Other Name")
        )
        self.assertEqual(fm.data["tags"], ["alpha"])
        self.assertEqual(fm.data["aliases"], ["Other Name"])

    def test_quoted_block_list_items_lose_exactly_one_quote_pair(self):
        fm = frontmatter.parse(salvaged("tags:", '  - "alpha"', "  - 'beta'"))
        self.assertEqual(fm.data["tags"], ["alpha", "beta"])

    def test_a_bare_key_with_no_list_under_it_is_still_none(self):
        fm = frontmatter.parse(salvaged("empty:", "author: Ada"))
        self.assertIsNone(fm.data["empty"])
        self.assertEqual(fm.data["author"], "Ada")

    def test_plain_continuation_lines_still_fold_into_the_previous_value(self):
        fm = frontmatter.parse(
            salvaged("description: first line", "  continued line")
        )
        self.assertEqual(fm.data["description"], "first line continued line")


if __name__ == "__main__":
    unittest.main()
