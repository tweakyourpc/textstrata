"""Task 6 — one bad line must not change any other line's value.

Salvage is all-or-nothing today: a single unquoted colon anywhere in a block
sends the *whole* block down the salvage path, where every unbracketed value is
taken as a literal string and ``.strip("\\"'")`` removes every quote character at
either end. So ``count: 3`` becomes the string ``"3"``, ``flag: true`` becomes
``"true"``, and ``mixed: "He said 'hi'"`` is corrupted to ``He said 'hi`` --
none of which have anything to do with the line that actually failed.

Salvage must be line-oriented: ``yaml.safe_load`` per line, literal fallback only
for the lines that genuinely fail, and exactly one matched quote pair removed.
"""

import unittest

from textstrata import frontmatter


# The only genuinely malformed line in every block below.
BAD_LINE = "title: Project: Subtitle"


def salvaged(*lines):
    body = "\n".join((BAD_LINE,) + lines)
    return f"---\n{body}\n---\n\nbody\n"


class SalvageDoesNotSpreadTests(unittest.TestCase):
    def test_scalar_types_on_good_lines_survive_a_bad_neighbour(self):
        fm = frontmatter.parse(
            salvaged("count: 3", "ratio: 1.5", "flag: true", "nothing: null")
        )
        self.assertEqual(fm.data["count"], 3)
        self.assertEqual(fm.data["ratio"], 1.5)
        self.assertIs(fm.data["flag"], True)
        self.assertIsNone(fm.data["nothing"])

    def test_inline_list_on_a_good_line_survives(self):
        fm = frontmatter.parse(salvaged("tags: [alpha, beta]"))
        self.assertEqual(fm.data["tags"], ["alpha", "beta"])

    def test_inline_mapping_on_a_good_line_survives(self):
        fm = frontmatter.parse(salvaged("meta: {a: 1}"))
        self.assertEqual(fm.data["meta"], {"a": 1})

    def test_the_failing_line_alone_falls_back_to_a_literal(self):
        fm = frontmatter.parse(salvaged("count: 3"))
        self.assertEqual(fm.data["title"], "Project: Subtitle")
        self.assertEqual(fm.data["count"], 3)


class QuoteHandlingTests(unittest.TestCase):
    def test_inner_quotes_of_a_different_kind_are_preserved(self):
        fm = frontmatter.parse(salvaged("mixed: \"He said 'hi'\""))
        self.assertEqual(fm.data["mixed"], "He said 'hi'")

    def test_only_one_matched_quote_pair_is_removed(self):
        fm = frontmatter.parse(salvaged("weird: '\"quoted\"'"))
        self.assertEqual(fm.data["weird"], '"quoted"')

    def test_an_apostrophe_inside_an_unquoted_value_is_kept(self):
        fm = frontmatter.parse(salvaged("apostrophe: it's fine"))
        self.assertEqual(fm.data["apostrophe"], "it's fine")

    def test_an_unmatched_quote_on_a_failing_line_is_not_stripped(self):
        text = "---\nheading: Project: \"Subtitle\ncount: 3\n---\n\nbody\n"
        fm = frontmatter.parse(text)
        self.assertEqual(fm.data["heading"], 'Project: "Subtitle')
        self.assertEqual(fm.data["count"], 3)


class SalvagedValuesStayJsonSerializableTests(unittest.TestCase):
    """Line-oriented YAML introduces a hazard the all-literal path did not have.

    ``yaml.safe_load("updated: 2026-07-03")`` yields a ``datetime.date``, which
    ``json.dumps`` cannot encode. The seed corpus carries exactly this shape
    (an unquoted colon in the title forces salvage, and ``updated:`` sits in the
    same block), and items are serialized to JSON on the web and MCP paths. A
    date-looking value must come back as a string.
    """

    def test_date_shaped_values_remain_strings(self):
        fm = frontmatter.parse(salvaged("updated: 2026-07-03"))
        self.assertEqual(fm.data["updated"], "2026-07-03")
        self.assertIsInstance(fm.data["updated"], str)

    def test_timestamp_shaped_values_remain_strings(self):
        fm = frontmatter.parse(salvaged("stamp: 2026-07-03T10:11:12Z"))
        self.assertIsInstance(fm.data["stamp"], str)

    def test_every_salvaged_value_is_json_serializable(self):
        import json

        fm = frontmatter.parse(
            salvaged("count: 3", "updated: 2026-07-03", "tags: [a, b]", "flag: true")
        )
        json.dumps(fm.data)  # must not raise


if __name__ == "__main__":
    unittest.main()
