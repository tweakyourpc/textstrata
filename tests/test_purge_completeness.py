"""Task 10 — revisions must follow the item through trash, restore, and purge.

Revisions live in ``.fabric/revisions/{item_id}/``, outside every directory that
``trash_item`` and ``purge_trash`` touch. Trashing an item leaves them behind;
purging the trash entry leaves them behind too. So content the user explicitly
destroyed is still sitting on disk, invisible to the UI, in a hidden metadata
directory. For anything a user purges deliberately -- a leaked credential, a
document they were asked to delete -- that is the whole point of the operation
silently failing.

The bar is about content, not paths: after a purge, no file anywhere under the
workspace may still contain the purged body.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textstrata import activity
from textstrata.ingest import build_item, ingest_text
from textstrata.store import TextStrataStore


MARKER = "zqxjvk-purge-marker"


def item_text(revision: str, item_id: str = "note.purge") -> str:
    return (
        f"---\nid: {item_id}\ntitle: Purge\ntype: note\ntags: [x]\n---\n\n"
        f"{MARKER} {revision}\n"
    )


def store_with_revisions(item_id: str = "note.purge", *, cleaned: bool = True):
    """An item with an original, a normalized file, revisions, and optionally a
    cleaned variant.

    The restore tests below pass ``cleaned=False`` on purpose. A cleaned variant
    triggers Task 9's ``rmdir`` crash in ``restore_trash``, which would mask what
    this file is actually measuring: whether revisions travel with the item.
    """
    store = TextStrataStore(tempfile.mkdtemp(), revision_limit=3)
    result = ingest_text(store, item_text("one", item_id))
    item = result.item
    if cleaned:
        item.cleaned_body = f"{MARKER} cleaned"
        store.publish_cleaned(item)
    for revision in ("two", "three"):
        rebuilt, _, _ = build_item(item_text(revision, item_id))
        store.publish_normalized(rebuilt)
    return store


def files_containing(root: Path, needle: str) -> list[str]:
    hits = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            if needle.encode("utf-8") in path.read_bytes():
                hits.append(str(path.relative_to(root)))
        except OSError:
            continue
    return hits


class RevisionsFollowTheItemTests(unittest.TestCase):
    def setUp(self):
        self.store = store_with_revisions()
        self.assertTrue(self.store.list_revisions("note.purge"), "fixture has no revisions")

    def test_trashing_takes_the_revisions_with_it(self):
        self.store.trash_item("note.purge")
        self.assertEqual(
            self.store.list_revisions("note.purge"), [],
            "revisions were left behind in the live store after trashing",
        )

    def test_restoring_brings_the_revisions_back(self):
        store = store_with_revisions(cleaned=False)
        before = store.list_revisions("note.purge")
        deleted = store.trash_item("note.purge")
        # Without this the assertion below is vacuous: it would pass simply
        # because the revisions never left the live directory.
        self.assertEqual(store.list_revisions("note.purge"), [])
        store.restore_trash(deleted["trash_name"])
        self.assertEqual(store.list_revisions("note.purge"), before)

    def test_a_restored_revision_is_still_usable(self):
        store = store_with_revisions(cleaned=False)
        revisions = store.list_revisions("note.purge")
        deleted = store.trash_item("note.purge")
        self.assertEqual(store.list_revisions("note.purge"), [])
        store.restore_trash(deleted["trash_name"])
        store.restore_revision("note.purge", revisions[-1]["name"])
        self.assertIn(MARKER, store.normalized_path_for_id("note.purge").read_text(encoding="utf-8"))


class PurgeLeavesNothingBehindTests(unittest.TestCase):
    def test_a_purged_body_is_gone_from_the_whole_workspace(self):
        store = store_with_revisions()
        deleted = store.trash_item("note.purge")
        store.purge_trash(deleted["trash_name"])
        self.assertEqual(
            files_containing(store.root, MARKER), [],
            "purged content is still on disk",
        )

    def test_purging_everything_leaves_nothing_behind(self):
        store = store_with_revisions()
        store.trash_item("note.purge")
        store.purge_trash()
        self.assertEqual(files_containing(store.root, MARKER), [])

    def test_purging_one_item_does_not_touch_another(self):
        store = store_with_revisions()
        other = build_item(item_text("one", "note.keeper"))[0]
        store.publish_normalized(other)
        deleted = store.trash_item("note.purge")
        store.purge_trash(deleted["trash_name"])
        self.assertIsNotNone(store.normalized_path_for_id("note.keeper"))

    def test_the_revision_directory_itself_is_removed(self):
        store = store_with_revisions()
        deleted = store.trash_item("note.purge")
        store.purge_trash(deleted["trash_name"])
        self.assertFalse((store.revision_dir / "note.purge").exists())


class OrphanedRevisionDirsTests(unittest.TestCase):
    """Report-only: it names them, it never deletes them."""

    def _orphan(self, store):
        orphan = store.revision_dir / "note.vanished"
        orphan.mkdir(parents=True, exist_ok=True)
        (orphan / "20260101T000000000000Z-abcdef0123.md").write_text("stale\n", encoding="utf-8")
        return orphan

    def test_an_orphaned_directory_is_reported(self):
        store = store_with_revisions()
        self._orphan(store)
        self.assertIn("note.vanished", store.orphaned_revision_dirs())

    def test_a_live_item_is_not_reported(self):
        store = store_with_revisions()
        self._orphan(store)
        self.assertNotIn("note.purge", store.orphaned_revision_dirs())

    def test_reporting_deletes_nothing(self):
        store = store_with_revisions()
        orphan = self._orphan(store)
        store.orphaned_revision_dirs()
        self.assertTrue(orphan.exists())
        self.assertTrue(any(orphan.iterdir()))

    def test_it_returns_item_ids(self):
        store = store_with_revisions()
        self._orphan(store)
        self.assertTrue(all(isinstance(value, str) for value in store.orphaned_revision_dirs()))


class PurgeIsLoggedAfterItSucceedsTests(unittest.TestCase):
    def purge_entries(self, store):
        return [e for e in activity.read(store.root) if e.get("action") == "purge"]

    def test_a_successful_purge_is_logged(self):
        store = store_with_revisions()
        deleted = store.trash_item("note.purge")
        store.purge_trash(deleted["trash_name"])
        self.assertTrue(self.purge_entries(store))

    def test_a_failed_purge_of_everything_is_not_logged(self):
        store = store_with_revisions()
        store.trash_item("note.purge")
        with patch("textstrata.store.shutil.rmtree", side_effect=OSError(5, "simulated")):
            with self.assertRaises(OSError):
                store.purge_trash()
        self.assertEqual(
            self.purge_entries(store), [],
            "a purge that never deleted anything was already written to the activity log",
        )

    def test_a_failed_purge_of_one_entry_is_not_logged(self):
        store = store_with_revisions()
        deleted = store.trash_item("note.purge")
        with patch("textstrata.store.shutil.rmtree", side_effect=OSError(5, "simulated")):
            with self.assertRaises(OSError):
                store.purge_trash(deleted["trash_name"])
        self.assertEqual(self.purge_entries(store), [])

    def test_a_failed_purge_leaves_the_trash_entry_intact(self):
        store = store_with_revisions()
        deleted = store.trash_item("note.purge")
        with patch("textstrata.store.shutil.rmtree", side_effect=OSError(5, "simulated")):
            with self.assertRaises(OSError):
                store.purge_trash(deleted["trash_name"])
        self.assertEqual(
            [entry["trash_name"] for entry in store.list_trash()], [deleted["trash_name"]]
        )


if __name__ == "__main__":
    unittest.main()
