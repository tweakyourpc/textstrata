"""Task 9 — trash and restore must be all-or-nothing.

``restore_trash`` moves ``normalized.md`` back, moves ``original.md`` back,
deletes the manifest, then calls ``source.rmdir()``. It never restores
``cleaned.md``, so for any item that has a cleaned variant the directory is not
empty and ``rmdir`` raises -- *after* normalized and original have already moved
and the manifest is already gone. The item is half restored, and what is left in
trash has no manifest, so ``list_trash`` reports it as ``unknown`` and it can
never be restored again.

``trash_item`` has the mirror-image problem: it writes the manifest last, so a
failure partway through leaves a manifest-less directory holding real content.
The manifest must be written first, ``os.replace`` used throughout, and a
manifest-less entry must raise a clear error rather than ``KeyError``.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textstrata.ingest import ingest_text
from textstrata.store import TextStrataStore


BODY = "the quick brown fox"
ITEM = f"---\nid: note.trio\ntitle: Trio\ntype: note\ntags: [x]\n---\n\n{BODY}\n"


def store_with_all_three_variants():
    store = TextStrataStore(tempfile.mkdtemp(), revision_limit=3)
    result = ingest_text(store, ITEM)
    item = result.item
    item.cleaned_body = f"{BODY} (cleaned)"
    store.publish_cleaned(item)
    return store


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file in the workspace except the append-only activity log."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "activity.jsonl"
    }


class RoundTripWithAllVariantsTests(unittest.TestCase):
    def setUp(self):
        self.store = store_with_all_three_variants()

    def test_an_item_with_a_cleaned_variant_restores(self):
        deleted = self.store.trash_item("note.trio")
        self.assertEqual(self.store.restore_trash(deleted["trash_name"]), "note.trio")

    def test_all_three_variants_come_back(self):
        deleted = self.store.trash_item("note.trio")
        self.store.restore_trash(deleted["trash_name"])
        self.assertIsNotNone(self.store.normalized_path_for_id("note.trio"))
        self.assertIsNotNone(self.store.cleaned_path_for_id("note.trio"))
        self.assertTrue((self.store.original_dir / "note.trio.md").exists())

    def test_the_trash_entry_is_gone_after_a_successful_restore(self):
        deleted = self.store.trash_item("note.trio")
        self.store.restore_trash(deleted["trash_name"])
        self.assertEqual(self.store.list_trash(), [])
        self.assertFalse((self.store.trash_dir / deleted["trash_name"]).exists())

    def test_a_full_round_trip_is_byte_identical(self):
        before = snapshot(self.store.root)
        deleted = self.store.trash_item("note.trio")
        self.store.restore_trash(deleted["trash_name"])
        self.assertEqual(snapshot(self.store.root), before)


class FailureLeavesNoHalfStateTests(unittest.TestCase):
    """Break one move partway through; the store must look untouched."""

    def setUp(self):
        self.store = store_with_all_three_variants()

    def _fail_on_nth_replace(self, n):
        real = os.replace
        calls = {"n": 0}

        def flaky(src, dst, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == n:
                raise OSError(5, "simulated I/O error")
            return real(src, dst, *args, **kwargs)

        return patch("textstrata.store.os.replace", side_effect=flaky)

    def test_a_failed_trash_leaves_the_store_untouched(self):
        for nth in (1, 2, 3):
            with self.subTest(failing_move=nth):
                store = store_with_all_three_variants()
                before = snapshot(store.root)
                with self._fail_on_nth_replace(nth):
                    with self.assertRaises(OSError):
                        store.trash_item("note.trio")
                self.assertEqual(
                    snapshot(store.root), before,
                    f"trash failing on move {nth} left the store in a half state",
                )

    def test_a_failed_restore_leaves_the_item_fully_in_trash(self):
        for nth in (1, 2, 3):
            with self.subTest(failing_move=nth):
                store = store_with_all_three_variants()
                deleted = store.trash_item("note.trio")
                before = snapshot(store.root)
                with self._fail_on_nth_replace(nth):
                    with self.assertRaises(OSError):
                        store.restore_trash(deleted["trash_name"])
                self.assertEqual(
                    snapshot(store.root), before,
                    f"restore failing on move {nth} left the store in a half state",
                )

    def test_a_failed_restore_can_be_retried(self):
        store = store_with_all_three_variants()
        deleted = store.trash_item("note.trio")
        with self._fail_on_nth_replace(1):
            with self.assertRaises(OSError):
                store.restore_trash(deleted["trash_name"])
        self.assertEqual(store.restore_trash(deleted["trash_name"]), "note.trio")
        self.assertIsNotNone(store.cleaned_path_for_id("note.trio"))


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.store = store_with_all_three_variants()

    def test_the_manifest_is_written_before_any_content_moves(self):
        real = os.replace
        seen = []

        def spy(src, dst, *args, **kwargs):
            seen.append(Path(dst).name)
            return real(src, dst, *args, **kwargs)

        with patch("textstrata.store.os.replace", side_effect=spy):
            self.store.trash_item("note.trio")
        self.assertIn("manifest.json", seen, "the manifest was not written through os.replace")
        self.assertEqual(
            seen[0], "manifest.json",
            f"content moved before the manifest existed: {seen}",
        )

    def test_a_missing_manifest_raises_a_clear_error(self):
        deleted = self.store.trash_item("note.trio")
        (self.store.trash_dir / deleted["trash_name"] / "manifest.json").unlink()
        with self.assertRaises(Exception) as caught:
            self.store.restore_trash(deleted["trash_name"])
        self.assertNotIsInstance(caught.exception, KeyError)
        self.assertIn(deleted["trash_name"], str(caught.exception))

    def test_a_manifest_without_an_item_id_raises_a_clear_error(self):
        deleted = self.store.trash_item("note.trio")
        manifest = self.store.trash_dir / deleted["trash_name"] / "manifest.json"
        manifest.write_text(json.dumps({"deleted_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
        with self.assertRaises(Exception) as caught:
            self.store.restore_trash(deleted["trash_name"])
        self.assertNotIsInstance(caught.exception, KeyError)
        self.assertIn(deleted["trash_name"], str(caught.exception))

    def test_a_corrupt_manifest_raises_a_clear_error(self):
        deleted = self.store.trash_item("note.trio")
        manifest = self.store.trash_dir / deleted["trash_name"] / "manifest.json"
        manifest.write_text("not json at all", encoding="utf-8")
        with self.assertRaises(Exception) as caught:
            self.store.restore_trash(deleted["trash_name"])
        self.assertNotIsInstance(caught.exception, KeyError)
        self.assertIn(deleted["trash_name"], str(caught.exception))


if __name__ == "__main__":
    unittest.main()
