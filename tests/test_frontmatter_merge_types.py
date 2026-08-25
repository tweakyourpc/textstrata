"""Task 1 — ``_merge_into`` must never let block 2 change block 1's value.

``_merge_into`` tests the list branch before the scalar branch, so
``isinstance(existing, list) or isinstance(value, list)`` fires whenever *either*
side is a list. A scalar declared in block 1 is silently promoted to a list and
unioned with block 2's values, on any key including the protected ones. There is
no protected-key handling at all.

The invariant under test: for every type pairing and every protected key, the
value present after merging block 2 is exactly the value block 1 declared, and a
differing later value is recorded as a conflict rather than absorbed.
"""

import unittest

import yaml

from textstrata import frontmatter


# Contract §4 pins the conflict message so tests can assert on it:
#   "<key>: kept <earlier> from block <n>, rejected <later> in block <m>"
# §4 does not pin how the values themselves are formatted. These tests use
# repr() for values and a bare key, which is the smallest delta from the
# message the implementation already emits. If the director amends §4, this
# one helper is the only place that changes.
def conflict_message(key, earlier, later, earlier_block=1, later_block=2):
    return f"{key}: kept {earlier!r} from block {earlier_block}, rejected {later!r} in block {later_block}"


# Task 15's rejection cannot use the form above: a key being ESTABLISHED by a
# later block has no earlier value to keep. §5 pins this second form:
#   "<key>: cannot be established in block <m>; identity fixed by block <n>"
# Bare unquoted key, no repr(), since there is no value to render. Sibling to
# conflict_message() so both message shapes have exactly one definition.
def establishment_message(key, later_block, anchor_block=1):
    return f"{key}: cannot be established in block {later_block}; identity fixed by block {anchor_block}"


PROTECTED_KEYS = ("id", "type", "title", "handling", "preservation", "dependencies", "related")

# One representative value per YAML shape, in two distinguishable variants.
SHAPES = {
    "scalar": ("alpha", "beta"),
    "list": (["alpha"], ["beta"]),
    "mapping": ({"k": "alpha"}, {"k": "beta"}),
}

PAIRINGS = [(a, b) for a in SHAPES for b in SHAPES]


def two_blocks(key, first, second):
    """Render two stacked front-matter blocks declaring ``key`` in each."""
    a = yaml.safe_dump({key: first}, sort_keys=False).rstrip("\n")
    b = yaml.safe_dump({key: second}, sort_keys=False).rstrip("\n")
    return f"---\n{a}\n---\n---\n{b}\n---\n\nbody\n"


class ProtectedKeyMergeTests(unittest.TestCase):
    def test_block_two_never_changes_a_protected_key_from_block_one(self):
        for key in PROTECTED_KEYS:
            for first_shape, second_shape in PAIRINGS:
                first = SHAPES[first_shape][0]
                second = SHAPES[second_shape][1]
                with self.subTest(key=key, first=first_shape, second=second_shape):
                    fm = frontmatter.parse(two_blocks(key, first, second))
                    self.assertEqual(fm.block_count, 2)
                    self.assertEqual(
                        fm.data[key],
                        first,
                        f"{key}: block 2 ({second_shape}) altered block 1's {first_shape} value",
                    )

    def test_every_protected_key_mismatch_records_one_pinned_conflict(self):
        for key in PROTECTED_KEYS:
            for first_shape, second_shape in PAIRINGS:
                first = SHAPES[first_shape][0]
                second = SHAPES[second_shape][1]
                with self.subTest(key=key, first=first_shape, second=second_shape):
                    fm = frontmatter.parse(two_blocks(key, first, second))
                    self.assertEqual(
                        fm.conflicts,
                        [conflict_message(key, first, second)],
                        f"{key}: {first_shape} then {second_shape} did not record exactly one conflict",
                    )

    def test_protected_keys_declared_identically_are_not_a_conflict(self):
        for key in PROTECTED_KEYS:
            for shape in SHAPES:
                value = SHAPES[shape][0]
                with self.subTest(key=key, shape=shape):
                    fm = frontmatter.parse(two_blocks(key, value, value))
                    self.assertEqual(fm.data[key], value)
                    self.assertEqual(fm.conflicts, [])


class UnprotectedKeyMergeTests(unittest.TestCase):
    """Union stays available for unprotected keys, but only list-to-list."""

    def test_two_lists_still_union(self):
        fm = frontmatter.parse(two_blocks("tags", ["a", "b"], ["b", "c"]))
        self.assertEqual(fm.data["tags"], ["a", "b", "c"])
        self.assertEqual(fm.conflicts, [])

    def test_scalar_is_not_promoted_to_a_list_by_a_later_block(self):
        fm = frontmatter.parse(two_blocks("tags", "a", ["b", "c"]))
        self.assertEqual(fm.data["tags"], "a")
        self.assertEqual(fm.conflicts, [conflict_message("tags", "a", ["b", "c"])])

    def test_list_is_not_extended_by_a_later_scalar(self):
        fm = frontmatter.parse(two_blocks("tags", ["a"], "b"))
        self.assertEqual(fm.data["tags"], ["a"])
        self.assertEqual(fm.conflicts, [conflict_message("tags", ["a"], "b")])

    def test_unprotected_mappings_still_merge_shallowly(self):
        fm = frontmatter.parse(two_blocks("extra", {"a": 1}, {"b": 2}))
        self.assertEqual(fm.data["extra"], {"a": 1, "b": 2})
        self.assertEqual(fm.conflicts, [])

    def test_a_key_absent_from_block_one_is_still_added_by_block_two(self):
        # Migrated for Task 15. This asserted that block 2 could establish
        # ``title``, which is a protected key, so it contradicted the rule that
        # a later block may not establish one. The property under test is that
        # a later block can still ADD a key, so it now uses an unprotected one.
        text = "---\nid: note.x\n---\n---\ncreated_via: textstrata-mcp\n---\n\nbody\n"
        fm = frontmatter.parse(text)
        self.assertEqual(fm.data["id"], "note.x")
        self.assertEqual(fm.data["created_via"], "textstrata-mcp")
        self.assertEqual(fm.conflicts, [])


if __name__ == "__main__":
    unittest.main()
