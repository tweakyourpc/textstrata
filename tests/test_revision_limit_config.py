"""Task 13 — revision-limit configuration must be named, validated, and warned about.

``TextStrataStore.__init__`` reads only ``FABRIC_REVISION_LIMIT`` and passes it
straight to ``int()``. Three problems follow. The environment variable still
carries the old product name with no TextStrata equivalent. A non-integer value
raises ``ValueError`` out of the constructor, so a typo in a shell profile makes
the store unconstructable rather than falling back. And a value outside 1-3 is
silently clamped, so an operator who sets 10 is never told they did not get it.

The ``.fabric`` directory name is a compatibility boundary and stays as it is;
this task is only about the environment variable.

Warnings are asserted through the ``warnings`` module, which is the standard
testable mechanism for exactly this (deprecation plus invalid configuration).
"""

import os
import tempfile
import unittest
import warnings
from unittest.mock import patch

from textstrata.store import TextStrataStore


TEXTSTRATA = "TEXTSTRATA_REVISION_LIMIT"
LEGACY = "FABRIC_REVISION_LIMIT"


def store_with(env):
    """Build a store with exactly ``env`` set; both names are otherwise unset.

    ``env`` values are used verbatim, including the empty string, which is a
    real thing to find in a shell profile.
    """
    with patch.dict(os.environ, dict(env), clear=False):
        for name in (TEXTSTRATA, LEGACY):
            if name not in env:
                os.environ.pop(name, None)
        return TextStrataStore(tempfile.mkdtemp())


class PreferredNameTests(unittest.TestCase):
    def test_textstrata_name_is_read(self):
        self.assertEqual(store_with({TEXTSTRATA: "2"}).revision_limit, 2)

    def test_textstrata_name_emits_no_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            store_with({TEXTSTRATA: "2"})
        self.assertEqual([str(w.message) for w in caught], [])

    def test_no_configuration_defaults_to_three_silently(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            store = store_with({})
        self.assertEqual(store.revision_limit, 3)
        self.assertEqual([str(w.message) for w in caught], [])

    def test_an_explicit_argument_still_wins_over_the_environment(self):
        with patch.dict(os.environ, {TEXTSTRATA: "1"}):
            self.assertEqual(TextStrataStore(tempfile.mkdtemp(), revision_limit=2).revision_limit, 2)


class DeprecatedNameTests(unittest.TestCase):
    def test_the_legacy_name_still_works(self):
        self.assertEqual(store_with({LEGACY: "2"}).revision_limit, 2)

    def test_the_legacy_name_warns(self):
        with self.assertWarns(DeprecationWarning):
            store_with({LEGACY: "2"})

    def test_the_deprecation_warning_names_both_variables(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            store_with({LEGACY: "2"})
        messages = " ".join(str(w.message) for w in caught)
        self.assertIn(LEGACY, messages)
        self.assertIn(TEXTSTRATA, messages)

    def test_textstrata_wins_when_both_are_set(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.assertEqual(store_with({TEXTSTRATA: "1", LEGACY: "3"}).revision_limit, 1)

    def test_the_legacy_name_does_not_warn_when_the_preferred_one_is_set(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            store_with({TEXTSTRATA: "1", LEGACY: "3"})
        self.assertEqual([str(w.message) for w in caught], [])


class InvalidValueTests(unittest.TestCase):
    """Run against both names: today only the legacy one is read at all, so
    testing the preferred name alone would pass for the wrong reason."""

    NAMES = (TEXTSTRATA, LEGACY)

    def test_a_non_integer_does_not_raise(self):
        for name in self.NAMES:
            for value in ("banana", "", "2.5", "three", " "):
                with self.subTest(name=name, value=value):
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        self.assertEqual(store_with({name: value}).revision_limit, 3)

    def test_a_non_integer_warns(self):
        for name in self.NAMES:
            with self.subTest(name=name):
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    store_with({name: "banana"})
                self.assertTrue(
                    any(issubclass(w.category, UserWarning) for w in caught),
                    f"{name}=banana produced no invalid-value warning",
                )

    def test_the_invalid_value_warning_quotes_the_value(self):
        for name in self.NAMES:
            with self.subTest(name=name):
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    store_with({name: "banana"})
                self.assertIn("banana", " ".join(str(w.message) for w in caught))


class ClampingTests(unittest.TestCase):
    def test_values_inside_the_range_are_untouched_and_silent(self):
        for value in ("1", "2", "3"):
            with self.subTest(value=value):
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    store = store_with({TEXTSTRATA: value})
                self.assertEqual(store.revision_limit, int(value))
                self.assertEqual([str(w.message) for w in caught], [])

    def test_a_value_above_the_range_clamps_and_warns(self):
        for name in (TEXTSTRATA, LEGACY):
            with self.subTest(name=name):
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    store = store_with({name: "10"})
                self.assertEqual(store.revision_limit, 3)
                self.assertTrue(
                    any(issubclass(w.category, UserWarning) for w in caught),
                    f"{name}=10 clamped silently",
                )

    def test_a_value_below_the_range_clamps_and_warns(self):
        for name in (TEXTSTRATA, LEGACY):
            with self.subTest(name=name):
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    store = store_with({name: "0"})
                self.assertEqual(store.revision_limit, 1)
                self.assertTrue(
                    any(issubclass(w.category, UserWarning) for w in caught),
                    f"{name}=0 clamped silently",
                )

    def test_the_clamp_warning_names_the_value_and_the_result(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            store_with({TEXTSTRATA: "10"})
        messages = " ".join(str(w.message) for w in caught)
        self.assertIn("10", messages)
        self.assertIn("3", messages)


class CompatibilityBoundaryTests(unittest.TestCase):
    """The on-disk directory name is deliberately left alone."""

    def test_the_metadata_directory_is_still_dot_fabric(self):
        store = store_with({})
        self.assertEqual(store.metadata_dir.name, ".fabric")
        self.assertEqual(store.revision_dir.parent.name, ".fabric")


if __name__ == "__main__":
    unittest.main()
