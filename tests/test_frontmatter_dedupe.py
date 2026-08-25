"""Task 3 — merge dedupe and validate() must agree on case.

``_merge_into`` dedupes list members with ``candidate not in merged``, which is
case-sensitive. ``validate()`` treats ``tags`` and ``aliases`` duplicates
case-insensitively and as a *hard error*. So two individually valid blocks, each
publishable on its own, merge into an item that cannot be published at all.

One shared field-aware helper is required: ``tags``/``aliases`` dedupe
case-insensitively preserving the first casing seen, ``related``/``dependencies``
stay case-sensitive because ids are case-sensitive.
"""

import tempfile
import unittest

from textstrata import frontmatter
from textstrata.ingest import build_item, ingest_text
from textstrata.store import TextStrataStore
from textstrata.validate import validate


def stacked(field, first, second, item_id="note.dedupe"):
    """Two stacked blocks, each declaring ``field``; block 1 also carries identity."""
    return (
        f"---\nid: {item_id}\ntitle: Dedupe\ntype: note\n{field}: {first}\n---\n"
        f"---\n{field}: {second}\n---\n\nbody\n"
    )


class CaseInsensitiveFieldsTests(unittest.TestCase):
    """tags and aliases: fold case, keep the first casing seen."""

    def test_tags_differing_only_in_case_collapse_to_the_first_casing(self):
        fm = frontmatter.parse(stacked("tags", "[Security]", "[security]"))
        self.assertEqual(fm.data["tags"], ["Security"])

    def test_aliases_differing_only_in_case_collapse_to_the_first_casing(self):
        fm = frontmatter.parse(stacked("aliases", "[Foo]", "[foo]"))
        self.assertEqual(fm.data["aliases"], ["Foo"])

    def test_case_folding_is_not_lowercasing(self):
        fm = frontmatter.parse(stacked("tags", "[MachineLearning]", "[machinelearning]"))
        self.assertEqual(fm.data["tags"], ["MachineLearning"])

    def test_genuinely_distinct_tags_are_all_kept(self):
        fm = frontmatter.parse(stacked("tags", "[alpha]", "[beta, Gamma]"))
        self.assertEqual(fm.data["tags"], ["alpha", "beta", "Gamma"])


def single(field, value, item_id="note.dedupe"):
    """One block declaring ``field``, so the dedupe helper is the only actor.

    Cross-block behaviour for ``related``/``dependencies`` is settled by Task 1:
    they are protected keys, so a second block never merges into them at all.
    Their case-sensitivity is therefore a property of one declared list.
    """
    return f"---\nid: {item_id}\ntitle: Dedupe\ntype: note\n{field}: {value}\n---\n\nbody\n"


class CaseSensitiveFieldsTests(unittest.TestCase):
    """related and dependencies point at ids, which are case-sensitive."""

    def test_related_ids_differing_in_case_are_kept_separate(self):
        item, _, _ = build_item(single("related", "[Note.A, note.a]"))
        self.assertEqual(item.related, ["Note.A", "note.a"])
        self.assertTrue(validate(item).ok, validate(item).errors)

    def test_dependencies_ids_differing_in_case_are_kept_separate(self):
        item, _, _ = build_item(single("dependencies", "[Note.B, note.b]"))
        self.assertEqual(item.dependencies, ["Note.B", "note.b"])
        self.assertTrue(validate(item).ok, validate(item).errors)

    def test_exact_duplicate_ids_within_one_list_still_collapse(self):
        item, _, _ = build_item(single("related", "[Note.A, Note.A]"))
        self.assertEqual(item.related, ["Note.A"])
        self.assertTrue(validate(item).ok, validate(item).errors)


class SingleBlockCaseFoldingTests(unittest.TestCase):
    """The same helper runs whether or not a merge happened."""

    def test_tags_differing_in_case_within_one_block_collapse(self):
        item, _, _ = build_item(single("tags", "[Security, security]"))
        self.assertEqual(item.tags[:1], ["Security"])
        self.assertNotIn("security", item.tags)
        self.assertTrue(validate(item).ok, validate(item).errors)

    def test_aliases_differing_in_case_within_one_block_collapse(self):
        item, _, _ = build_item(single("aliases", "[Foo, foo]"))
        self.assertEqual(item.aliases, ["Foo"])
        self.assertTrue(validate(item).ok, validate(item).errors)


class MergeAndValidateAgreeTests(unittest.TestCase):
    def test_two_valid_blocks_cannot_merge_into_an_unpublishable_item(self):
        for field, first, second in (
            ("tags", "[Security]", "[security]"),
            ("aliases", "[Foo]", "[foo]"),
        ):
            with self.subTest(field=field):
                text = stacked(field, first, second, item_id=f"note.{field}")
                item, _, _ = build_item(text)
                result = validate(item)
                self.assertTrue(
                    result.ok,
                    f"{field}: merge produced duplicates that validate() rejects: {result.errors}",
                )

    def test_stacked_case_variant_tags_still_publish(self):
        store = TextStrataStore(tempfile.mkdtemp())
        result = ingest_text(store, stacked("tags", "[Security]", "[security]"))
        self.assertTrue(result.published, result.validation.errors)
        self.assertEqual(result.item.tags[:1], ["Security"])


if __name__ == "__main__":
    unittest.main()


class ComparisonMatchesItemNormalizationTests(unittest.TestCase):
    """Verification gap found while checking Task 3 (§6).

    ``list_value_comparison_key`` compares the values exactly as YAML parsed
    them. ``ingest._as_list`` later runs every member through
    ``str(value).strip()`` before it reaches the item. So two members that the
    dedupe helper considers distinct can become identical on the item, and
    ``validate()`` then rejects the result as a duplicate.

    That is the same failure Task 3 exists to prevent -- two individually valid
    blocks merging into an item that cannot be published -- reached through
    normalization rather than through case. It also fires within a single block,
    so it is not specific to merging.

    The comparison key has to normalize the way the item does: coerce to string
    and strip, then fold case for ``tags`` and ``aliases`` only.
    """

    HEAD = "id: note.norm\ntitle: Norm\ntype: note\n"

    # (field, first value, second value) -- equal once _as_list normalizes them
    VARIANTS = (
        ("tags", "'a '", "a"),
        ("tags", "1", "'1'"),
        ("aliases", "'Foo '", "Foo"),
        ("related", "'note.a '", "note.a"),
        ("dependencies", "'note.b '", "note.b"),
    )

    # Only the unprotected fields can reach this through a merge at all: Task 1
    # rejects a second block's related/dependencies outright, so the two-block
    # form of those would pass without exercising the comparison helper.
    MERGEABLE = tuple(v for v in VARIANTS if v[0] in ("tags", "aliases"))

    def one_block(self, field, first, second):
        return f"---\n{self.HEAD}{field}: [{first}, {second}]\n---\n\nbody\n"

    def two_blocks(self, field, first, second):
        return (
            f"---\n{self.HEAD}{field}: [{first}]\n---\n"
            f"---\n{field}: [{second}]\n---\n\nbody\n"
        )

    def test_one_block_cannot_produce_an_unpublishable_item(self):
        for field, first, second in self.VARIANTS:
            with self.subTest(field=field, values=(first, second)):
                item, _, _ = build_item(self.one_block(field, first, second))
                result = validate(item)
                self.assertTrue(
                    result.ok,
                    f"{field} {first}/{second} survived dedupe but validate() rejects it: {result.errors}",
                )

    def test_two_valid_blocks_cannot_produce_an_unpublishable_item(self):
        for field, first, second in self.MERGEABLE:
            with self.subTest(field=field, values=(first, second)):
                item, _, _ = build_item(self.two_blocks(field, first, second))
                result = validate(item)
                self.assertTrue(
                    result.ok,
                    f"{field} {first}/{second} survived dedupe but validate() rejects it: {result.errors}",
                )

    def test_the_first_spelling_is_the_one_kept(self):
        item, _, _ = build_item(self.two_blocks("tags", "'a '", "a"))
        self.assertEqual(item.tags[:1], ["a"])

    def test_genuinely_distinct_ids_are_still_distinct_after_normalizing(self):
        item, _, _ = build_item(self.one_block("related", "Note.A", "note.a"))
        self.assertEqual(item.related, ["Note.A", "note.a"])
        self.assertTrue(validate(item).ok, validate(item).errors)
